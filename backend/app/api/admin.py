"""
로컬 전용 관리 API — 설정(API 키)·재시작·연결 테스트·감성 모델 설치·로그

보안
  - 이 PC(127.0.0.1 / ::1)에서 온 요청만 허용. 같은 와이파이의 다른 기기에서는 403.
  - Cloudflare Tunnel(stock.webig.ai)로 들어온 요청은 주소가 127.0.0.1 이어도 프록시 헤더(CF-Connecting-IP 등)로 판별해 403.
  - 브라우저가 Origin 을 보내면 localhost:3000 / 127.0.0.1:3000 / :5001 만 허용 (다른 웹사이트가 몰래 호출하는 것 차단).
  - 키 값은 화면에 앞 4자리·뒤 4자리만 보여 줍니다. .env 를 고치기 전 .env.bak 으로 백업합니다.
"""

import json
import os
import re
import sys
import time
import shutil
import platform
import threading
import subprocess
import importlib.util
from datetime import datetime
from typing import Dict, List, Optional

import requests
from flask import Blueprint, request, jsonify

from ..models.task import TaskManager, TaskStatus
from ..utils.logger import get_logger
from ..remote_access import is_proxied, mode as remote_mode, public_url, dist_info, access_status, DIST
from ..services import tunnel_manager

logger = get_logger('mirofish.admin')

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
BACKEND = os.path.join(ROOT, 'backend')
ENV_PATH = os.path.join(ROOT, '.env')
STARTED_AT = time.time()
SENTIMENT_MODEL = 'tabularisai/multilingual-sentiment-analysis'

# 화면(Vite) 포트: 기본 3300 (WEAID_WEB_PORT 로 변경 가능, 사용 중이면 Vite 가 다음 번호로 넘어감) · 예전 3000 · 백엔드 5001
_WEB_PORT = int(os.environ.get('WEAID_WEB_PORT', '3300') or 3300)
ALLOWED_ORIGINS = {f'http://{h}:{p}' for h in ('localhost', '127.0.0.1')
                   for p in [3000, 5001] + list(range(_WEB_PORT, _WEB_PORT + 10))}
LOOPBACK = {'127.0.0.1', '::1', '::ffff:127.0.0.1', 'localhost'}

