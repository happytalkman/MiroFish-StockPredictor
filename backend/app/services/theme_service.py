"""
오늘의 테마 — 매일 아침 시장 테마 추출

  1) 네이버 금융 테마별 시세 (finance.naver.com/sise/theme.naver) : 테마명·전일대비·최근 3일 등락률·상승/보합/하락 종목 수·주도주
  2) 상위 테마 상세 (sise_group_detail.naver?type=theme&no=N) : 구성 종목 현재가·등락률·거래량 (최대 DETAIL_TOP 개)
  3) 오늘 뉴스 헤드라인 (Google 뉴스 '증시 테마'·'특징주' + 네이버 금융 주요뉴스) → 한국어 키워드 빈도 (불용어·조사 제거, 바이그램 병합)
  4) LLM(Grok fast) 이 테마표 + 헤드라인만 근거로 Top 5~7 선정 (종목코드는 스크랩한 구성 종목으로 검증)
     LLM 이 없거나 실패하면 규칙 기반 순위(테마 등락률 + 상승 종목 비율 + 뉴스 키워드 적중)

결과는 uploads/themes/YYYY-MM-DD.json (KST 날짜) 로 저장합니다.

예의(politeness): 네이버 HTML 페이지는 요청 간 최소 REQ_GAP_SEC(1초) 간격으로 순차 호출하고, 목록 3분·상세 10분 캐시합니다.
HTML 선택자는 PC 에서 검증되지 않았으므로 '테마 링크(type=theme&no=)가 있는 행', '/item/main.naver?code= 링크가 있는 행'을
찾아 숫자를 정규식으로 읽는 관대한 방식으로 파싱합니다. (/api/market/debug?src=naver_theme 로 점검)
"""

import os
import re
import json
import time
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import Config
from ..utils.logger import get_logger
from . import market_data as md

logger = get_logger('mirofish.themes')

KST = timezone(timedelta(hours=9))   # zoneinfo 금지 (Windows 에 tzdata 없음)

NAVER_FIN = 'https://finance.naver.com'
THEME_LIST_URL = NAVER_FIN + '/sise/theme.naver?page={page}'
THEME_DETAIL_URL = NAVER_FIN + '/sise/sise_group_detail.naver?type=theme&no={no}'
HTML_HEADERS = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Referer': NAVER_FIN + '/sise/'}

LIST_PAGES = 1        # 1페이지(약 40개 테마, 전일대비 상승률 순)
DETAIL_TOP = 12       # 상세(구성 종목)를 조회할 상위 테마 수
OUTPUT_TOP = 7        # 최종 '오늘의 테마' 수 (5~7)
OUTPUT_MIN = 5
MEMBERS_MAX = 30      # 테마별 저장할 구성 종목 수
REQ_GAP_SEC = 1.0     # 네이버 HTML 요청 간 최소 간격 (≤ 1 req/s)
LIST_TTL = 180
DETAIL_TTL = 600

_req_lock = threading.Lock()
_last_req = [0.0]


# ═══════════════════════════════════════════════════════════════ 공통
def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> str:
    return now_kst().strftime('%Y-%m-%d')


def _themes_dir() -> str:
    d = os.path.join(Config.UPLOAD_FOLDER, 'themes')
    os.makedirs(d, exist_ok=True)
    return d


def _num(s: Any) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).replace(',', '').replace('\xa0', ' ').strip()
    m = re.search(r'[-+]?\d+(?:\.\d+)?', t)
    return float(m.group(0)) if m else None


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _decode(content: bytes) -> str:
    """네이버 금융은 euc-kr(cp949). <meta charset> 이 utf-8 이면 utf-8 로."""
    head = content[:4096].decode('ascii', errors='ignore').lower()
    m = re.search(r'charset=["\']?([\w-]+)', head)
    cs = (m.group(1) if m else 'euc-kr').lower()
    if cs in ('utf-8', 'utf8'):
        return content.decode('utf-8', errors='replace')
    return content.decode('cp949', errors='replace')


def _polite_get(url: str) -> str:
    """네이버 HTML 페이지 — 요청 간 REQ_GAP_SEC 이상 간격을 두고 순차 호출"""
    with _req_lock:
        wait = REQ_GAP_SEC - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = md._get(url, timeout=10, headers=HTML_HEADERS)
        finally:
            _last_req[0] = time.time()
    return _decode(r.content)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, 'html.parser')


def _signed_pct(el) -> Optional[float]:
    """'+5.23%' / '-1.2%' — 부호가 없으면 클래스(red=상승, nv/blue=하락)·blind 텍스트로 판정"""
    if el is None:
        return None
    txt = el.get_text(' ', strip=True)
    v = _num(txt)
    if v is None:
        return None
    if re.search(r'[+\-]\s*\d', txt):
        return v
    html = str(el)
    if re.search(r'nv\d|blue|down|하락', html):
        return -abs(v)
    return abs(v)


_CODE_RE = re.compile(r'code=([0-9A-Z]{6})')


# ═══════════════════════════════════════════════════════════════ 1) 테마 목록
def parse_theme_list(html: str) -> List[Dict[str, Any]]:
    soup = _soup(html)
    link_re = re.compile(r'type=theme(&|&amp;)no=\d+')
    # 테마 링크가 가장 많은 표 = 테마 시세 표 (사이드 메뉴 등의 테마 링크는 무시)
    scope, best_n = soup, 0
    for tb in soup.find_all('table'):
        n = len(tb.find_all('a', href=link_re))
        if n > best_n:
            scope, best_n = tb, n
    out, seen = [], set()
    for a in scope.find_all('a', href=link_re):
        m = re.search(r'no=(\d+)', a.get('href', ''))
        tr = a.find_parent('tr')
        if not m or tr is None:
            continue
        no = m.group(1)
        name = a.get_text(' ', strip=True)
        if not name or no in seen:
            continue
        seen.add(no)
        tds = tr.find_all('td', recursive=False) or tr.find_all('td')
        name_idx = next((i for i, td in enumerate(tds) if td.find('a', href=re.compile(r'type=theme'))), 0)
        rest = tds[name_idx + 1:]
        pcts, ints = [], []
        for td in rest:
            txt = td.get_text(' ', strip=True)
            if '%' in txt:
                pcts.append(_signed_pct(td))
            elif re.fullmatch(r'\d{1,4}', txt.replace(',', '')) and len(pcts) >= 1:
                ints.append(int(txt.replace(',', '')))
        leaders = []
        for la in tr.find_all('a', href=_CODE_RE):
            cm = _CODE_RE.search(la.get('href', ''))
            nm = la.get_text(' ', strip=True)
            if cm and nm and cm.group(1) not in {x['code'] for x in leaders}:
                leaders.append({'code': cm.group(1), 'name': nm})
        up, flat, down = (ints + [None, None, None])[:3]
        out.append({
            'no': no, 'name': name,
            'change_pct': pcts[0] if pcts else None,
            'change_3d_pct': pcts[1] if len(pcts) > 1 else None,
            'breadth': {'up': up or 0, 'flat': flat or 0, 'down': down or 0},
            'table_leaders': leaders[:4],
        })
    return out


