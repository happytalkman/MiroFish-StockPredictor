"""
원격 접속 게이트 + 빌드된 화면 서빙 (stock.webig.ai · Cloudflare Tunnel)

원격 판정
  - 접속 주소가 이 PC(루프백)가 아니거나
  - 프록시 헤더(CF-Connecting-IP · CF-Ray · X-Forwarded-For …)가 붙어 있으면 원격입니다.
    Cloudflare Tunnel 은 이 PC 의 cloudflared 가 127.0.0.1 로 접속하므로 주소만 보면 로컬처럼 보입니다.
    Cloudflare 가장자리는 이 헤더를 항상 붙이고, 방문자가 지울 수 없습니다.

원격 요청 처리 (REMOTE_ACCESS)
  - /api/admin/*          : 항상 403 — 키·설정·재시작은 이 PC 에서만
  - access (기본)          : Cloudflare Access 가 붙여 주는 JWT(Cf-Access-Jwt-Assertion)를 RS256 서명·aud·iss·만료까지 검증.
                            같은 와이파이에서 5001 로 바로 들어오는 요청처럼 Access 를 거치지 않은 요청은 차단
  - code                  : 접속 코드(REMOTE_ACCESS_CODE)로 로그인해야 사용. 코드가 비어 있으면 원격 전부 차단
  - open                  : 로그인 없이 허용 (권장하지 않음)
  - off                   : 원격 전부 차단
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from html import escape
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .utils.logger import get_logger

logger = get_logger('mirofish.remote')

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
BACKEND = os.path.join(ROOT, 'backend')
DIST = os.path.join(ROOT, 'frontend', 'dist')
SECRET_FILE = os.path.join(BACKEND, '.flask_secret')

LOOPBACK = {'127.0.0.1', '::1', '::ffff:127.0.0.1', 'localhost'}
PROXY_HEADERS = ('CF-Connecting-IP', 'CF-Ray', 'CF-Visitor', 'CDN-Loop', 'True-Client-IP',
                 'X-Forwarded-For', 'X-Forwarded-Host', 'X-Real-IP', 'Forwarded')
COOKIE = 'weaid_remote'
COOKIE_DAYS = 30
LOGIN_PATH = '/__weaid/login'
LOGOUT_PATH = '/__weaid/logout'
# 로그인 없이 원격에 열어 두는 경로 (정보 없음)
OPEN_PATHS = {'/health', LOGIN_PATH, LOGOUT_PATH, '/favicon.ico', '/weaidstock-icon.svg', '/icon.png'}


# ─────────────────────────────────────────────────────────── 판정
def is_proxied(req=None) -> bool:
    req = req or request
    return any(req.headers.get(h) for h in PROXY_HEADERS)


def is_remote(req=None) -> bool:
    req = req or request
    return (req.remote_addr or '').strip() not in LOOPBACK or is_proxied(req)


def client_ip(req=None) -> str:
    req = req or request
    return (req.headers.get('CF-Connecting-IP') or req.headers.get('X-Real-IP')
            or (req.headers.get('X-Forwarded-For') or '').split(',')[0].strip() or req.remote_addr or '?')


# Cloudflare Access (Zero Trust) — 이 배포(stock.webig.ai)의 값. 공개 식별자라 비밀이 아니며 환경변수로 바꿀 수 있습니다.
DEFAULT_ACCESS_TEAM = 'weaikorea.cloudflareaccess.com'
DEFAULT_ACCESS_AUD = '8c992b048bfb82a807e5c411ed18d3f98bd98b68e07d0f5a5c6f7b0305636b5f'
MODES = ('access', 'code', 'open', 'off')


def access_team() -> str:
    t = (os.environ.get('CF_ACCESS_TEAM_DOMAIN') or DEFAULT_ACCESS_TEAM).strip().lower()
    return t.replace('https://', '').replace('http://', '').strip('/')


def access_auds() -> List[str]:
    raw = os.environ.get('CF_ACCESS_AUD') or DEFAULT_ACCESS_AUD
    return [a.strip() for a in raw.split(',') if a.strip()]


def access_emails() -> List[str]:
    return [e.strip().lower() for e in (os.environ.get('REMOTE_ACCESS_EMAILS') or '').split(',') if e.strip()]


def mode() -> str:
    default = 'access' if access_team() and access_auds() else 'code'
    m = (os.environ.get('REMOTE_ACCESS') or default).strip().lower()
    return m if m in MODES else default


def _code() -> str:
    return (os.environ.get('REMOTE_ACCESS_CODE') or '').strip()


def public_url() -> str:
    return (os.environ.get('WEAID_PUBLIC_URL') or 'https://stock.webig.ai').strip().rstrip('/')


def internal_authorized(req=None) -> bool:
    req = req or request
    expected = (os.environ.get('CRON_SECRET') or '').strip()
    supplied = (req.headers.get('X-WeAid-Cron') or '').strip()
    return len(expected) >= 24 and bool(supplied) and hmac.compare_digest(supplied, expected)


# ─────────────────────────────────────────────────────────── 비밀키 · 쿠키
def ensure_secret_key(app: Flask):
    """기본값('mirofish-secret-key')이면 쿠키 위조가 가능하므로 PC 마다 무작위 키를 만들어 저장"""
    cur = app.config.get('SECRET_KEY')
    if cur and cur != 'mirofish-secret-key' and len(str(cur)) >= 16:
        return
    key = None
    try:
        if os.path.exists(SECRET_FILE):
            with open(SECRET_FILE, 'r', encoding='utf-8') as f:
                key = f.read().strip() or None
        if not key:
            key = secrets.token_hex(32)
            with open(SECRET_FILE, 'w', encoding='utf-8') as f:
                f.write(key)
    except OSError as e:
        logger.warning(f'세션 비밀키 파일 처리 실패, 이번 실행에만 임시 키 사용: {e}')
        key = key or secrets.token_hex(32)
    app.config['SECRET_KEY'] = key


def _serializer(app: Flask) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='weaid-remote-v1')


def _code_fp(app: Flask) -> str:
    """코드를 바꾸면 기존 로그인 쿠키가 모두 무효가 되도록 코드 지문을 쿠키에 넣음"""
    return hmac.new(app.config['SECRET_KEY'].encode(), _code().encode(), hashlib.sha256).hexdigest()[:24]


def _authed(app: Flask) -> bool:
    raw = request.cookies.get(COOKIE)
    if not raw or not _code():
        return False
    try:
        data = _serializer(app).loads(raw, max_age=COOKIE_DAYS * 86400)
    except BadSignature:
        return False
    return isinstance(data, dict) and hmac.compare_digest(str(data.get('fp', '')), _code_fp(app))


def _https() -> bool:
    v = (request.headers.get('CF-Visitor') or '') + (request.headers.get('X-Forwarded-Proto') or '')
    return 'https' in v.lower() or request.is_secure


# ─────────────────────────────────────────────────────────── Cloudflare Access JWT 검증 (외부 라이브러리 없이 RS256)
_SHA256_DIGESTINFO = bytes.fromhex('3031300d060960864801650304020105000420')
_jwks_lock = threading.Lock()
_jwks: Dict = {'team': None, 'keys': {}, 'at': 0.0, 'tried': 0.0, 'error': None}
_verified: Dict[str, Tuple[dict, float]] = {}      # 토큰 해시 → (claims, exp) — 화면 파일 요청마다 다시 검증하지 않도록
JWKS_TTL, JWKS_RETRY = 3600, 30


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def _rsa_pkcs1_sha256_verify(n: int, e: int, msg: bytes, sig: bytes) -> bool:
    k = (n.bit_length() + 7) // 8
    if len(sig) != k or k < 128:
        return False
    s = int.from_bytes(sig, 'big')
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, 'big')
    t = _SHA256_DIGESTINFO + hashlib.sha256(msg).digest()
    expected = b'\x00\x01' + b'\xff' * (k - len(t) - 3) + b'\x00' + t
    return hmac.compare_digest(em, expected)


def _fetch_jwks(team: str) -> Dict[str, Tuple[int, int]]:
    r = requests.get(f'https://{team}/cdn-cgi/access/certs', timeout=8)
    r.raise_for_status()
    keys = {}
    for k in r.json().get('keys', []):
        if k.get('kty') == 'RSA' and k.get('kid') and k.get('n') and k.get('e'):
            keys[k['kid']] = (int.from_bytes(_b64d(k['n']), 'big'), int.from_bytes(_b64d(k['e']), 'big'))
    if not keys:
        raise RuntimeError('공개키가 비어 있습니다')
    return keys


def _jwk(kid: str) -> Optional[Tuple[int, int]]:
    team = access_team()
    now = time.time()
    with _jwks_lock:
        fresh = _jwks['team'] == team and now - _jwks['at'] < JWKS_TTL
        if fresh and kid in _jwks['keys']:
            return _jwks['keys'][kid]
        if now - _jwks['tried'] < JWKS_RETRY and _jwks['team'] == team:
            return _jwks['keys'].get(kid)
        _jwks['tried'] = now
        try:
            keys = _fetch_jwks(team)
            _jwks.update(team=team, keys=keys, at=now, error=None)
        except Exception as e:
            _jwks['error'] = f'{type(e).__name__}: {str(e)[:160]}'
            logger.warning(f'Cloudflare Access 공개키를 가져오지 못했습니다: {_jwks["error"]}')
        return _jwks['keys'].get(kid) if _jwks['team'] == team else None


def verify_access_jwt(token: str) -> Optional[dict]:
    """Cloudflare Access JWT 검증 → claims (실패하면 None)"""
    if not token or token.count('.') != 2 or len(token) > 8192:
        return None
    th = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    hit = _verified.get(th)
    if hit and hit[1] > now:
        return hit[0]
    try:
        h64, p64, s64 = token.split('.')
        header = json.loads(_b64d(h64))
        if header.get('alg') != 'RS256' or not header.get('kid'):
            return None
        key = _jwk(str(header['kid']))
        if not key or not _rsa_pkcs1_sha256_verify(key[0], key[1], f'{h64}.{p64}'.encode('ascii'), _b64d(s64)):
            return None
        claims = json.loads(_b64d(p64))
    except Exception:
        return None
    exp = float(claims.get('exp') or 0)
    if exp < now - 30 or float(claims.get('nbf') or 0) > now + 60:
        return None
    aud = claims.get('aud')
    aud = [aud] if isinstance(aud, str) else (aud or [])
    if not set(aud) & set(access_auds()):
        return None
    if str(claims.get('iss') or '').rstrip('/') != f'https://{access_team()}':
        return None
    allow = access_emails()
    if allow and str(claims.get('email') or '').lower() not in allow:
        return None
    if len(_verified) > 512:
        _verified.clear()
    _verified[th] = (claims, min(exp, now + 600))
    return claims


def _access_claims() -> Optional[dict]:
    tok = request.headers.get('Cf-Access-Jwt-Assertion') or request.cookies.get('CF_Authorization') or ''
    return verify_access_jwt(tok.strip())


def access_status(check: bool = False) -> Dict:
    out = {'team': access_team(), 'aud': [a[:8] + '…' for a in access_auds()], 'emails': access_emails(),
           'keys': len(_jwks['keys']) if _jwks['team'] == access_team() else 0, 'error': _jwks.get('error')}
    if check:
        try:
            out['keys'] = len(_fetch_jwks(access_team()))
            out['certs_ok'] = True
        except Exception as e:
            out.update(certs_ok=False, error=f'{type(e).__name__}: {str(e)[:160]}')
    return out


# ─────────────────────────────────────────────────────────── 로그인 시도 제한 (IP 당 15분에 8회 실패)
_fail_lock = threading.Lock()
_fails: Dict[str, List[float]] = {}
FAIL_WINDOW, FAIL_MAX = 900, 8


def _blocked(ip: str) -> bool:
    now = time.time()
    with _fail_lock:
        arr = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
        _fails[ip] = arr
        return len(arr) >= FAIL_MAX


def _note_fail(ip: str):
    with _fail_lock:
        _fails.setdefault(ip, []).append(time.time())
        if len(_fails) > 5000:                 # 메모리 보호
            _fails.clear()


def _safe_next(v: str) -> str:
    v = (v or '/').strip()
    if not v.startswith('/') or v.startswith('//') or v.startswith('/\\') or v.startswith(LOGIN_PATH):
        return '/'
    return v[:500]


# ─────────────────────────────────────────────────────────── 화면 (로그인 · 차단 안내)
_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{title}</title>
<link rel="icon" href="/weaidstock-icon.svg" type="image/svg+xml">
<style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0f14;color:#e2e8f0;
font:14px/1.55 'Noto Sans KR','Apple SD Gothic Neo','Segoe UI',system-ui,sans-serif;padding:16px}}
.card{{width:100%;max-width:380px;background:#0f1419;border:1px solid #2a3041;border-radius:14px;padding:28px 24px}}
.brand{{display:flex;align-items:center;gap:10px;margin-bottom:18px}}
.wm{{font:700 18px/1 Inter,system-ui,sans-serif;letter-spacing:-.02em}}.wm b{{color:#60a5fa}}
h1{{font-size:16px;margin:0 0 6px}}p{{margin:0 0 14px;color:#8892a4;font-size:13px}}
label{{display:block;font-size:12px;color:#c9d4e0;margin-bottom:6px}}
input{{width:100%;padding:11px 12px;border-radius:8px;border:1px solid #2a3041;background:#121821;color:#e2e8f0;font-size:15px;letter-spacing:.08em}}
input:focus{{outline:2px solid #3b82f6;outline-offset:1px;border-color:transparent}}
button{{width:100%;margin-top:14px;padding:11px;border:0;border-radius:8px;background:#2563eb;color:#fff;font-weight:600;font-size:14px;cursor:pointer}}
button:hover{{background:#1d4ed8}}.err{{margin:10px 0 0;color:#fca5a5;font-size:12.5px}}
.foot{{margin-top:16px;font-size:11.5px;color:#64748b}}
</style></head><body><main class="card">
<div class="brand"><svg viewBox="0 0 32 32" width="30" height="30" aria-hidden="true"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#2563eb"/><stop offset="100%" stop-color="#10b981"/></linearGradient></defs><rect x="1" y="1" width="30" height="30" rx="8" fill="url(#g)"/><polyline points="7,22 12.5,16 16.5,19.5 24.5,10.5" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><polyline points="19.8,10.5 24.5,10.5 24.5,15.2" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
<span class="wm">WeAid<b>Stock</b></span></div>
{body}
<div class="foot">AI 분석은 참고 정보이며 투자 권유가 아닙니다.</div>
</main></body></html>"""