# key, 이름, 그룹, 비밀여부, 설명, 재시작 필요
SETTINGS: List[Dict] = [
    dict(key='XAI_API_KEY', label='xAI API 키', group='llm', secret=True, restart=True,
         help='Grok 보고서·지식그래프·판단·토론·음성(TTS) 전체에 사용합니다.', placeholder='xai-…', test='xai'),
    dict(key='LLM_PROVIDER', label='LLM 공급자', group='llm', secret=False, restart=True,
         help='auto(기본): xAI 키가 있으면 Grok 사용 · xai: 항상 xAI · openai: 아래 사용자 지정 값 사용', placeholder='auto',
         options=['auto', 'xai', 'openai']),
    dict(key='XAI_LLM_MODEL', label='보고서 모델', group='llm', secret=False, restart=True,
         help='비우면 grok-4.7 (추론형, 품질 우선)', placeholder='grok-4.7'),
    dict(key='XAI_FAST_MODEL', label='빠른 모델', group='llm', secret=False, restart=False,
         help='지식그래프·토론·요약에 쓰는 비추론 모델. 비우면 최신 non-reasoning 모델 자동 선택', placeholder='자동'),
    dict(key='XAI_JUDGE_MODEL', label='판단 모델', group='llm', secret=False, restart=False,
         help='비우면 자동 선택', placeholder='자동'),
    dict(key='REPORT_SPEED', label='보고서 속도', group='llm', secret=False, restart=False,
         help='balanced(기본): 밸류에이션만 추론 모델, 나머지는 빠른 모델(보고서 약 2분) · quality: 전부 추론 모델(4~7분) · '
              'fast: 전부 빠른 모델. 어떤 방식이든 섹션마다 시간 한도가 있어 모델이 멈추면 빠른 모델 → 규칙 기반으로 대체합니다.',
         placeholder='balanced', options=['balanced', 'quality', 'fast']),
    dict(key='PREFETCH_AFTER_REPORT', label='다음 단계 미리 준비', group='llm', secret=False, restart=False,
         help='on(기본): 보고서가 끝나면 지식그래프와 투자위원회 토론을 백그라운드로 먼저 진행해 4·5단계를 바로 엽니다 · off: 버튼을 누를 때 시작',
         placeholder='on', options=['on', 'off']),
    dict(key='LLM_API_KEY', label='사용자 지정 LLM 키', group='llm', secret=True, restart=True,
         help='LLM_PROVIDER=openai 일 때만 사용 (OpenAI 호환 API)', placeholder='sk-…'),
    dict(key='LLM_BASE_URL', label='사용자 지정 LLM 주소', group='llm', secret=False, restart=True,
         help='LLM_PROVIDER=openai 일 때만 사용', placeholder='https://api.openai.com/v1'),
    dict(key='LLM_MODEL_NAME', label='사용자 지정 LLM 모델', group='llm', secret=False, restart=True,
         help='LLM_PROVIDER=openai 일 때만 사용', placeholder='gpt-4o-mini'),

    dict(key='TYPESAFE_API_KEY', label='TypeSafe JEV 키', group='judge', secret=True, restart=False,
         help='5단계 판단의 보정 확률(NOUL)을 직접 받습니다. 있으면 게이트웨이보다 우선.', placeholder='apikey_…', test='typesafe'),
    dict(key='AI_GATEWAY_API_KEY', label='Vercel AI Gateway 키', group='judge', secret=True, restart=False,
         help='xAI 장애 시 대체 경로 + TypeSafe 키가 없을 때 JEV 경로', placeholder='vck_…', test='gateway'),

    dict(key='DART_API_KEY', label='OpenDART 인증키', group='data', secret=True, restart=False,
         help='전자공시 원문·분기 실적(매출·영업이익·순이익) 공식 원천 (opendart.fss.or.kr 에서 무료 발급). 없으면 네이버 공시·네이버 금융 재무로 대체',
         placeholder='40자리', test='dart'),
    dict(key='TAVILY_API_KEY', label='Tavily 검색 키', group='data', secret=True, restart=False,
         help='해외 웹 검색 보강 (선택). 없으면 구글 뉴스 RSS 만 사용', placeholder='tvly-…', test='tavily'),
    dict(key='YOUTUBE_API_KEY', label='YouTube Data API 키', group='data', secret=True, restart=False,
         help='유튜브 댓글 심리 수집 (선택, 공식 API 만 사용)', placeholder='AIza…', test='youtube'),
    dict(key='SENTIMENT_MODE', label='감성 분석 방식', group='data', secret=False, restart=False,
         help='auto: Grok(70%)+로컬 모델(30%) 앙상블, 한쪽만 되면 그것 사용 · local · llm · lexicon(사전) · off — '
              '범용 로컬 모델은 한국어 금융 헤드라인을 중립으로 보는 경향이 있어 단독 사용은 권하지 않습니다', placeholder='auto',
         options=['auto', 'local', 'llm', 'lexicon', 'off']),
    dict(key='COMMUNITY_SOURCES', label='커뮤니티 수집', group='data', secret=False, restart=False,
         help='naver(종목토론실 제목만, robots.txt 확인 후) · youtube(공식 API) · off — 작성자 정보는 저장하지 않습니다', placeholder='naver',
         options=['naver', 'naver,youtube', 'youtube', 'off']),
    dict(key='RESEARCH_ROUNDS', label='리플렉션 횟수', group='data', secret=False, restart=False,
         help='2단계 리서치에서 부족한 부분을 다시 찾는 횟수 (0~4, 기본 2)', placeholder='2',
         options=['0', '1', '2', '3', '4']),
    dict(key='RESEARCH_TIME_BUDGET', label='리서치 시간 한도(초)', group='data', secret=False, restart=False,
         help='2단계 리서치 최대 시간 (30~600, 기본 150초)', placeholder='150'),

    dict(key='FLOW_SOURCE', label='수급 데이터 출처', group='data', secret=False, restart=False,
         help='auto(기본): KRX(pykrx, 공식) 우선·실패 시 네이버 · krx · naver — 외국인·기관·개인 순매수와 외국인 보유 비중',
         placeholder='auto', options=['auto', 'krx', 'naver']),

    dict(key='WATCHLIST_SCHEDULE', label='관심종목 아침 일괄 분석', group='schedule', secret=False, restart=False,
         help='on 이면 평일(휴장일 제외) 정한 시각에 관심종목 전체를 1→5단계로 분석합니다 (종목당 3~6분, LLM 사용량 발생)',
         placeholder='off', options=['on', 'off']),
    dict(key='WATCHLIST_TIME', label='일괄 분석 시각 (KST)', group='schedule', secret=False, restart=False,
         help='HH:MM — 장 시작 전 07:40 권장', placeholder='07:40'),
    dict(key='LEDGER_SCHEDULE', label='예측 자동 채점', group='schedule', secret=False, restart=True,
         help='on(기본): 평일 16:40 에 기록된 예측을 실제 주가로 채점', placeholder='on', options=['on', 'off']),
    dict(key='ALERTS', label='매매계획 가격 알림', group='alerts', secret=False, restart=False,
         help='on(기본): 장중 진행 중인 매수 계획의 진입가·목표가·손절가 도달을 알림함(과 텔레그램)으로 알립니다',
         placeholder='on', options=['on', 'off']),
    dict(key='ALERT_INTERVAL', label='가격 확인 주기(분)', group='alerts', secret=False, restart=False,
         help='장중 가격 확인 간격 (기본 5분)', placeholder='5', options=['1', '3', '5', '10', '15']),
    dict(key='TELEGRAM_BOT_TOKEN', label='텔레그램 봇 토큰', group='alerts', secret=True, restart=False,
         help='@BotFather 에서 만든 봇의 토큰. 봇에게 먼저 아무 메시지나 보낸 뒤 아래 채팅 ID 를 넣으세요.',
         placeholder='123456:ABC…', test='telegram'),
    dict(key='TELEGRAM_CHAT_ID', label='텔레그램 채팅 ID', group='alerts', secret=False, restart=False,
         help='알림을 받을 개인·그룹 채팅 ID (@userinfobot 으로 확인)', placeholder='123456789'),

    dict(key='OPS_METRICS', label='운영 지표 수집', group='ops', secret=False, restart=True,
         help='LLM 호출·토큰·추정 비용과 단계별 소요 시간을 기록합니다 (기본 켬). 끄면 재시작 후 적용.', placeholder='on',
         options=['on', 'off']),
    dict(key='LLM_PRICES', label='LLM 단가 (USD / 1M 토큰)', group='ops', secret=False, restart=False,
         help='모델 이름에 포함된 문자열 → [입력, 출력] 단가. 기본값은 공개 가격표 기준 추정치입니다. 예: {"grok-4.7": [2, 6], "fast": [0.2, 0.5]}',
         placeholder='{"grok-4.7": [2, 6], "fast": [0.2, 0.5]}'),
    dict(key='USD_KRW', label='환율 (원/달러)', group='ops', secret=False, restart=False,
         help='비용을 원화로 보여 줄 때 쓰는 환율 (기본 1400)', placeholder='1400'),

    dict(key='ZEP_API_KEY', label='Zep 메모리 키', group='sim', secret=True, restart=True,
         help='시뮬레이션 랩(그래프 메모리)에 필요합니다.', placeholder='z_…'),

    dict(key='THEME_SCHEDULE', label='오늘의 테마 자동 수집', group='schedule', secret=False, restart=False,
         help='on 이면 평일(휴장일 제외) 정해진 시각에 테마를 추출해 대시보드에 올립니다. PC 를 늦게 켜면 켜자마자 1회 추출합니다.',
         placeholder='on', options=['on', 'off']),
    dict(key='THEME_SCHEDULE_TIME', label='수집 시각 (KST)', group='schedule', secret=False, restart=False,
         help='HH:MM — 장 시작 전 08:40 권장', placeholder='08:40'),
    dict(key='THEME_LLM', label='테마 선정 방식', group='schedule', secret=False, restart=False,
         help='auto: Grok 이 뉴스 근거로 선정(실패 시 규칙) · off: 규칙 기반 순위만', placeholder='auto', options=['auto', 'off']),

    dict(key='REMOTE_ACCESS', label='원격 접속 방식', group='remote', secret=False, restart=False,
         help='access(기본): Cloudflare Access(등록된 Gmail 로 일회용 코드 로그인)를 거친 요청만 서명 검증 후 허용 · '
              'code: 이 앱의 접속 코드로 로그인 · open: 로그인 없이 누구나(권장하지 않음) · off: 원격 차단. '
              '어떤 방식이든 이 설정 화면과 관리 기능은 원격에서 열리지 않습니다.',
         placeholder='access', options=['access', 'code', 'open', 'off']),
    dict(key='REMOTE_ACCESS_EMAILS', label='허용 이메일 (선택)', group='remote', secret=False, restart=False,
         help='access 방식에서 앱이 한 번 더 확인할 이메일 (쉼표로 구분). 비우면 Cloudflare Access 정책(현재 본인 Gmail 만 허용)을 그대로 따릅니다.',
         placeholder='비우면 Access 정책을 따름'),
    dict(key='CF_ACCESS_TEAM_DOMAIN', label='Access 팀 도메인', group='remote', secret=False, restart=False,
         help='JWT 발급자 확인용. 비우면 weaikorea.cloudflareaccess.com', placeholder='weaikorea.cloudflareaccess.com'),
    dict(key='CF_ACCESS_AUD', label='Access 앱 AUD 태그', group='remote', secret=False, restart=False,
         help='JWT 대상 확인용 (Zero Trust → Access → 애플리케이션 → WeAidStock → 개요). 비우면 이 배포의 기본값', placeholder='기본값 사용'),
    dict(key='REMOTE_ACCESS_CODE', label='원격 접속 코드', group='remote', secret=True, restart=False,
         help='code 방식일 때 공개 주소에서 입력하는 코드 (8자 이상). 바꾸면 다른 기기의 로그인이 모두 풀립니다.',
         placeholder='8자 이상'),
    dict(key='CLOUDFLARE_TUNNEL_TOKEN', label='Cloudflare 터널 토큰', group='remote', secret=True, restart=False,
         help='Cloudflare 대시보드 → Zero Trust → Networks → Tunnels → weaidstock → 커넥터 설치 화면의 명령어를 통째로 붙여 넣어도 됩니다 (토큰만 저장).',
         placeholder='eyJ… 또는 cloudflared.exe service install eyJ…'),
    dict(key='REMOTE_TUNNEL', label='터널 자동 연결', group='remote', secret=False, restart=False,
         help='on(기본): 백엔드가 켜질 때 커넥터(cloudflared)를 함께 실행하고 1분마다 확인 · off: 직접 관리(윈도우 서비스 등)',
         placeholder='on', options=['on', 'off']),
    dict(key='WEAID_PUBLIC_URL', label='공개 주소', group='remote', secret=False, restart=False,
         help='연결 확인에 쓰는 주소', placeholder='https://stock.webig.ai'),
]
GROUPS = {
    'llm': 'LLM · Grok', 'judge': '판단 엔진 · JEV', 'data': '데이터 소스 · 심리',
    'sim': '시뮬레이션', 'schedule': '스케줄', 'alerts': '알림 · 텔레그램', 'ops': '운영 지표 · 비용', 'remote': '원격 접속 · stock.webig.ai',
    'other': '기타 (.env 에 있는 값)',
}
KEY_RE = re.compile(r'^[A-Z][A-Z0-9_]{1,63}$')
SECRET_HINT = re.compile(r'(KEY|TOKEN|SECRET|PASSWORD)', re.I)


# ─────────────────────────────────────────────────────────── 보안 가드
@admin_bp.before_request
def _local_only():
    addr = (request.remote_addr or '').strip()
    if addr not in LOOPBACK or is_proxied(request):
        logger.warning(f"관리 API 원격 접근 차단: {addr} {request.headers.get('CF-Connecting-IP') or ''} {request.path}")
        return jsonify({'success': False, 'error': '이 PC 에서만 사용할 수 있습니다'}), 403
    origin = request.headers.get('Origin')
    if origin and origin.rstrip('/') not in ALLOWED_ORIGINS:
        logger.warning(f"관리 API 외부 Origin 차단: {origin}")
        return jsonify({'success': False, 'error': '허용되지 않은 출처입니다'}), 403
    return None


