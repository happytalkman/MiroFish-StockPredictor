"""
첫 화면(대시보드)용 시장 데이터 수집기

  - 국내 지수·종목  : 네이버 증권 모바일 API (m.stock.naver.com) + 일봉 차트 (fchart.stock.naver.com)
  - 해외 지수·환율·원자재 : Yahoo Finance v8 chart API
  - 암호화폐        : CoinMarketCap data-api (키 불필요) → 실패 시 CoinGecko
  - 뉴스            : Google 뉴스 RSS (국내·해외 증시) → 보조: 네이버 금융 주요뉴스
  - 증시 일정       : 규칙 기반(옵션·선물 만기, 미 고용지표, 수출입동향) + 연준 FOMC 공표 일정 + 휴장일

모든 호출은 짧은 TTL 캐시를 거치며, 개별 소스 실패가 전체 화면을 막지 않도록 오류를 필드에 담아 반환합니다.
"""

import re
import time
import threading
import calendar as _cal
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

import requests

from ..utils.logger import get_logger
from ..utils.resilience import retry_call

logger = get_logger('mirofish.market')

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
           "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}

_cache: Dict[str, Any] = {}
_cache_lock = threading.Lock()
_cache_inflight: Dict[str, threading.Event] = {}
_cache_failures: Dict[str, Any] = {}
_CACHE_FAILURE_COOLDOWN = 5.0


def cached(key: str, ttl: int, fn, *args, **kwargs):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        failed = _cache_failures.get(key)
        if failed and now - failed[0] < _CACHE_FAILURE_COOLDOWN:
            if hit and _can_serve_stale(hit[0], now, ttl):
                return hit[1]
            raise failed[1]
        pending = _cache_inflight.get(key)
        leader = pending is None
        if leader:
            pending = threading.Event()
            _cache_inflight[key] = pending

    if not leader:
        if not pending.wait(timeout=max(5.0, min(30.0, float(ttl)))):
            with _cache_lock:
                hit = _cache.get(key)
            if hit and _can_serve_stale(hit[0], time.time(), ttl):
                return hit[1]
            raise TimeoutError(f'캐시 갱신 대기 시간 초과: {key}')
        return cached(key, ttl, fn, *args, **kwargs)

    try:
        val = fn(*args, **kwargs)
    except Exception as exc:
        failed_at = time.time()
        with _cache_lock:
            _cache_failures[key] = (failed_at, exc)
            hit = _cache.get(key)
        if hit and _can_serve_stale(hit[0], failed_at, ttl):
            logger.warning(f'[{key}] 데이터 갱신 실패 — 만료된 캐시 사용: {str(exc)[:120]}')
            return hit[1]
        raise
    else:
        with _cache_lock:
            _cache[key] = (time.time(), val)
            _cache_failures.pop(key, None)
        return val
    finally:
        with _cache_lock:
            event = _cache_inflight.pop(key, None)
            if event:
                event.set()


def _can_serve_stale(cached_at: float, now: float, ttl: int) -> bool:
    stale_grace = min(max(float(ttl) * 10, 60.0), 3600.0)
    return now - cached_at < float(ttl) + stale_grace


def _get(url: str, timeout: float = 8, **kw) -> requests.Response:
    h = dict(HEADERS)
    h.update(kw.pop('headers', {}) or {})

    def once():
        r = requests.get(url, headers=h, timeout=timeout, **kw)
        r.raise_for_status()
        return r
    # 429·5xx·연결 오류만 1회 짧게 재시도 (대시보드 응답 지연을 막기 위해 최대 2회)
    return retry_call(once, tries=2, base=0.6, cap=3, label=url.split('?')[0][-60:])


def num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(',', '').replace('%', '').replace('+', '').strip()
    m = re.search(r'-?\d+(\.\d+)?', s)
    return float(m.group(0)) if m else None


def _pool(n=12):
    return ThreadPoolExecutor(max_workers=n)


# ═══════════════════════════════════════════════════════════════ 국내 (네이버)
NAVER_M = 'https://m.stock.naver.com/api'
_SIGN = {'1': 1, '2': 1, '3': 0, '4': -1, '5': -1}


def _signed(pct, change, code):
    s = _SIGN.get(str(code or ''), None)
    if s is not None and pct is not None:
        pct = abs(pct) * (1 if s >= 0 else -1) if s != 0 else 0.0
        if change is not None:
            change = abs(change) * (1 if s >= 0 else -1) if s != 0 else 0.0
    return pct, change