def _login_page(next_url: str, error: str = '', status: int = 200) -> Response:
    body = (f"<h1>원격 접속</h1><p>이 PC 에서 정한 접속 코드를 입력하세요.</p>"
            f"<form method='post' action='{LOGIN_PATH}' autocomplete='off'>"
            f"<input type='hidden' name='next' value='{escape(next_url)}'>"
            f"<label for='code'>접속 코드</label>"
            f"<input id='code' name='code' type='password' required autofocus inputmode='text' autocomplete='current-password'>"
            f"<button type='submit'>들어가기</button>"
            + (f"<p class='err' role='alert'>{escape(error)}</p>" if error else '') + "</form>")
    html = _PAGE.format(title='WeAidStock · 원격 접속', body=body)
    return Response(html, status=status, mimetype='text/html',
                    headers={'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin'})


def _closed_page() -> Response:
    m = mode()
    if m == 'access':
        body = ("<h1>인증되지 않은 접속</h1><p>Cloudflare Access 로그인을 거친 요청만 받습니다.</p>"
                f"<p><a href='{escape(public_url())}' style='color:#60a5fa'>{escape(public_url().replace('https://', ''))}</a> 로 접속해 "
                "등록된 이메일로 로그인하세요.</p>")
    else:
        reason = '원격 접속이 꺼져 있습니다.' if m == 'off' else '원격 접속 코드가 아직 설정되지 않았습니다.'
        body = (f"<h1>원격 접속 준비 중</h1><p>{reason}</p>"
                "<p>WeAidStock 을 실행 중인 PC 의 <b>설정 → 원격 접속</b>에서 접속 코드를 정하면 열립니다.</p>")
    return Response(_PAGE.format(title='WeAidStock', body=body), status=403, mimetype='text/html',
                    headers={'Cache-Control': 'no-store'})