# ─────────────────────────────────────────────────────────── .env 읽기/쓰기
_env_lock = threading.Lock()


def _read_env_lines() -> List[str]:
    if not os.path.exists(ENV_PATH):
        return []
    with open(ENV_PATH, 'r', encoding='utf-8-sig') as f:
        return f.read().splitlines()


def _parse_env(lines: List[str]) -> Dict[str, str]:
    out = {}
    for ln in lines:
        m = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', ln)
        if not m or ln.lstrip().startswith('#'):
            continue
        v = m.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        else:
            v = re.sub(r'\s+#.*$', '', v)
        out[m.group(1)] = v
    return out


def _fmt_value(v: str) -> str:
    if v == '' or re.search(r'[\s#"\'\\]', v):
        return '"' + v.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return v


def _mask(v: Optional[str]) -> str:
    if not v:
        return ''
    if len(v) <= 10:
        return '•' * 6
    return f"{v[:4]}…{v[-4:]}"


def _write_env(updates: Dict[str, Optional[str]]) -> List[str]:
    """updates: {KEY: 값 | None(삭제)} — 주석·순서는 그대로 두고 해당 줄만 교체"""
    with _env_lock:
        lines = _read_env_lines()
        if os.path.exists(ENV_PATH):
            shutil.copyfile(ENV_PATH, ENV_PATH + '.bak')
        changed, done = [], set()
        out = []
        for ln in lines:
            m = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=', ln)
            if m and not ln.lstrip().startswith('#') and m.group(1) in updates:
                k = m.group(1)
                if k in done:          # 중복 줄 정리
                    continue
                done.add(k)
                v = updates[k]
                if v is None:
                    changed.append(k)
                    continue
                out.append(f"{k}={_fmt_value(v)}")
                changed.append(k)
            else:
                out.append(ln)
        new_keys = [k for k, v in updates.items() if k not in done and v is not None]
        if new_keys:
            if out and out[-1].strip():
                out.append('')
            out.append(f"# WeAidStock 설정 화면에서 추가 ({datetime.now():%Y-%m-%d %H:%M})")
            for k in new_keys:
                out.append(f"{k}={_fmt_value(updates[k])}")
                changed.append(k)
        tmp = ENV_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(out).rstrip('\n') + '\n')
        os.replace(tmp, ENV_PATH)
        return changed