def naver_index(code: str) -> Dict[str, Any]:
    """KOSPI / KOSDAQ / KPI200 지수 현재값"""
    d = _get(f'{NAVER_M}/index/{code}/basic').json()
    price = num(d.get('closePrice'))
    change = num(d.get('compareToPreviousClosePrice'))
    pct = num(d.get('fluctuationsRatio'))
    code_ = (d.get('compareToPreviousPrice') or {}).get('code')
    pct, change = _signed(pct, change, code_)
    return {
        'code': code, 'name': d.get('stockName') or code, 'price': price, 'change': change, 'pct': pct,
        'status': d.get('marketStatus'), 'time': d.get('localTradedAt'),
    }


def fchart(symbol: str, count: int = 130, timeframe: str = 'day') -> List[Dict[str, Any]]:
    """네이버 일봉: 종목코드(005930) 또는 지수(KOSPI/KOSDAQ). [{d,o,h,l,c,v}] 오래된→최근"""
    url = (f'https://fchart.stock.naver.com/sise.nhn?symbol={symbol}'
           f'&timeframe={timeframe}&count={count}&requestType=0')
    txt = _get(url, headers={'Accept': '*/*'}).content.decode('euc-kr', errors='ignore')
    rows = []
    for m in re.finditer(r'data="([^"]+)"', txt):
        p = m.group(1).split('|')
        if len(p) >= 6:
            try:
                rows.append({'d': p[0], 'o': float(p[1]), 'h': float(p[2]), 'l': float(p[3]),
                             'c': float(p[4]), 'v': float(p[5])})
            except ValueError:
                continue
    return rows


def naver_stock_list(market: str = 'KOSPI', kind: str = 'marketValue', size: int = 100, pages: int = 1) -> List[Dict[str, Any]]:
    """kind: marketValue(시총) | up(상승) | down(하락) | quantTop(거래량) | priceTop(거래대금)"""
    out = []
    for page in range(1, pages + 1):
        d = _get(f'{NAVER_M}/stocks/{kind}/{market}?page={page}&pageSize={size}').json()
        items = d.get('stocks') if isinstance(d, dict) else d
        for s in items or []:
            if (s.get('stockEndType') or 'stock') not in ('stock',):
                continue  # ETF·ETN 등 제외 (개별 종목만)
            pct = num(s.get('fluctuationsRatio'))
            change = num(s.get('compareToPreviousClosePrice'))
            pct, change = _signed(pct, change, (s.get('compareToPreviousPrice') or {}).get('code'))
            out.append({
                'ticker': s.get('itemCode') or s.get('reutersCode', '')[:6],
                'name': s.get('stockName'),
                'market': market,
                'price': num(s.get('closePrice')),
                'change': change, 'pct': pct,
                'volume': num(s.get('accumulatedTradingVolume')),
                'value_mil': num(s.get('accumulatedTradingValue')),   # 백만원
                'mcap_eok': num(s.get('marketValue')),                # 억원
            })
        if not items or len(items) < size:
            break
    if not out and kind != 'marketValue':
        return out
    return [x for x in out if x['ticker']]


def naver_fundamentals(ticker: str) -> Dict[str, Any]:
    """PER·PBR·배당·외국인소진율 (종목 통합정보)"""
    d = _get(f'{NAVER_M}/stock/{ticker}/integration').json()
    res = {}
    for it in d.get('totalInfos') or []:
        key = (it.get('code') or it.get('key') or '').lower()
        val = num(it.get('value'))
        if 'per' == key or it.get('key') == 'PER':
            res['per'] = val
        elif 'pbr' == key or it.get('key') == 'PBR':
            res['pbr'] = val
        elif 'eps' == key or it.get('key') == 'EPS':
            res['eps'] = val
        elif 'dividendyieldratio' in key or '배당수익률' in (it.get('key') or ''):
            res['div_yield'] = val
        elif 'foreignrate' in key or '외인소진율' in (it.get('key') or ''):
            res['foreign_pct'] = val
        elif 'highpriceof52weeks' in key or '52주 최고' in (it.get('key') or ''):
            res['high52'] = val
        elif 'lowpriceof52weeks' in key or '52주 최저' in (it.get('key') or ''):
            res['low52'] = val
        elif key == 'bps':
            res['bps'] = val
        elif key == 'dividend':
            res['dps'] = val
        elif key == 'cnseps':
            res['cns_eps'] = val
        elif key == 'cnsper':
            res['cns_per'] = val
    # 증권사 컨센서스 (목표주가 평균·투자의견 평균 1~5) + 최근 증권사 리포트 제목
    c = d.get('consensusInfo') or {}
    if isinstance(c, dict):
        res['target_mean'] = num(c.get('priceTargetMean'))
        res['recomm_mean'] = num(c.get('recommMean'))
        res['consensus_date'] = c.get('createDate')
    reps = []
    for r in (d.get('researches') or [])[:8]:
        if not isinstance(r, dict) or not r.get('tit'):
            continue
        w = str(r.get('wdt') or '')
        reps.append({'broker': r.get('bnm') or '', 'title': str(r.get('tit')).strip(),
                     'date': f"{w[:4]}-{w[4:6]}-{w[6:8]}" if len(w) == 8 else w, 'id': r.get('id')})
    if reps:
        res['researches'] = reps
    return res


