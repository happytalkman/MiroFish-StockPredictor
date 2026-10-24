"""원격 접속 게이트 — Cloudflare Access JWT(RS256) 검증, 관리 API 원격 차단, 공개 경로

테스트 전용 RSA 키(1024비트, 이 파일에만 존재)로 토큰을 직접 서명해 검증 경로 전체를 확인합니다.
"""
import base64
import hashlib
import json
import os
import time

from tests.base import TempUploadCase

# 테스트 전용 키 — 실제 서비스와 무관
TEST_N = int(
    'be5b83b5b55c50d7024eb4c51af2f05d5aaf4dc09a9d3daffa14bdc2bc74219d6771d340fe52ef41867ffc8280d87ca0'
    'f8b223469e4a34ceecd246b3d3908e9bed627990e10fee27f8934ef71b4db9f96133e1c48c24040561a09caf021bb1e0'
    '2876dd54d02d7672fe90b39a73f6ddffefc1aa45d00b031b813984f6188a412d', 16)
TEST_D = int(
    '87d766b65525fbca73ab16f551b5ec31b27547571afefaf689d0235ac4815b3ba88661eb0f92bf8a532122613ef13e7e'
    'e0fabe91b683de0b8c94c23fb1ca86f922c48331154612bfa1bde1b007d701f7672b74c00d4da8878f7455ab37a3e73a'
    '09bdcaab8ded537c7d55335d89bbf107fe61db6a09047862b24759592b04835', 16)
TEST_E = 65537
EVIL_D = TEST_D - 2          # 잘못된 서명 키


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def sign(payload: dict, d=TEST_D, kid='k1', alg='RS256') -> str:
    h = _b64(json.dumps({'alg': alg, 'kid': kid, 'typ': 'JWT'}).encode())
    p = _b64(json.dumps(payload).encode())
    k = (TEST_N.bit_length() + 7) // 8
    t = bytes.fromhex('3031300d060960864801650304020105000420') + hashlib.sha256(f'{h}.{p}'.encode()).digest()
    em = b'\x00\x01' + b'\xff' * (k - len(t) - 3) + b'\x00' + t
    sig = pow(int.from_bytes(em, 'big'), d, TEST_N).to_bytes(k, 'big')
    return f'{h}.{p}.{_b64(sig)}'


class RemoteAccessTest(TempUploadCase):
    CF = {'CF-Connecting-IP': '203.0.113.9', 'CF-Ray': 'abc-ICN'}
    PROBE = '/api/ledger/summary'

    def setUp(self):
        super().setUp()
        self._env = {k: os.environ.pop(k, None) for k in ('REMOTE_ACCESS', 'REMOTE_ACCESS_CODE', 'CF_ACCESS_AUD',
                                                          'CF_ACCESS_TEAM_DOMAIN', 'REMOTE_ACCESS_EMAILS')}
        import app.remote_access as ra
        self.ra = ra
        self._fetch = ra._fetch_jwks
        ra._fetch_jwks = lambda team: {'k1': (TEST_N, TEST_E)}
        ra._jwks.update(team=None, keys={}, at=0.0, tried=0.0, error=None)
        ra._verified.clear()
        from app import create_app
        app = create_app()
        app.testing = True
        self.c = app.test_client()

    def tearDown(self):
        self.ra._fetch_jwks = self._fetch
        self.ra._jwks.update(team=None, keys={}, at=0.0, tried=0.0, error=None)
        self.ra._verified.clear()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def tok(self, **kw):
        now = int(time.time())
        claims = {'aud': [kw.pop('aud', self.ra.access_auds()[0])], 'iss': kw.pop('iss', f'https://{self.ra.access_team()}'),
                  'exp': kw.pop('exp', now + 3600), 'iat': now, 'nbf': now - 5,
                  'email': kw.pop('email', 'happytalkman@gmail.com'), 'sub': 'u1', 'type': 'app'}
        return sign(claims, **kw)

    def H(self, t):
        return dict(self.CF, **{'Cf-Access-Jwt-Assertion': t})

    def test_local_requests_pass(self):
        self.assertEqual(self.c.get(self.PROBE).status_code, 200)
        self.assertEqual(self.c.get('/api/admin/metrics').status_code, 200)

    def test_valid_token_and_admin_block(self):
        self.assertEqual(self.ra.mode(), 'access')
        self.assertEqual(self.c.get(self.PROBE, headers=self.H(self.tok())).status_code, 200)
        self.assertEqual(self.c.get('/api/admin/settings', headers=self.H(self.tok())).status_code, 403)
        self.assertEqual(self.c.get('/health', headers=self.CF).status_code, 200)

    def test_rejections(self):
        r = self.c.get(self.PROBE, headers=self.CF)
        self.assertEqual(r.status_code, 401)
        self.assertTrue(r.get_json().get('login'))
        bad = [self.tok(d=EVIL_D), self.tok(aud='deadbeef'), self.tok(iss='https://evil.cloudflareaccess.com'),
               self.tok(exp=int(time.time()) - 120), self.tok(kid='nope'), self.tok(alg='HS256')]
        for t in bad:
            self.assertEqual(self.c.get(self.PROBE, headers=self.H(t)).status_code, 401)
        h, p, s = self.tok().split('.')
        pl = json.loads(base64.urlsafe_b64decode(p + '=='))
        pl['email'] = 'x@evil.com'
        forged = f"{h}.{_b64(json.dumps(pl).encode())}.{s}"
        self.assertEqual(self.c.get(self.PROBE, headers=self.H(forged)).status_code, 401)
        self.assertEqual(self.c.get(self.PROBE, environ_base={'REMOTE_ADDR': '192.168.0.7'}).status_code, 401)

    def test_email_allowlist(self):
        os.environ['REMOTE_ACCESS_EMAILS'] = 'someone@else.com'
        self.assertEqual(self.c.get(self.PROBE, headers=self.H(self.tok(exp=int(time.time()) + 3000))).status_code, 401)
        os.environ['REMOTE_ACCESS_EMAILS'] = 'HappyTalkMan@gmail.com'
        self.assertEqual(self.c.get(self.PROBE, headers=self.H(self.tok(exp=int(time.time()) + 2999))).status_code, 200)

    def test_code_mode_ignores_jwt(self):
        os.environ['REMOTE_ACCESS'] = 'code'
        self.assertEqual(self.c.get(self.PROBE, headers=self.H(self.tok())).status_code, 403)
