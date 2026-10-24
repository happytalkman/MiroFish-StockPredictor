"""
조건검색 스크리너 (키움 영웅문 '조건검색' 방식 차용)

- 조건식을 알파벳(A, B, C …)으로 만들고 "A and B and (C or D)" 같은 논리식으로 결합
- 1차: 시세 스냅샷 조건(시총·가격·등락률·거래대금)으로 유니버스를 좁히고
- 2차: 후보 종목의 일봉 130개로 기술적 조건(정배열·골든크로스·RSI·신고가·볼린저·MACD·거래량 급증·연속양봉) 계산
- 3차: 필요 시 PER·PBR 등 재무 조건
- 결과마다 충족 신호·점수를 붙여 추천 순위를 매김
"""

import re
import math
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from ..utils.logger import get_logger
from . import market_data as md

logger = get_logger('mirofish.screener')

# ─────────────────────────────────────────────── 조건 정의 (UI 가 그대로 렌더링)
CONDITIONS: Dict[str, Dict[str, Any]] = {
    'mcap': {'group': '시세', 'label': '시가총액', 'unit': '억원', 'stage': 1,
             'params': {'min': 5000, 'max': None}, 'desc': '시가총액이 범위 안'},
    'price': {'group': '시세', 'label': '주가 범위', 'unit': '원', 'stage': 1,
              'params': {'min': 1000, 'max': None}, 'desc': '현재가가 범위 안'},
    'change': {'group': '시세', 'label': '당일 등락률', 'unit': '%', 'stage': 1,
               'params': {'min': -3, 'max': 15}, 'desc': '전일 대비 등락률 범위'},
    'value': {'group': '시세', 'label': '거래대금 이상', 'unit': '억원', 'stage': 1,
              'params': {'min': 50}, 'desc': '당일 거래대금 하한'},
    'vol_surge': {'group': '거래량', 'label': '거래량 급증', 'unit': '배', 'stage': 2,
                  'params': {'ratio': 2.0, 'avg': 20}, 'desc': '당일 거래량 ≥ N일 평균 × 배수'},
    'ma_align': {'group': '이동평균', 'label': '이동평균 정배열', 'stage': 2,
                 'params': {'short': 5, 'mid': 20, 'long': 60}, 'desc': '단기 > 중기 > 장기 이평선'},
    'golden_cross': {'group': '이동평균', 'label': '골든크로스', 'stage': 2,
                     'params': {'short': 5, 'long': 20, 'within': 3}, 'desc': '단기선이 장기선을 N일 이내 상향 돌파'},
    'above_ma': {'group': '이동평균', 'label': '이평선 위', 'stage': 2,
                 'params': {'ma': 20}, 'desc': '종가가 N일 이동평균 위'},
    'pullback': {'group': '이동평균', 'label': '눌림목 (이평선 근접)', 'unit': '%', 'stage': 2,
                 'params': {'ma': 20, 'gap': 3}, 'desc': '상승추세 중 종가가 N일선 ±gap% 이내'},
    'rsi': {'group': '보조지표', 'label': 'RSI(14) 범위', 'stage': 2,
            'params': {'min': 30, 'max': 70}, 'desc': 'RSI 가 범위 안'},
    'rsi_rebound': {'group': '보조지표', 'label': 'RSI 과매도 반등', 'stage': 2,
                    'params': {'level': 30, 'within': 5}, 'desc': 'N일 이내 RSI 가 기준선 아래였다가 상향 복귀'},
    'macd_cross': {'group': '보조지표', 'label': 'MACD 매수 신호', 'stage': 2,
                   'params': {'within': 3}, 'desc': 'MACD 가 시그널선을 N일 이내 상향 돌파'},
    'bb_break': {'group': '보조지표', 'label': '볼린저밴드 상단 돌파', 'stage': 2,
                 'params': {'period': 20, 'k': 2}, 'desc': '종가가 볼린저 상단 위'},
    'bb_lower': {'group': '보조지표', 'label': '볼린저밴드 하단 반등', 'stage': 2,
                 'params': {'period': 20, 'k': 2, 'within': 3}, 'desc': 'N일 이내 하단 이탈 후 밴드 안으로 복귀'},
    'new_high': {'group': '가격패턴', 'label': '신고가 근접', 'unit': '%', 'stage': 2,
                 'params': {'days': 120, 'gap': 3}, 'desc': '종가가 N일 최고가의 gap% 이내'},
    'up_candles': {'group': '가격패턴', 'label': '연속 양봉', 'unit': '일', 'stage': 2,
                   'params': {'count': 3}, 'desc': 'N일 연속 종가 > 시가'},
    'momentum': {'group': '가격패턴', 'label': 'N일 수익률', 'unit': '%', 'stage': 2,
                 'params': {'days': 20, 'min': 5, 'max': None}, 'desc': 'N일 수익률 범위'},
    'per': {'group': '재무', 'label': 'PER 범위', 'unit': '배', 'stage': 3,
            'params': {'min': 0.1, 'max': 15}, 'desc': 'PER 범위 (적자 제외)'},
    'pbr': {'group': '재무', 'label': 'PBR 범위', 'unit': '배', 'stage': 3,
            'params': {'min': 0.1, 'max': 1.5}, 'desc': 'PBR 범위'},
}