def korea_overview() -> Dict[str, Any]:
    def one(code, chart_sym):
        try:
            idx = naver_index(code)
        except Exception as e:
            idx = {'code': code, 'error': str(e)[:160]}
        try:
            ch = fchart(chart_sym, 90)
            idx['spark'] = [r['c'] for r in ch]
            idx['spark_dates'] = [r['d'] for r in ch]
            if idx.get('price') is None and ch:
                idx['price'] = ch[-1]['c']
                idx['change'] = ch[-1]['c'] - ch[-2]['c'] if len(ch) > 1 else None
                idx['pct'] = (idx['change'] / ch[-2]['c'] * 100) if len(ch) > 1 else None
                idx.pop('error', None)
        except Exception as e:
            idx.setdefault('spark', [])
            idx['spark_error'] = str(e)[:120]
        return idx
    with _pool(3) as ex:
        k, q, k2 = ex.map(lambda a: one(*a), [('KOSPI', 'KOSPI'), ('KOSDAQ', 'KOSDAQ'), ('KPI200', 'KPI200')])
    k.setdefault('name', '코스피'); q.setdefault('name', '코스닥'); k2.setdefault('name', '코스피200')
    return {'kospi': k, 'kosdaq': q, 'kospi200': k2}


def korea_movers(market: str = 'KOSPI') -> Dict[str, Any]:
    res = {}
    kinds = {'up': 'up', 'down': 'down', 'value': 'priceTop', 'volume': 'quantTop', 'mcap': 'marketValue'}
    def f(item):
        name, kind = item
        try:
            return name, naver_stock_list(market, kind, 20)[:20], None
        except Exception as e:
            return name, [], str(e)[:160]
    with _pool(5) as ex:
        for name, rows, err in ex.map(f, kinds.items()):
            res[name] = rows
            if err:
                res[name + '_error'] = err
    # 거래대금/거래량 순위 API 가 없으면 시총 상위에서 계산
    base = res.get('mcap') or []
    if not res.get('value') and base:
        res['value'] = sorted(base, key=lambda x: -(x.get('value_mil') or 0))[:20]
    if not res.get('volume') and base:
        res['volume'] = sorted(base, key=lambda x: -(x.get('volume') or 0))[:20]
    if not res.get('up') and base:
        res['up'] = sorted(base, key=lambda x: -(x.get('pct') or -999))[:20]
    if not res.get('down') and base:
        res['down'] = sorted(base, key=lambda x: (x.get('pct') or 999))[:20]
    # 시장 폭(상승/하락 종목 비율) — 시총 상위 기준
    if base:
        up = sum(1 for x in base if (x.get('pct') or 0) > 0)
        dn = sum(1 for x in base if (x.get('pct') or 0) < 0)
        res['breadth'] = {'up': up, 'down': dn, 'flat': len(base) - up - dn, 'sample': len(base)}
    return res


# ═══════════════════════════════════════════════════════════════ 해외 (Yahoo)
GLOBAL_SYMBOLS = [
    ('^GSPC', 'S&P 500', 'index'), ('^IXIC', '나스닥', 'index'), ('^DJI', '다우존스', 'index'),
    ('^SOX', '필라델피아 반도체', 'index'), ('^N225', '닛케이225', 'index'), ('^HSI', '항셍', 'index'),
    ('000001.SS', '상하이종합', 'index'), ('^STOXX50E', '유로스톡스50', 'index'),
    ('KRW=X', '원/달러', 'fx'), ('JPYKRW=X', '원/엔(1엔)', 'fx'), ('DX-Y.NYB', '달러인덱스', 'fx'),
    ('^TNX', '미 10년물 금리', 'rate'), ('^VIX', 'VIX 공포지수', 'rate'),
    ('CL=F', 'WTI 원유', 'cmdty'), ('GC=F', '금', 'cmdty'), ('HG=F', '구리', 'cmdty'),
    ('NQ=F', '나스닥100 선물', 'future'), ('ES=F', 'S&P500 선물', 'future'),
]