def _json(status: int, error: str, **extra):
    r = jsonify({'success': False, 'error': error, **extra})
    r.status_code = status
    r.headers['Cache-Control'] = 'no-store'
    return r


# ─────────────────────────────────────────────────────────── 등록
def init_remote_access(app: Flask):
    ensure_secret_key(app)

    @app.before_request
    def _remote_gate():
        if not is_remote():
            return None
        path = request.path or '/'
        if path.startswith('/api/admin'):
            logger.warning(f'원격 관리 API 차단: {client_ip()} {path}')
            return _json(403, '설정·관리 기능은 WeAidStock 을 실행 중인 PC 에서만 쓸 수 있습니다')
        if path in OPEN_PATHS:
            return None
        m = mode()
        if m == 'open':
            return None
        if m == 'access':
            claims = _access_claims()
            if claims:
                g.remote_user = claims.get('email') or claims.get('sub')
                return None
            logger.warning(f'Access 인증 없는 원격 요청 차단: {client_ip()} {path}')
            return _json(401, 'Cloudflare Access 인증이 필요합니다', login=LOGIN_PATH) if path.startswith('/api/') else _closed_page()
        if m == 'off' or not _code():
            return _json(403, '원격 접속이 열려 있지 않습니다') if path.startswith('/api/') else _closed_page()
        if _authed(app):
            return None
        if path.startswith('/api/'):
            return _json(401, '로그인이 필요합니다', login=LOGIN_PATH)
        full = request.full_path if request.query_string else path
        return redirect(f'{LOGIN_PATH}?next={quote(_safe_next(full), safe="/?=&")}', code=302)

    @app.after_request
    def _remote_headers(resp):
        if is_remote():
            resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
            resp.headers.setdefault('Referrer-Policy', 'same-origin')
            resp.headers.setdefault('X-Robots-Tag', 'noindex, nofollow')
        return resp

    @app.route(LOGIN_PATH, methods=['GET', 'POST'])
    def remote_login():
        if not is_remote():
            return redirect('/', code=302)       # 이 PC 에서는 로그인 불필요
        m = mode()
        nxt = _safe_next(request.values.get('next', '/'))
        if m in ('open', 'access'):
            return redirect(nxt, code=302)   # access: 원래 주소로 보내면 Cloudflare 가 로그인 화면을 띄움
        if m == 'off' or not _code():
            return _closed_page()
        if request.method == 'GET':
            return _login_page(nxt)
        ip = client_ip()
        if _blocked(ip):
            return _login_page(nxt, '시도가 너무 많습니다. 15분 뒤 다시 시도하세요.', 429)
        given = (request.form.get('code') or '').strip()
        if not given or not hmac.compare_digest(given.encode(), _code().encode()):
            _note_fail(ip)
            time.sleep(0.6)
            logger.warning(f'원격 로그인 실패: {ip}')
            return _login_page(nxt, '접속 코드가 맞지 않습니다.', 401)
        token = _serializer(app).dumps({'fp': _code_fp(app), 't': int(time.time())})
        resp = redirect(nxt, code=303)
        resp.set_cookie(COOKIE, token, max_age=COOKIE_DAYS * 86400, httponly=True, secure=_https(),
                        samesite='Lax', path='/')
        logger.info(f'원격 로그인: {ip}')
        return resp

    @app.route(LOGOUT_PATH, methods=['GET', 'POST'])
    def remote_logout():
        resp = redirect(LOGIN_PATH if is_remote() else '/', code=303)
        resp.delete_cookie(COOKIE, path='/')
        return resp

    # ── 빌드된 화면(frontend/dist) 서빙 — /api 가 아닌 GET 요청. 없는 경로는 index.html (Vue 라우터)
    @app.route('/', defaults={'path': ''}, methods=['GET'])
    @app.route('/<path:path>', methods=['GET'])
    def spa(path: str):
        if path.startswith('api/') or path == 'api':
            return _json(404, '없는 API 입니다')
        index = os.path.join(DIST, 'index.html')
        if not os.path.exists(index):
            body = ("<h1>화면이 아직 빌드되지 않았습니다</h1>"
                    "<p>이 PC 의 설정 화면(<b>원격 접속 → 화면 빌드</b>)에서 빌드하면 여기서 열립니다.</p>")
            return Response(_PAGE.format(title='WeAidStock', body=body), status=503, mimetype='text/html',
                            headers={'Cache-Control': 'no-store'})
        if path:
            full = os.path.normpath(os.path.join(DIST, path))
            if full.startswith(DIST + os.sep) and os.path.isfile(full):
                resp = send_from_directory(DIST, path)
                if path.startswith('assets/'):
                    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
                else:
                    resp.headers['Cache-Control'] = 'no-cache'
                return resp
            if os.path.splitext(path)[1] and not path.endswith('.html'):
                return Response('Not Found', status=404, mimetype='text/plain')
        resp = send_from_directory(DIST, 'index.html')
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    logger.info(f'원격 접속 게이트: 모드={mode()}'
                + (f' · Access {access_team()}' if mode() == 'access' else f' · 코드 {"설정됨" if _code() else "미설정"}')
                + f' · 화면 빌드 {"있음" if os.path.exists(os.path.join(DIST, "index.html")) else "없음"}')


def dist_info() -> Dict:
    index = os.path.join(DIST, 'index.html')
    if not os.path.exists(index):
        return {'exists': False}
    built = os.path.getmtime(index)
    newest = 0.0
    src = os.path.join(ROOT, 'frontend', 'src')
    for base, _dirs, files in os.walk(src):
        for f in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(base, f)))
            except OSError:
                pass
    for f in ('index.html', 'vite.config.js', 'package.json'):
        p = os.path.join(ROOT, 'frontend', f)
        if os.path.exists(p):
            newest = max(newest, os.path.getmtime(p))
    return {'exists': True, 'built_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(built)),
            'stale': newest > built + 1}
