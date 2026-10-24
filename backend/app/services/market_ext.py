"""
실적·수급·공매도 데이터 (2단계 분석 보강)

quarterly_financials(ticker)
  1순위 OpenDART '단일회사 주요계정'(DART_API_KEY 가 있을 때) — 공식 공시 원천
  2순위 네이버 금융 분기·연간 재무 (매출액·영업이익·순이익·영업이익률, 컨센서스 분기 포함)
  단위: 억원

investor_flows(ticker)
  1순위 KRX(pykrx, 투자자별 거래대금 — 공식) · 2순위 네이버 투자자별 매매동향(수량 × 종가로 금액 근사)
  외국인·기관·개인 5·20거래일 순매수, 외국인 보유 비중 변화

short_balance(ticker)
  KRX(pykrx) 공매도 잔고·비중 (없으면 None)

모든 함수는 실패해도 예외 대신 None 을 돌려주고, 호출하는 쪽에서 시간 한도를 둡니다.
"""

import io
import json
import os
import re
import time
import zipfile
import concurrent.futures as cf
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.logger import get_logger
from . import market_data as md

logger = get_logger('mirofish.market_ext')
DART = 'https://opendart.fss.or.kr/api'


def _num(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(',', '').replace('%', '').replace('+', '').strip()
    if s in ('', '-', 'N/A', 'null'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return round((a / b - 1) * 100 * (1 if b > 0 else -1), 1)


def _timeboxed(fn, *a, timeout: float = 15.0):
    ex = cf.ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn, *a).result(timeout=timeout)
    finally:
        ex.shutdown(wait=False)


# ─────────────────────────────────────────────────────────── 분기 실적
def _summarize_quarters(quarters: List[Dict[str, Any]], annual: List[Dict[str, Any]], source: str) -> Dict[str, Any]:
    actual = [q for q in quarters if not q.get('consensus')]
    est = [q for q in quarters if q.get('consensus')]
    out: Dict[str, Any] = {'source': source, 'unit': '억원', 'quarters': quarters, 'annual': annual}
    if not actual:
        return out
    last = actual[-1]
    out['latest'] = last
    # 전년 동기 (4분기 전), 직전 분기
    prev_y = next((q for q in actual if q['period'][:4] == str(int(last['period'][:4]) - 1) and q['period'][5:7] == last['period'][5:7]), None)
    prev_q = actual[-2] if len(actual) >= 2 else None
    for k, lbl in (('revenue', 'revenue'), ('op_income', 'op_income'), ('net_income', 'net_income')):
        out[f'{lbl}_yoy_pct'] = _pct(last.get(k), (prev_y or {}).get(k)) if prev_y else None
        out[f'{lbl}_qoq_pct'] = _pct(last.get(k), (prev_q or {}).get(k)) if prev_q else None
    out['yoy_base'] = prev_y['period'] if prev_y else None
    out['qoq_base'] = prev_q['period'] if prev_q else None
    last4 = actual[-4:]
    if len(last4) == 4:
        out['ttm'] = {k: round(sum((q.get(k) or 0) for q in last4), 1) if all(q.get(k) is not None for q in last4) else None
                      for k in ('revenue', 'op_income', 'net_income')}
        out['ttm_periods'] = f"{last4[0]['period']}~{last4[-1]['period']}"
    if est:
        out['next_estimate'] = est[0]
    # 연속 흑자·적자 판단
    ops = [q.get('op_income') for q in actual if q.get('op_income') is not None]
    out['op_loss_quarters'] = sum(1 for x in ops if x < 0)
    return out


def _naver_finance(ticker: str) -> Optional[Dict[str, Any]]:
    def grab(kind):
        d = md._get(f'{md.NAVER_M}/stock/{ticker}/finance/{kind}').json()
        info = (d or {}).get('financeInfo') or {}
        titles = info.get('trTitleList') or []
        rows = {r.get('title'): r.get('columns') or {} for r in info.get('rowList') or []}
        out = []
        for t in titles:
            key = t.get('key')
            period = str(t.get('title') or key).strip().rstrip('.')
            cell = lambda name: _num(((rows.get(name) or {}).get(key) or {}).get('value'))  # noqa: E731
            rev, op, ni = cell('매출액'), cell('영업이익'), cell('당기순이익')
            if rev is None and op is None and ni is None:
                continue
            out.append({'period': period, 'revenue': rev, 'op_income': op, 'net_income': ni,
                        'op_margin': cell('영업이익률'), 'roe': cell('ROE'), 'eps': cell('EPS'),
                        'debt_ratio': cell('부채비율'), 'consensus': t.get('isConsensus') == 'Y'})
        return out

    quarters = grab('quarter')
    if not quarters:
        return None
    try:
        annual = grab('annual')
    except Exception:
        annual = []
    return _summarize_quarters(quarters, annual, 'naver')


def _dart_corp_code(ticker: str, key: str) -> Optional[str]:
    cache = os.path.join(Config.UPLOAD_FOLDER, 'cache', 'dart_corpcode.json')
    data = None
    try:
        if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 30 * 86400:
            with open(cache, 'r', encoding='utf-8') as f:
                data = json.load(f)
    except Exception:
        data = None
    if data is None:
        r = md.requests.get(f'{DART}/corpCode.xml', params={'crtfc_key': key}, timeout=30)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        xml = z.read(z.namelist()[0]).decode('utf-8', errors='ignore')
        data = {}
        for m in re.finditer(r'<list>\s*<corp_code>(\d+)</corp_code>.*?<stock_code>\s*(\d{6})?\s*</stock_code>', xml, re.S):
            if m.group(2):
                data[m.group(2)] = m.group(1)
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    return data.get(ticker)


def _dart_finance(ticker: str, key: str) -> Optional[Dict[str, Any]]:
    corp = _dart_corp_code(ticker, key)
    if not corp:
        return None
    reports = []           # (year, reprt_code, label_month)
    now = datetime.now()
    for y in (now.year - 2, now.year - 1, now.year):
        for code, mm in (('11013', '03'), ('11012', '06'), ('11014', '09'), ('11011', '12')):
            reports.append((y, code, mm))
    cum: Dict[str, Dict[str, Optional[float]]] = {}
    quarters = []
    for y, code, mm in reports:
        if datetime(y, int(mm), 28) > now:
            continue
        try:
            r = md.requests.get(f'{DART}/fnlttSinglAcnt.json', timeout=12,
                                params={'crtfc_key': key, 'corp_code': corp, 'bsns_year': str(y), 'reprt_code': code})
            j = r.json()
        except Exception:
            continue
        if j.get('status') != '000':
            continue
        items = j.get('list') or []
        fs = 'CFS' if any(i.get('fs_div') == 'CFS' for i in items) else 'OFS'
        vals = {}
        for i in items:
            if i.get('fs_div') != fs:
                continue
            name = (i.get('account_nm') or '').replace(' ', '')
            k = 'revenue' if name in ('매출액', '수익(매출액)', '영업수익') else 'op_income' if name == '영업이익' else \
                'net_income' if name in ('당기순이익', '당기순이익(손실)') else None
            if not k or k in vals:
                continue
            if code == '11011':          # 사업보고서: 연간 → 4분기 = 연간 − 1~3분기 누적
                annual = _num(i.get('thstrm_amount'))
                q3 = cum.get(f'{y}-09', {}).get(k)
                vals[k] = (annual - q3) if (annual is not None and q3 is not None) else None
                cum.setdefault(f'{y}-12', {})[k] = annual
            else:
                q_amt = _num(i.get('thstrm_amount'))
                add_amt = _num(i.get('thstrm_add_amount'))
                vals[k] = q_amt
                cum.setdefault(f'{y}-{mm}', {})[k] = add_amt if add_amt is not None else q_amt
        if vals:
            to_eok = lambda v: None if v is None else round(v / 1e8, 1)  # noqa: E731
            q = {'period': f'{y}.{mm}', 'revenue': to_eok(vals.get('revenue')), 'op_income': to_eok(vals.get('op_income')),
                 'net_income': to_eok(vals.get('net_income')), 'consensus': False}
            q['op_margin'] = round(q['op_income'] / q['revenue'] * 100, 2) if q['revenue'] and q['op_income'] is not None else None
            quarters.append(q)
    quarters = [q for q in quarters if q['revenue'] is not None or q['op_income'] is not None][-6:]
    if not quarters:
        return None
    return _summarize_quarters(quarters, [], 'dart')


def quarterly_financials(ticker: str) -> Optional[Dict[str, Any]]:
    key = (os.environ.get('DART_API_KEY') or '').strip()
    if key:
        try:
            d = _dart_finance(ticker, key)
            if d:
                return d
        except Exception as e:
            logger.warning(f"[{ticker}] DART 재무 실패 → 네이버 금융으로 대체: {e}")
    try:
        return _naver_finance(ticker)
    except Exception as e:
        logger.warning(f"[{ticker}] 분기 실적 수집 실패: {e}")
        return None


# ─────────────────────────────────────────────────────────── 투자자별 수급
def _flows_summary(rows: List[Dict[str, Any]], source: str, value_unit: str) -> Dict[str, Any]:
    rows = sorted(rows, key=lambda r: r['date'])
    out: Dict[str, Any] = {'source': source, 'value_unit': value_unit, 'rows': rows[-20:]}
    for n in (5, 20):
        part = rows[-n:]
        if len(part) < min(n, 3):
            continue
        s = {}
        for who in ('foreign', 'organ', 'individual'):
            vals = [r.get(f'{who}_value') for r in part if r.get(f'{who}_value') is not None]
            s[who] = round(sum(vals), 1) if vals else None
            q = [r.get(f'{who}_qty') for r in part if r.get(f'{who}_qty') is not None]
            s[f'{who}_qty'] = int(sum(q)) if q else None
        s['days'] = len(part)
        out[f'sum{n}'] = s
    fr = [r.get('foreign_ratio') for r in rows if r.get('foreign_ratio') is not None]
    if fr:
        out['foreign_ratio'] = fr[-1]
        if len(fr) >= 2:
            out['foreign_ratio_chg'] = round(fr[-1] - fr[max(0, len(fr) - 20)], 2)
    # 연속 순매수·순매도 일수
    for who in ('foreign', 'organ'):
        streak = 0
        for r in reversed(rows):
            v = r.get(f'{who}_value') if r.get(f'{who}_value') is not None else r.get(f'{who}_qty')
            if v is None or v == 0:
                break
            if streak == 0 or (streak > 0) == (v > 0):
                streak += 1 if v > 0 else -1
            else:
                break
        out[f'{who}_streak'] = streak
    return out


def _naver_flows(ticker: str) -> Optional[Dict[str, Any]]:
    d = md._get(f'{md.NAVER_M}/stock/{ticker}/trend?pageSize=30').json()
    if not isinstance(d, list) or not d:
        return None
    rows = []
    for x in d:
        close = _num(x.get('closePrice'))
        fq, oq, iq = _num(x.get('foreignerPureBuyQuant')), _num(x.get('organPureBuyQuant')), _num(x.get('individualPureBuyQuant'))
        to_eok = lambda q: None if (q is None or close is None) else round(q * close / 1e8, 2)  # noqa: E731
        date = str(x.get('bizdate') or '')
        rows.append({'date': f'{date[:4]}-{date[4:6]}-{date[6:8]}' if len(date) == 8 else date, 'close': close,
                     'foreign_qty': fq, 'organ_qty': oq, 'individual_qty': iq,
                     'foreign_value': to_eok(fq), 'organ_value': to_eok(oq), 'individual_value': to_eok(iq),
                     'foreign_ratio': _num(x.get('foreignerHoldRatio'))})
    return _flows_summary(rows, 'naver', '억원(수량×종가 근사)')


def _krx_flows(ticker: str, krx) -> Optional[Dict[str, Any]]:
    end = datetime.now()
    start = end - timedelta(days=45)
    df = krx.get_market_trading_value_by_date(start.strftime('%Y%m%d'), end.strftime('%Y%m%d'), ticker)
    if df is None or df.empty:
        return None
    rows = []
    for idx, r in df.iterrows():
        g = lambda *names: next((float(r[n]) for n in names if n in r.index), None)  # noqa: E731
        f, o, i = g('외국인합계', '외국인'), g('기관합계', '기관'), g('개인')
        rows.append({'date': idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx)[:10],
                     'foreign_value': round(f / 1e8, 2) if f is not None else None,
                     'organ_value': round(o / 1e8, 2) if o is not None else None,
                     'individual_value': round(i / 1e8, 2) if i is not None else None})
    return _flows_summary(rows, 'krx', '억원')