def _installed(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def _hf_model_cached(model: str = SENTIMENT_MODEL) -> bool:
    base = os.environ.get('HF_HOME') or os.path.join(os.path.expanduser('~'), '.cache', 'huggingface')
    snap = os.path.join(base, 'hub', 'models--' + model.replace('/', '--'), 'snapshots')
    try:
        return os.path.isdir(snap) and any(True for _ in os.scandir(snap))
    except OSError:
        return False


# ─────────────────────────────────────────────────────────── 설정
@admin_bp.route('/settings', methods=['GET'])
def get_settings():
    env_file = _parse_env(_read_env_lines())
    known = {s['key'] for s in SETTINGS}
    items = []
    for s in SETTINGS:
        k = s['key']
        v = env_file.get(k)
        live = os.environ.get(k)
        item = {kk: s[kk] for kk in ('key', 'label', 'group', 'secret', 'help', 'placeholder', 'restart') if kk in s}
        item['options'] = s.get('options')
        item['test'] = s.get('test')
        item['in_file'] = k in env_file
        item['set'] = bool(v or live)
        item['display'] = _mask(v or live) if s['secret'] else (v if v is not None else (live or ''))
        derived = os.environ.get('LLM_PROVIDER_ACTIVE') == 'xai' and k in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL_NAME')
        item['pending_restart'] = bool(s.get('restart') and k in env_file and not derived and (v or '') != (live or ''))
        items.append(item)
    for k, v in env_file.items():
        if k in known:
            continue
        secret = bool(SECRET_HINT.search(k))
        items.append({'key': k, 'label': k, 'group': 'other', 'secret': secret, 'help': '', 'placeholder': '',
                      'restart': True, 'options': None, 'test': None, 'in_file': True, 'set': bool(v),
                      'display': _mask(v) if secret else v, 'pending_restart': False})
    return jsonify({'success': True, 'data': {
        'items': items, 'groups': GROUPS, 'env_path': ENV_PATH, 'env_exists': os.path.exists(ENV_PATH),
        'llm_active': os.environ.get('LLM_PROVIDER_ACTIVE'), 'llm_model': os.environ.get('LLM_MODEL_NAME'),
    }})


@admin_bp.route('/settings', methods=['POST'])
def save_settings():
    body = request.get_json(silent=True) or {}
    values = body.get('values') or {}
    clear = body.get('clear') or []
    if not isinstance(values, dict) or not isinstance(clear, list):
        return jsonify({'success': False, 'error': '형식 오류'}), 400
    updates: Dict[str, Optional[str]] = {}
    for k, v in values.items():
        if not isinstance(k, str) or not KEY_RE.match(k):
            return jsonify({'success': False, 'error': f'잘못된 키 이름: {k}'}), 400
        v = '' if v is None else str(v).strip()
        if len(v) > 2000 or re.search(r'[\r\n\x00]', v):
            return jsonify({'success': False, 'error': f'{k}: 줄바꿈 없이 2000자 이내로 입력하세요'}), 400
        if v == '':
            continue           # 빈 칸은 '변경 없음'
        if k == 'CLOUDFLARE_TUNNEL_TOKEN':
            tok = tunnel_manager.extract_token(v)
            if not tok or not tunnel_manager.token_info(tok):
                return jsonify({'success': False, 'error': '터널 토큰을 찾지 못했습니다 — eyJ 로 시작하는 토큰이나 대시보드의 설치 명령을 붙여 넣으세요'}), 400
            v = tok
        if k == 'LLM_PRICES':
            try:
                pj = json.loads(v)
                if not isinstance(pj, dict) or not all(isinstance(x, (list, tuple)) and len(x) == 2 and all(isinstance(y, (int, float)) for y in x) for x in pj.values()):
                    raise ValueError
            except ValueError:
                return jsonify({'success': False, 'error': 'LLM_PRICES 는 {"모델 문자열": [입력단가, 출력단가]} 형식의 JSON 이어야 합니다'}), 400
        if k == 'USD_KRW':
            try:
                if not 100 <= float(v) <= 10000:
                    raise ValueError
            except ValueError:
                return jsonify({'success': False, 'error': '환율은 100~10000 사이 숫자로 입력하세요'}), 400
        if k == 'REMOTE_ACCESS_CODE' and (len(v) < 8 or ' ' in v):
            return jsonify({'success': False, 'error': '원격 접속 코드는 공백 없이 8자 이상으로 정하세요'}), 400
        updates[k] = v
    for k in clear:
        if isinstance(k, str) and KEY_RE.match(k):
            updates[k] = None
    if not updates:
        return jsonify({'success': True, 'data': {'changed': [], 'restart_required': False}})
    changed = _write_env(updates)
    restart_keys = {s['key'] for s in SETTINGS if s.get('restart')}
    for k, v in updates.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    need = any(k in restart_keys or k not in {s['key'] for s in SETTINGS} for k in changed)
    logger.info(f"설정 저장: {', '.join(changed)} (재시작 필요: {need})")
    return jsonify({'success': True, 'data': {'changed': changed, 'restart_required': need}})


# ─────────────────────────────────────────────────────────── 연결 테스트
def _test_service(service: str, value: Optional[str]) -> Dict:
    env = lambda k: (value or os.environ.get(k) or '').strip()  # noqa: E731
    try:
        if service == 'xai':
            key = env('XAI_API_KEY')
            if not key:
                return {'ok': False, 'message': 'XAI_API_KEY 없음'}
            r = requests.get('https://api.x.ai/v1/models', headers={'Authorization': f'Bearer {key}'}, timeout=12)
            if r.status_code != 200:
                return {'ok': False, 'message': f'HTTP {r.status_code}: {r.text[:160]}'}
            ids = [m.get('id') for m in r.json().get('data', [])]
            grok = [i for i in ids if i and i.startswith('grok')]
            return {'ok': True, 'message': f'모델 {len(ids)}개 사용 가능 — ' + ', '.join(sorted(grok)[-4:])}
        if service == 'gateway':
            key = env('AI_GATEWAY_API_KEY')
            if not key:
                return {'ok': False, 'message': 'AI_GATEWAY_API_KEY 없음'}
            r = requests.get('https://ai-gateway.vercel.sh/v1/models', headers={'Authorization': f'Bearer {key}'}, timeout=12)
            if r.status_code != 200:
                return {'ok': False, 'message': f'HTTP {r.status_code}: {r.text[:160]}'}
            ids = [m.get('id') for m in r.json().get('data', [])]
            jev = [i for i in ids if 'jev' in (i or '')]
            return {'ok': True, 'message': f'모델 {len(ids)}개 · JEV {", ".join(jev) or "목록에 없음"}'}
        if service == 'typesafe':
            key = env('TYPESAFE_API_KEY')
            if not key:
                return {'ok': False, 'message': 'TYPESAFE_API_KEY 없음'}
            url = os.environ.get('TYPESAFE_URL', 'https://api.typesafe.ai/v1/systemone')
            r = requests.post(url, headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                              json={'model': 'jev-latest', 'state': '연결 테스트입니다. 1 더하기 1은 2입니다.',
                                    'questions': {'t': {'type': 'noul', 'instructions': '1 더하기 1은 2이다.'}}}, timeout=30)
            if r.status_code != 200:
                return {'ok': False, 'message': f'HTTP {r.status_code}: {r.text[:160]}'}
            a = (r.json().get('answers') or {}).get('t') or {}
            return {'ok': True, 'message': f"JEV 응답 정상 — 참 확률 {a.get('probability', a.get('value', '?'))}"}
        if service == 'dart':
            key = env('DART_API_KEY')
            if not key:
                return {'ok': False, 'message': 'DART_API_KEY 없음'}
            r = requests.get('https://opendart.fss.or.kr/api/company.json',
                             params={'crtfc_key': key, 'corp_code': '00126380'}, timeout=12)
            j = r.json() if r.headers.get('content-type', '').startswith('application/json') else {}
            if j.get('status') == '000':
                return {'ok': True, 'message': f"정상 — {j.get('corp_name')} 조회 성공"}
            return {'ok': False, 'message': f"{j.get('status', r.status_code)}: {j.get('message', r.text[:120])}"}
        if service == 'tavily':
            key = env('TAVILY_API_KEY')
            if not key:
                return {'ok': False, 'message': 'TAVILY_API_KEY 없음'}
            r = requests.post('https://api.tavily.com/search', json={'api_key': key, 'query': 'KOSPI', 'max_results': 1}, timeout=15)
            if r.status_code != 200:
                return {'ok': False, 'message': f'HTTP {r.status_code}: {r.text[:160]}'}
            return {'ok': True, 'message': f"정상 — 결과 {len(r.json().get('results', []))}건"}
        if service == 'youtube':
            key = env('YOUTUBE_API_KEY')
            if not key:
                return {'ok': False, 'message': 'YOUTUBE_API_KEY 없음'}
            r = requests.get('https://www.googleapis.com/youtube/v3/search',
                             params={'part': 'id', 'q': '삼성전자 주가', 'maxResults': 1, 'key': key}, timeout=12)
            if r.status_code != 200:
                return {'ok': False, 'message': f'HTTP {r.status_code}: {r.text[:160]}'}
            return {'ok': True, 'message': '정상 — 검색 API 응답'}
        if service == 'telegram':
            from ..services import alerts_service
            prev = os.environ.get('TELEGRAM_BOT_TOKEN')
            if value:
                os.environ['TELEGRAM_BOT_TOKEN'] = value      # 입력 칸의 새 토큰으로 시험 (저장 전) — 끝나면 되돌림
            try:
                r = alerts_service.send_telegram('WeAidStock 연결 테스트 — 이 메시지가 보이면 알림이 정상입니다.')
            finally:
                if value:
                    if prev is None:
                        os.environ.pop('TELEGRAM_BOT_TOKEN', None)
                    else:
                        os.environ['TELEGRAM_BOT_TOKEN'] = prev
            return {'ok': r['ok'], 'message': r['message']}
        if service == 'naver':
            r = requests.get('https://m.stock.naver.com/api/index/KOSPI/basic', timeout=8,
                             headers={'User-Agent': 'Mozilla/5.0'})
            return {'ok': r.status_code == 200, 'message': f"KOSPI {r.json().get('closePrice')}" if r.status_code == 200 else f'HTTP {r.status_code}'}
        return {'ok': False, 'message': f'알 수 없는 서비스: {service}'}
    except Exception as e:
        return {'ok': False, 'message': str(e)[:200]}


@admin_bp.route('/test', methods=['POST'])
def test_service():
    body = request.get_json(silent=True) or {}
    service = str(body.get('service') or '')
    value = body.get('value')
    value = str(value).strip() if value else None
    t0 = time.time()
    res = _test_service(service, value)
    res['ms'] = int((time.time() - t0) * 1000)
    res['service'] = service
    return jsonify({'success': True, 'data': res})


# ─────────────────────────────────────────────────────────── 상태 · 로그
@admin_bp.route('/status', methods=['GET'])
def status():
    tm = TaskManager()
    running = [t for t in tm.list_tasks() if t['status'] in ('pending', 'processing')]
    sched = None
    try:
        from ..services import theme_scheduler  # 오늘의 테마 스케줄러 (있으면)
        sched = theme_scheduler.status()
    except Exception:
        sched = None
    return jsonify({'success': True, 'data': {
        'pid': os.getpid(),
        'python': sys.version.split()[0],
        'executable': sys.executable,
        'platform': platform.platform(),
        'started_at': datetime.fromtimestamp(STARTED_AT).isoformat(timespec='seconds'),
        'uptime_sec': int(time.time() - STARTED_AT),
        'llm_active': os.environ.get('LLM_PROVIDER_ACTIVE'),
        'llm_model': os.environ.get('LLM_MODEL_NAME'),
        'packages': {m: _installed(m) for m in ('torch', 'transformers', 'pykrx', 'bs4', 'zep_cloud', 'rdflib', 'pyshacl')},
        'sentiment': {'model': SENTIMENT_MODEL, 'installed': _installed('torch') and _installed('transformers'),
                      'cached': _hf_model_cached(), 'mode': os.environ.get('SENTIMENT_MODE', 'auto'),
                      'installing': _INSTALL.get('task_id')},
        'sentiment_engine': _safe_sentiment_status(),
        'running_tasks': [{k: t[k] for k in ('task_id', 'task_type', 'progress', 'message')} for t in running[:10]],
        'scheduler': sched,
        'restart_script': os.path.exists(os.path.join(ROOT, 'restart-backend.ps1')),
    }})


def _safe_sentiment_status():
    try:
        from ..services import sentiment
        return sentiment.status()
    except Exception as e:
        return {'error': str(e)[:160]}


# ─────────────────────────────────────────────────────────── 데이터 소스 점검 (허용된 도메인만)
PROBE_HOSTS = {
    'm.stock.naver.com', 'finance.naver.com', 'api.stock.naver.com', 'fchart.stock.naver.com', 'polling.finance.naver.com',
    'n.news.naver.com', 'news.google.com', 'opendart.fss.or.kr', 'dart.fss.or.kr', 'stock.naver.com',
}


@admin_bp.route('/probe', methods=['GET'])
def probe():
    """허용된 공개 데이터 도메인의 응답 구조를 확인 (개발·점검용, 키는 붙이지 않음)"""
    from urllib.parse import urlparse
    url = request.args.get('url', '')
    n = max(200, min(20000, int(request.args.get('n', 3000))))
    u = urlparse(url)
    if u.scheme != 'https' or u.hostname not in PROBE_HOSTS:
        return jsonify({'success': False, 'error': '허용되지 않은 주소입니다'}), 400
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
               'Accept': request.args.get('accept') or '*/*', 'Accept-Language': 'ko-KR,ko;q=0.9'}
    if request.args.get('referer', '').startswith('https://'):
        headers['Referer'] = request.args['referer']
    t0 = time.time()
    try:
        r = requests.get(url, headers=headers, timeout=12)
        raw = r.content
        enc = 'utf-8'
        ctype = r.headers.get('content-type', '')
        m = re.search(r'charset=([\w-]+)', ctype, re.I) or re.search(rb'charset=["\']?([\w-]+)', raw[:2000], re.I)
        if m:
            enc = m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)
        try:
            text = raw.decode(enc, errors='replace')
        except LookupError:
            text = raw.decode('utf-8', errors='replace')
        find = request.args.get('find')
        snippet = text[:n]
        if find and find in text:
            i = text.index(find)
            snippet = text[max(0, i - n // 3): i + n]
        return jsonify({'success': True, 'data': {'status': r.status_code, 'content_type': ctype, 'encoding': enc,
                                                  'length': len(raw), 'ms': int((time.time() - t0) * 1000), 'text': snippet}})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:300]}), 502


