"""
Cloudflare Tunnel 커넥터(cloudflared) 관리 — stock.webig.ai 를 이 PC 의 백엔드(127.0.0.1:5001)로 연결

- 토큰(CLOUDFLARE_TUNNEL_TOKEN)은 사용자가 설정 화면에 붙여 넣은 값만 사용합니다. 명령줄이 아니라 환경변수(TUNNEL_TOKEN)로 넘깁니다.
- cloudflared.exe 를 못 찾으면, 사용자가 설정 화면에서 '연결'을 누를 때 Cloudflare 공식 GitHub 릴리스에서 backend/bin 으로 내려받습니다.
- 윈도우 서비스로 이미 설치돼 돌고 있으면 따로 띄우지 않습니다.
- REMOTE_TUNNEL=on(기본)이고 토큰이 있으면 백엔드가 켜질 때 함께 실행하고, 1분마다 살아 있는지 확인합니다.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Dict, List, Optional

import requests

from ..utils.logger import get_logger

logger = get_logger('mirofish.tunnel')

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
BACKEND = os.path.join(ROOT, 'backend')
BIN_DIR = os.path.join(BACKEND, 'bin')
LOCAL_EXE = os.path.join(BIN_DIR, 'cloudflared.exe')
LOG_PATH = os.path.join(BACKEND, 'logs', 'cloudflared.log')
PID_PATH = os.path.join(BACKEND, 'logs', 'cloudflared.pid')
DOWNLOAD_URL = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
IS_WIN = os.name == 'nt'
_NOWIN = 0x08000000 if IS_WIN else 0
TOKEN_RE = re.compile(r'eyJ[A-Za-z0-9_\-+/=]{40,}')

_lock = threading.Lock()
_dl: Dict = {'state': 'idle', 'bytes': 0, 'total': 0, 'error': None}
_watch: Dict = {'thread': None, 'last_error': None, 'last_start': None}


# ─────────────────────────────────────────────────────────── 토큰
def extract_token(text: str) -> Optional[str]:
    """토큰만, 또는 대시보드의 'cloudflared.exe service install eyJ…' 명령 전체를 받아 토큰만 반환"""
    m = TOKEN_RE.search(text or '')
    return m.group(0) if m else None


def token_info(token: Optional[str]) -> Optional[Dict]:
    """토큰은 base64 JSON {a: 계정, t: 터널 id, s: 비밀} — 비밀은 돌려주지 않음"""
    if not token:
        return None
    try:
        pad = '=' * (-len(token) % 4)
        data = json.loads(base64.b64decode(token + pad, altchars=b'-_' if ('-' in token or '_' in token) else None))
        if not all(k in data for k in ('a', 't', 's')):
            return None
        return {'account': str(data['a']), 'tunnel_id': str(data['t'])}
    except Exception:
        return None


def _token() -> Optional[str]:
    return extract_token(os.environ.get('CLOUDFLARE_TUNNEL_TOKEN') or '')


# ─────────────────────────────────────────────────────────── 실행 파일 · 프로세스
def find_exe() -> Optional[str]:
    cands = [shutil.which('cloudflared'), LOCAL_EXE]
    if IS_WIN:
        pf86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
        pf = os.environ.get('ProgramFiles', r'C:\Program Files')
        la = os.environ.get('LOCALAPPDATA', '')
        cands += [os.path.join(pf86, 'cloudflared', 'cloudflared.exe'), os.path.join(pf, 'cloudflared', 'cloudflared.exe')]
        if la:
            cands.append(os.path.join(la, 'Microsoft', 'WinGet', 'Links', 'cloudflared.exe'))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _run(cmd: List[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=_NOWIN,
                          encoding='utf-8', errors='replace')


def service_state() -> Optional[str]:
    """윈도우 서비스 'cloudflared' 상태 (RUNNING / STOPPED …) — 없으면 None"""
    if not IS_WIN:
        return None
    try:
        r = _run(['sc', 'query', 'cloudflared'])
    except Exception:
        return None
    m = re.search(r'\b(RUNNING|STOPPED|START_PENDING|STOP_PENDING|PAUSED)\b', r.stdout or '')
    return m.group(1) if m else None


def running_pids() -> List[int]:
    if not IS_WIN:
        return []
    try:
        r = _run(['tasklist', '/FI', 'IMAGENAME eq cloudflared.exe', '/FO', 'CSV', '/NH'])
    except Exception:
        return []
    pids = []
    for ln in (r.stdout or '').splitlines():
        parts = [p.strip('"') for p in ln.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith('cloudflared'):
            try:
                pids.append(int(parts[1].strip('"')))
            except ValueError:
                pass
    return pids


def _our_pid() -> Optional[int]:
    try:
        with open(PID_PATH, 'r', encoding='utf-8') as f:
            pid = int(f.read().strip())
        return pid if pid in running_pids() else None
    except (OSError, ValueError):
        return None


def _tail_log(n: int = 12) -> List[str]:
    try:
        with open(LOG_PATH, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 20000))
            lines = f.read().decode('utf-8', errors='replace').splitlines()
        return [TOKEN_RE.sub('eyJ••••', ln) for ln in lines[-n:]]
    except OSError:
        return []


# ─────────────────────────────────────────────────────────── 내려받기
def _download():
    tmp = LOCAL_EXE + '.part'
    try:
        os.makedirs(BIN_DIR, exist_ok=True)
        with requests.get(DOWNLOAD_URL, stream=True, timeout=(15, 60), allow_redirects=True) as r:
            r.raise_for_status()
            _dl['total'] = int(r.headers.get('content-length') or 0)
            _dl['bytes'] = 0
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        f.write(chunk)
                        _dl['bytes'] += len(chunk)
        with open(tmp, 'rb') as f:
            if f.read(2) != b'MZ':
                raise RuntimeError('내려받은 파일이 윈도우 실행 파일이 아닙니다')
        if _dl['bytes'] < 5_000_000:
            raise RuntimeError(f"파일 크기가 너무 작습니다 ({_dl['bytes']} bytes)")
        os.replace(tmp, LOCAL_EXE)
        _dl['state'] = 'done'
        logger.info(f"cloudflared 내려받기 완료: {LOCAL_EXE} ({_dl['bytes'] // 1_000_000}MB)")
        return True
    except Exception as e:
        _dl.update(state='error', error=str(e)[:300])
        logger.error(f'cloudflared 내려받기 실패: {e}')
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


# ─────────────────────────────────────────────────────────── 시작 · 중지
def _spawn(exe: str, token: str) -> int:
    env = dict(os.environ)
    env['TUNNEL_TOKEN'] = token
    env.pop('CLOUDFLARE_TUNNEL_TOKEN', None)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    log = open(LOG_PATH, 'ab')
    base = 0x00000200 | _NOWIN              # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW — 백엔드 재시작과 무관하게 유지
    last = None
    for flags in ((base | 0x01000000, base) if IS_WIN else (0,)):
        try:
            p = subprocess.Popen([exe, 'tunnel', '--no-autoupdate', 'run'], cwd=BACKEND, env=env,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags, close_fds=True)
            with open(PID_PATH, 'w', encoding='utf-8') as f:
                f.write(str(p.pid))
            _watch['last_start'] = time.strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f'cloudflared 시작 pid={p.pid}')
            return p.pid
        except OSError as e:
            last = e
    raise RuntimeError(f'cloudflared 실행 실패: {last}')


def start(allow_download: bool = False) -> Dict:
    """토큰이 있으면 커넥터 실행. 이미 (서비스든 우리 프로세스든) 돌고 있으면 그대로 둠."""
    token = _token()
    if not token:
        return {'ok': False, 'error': '터널 토큰이 없습니다 — 설정의 “Cloudflare 터널 토큰”에 붙여 넣으세요'}
    if not token_info(token):
        return {'ok': False, 'error': '터널 토큰 형식이 올바르지 않습니다'}
    with _lock:
        if service_state() == 'RUNNING':
            return {'ok': True, 'state': 'service', 'message': '윈도우 서비스로 이미 실행 중입니다'}
        pids = running_pids()
        if pids:
            return {'ok': True, 'state': 'running', 'pids': pids}
        exe = find_exe()
        if not exe:
            if not allow_download:
                return {'ok': False, 'error': 'cloudflared 가 설치되어 있지 않습니다'}
            if _dl['state'] == 'downloading':
                return {'ok': True, 'state': 'downloading'}
            _dl.update(state='downloading', bytes=0, total=0, error=None)

            def job():
                if _download():
                    try:
                        _spawn(LOCAL_EXE, _token() or token)
                    except Exception as e:
                        _watch['last_error'] = str(e)[:300]
            threading.Thread(target=job, daemon=True, name='cloudflared-download').start()
            return {'ok': True, 'state': 'downloading'}
        try:
            pid = _spawn(exe, token)
        except Exception as e:
            _watch['last_error'] = str(e)[:300]
            return {'ok': False, 'error': str(e)[:300]}
    return {'ok': True, 'state': 'started', 'pid': pid}


def stop() -> Dict:
    """이 앱이 띄운 커넥터만 종료 (윈도우 서비스는 건드리지 않음)"""
    pid = _our_pid()
    if not pid:
        return {'ok': True, 'stopped': None, 'message': '이 앱이 띄운 커넥터가 없습니다'}
    try:
        r = _run(['taskkill', '/PID', str(pid), '/F'])
        ok = r.returncode == 0
    except Exception as e:
        return {'ok': False, 'error': str(e)[:200]}
    try:
        os.remove(PID_PATH)
    except OSError:
        pass
    logger.info(f'cloudflared 중지 pid={pid} ok={ok}')
    return {'ok': ok, 'stopped': pid}


def check_public(url: str, timeout: int = 10) -> Dict:
    """공개 주소 /health 가 이 백엔드까지 오는지 확인 (Cloudflare → 터널 → 이 PC)"""
    t0 = time.time()
    try:
        r = requests.get(url.rstrip('/') + '/health', timeout=timeout, allow_redirects=False,
                         headers={'User-Agent': 'WeAidStock-selfcheck'})
        ms = int((time.time() - t0) * 1000)
        body = r.text[:400]
        if r.status_code == 200 and 'WeAidStock' in body:
            return {'ok': True, 'status': 200, 'ms': ms, 'message': '공개 주소가 이 PC 로 연결됩니다'}
        if '1033' in body or r.status_code == 530:
            msg = '터널에 연결된 커넥터가 없습니다 (Cloudflare 1033)'
        elif r.status_code == 502:
            msg = '커넥터는 연결됐지만 백엔드(5001)에 닿지 못했습니다 (502)'
        else:
            msg = f'HTTP {r.status_code}'
        return {'ok': False, 'status': r.status_code, 'ms': ms, 'message': msg}
    except Exception as e:
        return {'ok': False, 'status': None, 'ms': int((time.time() - t0) * 1000),
                'message': f'{type(e).__name__}: {str(e)[:160]}'}


def status(check_url: Optional[str] = None) -> Dict:
    token = _token()
    exe = find_exe()
    out = {
        'platform_ok': IS_WIN,
        'token_set': bool(token),
        'token': token_info(token),
        'exe': exe,
        'service': service_state(),
        'pids': running_pids(),
        'our_pid': _our_pid(),
        'autostart': (os.environ.get('REMOTE_TUNNEL') or 'on').strip().lower() != 'off',
        'download': dict(_dl),
        'last_error': _watch.get('last_error'),
        'last_start': _watch.get('last_start'),
        'log_tail': _tail_log(),
    }
    out['running'] = out['service'] == 'RUNNING' or bool(out['pids'])
    if check_url:
        out['public'] = check_public(check_url)
    return out


# ─────────────────────────────────────────────────────────── 자동 실행 · 감시
def _loop(interval: int):
    time.sleep(3)
    while True:
        try:
            if (os.environ.get('REMOTE_TUNNEL') or 'on').strip().lower() != 'off' and _token() and find_exe():
                res = start(allow_download=False)
                if not res.get('ok'):
                    _watch['last_error'] = res.get('error')
        except Exception as e:
            _watch['last_error'] = str(e)[:300]
        time.sleep(interval)


def autostart(debug: bool, interval: int = 60) -> bool:
    """create_app() 에서 1회 호출 — 리로더 부모 프로세스에서는 시작하지 않음"""
    if not IS_WIN:
        return False
    if debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return False
    t = _watch.get('thread')
    if t is not None and t.is_alive():
        return True
    t = threading.Thread(target=_loop, args=(interval,), daemon=True, name='cloudflared-watch')
    _watch['thread'] = t
    t.start()
    return True