def investor_flows(ticker: str, krx=None) -> Optional[Dict[str, Any]]:
    src = (os.environ.get('FLOW_SOURCE') or 'auto').strip().lower()
    out = None
    if krx is not None and src in ('auto', 'krx'):
        try:
            out = _timeboxed(_krx_flows, ticker, krx, timeout=15)
        except Exception as e:
            logger.info(f"[{ticker}] KRX 수급 조회 실패 → 네이버로 대체: {type(e).__name__}")
    if not out and src in ('auto', 'naver'):
        try:
            out = _naver_flows(ticker)
        except Exception as e:
            logger.warning(f"[{ticker}] 수급 조회 실패: {e}")
    # 네이버 외국인 보유 비중은 KRX 결과에 없으므로 보완
    if out and out.get('source') == 'krx' and out.get('foreign_ratio') is None and src == 'auto':
        try:
            nv = _naver_flows(ticker)
            if nv:
                out['foreign_ratio'], out['foreign_ratio_chg'] = nv.get('foreign_ratio'), nv.get('foreign_ratio_chg')
        except Exception:
            pass
    return out


# ─────────────────────────────────────────────────────────── 공매도
def short_balance(ticker: str, krx=None) -> Optional[Dict[str, Any]]:
    if krx is None:
        return None

    def run():
        end = datetime.now()
        start = end - timedelta(days=50)
        df = krx.get_shorting_balance_by_date(start.strftime('%Y%m%d'), end.strftime('%Y%m%d'), ticker)
        if df is None or df.empty:
            return None
        rows = []
        for idx, r in df.iterrows():
            bal = _num(r.get('공매도잔고'))
            ratio = _num(r.get('비중'))
            amt = _num(r.get('공매도금액'))
            rows.append({'date': idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx)[:10],
                         'balance': bal, 'ratio_pct': ratio, 'amount_eok': round(amt / 1e8, 1) if amt else None})
        rows = [r for r in rows if r['ratio_pct'] is not None]
        if not rows:
            return None
        last, first = rows[-1], rows[max(0, len(rows) - 20)]
        return {'source': 'krx', 'rows': rows[-20:], 'date': last['date'], 'ratio_pct': last['ratio_pct'],
                'balance': last['balance'], 'amount_eok': last['amount_eok'],
                'ratio_chg_pp': round(last['ratio_pct'] - first['ratio_pct'], 3) if first['ratio_pct'] is not None else None}
    try:
        return _timeboxed(run, timeout=15)
    except Exception as e:
        logger.info(f"[{ticker}] 공매도 잔고 조회 실패(생략): {type(e).__name__}: {str(e)[:120]}")
        return None