def yahoo_chart(symbol: str, rng: str = '1mo', interval: str = '1d') -> Dict[str, Any]:
    last_err = None
    for host in ('query1', 'query2'):
        try:
            url = f'https://{host}.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol)}?range={rng}&interval={interval}'
            d = _get(url).json()
            r = d['chart']['result'][0]
            meta = r.get('meta', {})
            closes = [c for c in ((r.get('indicators', {}).get('quote') or [{}])[0].get('close') or []) if c is not None]
            price = meta.get('regularMarketPrice') or (closes[-1] if closes else None)
            prev = meta.get('chartPreviousClose') if rng == '1d' else None
            if prev is None:
                prev = closes[-2] if len(closes) >= 2 else meta.get('previousClose') or meta.get('chartPreviousClose')
            change = (price - prev) if (price is not None and prev) else None
            return {'symbol': symbol, 'price': price, 'prev': prev, 'change': change,
                    'pct': (change / prev * 100) if change is not None and prev else None,
                    'currency': meta.get('currency'), 'spark': [round(c, 4) for c in closes[-30:]],
                    'time': meta.get('regularMarketTime')}
        except Exception as e:
            last_err = e
    raise RuntimeError(f'Yahoo {symbol}: {last_err}')


def global_overview() -> List[Dict[str, Any]]:
    def f(item):
        sym, name, kind = item
        try:
            q = yahoo_chart(sym)
        except Exception as e:
            q = {'symbol': sym, 'error': str(e)[:140]}
        q.update({'name': name, 'kind': kind})
        return q
    with _pool(12) as ex:
        return list(ex.map(f, GLOBAL_SYMBOLS))


# ═══════════════════════════════════════════════════════════════ 암호화폐 (CMC)
def crypto_overview(limit: int = 15) -> Dict[str, Any]:
    try:
        d = _get('https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing'
                 f'?start=1&limit={limit}&sortBy=market_cap&sortType=desc&convert=USD').json()
        rows = []
        for c in d['data']['cryptoCurrencyList']:
            q = next((x for x in c.get('quotes', []) if x.get('name') == 'USD'), (c.get('quotes') or [{}])[0])
            rows.append({'rank': c.get('cmcRank'), 'name': c.get('name'), 'symbol': c.get('symbol'),
                         'slug': c.get('slug'), 'price': q.get('price'), 'pct_1h': q.get('percentChange1h'),
                         'pct_24h': q.get('percentChange24h'), 'pct_7d': q.get('percentChange7d'),
                         'mcap': q.get('marketCap'), 'volume24h': q.get('volume24h'),
                         'dominance': q.get('dominance')})
        total = d['data'].get('totalCount')
        glob = {}
        try:
            g = _get('https://api.coinmarketcap.com/data-api/v3/global-metrics/quotes/latest').json()['data']
            gq = next((x for x in g.get('quotes', []) if x.get('name') == 'USD'), (g.get('quotes') or [{}])[0])
            glob = {'total_mcap': gq.get('totalMarketCap'), 'total_volume24h': gq.get('totalVolume24H'),
                    'btc_dominance': g.get('btcDominance'), 'eth_dominance': g.get('ethDominance'),
                    'mcap_change_24h': gq.get('totalMarketCapYesterdayPercentageChange')}
        except Exception as e:
            glob = {'error': str(e)[:120]}
        try:
            _now = int(time.time())
            fg = _get(f'https://api.coinmarketcap.com/data-api/v3/fear-greed/chart?start={_now - 7 * 86400}&end={_now}').json()['data']
            last = (fg.get('dataList') or [])[-1]
            glob['fear_greed'] = {'score': last.get('score'), 'label': last.get('name')}
        except Exception:
            pass
        return {'source': 'CoinMarketCap', 'coins': rows, 'global': glob, 'total_count': total}
    except Exception as e:
        logger.warning(f'CMC 실패, CoinGecko 로 대체: {e}')
        d = _get('https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc'
                 f'&per_page={limit}&page=1&price_change_percentage=1h,24h,7d').json()
        rows = [{'rank': c.get('market_cap_rank'), 'name': c.get('name'), 'symbol': (c.get('symbol') or '').upper(),
                 'price': c.get('current_price'), 'pct_1h': c.get('price_change_percentage_1h_in_currency'),
                 'pct_24h': c.get('price_change_percentage_24h'), 'pct_7d': c.get('price_change_percentage_7d_in_currency'),
                 'mcap': c.get('market_cap'), 'volume24h': c.get('total_volume')} for c in d]
        return {'source': 'CoinGecko', 'coins': rows, 'global': {}, 'fallback_reason': str(e)[:160]}