# 신호 가중치 (추천 점수)
WEIGHTS = {'ma_align': 14, 'golden_cross': 16, 'vol_surge': 12, 'macd_cross': 12, 'new_high': 12, 'rsi_rebound': 10,
           'bb_lower': 8, 'bb_break': 8, 'pullback': 10, 'above_ma': 6, 'up_candles': 6, 'momentum': 8,
           'per': 6, 'pbr': 6}

PRESETS = [
    {'id': 'trend_volume', 'name': '정배열 + 거래량 급증', 'desc': '추세가 살아 있는 종목에 수급이 붙는 순간',
     'conditions': {'A': ('mcap', {'min': 3000}), 'B': ('value', {'min': 100}), 'C': ('ma_align', {}),
                    'D': ('vol_surge', {'ratio': 1.8, 'avg': 20}), 'E': ('change', {'min': 0, 'max': 15})},
     'expr': 'A and B and C and D and E'},
    {'id': 'golden', 'name': '골든크로스 초기 (5·20)', 'desc': '단기 추세 전환 초입 포착',
     'conditions': {'A': ('mcap', {'min': 3000}), 'B': ('golden_cross', {'short': 5, 'long': 20, 'within': 3}),
                    'C': ('above_ma', {'ma': 60}), 'D': ('rsi', {'min': 40, 'max': 70})},
     'expr': 'A and B and (C or D)'},
    {'id': 'new_high', 'name': '신고가 돌파 임박', 'desc': '120일 최고가 3% 이내 + 정배열',
     'conditions': {'A': ('mcap', {'min': 5000}), 'B': ('new_high', {'days': 120, 'gap': 3}), 'C': ('ma_align', {}),
                    'D': ('value', {'min': 100})},
     'expr': 'A and B and C and D'},
    {'id': 'rsi_rebound', 'name': 'RSI 과매도 반등', 'desc': '과매도 구간 탈출 초입',
     'conditions': {'A': ('mcap', {'min': 5000}), 'B': ('rsi_rebound', {'level': 30, 'within': 5}),
                    'C': ('bb_lower', {'within': 5})},
     'expr': 'A and (B or C)'},
    {'id': 'macd', 'name': 'MACD 매수 전환', 'desc': 'MACD 골든크로스 + 20일선 위',
     'conditions': {'A': ('mcap', {'min': 3000}), 'B': ('macd_cross', {'within': 3}), 'C': ('above_ma', {'ma': 20})},
     'expr': 'A and B and C'},
    {'id': 'pullback', 'name': '대형주 눌림목', 'desc': '정배열 대형주가 20일선까지 조정',
     'conditions': {'A': ('mcap', {'min': 20000}), 'B': ('ma_align', {'short': 20, 'mid': 60, 'long': 120}),
                    'C': ('pullback', {'ma': 20, 'gap': 3}), 'D': ('rsi', {'min': 35, 'max': 60})},
     'expr': 'A and B and C and D'},
    {'id': 'breakout', 'name': '볼린저 상단 돌파 + 연속양봉', 'desc': '변동성 확장 돌파',
     'conditions': {'A': ('mcap', {'min': 3000}), 'B': ('bb_break', {}), 'C': ('up_candles', {'count': 2}),
                    'D': ('vol_surge', {'ratio': 1.5, 'avg': 20})},
     'expr': 'A and B and C and D'},
    {'id': 'value', 'name': '저PER·저PBR + 상승추세', 'desc': '가치주 중 추세가 살아난 종목',
     'conditions': {'A': ('mcap', {'min': 3000}), 'B': ('above_ma', {'ma': 60}), 'C': ('per', {'min': 0.1, 'max': 12}),
                    'D': ('pbr', {'min': 0.1, 'max': 1.2})},
     'expr': 'A and B and C and D'},
]


