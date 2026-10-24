"""
국내 증권 커뮤니티 여론 수집 (이용 규칙 준수형)

  - 네이버 종목토론실 : 공개 목록 페이지의 '제목'만 (본문 페이지 미접속), 작성자 이름·ID 미저장,
                       최대 2페이지, 요청 간격 1.5초 이상, 종목별 30분 캐시,
                       실행 시 robots.txt 확인(urllib.robotparser) → 불허면 수집하지 않음
  - YouTube           : 공식 Data API v3 (YOUTUBE_API_KEY) 로 최근 7일 영상 → 댓글 최대 50개, 작성자 미저장

공개 API
  collect(ticker, company_name, limit=40) -> dict   # research.json 의 "community" 객체 (감성 채움)
      {"sources": [...], "posts": [{source,title,date,url,views,likes,dislikes,sentiment}], "notes": [...],
       "stats": {"n", "filtered_spam", "by_source", "engine", "sentiment"}}

환경 변수
  COMMUNITY_SOURCES = naver(기본) | naver,youtube | youtube | off
  YOUTUBE_API_KEY   = YouTube Data API v3 키 (없으면 YouTube 건너뜀)
"""

import hashlib
import html as _html
import os
import re
import threading
import time
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests

from ..utils.logger import get_logger
from . import market_data as md
from . import sentiment

logger = get_logger('mirofish.community')

NAVER_BASE = 'https://finance.naver.com'
NAVER_BOARD = NAVER_BASE + '/item/board.naver?code={code}&page={page}'
NAVER_READ = NAVER_BASE + '/item/board_read.naver?code={code}&nid={nid}'
NAVER_ROBOTS = NAVER_BASE + '/robots.txt'
YT_API = 'https://www.googleapis.com/youtube/v3'
YT_WATCH = 'https://www.youtube.com/watch?v={vid}'

ROBOTS_UA = '*'
NAVER_MIN_INTERVAL = 1.5      # 네이버 요청 간 최소 간격 (초)
NAVER_MAX_PAGES = 2
NAVER_CACHE_TTL = 1800        # 종목별 30분
ROBOTS_TTL = 12 * 3600        # robots.txt 12시간
ROBOTS_FAIL_TTL = 600         # robots.txt 확인 실패 시 10분 후 재시도
YT_CACHE_TTL = 1800
YT_MAX_VIDEOS = 3
YT_MAX_COMMENTS = 50
YT_TEXT_MAX = 200

NOTE_OFF = "커뮤니티 수집이 꺼져 있습니다 (설정 > 커뮤니티 수집)"
NOTE_ROBOTS_OK = "네이버 robots.txt 확인 결과 종목토론실 목록 수집 허용 — 제목만 수집하며 작성자 정보는 저장하지 않습니다"
NOTE_ROBOTS_DENY = "네이버 robots.txt 가 종목토론실 수집을 허용하지 않아 건너뛰었습니다"
NOTE_ROBOTS_UNKNOWN = "네이버 robots.txt 를 확인하지 못해 보수적으로 1페이지만 수집했습니다"
NOTE_NAVER_PARSE = "네이버 종목토론실 목록을 해석하지 못했습니다 (페이지 구조 변경 가능성)"
NOTE_YT_NOKEY = "YouTube API 키가 없어 건너뛰었습니다"
NOTE_YT_QUOTA = "YouTube API 일일 할당량을 초과해 건너뛰었습니다"
NOTE_YT_BADKEY = "YouTube API 키가 유효하지 않아 건너뛰었습니다"
NOTE_YT_NOTENABLED = "YouTube Data API v3 가 이 키의 프로젝트에서 활성화되지 않아 건너뛰었습니다"
NOTE_YT_NOVIDEO = "최근 7일 내 관련 YouTube 영상이 없습니다"

_SOURCE_ALIASES = {'naver': 'naver', '네이버': 'naver', 'youtube': 'youtube', 'yt': 'youtube', '유튜브': 'youtube'}
_OFF_TOKENS = {'off', 'none', 'false', '0', 'disabled', 'no', '끄기'}

