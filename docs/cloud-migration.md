# WeAidStock 클라우드 이전 가이드

지금은 PC에서 백엔드(Flask 5001)가 돌고 Cloudflare Tunnel로 `stock.webig.ai`에 연결됩니다. 이 문서는 PC를 켜 두지 않아도 되도록 옮기는 방법을 정리합니다. 코드와 설정 파일은 준비돼 있고, **실제 이전(결제·배포·DNS 전환)은 아직 하지 않았습니다.**

## 한눈에 보기

| 단계 | 어디서 | 데이터 보존 | 필요한 것 | 비고 |
|---|---|---|---|---|
| 0. 현재 | PC + Tunnel | PC 디스크 | PC 켜 두기 | 설정 화면·관리 기능 모두 사용 |
| 1. VM | 작은 리눅스 서버 + `docker compose` + cloudflared | 도커 볼륨 | VM 1대 (2 vCPU·4 GB 권장) | 가장 단순, 코드 변경 없음 |
| 2. Cloudflare Containers | Worker + Container 1개 | R2 스냅샷 | Workers Paid, R2 버킷 | 서버 관리 없음, 평일 낮에만 켜짐 |

어느 쪽이든 **컨테이너 인스턴스는 1개**여야 합니다. 작업 진행 상태(TaskManager)와 스케줄러가 한 프로세스를 전제로 합니다.

## 준비된 파일

- `Dockerfile` — 화면(Vite)을 빌드해 백엔드가 직접 서빙하는 단일 이미지. 포트 8080, 비루트 사용자, 헬스체크 `/health`. rdflib·pyshacl이 포함됩니다. 감성 모델(PyTorch)은 용량 때문에 빼고 `SENTIMENT_MODE=llm`으로 둡니다.
- `docker-compose.yml` — 1단계용. `weaid-data` 볼륨에 `uploads/`를 보존합니다.
- `deploy/cloudflare/entrypoint.sh` — 시작할 때 R2에서 상태를 복원하고, 15분마다와 종료 신호(SIGTERM)를 받을 때 스냅샷을 R2에 올립니다. R2 설정이 없으면 이 과정은 건너뜁니다.
- `deploy/cloudflare/wrangler.jsonc`, `src/index.ts` — 2단계용 Worker. 모든 요청을 컨테이너 `main`으로 넘기고, 5분마다 도는 cron이 평일 07:30~17:00(KST)에 컨테이너를 깨워 둡니다.
- `backend/app/services/state_snapshot.py` — SQLite를 온라인 백업 API로 떠서 쓰는 중에도 일관된 사본을 만듭니다. PC 수동 백업에도 쓸 수 있습니다: `python -m app.services.state_snapshot snapshot D:\backup\weaid`
- `backend/app/api/internal.py` — 예약 작업 진입점 `/api/internal/cron/<job>`. `CRON_SECRET`(24자 이상)이 맞을 때만 동작하고, 그 외에는 404를 돌려줍니다.

## 1단계 — VM + docker compose

1. VM에 Docker를 설치하고 저장소를 받습니다. PC의 `.env`는 안전한 경로로 옮깁니다(채팅·메일로 보내지 마세요).
2. PC 데이터를 옮깁니다: PC에서 `python -m app.services.state_snapshot snapshot <폴더>`를 실행한 뒤, 그 폴더를 VM에서 볼륨으로 복원합니다(`docker compose run --rm weaidstock python -m app.services.state_snapshot restore <폴더>`).
3. `docker compose up -d --build` 후 `http://VM:8080/health`를 확인합니다.
4. Tunnel 커넥터를 VM으로 옮깁니다. 같은 토큰으로 `cloudflared tunnel run --token …`을 실행하고 PC 쪽 커넥터는 끕니다. 터널 인그레스의 서비스 주소를 `http://localhost:8080`으로 바꿉니다(Zero Trust › Networks › Tunnels › weaidstock).
5. Access 앱·정책은 그대로 둡니다.

## 2단계 — Cloudflare Containers

### 알아둘 점 (Cloudflare 문서 기준, 2026-09 확인)