DIAG_HOSTS = PROBE_HOSTS | {'api.x.ai', 'ai-gateway.vercel.sh', 'api.typesafe.ai', 'www.google.com', 'pypi.org',
                             'huggingface.co', 'api.coinmarketcap.com'}


@admin_bp.route('/netdiag', methods=['GET'])
def netdiag():
    """백엔드(파이썬)의 인터넷 연결 진단 — 프록시 설정·DNS·TCP·TLS(1.2/1.3)·requests 경로별 결과"""
    import socket
    import ssl
    import urllib.request
    host = request.args.get('host', 'api.x.ai')
    if host not in DIAG_HOSTS:
        return jsonify({'success': False, 'error': '허용되지 않은 호스트'}), 400
    out = {'host': host, 'openssl': ssl.OPENSSL_VERSION, 'python': sys.version.split()[0],
           'system_proxies': {k: v for k, v in urllib.request.getproxies().items()},
           'env': {k: os.environ.get(k) for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'NO_PROXY',
                                                   'REQUESTS_CA_BUNDLE', 'SSL_CERT_FILE', 'CURL_CA_BUNDLE') if os.environ.get(k)}}
    t0 = time.time()
    addrs = []
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        addrs = []
        for fam, _, _, _, sa in infos:
            if (fam, sa[0]) not in addrs:
                addrs.append((fam, sa[0]))
        out['dns'] = {'ok': True, 'ms': int((time.time() - t0) * 1000), 'addrs': [a for _, a in addrs][:6]}
    except Exception as e:
        out['dns'] = {'ok': False, 'ms': int((time.time() - t0) * 1000), 'error': str(e)[:200]}
    tries = []
    for fam, addr in addrs[:3]:
        for maxv in ('1.3', '1.2'):
            r = {'addr': addr, 'tls_max': maxv}
            t1 = time.time()
            try:
                sock = socket.socket(fam, socket.SOCK_STREAM)
                sock.settimeout(6)
                sock.connect((addr, 443))
                r['tcp_ms'] = int((time.time() - t1) * 1000)
                ctx = ssl.create_default_context()
                if maxv == '1.2':
                    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
                tls = ctx.wrap_socket(sock, server_hostname=host)
                r.update(ok=True, version=tls.version(), ms=int((time.time() - t1) * 1000))
                tls.close()
            except Exception as e:
                r.update(ok=False, ms=int((time.time() - t1) * 1000), error=f'{type(e).__name__}: {str(e)[:160]}')
            tries.append(r)
    out['tls'] = tries
    for label, trust in (('requests_default', True), ('requests_no_proxy', False)):
        t2 = time.time()
        try:
            sess = requests.Session()
            sess.trust_env = trust
            rr = sess.get(f'https://{host}/', timeout=(6, 8), headers={'User-Agent': 'Mozilla/5.0'})
            out[label] = {'ok': True, 'status': rr.status_code, 'ms': int((time.time() - t2) * 1000)}
        except Exception as e:
            out[label] = {'ok': False, 'ms': int((time.time() - t2) * 1000), 'error': f'{type(e).__name__}: {str(e)[:200]}'}
    return jsonify({'success': True, 'data': out})


@admin_bp.route('/logs', methods=['GET'])
def logs():
    n = max(10, min(1000, int(request.args.get('n', 200))))
    level = (request.args.get('level') or '').upper()
    d = os.path.join(BACKEND, 'logs')
    path = os.path.join(d, f"{datetime.now():%Y-%m-%d}.log")
    if not os.path.exists(path):
        files = sorted([f for f in os.listdir(d) if f.endswith('.log')]) if os.path.isdir(d) else []
        if not files:
            return jsonify({'success': True, 'data': {'lines': [], 'file': None}})
        path = os.path.join(d, files[-1])
    with open(path, 'rb') as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 400_000))
        text = f.read().decode('utf-8', errors='replace')
    lines = text.splitlines()
    if level:
        lines = [ln for ln in lines if level in ln]
    # 키가 로그에 찍혔을 가능성 대비 마스킹
    lines = [re.sub(r'(xai-|vck_|apikey_|sk-|tvly-|AIza)[A-Za-z0-9_\-]{8,}', r'\1••••', ln) for ln in lines[-n:]]
    return jsonify({'success': True, 'data': {'lines': lines, 'file': os.path.basename(path)}})


# ─────────────────────────────────────────────────────────── 재시작
# 시작 시 계산되는 파생 값 — 새 프로세스가 .env 로 다시 계산하도록 물려주지 않음
_DERIVED_ENV = ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL_NAME', 'LLM_PROVIDER_ACTIVE',
                'WERKZEUG_RUN_MAIN', 'WERKZEUG_SERVER_FD')
_RESTART_LOG = os.path.join(BACKEND, 'logs', 'restart_self.log')


def _restart_note(msg: str):
    try:
        os.makedirs(os.path.dirname(_RESTART_LOG), exist_ok=True)
        with open(_RESTART_LOG, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def _self_restart():
    """같은 파이썬으로 run.py 를 새로 띄우고 현재 프로세스를 종료.
    PowerShell 을 거치지 않으므로 보안 프로그램이 '스크립트 실행'을 막는 PC 에서도 동작합니다.
    새 프로세스는 WEAID_WAIT_PORT=1 로 포트 5001 이 비워질 때까지 기다렸다가 기동합니다."""
    env = dict(os.environ)
    for k in _DERIVED_ENV:
        env.pop(k, None)
    for s in SETTINGS:                 # .env 에서 지운 값이 남지 않도록 — 새 프로세스가 .env 를 다시 읽음
        env.pop(s['key'], None)
    env['WEAID_WAIT_PORT'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    out = err = subprocess.DEVNULL
    try:
        out = open(os.path.join(BACKEND, 'logs', 'server_restart.out.log'), 'ab')
        err = out
    except Exception:
        pass
    base = 0x00000200 | 0x08000000          # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    last = None
    for flags in (base | 0x01000000, base):  # + CREATE_BREAKAWAY_FROM_JOB (허용될 때만)
        try:
            p = subprocess.Popen([sys.executable, 'run.py'], cwd=BACKEND, env=env, creationflags=flags,
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=err, close_fds=True)
            _restart_note(f'새 백엔드 프로세스 시작 pid={p.pid} (flags={hex(flags)}) — 현재 pid={os.getpid()} 종료')
            logger.info(f'백엔드 재시작: 새 프로세스 pid={p.pid}, 현재 프로세스 종료')
            time.sleep(0.3)
            os._exit(0)
        except OSError as e:
            last = e
            _restart_note(f'새 프로세스 시작 재시도: {e}')
    _restart_note(f'재시작 실패: {last}')
    logger.error(f'재시작 실패: {last}')


@admin_bp.route('/restart', methods=['POST'])
def restart():
    if os.name != 'nt' and not os.environ.get('WEAID_ALLOW_RESTART'):
        return jsonify({'success': False, 'error': 'Windows 에서만 지원합니다'}), 400
    threading.Timer(1.0, _self_restart).start()
    return jsonify({'success': True, 'data': {'message': '1초 뒤 백엔드를 다시 시작합니다 (5~15초 소요)', 'old_pid': os.getpid()}})


# ─────────────────────────────────────────────────────────── 화면 서버(Vite) 포트 관리
FRONTEND = os.path.join(ROOT, 'frontend')
_NOWIN = 0x08000000 if os.name == 'nt' else 0


def _listening_pids(ports) -> Dict[int, int]:
    """{port: pid} — netstat 으로 LISTEN 중인 로컬 포트 조회 (Windows)"""
    out = {}
    try:
        txt = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, text=True, timeout=15,
                             creationflags=_NOWIN, encoding='utf-8', errors='replace').stdout
    except Exception:
        return out
    for ln in txt.splitlines():
        parts = ln.split()
        if len(parts) >= 5 and parts[0].upper() == 'TCP' and parts[3].upper() in ('LISTENING', '수신 대기'):
            try:
                port = int(parts[1].rsplit(':', 1)[1])
                pid = int(parts[4])
            except (ValueError, IndexError):
                continue
            if port in ports:
                out[port] = pid
    return out


def _proc_info(pid: int) -> Dict[str, str]:
    info = {'pid': pid, 'name': '', 'cmd': ''}
    try:
        r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'], capture_output=True, text=True,
                           timeout=10, creationflags=_NOWIN, encoding='utf-8', errors='replace').stdout.strip()
        if r.startswith('"'):
            info['name'] = r.split('","')[0].strip('"')
    except Exception:
        pass
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                            f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'],
                           capture_output=True, text=True, timeout=15, creationflags=_NOWIN,
                           encoding='utf-8', errors='replace')
        info['cmd'] = (r.stdout or '').strip()[:400]
    except Exception:
        pass
    return info