# ── 네이버 증권 모바일 JSON (HTML 파싱보다 가볍고 시장 구분까지 제공) — 실패 시 HTML 로 대체
THEME_API_LIST = md.NAVER_M + '/stocks/theme?page={page}&pageSize={size}'
THEME_API_DETAIL = md.NAVER_M + '/stocks/theme/{no}?page=1&pageSize={size}'
_MARKET_NAME = {'KS': 'KOSPI', 'KQ': 'KOSDAQ', 'KOSPI': 'KOSPI', 'KOSDAQ': 'KOSDAQ'}


def _signed_ratio(ratio: Any, cmp: Any) -> Optional[float]:
    v = _num(ratio)
    code = str((cmp or {}).get('code') or '') if isinstance(cmp, dict) else ''
    if v is not None and v > 0 and code in ('4', '5'):
        v = -v
    return v


def _theme_list_json(size: int = 100) -> List[Dict[str, Any]]:
    d = md._get(THEME_API_LIST.format(page=1, size=size), timeout=8).json()
    rows = []
    for g in (d.get('groups') or []):
        if not isinstance(g, dict) or g.get('no') is None or not g.get('name'):
            continue
        rows.append({
            'no': str(g['no']), 'name': str(g['name']).strip(),
            'change_pct': _num(g.get('changeRate')), 'change_3d_pct': None,
            'breadth': {'up': int(_num(g.get('riseCount')) or 0), 'flat': int(_num(g.get('steadyCount')) or 0),
                        'down': int(_num(g.get('fallCount')) or 0)},
            'table_leaders': [], 'total': g.get('totalCount'),
        })
    rows.sort(key=lambda r: -(r['change_pct'] if r['change_pct'] is not None else -1e9))
    return rows


def _theme_detail_json(no: str, size: int = 60) -> Dict[str, Any]:
    d = md._get(THEME_API_DETAIL.format(no=no, size=size), timeout=8).json()
    members = []
    for it in (d.get('stocks') or []):
        if not isinstance(it, dict) or it.get('stockEndType') not in (None, 'stock'):
            continue
        code = str(it.get('itemCode') or '').strip()
        if not re.fullmatch(r'[0-9A-Z]{6}', code):
            continue
        ex = it.get('stockExchangeType') or {}
        m = {'code': code, 'name': str(it.get('stockName') or '').strip(),
             'price': _num(it.get('closePriceRaw') or it.get('closePrice')),
             'change_pct': _signed_ratio(it.get('fluctuationsRatio'), it.get('compareToPreviousPrice')),
             'volume': int(_num(it.get('accumulatedTradingVolumeRaw') or it.get('accumulatedTradingVolume')) or 0) or None}
        raw_val = _num(it.get('accumulatedTradingValueRaw'))
        m['value_mil'] = round(raw_val / 1e6) if raw_val else _num(it.get('accumulatedTradingValue'))
        mk = _MARKET_NAME.get(str(ex.get('code') or ex.get('name') or '').upper())
        if mk:
            m['market'] = mk
        members.append(m)
    return {'members': members, 'kosdaq_legend': True, 'headers': ['json'], 'source': 'json'}


def fetch_theme_list(pages: int = LIST_PAGES) -> List[Dict[str, Any]]:
    try:
        rows = md.cached('theme_list_json', LIST_TTL, _theme_list_json)
        if rows:
            return rows
    except Exception as e:
        logger.warning(f'테마 목록 JSON 실패 → HTML 로 대체: {e}')
    rows: List[Dict[str, Any]] = []
    for p in range(1, pages + 1):
        html = md.cached(f'theme_list:{p}', LIST_TTL, _polite_get, THEME_LIST_URL.format(page=p))
        got = parse_theme_list(html)
        known = {r['no'] for r in rows}
        rows.extend(r for r in got if r['no'] not in known)
        if not got:
            break
    return rows


# ═══════════════════════════════════════════════════════════════ 2) 테마 상세 (구성 종목)
def _header_map(table) -> Tuple[Dict[str, int], int]:
    """헤더 텍스트 → 열 번호 (colspan 반영), 전체 열 수. 헤더 행 = th 만 있고 td 가 없는 첫 행"""
    for tr in table.find_all('tr'):
        ths = tr.find_all('th', recursive=False) or tr.find_all('th')
        if not ths or tr.find('td'):
            continue
        cols, i = {}, 0
        for th in ths:
            t = re.sub(r'\s+', '', th.get_text('', strip=True))
            cols.setdefault(t, i)
            try:
                i += max(1, int(th.get('colspan', 1)))
            except (TypeError, ValueError):
                i += 1
        return cols, i
    return {}, 0


def _col(hmap: Dict[str, int], *names: str) -> Optional[int]:
    for n in names:
        if n in hmap:
            return hmap[n]
    return None