- 컨테이너 디스크는 비휘발성이 아닙니다. 잠들었다 깨면 이미지 상태로 초기화되므로 상태는 R2 스냅샷으로 보존합니다. R2를 FUSE로 직접 마운트하는 방법도 있지만, SQLite를 올려 쓰기에는 성능과 잠금 문제가 있어 쓰지 않습니다.
- 기본 `sleepAfter`는 10분이고 이 설정에서는 45분입니다. 호스트 재시작 등으로 언제든 멈출 수 있으며, 멈출 때 SIGTERM을 보내고 최대 15분 기다린 뒤 SIGKILL을 보냅니다. entrypoint가 SIGTERM을 받으면 마지막 스냅샷을 올립니다.
- 인스턴스 유형은 `standard-1`(1/2 vCPU·4 GiB·8 GB)입니다. 부족하면 `standard-2`(1 vCPU·6 GiB)로 올립니다.
- 배치 지역은 `APAC`로 제한합니다. 네이버·KRX 데이터 소스와의 지연을 줄이고 해외 IP 차단 가능성을 낮추기 위해서입니다.

### 순서

1. R2 버킷을 만들고(예: `weaidstock-state`) 그 버킷에만 권한이 있는 R2 API 토큰(Object Read & Write)을 발급합니다.
2. PC 상태를 첫 스냅샷으로 올립니다. 스냅샷을 만든 뒤 `rclone copy <폴더> r2:weaidstock-state/weaidstock/state`를 실행합니다.
3. 비밀값을 등록합니다(`deploy/cloudflare`에서):
   `npx wrangler secret put` 으로 다음 값을 넣습니다. `XAI_API_KEY`, `ZEP_API_KEY`, `TYPESAFE_API_KEY`, `DART_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `CRON_SECRET`(무작위 32자 이상), 필요하면 `REMOTE_ACCESS_EMAILS`.
4. `npm install && npx wrangler deploy`를 실행합니다. 처음에는 `routes`를 주석으로 둔 채 배포해 이미지 빌드와 기동만 확인합니다.
5. 전환(컷오버):
   - PC 쪽 커넥터를 끕니다.
   - `stock.webig.ai`의 터널 CNAME을 삭제합니다(레코드 `7e06a3c92a8fd29e822544bdc9c09a4c`).
   - `wrangler.jsonc`의 `routes` 주석을 풀고 다시 배포합니다.
   - Access 앱(`WeAidStock`)과 `/health` 우회 정책은 호스트 이름 기준이라 그대로 적용됩니다.
6. 되돌리기: `routes`를 다시 주석 처리해 배포하고, 터널 CNAME을 복구한 뒤 PC 커넥터를 켭니다. 그 사이 쌓인 데이터는 R2 스냅샷을 PC로 복원하면 됩니다.

### 클라우드에서 달라지는 점

- **설정 화면(`/api/admin/*`)은 원격에서 항상 403입니다.** 설정은 `wrangler secret`/`vars`로 바꾸고 다시 배포합니다. 자체 점검도 PC에서만 실행할 수 있습니다. 클라우드용 관리 모드(Access 이메일 기반)는 필요할 때 따로 추가합니다.
- 진행 중인 작업은 컨테이너가 멈추면 사라집니다. 결과 파일은 스냅샷 주기(15분) 안에서 보존됩니다.
- 비용은 평일 약 9.5시간 켜짐 기준입니다. 정확한 금액은 Cloudflare 대시보드의 Containers 사용량으로 확인하세요.

## 설정값 요약

| 이름 | 어디서 | 설명 |
|---|---|---|
| `CRON_SECRET` | secret | 예약 작업 호출용 공유 비밀 (24자 이상, 없으면 예약 작업 비활성) |
| `R2_ACCOUNT_ID`·`R2_ACCESS_KEY_ID`·`R2_SECRET_ACCESS_KEY`·`R2_BUCKET` | secret | 상태 스냅샷 저장소 |
| `R2_PREFIX` | vars | 버킷 안 경로 (기본 `weaidstock`) |
| `SNAPSHOT_EVERY` | vars | 스냅샷 주기(초), 기본 900 |
| `REMOTE_ACCESS` | vars | `access` 유지 (Cloudflare Access JWT 검증) |
| `OPS_METRICS`·`LLM_PRICES`·`USD_KRW` | vars | 운영 지표·비용 계산 |
