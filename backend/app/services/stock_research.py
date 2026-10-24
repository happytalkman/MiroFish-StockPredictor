"""
2단계 리서치 에이전트 — 리플렉션 루프 + 출처 인용

흐름 (자체 구현)
  1) 계획(plan)      : Grok(빠른 모델)이 조사 질문 4~6개와 소스별 검색어를 만든다 (실패 시 템플릿 검색어)
  2) 검색(search)    : Google 뉴스(국내·해외) · 네이버 종목 뉴스 · 공시(OpenDART 또는 네이버) · (선택) Tavily 를 병렬 수집
  3) 요약(summarize) : 제목·요약문에 실제로 있는 내용만 '원자적 사실' 한 문장으로 추출 — 사실 하나 = 출처 URL 하나.
                        수치가 원문에 없거나 원문과 동떨어진 문장은 버리고 기사 제목으로 대체한다 (환각 방지)
  4) 리플렉션(reflect): 부족한 관점(실적·공시·해외 시각 등)을 찾아 새 검색어로 재검색 → 다시 요약 (rounds 회)
  5) 종합(synthesize): [R#] 인용이 붙은 요약·핵심 포인트·리스크·남은 공백
  + 감성(sentiment.analyze) 과 커뮤니티 심리(community.collect, 1라운드와 병렬) 를 붙여 research.json 계약대로 반환

외부 소스 실패는 절대 전체를 멈추지 않는다. 모든 소스가 실패해도 빈 findings 와 한국어 gaps_remaining 을 반환한다.
검증이 필요한 외부 URL·JSON 구조는 각 함수 docstring 에 'UNVERIFIED' 로 표시했다.
"""

import html
import io
import json
import os
import re
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import requests

from ..config import Config
from ..utils.logger import get_logger
from . import market_data as md

logger = get_logger('mirofish.stock_research')

CACHE_TTL = 20 * 60              # 소스별 캐시 20분
MAX_FINDINGS = 40
SNIPPET_MAX = 300
NEWS_DAYS = 7
DISCLOSURE_DAYS = 90
KINDS = ('news', 'disclosure', 'global')
TOPICS = ('실적', '업황', '수급', '정책', '경쟁', '리스크', '기타')
KIND_KO = {'news': '국내 뉴스', 'disclosure': '공시', 'global': '해외'}

DART_CORP_CACHE_DAYS = 30
_dart_lock = threading.Lock()

# 자주 쓰는 종목의 영문명 (LLM 계획이 실패했을 때 해외 검색어용)
EN_NAMES = {
    '005930': 'Samsung Electronics', '000660': 'SK hynix', '035420': 'Naver', '035720': 'Kakao',
    '005380': 'Hyundai Motor', '000270': 'Kia', '051910': 'LG Chem', '373220': 'LG Energy Solution',
    '207940': 'Samsung Biologics', '006400': 'Samsung SDI', '068270': 'Celltrion', '005490': 'POSCO Holdings',
    '105560': 'KB Financial', '055550': 'Shinhan Financial', '086790': 'Hana Financial', '012330': 'Hyundai Mobis',
    '066570': 'LG Electronics', '003550': 'LG Corp', '034730': 'SK Inc', '017670': 'SK Telecom', '030200': 'KT Corp',
    '028260': 'Samsung C&T', '096770': 'SK Innovation', '015760': 'KEPCO', '032830': 'Samsung Life Insurance',
    '018260': 'Samsung SDS', '009150': 'Samsung Electro-Mechanics', '010130': 'Korea Zinc', '011200': 'HMM',
    '259960': 'Krafton', '036570': 'NCSoft', '251270': 'Netmarble', '352820': 'HYBE', '042700': 'Hanmi Semiconductor',
    '012450': 'Hanwha Aerospace', '329180': 'HD Hyundai Heavy Industries', '247540': 'EcoPro BM', '086520': 'EcoPro',
    '003670': 'POSCO Future M', '010950': 'S-Oil', '316140': 'Woori Financial', '009540': 'HD Korea Shipbuilding',
    '042660': 'Hanwha Ocean', '064350': 'Hyundai Rotem', '079550': 'LIG Nex1', '000810': 'Samsung Fire & Marine',
}

TOPIC_KEYWORDS = [
    ('실적', ['실적', '영업이익', '영업익', '매출', '순이익', '어닝', '컨센서스', '분기', '잠정', 'earnings', 'revenue',
             'profit', 'eps', 'guidance', 'quarter']),
    ('리스크', ['소송', '리콜', '사고', '적자', '하향', '우려', '급락', '조사', '파업', '횡령', '제재', '불확실', 'lawsuit',
              'recall', 'probe', 'strike', 'downgrade', 'fine', 'plunge', 'risk']),
    ('수급', ['외국인', '기관', '순매수', '순매도', '공매도', '수급', '자사주', '매수세', '매도세', 'buyback', 'inflow', 'outflow']),
    ('정책', ['정부', '규제', '정책', '관세', '금리', '법안', '보조금', 'tariff', 'regulation', 'policy', 'subsidy', 'rate']),
    ('경쟁', ['경쟁', '점유율', '경쟁사', '추격', 'rival', 'competitor', 'market share']),
    ('업황', ['업황', '수요', '가격', '공급', '사이클', '업계', '시장', '수주', 'demand', 'supply', 'price', 'cycle', 'order']),
]

_POS_WORDS = ['상승', '호조', '최대', '개선', '흑자', '상회', '수주', '급등', '강세', '증가', '신고가', '성장', '상향', '호실적',
              'beat', 'surge', 'record', 'upgrade', 'rally', 'gain', 'growth', 'jump', 'strong']
_NEG_WORDS = ['하락', '부진', '적자', '하회', '급락', '약세', '감소', '우려', '소송', '리콜', '하향', '손실', '쇼크', '둔화',
              'miss', 'plunge', 'downgrade', 'cut', 'loss', 'drop', 'fall', 'weak', 'lawsuit']


# ═══════════════════════════════════════════════════════════════ 공통 도우미
def _clean(s: Any, limit: Optional[int] = None) -> str:
    t = html.unescape(re.sub(r'<[^>]+>', ' ', str(s or '')))
    t = re.sub(r'\s+', ' ', t.replace('\xa0', ' ')).strip()
    if limit and len(t) > limit:
        t = t[: limit - 1].rstrip() + '…'
    return t


def _is_http(u: Any) -> bool:
    return bool(re.match(r'^https?://[^\s<>"\']+$', str(u or '').strip(), re.I))


def _decode(content: bytes) -> str:
    """네이버 금융 구형 페이지는 euc-kr(cp949), 신형은 utf-8"""
    try:
        return content.decode('utf-8')
    except UnicodeDecodeError:
        return content.decode('cp949', errors='ignore')


def _date_any(v: Any) -> str:
    """'202609251530' · '20260925' · '2026.09.25 15:30' · ISO · RFC822 → 'YYYY-MM-DD' ('' 실패)"""
    s = str(v or '').strip()
    if not s:
        return ''
    m = re.match(r'^(\d{4})(\d{2})(\d{2})(\d{2,6})?$', s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r'^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r'^(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})(\s|$)', s)
    if m:
        return f"20{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    try:
        return parsedate_to_datetime(s).astimezone().strftime('%Y-%m-%d')
    except Exception:
        return ''


def _norm_text(t: str) -> str:
    t = re.sub(r'\[[^\]]{0,12}\]|【[^】]{0,12}】', ' ', str(t or ''))   # [단독] [속보] 등 머리표 제거
    return re.sub(r'[^0-9a-z가-힣]+', '', t.lower())


def _bigrams(t: str) -> set:
    t = _norm_text(t)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else ({t} if t else set())


def _similar(a: str, b: str) -> float:
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _numbers(t: str) -> set:
    out = set()
    for m in re.findall(r'\d+(?:[.,]\d+)*', str(t or '')):
        n = m.replace(',', '')
        out.add(n)
        if '.' in n:
            out.add(n.rstrip('0').rstrip('.'))
    return out


def _is_korean(t: str) -> bool:
    return bool(re.search(r'[가-힣]', str(t or '')))


def _kw_pattern(words: List[str]) -> 're.Pattern':
    """한글 키워드는 부분 일치, 영문 키워드는 단어 경계 일치 ('rate' 가 'corporate' 에 걸리지 않도록)"""
    parts = [re.escape(w) if _is_korean(w) else r'\b' + re.escape(w) + r'\b' for w in words]
    return re.compile('|'.join(parts), re.I)