def parse_theme_detail(html: str) -> Dict[str, Any]:
    soup = _soup(html)
    # 종목 링크가 가장 많은 표 = 구성 종목 표
    best, best_n = None, 0
    for tb in soup.find_all('table'):
        n = len(tb.find_all('a', href=re.compile(r'/item/main\.naver\?code=')))
        if n > best_n:
            best, best_n = tb, n
    page_text = soup.get_text(' ', strip=True)
    # '* 표시는 코스닥 종목' 범례가 있을 때만 * 로 시장을 판정 (추측 금지)
    legend = bool(re.search(r'\*\s*(표시\S*)?\s*[:：]?\s*코스닥|코스닥\s*종목\s*[:：]?\s*\*', page_text))
    members: List[Dict[str, Any]] = []
    headers: Dict[str, int] = {}
    if best is not None:
        headers, ncols = _header_map(best)
        ci_price = _col(headers, '현재가')
        ci_pct = _col(headers, '등락률')
        ci_vol = _col(headers, '거래량')
        ci_val = _col(headers, '거래대금', '거래대금(백만)')
        seen = set()
        for tr in best.find_all('tr'):
            a = tr.find('a', href=re.compile(r'/item/main\.naver\?code='))
            if not a:
                continue
            cm = _CODE_RE.search(a.get('href', ''))
            if not cm or cm.group(1) in seen:
                continue
            code = cm.group(1)
            seen.add(code)
            tds = tr.find_all('td', recursive=False) or tr.find_all('td')
            name_td = a.find_parent('td')
            name = a.get_text(' ', strip=True)
            reason_el = name_td.select_one('.info_txt') if name_td is not None else None
            reason = reason_el.get_text(' ', strip=True) if reason_el is not None else ''
            if reason_el is not None:
                reason_el.extract()
            star = name_td is not None and '*' in name_td.get_text(' ', strip=True)

            aligned = bool(headers) and ncols == len(tds)   # 헤더 열 수와 셀 수가 같을 때만 헤더 위치 사용

            def cell(i):
                return tds[i] if aligned and i is not None and 0 <= i < len(tds) else None

            price = _num(cell(ci_price).get_text(' ', strip=True)) if cell(ci_price) is not None else None
            pct = _signed_pct(cell(ci_pct)) if cell(ci_pct) is not None else None
            vol = _num(cell(ci_vol).get_text(' ', strip=True)) if cell(ci_vol) is not None else None
            val = _num(cell(ci_val).get_text(' ', strip=True)) if cell(ci_val) is not None else None
            if price is None or pct is None:   # 헤더가 없거나 다를 때: 위치·패턴 기반
                name_i = next((i for i, td in enumerate(tds) if td is name_td), 0)
                nums = []
                for td in tds[name_i + 1:]:
                    t = td.get_text(' ', strip=True)
                    if '%' in t:
                        if pct is None:
                            pct = _signed_pct(td)
                        nums.append(None)
                    elif re.fullmatch(r'[\d,]+', t.replace(' ', '')):
                        nums.append(_num(t))
                    else:
                        nums.append(_num(t) if re.search(r'\d', t) else None)
                if price is None and nums:
                    price = next((x for x in nums if x is not None), None)
                # 기본 배열: 현재가, 전일비, 등락률, 매수호가, 매도호가, 거래량, 거래대금, 전일거래량
                if vol is None and len(nums) >= 6:
                    vol = nums[5]
                if val is None and len(nums) >= 7:
                    val = nums[6]
            m = {'code': code, 'name': name, 'price': price, 'change_pct': pct,
                 'volume': int(vol) if vol is not None else None,
                 'value_mil': val}
            if legend:
                m['market'] = 'KOSDAQ' if star else 'KOSPI'
            if reason:
                m['reason'] = reason[:200]
            members.append(m)
    return {'members': members, 'kosdaq_legend': legend, 'headers': list(headers.keys())}


def _fetch_detail_uncached(no: str) -> Dict[str, Any]:
    return parse_theme_detail(_polite_get(THEME_DETAIL_URL.format(no=no)))


def fetch_theme_detail(no: str) -> Dict[str, Any]:
    try:
        d = md.cached(f'theme_detail_json:{no}', DETAIL_TTL, _theme_detail_json, str(no))
        if d.get('members'):
            return d
    except Exception as e:
        logger.warning(f'테마 상세 JSON 실패({no}) → HTML 로 대체: {e}')
    return md.cached(f'theme_detail:{no}', DETAIL_TTL, _fetch_detail_uncached, str(no))


# ═══════════════════════════════════════════════════════════════ 3) 뉴스 · 키워드
NEWS_SOURCES = [
    ('google', '증시 테마', 20),
    ('google', '특징주', 20),
    ('naver_main', None, 20),
]
PRESS_NAMES = ['조선비즈', '조선일보', 'TV조선', '한국경제TV', '한국경제', '매일경제', '서울경제', '아시아경제', '헤럴드경제',
               '파이낸셜뉴스', '머니투데이', '이데일리', '연합뉴스', '연합인포맥스', '뉴스1', '뉴시스', '인포스탁데일리',
               '비즈워치', '더벨', '딜사이트', '한경', '매경', '머니S', '데일리안', '아주경제', '이투데이', '뉴스핌', '글로벌이코노믹',
               '조세일보', '한국일보', '중앙일보', '동아일보', '경향신문', '세계일보', '국민일보', 'SBS Biz', 'MBN', 'YTN', 'KBS', 'MBC']