# 스팸·광고 제목 (리딩방·연락처·링크 등)
_SPAM_RES = [re.compile(p, re.I) for p in (
    r'리딩\s*방', r'무료\s*추천', r'추천\s*방', r'종목\s*상담', r'수익\s*보장', r'오픈\s*(채팅|톡|카톡)', r'카톡\s*방',
    r'텔레그램', r'단톡\s*방', r'무료\s*체험', r'VIP\s*방', r'급등주\s*(공유|무료)',
    r'https?://', r'www\.', r'bit\.ly', r'[a-z0-9\-]+\.(com|co\.kr|kr|net|io|me|ly)\b',
    r'(?<!\d)01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)', r'(?<!\d)0(2|[3-6]\d|70)[-.\s]\d{3,4}[-.\s]\d{4}(?!\d)',
    r'(?<!\d)1[568]\d{2}-\d{4}(?!\d)', r'[\[(]\s*광고\s*[\])]', r'클린봇',
)]
_DATE_RE = re.compile(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?')
_NUM_RE = re.compile(r'^\d[\d,]*$')
_KST = timezone(timedelta(hours=9))

_sleep = time.sleep           # 테스트에서 교체 가능
_naver_lock = threading.Lock()
_naver_last = [0.0]
_robots_lock = threading.Lock()
_robots_cache: Dict[str, Any] = {'at': 0.0, 'ttl': 0, 'state': None, 'rp': None}


class _SoftFail(Exception):
    """일시적 실패 — 캐시하지 않고 메모만 남김."""

    def __init__(self, notes: List[str]):
        super().__init__('; '.join(notes))
        self.notes = list(notes)


# ═══════════════════════════════════════════════════════════════ 공통
def _parse_sources() -> Tuple[List[str], List[str]]:
    """(수집 소스 목록, 무시된 설정값). 빈 목록 = 꺼짐."""
    raw = os.environ.get('COMMUNITY_SOURCES')
    if raw is None or not raw.strip():
        return ['naver'], []
    toks = [t for t in re.split(r'[\s,;|+/]+', raw.strip().lower()) if t]
    if any(t in _OFF_TOKENS for t in toks):
        return [], []
    found = {_SOURCE_ALIASES[t] for t in toks if t in _SOURCE_ALIASES}
    unknown = [t for t in toks if t not in _SOURCE_ALIASES]
    out = [s for s in ('naver', 'youtube') if s in found]
    if not out:                       # 알 수 없는 값만 있으면 기본값(naver)
        return ['naver'], unknown
    return out, unknown


def _norm_ticker(ticker: Any) -> Optional[str]:
    t = str(ticker or '').strip().upper()
    t = re.sub(r'\.(KS|KQ|KRX)$', '', t)
    if re.fullmatch(r'A\d{6}', t):
        t = t[1:]
    return t if re.fullmatch(r'\d[0-9A-Z]{5}', t) else None


def _is_spam(text: str) -> bool:
    return any(rx.search(text or '') for rx in _SPAM_RES)


def _clean_text(s: Any, n: int = 0) -> str:
    s = re.sub(r'\s+', ' ', _html.unescape(str(s or ''))).strip()
    return s[:n] if n else s


def _empty_stats() -> Dict[str, Any]:
    return {'n': 0, 'filtered_spam': 0, 'by_source': {}, 'engine': None,
            'sentiment': {'pos': 0, 'neg': 0, 'neu': 0, 'n': 0, 'score': 0.0}}


# ═══════════════════════════════════════════════════════════════ 네이버 종목토론실
def _naver_get(url: str, **kw):
    """네이버 요청 직렬화 + 최소 1.5초 간격."""
    with _naver_lock:
        wait = NAVER_MIN_INTERVAL - (time.monotonic() - _naver_last[0])
        if _naver_last[0] and wait > 0:
            _sleep(wait)
        try:
            return md._get(url, **kw)
        finally:
            _naver_last[0] = time.monotonic()


def _decode(resp) -> str:
    raw = getattr(resp, 'content', b'') or b''
    if isinstance(raw, str):
        return raw
    enc = None
    try:
        ct = (getattr(resp, 'headers', None) or {}).get('Content-Type') or ''
    except Exception:
        ct = ''
    m = re.search(r'charset=([\w\-]+)', ct, re.I)
    if m:
        enc = m.group(1)
    else:
        m = re.search(rb'charset=["\']?([\w\-]+)', raw[:4096], re.I)
        if m:
            enc = m.group(1).decode('ascii', errors='ignore')
    enc = (enc or 'cp949').lower()
    if enc in ('euc-kr', 'euckr', 'euc_kr', 'ks_c_5601-1987', 'ksc5601', 'x-windows-949', 'ms949', 'windows-949'):
        enc = 'cp949'
    try:
        return raw.decode(enc, errors='replace')
    except LookupError:
        return raw.decode('cp949', errors='replace')


def _robots() -> Tuple[str, Optional[urllib.robotparser.RobotFileParser]]:
    """('ok', parser) 또는 ('unreachable', None). 12시간 캐시."""
    now = time.time()
    with _robots_lock:
        c = _robots_cache
        if c['state'] and now - c['at'] < c['ttl']:
            return c['state'], c['rp']
    state, rp = 'unreachable', None
    try:
        r = _naver_get(NAVER_ROBOTS, timeout=6, headers={'Accept': 'text/plain,*/*;q=0.8'})
        raw = getattr(r, 'content', b'') or b''
        text = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else str(raw)
        rp = urllib.robotparser.RobotFileParser(NAVER_ROBOTS)
        rp.parse(text.splitlines())
        state = 'ok'
    except requests.HTTPError as e:
        code = getattr(getattr(e, 'response', None), 'status_code', None)
        # RFC 9309 / urllib.robotparser 관례: 401·403 → 전부 불허, 그 밖의 4xx → 전부 허용, 5xx → 확인 불가
        if code in (401, 403):
            rp = urllib.robotparser.RobotFileParser(NAVER_ROBOTS)
            rp.disallow_all = True
            state = 'ok'
        elif isinstance(code, int) and 400 <= code < 500:
            rp = urllib.robotparser.RobotFileParser(NAVER_ROBOTS)
            rp.allow_all = True
            state = 'ok'
        else:
            logger.warning(f"robots.txt 확인 실패 (HTTP {code})")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"robots.txt 확인 실패: {type(e).__name__}: {str(e)[:120]}")
    with _robots_lock:
        _robots_cache.update(at=now, ttl=ROBOTS_TTL if state == 'ok' else ROBOTS_FAIL_TTL, state=state, rp=rp)
    return state, rp