_TOPIC_RE = [(t, _kw_pattern(ws)) for t, ws in TOPIC_KEYWORDS]
_POS_RE, _NEG_RE = _kw_pattern(_POS_WORDS), _kw_pattern(_NEG_WORDS)


def _topic_of(text: str) -> str:
    s = str(text or '')
    for topic, rx in _TOPIC_RE:
        if rx.search(s):
            return topic
    return '기타'


LOW_SIGNAL_DISCLOSURES = ('소유상황보고서', '대량보유상황보고서', '임원ㆍ주요주주', '임원·주요주주', '증권발행실적보고서')


def _importance_of(item: Dict[str, Any], topic: str) -> int:
    if item.get('kind') == 'disclosure' and any(w in item.get('title', '') for w in LOW_SIGNAL_DISCLOSURES):
        return 1
    base = 3 if item.get('kind') == 'disclosure' else 2
    if topic in ('실적', '리스크'):
        base += 1
    if re.search(r'\d', item.get('title') or ''):
        base += 1
    return max(1, min(5, base))


def _mk_item(title, url, publisher, date, snippet, kind, via) -> Optional[Dict[str, Any]]:
    title = _clean(title, 240)
    url = str(url or '').strip()
    if not title or not _is_http(url):
        return None
    snip = _clean(snippet, SNIPPET_MAX)
    if snip and _similar(snip, title) > 0.6:
        snip = ''
    return {'title': title, 'url': url, 'publisher': _clean(publisher, 60), 'date': _date_any(date),
            'snippet': snip, 'kind': kind, 'via': via}


def _note_error(errors: Optional[list], label: str, e: Exception):
    msg = f"{label}: {type(e).__name__} {str(e)[:140]}"
    logger.warning(f"[리서치] {msg}")
    if errors is not None:
        errors.append(msg)


# ═══════════════════════════════════════════════════════════════ 소스 (a,b) Google 뉴스 RSS
def _gnews_fetch(q: str, n: int, days: int, hl: str, gl: str, ceid: str, kind: str) -> List[Dict[str, Any]]:
    url = (f"https://news.google.com/rss/search?q={quote(f'{q} when:{days}d')}"
           f"&hl={hl}&gl={gl}&ceid={quote(ceid)}")
    root = ET.fromstring(md._get(url, headers={'Accept': 'application/rss+xml, application/xml'}).content)
    out = []
    for it in root.iter('item'):
        title = (it.findtext('title') or '').strip()
        src = it.find('source')
        press = src.text.strip() if src is not None and src.text else ''
        if press and title.endswith(' - ' + press):
            title = title[: -len(' - ' + press)]
        row = _mk_item(title, it.findtext('link'), press, it.findtext('pubDate'), it.findtext('description'),
                       kind, 'google_ko' if kind == 'news' else 'google_en')
        if row:
            out.append(row)
        if len(out) >= n:
            break
    return out


def google_news_ko(q: str, n: int = 10, days: int = NEWS_DAYS, errors: Optional[list] = None) -> List[Dict[str, Any]]:
    """Google 뉴스 RSS (ko-KR, 최근 days 일). md.google_news 는 when:3d 고정이라 별도 구현."""
    try:
        return md.cached(f'research:gko:{q}:{n}:{days}', CACHE_TTL, _gnews_fetch, q, n, days, 'ko', 'KR', 'KR:ko', 'news')
    except Exception as e:
        _note_error(errors, f'Google 뉴스(국내) "{q}"', e)
        return []


def google_news_en(q: str, n: int = 8, days: int = NEWS_DAYS, errors: Optional[list] = None) -> List[Dict[str, Any]]:
    """Google 뉴스 RSS (en-US) — 해외 시각 (kind=global)"""
    try:
        return md.cached(f'research:gen:{q}:{n}:{days}', CACHE_TTL, _gnews_fetch, q, n, days, 'en-US', 'US', 'US:en', 'global')
    except Exception as e:
        _note_error(errors, f'Google News(해외) "{q}"', e)
        return []


# ═══════════════════════════════════════════════════════════════ 소스 (c) 네이버 종목 뉴스
_TITLE_KEYS = ('titleFull', 'title', 'subject', 'reportNm', 'report_nm', 'disclosureTitle')
_DATE_KEYS = ('datetime', 'dateTime', 'date', 'rceptDt', 'rcept_dt', 'publishedAt', 'registeredAt', 'createdAt', 'time')
_PRESS_KEYS = ('officeName', 'press', 'source', 'flrNm', 'flr_nm', 'author', 'submitter')
_URL_KEYS = ('linkUrl', 'mobileNewsUrl', 'url', 'link')
_RCP_KEYS = ('rcpNo', 'rceptNo', 'rcept_no', 'receiptNo')