STOP = set('''
증시 주식 주가 코스피 코스닥 지수 종목 종목들 특징주 테마 테마주 관련주 수혜주 대장주 상승 하락 급등 급락 강세 약세 상한가 하한가
신고가 신저가 오늘 내일 어제 전일 장중 마감 개장 출발 오전 오후 투자 투자자 외국인 기관 개인 순매수 순매도 매수 매도 시장
전망 기업 관련 대비 억원 조원 만원 달러 기록 발표 공개 확대 증가 감소 기대 우려 영향 효과 이유 소식 속보 단독 종합 뉴스 기자
올해 내년 이번 최대 최고 최저 연속 거래 거래량 거래대금 주간 이슈 분석 증권 증권사 리포트 목표가 실적 전년 분기 가능성 전환
돌파 반등 회복 우위 동반 일제히 반면 위해 대한 통해 따른 위한 있는 없는 한다 했다 모두 마감시황 시황 개장시황 장마감 오름세
내림세 상승세 하락세 상승률 하락률 등락률 사자 팔자 주목 급부상 부각 전날 이날 당일 거래일 무더기 줄줄이 껑충 뚝 활짝 훨훨
앞두고 앞둔 앞두 기대감 결과 관심 가운데 이어 계속 여전히 다시 최근 현재 향후 예정 추진 진행 본격 가속 가속화
the and for with 기자회견 인터뷰 칼럼 사설 포토 영상 브리핑 체크 오늘의 이번주 다음주 지난주 한주 한때 일부 전체 국내 해외 미국 중국
'''.split()) | {p for p in PRESS_NAMES}
JOSA = sorted(['으로써', '으로서', '에서는', '에서도', '이라는', '이라며', '라는', '라며', '으로', '에서', '에게', '까지', '부터', '보다',
               '처럼', '마저', '조차', '이며', '이고', '하며', '했다', '한다', '하는', '하고', '되는', '된다', '됐다', '이다', '에도', '에는',
               '은', '는', '이', '가', '을', '를', '의', '에', '와', '과', '도', '만', '로', '며', '고', '엔'], key=len, reverse=True)
TOKEN_RE = re.compile(r'\d+[가-힣A-Za-z]+[가-힣A-Za-z0-9]*|[A-Za-z][A-Za-z0-9&]*|[가-힣]+')
ALIAS_STOP = {'관련', '관련주', '대표주', '주요종목', '주요', '대표', '기타', '산업', '업체', '서비스', '시스템', '솔루션', '장비', '부품', '소재',
              '제조', '기업', '테마', '수혜', '수혜주', '치료', '치료제', '국내', '해외', '글로벌', '신규', '상장', '등', '및', '기술', '개발', '사업'}
ALIAS_SUFFIX = ('산업', '업체', '관련주', '대표주', '주요종목', '에너지', '테마')
SYNONYMS = {
    '원자력발전': ['원전', '원자력', 'SMR'], '원자력': ['원전', 'SMR'], '2차전지': ['이차전지', '배터리'], '방위산업': ['방산'],
    '조선': ['조선주', '조선업'], '우주항공': ['우주', '위성', '누리호'], '자율주행차': ['자율주행'], '수소차': ['수소'],
    'HBM': ['고대역폭메모리'], '엔터테인먼트': ['엔터'], '비만치료제': ['비만약', '비만 치료'], '가상화폐': ['비트코인', '코인'],
    '비트코인': ['가상화폐', '가상자산'], '스테이블코인': ['스테이블 코인'], '로봇': ['휴머노이드'], '제약': ['제약바이오'],
    '바이오시밀러': ['시밀러'], '인공지능': ['AI'], '전력설비': ['전력기기', '변압기'], '전선': ['케이블'],
}
MEMBER_NAME_BLACKLIST = {'대상', '한화', '두산', '효성', '코스모', '오리온', '태양', '동방', '대원', '신세계'}


def _strip_josa(w: str) -> str:
    for j in JOSA:
        if w.endswith(j) and len(w) - len(j) >= 2:
            return w[:-len(j)]
    return w


def _clean_title(title: str) -> str:
    t = re.sub(r'<[^>]+>', ' ', title or '')
    for p in PRESS_NAMES:
        t = t.replace(p, ' ')
    return t


def tokenize(title: str) -> List[Optional[str]]:
    """토큰 목록 (불용어 자리는 None — 바이그램이 불용어를 건너 붙지 않게)"""
    toks: List[Optional[str]] = []
    for m in TOKEN_RE.finditer(_clean_title(title)):
        w = m.group(0)
        if re.fullmatch(r'[가-힣]+', w):
            w = _strip_josa(w)
        if len(w) < 2 or w in STOP or w.lower() in STOP:
            toks.append(None)
        else:
            toks.append(w)
    return toks


def extract_keywords(titles: List[str], top: int = 30) -> List[Dict[str, Any]]:
    """헤드라인 단위 문서빈도. 자주 붙어 나오는 두 단어는 바이그램으로 병합."""
    uni, bi = Counter(), Counter()
    for t in titles:
        toks = tokenize(t)
        uni.update(set(w for w in toks if w))
        pairs = set()
        for a, b in zip(toks, toks[1:]):
            if a and b and a != b:
                pairs.add(f'{a} {b}')
        bi.update(pairs)
    merged = Counter(uni)
    for pair, n in bi.most_common():
        if n < 2:
            break
        a, b = pair.split(' ', 1)
        if n >= 0.6 * min(uni[a], uni[b]):
            merged[pair] = n
            merged[a] = max(0, merged[a] - n)
            merged[b] = max(0, merged[b] - n)
    items = [(w, n) for w, n in merged.items() if n >= 2]
    if len(items) < 5:
        items = [(w, n) for w, n in merged.items() if n >= 1]
    items.sort(key=lambda x: (-x[1], -len(x[0]), x[0]))
    return [{'word': w, 'count': n} for w, n in items[:top]]


def theme_aliases(name: str) -> List[str]:
    """'2차전지(LFP/리튬인산철)' → ['2차전지', 'LFP', '리튬인산철', '이차전지', '배터리']"""
    base = re.sub(r'\s+', ' ', name or '').strip()
    parts = [p.strip() for p in re.split(r'[()\[\]/·,&]|\s및\s|\s등(?=\s|$)', base) if p and p.strip()]
    out: List[str] = []

    def add(x):
        x = re.sub(r'\s*등$', '', x.strip())
        if len(x) >= 2 and x not in ALIAS_STOP and x not in out:
            out.append(x)

    for p in parts:
        add(p)
        for suf in ALIAS_SUFFIX:
            if p.endswith(suf) and len(p) - len(suf) >= 2:
                add(p[:-len(suf)].strip())
        if ' ' in p:
            for w in p.split(' '):
                add(w)
    for a in list(out):
        for k, syns in SYNONYMS.items():
            if a == k or (len(k) >= 2 and k in a):
                for s in syns:
                    add(s)
    return out