def _web_ports():
    return [3000] + list(range(_WEB_PORT, _WEB_PORT + 10))


def _is_our_vite(info: Dict[str, str]) -> bool:
    cmd = (info.get('cmd') or '').lower()
    if cmd:
        return 'vite' in cmd and ('mirofish-stockpredictor' in cmd or FRONTEND.lower() in cmd or 'node_modules' in cmd)
    return (info.get('name') or '').lower() == 'node.exe'


@admin_bp.route('/web/status', methods=['GET'])
def web_status():
    if os.name != 'nt':
        return jsonify({'success': False, 'error': 'Windows 에서만 지원합니다'}), 400
    lp = _listening_pids(set(_web_ports()))
    items = []
    for port, pid in sorted(lp.items()):
        info = _proc_info(pid)
        items.append({'port': port, 'pid': pid, 'name': info['name'], 'ours': _is_our_vite(info), 'cmd': info['cmd'][:200]})
    return jsonify({'success': True, 'data': {'target_port': _WEB_PORT, 'listening': items}})


@admin_bp.route('/web/restart', methods=['POST'])
def web_restart():
    """화면 서버(Vite)를 지정 포트로 다시 띄움. 우리 Vite 가 다른 포트(예: 3000)를 쓰고 있으면 종료해서 비워 줌."""
    if os.name != 'nt':
        return jsonify({'success': False, 'error': 'Windows 에서만 지원합니다'}), 400
    body = request.get_json(silent=True) or {}
    port = int(body.get('port') or _WEB_PORT)
    if not (1024 < port < 65535):
        return jsonify({'success': False, 'error': '포트 범위 오류'}), 400
    node = shutil.which('node') or r'C:\Program Files\nodejs\node.exe'
    if not os.path.exists(node):
        return jsonify({'success': False, 'error': 'node.exe 를 찾지 못했습니다'}), 500
    vite = os.path.join(FRONTEND, 'node_modules', 'vite', 'bin', 'vite.js')
    if not os.path.exists(vite):
        return jsonify({'success': False, 'error': 'frontend/node_modules 에 vite 가 없습니다'}), 500
    stopped, kept = [], []
    for p_, pid in sorted(_listening_pids(set(_web_ports()) | {port}).items()):
        info = _proc_info(pid)
        if _is_our_vite(info):
            r = subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, text=True, timeout=15,
                               creationflags=_NOWIN, encoding='utf-8', errors='replace')
            stopped.append({'port': p_, 'pid': pid, 'ok': r.returncode == 0})
        else:
            kept.append({'port': p_, 'pid': pid, 'name': info['name']})
    if any(k['port'] == port for k in kept):
        return jsonify({'success': False, 'error': f'{port} 포트를 다른 프로그램이 쓰고 있습니다', 'data': {'kept': kept}}), 409
    time.sleep(1.0)
    log = open(os.path.join(BACKEND, 'logs', 'web_server.log'), 'ab')
    base = 0x00000200 | _NOWIN
    proc = None
    for flags in (base | 0x01000000, base):
        try:
            proc = subprocess.Popen([node, vite, '--host', '127.0.0.1', '--port', str(port), '--strictPort'],
                                    cwd=FRONTEND, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    creationflags=flags, close_fds=True)
            break
        except OSError:
            continue
    if proc is None:
        return jsonify({'success': False, 'error': '화면 서버 실행 실패'}), 500
    import socket
    ok = False
    for _ in range(60):
        time.sleep(0.5)
        if proc.poll() is not None:
            break
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                ok = True
                break
        except OSError:
            pass
    logger.info(f'화면 서버 재시작: port={port} pid={proc.pid} ok={ok} stopped={stopped}')
    return jsonify({'success': ok, 'data': {'port': port, 'pid': proc.pid, 'url': f'http://localhost:{port}/',
                                            'stopped': stopped, 'kept': kept},
                    'error': None if ok else '화면 서버가 시작되지 않았습니다 (logs/web_server.log 확인)'})


# ─────────────────────────────────────────────────────────── 감성 모델 설치 (torch CPU + transformers)
_INSTALL: Dict[str, Optional[str]] = {'task_id': None}


def _run_streamed(cmd: List[str], on_line, timeout: int = 3600) -> int:
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PIP_DISABLE_PIP_VERSION_CHECK='1')
    kw = {}
    if os.name == 'nt':
        kw['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    p = subprocess.Popen(cmd, cwd=BACKEND, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                         text=True, encoding='utf-8', errors='replace', bufsize=1, **kw)
    t0 = time.time()
    for ln in p.stdout:
        ln = ln.strip()
        if ln:
            on_line(ln)
        if time.time() - t0 > timeout:
            p.kill()
            raise TimeoutError('설치 시간 초과')
    return p.wait()


@admin_bp.route('/install/sentiment', methods=['POST'])
def install_sentiment():
    tm = TaskManager()
    cur = _INSTALL.get('task_id')
    if cur:
        t = tm.get_task(cur)
        if t and t.status.value in ('pending', 'processing'):
            return jsonify({'success': True, 'data': {'task_id': cur, 'reused': True}})
    task_id = tm.create_task(task_type='install_sentiment', metadata={'model': SENTIMENT_MODEL})
    _INSTALL['task_id'] = task_id

    def upd(pct, msg, stage):
        tm.update_task(task_id, status=TaskStatus.PROCESSING, progress=int(pct), message=msg[:300],
                       progress_detail={'stage': stage})

    def run():
        py = sys.executable
        try:
            upd(2, 'pip 확인 중', 'pip')
            if _run_streamed([py, '-m', 'pip', '--version'], lambda s: None) != 0:
                _run_streamed([py, '-m', 'ensurepip', '--upgrade'], lambda s: upd(3, s, 'pip'))
            steps = [
                ('torch', 5, 55, [py, '-m', 'pip', 'install', '--upgrade', 'torch',
                                  '--index-url', 'https://download.pytorch.org/whl/cpu']),
                ('transformers', 55, 75, [py, '-m', 'pip', 'install', '--upgrade', 'transformers>=4.40', 'safetensors']),
                ('model', 75, 98, [py, '-c',
                                   "from transformers import pipeline;"
                                   f"p=pipeline('text-classification', model='{SENTIMENT_MODEL}', device=-1);"
                                   "print('테스트:', p(['삼성전자 실적 기대감에 매수세 유입', '반도체 업황 둔화로 급락'])))"]),
            ]
            label = {'torch': 'PyTorch(CPU) 설치', 'transformers': 'transformers 설치', 'model': '감성 모델 내려받기·테스트'}
            for name, a, b, cmd in steps:
                state = {'pct': a}

                def on_line(s, name=name, a=a, b=b, state=state):
                    state['pct'] = min(b - 1, state['pct'] + 0.4)
                    upd(state['pct'], f"{label[name]} — {s[:200]}", name)
                upd(a, f"{label[name]} 시작", name)
                rc = _run_streamed(cmd, on_line)
                if rc != 0:
                    raise RuntimeError(f"{label[name]} 실패 (코드 {rc}) — 로그를 확인하세요")
            tm.complete_task(task_id, {'model': SENTIMENT_MODEL, 'installed': True})
            tm.update_task(task_id, message='감성 모델 설치 완료 — 다음 분석부터 자동 사용됩니다')
        except Exception as e:
            logger.error(f'감성 모델 설치 실패: {e}')
            tm.fail_task(task_id, str(e)[:400])

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'success': True, 'data': {'task_id': task_id}})