def _walk_dicts(obj: Any, depth: int = 0):
    """JSON 구조를 모를 때: 제목 키가 있는 dict 를 재귀로 찾는다 (최대 4단계)"""
    if depth > 4:
        return
    if isinstance(obj, dict):
        if any(k in obj for k in _TITLE_KEYS):
            yield obj
            return
        for v in obj.values():
            if isinstance(v, (list, dict)):
                yield from _walk_dicts(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_dicts(v, depth + 1)


def _first(d: Dict[str, Any], keys) -> str:
    for k in keys:
        v = d.get(k)
        if v not in (None, ''):
            return str(v)
    return ''


def _naver_news_json(code: str, n: int) -> List[Dict[str, Any]]:
    """UNVERIFIED: GET m.stock.naver.com/api/news/stock/{code}?pageSize=&page=1
    예상 구조: [{total, items: [{officeId, articleId, officeName, datetime:'202609251530', title, titleFull?, body?}]}]"""
    d = md._get(f'{md.NAVER_M}/news/stock/{code}?pageSize={n}&page=1', timeout=8).json()
    out = []
    for it in _walk_dicts(d):
        oid, aid = str(it.get('officeId') or '').strip(), str(it.get('articleId') or '').strip()
        if oid and aid:
            url = f'https://n.news.naver.com/mnews/article/{oid}/{aid}'
        else:
            url = _first(it, _URL_KEYS)
        row = _mk_item(_first(it, _TITLE_KEYS), url, _first(it, _PRESS_KEYS) or '네이버 증권', _first(it, _DATE_KEYS),
                       it.get('body') or it.get('summary') or it.get('subcontent') or '', 'news', 'naver_api')
        if row:
            out.append(row)
        if len(out) >= n:
            break
    return out


def _naver_table_rows(html_text: str, base: str) -> List[Tuple[str, str, str, str]]:
    """finance.naver.com 종목 뉴스·공시 목록 (table.type5: td.title a / td.info / td.date)"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text, 'html.parser')
    rows = []
    for tr in soup.select('table.type5 tr'):
        a = tr.select_one('td.title a')
        if not a:
            continue
        href = a.get('href', '') or ''
        if href.startswith('javascript'):
            continue
        info = tr.select_one('td.info')
        date = tr.select_one('td.date')
        rows.append((a.get_text(' ', strip=True), href if href.startswith('http') else base + href,
                     info.get_text(' ', strip=True) if info else '', date.get_text(' ', strip=True) if date else ''))
    return rows


def _naver_news_html(code: str, n: int) -> List[Dict[str, Any]]:
    """UNVERIFIED 셀렉터: finance.naver.com/item/news_news.naver?code= (Referer 필요, euc-kr)"""
    r = md._get(f'https://finance.naver.com/item/news_news.naver?code={code}&page=1', timeout=8,
                headers={'Accept': 'text/html', 'Referer': f'https://finance.naver.com/item/news.naver?code={code}'})
    out, seen = [], set()
    for title, href, press, date in _naver_table_rows(_decode(r.content), 'https://finance.naver.com'):
        m_a, m_o = re.search(r'article_id=(\d+)', href), re.search(r'office_id=(\d+)', href)
        url = f'https://n.news.naver.com/mnews/article/{m_o.group(1)}/{m_a.group(1)}' if (m_a and m_o) else href
        if url in seen:
            continue
        seen.add(url)
        row = _mk_item(title, url, press or '네이버 금융', date, '', 'news', 'naver_html')
        if row:
            out.append(row)
        if len(out) >= n:
            break
    return out


def _naver_news_fetch(code: str, n: int) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    err = None
    try:
        items = _naver_news_json(code, n)
    except Exception as e:
        err = e
        logger.info(f"[리서치] 네이버 뉴스 JSON 실패 → HTML 대체: {type(e).__name__} {str(e)[:100]}")
    if not items:
        try:
            items = _naver_news_html(code, n)
        except Exception as e:
            raise RuntimeError(f"JSON({err}) / HTML({e})") from e
    return items


def naver_company_news(code: str, n: int = 20, errors: Optional[list] = None) -> List[Dict[str, Any]]:
    try:
        return md.cached(f'research:nnews:{code}:{n}', CACHE_TTL, _naver_news_fetch, code, n)
    except Exception as e:
        _note_error(errors, '네이버 종목 뉴스', e)
        return []


# ═══════════════════════════════════════════════════════════════ 소스 (d) 공시
def _dart_cache_path() -> str:
    return os.path.join(Config.UPLOAD_FOLDER, 'cache', 'dart_corp_codes.json')


def dart_corp_map(key: str) -> Dict[str, Dict[str, str]]:
    """stock_code → {corp_code, corp_name, corp_eng_name}. corpCode.xml(ZIP) 을 받아 30일 캐시."""
    path = _dart_cache_path()
    with _dart_lock:
        try:
            if os.path.exists(path) and time.time() - os.path.getmtime(path) < DART_CORP_CACHE_DAYS * 86400:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and data:
                    return data
        except Exception as e:
            logger.warning(f"[리서치] DART 기업코드 캐시 읽기 실패: {e}")
        r = md._get(f'https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={key}', timeout=40,
                    headers={'Accept': 'application/zip, */*'})
        try:
            zf = zipfile.ZipFile(io.BytesIO(r.content))
        except zipfile.BadZipFile:
            raise RuntimeError(f"corpCode.xml 이 ZIP 이 아닙니다 (키 오류?): {_clean(r.content[:200].decode('utf-8', 'ignore'))}")
        name = next((x for x in zf.namelist() if x.lower().endswith('.xml')), None)
        if not name:
            raise RuntimeError('corpCode ZIP 에 XML 이 없습니다')
        root = ET.fromstring(zf.read(name))
        out: Dict[str, Dict[str, str]] = {}
        for it in root.iter('list'):
            sc = (it.findtext('stock_code') or '').strip()
            if not sc:
                continue
            out[sc] = {'corp_code': (it.findtext('corp_code') or '').strip(),
                       'corp_name': (it.findtext('corp_name') or '').strip(),
                       'corp_eng_name': (it.findtext('corp_eng_name') or '').strip()}
        if out:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(out, f, ensure_ascii=False)
            os.replace(tmp, path)
        return out


def _dart_fetch(code: str, n: int, key: str) -> List[Dict[str, Any]]:
    corp = dart_corp_map(key).get(code)
    if not corp or not corp.get('corp_code'):
        raise RuntimeError(f'DART 기업코드 없음: {code}')
    bgn = (datetime.now() - timedelta(days=DISCLOSURE_DAYS)).strftime('%Y%m%d')
    d = md._get(f"https://opendart.fss.or.kr/api/list.json?crtfc_key={key}&corp_code={corp['corp_code']}"
                f"&bgn_de={bgn}&page_count={min(100, max(n, 10))}", timeout=10).json()
    st = str(d.get('status', ''))
    if st == '013':          # 조회된 데이터 없음
        return []
    if st != '000':
        raise RuntimeError(f"OpenDART {st}: {d.get('message', '')}")
    out = []
    for it in d.get('list') or []:
        rno = str(it.get('rcept_no') or '').strip()
        if not rno:
            continue
        row = _mk_item(it.get('report_nm'), f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}',
                       it.get('flr_nm') or 'DART', it.get('rcept_dt'), it.get('rm') or '', 'disclosure', 'dart')
        if row:
            out.append(row)
        if len(out) >= n:
            break
    return out


def _naver_disclosure_json(code: str, n: int) -> List[Dict[str, Any]]:
    """UNVERIFIED: GET m.stock.naver.com/api/stock/{code}/disclosure?pageSize=&page=1 (구조 모름 — 방어적 파싱)"""
    d = md._get(f'{md.NAVER_M}/stock/{code}/disclosure?pageSize={n}&page=1', timeout=8).json()
    out = []
    for it in _walk_dicts(d):
        rno = _first(it, _RCP_KEYS)
        if re.fullmatch(r'\d{14}', rno or ''):
            url = f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}'
        else:
            url = _first(it, _URL_KEYS) or f'https://finance.naver.com/item/news_notice.naver?code={code}'
        row = _mk_item(_first(it, _TITLE_KEYS), url, _first(it, _PRESS_KEYS) or '전자공시', _first(it, _DATE_KEYS),
                       '', 'disclosure', 'naver_api')
        if row:
            out.append(row)
        if len(out) >= n:
            break
    return out


def _naver_disclosure_html(code: str, n: int) -> List[Dict[str, Any]]:
    """UNVERIFIED 셀렉터: finance.naver.com/item/news_notice.naver?code= (table.type5, euc-kr)"""
    r = md._get(f'https://finance.naver.com/item/news_notice.naver?code={code}&page=1', timeout=8,
                headers={'Accept': 'text/html', 'Referer': f'https://finance.naver.com/item/news.naver?code={code}'})
    out = []
    for title, href, press, date in _naver_table_rows(_decode(r.content), 'https://finance.naver.com'):
        row = _mk_item(title, href, press or '전자공시', date, '', 'disclosure', 'naver_html')
        if row:
            out.append(row)
        if len(out) >= n:
            break
    return out


def _disclosure_fetch(code: str, n: int, use_dart: bool) -> List[Dict[str, Any]]:
    key = (os.environ.get('DART_API_KEY') or '').strip()
    errs = []
    if use_dart and key:
        try:
            items = _dart_fetch(code, n, key)
            if items:
                return items
        except Exception as e:
            errs.append(f"DART {type(e).__name__}: {str(e)[:120]}")
            logger.warning(f"[리서치] OpenDART 실패 → 네이버 공시로 대체: {errs[-1]}")
    for fn, label in ((_naver_disclosure_json, 'JSON'), (_naver_disclosure_html, 'HTML')):
        try:
            items = fn(code, n)
            if items:
                return items
        except Exception as e:
            errs.append(f"네이버 {label} {type(e).__name__}: {str(e)[:100]}")
    if errs:
        raise RuntimeError(' / '.join(errs))
    return []


def disclosures(code: str, n: int = 20, errors: Optional[list] = None) -> List[Dict[str, Any]]:
    """DART_API_KEY 가 있으면 OpenDART(최근 90일), 없으면 네이버 공시 목록"""
    use_dart = bool((os.environ.get('DART_API_KEY') or '').strip())
    try:
        return md.cached(f'research:disc:{code}:{n}:{int(use_dart)}', CACHE_TTL, _disclosure_fetch, code, n, use_dart)
    except Exception as e:
        _note_error(errors, '공시', e)
        return []


# ═══════════════════════════════════════════════════════════════ 소스 (e) Tavily (선택)
def _tavily_fetch(q: str, n: int) -> List[Dict[str, Any]]:
    key = (os.environ.get('TAVILY_API_KEY') or '').strip()
    r = requests.post('https://api.tavily.com/search',
                      json={'api_key': key, 'query': q, 'max_results': n, 'search_depth': 'basic'},
                      headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}, timeout=15)
    r.raise_for_status()
    kind = 'news' if _is_korean(q) else 'global'
    out = []
    for it in (r.json() or {}).get('results') or []:
        host = urlparse(str(it.get('url') or '')).netloc.replace('www.', '')
        row = _mk_item(it.get('title'), it.get('url'), host, it.get('published_date') or '', it.get('content') or '', kind, 'tavily')
        if row:
            out.append(row)
    return out[:n]


def tavily_search(q: str, n: int = 5, errors: Optional[list] = None) -> List[Dict[str, Any]]:
    if not (os.environ.get('TAVILY_API_KEY') or '').strip():
        return []
    try:
        return md.cached(f'research:tavily:{q}:{n}', CACHE_TTL, _tavily_fetch, q, n)
    except Exception as e:
        _note_error(errors, f'Tavily "{q}"', e)
        return []


# ═══════════════════════════════════════════════════════════════ 감성·커뮤니티 (D-a 모듈, 없으면 대체)
def _fallback_analyze(texts: List[str]) -> Tuple[List[Dict[str, Any]], str]:
    out = []
    for t in texts:
        p = len(_POS_RE.findall(str(t or '')))
        n = len(_NEG_RE.findall(str(t or '')))
        score = 0.0 if p == n else max(-1.0, min(1.0, (p - n) / max(2, p + n)))
        label = 'positive' if score > 0.15 else 'negative' if score < -0.15 else 'neutral'
        out.append({'label': label, 'score': round(score, 3), 'confidence': 0.3 if p or n else 0.1})
    return out, 'lexicon'


def _fallback_summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    labs = [str((r or {}).get('label') or 'neutral') for r in results]
    scores = []
    for r in results:
        try:
            scores.append(float((r or {}).get('score') or 0))
        except (TypeError, ValueError):
            scores.append(0.0)
    n = len(results)
    return {'pos': labs.count('positive'), 'neg': labs.count('negative'), 'neu': n - labs.count('positive') - labs.count('negative'),
            'n': n, 'score': round(sum(scores) / n, 3) if n else 0.0}


def sentiment_analyze(texts: List[str], mode: Optional[str] = None) -> Tuple[List[Dict[str, Any]], str]:
    try:
        from . import sentiment as _s
        res, engine = _s.analyze(texts, mode) if mode else _s.analyze(texts)
        if isinstance(res, list) and len(res) == len(texts):
            return res, str(engine or 'local')
        logger.warning('[리서치] sentiment.analyze 결과 길이 불일치 → 사전 기반 대체')
    except Exception as e:
        logger.warning(f"[리서치] sentiment 모듈 사용 불가 → 사전 기반 대체: {type(e).__name__} {str(e)[:120]}")
    return _fallback_analyze(texts)


def sentiment_summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        from . import sentiment as _s
        out = _s.summarize(results)
        if isinstance(out, dict) and 'score' in out:
            return {k: out.get(k, 0) for k in ('pos', 'neg', 'neu', 'n', 'score')}
    except Exception:
        pass
    return _fallback_summarize(results)


def community_collect(ticker: str, company_name: str) -> Dict[str, Any]:
    try:
        from . import community as _c
        out = _c.collect(ticker, company_name)
        if isinstance(out, dict):
            out.setdefault('sources', [])
            out.setdefault('posts', [])
            out.setdefault('notes', [])
            return out
    except Exception as e:
        logger.warning(f"[리서치] community 모듈 사용 불가: {type(e).__name__} {str(e)[:120]}")
        return {'sources': [], 'posts': [], 'notes': [f'커뮤니티 수집을 건너뛰었습니다 ({type(e).__name__})']}
    return {'sources': [], 'posts': [], 'notes': ['커뮤니티 수집 결과가 없습니다']}


# ═══════════════════════════════════════════════════════════════ 에이전트
class StockResearchAgent:
    """plan → search → summarize → reflect(×rounds) → synthesize. run() 은 research.json 계약 dict 를 반환(저장은 호출자)."""

    SEARCH_ROUND_SEC = 22        # 라운드별 검색 대기 상한
    FINAL_RESERVE_SEC = 30       # 감성·종합 단계를 위해 남겨 둘 시간
    MIN_ROUND_SEC = 20           # 리플렉션 라운드를 새로 시작하려면 (예약분 외) 최소 이만큼 남아야 함

    def __init__(self, llm: Any = None):
        self._llm = llm              # _llm_json(system, user, max_tokens, fast, on_tick) 를 가진 객체
        self._llm_disabled = False
        self._llm_fail = 0
        self.models: List[str] = []
        self.llm_errors: List[str] = []

    # ---------------------------------------------------------- LLM (시간 제한 + 실패 시 규칙 기반)
    def _llm_obj(self):
        if self._llm is None:
            from .stock_knowledge_graph import StockKnowledgeGraph
            self._llm = StockKnowledgeGraph()
        return self._llm

    def _ask(self, system: str, user: str, max_tokens: int, timeout: float,
             on_tick: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
        if self._llm_disabled or timeout < 4:
            return None
        active = {'on': True}

        def tick(nc, nr, sec):
            if active['on'] and on_tick:
                try:
                    on_tick(nc, nr, sec)
                except Exception:
                    pass
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(self._llm_obj()._llm_json, system, user, max_tokens, True, tick)
            data, model = fut.result(timeout=timeout)
            if model and model not in self.models:
                self.models.append(model)
            self._llm_fail = 0
            return data if isinstance(data, dict) else None
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)[:160]}" if str(e) else f"{type(e).__name__} (시간 초과 {timeout:.0f}초)"
            self.llm_errors.append(msg)
            logger.warning(f"[리서치] LLM 호출 실패 → 규칙 기반 대체: {msg}")
            self._llm_fail += 1
            if 'API 키 없음' in str(e) or self._llm_fail >= 2:
                self._llm_disabled = True
            return None
        finally:
            active['on'] = False
            ex.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------- 1) 계획
    @staticmethod
    def _template_plan(name: str, name_en: str) -> Dict[str, Any]:
        return {
            'company_name_en': name_en,
            'questions': ['최근 실적과 시장 기대치 대비 수준은 어떤가?', '최근 주가에 영향을 준 이슈는 무엇인가?',
                          '최근 공시(계약·증자·자사주 등)의 내용은?', '증권사 목표주가·투자의견 변화는?',
                          '해외 시장은 이 회사를 어떻게 보는가?'],
            'queries': {'news_ko': [f'{name} 실적', f'{name} 주가', f'{name} 공시', f'{name} 목표주가'],
                        'global_en': [f'{name_en} stock'] if name_en else []},
            'source': 'template',
        }

    def _plan(self, ticker: str, name: str, name_en: str, timeout: float, on_tick=None) -> Dict[str, Any]:
        tpl = self._template_plan(name, name_en)
        system = ("당신은 한국 주식 리서치 팀장입니다. 종목의 최근 뉴스·공시·해외 보도를 조사하기 위한 계획을 세웁니다. "
                  "모든 질문은 한국어, 영어 검색어만 영어. JSON 한 개만 출력.")
        user = f"""[종목] {name} ({ticker}) · 영문명 후보: {name_en or '모름'}
[기준일] {datetime.now():%Y-%m-%d} (최근 {NEWS_DAYS}일 뉴스, {DISCLOSURE_DAYS}일 공시를 조사)
투자 판단에 필요한 조사 질문 4~6개(실적·업황·수급·정책·경쟁·리스크 관점)와 검색어를 만드세요.
- news_ko: 한국어 Google 뉴스 검색어 3~5개 (반드시 종목명 포함, 2~4단어)
- global_en: 영어 Google News 검색어 1~3개 (공식 영문 회사명 포함)
출력: {{"company_name_en": "공식 영문 회사명", "questions": ["..."], "queries": {{"news_ko": ["..."], "global_en": ["..."]}}}}"""
        data = self._ask(system, user, 1200, timeout, on_tick)
        if not data:
            return tpl
        en = _clean(data.get('company_name_en'), 60)
        if en and not _is_korean(en) and len(en) >= 2:
            name_en = en
        qs = [_clean(q, 120) for q in (data.get('questions') or []) if _clean(q)][:6]
        qd = data.get('queries') or {}
        ko = []
        for q in (qd.get('news_ko') or [])[:5]:
            q = _clean(q, 60)
            if not q:
                continue
            if name not in q:
                q = f'{name} {q}'
            if q not in ko:
                ko.append(q)
        en_q = []
        for q in (qd.get('global_en') or [])[:3]:
            q = _clean(q, 80)
            if not q or _is_korean(q):
                continue
            if name_en and name_en.lower().split()[0] not in q.lower():
                q = f'{name_en} {q}'
            if q not in en_q:
                en_q.append(q)
        if not en_q and name_en:
            en_q = [f'{name_en} stock']
        return {'company_name_en': name_en, 'questions': qs or tpl['questions'],
                'queries': {'news_ko': ko or tpl['queries']['news_ko'], 'global_en': en_q}, 'source': 'llm'}

    # ---------------------------------------------------------- 2) 검색
    def _specs(self, ticker: str, queries: Dict[str, List[str]], fixed: bool, tavily_qs: List[str]) -> List[Dict[str, Any]]:
        specs = []
        if fixed:
            specs.append({'label': '네이버 종목 뉴스', 'query': f'네이버 뉴스 {ticker}', 'fn': naver_company_news, 'args': (ticker, 20)})
            specs.append({'label': '공시', 'query': f'공시 {ticker}', 'fn': disclosures, 'args': (ticker, 20)})
        for q in queries.get('news_ko') or []:
            specs.append({'label': f'국내 뉴스 "{q}"', 'query': q, 'fn': google_news_ko, 'args': (q, 10)})
        for q in queries.get('global_en') or []:
            specs.append({'label': f'해외 뉴스 "{q}"', 'query': q, 'fn': google_news_en, 'args': (q, 8)})
        for q in tavily_qs:
            specs.append({'label': f'Tavily "{q}"', 'query': f'Tavily: {q}', 'fn': tavily_search, 'args': (q, 5)})
        return specs

    @staticmethod
    def _search(specs: List[Dict[str, Any]], deadline: float, on_update: Callable) -> Tuple[List[Dict[str, Any]], Dict[str, int], List[str]]:
        via: Dict[str, int] = {}
        errors: List[str] = []
        if not specs:
            return [], via, errors
        ex = ThreadPoolExecutor(max_workers=min(6, len(specs)))

        def one(sp):
            errs: List[str] = []
            try:
                res = sp['fn'](*sp['args'], errors=errs) or []
            except Exception as e:      # 소스 함수가 예외를 내도 전체는 계속
                errs.append(f"{sp['label']}: {type(e).__name__} {str(e)[:120]}")
                res = []
            return res, errs
        futs = {ex.submit(one, sp): i for i, sp in enumerate(specs)}
        by_spec: Dict[int, List[Dict[str, Any]]] = {}
        pending = set(futs)
        try:
            while pending:
                left = deadline - time.time()
                if left <= 0:
                    break
                done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
                for f in done:
                    i = futs[f]
                    try:
                        res, errs = f.result()
                    except Exception as e:
                        res, errs = [], [f"{specs[i]['label']}: {e}"]
                    errors.extend(errs)
                    by_spec[i] = [dict(it) for it in res if isinstance(it, dict)]
                    for it in by_spec[i]:
                        via[it.get('via') or 'etc'] = via.get(it.get('via') or 'etc', 0) + 1
                    on_update([it for j in sorted(by_spec) for it in by_spec[j]])
            for f in pending:
                errors.append(f"{specs[futs[f]]['label']}: 시간 초과")
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        items = [it for j in sorted(by_spec) for it in by_spec[j]]     # 완료 순서가 아닌 소스 순서 (재현성)
        return items, via, errors

    @staticmethod
    def _dedupe_items(new: List[Dict[str, Any]], seen: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """URL 중복·거의 같은 제목(바이그램 유사도 ≥ 0.6) 제거. 공시 > 네이버 > 국내 > 해외 순으로 우선."""
        prio = {'disclosure': 0, 'news': 1, 'global': 2}
        via_p = {'dart': 0, 'naver_api': 1, 'naver_html': 1, 'google_ko': 2, 'tavily': 3, 'google_en': 3}
        new = sorted(new, key=lambda x: (prio.get(x.get('kind'), 3), via_p.get(x.get('via'), 4)))
        urls = {x['url'] for x in seen}
        kept: List[Dict[str, Any]] = []
        pool = list(seen)
        for it in new:
            if it['url'] in urls:
                continue
            if any(_similar(it['title'], p['title']) >= 0.6 for p in pool):
                continue
            urls.add(it['url'])
            kept.append(it)
            pool.append(it)
        return kept

    @staticmethod
    def _balanced(items: List[Dict[str, Any]], cap: int = 36) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """요약에 넘길 자료를 소스별로 번갈아 고른다 (공시·네이버가 많아도 구글·해외가 밀려나지 않도록).
        반환 (선택, 남은 것). 저신호 공시(지분 보고 등)는 뒤로."""
        order = [('disclosure', None), ('news', 'naver'), ('news', 'google'), ('global', None), ('news', 'tavily')]
        buckets: Dict[int, List[Dict[str, Any]]] = {i: [] for i in range(len(order))}
        for it in items:
            via = str(it.get('via') or '')
            if it.get('kind') == 'disclosure':
                k = 0
            elif it.get('kind') == 'global':
                k = 3
            elif via.startswith('naver'):
                k = 1
            elif via == 'tavily':
                k = 4
            else:
                k = 2
            buckets[k].append(it)
        buckets[0].sort(key=lambda x: any(w in x.get('title', '') for w in LOW_SIGNAL_DISCLOSURES))
        picked: List[Dict[str, Any]] = []
        while len(picked) < cap and any(buckets.values()):
            for k in range(len(order)):
                if buckets[k] and len(picked) < cap:
                    picked.append(buckets[k].pop(0))
        rest = [x for k in range(len(order)) for x in buckets[k]]
        return picked, rest

    # ---------------------------------------------------------- 3) 요약 (원자적 사실)
    @staticmethod
    def _relevant(it: Dict[str, Any], name: str, name_en: str, ticker: str) -> bool:
        if it.get('via') in ('naver_api', 'naver_html', 'dart') or it.get('kind') == 'disclosure':
            return True
        text = f"{it.get('title', '')} {it.get('snippet', '')}"
        if name and name.replace(' ', '') in text.replace(' ', ''):
            return True
        if name_en and name_en.lower().split()[0] in text.lower():
            return True
        return bool(ticker and ticker in text)

    @staticmethod
    def _supported(claim: str, it: Dict[str, Any]) -> bool:
        """주장이 제목·요약에 근거하는지: 수치는 모두 원문(또는 날짜)에 있어야 하고, 국문 자료는 표현도 일정 부분 겹쳐야 함"""
        src = f"{it.get('title', '')} {it.get('snippet', '')}"
        allowed = _numbers(src) | _numbers(it.get('date', '').replace('-', ' '))
        allowed |= {x.lstrip('0') for x in allowed if x.lstrip('0')}
        for n in _numbers(claim):
            if n not in allowed and n.lstrip('0') not in allowed:
                return False
        if it.get('kind') != 'global' and _is_korean(src):
            cb = _bigrams(claim)
            if cb and len(cb & _bigrams(src)) / len(cb) < 0.25:
                return False
        return True

    def _rule_finding(self, it: Dict[str, Any]) -> Dict[str, Any]:
        topic = _topic_of(f"{it['title']} {it.get('snippet', '')}")
        return {'kind': it['kind'], 'claim': it['title'], 'detail': it.get('snippet', ''), 'date': it.get('date', ''),
                'source': {'title': it['title'], 'url': it['url'], 'publisher': it.get('publisher', '')},
                'topic': topic, 'importance': _importance_of(it, topic), 'origin': 'title'}

    def _summarize(self, name: str, ticker: str, name_en: str, items: List[Dict[str, Any]], timeout: float,
                   on_tick=None) -> List[Dict[str, Any]]:
        items = items[:36]
        if not items:
            return []
        smap = {f'S{i + 1}': it for i, it in enumerate(items)}
        lines = [f"[{sid}] {it['kind']} | {it.get('publisher') or '-'} | {it.get('date') or '-'} | {it['title']}"
                 + (f" — {it['snippet'][:160]}" if it.get('snippet') else '') for sid, it in smap.items()]
        system = ("당신은 한국 증권사 리서치 어시스턴트입니다. 주어진 기사 제목·요약에 명시된 사실만 한 문장으로 추출합니다. "
                  "제목·요약에 없는 수치나 사실을 절대 만들지 마세요. 전망·추정은 '~전망', '~보도' 처럼 원문의 표현을 유지합니다. "
                  "JSON 한 개만 출력.")
        user = f"""[종목] {name} ({ticker}){f' / {name_en}' if name_en else ''}
[자료] (각 줄: [S번호] 종류 | 매체 | 날짜 | 제목 — 요약)
{chr(10).join(lines)}

규칙:
- 이 종목과 직접 관련된 자료만 사용합니다. 다른 회사·광고·일반 시황은 건너뜁니다.
- 발견 1개 = 자료 1개(src 하나). 같은 내용의 기사가 여러 개면 가장 구체적인 하나만 씁니다.
- claim: 한국어 한 문장(80자 이내). 자료에 있는 수치를 그대로 포함. 영어 자료는 한국어로 옮기되 수치·단위는 원문 그대로(예: $1.2 billion).
- detail: 자료에서 확인되는 보충 설명 한 문장(없으면 빈 문자열).
- topic: 실적|업황|수급|정책|경쟁|리스크|기타 / importance: 1~5 (실적·중요 공시·대형 계약일수록 높게)
출력: {{"findings": [{{"src": "S1", "claim": "...", "detail": "...", "topic": "실적", "importance": 4}}]}}"""
        data = self._ask(system, user, 3500, timeout, on_tick)
        rel = [it for it in items if self._relevant(it, name, name_en, ticker)]
        if not data or not isinstance(data.get('findings'), list):
            return [self._rule_finding(it) for it in rel[:18]]
        out, used = [], set()
        for f in data['findings']:
            if not isinstance(f, dict):
                continue
            sid = str(f.get('src') or f.get('source') or '').strip().strip('[]')
            it = smap.get(sid)
            if not it or sid in used:
                continue
            used.add(sid)
            claim = _clean(f.get('claim'), 160)
            base = self._rule_finding(it)
            if claim and self._supported(claim, it):
                base['claim'] = claim
                base['origin'] = 'llm'
                detail = _clean(f.get('detail'), SNIPPET_MAX)
                if detail and self._supported(detail, it):
                    base['detail'] = detail
            topic = str(f.get('topic') or '').strip()
            if topic in TOPICS:
                base['topic'] = topic
            try:
                base['importance'] = max(1, min(5, int(float(f.get('importance')))))
            except (TypeError, ValueError):
                pass
            out.append(base)
        # LLM 이 빠뜨린 공시는 규칙 기반으로 보충 (공시는 항상 사실)
        for sid, it in smap.items():
            if sid not in used and it['kind'] == 'disclosure' and len(out) < 24:
                out.append(self._rule_finding(it))
        return out

    @staticmethod
    def _merge_findings(existing: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        added = []
        urls = {f['source']['url'] for f in existing}
        for f in new:
            if f['source']['url'] in urls:
                continue
            if any(_similar(f['claim'], g['claim']) >= 0.7 for g in existing + added):
                continue
            urls.add(f['source']['url'])
            added.append(f)
        return added

    # ---------------------------------------------------------- 4) 리플렉션
    @staticmethod
    def _rule_reflect(name: str, name_en: str, findings: List[Dict[str, Any]], used: set) -> Dict[str, Any]:
        topics = {f['topic'] for f in findings}
        kinds = {f['kind'] for f in findings}
        claims = ' '.join(f['claim'] for f in findings)
        cands = []
        if '실적' not in topics:
            cands.append(('실적·컨센서스 정보 부족', 'news_ko', f'{name} 실적 전망 영업이익'))
        if 'disclosure' not in kinds:
            cands.append(('최근 공시 확인 부족', 'news_ko', f'{name} 공시 계약'))
        if 'global' not in kinds and name_en:
            cands.append(('해외 시각 부족', 'global_en', f'{name_en} shares outlook'))
        if '목표주가' not in claims:
            cands.append(('증권사 목표주가·투자의견 부족', 'news_ko', f'{name} 목표주가 투자의견'))
        if '리스크' not in topics:
            cands.append(('리스크 요인 점검 부족', 'news_ko', f'{name} 우려 리스크'))
        gaps, q = [], {'news_ko': [], 'global_en': []}
        for gap, src, query in cands:
            if query.lower() in used:
                continue
            gaps.append(gap)
            q[src].append(query)
            if len(gaps) >= 3:
                break
        return {'gaps': gaps, 'queries': q, 'source': 'rule'}

    def _reflect(self, plan: Dict[str, Any], name: str, name_en: str, findings: List[Dict[str, Any]],
                 used: set, timeout: float, on_tick=None) -> Dict[str, Any]:
        cnt = {k: sum(1 for f in findings if f['kind'] == k) for k in KINDS}
        tcnt = {t: sum(1 for f in findings if f['topic'] == t) for t in TOPICS}
        flines = '\n'.join(f"- ({f['kind']}/{f['topic']}) {f['claim'][:90]}" for f in findings[:28]) or '- (없음)'
        system = ("당신은 증권사 리서치 팀장입니다. 지금까지의 발견을 보고 투자 판단에 부족한 정보를 찾아 추가 검색어를 만듭니다. "
                  "JSON 한 개만 출력.")
        user = f"""[종목] {name}{f' / {name_en}' if name_en else ''}
[조사 질문] {' / '.join(plan.get('questions') or [])}
[현재 발견 수] 국내 뉴스 {cnt['news']} · 공시 {cnt['disclosure']} · 해외 {cnt['global']} · 주제별 {json.dumps(tcnt, ensure_ascii=False)}
[발견 목록]
{flines}
[이미 쓴 검색어] {', '.join(sorted(used))[:600]}

부족한 관점(예: 실적 수치 없음, 공시 없음, 해외 시각 없음, 목표주가 없음, 리스크 없음)을 최대 3개 찾고,
각 부족분을 메울 새 검색어를 만드세요 (이미 쓴 검색어 금지, 한국어는 종목명 포함).
충분하면 sufficient 를 true 로.
출력: {{"gaps": ["부족한 점 (짧게)"], "queries": {{"news_ko": ["..."], "global_en": ["..."]}}, "sufficient": false}}"""
        data = self._ask(system, user, 900, timeout, on_tick)
        if not data:
            return self._rule_reflect(name, name_en, findings, used)
        gaps = [_clean(g, 80) for g in (data.get('gaps') or []) if _clean(g)][:3]
        qd = data.get('queries') or {}
        q = {'news_ko': [], 'global_en': []}
        for key, lim in (('news_ko', 3), ('global_en', 2)):
            for x in qd.get(key) or []:
                x = _clean(x, 80)
                if not x or x.lower() in used or x in q[key]:
                    continue
                if key == 'global_en' and _is_korean(x):
                    continue
                if key == 'news_ko' and name not in x:
                    x = f'{name} {x}'
                q[key].append(x)
                if len(q[key]) >= lim:
                    break
        if data.get('sufficient') and not gaps:
            return {'gaps': [], 'queries': {'news_ko': [], 'global_en': []}, 'source': 'llm', 'sufficient': True}
        if not (q['news_ko'] or q['global_en']):
            rb = self._rule_reflect(name, name_en, findings, used)
            return {'gaps': gaps or rb['gaps'], 'queries': rb['queries'], 'source': 'llm+rule'}
        return {'gaps': gaps, 'queries': q, 'source': 'llm'}

    # ---------------------------------------------------------- 5) 종합
    @staticmethod
    def _valid_refs(text: str, ids: set) -> str:
        t = re.sub(r'\[\s*(R\d{1,3})\s*\]', lambda m: f'[{m.group(1)}]' if m.group(1) in ids else '', str(text or ''))
        return re.sub(r'\s+([.,])', r'\1', re.sub(r'[ \t]{2,}', ' ', t)).strip()

    def _rule_synthesis(self, findings, sent_news, counts, gaps_hint) -> Dict[str, Any]:
        if not findings:
            return {'summary': '수집된 자료가 없어 리서치 요약을 만들지 못했습니다.', 'key_points': [], 'risks': [], 'gaps_remaining': gaps_hint}
        sc = float(sent_news.get('score') or 0)
        tone = '우호적' if sc > 0.1 else '비우호적' if sc < -0.1 else '중립적'
        top = findings[:3]
        s = [f"최근 {NEWS_DAYS}일 국내 뉴스 {counts['news']}건·공시 {counts['disclosure']}건·해외 {counts['global']}건에서 핵심 사실 {len(findings)}건을 추출했다."]
        s += [f"{f['claim'].rstrip('.')} [{f['id']}]." for f in top]
        if sent_news.get('n'):
            s.append(f"발견 문장의 감성은 {tone}이다(점수 {sc:+.2f}).")
        pos = [f for f in findings if (f.get('sentiment') or {}).get('label') != 'negative' and f['topic'] != '리스크']
        neg = [f for f in findings if (f.get('sentiment') or {}).get('label') == 'negative' or f['topic'] == '리스크']
        return {'summary': ' '.join(s),
                'key_points': [{'text': f['claim'], 'refs': [f['id']]} for f in pos[:4]],
                'risks': [{'text': f['claim'], 'refs': [f['id']]} for f in neg[:3]],
                'gaps_remaining': gaps_hint}

    def _synthesize(self, name: str, findings, sent_news, counts, plan, gaps_hint, timeout, on_tick=None) -> Dict[str, Any]:
        rule = self._rule_synthesis(findings, sent_news, counts, gaps_hint)
        if not findings:
            return dict(rule, source='rule')
        ids = {f['id'] for f in findings}
        lab = {'positive': '긍정', 'negative': '부정', 'neutral': '중립'}
        flines = '\n'.join(f"[{f['id']}] ({KIND_KO[f['kind']]} | {f['source'].get('publisher') or '-'} | {f['date'] or '-'} | "
                           f"{f['topic']} | {lab.get((f.get('sentiment') or {}).get('label'), '중립')}) {f['claim']}"
                           for f in findings)
        system = ("당신은 증권사 리서치 애널리스트입니다. 주어진 발견(R#)만 근거로 요약합니다. "
                  "모든 문장 끝에 근거 [R#] 를 붙이고, 발견에 없는 사실·수치는 절대 만들지 마세요. 한국어. JSON 한 개만 출력.")
        user = f"""[종목] {name}
[조사 질문] {' / '.join(plan.get('questions') or [])}
[발견]
{flines}
[감성] 긍정 {sent_news.get('pos', 0)} · 중립 {sent_news.get('neu', 0)} · 부정 {sent_news.get('neg', 0)} (점수 {float(sent_news.get('score') or 0):+.2f})
[리플렉션이 찾은 부족분] {' / '.join(gaps_hint) or '없음'}
출력: {{"summary": "4~7문장, 문장마다 [R#] 인용", "key_points": [{{"text": "핵심 포인트", "refs": ["R1"]}}] (3~5개),
"risks": [{{"text": "리스크", "refs": ["R2"]}}] (1~4개), "gaps_remaining": ["여전히 확인 못 한 정보"]}}"""
        data = self._ask(system, user, 2500, timeout, on_tick)
        if not data:
            return dict(rule, source='rule')
        summary = self._valid_refs(data.get('summary'), ids)
        if not re.search(r'\[R\d+\]', summary):
            summary = rule['summary']

        def items(key, lim):
            out = []
            for x in data.get(key) or []:
                if isinstance(x, str):
                    x = {'text': x, 'refs': re.findall(r'R\d{1,3}', x)}
                if not isinstance(x, dict):
                    continue
                refs = [str(r).strip('[] ') for r in (x.get('refs') or [])]
                refs = [r for r in dict.fromkeys(refs + re.findall(r'\[(R\d{1,3})\]', str(x.get('text') or ''))) if r in ids]
                text = self._valid_refs(re.sub(r'\[R\d{1,3}\]', '', str(x.get('text') or '')), ids)
                text = _clean(text, 240)
                if text and refs:
                    out.append({'text': text, 'refs': refs[:4]})
                if len(out) >= lim:
                    break
            return out
        kp, rk = items('key_points', 5), items('risks', 4)
        gaps = [_clean(g, 120) for g in (data.get('gaps_remaining') or []) if _clean(g)][:5]
        return {'summary': summary[:1600], 'key_points': kp or rule['key_points'], 'risks': rk or rule['risks'],
                'gaps_remaining': gaps or gaps_hint, 'source': 'llm'}

    # ---------------------------------------------------------- 실행
    def run(self, ticker: str, company_name: str, progress: Optional[Callable] = None, rounds: int = 2,
            time_budget_sec: float = 150, pct_range: Tuple[float, float] = (0, 100),
            company_name_en: Optional[str] = None) -> Dict[str, Any]:
        t0 = time.time()
        deadline = t0 + max(20.0, float(time_budget_sec))
        a, b = pct_range
        ticker = str(ticker or '').strip()
        name = str(company_name or ticker).strip()
        last = {'frac': 0.0}

        def left() -> float:
            return deadline - time.time()

        def emit(frac, msg, rnd=0, phase='', **extra):
            frac = max(last['frac'], min(1.0, frac))
            last['frac'] = frac
            if progress:
                try:
                    progress(round(a + (b - a) * frac), msg, dict({'stage': 'research', 'round': rnd, 'phase': phase}, **extra))
                except Exception:
                    pass

        def ticker_fn(base, frac, rnd, phase):
            return lambda nc, nr, sec: emit(frac, f"{base} — Grok {nc:,}자 수신 ({sec:.0f}초)", rnd, phase)

        name_en = (company_name_en or EN_NAMES.get(ticker) or (name if not _is_korean(name) else '')).strip()

        # 커뮤니티는 1라운드와 병렬
        side = ThreadPoolExecutor(max_workers=1)
        com_future = side.submit(community_collect, ticker, name)

        # 1) 계획
        base = '리서치 계획 수립 중'
        emit(0.02, f'{base} — Grok 이 조사 질문·검색어를 만듭니다', 0, 'plan')
        plan = self._plan(ticker, name, name_en, timeout=min(25.0, max(0.0, left() * 0.18)),
                          on_tick=ticker_fn(base, 0.04, 0, 'plan'))
        name_en = plan.get('company_name_en') or name_en
        nq = sum(len(v) for v in plan['queries'].values())
        emit(0.08, f"리서치 계획 완료 — 질문 {len(plan['questions'])}개 · 검색어 {nq}개"
                   f"{' (템플릿)' if plan.get('source') == 'template' else ''}", 0, 'plan')

        seen_items: List[Dict[str, Any]] = []
        backlog: List[Dict[str, Any]] = []
        findings: List[Dict[str, Any]] = []
        rounds_log: List[Dict[str, Any]] = []
        used = set()
        queries = plan['queries']
        gaps_for_round: List[str] = []
        pending_gaps: List[str] = []
        total_rounds = 1 + max(0, int(rounds))
        tavily_on = bool((os.environ.get('TAVILY_API_KEY') or '').strip())
        stop_note = ''
        sufficient = False

        for r in range(1, total_rounds + 1):
            label = '리서치' if r == 1 else '리플렉션'
            f0 = 0.08 + 0.72 * (r - 1) / total_rounds
            f1 = 0.08 + 0.72 * r / total_rounds
            if r > 1 and left() < self.FINAL_RESERVE_SEC + self.MIN_ROUND_SEC:
                stop_note = f'시간 예산({int(time_budget_sec)}초) 안에 {r}라운드를 진행하지 못했습니다'
                break
            tav = (queries.get('news_ko') or [])[:2] if (tavily_on and r > 1) else []
            specs = self._specs(ticker, queries, r == 1, tav)
            for sp in specs:
                used.add(str(sp['query']).lower())
            pending_gaps = []          # 이번 라운드가 직전 리플렉션의 부족분을 검색함
            rtag = f'{label} {r}라운드'

            def on_update(items, _r=r, _tag=rtag, _f=f0, _f1=f1):
                c = {k: sum(1 for x in items if x.get('kind') == k) for k in KINDS}
                emit(_f + (_f1 - _f) * 0.3, f"{_tag}: 검색 중 — 뉴스 {c['news']} · 공시 {c['disclosure']} · 해외 {c['global']}건",
                     _r, 'search', counts=c)
            emit(f0, f"{rtag}: 검색 중 — 소스 {len(specs)}곳 병렬 조회", r, 'search')
            search_deadline = min(time.time() + self.SEARCH_ROUND_SEC, deadline - self.FINAL_RESERVE_SEC * 0.5)
            raw, via, errors = self._search(specs, search_deadline, on_update)
            counts = {k: sum(1 for x in raw if x.get('kind') == k) for k in KINDS}
            fresh = self._dedupe_items(raw, seen_items + backlog)
            new_items, backlog = self._balanced(backlog + fresh, 36)   # 이번 라운드에 못 다룬 자료는 다음 라운드로
            backlog = backlog[:40]
            seen_items.extend(new_items)

            sbase = f'{rtag}: 핵심 사실 추출 중'
            emit(f0 + (f1 - f0) * 0.45, f"{sbase} — 새 자료 {len(new_items)}건", r, 'summarize')
            budget = min(40.0, left() - self.FINAL_RESERVE_SEC)
            new_f = self._summarize(name, ticker, name_en, new_items, budget,
                                    on_tick=ticker_fn(sbase, f0 + (f1 - f0) * 0.6, r, 'summarize'))
            added = self._merge_findings(findings, new_f)
            findings.extend(added)
            rounds_log.append({
                'round': r, 'type': 'initial' if r == 1 else 'reflection',
                'queries': [sp['query'] for sp in specs], 'new_findings': len(added), 'gaps': gaps_for_round,
                'collected': counts, 'via': via, 'errors': errors[:12],
                'elapsed_sec': round(time.time() - t0, 1),
            })
            emit(f1 - 0.02, f"{rtag} 완료 — 새 발견 {len(added)}건 (누적 {len(findings)}건)", r, 'summarize')

            if r >= total_rounds:
                break
            if left() < self.FINAL_RESERVE_SEC + self.MIN_ROUND_SEC:
                stop_note = f'시간 예산({int(time_budget_sec)}초)이 부족해 리플렉션을 {r}라운드에서 멈췄습니다'
                break
            rbase = f'리플렉션 {r + 1}라운드: 부족한 부분 점검 중'
            emit(f1, f'{rbase} — 발견 {len(findings)}건 검토', r + 1, 'reflect')
            refl = self._reflect(plan, name, name_en, findings, used, timeout=min(20.0, left() - self.FINAL_RESERVE_SEC - 10),
                                 on_tick=ticker_fn(rbase, f1, r + 1, 'reflect'))
            gaps_for_round = refl.get('gaps') or []
            pending_gaps = list(gaps_for_round)
            queries = refl.get('queries') or {}
            if not (queries.get('news_ko') or queries.get('global_en')):
                emit(f1, f"리플렉션 {r + 1}라운드: 추가 검색 불필요 — 조사 질문을 충분히 다뤘습니다", r + 1, 'reflect')
                sufficient = bool(refl.get('sufficient')) or not gaps_for_round
                break
            emit(f1, f"리플렉션 {r + 1}라운드: 부족한 부분 {len(gaps_for_round)}개 재검색 — "
                     f"{' · '.join(gaps_for_round[:3]) or '추가 관점 보강'}", r + 1, 'reflect', gaps=gaps_for_round)
        # 남은 공백 = 검색하지 못한 리플렉션 부족분 + 규칙 기반 재점검 (조사가 충분하다고 판단했으면 비움)
        if not sufficient:
            for g in self._rule_reflect(name, name_en, findings, set())['gaps']:
                if g not in pending_gaps:
                    pending_gaps.append(g)

        # 정리: 중요도 → 최신순, 최대 40건, R# 부여
        findings = sorted(findings, key=lambda f: (-(f.get('importance') or 0),
                                                   -int((f.get('date') or '0').replace('-', '') or 0)))[:MAX_FINDINGS]
        for i, f in enumerate(findings):
            f['id'] = f'R{i + 1}'
            f['detail'] = _clean(f.get('detail'), SNIPPET_MAX)

        # 감성
        emit(0.84, f"감성 분석 중 — 발견 {len(findings)}건", 0, 'sentiment')
        texts = [f['claim'] for f in findings]
        engine = 'lexicon'
        if texts:
            ex = ThreadPoolExecutor(max_workers=1)
            try:
                res, engine = ex.submit(sentiment_analyze, texts).result(timeout=max(8.0, min(40.0, left() - 12)))
            except Exception as e:
                logger.warning(f"[리서치] 감성 분석 시간 초과/실패 → 사전 기반: {type(e).__name__}")
                res, engine = sentiment_analyze(texts, mode='lexicon')
            finally:
                ex.shutdown(wait=False, cancel_futures=True)
        else:
            res = []
        for f, s in zip(findings, res):
            try:
                sc = max(-1.0, min(1.0, float((s or {}).get('score') or 0)))
            except (TypeError, ValueError):
                sc = 0.0
            lab = (s or {}).get('label')
            f['sentiment'] = {'label': lab if lab in ('positive', 'negative', 'neutral') else 'neutral',
                              'score': round(sc, 3), 'engine': engine}
        news_sent = sentiment_summarize([f['sentiment'] for f in findings]) if findings else \
            {'pos': 0, 'neg': 0, 'neu': 0, 'n': 0, 'score': 0.0}

        # 커뮤니티
        try:
            community = com_future.result(timeout=max(3.0, min(25.0, left() - 8)))
        except Exception as e:
            community = {'sources': [], 'posts': [], 'notes': [f'커뮤니티 수집 시간 초과·실패 ({type(e).__name__})']}
        finally:
            side.shutdown(wait=False, cancel_futures=True)
        posts = [p for p in (community.get('posts') or []) if isinstance(p, dict)]
        community['posts'] = posts
        com_sent = sentiment_summarize([p.get('sentiment') or {} for p in posts]) if posts else \
            {'pos': 0, 'neg': 0, 'neu': 0, 'n': 0, 'score': 0.0}
        emit(0.87, f"커뮤니티 심리 수집 완료 — 게시글 {len(posts)}건", 0, 'sentiment')

        ns = float(news_sent.get('score') or 0)
        cs = float(com_sent.get('score') or 0)
        if com_sent.get('n') and news_sent.get('n'):
            overall = 0.7 * ns + 0.3 * cs
        elif com_sent.get('n'):
            overall = cs
        else:
            overall = ns

        # 종합
        counts = {k: sum(1 for f in findings if f['kind'] == k) for k in KINDS}
        gaps_hint = list(pending_gaps)
        if stop_note:
            gaps_hint.append(stop_note)
        if not findings:
            all_err = [e for rl in rounds_log for e in rl.get('errors') or []]
            gaps_hint = ['모든 외부 소스에서 자료를 가져오지 못했습니다 — 네트워크·차단 여부나 API 키를 확인한 뒤 다시 시도하세요'
                         + (f" (오류 {len(all_err)}건)" if all_err else '')] + gaps_hint
        cbase = '리서치 종합 중'
        emit(0.9, f"{cbase} — 발견 {len(findings)}건 요약·인용 정리", 0, 'synthesize')
        syn = self._synthesize(name, findings, news_sent, counts, plan, gaps_hint,
                               timeout=min(35.0, left() - 2), on_tick=ticker_fn(cbase, 0.93, 0, 'synthesize'))

        for f in findings:
            f.pop('origin', None)
            f['source'] = {'title': f['source'].get('title', ''), 'url': f['source'].get('url', ''),
                           'publisher': f['source'].get('publisher', '')}
        cited = {f['source']['url'] for f in findings}
        research = {
            'ticker': ticker, 'company_name': name, 'company_name_en': name_en,
            'created_at': datetime.now().isoformat(timespec='seconds'),
            'plan': plan['questions'],
            'rounds': rounds_log,
            'findings': [{k: f.get(k) for k in ('id', 'kind', 'claim', 'detail', 'date', 'source', 'sentiment', 'topic', 'importance')}
                         for f in findings],
            'summary': syn['summary'],
            'key_points': syn['key_points'],
            'risks': syn['risks'],
            'gaps_remaining': syn['gaps_remaining'],
            'sentiment': {'news': news_sent, 'community': com_sent, 'overall_score': round(overall, 3), 'engine': engine},
            'community': community,
            'sources_count': len(cited),
            'documents_scanned': len(seen_items) + len(backlog),
            'elapsed_sec': round(time.time() - t0, 1),
            'llm': ', '.join(self.models) or 'rule-based',
            'llm_errors': self.llm_errors[:6],
            'plan_source': plan.get('source'),
            'synthesis_source': syn.get('source'),
        }
        emit(1.0, f"리서치 완료 — 발견 {len(findings)}건 · 출처 {len(cited)}곳 · 감성 {overall:+.2f}", 0, 'done')
        return research


def validate_research(r: Dict[str, Any]) -> List[str]:
    """research.json 계약 점검 (테스트·디버깅용). 문제 목록을 반환 (빈 리스트 = 정상)."""
    errs = []
    for k in ('ticker', 'company_name', 'company_name_en', 'created_at', 'plan', 'rounds', 'findings', 'summary',
              'key_points', 'risks', 'gaps_remaining', 'sentiment', 'community', 'sources_count', 'elapsed_sec'):
        if k not in r:
            errs.append(f'키 없음: {k}')
    ids = set()
    for f in r.get('findings') or []:
        ids.add(f.get('id'))
        if not re.fullmatch(r'R\d+', str(f.get('id'))):
            errs.append(f"잘못된 id {f.get('id')}")
        if f.get('kind') not in KINDS:
            errs.append(f"{f.get('id')} kind {f.get('kind')}")
        if f.get('topic') not in TOPICS:
            errs.append(f"{f.get('id')} topic {f.get('topic')}")
        if not _is_http((f.get('source') or {}).get('url')):
            errs.append(f"{f.get('id')} url")
        s = f.get('sentiment') or {}
        if s.get('label') not in ('positive', 'negative', 'neutral') or not -1 <= float(s.get('score', 0)) <= 1:
            errs.append(f"{f.get('id')} sentiment")
        if len(str(f.get('detail') or '')) > SNIPPET_MAX:
            errs.append(f"{f.get('id')} detail 길이")
    if len(r.get('findings') or []) > MAX_FINDINGS:
        errs.append('findings > 40')
    for key in ('key_points', 'risks'):
        for x in r.get(key) or []:
            if not x.get('text') or any(ref not in ids for ref in x.get('refs') or []):
                errs.append(f'{key} refs 오류: {x}')
    for m in re.findall(r'\[(R\d+)\]', str(r.get('summary') or '')):
        if m not in ids:
            errs.append(f'summary 에 없는 인용 {m}')
    s = r.get('sentiment') or {}
    for k in ('news', 'community'):
        if not all(x in (s.get(k) or {}) for x in ('pos', 'neg', 'neu', 'n', 'score')):
            errs.append(f'sentiment.{k} 형식')
    if 'overall_score' not in s or 'engine' not in s:
        errs.append('sentiment overall/engine')
    for rd in r.get('rounds') or []:
        if not all(k in rd for k in ('round', 'queries', 'new_findings', 'gaps')):
            errs.append(f'rounds 형식 {rd.get("round")}')
    com = r.get('community') or {}
    if not all(k in com for k in ('sources', 'posts', 'notes')):
        errs.append('community 형식')
    return errs