def _title_hits(title: str, aliases: List[str], member_names: List[str]) -> bool:
    t = _clean_title(title)
    for a in aliases:
        if re.fullmatch(r'[A-Za-z0-9 ]+', a):
            if re.search(r'(?<![A-Za-z])' + re.escape(a) + r'(?![A-Za-z])', t):
                return True
        elif a in t:
            return True
    return any(n in t for n in member_names)


def _usable_member_names(members: List[Dict[str, Any]]) -> List[str]:
    out = []
    for m in members:
        n = (m.get('name') or '').strip()
        if n and n not in MEMBER_NAME_BLACKLIST and (len(n) >= 3 or re.search(r'[A-Za-z]{2}', n)):
            out.append(n)
    return out


def collect_headlines(notes: List[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for kind, q, n in NEWS_SOURCES:
        try:
            got = md.google_news(q, n) if kind == 'google' else md.naver_main_news(n)
            items.extend(dict(x, _src=(f"Google 뉴스 '{q}'" if kind == 'google' else '네이버 금융 주요뉴스')) for x in got or [])
        except Exception as e:
            notes.append(f"뉴스 수집 실패({q or '네이버 주요뉴스'}): {str(e)[:80]}")
    out, seen = [], set()
    for it in items:
        title = (it.get('title') or '').strip()
        key = re.sub(r'[\W_]+', '', title)[:40]
        if not title or key in seen:
            continue
        seen.add(key)
        out.append({'id': f'N{len(out) + 1}', 'title': title[:200], 'url': it.get('url') or '',
                    'press': it.get('press') or '', 'time': it.get('time') or ''})
        if len(out) >= 60:
            break
    return out


# ═══════════════════════════════════════════════════════════════ 4) 점수 · 문장
def rule_score(t: Dict[str, Any]) -> int:
    chg = t.get('change_pct') or 0.0
    c3 = t.get('change_3d_pct') or 0.0
    b = t.get('breadth') or {}
    tot = (b.get('up') or 0) + (b.get('flat') or 0) + (b.get('down') or 0)
    br = (b.get('up') or 0) / tot if tot else 0.0
    hits = len(t.get('_news_ids') or [])
    s = 45 * _clamp((chg + 1) / 7) + 15 * _clamp((c3 + 2) / 14) + 25 * br + 15 * _clamp(hits / 4)
    return int(round(_clamp(s / 100) * 100))


def _fmt_pct(x: Optional[float]) -> str:
    return '정보 없음' if x is None else f'{x:+.2f}%'


def rule_why(t: Dict[str, Any], news_ids: List[str]) -> str:
    b = t.get('breadth') or {}
    up, fl, dn = b.get('up') or 0, b.get('flat') or 0, b.get('down') or 0
    tot = up + fl + dn
    c3 = t.get('change_3d_pct')
    chg = t.get('change_pct')
    if chg is None:
        s1 = f"{t['name']} 테마는 등락률 정보를 확인하지 못했고"
    else:
        verb = '올랐고' if chg > 0 else '내렸고' if chg < 0 else '보합이었고'
        s1 = f"{t['name']} 테마는 전일 대비 {_fmt_pct(chg)} {verb}"
    s1 += f"(최근 3일 {_fmt_pct(c3)})" if c3 is not None else ''
    if tot:
        s1 += f", 구성 종목 {tot}개 중 {up}개가 상승했습니다." if up / tot < 0.7 else \
              f", 구성 종목 {tot}개 중 {up}개가 상승해 매수세가 테마 전반으로 퍼졌습니다."
    else:
        s1 = re.sub(r'(올랐|내렸|보합이었|못했)고(\(.*\))?$', lambda m: m.group(1) + '습니다' + (m.group(2) or ''), s1) + '.'
    leaders = [x['name'] for x in (t.get('leaders') or [])[:2]]
    lead_txt = f"{'·'.join(leaders)} 등이 흐름을 이끌었고" if leaders else '구성 종목 전반이 움직였고'
    if news_ids:
        refs = ''.join(f'[{i}]' for i in news_ids[:2])
        s2 = f"{lead_txt}, 관련 뉴스 헤드라인 {len(news_ids)}건이 확인됩니다 {refs}."
    else:
        s2 = f"{lead_txt}, 뚜렷한 뉴스 재료는 헤드라인에서 확인되지 않았습니다."
    return s1 + ' ' + s2


def rule_risk(t: Dict[str, Any], has_news: bool) -> str:
    chg = t.get('change_pct') or 0.0
    c3 = t.get('change_3d_pct') or 0.0
    b = t.get('breadth') or {}
    tot = (b.get('up') or 0) + (b.get('flat') or 0) + (b.get('down') or 0)
    br = (b.get('up') or 0) / tot if tot else 0.0
    if chg >= 7 or c3 >= 15:
        return '단기 급등 폭이 커 차익실현 매물과 변동성 확대에 유의해야 합니다.'
    if chg < 0:
        return '테마 전체가 약세라 반등을 확인하기 전 추격 매수는 신중해야 합니다.'
    if tot and br < 0.5:
        return '상승 종목이 일부에 쏠려 있어 테마 확산성은 제한적일 수 있습니다.'
    if not has_news:
        return '뉴스 재료가 확인되지 않아 수급 중심의 단기 순환매일 가능성이 있습니다.'
    return '테마 순환이 빠른 장세라 추격 매수보다 눌림 확인이 필요합니다.'


def _leaders_of(t: Dict[str, Any], members: List[Dict[str, Any]], k: int = 3) -> List[Dict[str, str]]:
    codes = {m['code'] for m in members}
    out: List[Dict[str, str]] = []
    for x in t.get('table_leaders') or []:
        if (not members or x['code'] in codes) and x['code'] not in {o['code'] for o in out}:
            out.append({'code': x['code'], 'name': x['name']})
    for m in sorted(members, key=lambda m: -(m.get('change_pct') if m.get('change_pct') is not None else -999)):
        if len(out) >= k:
            break
        if m['code'] not in {o['code'] for o in out}:
            out.append({'code': m['code'], 'name': m['name']})
    return out[:k]


# ═══════════════════════════════════════════════════════════════ 5) LLM 선정
LLM_SYSTEM = (
    "너는 한국 증권사 리서치센터의 테마 애널리스트다. 제공된 네이버 테마 시세표와 오늘 뉴스 헤드라인만 근거로 "
    "'오늘의 테마' 5~7개를 고른다.\n규칙:\n"
    "1) JSON 객체 하나만 출력한다.\n"
    "2) 제공되지 않은 사실·수치·종목을 만들지 않는다.\n"
    "3) why 는 정확히 2문장. 각 문장 끝에 근거 헤드라인 번호를 [N3] 형식으로 인용한다(제공된 번호만). "
    "근거 헤드라인이 없으면 시세 수치를 근거로 쓰고 인용하지 않는다.\n"
    "4) leaders·related 의 code 는 해당 테마 members 에 있는 6자리 코드만 쓴다.\n"
    "5) momentum_score 는 0~100 정수(당일 등락률·3일 등락률·상승 종목 비율·뉴스 강도 종합).\n"
    "6) risk_note 는 1문장.\n7) 매수 권유·확정적 표현을 쓰지 않는다. 자연스러운 한국어로 쓴다."
)
LLM_SCHEMA = {"themes": [{"name": "테마명(표의 이름 그대로)", "naver_theme_no": "표의 no", "why": "2문장 [N1]",
                          "momentum_score": 0, "leaders": [{"code": "000000", "name": "종목명"}],
                          "related": [{"code": "000000", "name": "종목명"}], "risk_note": "1문장"}]}


def _llm_engine():
    if (os.environ.get('THEME_LLM') or 'auto').strip().lower() in ('off', '0', 'false', 'no', 'rule'):
        return None
    try:
        from .stock_knowledge_graph import StockKnowledgeGraph
        kg = StockKnowledgeGraph()
        if not getattr(kg.judge, 'providers', None):
            return None
        return kg
    except Exception as e:
        logger.warning(f'테마 LLM 준비 실패, 규칙 기반으로 진행: {e}')
        return None


def _norm_name(s: str) -> str:
    return re.sub(r'[\s()\[\]/·,.\-]+', '', s or '').lower()


_REF_RE = re.compile(r'\[\s*(N\d+(?:\s*[,，]\s*N\d+)*)\s*\]')


def _clean_refs(text: str, valid: set) -> Tuple[str, List[str]]:
    used: List[str] = []

    def rep(m):
        ids = [x.strip() for x in re.split(r'[,，]', m.group(1)) if x.strip() in valid]
        for i in ids:
            if i not in used:
                used.append(i)
        return ''.join(f'[{i}]' for i in ids)
    out = _REF_RE.sub(rep, text or '')
    return re.sub(r'\s+([.,])', r'\1', re.sub(r'[ \t]{2,}', ' ', out)).strip(), used


def llm_select(kg, cands: List[Dict[str, Any]], headlines: List[Dict[str, Any]], date: str,
               market_note: str, progress: Callable = None) -> Tuple[List[Dict[str, Any]], str]:
    """LLM 결과를 검증해 테마 목록 반환. 유효 테마가 3개 미만이면 ValueError."""
    payload = {
        'date': date, 'market_note': market_note,
        'themes': [{'no': t['no'], 'name': t['name'], 'change_pct': t.get('change_pct'),
                    'change_3d_pct': t.get('change_3d_pct'), 'breadth': t.get('breadth'),
                    'news_refs': (t.get('_news_ids') or [])[:6],
                    'members': [{'code': m['code'], 'name': m['name'], 'change_pct': m.get('change_pct')}
                                for m in sorted(t.get('_members') or [], key=lambda m: -(m.get('change_pct') or -999))[:15]]}
                   for t in cands],
        'headlines': [{'id': h['id'], 'title': h['title'], 'press': h.get('press', '')} for h in headlines],
        'output_schema': LLM_SCHEMA,
    }
    user = ('아래 데이터로 오늘의 테마 5~7개를 골라 output_schema 형식의 JSON 으로 답하라.\n'
            + json.dumps(payload, ensure_ascii=False))

    def tick(n_c, n_r, sec):
        if progress:
            progress(80, f'AI 테마 선정 — 응답 {n_c:,}자 수신 ({sec:.0f}초)', {'stage': 'rank'})
    raw, model = kg._llm_json(LLM_SYSTEM, user, max_tokens=4000, fast=True, on_tick=tick)
    items = (raw or {}).get('themes') if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError('LLM 응답에 themes 배열이 없습니다')

    by_no = {str(t['no']): t for t in cands}
    by_name = {_norm_name(t['name']): t for t in cands}
    all_members: Dict[str, Dict[str, Any]] = {}
    for t in cands:
        for m in t.get('_members') or []:
            all_members.setdefault(m['code'], m)
    valid_ids = {h['id'] for h in headlines}
    out, used_keys = [], set()
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get('name') or '').strip()
        cand = by_no.get(str(it.get('naver_theme_no') or it.get('no') or '').strip())
        if cand is None and name:
            nn = _norm_name(name)
            cand = by_name.get(nn) or next((t for k, t in by_name.items() if nn and (nn in k or k in nn)), None)
        pool = {m['code']: m for m in (cand.get('_members') or [])} if cand else all_members
        if cand is not None and not pool:          # 상세 조회 실패 테마 → 표의 주도주만 허용
            pool = {x['code']: x for x in cand.get('table_leaders') or []}

        def codes(key):
            res = []
            for x in it.get(key) or []:
                c = str((x or {}).get('code') if isinstance(x, dict) else x or '').strip().upper()
                c = re.sub(r'^A(?=[0-9A-Z]{6}$)', '', c).zfill(6)[:6]
                if c in pool and c not in {r['code'] for r in res}:
                    res.append({'code': c, 'name': pool[c]['name']})
            return res
        leaders, related = codes('leaders'), codes('related')
        related = [r for r in related if r['code'] not in {l['code'] for l in leaders}]
        if not leaders and not related and cand is None:
            continue                                  # 근거 없는 테마 제거
        key = cand['no'] if cand else _norm_name(name)
        if key in used_keys:
            continue
        used_keys.add(key)
        why, refs = _clean_refs(str(it.get('why') or ''), valid_ids)
        base = cand or {'no': None, 'name': name, 'change_pct': None, 'change_3d_pct': None,
                        'breadth': {'up': 0, 'flat': 0, 'down': 0}, '_members': [pool[x['code']] for x in leaders + related],
                        '_news_ids': []}
        try:
            ms = int(round(float(it.get('momentum_score'))))
            ms = max(0, min(100, ms))
        except (TypeError, ValueError):
            ms = rule_score(base)
        if not leaders:
            leaders = _leaders_of(base, base.get('_members') or [])
        out.append({'_base': base, 'name': base['name'] or name, 'why': why or rule_why(dict(base, leaders=leaders), base.get('_news_ids') or []),
                    'refs': refs, 'momentum_score': ms, 'leaders': leaders, 'related': related[:8],
                    'risk_note': str(it.get('risk_note') or '').strip()[:240] or rule_risk(base, bool(base.get('_news_ids')))})
        if len(out) >= OUTPUT_TOP:
            break
    if len(out) < 3:
        raise ValueError(f'LLM 이 유효한 테마를 {len(out)}개만 반환했습니다')
    return out, model


# ═══════════════════════════════════════════════════════════════ 조립
def _theme_out(rank: int, base: Dict[str, Any], why: str, refs: List[str], momentum: int, leaders, related,
               risk: str, headlines_by_id: Dict[str, Dict[str, Any]], keywords: List[Dict[str, Any]]) -> Dict[str, Any]:
    members = sorted(base.get('_members') or [], key=lambda m: -(m.get('change_pct') if m.get('change_pct') is not None else -999))
    news_ids = list(refs) + [i for i in (base.get('_news_ids') or []) if i not in refs]
    news = [{k: headlines_by_id[i][k] for k in ('id', 'title', 'url', 'press')} for i in news_ids[:6] if i in headlines_by_id]
    aliases = theme_aliases(base.get('name') or '')
    titles = ' '.join(_clean_title(n['title']) for n in news)
    kws = []
    for k in keywords:
        w = k['word']
        if any(p in titles for p in w.split(' ')) or any(w in a or a in w for a in aliases):
            kws.append(w)
        if len(kws) >= 6:
            break
    return {
        'rank': rank, 'name': base.get('name'), 'no': base.get('no'),
        'change_pct': base.get('change_pct'), 'change_3d_pct': base.get('change_3d_pct'),
        'breadth': base.get('breadth') or {'up': 0, 'flat': 0, 'down': 0},
        'momentum_score': momentum, 'why': why, 'news': news,
        'leaders': leaders, 'related': related,
        'members': [{k: m.get(k) for k in ('code', 'name', 'market', 'price', 'change_pct', 'volume', 'value_mil', 'reason')
                     if m.get(k) is not None} for m in members[:MEMBERS_MAX]],
        'keywords': kws, 'risk_note': risk,
    }


def _market_note(now: datetime) -> str:
    hm = now.hour * 60 + now.minute
    if now.weekday() >= 5:
        return '주말 — 테마 등락률은 직전 거래일 종가 기준입니다.'
    if hm < 9 * 60:
        return '장 시작 전 — 테마 등락률은 전 거래일 종가 기준, 뉴스는 오늘 아침까지입니다.'
    if hm < 15 * 60 + 30:
        return '장중 — 테마 등락률은 추출 시점의 실시간 시세 기준입니다.'
    return '장 마감 후 — 테마 등락률은 오늘 종가 기준입니다.'


def build_today(progress: Callable = None, now: datetime = None, use_llm: Optional[bool] = None,
                save: bool = True) -> Dict[str, Any]:
    """오늘의 테마를 만들고 uploads/themes/YYYY-MM-DD.json 에 저장. progress(pct, msg, detail)"""
    t0 = time.time()
    now = now or now_kst()
    date = now.strftime('%Y-%m-%d')
    notes: List[str] = []

    def P(pct, msg, stage):
        if progress:
            try:
                progress(pct, msg, {'stage': stage})
            except Exception:
                pass

    # 1) 테마 목록
    P(5, '네이버 테마별 시세 수집 중', 'list')
    try:
        themes = fetch_theme_list()
    except Exception as e:
        raise RuntimeError(f'네이버 테마 시세를 가져오지 못했습니다: {str(e)[:160]}')
    if not themes:
        raise RuntimeError('네이버 테마 시세 표를 해석하지 못했습니다 (페이지 구조 변경 가능성 — /api/market/debug?src=naver_theme 확인)')
    notes.append(f'네이버 테마별 시세 {LIST_PAGES}페이지 · 테마 {len(themes)}개')

    # 2) 뉴스 · 키워드
    P(14, f'테마 {len(themes)}개 수집 — 오늘 뉴스 헤드라인 수집 중', 'news')
    headlines = collect_headlines(notes)
    hid = {h['id']: h for h in headlines}
    keywords = extract_keywords([h['title'] for h in headlines])
    notes.append(f"뉴스 헤드라인 {len(headlines)}건 (Google 뉴스 '증시 테마'·'특징주', 네이버 금융 주요뉴스)")

    for t in themes:
        al = theme_aliases(t['name'])
        t['_news_ids'] = [h['id'] for h in headlines if _title_hits(h['title'], al, [])]
    cands = sorted(themes, key=lambda t: -rule_score(t))[:DETAIL_TOP]

    # 3) 상세 (구성 종목) — 1 req/s
    ok_detail = 0
    for i, t in enumerate(cands):
        P(22 + int(40 * i / max(1, len(cands))), f"테마 구성 종목 조회 — {t['name']} ({i + 1}/{len(cands)})", 'detail')
        try:
            d = fetch_theme_detail(t['no'])
            t['_members'] = d.get('members') or []
            ok_detail += 1 if t['_members'] else 0
        except Exception as e:
            t['_members'] = []
            notes.append(f"테마 상세 실패({t['name']}): {str(e)[:60]}")
    notes.append(f'테마 상세 {ok_detail}/{len(cands)}개 조회 (구성 종목)')

    # 종목명까지 포함해 뉴스 재매칭 + 키워드 가중
    for t in cands:
        al = theme_aliases(t['name'])
        names = _usable_member_names(t.get('_members') or [])
        t['_news_ids'] = [h['id'] for h in headlines if _title_hits(h['title'], al, names)]
        t['leaders'] = _leaders_of(t, t.get('_members') or [])
    for k in keywords:
        w = k['word']
        for t in cands:
            al = theme_aliases(t['name'])
            names = _usable_member_names(t.get('_members') or [])
            if any(w == a or (len(a) >= 2 and a in w) or (len(w) >= 2 and w in a) for a in al) or w in names:
                k['theme'] = t['name']
                break
    keywords.sort(key=lambda k: -(k['count'] * (1.6 if k.get('theme') else 1.0)))

    # 4) 선정
    engine, model, selected = 'rule', None, None
    kg = None if use_llm is False else _llm_engine()
    if kg is not None:
        P(70, 'AI 테마 선정 중 (Grok)', 'rank')
        try:
            picks, model = llm_select(kg, cands, headlines, date, _market_note(now), progress)
            selected = [_theme_out(r + 1, p['_base'], p['why'], p['refs'], p['momentum_score'], p['leaders'],
                                   p['related'], p['risk_note'], hid, keywords) for r, p in enumerate(picks)]
            engine = 'llm'
            notes.append(f'AI 선정: {model}')
        except Exception as e:
            logger.warning(f'테마 LLM 선정 실패 → 규칙 기반: {e}')
            notes.append(f'AI 선정 실패 → 규칙 기반 순위 ({str(e)[:80]})')
    elif use_llm is not False:
        notes.append('AI 선정 미사용(키 없음 또는 THEME_LLM=off) → 규칙 기반 순위')
    if selected is None:
        P(80, '규칙 기반 테마 순위 계산 중', 'rank')
        ranked = sorted(cands, key=lambda t: (-rule_score(t), -(t.get('change_pct') or -99)))
        n = max(OUTPUT_MIN, min(OUTPUT_TOP, len(ranked)))
        selected = []
        for r, t in enumerate(ranked[:n]):
            leaders = t.get('leaders') or _leaders_of(t, t.get('_members') or [])
            lc = {l['code'] for l in leaders}
            members = sorted(t.get('_members') or [], key=lambda m: -(m.get('change_pct') or -999))
            related = [{'code': m['code'], 'name': m['name']} for m in members if m['code'] not in lc][:5]
            ids = t.get('_news_ids') or []
            selected.append(_theme_out(r + 1, t, rule_why(dict(t, leaders=leaders), ids), ids[:2], rule_score(t),
                                       leaders, related, rule_risk(t, bool(ids)), hid, keywords))

    notes.append(_market_note(now))
    data = {
        'date': date,
        'generated_at': now_kst().isoformat(timespec='seconds'),
        'source_notes': notes,
        'themes': selected,
        'keywords': [{k: v for k, v in kw.items() if k in ('word', 'count', 'theme')} for kw in keywords[:30]],
        'engine': engine,
        'model': model,
        'scanned': {'themes': len(themes), 'detailed': len(cands), 'headlines': len(headlines)},
        'elapsed_sec': round(time.time() - t0, 1),
    }
    if save:
        P(96, '결과 저장 중', 'save')
        _write_json(os.path.join(_themes_dir(), f'{date}.json'), data)
        try:                            # 테마 ↔ 종목 관계를 누적 온톨로지에 반영
            from . import ontology_store
            ontology_store.ingest_themes(max_files=1)
        except Exception as e:
            logger.warning(f"온톨로지 테마 반영 실패: {e}")
    P(100, f"오늘의 테마 {len(selected)}개 추출 완료 ({'AI 선정' if engine == 'llm' else '규칙 기반'})", 'done')
    return data


# ═══════════════════════════════════════════════════════════════ 저장 · 조회
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _write_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def list_dates() -> List[str]:
    try:
        names = os.listdir(_themes_dir())
    except OSError:
        return []
    return sorted((n[:-5] for n in names if n.endswith('.json') and _DATE_RE.match(n[:-5])), reverse=True)


def load(date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if date is None:
        dates = list_dates()
        if not dates:
            return None
        date = dates[0]
    if not _DATE_RE.match(str(date)):
        return None
    path = os.path.join(_themes_dir(), f'{date}.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        logger.error(f'테마 파일 읽기 실패 {path}: {e}')
        return None


def generated_at(date: str) -> Optional[datetime]:
    """해당 날짜 파일의 생성 시각 (KST aware). 파일이 없으면 None"""
    d = load(date)
    if not d:
        return None
    try:
        g = datetime.fromisoformat(str(d.get('generated_at')))
        return g if g.tzinfo else g.replace(tzinfo=KST)
    except (TypeError, ValueError):
        path = os.path.join(_themes_dir(), f'{date}.json')
        return datetime.fromtimestamp(os.path.getmtime(path), KST)


# ═══════════════════════════════════════════════════════════════ 점검 (/api/market/debug)
def debug_probe_list() -> Dict[str, Any]:
    html = _polite_get(THEME_LIST_URL.format(page=1))
    rows = parse_theme_list(html)
    return {'url': THEME_LIST_URL.format(page=1), 'raw_len': len(html), 'parsed': len(rows),
            'first_rows': rows[:3], 'has_table_type_1': 'type_1' in html}


def debug_probe_detail(no: Optional[str] = None) -> Dict[str, Any]:
    if not no:
        try:
            rows = fetch_theme_list(1)
            no = rows[0]['no'] if rows else None
        except Exception:
            no = None
    no = str(no or '536')
    html = _polite_get(THEME_DETAIL_URL.format(no=no))
    d = parse_theme_detail(html)
    return {'url': THEME_DETAIL_URL.format(no=no), 'raw_len': len(html), 'members': len(d['members']),
            'first': d['members'][:3], 'headers': d['headers'], 'kosdaq_legend': d['kosdaq_legend']}