# ═══════════════════════════════════════════════════════════════ 뉴스
NEWS_QUERIES = {
    'domestic': '코스피 OR 코스닥 증시',
    'global': '뉴욕증시 OR 미국 증시 OR 연준',
    'semis': '반도체 주가 삼성전자 OR SK하이닉스',
    'crypto': '비트코인 시세',
}


def google_news(q: str, n: int = 14) -> List[Dict[str, Any]]:
    url = f'https://news.google.com/rss/search?q={requests.utils.quote(q + " when:3d")}&hl=ko&gl=KR&ceid=KR:ko'
    root = ET.fromstring(_get(url, headers={'Accept': 'application/rss+xml'}).content)
    out = []
    for it in root.iter('item'):
        title = (it.findtext('title') or '').strip()
        src = it.find('source')
        press = src.text.strip() if src is not None and src.text else ''
        if press and title.endswith(' - ' + press):
            title = title[: -len(' - ' + press)]
        try:
            ts = parsedate_to_datetime(it.findtext('pubDate')).astimezone().strftime('%m-%d %H:%M')
        except Exception:
            ts = ''
        out.append({'title': title, 'url': it.findtext('link'), 'press': press, 'time': ts})
        if len(out) >= n:
            break
    return out


def naver_main_news(n: int = 14) -> List[Dict[str, Any]]:
    from bs4 import BeautifulSoup
    html = _get('https://finance.naver.com/news/mainnews.naver',
                headers={'Accept': 'text/html'}).content.decode('euc-kr', errors='ignore')
    soup = BeautifulSoup(html, 'html.parser')
    out = []
    for li in soup.select('.mainNewsList li, ul.newsList li'):
        a = li.select_one('.articleSubject a') or li.select_one('dt a, dd a')
        if not a:
            continue
        href = a.get('href', '')
        out.append({'title': a.get_text(strip=True), 'url': href if href.startswith('http') else 'https://finance.naver.com' + href,
                    'press': (li.select_one('.press').get_text(strip=True) if li.select_one('.press') else '네이버 금융'),
                    'time': (li.select_one('.wdate').get_text(strip=True)[5:16] if li.select_one('.wdate') else '')})
        if len(out) >= n:
            break
    return out


NEWS_FALLBACK = {'domestic': ['코스피', '증시'], 'global': ['뉴욕증시', '미국 증시'], 'semis': ['반도체 주가', 'SK하이닉스'],
                 'crypto': ['비트코인', '가상자산']}


def news(cat: str = 'domestic') -> Dict[str, Any]:
    queries = [NEWS_QUERIES.get(cat, NEWS_QUERIES['domestic'])] + NEWS_FALLBACK.get(cat, [])
    last_err = None
    for q in queries:
        try:
            items = google_news(q)
            if items:
                return {'items': items, 'source': 'Google 뉴스', 'query': q}
        except Exception as e:
            last_err = e
    if cat == 'domestic':
        try:
            items = naver_main_news()
            if items:
                return {'items': items, 'source': '네이버 금융'}
        except Exception as e:
            last_err = e
    return {'items': [], 'error': str(last_err)[:160] if last_err else '뉴스 결과가 없습니다'}


# ═══════════════════════════════════════════════════════════════ 증시 일정
# 연준 FOMC 2026 정례회의 (Federal Reserve 공표 일정, 둘째 날 결과 발표 · 한국시간 익일 새벽)
FOMC_2026 = [(1, 27, 28), (3, 17, 18), (4, 28, 29), (6, 16, 17), (7, 28, 29), (9, 15, 16), (10, 27, 28), (12, 8, 9)]
# 한국거래소 휴장일 (공휴일 기준)
KRX_HOLIDAYS = {
    date(2026, 10, 5): '개천절 대체휴일', date(2026, 10, 9): '한글날', date(2026, 12, 25): '성탄절',
    date(2026, 12, 31): '연말 휴장', date(2027, 1, 1): '신정',
}


