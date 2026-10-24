#!/bin/sh
# WeAidStock 컨테이너 시작 스크립트
#  1) R2 설정이 있으면 마지막 상태 스냅샷을 내려받아 uploads/ 로 복원 (컨테이너 디스크는 재시작마다 비워짐)
#  2) 백엔드 실행 (화면은 frontend/dist 를 백엔드가 서빙)
#  3) SNAPSHOT_EVERY 초마다(기본 900) 스냅샷 → R2 동기화, 종료 신호(SIGTERM) 때 마지막으로 한 번 더
# R2 없이 실행하면 1·3단계는 건너뜀 (docker compose 볼륨처럼 디스크가 보존되는 곳에서는 불필요)
set -eu

APP=/app/backend
UP="$APP/uploads"
SNAP="${SNAPSHOT_DIR:-/tmp/weaid_snapshot}"
EVERY="${SNAPSHOT_EVERY:-900}"

if [ -n "${R2_ACCOUNT_ID:-}" ] && [ -n "${R2_ACCESS_KEY_ID:-}" ] && [ -n "${R2_SECRET_ACCESS_KEY:-}" ] && [ -n "${R2_BUCKET:-}" ]; then
  export RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
  export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
  export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
  REMOTE="r2:${R2_BUCKET}/${R2_PREFIX:-weaidstock}/state"
else
  REMOTE=""
fi

snapshot_and_push() {
  [ -n "$REMOTE" ] || return 0
  rm -rf "$SNAP" && mkdir -p "$SNAP"
  (cd "$APP" && python -m app.services.state_snapshot snapshot "$SNAP") >/dev/null
  rclone sync "$SNAP" "$REMOTE" --fast-list --transfers 8 --checksum -q && echo "[entrypoint] 상태 스냅샷을 R2 에 저장했습니다"
}

mkdir -p "$UP"
if [ -n "$REMOTE" ]; then
  echo "[entrypoint] R2 에서 상태 복원: $REMOTE"
  rm -rf "$SNAP" && mkdir -p "$SNAP"
  if rclone copy "$REMOTE" "$SNAP" --fast-list --transfers 8 -q; then
    (cd "$APP" && python -m app.services.state_snapshot restore "$SNAP") || echo "[entrypoint] 복원 실패 — 빈 상태로 시작"
  else
    echo "[entrypoint] R2 스냅샷 없음 — 빈 상태로 시작"
  fi
fi

cd "$APP"
python run.py &
APP_PID=$!

stop() {
  echo "[entrypoint] 종료 신호 — 마지막 스냅샷 저장 후 종료"
  snapshot_and_push || true
  kill -TERM "$APP_PID" 2>/dev/null || true
  wait "$APP_PID" 2>/dev/null || true
  exit 0
}
trap stop TERM INT

if [ -n "$REMOTE" ]; then
  ( while sleep "$EVERY"; do snapshot_and_push || echo "[entrypoint] 스냅샷 실패 (다음 주기에 다시)"; done ) &
fi

# 백엔드가 스스로 끝나면 컨테이너도 종료 (재시작은 플랫폼이 담당)
while kill -0 "$APP_PID" 2>/dev/null; do
  sleep 5 &
  wait $! || true
done
wait "$APP_PID"