# ─────────────────────────────────────────────────────────── 운영 지표
@admin_bp.route('/metrics', methods=['GET'])
def ops_metrics_summary():
    from ..services import ops_metrics
    try:
        days = int(request.args.get('days') or 7)
    except ValueError:
        days = 7
    try:
        return jsonify({'success': True, 'data': ops_metrics.summary(days)})
    except Exception as e:
        logger.error(f'운영 지표 조회 실패: {e}')
        return jsonify({'success': False, 'error': str(e)[:300]}), 500


# ─────────────────────────────────────────────────────────── 자체 점검 (회귀 테스트 + 실시간 점검)
_SELFTEST: Dict = {'state': 'idle'}
_selftest_lock = threading.Lock()


def _live_checks() -> List[Dict]:
    """실행 중인 서버의 핵심 구성요소가 응답하는지 (데이터는 읽기만)"""
    out: List[Dict] = []

    def chk(name, fn):
        t0 = time.time()
        try:
            detail = fn()
            out.append({'name': name, 'ok': True, 'detail': str(detail)[:160], 'ms': int((time.time() - t0) * 1000)})
        except Exception as e:
            out.append({'name': name, 'ok': False, 'detail': f'{type(e).__name__}: {str(e)[:160]}', 'ms': int((time.time() - t0) * 1000)})

    from ..config import Config
    from ..services import prediction_ledger, ontology_store, ops_metrics, report_library

    def writable():
        p = os.path.join(Config.UPLOAD_FOLDER, '.write_test')
        with open(p, 'w') as f:
            f.write('ok')
        os.remove(p)
        free = shutil.disk_usage(Config.UPLOAD_FOLDER).free / 1e9
        if free < 1:
            raise RuntimeError(f'디스크 여유 {free:.1f}GB — 1GB 미만')
        return f'uploads 쓰기 가능 · 여유 {free:.0f}GB'

    def llm_key():
        if not (os.environ.get('XAI_API_KEY') or os.environ.get('AI_GATEWAY_API_KEY') or os.environ.get('LLM_API_KEY')):
            raise RuntimeError('LLM 키가 없습니다 (XAI_API_KEY 등)')
        return f"모델 {os.environ.get('XAI_LLM_MODEL') or os.environ.get('LLM_MODEL_NAME') or '-'}"

    def ledger():
        sm = prediction_ledger.summary()
        return f"예측 {sm['counts']['total']}건 · 채점 {sm['n_scored']}건"

    def onto():
        st = ontology_store.stats()
        return f"트리플 {st['triples']:,} · 보고서 {st['reports']}"

    def library():
        return f"보고서 {len(report_library.list_reports(limit=2000))}건"

    def ops():
        s = ops_metrics.summary(1)
        if not s['installed'].get('llm'):
            raise RuntimeError('LLM 계측이 설치되지 않았습니다')
        return f"24시간 LLM 호출 {s['total']['calls']}건 · 오류율 {s['total']['error_rate'] if s['total']['error_rate'] is not None else '-'}%"

    def web():
        info = dist_info()
        if not info.get('exists'):
            raise RuntimeError('frontend/dist 없음 — 화면 빌드 필요')
        return f"빌드 {info.get('built_at')}" + (' · 소스가 더 최신 — 다시 빌드 권장' if info.get('stale') else '')

    for name, fn in (('데이터 폴더', writable), ('LLM 키', llm_key), ('예측 원장', ledger), ('누적 온톨로지', onto),
                     ('보고서 보관함', library), ('운영 지표', ops), ('화면 빌드', web)):
        chk(name, fn)
    return out


def _selftest_job():
    t0 = time.time()
    env = dict(os.environ, WEAIDSTOCK_TESTING='1', PYTHONIOENCODING='utf-8')
    kw = {'creationflags': 0x08000000} if os.name == 'nt' else {}
    try:
        p = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.', '-v'], cwd=BACKEND, env=env,
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600, **kw)
        text = (p.stderr or '') + '\n' + (p.stdout or '')
        lines = [ln for ln in text.splitlines() if not re.match(r'^\[\d\d:\d\d:\d\d\]', ln.strip())]
        def case_of(t, dotted):
            parts = dotted.split('.')
            return parts[-2] if parts[-1] == t and len(parts) > 1 else parts[-1]
        results = []
        for ln in lines:
            m = re.match(r'^(test_\w+) \(([\w.]+)\) \.\.\. (ok|FAIL|ERROR|skipped)', ln.strip())
            if m:
                results.append({'test': m.group(1), 'case': case_of(m.group(1), m.group(2)), 'status': m.group(3)})
        ran = re.search(r'Ran (\d+) tests? in ([\d.]+)s', text)
        fails = re.findall(r'^(?:FAIL|ERROR): (test_\w+) \(([\w.]+)\)', text, re.M)
        _SELFTEST.update(state='done', rc=p.returncode, ok=p.returncode == 0, ran=int(ran.group(1)) if ran else len(results),
                         sec=round(time.time() - t0, 1), results=results,
                         failed=[f'{case_of(t, c)}.{t}' for t, c in fails],
                         tail='\n'.join(lines[-80:]) if p.returncode else '\n'.join(lines[-6:]))
    except subprocess.TimeoutExpired:
        _SELFTEST.update(state='done', ok=False, rc=None, error='10분 안에 끝나지 않았습니다', sec=round(time.time() - t0, 1))
    except Exception as e:
        _SELFTEST.update(state='done', ok=False, rc=None, error=f'{type(e).__name__}: {str(e)[:200]}', sec=round(time.time() - t0, 1))
    finally:
        _SELFTEST['finished_at'] = datetime.now().isoformat(timespec='seconds')
        logger.info(f"자체 점검 완료: {'통과' if _SELFTEST.get('ok') else '실패'} · 테스트 {_SELFTEST.get('ran')}개 · {_SELFTEST.get('sec')}초")


@admin_bp.route('/selftest', methods=['POST'])
def selftest_start():
    with _selftest_lock:
        if _SELFTEST.get('state') == 'running':
            return jsonify({'success': True, 'data': _SELFTEST})
        _SELFTEST.clear()
        _SELFTEST.update(state='running', started_at=datetime.now().isoformat(timespec='seconds'))
    _SELFTEST['live'] = _live_checks()
    threading.Thread(target=_selftest_job, daemon=True, name='selftest').start()
    return jsonify({'success': True, 'data': _SELFTEST})


@admin_bp.route('/selftest', methods=['GET'])
def selftest_status():
    return jsonify({'success': True, 'data': _SELFTEST})


# 선택 패키지 묶음 — 설정 화면의 버튼으로만 설치 (PyPI)
PKG_GROUPS = {
    'ontology': {'label': '온톨로지 도구 (SPARQL·표준 SHACL)', 'pkgs': ['rdflib>=7.0', 'pyshacl>=0.26'], 'mods': ['rdflib', 'pyshacl']},
    'krx': {'label': 'KRX 수급·공매도 (pykrx)', 'pkgs': ['pykrx'], 'mods': ['pykrx']},
}
_PKG_TASK: Dict[str, str] = {}