def _parse_board(html: str, code: str) -> Tuple[List[Dict[str, Any]], int, int]:
    """목록 HTML → (posts, 스팸·숨김 제외 수, board_read 링크 수). 셀렉터에 의존하지 않고 관대하게 해석."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or '', 'html.parser')
    posts: List[Dict[str, Any]] = []
    seen = set()
    filtered = 0
    links = 0
    for a in soup.find_all('a', href=True):
        href = a.get('href') or ''
        if 'board_read' not in href:
            continue
        links += 1
        tr = a.find_parent('tr')
        if tr is None:
            continue
        m_nid = re.search(r'[?&]nid=(\d+)', href)
        nid = m_nid.group(1) if m_nid else None
        key = nid or href
        if key in seen:
            continue
        seen.add(key)
        t_attr = _clean_text(a.get('title'))
        t_text = _clean_text(a.get_text(' ', strip=True))
        t_short = re.sub(r'\s*(\.\.\.|…)$', '', t_text)
        # 목록의 앵커 텍스트는 말줄임(...)될 수 있으므로 title 속성(전체 제목)을 우선
        title = t_attr if t_attr and (t_attr.startswith(t_short[:8]) or len(t_attr) >= len(t_short)) else t_short
        if not title:
            continue
        if _is_spam(title):
            filtered += 1
            continue
        cells = tr.find_all('td', recursive=False) or tr.find_all('td')
        idx = next((i for i, c in enumerate(cells) if any(x is a for x in c.find_all('a'))), -1)
        date = ''
        for c in cells:
            m = _DATE_RE.search(c.get_text(' ', strip=True))
            if m:
                y, mo, d, hh, mm = m.groups()
                date = f'{int(y):04d}-{int(mo):02d}-{int(d):02d}' + (f' {int(hh):02d}:{mm}' if hh else '')
                break
        nums: List[int] = []
        for c in (cells[idx + 1:] if idx >= 0 else cells):
            txt = c.get_text('', strip=True)
            if _NUM_RE.match(txt):
                try:
                    nums.append(int(txt.replace(',', '')))
                except ValueError:
                    pass
        views = likes = dislikes = 0
        if len(nums) >= 3:
            views, likes, dislikes = nums[-3], nums[-2], nums[-1]
        elif nums:
            views = nums[0]
        url = NAVER_READ.format(code=code, nid=nid) if nid else urljoin(NAVER_BASE + '/item/', href)
        posts.append({'source': 'naver', 'title': title[:200], 'date': date, 'url': url,
                      'views': views, 'likes': likes, 'dislikes': dislikes})
    return posts, filtered, links


def _naver_fetch(code: str, pages: int) -> Dict[str, Any]:
    notes: List[str] = []
    state, rp = _robots()
    if state != 'ok':
        pages = 1
        notes.append(NOTE_ROBOTS_UNKNOWN)
    posts: List[Dict[str, Any]] = []
    seen = set()
    filtered = 0
    fetched = 0
    parsed_links = 0
    for page in range(1, max(1, min(pages, NAVER_MAX_PAGES)) + 1):
        url = NAVER_BOARD.format(code=code, page=page)
        if rp is not None and not rp.can_fetch(ROBOTS_UA, url):
            logger.info(f"robots.txt 불허 → 네이버 종목토론실 건너뜀: {url}")
            return {'posts': [], 'filtered': 0, 'notes': [NOTE_ROBOTS_DENY], 'fetched': False}
        try:
            r = _naver_get(url, timeout=8, headers={
                'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
                'Referer': f'{NAVER_BASE}/item/main.naver?code={code}'})
        except Exception as e:  # noqa: BLE001
            msg = f"네이버 종목토론실 {page}페이지 접속 실패 ({type(e).__name__})"
            logger.warning(f"{msg}: {str(e)[:160]}")
            if fetched == 0:
                raise _SoftFail(notes + [msg])
            notes.append(msg)
            break
        fetched += 1
        ps, f, links = _parse_board(_decode(r), code)
        filtered += f
        parsed_links += links
        for p in ps:
            if p['url'] not in seen:
                seen.add(p['url'])
                posts.append(p)
        if links < 15:          # 마지막 페이지(글 수 부족)면 다음 페이지 생략
            break
    if state == 'ok':
        notes.insert(0, NOTE_ROBOTS_OK)
    if fetched and parsed_links == 0:
        notes.append(NOTE_NAVER_PARSE)
    return {'posts': posts, 'filtered': filtered, 'notes': notes, 'fetched': fetched > 0}


# ═══════════════════════════════════════════════════════════════ YouTube Data API v3
class _YTError(Exception):
    def __init__(self, status: Optional[int], reason: str, transient: bool = False, message: str = ''):
        super().__init__(f'{status} {reason}')
        self.status, self.reason, self.transient, self.message = status, reason or '', transient, message


_YT_QUOTA = {'quotaexceeded', 'dailylimitexceeded', 'ratelimitexceeded', 'userratelimitexceeded'}
_YT_BADKEY = {'keyinvalid', 'keyexpired', 'apikeyinvalid'}
_YT_SKIP_VIDEO = {'commentsdisabled', 'videonotfound', 'forbidden', 'channelnotfound'}


def _yt_call(endpoint: str, params: Dict[str, Any], key: str) -> Dict[str, Any]:
    def scrub(s: str) -> str:
        return (s or '').replace(key, '***') if key else (s or '')
    try:
        r = requests.get(f'{YT_API}/{endpoint}', params=dict(params, key=key), timeout=10,
                         headers={'Accept': 'application/json'})
    except Exception as e:  # noqa: BLE001
        raise _YTError(None, type(e).__name__, transient=True, message=scrub(str(e))[:160])
    status = getattr(r, 'status_code', 0)
    if status != 200:
        reason, message = '', ''
        try:
            err = (r.json() or {}).get('error') or {}
            message = str(err.get('message') or '')
            errs = err.get('errors') or []
            if errs and isinstance(errs[0], dict):
                reason = str(errs[0].get('reason') or '')
            if not reason:
                for d in err.get('details') or []:
                    if isinstance(d, dict) and d.get('reason'):
                        reason = str(d['reason'])
                        break
        except Exception:  # noqa: BLE001
            pass
        if not reason and 'api key not valid' in message.lower():
            reason = 'keyInvalid'
        raise _YTError(status, reason or f'http{status}', transient=(status == 429 or status >= 500),
                       message=scrub(message)[:160])
    try:
        return r.json() or {}
    except Exception:  # noqa: BLE001
        raise _YTError(status, 'badJson', transient=True)


def _yt_note(e: _YTError) -> str:
    rs = e.reason.lower()
    if rs in _YT_QUOTA:
        return NOTE_YT_QUOTA
    if rs in _YT_BADKEY or (e.status == 400 and 'key' in (e.message or '').lower()):
        return NOTE_YT_BADKEY
    if rs in ('accessnotconfigured', 'service_disabled'):
        return NOTE_YT_NOTENABLED
    return f"YouTube 수집 실패 (HTTP {e.status or '-'} {e.reason})"


def _kst(iso: Any) -> str:
    try:
        dt = datetime.strptime(str(iso)[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
        return dt.astimezone(_KST).strftime('%Y-%m-%d %H:%M')
    except Exception:  # noqa: BLE001
        return str(iso or '')[:16].replace('T', ' ')


def _yt_fetch(query: str, key: str) -> Dict[str, Any]:
    notes: List[str] = []
    after = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        data = _yt_call('search', {'part': 'snippet', 'q': query, 'type': 'video', 'order': 'date',
                                   'publishedAfter': after, 'maxResults': 5, 'relevanceLanguage': 'ko',
                                   'regionCode': 'KR'}, key)
    except _YTError as e:
        logger.warning(f"YouTube 검색 실패: {e.status} {e.reason} {e.message}")
        if e.transient and e.reason.lower() not in _YT_QUOTA:
            raise _SoftFail([_yt_note(e)])
        return {'posts': [], 'filtered': 0, 'notes': [_yt_note(e)], 'fetched': False}
    videos: List[Tuple[str, str]] = []
    for it in data.get('items') or []:
        vid = ((it or {}).get('id') or {}).get('videoId')
        if vid and vid not in [v for v, _ in videos]:
            videos.append((vid, _clean_text(((it.get('snippet') or {}).get('title')), 120)))
    if not videos:
        return {'posts': [], 'filtered': 0, 'notes': [NOTE_YT_NOVIDEO], 'fetched': True}
    posts: List[Dict[str, Any]] = []
    filtered = skipped = 0
    for vid, vtitle in videos[:YT_MAX_VIDEOS]:
        if len(posts) >= YT_MAX_COMMENTS:
            break
        try:
            cd = _yt_call('commentThreads', {'part': 'snippet', 'videoId': vid, 'maxResults': 20,
                                             'order': 'relevance', 'textFormat': 'plainText'}, key)
        except _YTError as e:
            if e.reason.lower() in _YT_SKIP_VIDEO or e.status == 404:
                skipped += 1
                continue
            logger.warning(f"YouTube 댓글 조회 실패: {e.status} {e.reason} {e.message}")
            notes.append(_yt_note(e))
            if e.reason.lower() in _YT_QUOTA or e.reason.lower() in _YT_BADKEY:
                break
            continue
        for th in cd.get('items') or []:
            sn = (((th or {}).get('snippet') or {}).get('topLevelComment') or {}).get('snippet') or {}
            text = _clean_text(sn.get('textDisplay') or sn.get('textOriginal'), YT_TEXT_MAX)
            if not text:
                continue
            if _is_spam(text):
                filtered += 1
                continue
            try:
                likes = int(sn.get('likeCount') or 0)
            except (TypeError, ValueError):
                likes = 0
            posts.append({'source': 'youtube', 'title': text, 'date': _kst(sn.get('publishedAt')),
                          'url': YT_WATCH.format(vid=vid), 'views': 0, 'likes': likes, 'dislikes': 0,
                          'video_title': vtitle})
            if len(posts) >= YT_MAX_COMMENTS:
                break
    if skipped:
        notes.append(f"댓글이 막혔거나 볼 수 없는 YouTube 영상 {skipped}개는 건너뛰었습니다")
    return {'posts': posts, 'filtered': filtered, 'notes': notes, 'fetched': True}


# ═══════════════════════════════════════════════════════════════ 공개 API
def _merge(per_source: Dict[str, List[Dict[str, Any]]], limit: int) -> List[Dict[str, Any]]:
    """소스별로 번갈아 할당해 limit 개로 자른 뒤 소스 순서대로 반환."""
    order = [s for s in ('naver', 'youtube') if per_source.get(s)]
    take = {s: 0 for s in order}
    remaining = limit
    while remaining > 0:
        progressed = False
        for s in order:
            if remaining <= 0:
                break
            if take[s] < len(per_source[s]):
                take[s] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    out: List[Dict[str, Any]] = []
    for s in order:
        out.extend(per_source[s][:take[s]])
    return out


def collect(ticker: str, company_name: str, limit: int = 40) -> Dict[str, Any]:
    """커뮤니티 글 수집 + 감성 판정. 절대 예외를 내지 않음 (실패는 notes 에 기록)."""
    notes: List[str] = []
    per_source: Dict[str, List[Dict[str, Any]]] = {}
    used: List[str] = []
    filtered = 0
    posts: List[Dict[str, Any]] = []
    stats = _empty_stats()
    try:
        srcs, unknown = _parse_sources()
        if not srcs:
            return {'sources': [], 'posts': [], 'notes': [NOTE_OFF], 'stats': stats}
        if unknown:
            notes.append(f"지원하지 않는 커뮤니티 소스 설정은 무시했습니다: {', '.join(unknown)}")
        try:
            limit = max(1, min(100, int(limit)))
        except (TypeError, ValueError):
            limit = 40
        code = _norm_ticker(ticker)
        name = _clean_text(company_name, 40)

        if 'naver' in srcs:
            if not code:
                notes.append("종목코드 형식이 올바르지 않아 네이버 종목토론실을 건너뛰었습니다")
            else:
                pages = 1 if limit <= 18 else NAVER_MAX_PAGES
                try:
                    res = md.cached(f'community:naver:{code}:{pages}', NAVER_CACHE_TTL, _naver_fetch, code, pages)
                    notes.extend(res.get('notes') or [])
                    filtered += int(res.get('filtered') or 0)
                    if res.get('fetched'):
                        used.append('naver')
                        per_source['naver'] = [dict(p) for p in res.get('posts') or []]
                except _SoftFail as e:
                    notes.extend(e.notes)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"네이버 종목토론실 수집 오류: {e}")
                    notes.append(f"네이버 종목토론실 수집 실패 ({type(e).__name__})")

        if 'youtube' in srcs:
            key = (os.environ.get('YOUTUBE_API_KEY') or '').strip()
            if not key:
                notes.append(NOTE_YT_NOKEY)
            else:
                subject = name or code or ''
                query = f"{subject} 주가"
                try:
                    if not subject:
                        raise _SoftFail(["종목명이 없어 YouTube 검색을 건너뛰었습니다"])
                    kh = hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]
                    res = md.cached(f'community:yt:{kh}:{query}', YT_CACHE_TTL, _yt_fetch, query, key)
                    notes.extend(res.get('notes') or [])
                    filtered += int(res.get('filtered') or 0)
                    if res.get('fetched'):
                        used.append('youtube')
                        per_source['youtube'] = [dict(p) for p in res.get('posts') or []]
                except _SoftFail as e:
                    notes.extend(e.notes)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"YouTube 수집 오류: {type(e).__name__}")
                    notes.append(f"YouTube 수집 실패 ({type(e).__name__})")

        posts = _merge(per_source, limit)
        engine = None
        if posts:
            try:
                results, engine = sentiment.analyze([p['title'] for p in posts])
            except Exception:  # noqa: BLE001 — analyze 는 예외를 내지 않지만 이중 방어
                results, engine = [], None
            for i, p in enumerate(posts):
                r = results[i] if i < len(results) and isinstance(results[i], dict) else {}
                p['sentiment'] = {'label': r.get('label', 'neutral'), 'score': float(r.get('score') or 0.0),
                                  'engine': r.get('engine') or engine or 'lexicon'}
        by_source = {s: 0 for s in used}
        for p in posts:
            by_source[p['source']] = by_source.get(p['source'], 0) + 1
        stats = {'n': len(posts), 'filtered_spam': filtered, 'by_source': by_source, 'engine': engine,
                 'sentiment': sentiment.summarize([p.get('sentiment') or {} for p in posts])}
    except Exception as e:  # noqa: BLE001
        logger.error(f"커뮤니티 수집 중 오류: {e}")
        notes.append(f"커뮤니티 수집 중 오류가 발생했습니다 ({type(e).__name__})")
    # 중복 메모 제거 (순서 유지)
    seen = set()
    notes = [n for n in notes if not (n in seen or seen.add(n))]
    return {'sources': used, 'posts': posts, 'notes': notes, 'stats': stats}