def collect(ticker: str, krx=None, timeout: float = 25.0) -> Dict[str, Any]:
    """세 가지를 동시에 수집 (전체 최대 timeout 초)"""
    ex = cf.ThreadPoolExecutor(max_workers=3)
    futs = {'quarterly': ex.submit(quarterly_financials, ticker),
            'flows': ex.submit(investor_flows, ticker, krx),
            'short': ex.submit(short_balance, ticker, krx)}
    ex.shutdown(wait=False)
    out: Dict[str, Any] = {}
    t_end = time.time() + timeout
    for k, f in futs.items():
        try:
            out[k] = f.result(timeout=max(0.5, t_end - time.time()))
        except Exception as e:
            logger.info(f"[{ticker}] {k} 수집 시간 초과·실패: {type(e).__name__}")
            out[k] = None
    return out


# ─────────────────────────────────────────────────────────── 사실표 문장
def _eok(v: Optional[float]) -> str:
    if v is None:
        return '-'
    if abs(v) >= 10000:
        return f"{v / 10000:,.2f}조원"
    return f"{v:,.0f}억원"


def _sg(v: Optional[float], unit: str = '%') -> str:
    return '-' if v is None else f"{v:+.1f}{unit}"


def fact_lines(ext: Dict[str, Any]) -> List[Dict[str, str]]:
    """[{'kind': 'earnings'|'flow'|'short', 'text': ...}] — 사실표(F#)에 들어갈 문장"""
    out = []
    q = (ext or {}).get('quarterly') or {}
    if q.get('latest'):
        L = q['latest']
        src = 'OpenDART' if q.get('source') == 'dart' else '네이버 금융'
        t = (f"분기 실적({src}) {L['period']}: 매출 {_eok(L.get('revenue'))} · 영업이익 {_eok(L.get('op_income'))}"
             + (f" (영업이익률 {L['op_margin']:.1f}%)" if L.get('op_margin') is not None else '')
             + f" · 순이익 {_eok(L.get('net_income'))}")
        if q.get('yoy_base'):
            t += f" · 전년 동기({q['yoy_base']}) 대비 매출 {_sg(q.get('revenue_yoy_pct'))}, 영업이익 {_sg(q.get('op_income_yoy_pct'))}"
        if q.get('qoq_base'):
            t += f" · 직전 분기 대비 매출 {_sg(q.get('revenue_qoq_pct'))}, 영업이익 {_sg(q.get('op_income_qoq_pct'))}"
        out.append({'kind': 'earnings', 'text': t, 'role': 'qfin'})
        if q.get('ttm'):
            tt = q['ttm']
            out.append({'kind': 'earnings', 'role': 'qfin_ttm',
                        'text': f"최근 4분기 합계({q['ttm_periods']}): 매출 {_eok(tt.get('revenue'))} · 영업이익 {_eok(tt.get('op_income'))} · 순이익 {_eok(tt.get('net_income'))}"})
        ne = q.get('next_estimate')
        if ne:
            out.append({'kind': 'earnings', 'role': 'qfin_est',
                        'text': f"{ne['period']} 분기 컨센서스 추정: 매출 {_eok(ne.get('revenue'))} · 영업이익 {_eok(ne.get('op_income'))}"})
    fl = (ext or {}).get('flows') or {}
    s5, s20 = fl.get('sum5') or {}, fl.get('sum20') or {}
    if s20 or s5:
        src = 'KRX' if fl.get('source') == 'krx' else '네이버'
        parts = []
        for lbl, s in (('5일', s5), (f"{s20.get('days', 20)}일", s20)):
            if s:
                parts.append(f"{lbl} 외국인 {_sg(s.get('foreign'), '억원')} · 기관 {_sg(s.get('organ'), '억원')} · 개인 {_sg(s.get('individual'), '억원')}")
        t = f"투자자별 순매수({src}): " + ' / '.join(parts)
        if fl.get('foreign_ratio') is not None:
            t += f" · 외국인 보유 비중 {fl['foreign_ratio']:.2f}%" + (f" ({fl['foreign_ratio_chg']:+.2f}%p)" if fl.get('foreign_ratio_chg') is not None else '')
        if fl.get('foreign_streak'):
            t += f" · 외국인 {abs(fl['foreign_streak'])}일 연속 순{'매수' if fl['foreign_streak'] > 0 else '매도'}"
        out.append({'kind': 'flow', 'text': t, 'role': 'flows'})
    sh = (ext or {}).get('short') or {}
    if sh.get('ratio_pct') is not None:
        out.append({'kind': 'short', 'role': 'short',
                    'text': f"공매도 잔고(KRX, {sh.get('date')}): 비중 {sh['ratio_pct']:.2f}%"
                            + (f" · 20일 변화 {sh['ratio_chg_pp']:+.2f}%p" if sh.get('ratio_chg_pp') is not None else '')
                            + (f" · 잔고 금액 {_eok(sh.get('amount_eok'))}" if sh.get('amount_eok') else '')})
    return out