@admin_bp.route('/install/packages', methods=['GET'])
def packages_status():
    return jsonify({'success': True, 'data': {g: {'label': v['label'], 'installed': all(_installed(m) for m in v['mods']),
                                                  'task_id': _PKG_TASK.get(g)} for g, v in PKG_GROUPS.items()}})


@admin_bp.route('/install/packages', methods=['POST'])
def install_packages():
    group = str((request.get_json(silent=True) or {}).get('group') or '')
    if group not in PKG_GROUPS:
        return jsonify({'success': False, 'error': f'알 수 없는 묶음: {group}'}), 400
    tm = TaskManager()
    cur = _PKG_TASK.get(group)
    if cur:
        t = tm.get_task(cur)
        if t and t.status.value in ('pending', 'processing'):
            return jsonify({'success': True, 'data': {'task_id': cur, 'reused': True}})
    spec = PKG_GROUPS[group]
    task_id = tm.create_task(task_type=f'install_{group}', metadata={'packages': spec['pkgs']})
    _PKG_TASK[group] = task_id

    def run():
        py = sys.executable
        state = {'pct': 5}
        tail: List[str] = []
        try:
            def on_line(line):
                tail.append(line[:300])
                del tail[:-40]
                state['pct'] = min(95, state['pct'] + 1)
                tm.update_task(task_id, status=TaskStatus.PROCESSING, progress=int(state['pct']),
                               message=f"{spec['label']} 설치 — {line[:200]}")
            tm.update_task(task_id, status=TaskStatus.PROCESSING, progress=3, message=f"{spec['label']} 설치 시작")
            # uv 로 만든 가상환경에는 pip 이 없을 수 있음 → ensurepip 으로 먼저 준비
            if _run_streamed([py, '-m', 'pip', '--version'], lambda s_: None) != 0:
                tm.update_task(task_id, status=TaskStatus.PROCESSING, progress=4, message='pip 준비 중 (ensurepip)')
                if _run_streamed([py, '-m', 'ensurepip', '--upgrade'], on_line) != 0:
                    raise RuntimeError('pip 을 준비하지 못했습니다 — ' + ' / '.join(tail[-3:])[:300])
            rc = _run_streamed([py, '-m', 'pip', 'install', '--upgrade'] + spec['pkgs'], on_line)
            if rc != 0:
                logger.error(f"{group} 설치 출력:\n" + '\n'.join(tail[-20:]))
                raise RuntimeError(f"pip 설치 실패 (코드 {rc}) — " + ' / '.join(tail[-3:])[:400])
            importlib.invalidate_caches()
            tm.complete_task(task_id, {'group': group, 'installed': all(_installed(m) for m in spec['mods'])})
            tm.update_task(task_id, message=f"{spec['label']} 설치 완료 — 바로 사용할 수 있습니다")
        except Exception as e:
            logger.error(f'{group} 설치 실패: {e}')
            tm.fail_task(task_id, str(e)[:400])

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'success': True, 'data': {'task_id': task_id}})


# ─────────────────────────────────────────────────────────── 원격 접속 (stock.webig.ai) · 화면 빌드 · 터널
_BUILD: Dict = {'state': 'idle', 'started_at': None, 'finished_at': None, 'rc': None, 'error': None, 'tail': []}
_build_lock = threading.Lock()


def _build_job():
    node = shutil.which('node') or r'C:\Program Files\nodejs\node.exe'
    vite = os.path.join(FRONTEND, 'node_modules', 'vite', 'bin', 'vite.js')
    out_tmp = os.path.join(FRONTEND, 'dist_build')
    old = os.path.join(FRONTEND, 'dist_old')
    tail: List[str] = []
    try:
        if not os.path.exists(node):
            raise RuntimeError('node.exe 를 찾지 못했습니다')
        if not os.path.exists(vite):
            raise RuntimeError('frontend/node_modules 에 vite 가 없습니다 (npm install 필요)')
        log_path = os.path.join(BACKEND, 'logs', 'web_build.log')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        kw = {'creationflags': _NOWIN} if os.name == 'nt' else {}
        env = dict(os.environ, NODE_ENV='production', FORCE_COLOR='0')
        with open(log_path, 'a', encoding='utf-8') as log:
            log.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 화면 빌드 =====\n")
            p = subprocess.Popen([node, vite, 'build', '--outDir', 'dist_build', '--emptyOutDir'], cwd=FRONTEND, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                 errors='replace', stdin=subprocess.DEVNULL, **kw)
            t0 = time.time()
            for ln in p.stdout:
                ln = re.sub(r'\x1b\[[0-9;]*m', '', ln.rstrip())
                if ln:
                    log.write(ln + '\n')
                    tail.append(ln)
                    _BUILD['tail'] = tail[-14:]
                if time.time() - t0 > 600:
                    p.kill()
                    raise RuntimeError('빌드 시간 초과 (10분)')
            rc = p.wait()
        _BUILD['rc'] = rc
        if rc != 0 or not os.path.exists(os.path.join(out_tmp, 'index.html')):
            raise RuntimeError(f'빌드 실패 (코드 {rc}) — logs/web_build.log 확인')
        # 새 빌드로 교체 (파일이 잠깐 열려 있으면 몇 번 재시도, 끝내 안 되면 덮어쓰기)
        swapped = False
        for _ in range(6):
            try:
                if os.path.exists(old):
                    shutil.rmtree(old, ignore_errors=True)
                if os.path.exists(DIST):
                    os.rename(DIST, old)
                os.rename(out_tmp, DIST)
                swapped = True
                break
            except OSError:
                time.sleep(0.5)
        if not swapped:
            shutil.copytree(out_tmp, DIST, dirs_exist_ok=True)
            shutil.rmtree(out_tmp, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
        _BUILD.update(state='done', finished_at=datetime.now().isoformat(timespec='seconds'), error=None)
        logger.info('화면 빌드 완료 → frontend/dist')
    except Exception as e:
        _BUILD.update(state='error', finished_at=datetime.now().isoformat(timespec='seconds'), error=str(e)[:400])
        logger.error(f'화면 빌드 실패: {e}')


@admin_bp.route('/web/build', methods=['POST'])
def web_build():
    """frontend 를 빌드해 frontend/dist 로 교체 — 백엔드(5001)가 그대로 서빙 (공개 주소용)"""
    with _build_lock:
        if _BUILD['state'] == 'running':
            return jsonify({'success': True, 'data': dict(_BUILD, reused=True)})
        _BUILD.update(state='running', started_at=datetime.now().isoformat(timespec='seconds'), finished_at=None,
                      rc=None, error=None, tail=[])
        threading.Thread(target=_build_job, daemon=True, name='web-build').start()
    return jsonify({'success': True, 'data': dict(_BUILD)})


@admin_bp.route('/web/build', methods=['GET'])
def web_build_status():
    return jsonify({'success': True, 'data': dict(_BUILD, dist=dist_info())})


def _remote_code_set() -> bool:
    return len((os.environ.get('REMOTE_ACCESS_CODE') or '').strip()) >= 8


@admin_bp.route('/remote/status', methods=['GET'])
def remote_status():
    check = request.args.get('check') in ('1', 'true', 'yes')
    url = public_url()
    tun = tunnel_manager.status(check_url=url if check else None)
    return jsonify({'success': True, 'data': {
        'public_url': url,
        'mode': remote_mode(),
        'code_set': _remote_code_set(),
        'access': access_status(check=check),
        'tunnel': tun,
        'dist': dist_info(),
        'build': dict(_BUILD),
    }})


@admin_bp.route('/remote/tunnel/start', methods=['POST'])
def remote_tunnel_start():
    """사용자가 설정 화면에서 '연결'을 눌렀을 때 — cloudflared 가 없으면 공식 릴리스에서 내려받은 뒤 실행"""
    res = tunnel_manager.start(allow_download=True)
    code = 200 if res.get('ok') else 400
    return jsonify({'success': bool(res.get('ok')), 'data': res, 'error': res.get('error')}), code


@admin_bp.route('/remote/tunnel/stop', methods=['POST'])
def remote_tunnel_stop():
    res = tunnel_manager.stop()
    return jsonify({'success': bool(res.get('ok')), 'data': res, 'error': res.get('error')})