def _nth_weekday(y, m, weekday, n):
    d = date(y, m, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _prev_business(d):
    while d.weekday() >= 5 or d in KRX_HOLIDAYS:
        d -= timedelta(days=1)
    return d


def market_calendar(days: int = 45) -> List[Dict[str, Any]]:
    today = date.today()
    end = today + timedelta(days=days)
    ev = []
    y, m = today.year, today.month
    for _ in range(4):
        exp = _prev_business(_nth_weekday(y, m, 3, 2))  # 둘째 목요일
        quad = m in (3, 6, 9, 12)
        ev.append({'date': exp, 'title': '선물·옵션 동시만기일 (네 마녀의 날)' if quad else '코스피200 옵션 만기일',
                   'region': 'KR', 'importance': 3 if quad else 2, 'category': '파생'})
        ev.append({'date': date(y, m, 1), 'title': f'{m}월 수출입동향 발표 (산업통상자원부)', 'region': 'KR', 'importance': 2, 'category': '경제지표'})
        nfp = _nth_weekday(y, m, 4, 1)
        ev.append({'date': nfp, 'title': '미국 고용보고서(비농업 고용·실업률) — 한국시간 21:30', 'region': 'US', 'importance': 3, 'category': '경제지표'})
        if m in (1, 4, 7, 10):
            ev.append({'date': date(y, m, 5), 'title': '삼성전자·LG전자 분기 잠정실적 발표 시즌 (전후)', 'region': 'KR', 'importance': 3, 'category': '실적'})
            ev.append({'date': _nth_weekday(y, m, 1, 2) + timedelta(days=1), 'title': '미국 대형은행 실적 시즌 개막 (JP모건 등)', 'region': 'US', 'importance': 2, 'category': '실적'})
        m += 1
        if m > 12:
            m, y = 1, y + 1
    for yy, table in ((2026, FOMC_2026),):
        for mm, d1, d2 in table:
            ev.append({'date': date(yy, mm, d2), 'title': f'미국 FOMC 금리 결정 ({mm}/{d1}~{d2}) — 한국시간 익일 03:00 발표',
                       'region': 'US', 'importance': 3, 'category': '통화정책'})
    for d, name in KRX_HOLIDAYS.items():
        ev.append({'date': d, 'title': f'한국거래소 휴장 — {name}', 'region': 'KR', 'importance': 1, 'category': '휴장'})
    out = []
    for e in ev:
        if today <= e['date'] <= end:
            e = dict(e)
            e['dday'] = (e['date'] - today).days
            e['weekday'] = '월화수목금토일'[e['date'].weekday()]
            e['date'] = e['date'].isoformat()
            out.append(e)
    out.sort(key=lambda x: (x['date'], -x['importance']))
    return out


def market_status() -> Dict[str, Any]:
    now = datetime.now()
    t = now.hour * 60 + now.minute
    d = now.date()
    if d.weekday() >= 5 or d in KRX_HOLIDAYS:
        st = '휴장'
    elif 8 * 60 + 30 <= t < 9 * 60:
        st = '장 시작 전 동시호가'
    elif 9 * 60 <= t < 15 * 60 + 20:
        st = '장중'
    elif 15 * 60 + 20 <= t < 15 * 60 + 30:
        st = '장 마감 동시호가'
    elif 15 * 60 + 40 <= t < 18 * 60:
        st = '시간외 거래'
    else:
        st = '장 마감'
    return {'status': st, 'now': now.strftime('%Y-%m-%d %H:%M:%S')}


# ═══════════════════════════════════════════════════════════════ 묶음
def overview() -> Dict[str, Any]:
    out = {}
    jobs = {
        'korea': (lambda: cached('korea', 45, korea_overview)),
        'global': (lambda: cached('global', 90, global_overview)),
        'crypto': (lambda: cached('crypto', 60, crypto_overview)),
    }
    ex = _pool(3)
    try:
        futs = {k: ex.submit(f) for k, f in jobs.items()}
        for k, f in futs.items():
            try:
                out[k] = f.result(timeout=25)
            except Exception as e:
                out[k] = {'error': f'응답 지연 또는 실패: {str(e)[:160]}'}
    finally:
        ex.shutdown(wait=False)  # 느린 소스 하나가 전체 응답을 막지 않도록
    out['market'] = market_status()
    out['updated_at'] = datetime.now().strftime('%H:%M:%S')
    return out