# ─────────────────────────────────────────────── 지표 계산
def sma(a: List[float], n: int, end: int = None) -> Optional[float]:
    end = len(a) if end is None else end
    if end < n or n <= 0:
        return None
    return sum(a[end - n:end]) / n


def ema_series(a: List[float], n: int) -> List[float]:
    if not a:
        return []
    k = 2 / (n + 1)
    out = [a[0]]
    for x in a[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def rsi_series(c: List[float], n: int = 14) -> List[Optional[float]]:
    out = [None] * len(c)
    if len(c) <= n:
        return out
    gains = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    losses = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(c)):
        ag = (ag * (n - 1) + gains[i - 1]) / n
        al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def bollinger(c: List[float], n: int, k: float, end: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if end < n:
        return None, None, None
    w = c[end - n:end]
    m = sum(w) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
    return m - k * sd, m, m + k * sd


def indicators(rows: List[Dict[str, float]]) -> Dict[str, Any]:
    c = [r['c'] for r in rows]
    v = [r['v'] for r in rows]
    rsi = rsi_series(c)
    e12, e26 = ema_series(c, 12), ema_series(c, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ema_series(macd, 9)
    return {'c': c, 'v': v, 'o': [r['o'] for r in rows], 'h': [r['h'] for r in rows], 'rsi': rsi, 'macd': macd, 'sig': sig}


def check(cond: str, p: Dict[str, Any], snap: Dict[str, Any], ind: Optional[Dict[str, Any]], fund: Dict[str, Any]) -> Tuple[bool, str]:
    """(충족 여부, 설명 문자열)"""
    def rng(x, lo, hi):
        return x is not None and (lo is None or x >= lo) and (hi is None or x <= hi)

    if cond == 'mcap':
        x = snap.get('mcap_eok')
        return rng(x, p.get('min'), p.get('max')), f"시총 {x:,.0f}억" if x else '시총 없음'
    if cond == 'price':
        x = snap.get('price')
        return rng(x, p.get('min'), p.get('max')), f"{x:,.0f}원" if x else ''
    if cond == 'change':
        x = snap.get('pct')
        return rng(x, p.get('min'), p.get('max')), f"등락 {x:+.2f}%" if x is not None else ''
    if cond == 'value':
        x = (snap.get('value_mil') or 0) / 100  # 백만원 → 억원
        return rng(x, p.get('min'), p.get('max')), f"거래대금 {x:,.0f}억"
    if cond in ('per', 'pbr'):
        x = fund.get(cond)
        return rng(x, p.get('min'), p.get('max')), f"{cond.upper()} {x:.2f}" if x is not None else f"{cond.upper()} 없음"

    if not ind or len(ind['c']) < 30:
        return False, '차트 부족'
    c, v, n = ind['c'], ind['v'], len(ind['c'])

    if cond == 'vol_surge':
        avg = sma(v, int(p.get('avg', 20)), n - 1)
        r = v[-1] / avg if avg else 0
        return r >= float(p.get('ratio', 2)), f"거래량 {r:.1f}배"
    if cond == 'ma_align':
        s, m_, l = (sma(c, int(p.get(k, d))) for k, d in (('short', 5), ('mid', 20), ('long', 60)))
        ok = None not in (s, m_, l) and s > m_ > l
        return ok, '정배열' if ok else '비정배열'
    if cond == 'golden_cross':
        s, l, w = int(p.get('short', 5)), int(p.get('long', 20)), int(p.get('within', 3))
        for back in range(0, w):
            e = n - back
            a1, b1, a0, b0 = sma(c, s, e), sma(c, l, e), sma(c, s, e - 1), sma(c, l, e - 1)
            if None not in (a1, b1, a0, b0) and a0 <= b0 and a1 > b1:
                return True, f"{s}/{l} 골든크로스 {back}일 전" if back else f"{s}/{l} 골든크로스 오늘"
        return False, ''
    if cond == 'above_ma':
        m_ = sma(c, int(p.get('ma', 20)))
        return (m_ is not None and c[-1] > m_), f"{p.get('ma', 20)}일선 {'위' if m_ and c[-1] > m_ else '아래'}"
    if cond == 'pullback':
        m_ = sma(c, int(p.get('ma', 20)))
        if not m_:
            return False, ''
        gap = (c[-1] / m_ - 1) * 100
        return abs(gap) <= float(p.get('gap', 3)), f"{p.get('ma', 20)}일선 대비 {gap:+.1f}%"
    if cond == 'rsi':
        r = ind['rsi'][-1]
        return rng(r, p.get('min'), p.get('max')), f"RSI {r:.0f}" if r is not None else ''
    if cond == 'rsi_rebound':
        lv, w = float(p.get('level', 30)), int(p.get('within', 5))
        rs = [x for x in ind['rsi'][-(w + 1):] if x is not None]
        ok = len(rs) >= 2 and min(rs[:-1]) < lv <= rs[-1]
        return ok, f"RSI {rs[-1]:.0f} (최저 {min(rs):.0f})" if rs else ''
    if cond == 'macd_cross':
        w = int(p.get('within', 3))
        m_, s_ = ind['macd'], ind['sig']
        for back in range(0, w):
            i = n - 1 - back
            if i > 0 and m_[i - 1] <= s_[i - 1] and m_[i] > s_[i]:
                return True, f"MACD 골든크로스 {back}일 전" if back else 'MACD 골든크로스 오늘'
        return False, ''
    if cond == 'bb_break':
        lo, mid, up = bollinger(c, int(p.get('period', 20)), float(p.get('k', 2)), n)
        return (up is not None and c[-1] > up), '볼린저 상단 돌파' if up and c[-1] > up else ''
    if cond == 'bb_lower':
        per, k, w = int(p.get('period', 20)), float(p.get('k', 2)), int(p.get('within', 3))
        lo_now, _, _ = bollinger(c, per, k, n)
        broke = any((lambda b: b[0] is not None and c[e - 1] < b[0])(bollinger(c, per, k, e)) for e in range(n - w, n))
        ok = broke and lo_now is not None and c[-1] >= lo_now
        return ok, '볼린저 하단 반등' if ok else ''
    if cond == 'new_high':
        d = min(int(p.get('days', 120)), n)
        hi = max(ind['h'][-d:])
        gap = (1 - c[-1] / hi) * 100 if hi else 99
        return gap <= float(p.get('gap', 3)), f"{d}일 고가 대비 -{gap:.1f}%"
    if cond == 'up_candles':
        k = int(p.get('count', 3))
        ok = all(c[-i] > ind['o'][-i] for i in range(1, k + 1))
        return ok, f"{k}연속 양봉" if ok else ''
    if cond == 'momentum':
        d = int(p.get('days', 20))
        if n <= d:
            return False, ''
        r = (c[-1] / c[-1 - d] - 1) * 100
        return rng(r, p.get('min'), p.get('max')), f"{d}일 {r:+.1f}%"
    return False, '알 수 없는 조건'


# ─────────────────────────────────────────────── 논리식 평가 (A and (B or not C))
def eval_expr(expr: str, values: Dict[str, bool]) -> bool:
    tokens = re.findall(r'\(|\)|and|or|not|[A-Z]', expr.replace('AND', 'and').replace('OR', 'or').replace('NOT', 'not'))
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        t = tokens[pos]
        pos += 1
        return t

    def p_or():
        v = p_and()
        while peek() == 'or':
            take()
            v = p_and() or v
        return v

    def p_and():
        v = p_not()
        while peek() == 'and':
            take()
            v = p_not() and v
        return v

    def p_not():
        if peek() == 'not':
            take()
            return not p_not()
        return p_atom()

    def p_atom():
        t = take()
        if t == '(':
            v = p_or()
            if peek() == ')':
                take()
            return v
        return bool(values.get(t, False))

    if not tokens:
        return all(values.values())
    return p_or()


# ─────────────────────────────────────────────── 실행
def universe(market: str, size: int) -> List[Dict[str, Any]]:
    markets = ['KOSPI', 'KOSDAQ'] if market == 'ALL' else [market]
    rows = []
    for m in markets:
        per_m = size if market != 'ALL' else max(50, size // 2)
        pages = max(1, math.ceil(per_m / 100))
        got = md.cached(f'univ:{m}:{pages}', 120, md.naver_stock_list, m, 'marketValue', 100, pages)
        rows.extend(got[:per_m])
    return rows


# ─────────────────────────────────────────────── 지정 종목 유니버스 (오늘의 테마 → 스크리너)
def normalize_codes(codes: Any, cap: int = 200) -> List[str]:
    """['005930', {'code': '000660'}, 'A035420'] → 6자리 코드 목록 (중복 제거, 최대 cap)"""
    out: List[str] = []
    for c in codes or []:
        s = str(c.get('code') or c.get('ticker') or '' if isinstance(c, dict) else c).strip().upper()
        s = re.sub(r'^A(?=[0-9A-Z]{6}$)', '', s)
        if re.fullmatch(r'[0-9A-Z]{6}', s) and s not in out:
            out.append(s)
        if len(out) >= cap:
            break
    return out


def _to_eok(v: Any) -> Optional[float]:
    """'423조 5,678억' / '5,678억' / '12,345' → 억원"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).replace(',', '').replace(' ', '')
    jo = re.search(r'(\d+(?:\.\d+)?)조', t)
    ek = re.search(r'(\d+(?:\.\d+)?)억', t)
    if jo or ek:
        return (float(jo.group(1)) * 10000 if jo else 0.0) + (float(ek.group(1)) if ek else 0.0)
    return md.num(t)


def _to_mil(v: Any) -> Optional[float]:
    """거래대금 '876,543백만' / '1조 2,345억' / '8,765억' → 백만원"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).replace(',', '').replace(' ', '')
    if '백만' in t:
        return md.num(t)
    if '조' in t or '억' in t:
        e = _to_eok(t)
        return e * 100 if e is not None else None
    return md.num(t)


def _market_of(d: Dict[str, Any]) -> Optional[str]:
    se = d.get('stockExchangeType') or {}
    code = str(se.get('code') or '').upper()
    names = ' '.join(str(x or '') for x in (se.get('name'), se.get('nameEng'), se.get('nameKor'), d.get('stockExchangeName'))).upper()
    if code == 'KQ' or 'KOSDAQ' in names or '코스닥' in names:
        return 'KOSDAQ'
    if code == 'KS' or 'KOSPI' in names or '코스피' in names:
        return 'KOSPI'
    if 'KONEX' in names or '코넥스' in names:
        return 'KONEX'
    return None


def stock_snapshot(code: str, need_totals: bool = False) -> Optional[Dict[str, Any]]:
    """시세 스냅샷(유니버스 행과 같은 모양). 네이버 모바일 basic(+integration) → 실패 시 일봉으로 보완"""
    snap: Dict[str, Any] = {'ticker': code, 'name': None, 'market': None, 'price': None, 'change': None, 'pct': None,
                            'volume': None, 'value_mil': None, 'mcap_eok': None}
    try:
        d = md._get(f'{md.NAVER_M}/stock/{code}/basic').json()
        pct = md.num(d.get('fluctuationsRatio'))
        ch = md.num(d.get('compareToPreviousClosePrice'))
        pct, ch = md._signed(pct, ch, (d.get('compareToPreviousPrice') or {}).get('code'))
        snap.update(name=d.get('stockName'), market=_market_of(d), price=md.num(d.get('closePrice')), change=ch, pct=pct)
    except Exception as e:
        logger.debug(f'basic 실패 {code}: {e}')
    if need_totals:
        try:
            d2 = md._get(f'{md.NAVER_M}/stock/{code}/integration').json()
            snap['name'] = snap['name'] or d2.get('stockName')
            for it in d2.get('totalInfos') or []:
                k = str(it.get('code') or '').lower()
                label = str(it.get('key') or '')
                val = it.get('value')
                if k == 'accumulatedtradingvolume' or label == '거래량':
                    snap['volume'] = md.num(val)
                elif k == 'accumulatedtradingvalue' or label in ('대금', '거래대금'):
                    snap['value_mil'] = _to_mil(val)
                elif k == 'marketvalue' or label in ('시총', '시가총액'):
                    snap['mcap_eok'] = _to_eok(val)
        except Exception as e:
            logger.debug(f'integration 실패 {code}: {e}')
    if snap['price'] is None or snap['volume'] is None:
        try:
            rows = md.cached(f"chart:{code}", 600, md.fchart, code, 130)
            if rows:
                c = rows[-1]['c']
                if snap['price'] is None:
                    snap['price'] = c
                    if len(rows) > 1 and rows[-2]['c']:
                        snap['change'] = c - rows[-2]['c']
                        snap['pct'] = round(snap['change'] / rows[-2]['c'] * 100, 2)
                if snap['volume'] is None:
                    snap['volume'] = rows[-1]['v']
                if snap['value_mil'] is None and rows[-1]['v']:
                    snap['value_mil'] = round(c * rows[-1]['v'] / 1e6, 1)   # 근사 (종가 × 거래량)
        except Exception as e:
            logger.debug(f'일봉 보완 실패 {code}: {e}')
    if snap['price'] is None and not snap['name']:
        return None
    snap['name'] = snap['name'] or code
    return snap


def universe_codes(codes: List[str], need_totals: bool = False) -> Tuple[List[Dict[str, Any]], List[str]]:
    """지정 종목의 스냅샷. 시총 상위 목록(캐시 공유)에서 먼저 찾고, 없으면 종목별 조회. (rows, 못 찾은 코드)"""
    index: Dict[str, Dict[str, Any]] = {}
    for m in ('KOSPI', 'KOSDAQ'):
        try:
            for r in md.cached(f'univ:{m}:2', 120, md.naver_stock_list, m, 'marketValue', 100, 2):
                index.setdefault(r['ticker'], r)
        except Exception as e:
            logger.debug(f'유니버스 목록 실패 {m}: {e}')
    missing = [c for c in codes if c not in index]
    fetched: Dict[str, Optional[Dict[str, Any]]] = {}
    if missing:
        def one(c):
            try:
                return c, md.cached(f'snap:{c}:{int(need_totals)}', 120, stock_snapshot, c, need_totals)
            except Exception:
                return c, None
        with ThreadPoolExecutor(max_workers=8) as ex:
            fetched = dict(ex.map(one, missing))
    rows, lost = [], []
    for c in codes:
        s = index.get(c) or fetched.get(c)
        if s:
            rows.append(s)
        else:
            lost.append(c)
    return rows, lost


def run(market: str = 'ALL', size: int = 200, conditions: Dict[str, Any] = None, expr: str = '',
        limit: int = 40, codes: Optional[List[Any]] = None) -> Dict[str, Any]:
    """codes 가 있으면 시장·size 대신 그 종목들만 평가 (오늘의 테마 구성 종목 검사 등)"""
    t0 = datetime.now()
    conds = {}
    for letter, spec in (conditions or {}).items():
        if isinstance(spec, (list, tuple)):
            ctype, params = spec[0], dict(spec[1] or {})
        else:
            ctype, params = spec.get('type'), dict(spec.get('params') or {})
        if ctype not in CONDITIONS:
            continue
        merged = dict(CONDITIONS[ctype]['params'])
        merged.update({k: v for k, v in params.items() if v is not None or k in ('min', 'max')})
        conds[letter.upper()[:1]] = (ctype, merged)
    if not conds:
        raise ValueError('조건을 하나 이상 선택하세요')
    expr = expr or ' and '.join(sorted(conds))

    code_list = normalize_codes(codes) if codes else []
    lost: List[str] = []
    if code_list:
        need_totals = any(t in ('mcap', 'value') for t, _ in conds.values())
        uni, lost = universe_codes(code_list, need_totals)
    else:
        uni = universe(market, size)
    stage1 = {L for L, (t, _) in conds.items() if CONDITIONS[t]['stage'] == 1}
    need_chart = any(CONDITIONS[t]['stage'] == 2 for t, _ in conds.values())
    need_fund = any(CONDITIONS[t]['stage'] == 3 for t, _ in conds.values())

    # 1차: 스냅샷 조건만 먼저 평가해 탈락 확정 종목 제거 (다른 조건은 True 로 가정해 식 평가)
    cands = []
    for s in uni:
        vals, notes = {}, {}
        for L in conds:
            if L in stage1:
                ok, note = check(conds[L][0], conds[L][1], s, None, {})
                vals[L], notes[L] = ok, note
            else:
                vals[L] = True
        if eval_expr(expr, vals):
            cands.append((s, vals, notes))
    cands = cands[:160]

    def enrich(item):
        s, vals, notes = item
        ind, fund = None, {}
        try:
            if need_chart:
                rows = md.cached(f"chart:{s['ticker']}", 600, md.fchart, s['ticker'], 130)
                ind = indicators(rows) if rows else None
            if need_fund:
                fund = md.cached(f"fund:{s['ticker']}", 1800, md.naver_fundamentals, s['ticker'])
        except Exception as e:
            notes['_err'] = str(e)[:80]
        for L, (t, p) in conds.items():
            if L not in stage1:
                vals[L], notes[L] = check(t, p, s, ind, fund)
        return s, vals, notes, ind, fund

    with ThreadPoolExecutor(max_workers=16) as ex:
        enriched = list(ex.map(enrich, cands))

    results = []
    for s, vals, notes, ind, fund in enriched:
        if not eval_expr(expr, vals):
            continue
        signals = [{'letter': L, 'type': conds[L][0], 'label': CONDITIONS[conds[L][0]]['label'], 'ok': vals[L], 'note': notes.get(L, '')}
                   for L in sorted(conds)]
        score = sum(WEIGHTS.get(sig['type'], 3) for sig in signals if sig['ok'])
        extra = {}
        if ind:
            c = ind['c']
            extra = {'rsi': round(ind['rsi'][-1], 1) if ind['rsi'][-1] is not None else None,
                     'ret20': round((c[-1] / c[-21] - 1) * 100, 2) if len(c) > 21 else None,
                     'spark': [round(x) for x in c[-40:]]}
            # 추세 가점
            s20, s60 = sma(c, 20), sma(c, 60)
            if s20 and s60 and c[-1] > s20 > s60:
                score += 6
        results.append({**s, **extra, **{k: fund.get(k) for k in ('per', 'pbr')}, 'signals': signals, 'score': score})
    results.sort(key=lambda r: (-r['score'], -(r.get('mcap_eok') or 0)))
    scope = {'scope': 'codes', 'codes_requested': len(code_list), 'codes_missing': lost} if code_list else {'scope': 'market'}
    return {**scope, 'market': market, 'universe': len(uni), 'candidates': len(cands), 'matched': len(results),
            'expr': expr, 'conditions': {L: {'type': t, 'label': CONDITIONS[t]['label'], 'params': p} for L, (t, p) in conds.items()},
            'results': results[:limit], 'elapsed_sec': round((datetime.now() - t0).total_seconds(), 1),
            'as_of': datetime.now().strftime('%Y-%m-%d %H:%M')}


def recommend(market: str = 'ALL', size: int = 150, top: int = 12) -> Dict[str, Any]:
    """모든 프리셋을 돌려 여러 조건에 동시에 걸리는 종목을 추천"""
    agg: Dict[str, Dict[str, Any]] = {}
    ran = []
    for p in PRESETS:
        if p['id'] == 'value':
            continue  # 재무 호출이 많아 추천 합산에서는 제외
        try:
            r = run(market, size, p['conditions'], p['expr'], limit=30)
        except Exception as e:
            ran.append({'id': p['id'], 'name': p['name'], 'error': str(e)[:120]})
            continue
        ran.append({'id': p['id'], 'name': p['name'], 'matched': r['matched']})
        for x in r['results']:
            a = agg.setdefault(x['ticker'], {**{k: x.get(k) for k in ('ticker', 'name', 'market', 'price', 'pct', 'mcap_eok', 'rsi', 'ret20', 'spark')},
                                            'presets': [], 'score': 0, 'tags': set()})
            a['presets'].append(p['name'])
            a['score'] += x['score'] + 10
            for sig in x['signals']:
                if sig['ok'] and CONDITIONS[sig['type']]['stage'] == 2:
                    a['tags'].add(sig['label'])
    recs = sorted(agg.values(), key=lambda a: (-len(a['presets']), -a['score']))[:top]
    for a in recs:
        a['tags'] = sorted(a['tags'])
        a['reason'] = f"{len(a['presets'])}개 전략 동시 포착: " + ', '.join(a['presets'][:3])
    return {'as_of': datetime.now().strftime('%Y-%m-%d %H:%M'), 'presets': ran, 'items': recs}


def definitions() -> Dict[str, Any]:
    return {
        'conditions': [{'type': k, **{kk: vv for kk, vv in v.items()}} for k, v in CONDITIONS.items()],
        'presets': [{'id': p['id'], 'name': p['name'], 'desc': p['desc'], 'expr': p['expr'],
                     'conditions': {L: {'type': t, 'params': prm} for L, (t, prm) in p['conditions'].items()}}
                    for p in PRESETS],
    }
