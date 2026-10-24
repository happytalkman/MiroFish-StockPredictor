"""
예측 원장 · 자동 채점 · 확률 보정

판단(5단계)이 끝날 때마다 엔진별 방향 확률·전략·매매계획을 SQLite 원장에 기록하고,
기준일로부터 20거래일(방향)·계획 기간(매매계획)이 지나면 실제 주가로 채점합니다.

엔진 (방향 확률 3분류: up ≥ +3% · flat · down ≤ −3%, 20거래일 종가 기준 — JEV 질문과 같은 기준)
  jev        TypeSafe JEV 보정 확률
  grok       Grok 매매계획의 방향 확률
  committee  투자위원회 강세·약세 점수 환산
  agents     2단계 에이전트 투표 (매수·보합·매도 비율)
  mc         몬테카를로 주가 경로 (있을 때)

채점 지표
  - 적중(최대 확률 클래스 = 실제), 다중 분류 Brier, 로그 손실
  - 매매계획: 목표가·손절가 중 먼저 닿은 쪽(같은 날이면 손절 우선), 손익비(R), 기간 말 수익률
  - 전략 방향 적중(매수·분할매수=상승, 관망=보합, 비중축소·매도=하락)

보정
  - 엔진별 온도(T) 스케일링: 채점 12건 이상이면 로그 손실을 최소화하는 T(0.5~4) 학습, 그 전에는 균등 분포 5% 혼합(과신 완화)
  - 엔진 가중치: Brier 기술점수(균등 예측 대비 개선) 비례, 데이터 부족 시 사전 가중치
  - 보정 확률 = Σ 가중치 × 엔진별 보정 확률
"""

import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.ledger')

KST = timezone(timedelta(hours=9))
CLASSES = ('up', 'flat', 'down')
LABEL_KO = {'up': '상승', 'flat': '보합', 'down': '하락'}
KO_TO_KEY = {'상승': 'up', '보합': 'flat', '하락': 'down', 'up': 'up', 'flat': 'flat', 'down': 'down'}
STRATEGY_KO = {'buy': '매수', 'accumulate': '분할매수', 'hold': '관망', 'reduce': '비중축소', 'sell': '매도'}
STRATEGY_SIDE = {'buy': 'up', 'accumulate': 'up', 'hold': 'flat', 'reduce': 'down', 'sell': 'down',
                 '매수': 'up', '분할매수': 'up', '관망': 'flat', '비중축소': 'down', '매도': 'down'}
ENGINES = ('jev', 'grok', 'committee', 'agents', 'mc')
ENGINE_KO = {'jev': 'JEV', 'grok': 'Grok 계획', 'committee': '투자위원회', 'agents': '에이전트 투표',
             'mc': '몬테카를로', 'blend': '보정 합성'}
PRIOR_WEIGHTS = {'jev': 0.35, 'mc': 0.20, 'grok': 0.15, 'committee': 0.15, 'agents': 0.15}
UP_TH, DOWN_TH = 3.0, -3.0          # %
DIR_HORIZON = 20                    # 거래일
MIN_FIT = 12                        # 보정 학습 최소 채점 건수
UNIFORM_BRIER = 2.0 / 3.0           # 3분류 균등 예측의 Brier (기준선)
SMOOTH_EPS = 0.05                   # 데이터 부족 시 균등 분포 혼합 비율

_DB_LOCK = threading.RLock()
_PARAMS: Dict[str, Any] = {'at': 0.0, 'data': None}


def _db_path() -> str:
    d = os.path.join(Config.UPLOAD_FOLDER, 'ledger')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'ledger.db')


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path(), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('''CREATE TABLE IF NOT EXISTS predictions (
        id TEXT PRIMARY KEY, ticker TEXT, company TEXT, created_at TEXT, as_of TEXT,
        base_price REAL, dir_horizon INTEGER, plan_horizon INTEGER,
        rating TEXT, report_target REAL, strategy TEXT, strategy_probs TEXT,
        committee_strategy TEXT, grok_strategy TEXT,
        plan TEXT, sources TEXT, extra TEXT,
        status TEXT DEFAULT 'pending', scored_at TEXT, outcome TEXT)''')
    c.execute('CREATE INDEX IF NOT EXISTS ix_pred_ticker ON predictions(ticker)')
    c.execute('CREATE INDEX IF NOT EXISTS ix_pred_status ON predictions(status)')
    c.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
    return c


def _meta_get(c, key: str, default=None):
    r = c.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return json.loads(r['value']) if r else default


def _meta_set(c, key: str, value) -> None:
    c.execute('INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)', (key, json.dumps(value, ensure_ascii=False)))


# ─────────────────────────────────────────────────────────── 확률 도우미
def _norm3(p: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """{'상승': 66, '보합': 30, '하락': 4} 또는 {'up': .66, ...} → 합 1 인 {up, flat, down}"""
    if not isinstance(p, dict):
        return None
    vals = {k: 0.0 for k in CLASSES}
    for k, v in p.items():
        kk = KO_TO_KEY.get(str(k).strip())
        if kk is None:
            continue
        try:
            vals[kk] += max(0.0, float(v))
        except (TypeError, ValueError):
            continue
    s = sum(vals.values())
    if s <= 0:
        return None
    return {k: v / s for k, v in vals.items()}


def _smooth(p: Dict[str, float], eps: float) -> Dict[str, float]:
    return {k: (1 - eps) * p[k] + eps / 3.0 for k in CLASSES}


def _temper(p: Dict[str, float], T: float) -> Dict[str, float]:
    """온도 스케일링: p_i^(1/T) 정규화 (T>1 이면 과신 완화)"""
    q = {k: max(1e-4, p[k]) ** (1.0 / T) for k in CLASSES}
    s = sum(q.values())
    return {k: v / s for k, v in q.items()}


def _brier(p: Dict[str, float], y: str) -> float:
    return sum((p[k] - (1.0 if k == y else 0.0)) ** 2 for k in CLASSES)


def _logloss(p: Dict[str, float], y: str) -> float:
    return -math.log(max(1e-4, p[y]))


def _argmax(p: Dict[str, float]) -> str:
    return max(CLASSES, key=lambda k: p[k])


def realized_class(ret_pct: float) -> str:
    return 'up' if ret_pct >= UP_TH else ('down' if ret_pct <= DOWN_TH else 'flat')


# ─────────────────────────────────────────────────────────── 기록
def sources_from_decision(decision: Dict[str, Any], votes: Optional[Dict[str, Any]] = None,
                          mc: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, float]]:
    src: Dict[str, Dict[str, float]] = {}
    engines = decision.get('engines') or {}
    if decision.get('direction_source') == 'jev' and (engines.get('jev') or {}).get('ok') is not False:
        p = _norm3(decision.get('direction_probs') or {})
        if p:
            src['jev'] = p
    g = _norm3(decision.get('grok_direction_probs') or {})
    if not g and decision.get('direction_source') == 'grok':
        g = _norm3(decision.get('direction_probs') or {})
    if g:
        src['grok'] = g
    com = decision.get('committee') or {}
    try:
        bull, bear = float(com.get('bull_score')), float(com.get('bear_score'))
        d = max(-1.0, min(1.0, (bull - bear) / 100.0))
        up, down = 0.34 + 0.45 * d, 0.33 - 0.45 * d
        src['committee'] = _norm3({'up': max(0.03, up), 'down': max(0.03, down), 'flat': max(0.1, 1 - up - down)})
    except (TypeError, ValueError):
        pass
    if votes:
        p = _norm3({'up': votes.get('buy_pct'), 'flat': votes.get('hold_pct'), 'down': votes.get('sell_pct')})
        if p:
            src['agents'] = p
    mcp = (mc or {}).get('direction_probs') or decision.get('mc_direction_probs')
    p = _norm3(mcp or {})
    if p:
        src['mc'] = p
    return src


def record_decision(report_id: str, decision: Dict[str, Any], report: Optional[Dict[str, Any]] = None,
                    votes: Optional[Dict[str, Any]] = None, mc: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """판단 결과를 원장에 기록 (같은 보고서를 다시 판단하면 덮어쓰고 채점을 초기화)"""
    try:
        from .stock_decision import _horizon_days
    except Exception:  # pragma: no cover
        def _horizon_days(_h):
            return 20
    report = report or {}
    summary = report.get('summary') or {}
    plan = decision.get('plan') or {}
    base = plan.get('current_price') or summary.get('last_close')
    as_of = summary.get('as_of') or (decision.get('created_at') or '')[:10]
    if not base or not as_of:
        logger.warning(f"[{report_id}] 원장 기록 생략: 기준가·기준일 없음")
        return None
    sources = sources_from_decision(decision, votes, mc)
    com = decision.get('committee') or {}
    row = {
        'id': report_id,
        'ticker': decision.get('ticker') or report.get('ticker'),
        'company': decision.get('company_name') or report.get('company_name'),
        'created_at': decision.get('created_at') or datetime.now().isoformat(timespec='seconds'),
        'as_of': str(as_of)[:10],
        'base_price': float(base),
        'dir_horizon': DIR_HORIZON,
        'plan_horizon': int(_horizon_days(plan.get('horizon')) or DIR_HORIZON),
        'rating': summary.get('rating'),
        'report_target': summary.get('target_price'),
        'strategy': decision.get('verdict_key') or decision.get('verdict'),
        'strategy_probs': json.dumps(decision.get('strategy_probs') or {}, ensure_ascii=False),
        'committee_strategy': com.get('recommended_strategy'),
        'grok_strategy': decision.get('grok_strategy'),
        'plan': json.dumps({k: plan.get(k) for k in ('entry_price', 'target_price', 'stop_price', 'position_pct',
                                                      'horizon', 'win_rate_pct', 'expected_return_pct', 'risk_reward',
                                                      'target_pct', 'stop_pct')}, ensure_ascii=False),
        'sources': json.dumps(sources, ensure_ascii=False),
        'extra': json.dumps({'conviction_pct': decision.get('conviction_pct'), 'risk_pct': decision.get('risk_pct'),
                             'technical_score_pct': decision.get('technical_score_pct'),
                             'direction_source': decision.get('direction_source'),
                             'strategy_confidence_pct': decision.get('strategy_confidence_pct'),
                             'direction_confidence_pct': decision.get('direction_confidence_pct'),
                             'calibrated': (decision.get('calibration') or {}).get('blend')}, ensure_ascii=False),
    }
    with _DB_LOCK:
        c = _conn()
        try:
            c.execute('''INSERT OR REPLACE INTO predictions
                (id, ticker, company, created_at, as_of, base_price, dir_horizon, plan_horizon, rating, report_target,
                 strategy, strategy_probs, committee_strategy, grok_strategy, plan, sources, extra, status, scored_at, outcome)
                VALUES (:id, :ticker, :company, :created_at, :as_of, :base_price, :dir_horizon, :plan_horizon, :rating,
                        :report_target, :strategy, :strategy_probs, :committee_strategy, :grok_strategy, :plan, :sources,
                        :extra, 'pending', NULL, NULL)''', row)
            c.commit()
        finally:
            c.close()
    logger.info(f"[{report_id}] 예측 원장 기록: {row['ticker']} 기준 {row['as_of']} {row['base_price']:,.0f}원 · 엔진 {', '.join(sources) or '없음'}")
    return row


# ─────────────────────────────────────────────────────────── 채점
def _candles_after(ticker: str, as_of: str, need: int, fetch: Callable) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """기준일 이후 거래일 봉 목록과 기준일 봉 (fetch = market_data.fchart)"""
    try:
        start = datetime.strptime(as_of[:10], '%Y-%m-%d')
    except ValueError:
        return [], None
    days = max(30, (datetime.now() - start).days + need + 15)
    rows = fetch(ticker, min(1000, int(days * 0.75) + 40))
    key = start.strftime('%Y%m%d')
    base_row = None
    after = []
    for r in rows:
        d = str(r.get('d') or '')
        if d <= key:
            base_row = r
        else:
            after.append(r)
    return after, base_row


def _score_row(r: sqlite3.Row, fetch: Callable) -> Tuple[str, Optional[Dict[str, Any]]]:
    after, base_row = _candles_after(r['ticker'], r['as_of'], max(r['dir_horizon'], r['plan_horizon']), fetch)
    base = float(r['base_price'])
    sources = json.loads(r['sources'] or '{}')
    plan = json.loads(r['plan'] or '{}')
    out: Dict[str, Any] = {'days_elapsed': len(after), 'base_price': base, 'as_of': r['as_of']}
    if after:
        last = after[-1]
        out['last_close'] = last['c']
        out['last_date'] = last['d']
        out['mtm_pct'] = round((last['c'] / base - 1) * 100, 2)
    # ── 방향 (20거래일 종가)
    dir_done = len(after) >= r['dir_horizon']
    if dir_done:
        close_h = after[r['dir_horizon'] - 1]['c']
        ret = (close_h / base - 1) * 100
        y = realized_class(ret)
        eng = {}
        for name, p in sources.items():
            p = _norm3(p)
            if not p:
                continue
            eng[name] = {'hit': _argmax(p) == y, 'brier': round(_brier(p, y), 4), 'logloss': round(_logloss(p, y), 4),
                         'p_true': round(p[y], 4), 'pred': _argmax(p)}
        side = STRATEGY_SIDE.get(str(r['strategy'] or ''))
        out['direction'] = {'horizon_days': r['dir_horizon'], 'close': close_h, 'date': after[r['dir_horizon'] - 1]['d'],
                            'return_pct': round(ret, 2), 'class': y, 'engines': eng,
                            'strategy_hit': (side == y) if side else None,
                            'committee_strategy_hit': (STRATEGY_SIDE.get(str(r['committee_strategy'] or '')) == y)
                            if r['committee_strategy'] else None}
    # ── 매매계획 (목표·손절 선도달)
    entry = plan.get('entry_price') or base
    target, stop = plan.get('target_price'), plan.get('stop_price')
    long_side = STRATEGY_SIDE.get(str(r['strategy'] or '')) == 'up'
    plan_done = False
    if entry and target and stop and long_side:
        hit, hit_day = None, None
        window = after[:r['plan_horizon']]
        for i, b in enumerate(window, 1):
            if b['l'] <= stop:
                hit, hit_day = 'stop', i
                break
            if b['h'] >= target:
                hit, hit_day = 'target', i
                break
        risk = entry - stop
        if hit == 'target':
            exit_px = target
        elif hit == 'stop':
            exit_px = stop
        elif len(after) >= r['plan_horizon']:
            exit_px = window[-1]['c']
            hit = 'horizon'
        else:
            exit_px = after[-1]['c'] if after else None
        plan_done = hit in ('target', 'stop', 'horizon')
        out['plan'] = {
            'outcome': hit or 'open', 'day': hit_day, 'final': plan_done,
            'exit_price': exit_px,
            'return_pct': round((exit_px / entry - 1) * 100, 2) if exit_px else None,
            'r_multiple': round((exit_px - entry) / risk, 2) if exit_px and risk > 0 else None,
            'max_high': max((b['h'] for b in window), default=None), 'min_low': min((b['l'] for b in window), default=None),
        }
    else:
        plan_done = True        # 매수 계획이 아니면 계획 채점 없음
        out['plan'] = {'outcome': 'n/a', 'final': True}
    status = 'scored' if (dir_done and plan_done) else ('partial' if dir_done or after else 'pending')
    return status, out


def score_pending(fetch: Optional[Callable] = None, only_id: Optional[str] = None) -> Dict[str, Any]:
    """채점이 끝나지 않은 예측을 모두 다시 계산 (가격을 가져오지 못하면 다음 기회로)"""
    if fetch is None:
        from . import market_data
        fetch = market_data.fchart
    t0 = time.time()
    done, errors, changed = 0, [], 0
    with _DB_LOCK:
        c = _conn()
        try:
            q = "SELECT * FROM predictions WHERE status != 'scored'" + (" AND id = ?" if only_id else '')
            rows = c.execute(q, (only_id,) if only_id else ()).fetchall()
        finally:
            c.close()
    cache: Dict[str, Any] = {}

    def cached_fetch(ticker, count):
        k = ticker
        if k not in cache or len(cache[k]) < count:
            cache[k] = fetch(ticker, count)
        return cache[k][-count:]

    for r in rows:
        try:
            status, out = _score_row(r, cached_fetch)
            with _DB_LOCK:
                c = _conn()
                try:
                    c.execute('UPDATE predictions SET status=?, scored_at=?, outcome=? WHERE id=?',
                              (status, datetime.now().isoformat(timespec='seconds'), json.dumps(out, ensure_ascii=False), r['id']))
                    c.commit()
                finally:
                    c.close()
            done += 1
            changed += status != r['status']
        except Exception as e:
            errors.append(f"{r['ticker']} {r['id'][-6:]}: {str(e)[:120]}")
    _PARAMS['at'] = 0.0             # 보정 파라미터 다시 계산
    with _DB_LOCK:
        c = _conn()
        try:
            _meta_set(c, 'last_score', {'at': datetime.now(KST).isoformat(timespec='seconds'), 'rows': done,
                                        'errors': errors[:10], 'sec': round(time.time() - t0, 1)})
            c.commit()
        finally:
            c.close()
    logger.info(f"예측 원장 채점: {done}건 갱신 (상태 변경 {changed}) · 오류 {len(errors)} · {time.time() - t0:.1f}초")
    if done:
        try:                            # 채점 결과를 누적 온톨로지의 판단 노드에도 반영
            from . import ontology_store
            ontology_store.ingest_outcomes()
        except Exception as e:
            logger.warning(f"온톨로지 결과 반영 실패: {e}")
    return {'updated': done, 'status_changed': changed, 'errors': errors, 'sec': round(time.time() - t0, 1)}


# ─────────────────────────────────────────────────────────── 집계 · 보정
def _rows(where: str = '', args: tuple = ()) -> List[sqlite3.Row]:
    with _DB_LOCK:
        c = _conn()
        try:
            return c.execute('SELECT * FROM predictions ' + where + ' ORDER BY created_at DESC', args).fetchall()
        finally:
            c.close()


def _scored_pairs(rows) -> Dict[str, List[Tuple[Dict[str, float], str]]]:
    pairs: Dict[str, List[Tuple[Dict[str, float], str]]] = {e: [] for e in ENGINES}
    for r in rows:
        out = json.loads(r['outcome'] or '{}')
        d = out.get('direction')
        if not d:
            continue
        y = d['class']
        for name, p in json.loads(r['sources'] or '{}').items():
            p = _norm3(p)
            if p and name in pairs:
                pairs[name].append((p, y))
    return pairs


def _fit_temperature(pairs: List[Tuple[Dict[str, float], str]]) -> float:
    best_T, best = 1.0, float('inf')
    T = 0.5
    while T <= 4.0001:
        ll = sum(_logloss(_temper(p, T), y) for p, y in pairs) / len(pairs)
        if ll < best - 1e-9:
            best, best_T = ll, T
        T = round(T + 0.1, 2)
    return best_T


def calibration_params(force: bool = False) -> Dict[str, Any]:
    if not force and _PARAMS['data'] is not None and time.time() - _PARAMS['at'] < 600:
        return _PARAMS['data']
    rows = _rows("WHERE outcome IS NOT NULL")
    pairs = _scored_pairs(rows)
    eng = {}
    skills = {}
    for name in ENGINES:
        ps = pairs[name]
        n = len(ps)
        info: Dict[str, Any] = {'n': n}
        if n:
            info['brier'] = round(sum(_brier(p, y) for p, y in ps) / n, 4)
            info['logloss'] = round(sum(_logloss(p, y) for p, y in ps) / n, 4)
            info['hit_rate'] = round(sum(_argmax(p) == y for p, y in ps) / n * 100, 1)
        if n >= MIN_FIT:
            info['T'] = _fit_temperature(ps)
            info['method'] = 'temperature'
            skills[name] = max(0.0, 1.0 - info['brier'] / UNIFORM_BRIER)
        else:
            info['T'] = 1.0
            info['method'] = 'smooth'
        eng[name] = info
    # 가중치: 학습된 엔진은 기술점수 비례, 나머지는 사전 가중치 (전체를 합 1 로)
    weights = {}
    learned = {k: v for k, v in skills.items()}
    if learned and sum(learned.values()) > 0:
        learned_mass = sum(PRIOR_WEIGHTS[k] for k in learned)
        s = sum(learned.values())
        for k in ENGINES:
            weights[k] = (learned[k] / s * learned_mass) if k in learned else PRIOR_WEIGHTS[k]
    else:
        weights = dict(PRIOR_WEIGHTS)
    data = {'engines': eng, 'weights': weights, 'n_scored': sum(1 for r in rows if json.loads(r['outcome'] or '{}').get('direction')),
            'min_fit': MIN_FIT}
    _PARAMS.update(at=time.time(), data=data)
    return data


def calibrate(sources: Dict[str, Dict[str, float]], strategy_probs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """엔진별 보정 확률과 가중 합성 확률 (판단 화면·원장 기록용)"""
    params = calibration_params()
    per, raw, wsum = {}, {}, 0.0
    blend = {k: 0.0 for k in CLASSES}
    for name, p in sources.items():
        p = _norm3(p)
        if not p or name not in params['engines']:
            continue
        raw[name] = {LABEL_KO[k]: round(p[k] * 100, 1) for k in CLASSES}
        info = params['engines'][name]
        q = _temper(p, info['T']) if info['method'] == 'temperature' else _smooth(p, SMOOTH_EPS)
        per[name] = {LABEL_KO[k]: round(q[k] * 100, 1) for k in CLASSES}
        w = params['weights'].get(name, 0.0)
        wsum += w
        for k in CLASSES:
            blend[k] += w * q[k]
    out: Dict[str, Any] = {'engines': per, 'raw': raw, 'labels': {k: ENGINE_KO[k] for k in per},
                           'hit_rates': {k: params['engines'][k].get('hit_rate') for k in per},
                           'n_by_engine': {k: params['engines'][k].get('n', 0) for k in per},
                           'n_scored': params['n_scored'], 'min_fit': MIN_FIT,
                           'learned': [k for k, v in params['engines'].items() if v['method'] == 'temperature'],
                           'weights': {k: round(params['weights'][k] / wsum, 3) for k in per} if wsum else {}}
    if wsum:
        out['blend'] = {LABEL_KO[k]: round(blend[k] / wsum * 100, 1) for k in CLASSES}
        out['blend_direction'] = LABEL_KO[max(CLASSES, key=lambda k: blend[k])]
    # 전략 확률 과신 완화: 한 전략에 99% 이상 쏠리면 균등 분포와 섞어 표시 (원값은 그대로 보존)
    if isinstance(strategy_probs, dict) and strategy_probs:
        vals = {k: max(0.0, float(v or 0)) for k, v in strategy_probs.items()}
        s = sum(vals.values()) or 1.0
        K = len(vals)
        eps = 0.15 if params['n_scored'] < MIN_FIT else 0.08
        out['strategy'] = {k: round(((1 - eps) * v / s + eps / K) * 100, 1) for k, v in vals.items()}
    return out


def summary(ticker: Optional[str] = None) -> Dict[str, Any]:
    rows = _rows("WHERE ticker = ?", (ticker,)) if ticker else _rows()
    params = calibration_params()
    counts = {'total': len(rows), 'pending': 0, 'partial': 0, 'scored': 0}
    for r in rows:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    scored = [r for r in rows if json.loads(r['outcome'] or '{}').get('direction')]
    pairs = _scored_pairs(scored)
    # 엔진별 성적 + 신뢰도 곡선 (P(실제 클래스) 가 아니라 모든 클래스 확률을 10구간으로 묶어 관측 빈도와 비교)
    engines = []
    for name in ENGINES:
        ps = pairs[name]
        if not ps and name not in (params['engines'] or {}):
            continue
        bins = [{'lo': i / 10, 'hi': (i + 1) / 10, 'n': 0, 'p_sum': 0.0, 'o_sum': 0.0} for i in range(10)]
        for p, y in ps:
            for k in CLASSES:
                b = bins[min(9, int(p[k] * 10))]
                b['n'] += 1
                b['p_sum'] += p[k]
                b['o_sum'] += 1.0 if k == y else 0.0
        rel = [{'bin': f"{int(b['lo'] * 100)}~{int(b['hi'] * 100)}%", 'n': b['n'],
                'pred': round(b['p_sum'] / b['n'] * 100, 1), 'obs': round(b['o_sum'] / b['n'] * 100, 1)}
               for b in bins if b['n']]
        n = len(ps)
        info = params['engines'].get(name, {})
        engines.append({
            'engine': name, 'label': ENGINE_KO[name], 'n': n,
            'hit_rate': round(sum(_argmax(p) == y for p, y in ps) / n * 100, 1) if n else None,
            'brier': round(sum(_brier(p, y) for p, y in ps) / n, 4) if n else None,
            'logloss': round(sum(_logloss(p, y) for p, y in ps) / n, 4) if n else None,
            'skill_pct': round((1 - (sum(_brier(p, y) for p, y in ps) / n) / UNIFORM_BRIER) * 100, 1) if n else None,
            'T': info.get('T'), 'method': info.get('method'), 'weight': round(params['weights'].get(name, 0), 3),
            'reliability': rel,
        })
    # 매매계획 성적
    plans = [json.loads(r['outcome'] or '{}').get('plan') or {} for r in rows]
    finals = [p for p in plans if p.get('final') and p.get('outcome') in ('target', 'stop', 'horizon')]
    rs = [p['r_multiple'] for p in finals if isinstance(p.get('r_multiple'), (int, float))]
    plan_stats = {
        'n': len(finals),
        'target_pct': round(sum(p['outcome'] == 'target' for p in finals) / len(finals) * 100, 1) if finals else None,
        'stop_pct': round(sum(p['outcome'] == 'stop' for p in finals) / len(finals) * 100, 1) if finals else None,
        'horizon_pct': round(sum(p['outcome'] == 'horizon' for p in finals) / len(finals) * 100, 1) if finals else None,
        'avg_r': round(sum(rs) / len(rs), 2) if rs else None,
        'avg_return_pct': round(sum(p['return_pct'] for p in finals if p.get('return_pct') is not None) / len(finals), 2) if finals else None,
    }
    strat = [json.loads(r['outcome'] or '{}').get('direction', {}).get('strategy_hit') for r in scored]
    strat = [s for s in strat if s is not None]
    com = [json.loads(r['outcome'] or '{}').get('direction', {}).get('committee_strategy_hit') for r in scored]
    com = [s for s in com if s is not None]
    with _DB_LOCK:
        c = _conn()
        try:
            last = _meta_get(c, 'last_score')
        finally:
            c.close()
    return {
        'counts': counts, 'n_scored': len(scored), 'min_fit': MIN_FIT,
        'engines': engines, 'weights': params['weights'],
        'plan': plan_stats,
        'strategy_hit_rate': round(sum(strat) / len(strat) * 100, 1) if strat else None,
        'committee_strategy_hit_rate': round(sum(com) / len(com) * 100, 1) if com else None,
        'last_score': last,
        'thresholds': {'up_pct': UP_TH, 'down_pct': DOWN_TH, 'dir_horizon': DIR_HORIZON},
    }


def _format_row(r: sqlite3.Row) -> Dict[str, Any]:
    o = json.loads(r['outcome'] or '{}')
    d = o.get('direction') or {}
    srcs = {}
    for k, v in json.loads(r['sources'] or '{}').items():
        v = _norm3(v)
        if v:
            srcs[k] = {LABEL_KO[c]: round(v[c] * 100, 1) for c in CLASSES}
    extra = json.loads(r['extra'] or '{}')
    return {
        'id': r['id'], 'ticker': r['ticker'], 'company': r['company'], 'created_at': r['created_at'],
        'as_of': r['as_of'], 'base_price': r['base_price'], 'status': r['status'],
        'strategy': STRATEGY_KO.get(r['strategy'] or '', r['strategy']), 'rating': r['rating'],
        'report_target': r['report_target'], 'dir_horizon': r['dir_horizon'], 'plan_horizon': r['plan_horizon'],
        'plan': json.loads(r['plan'] or '{}'), 'sources': srcs, 'calibrated': extra.get('calibrated'),
        'days_elapsed': o.get('days_elapsed'), 'mtm_pct': o.get('mtm_pct'), 'last_close': o.get('last_close'),
        'realized': {'return_pct': d.get('return_pct'), 'class': LABEL_KO.get(d.get('class')), 'date': d.get('date')} if d else None,
        'engines': d.get('engines') if d else None,
        'plan_outcome': o.get('plan'),
        'strategy_hit': d.get('strategy_hit') if d else None,
    }


def list_predictions(ticker: Optional[str] = None, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    where, args = [], []
    if ticker:
        where.append('ticker = ?')
        args.append(ticker)
    if status:
        where.append('status = ?')
        args.append(status)
    rows = _rows(('WHERE ' + ' AND '.join(where)) if where else '', tuple(args))
    return [_format_row(r) for r in rows[:max(1, min(2000, int(limit or 200)))]]


def get_prediction(report_id: str) -> Optional[Dict[str, Any]]:
    rows = _rows('WHERE id = ?', (report_id,))
    return _format_row(rows[0]) if rows else None


# ─────────────────────────────────────────────────────────── 백필 · 스케줄
def backfill() -> Dict[str, Any]:
    """이미 만들어진 판단(decision.json)을 원장에 가져옴 — 원장에 없는 것만"""
    base = os.path.join(Config.UPLOAD_FOLDER, 'stock_reports')
    added, skipped = 0, 0
    if not os.path.isdir(base):
        return {'added': 0, 'skipped': 0}
    with _DB_LOCK:
        c = _conn()
        try:
            have = {r['id'] for r in c.execute('SELECT id FROM predictions').fetchall()}
        finally:
            c.close()
    for rid in sorted(os.listdir(base)):
        if rid in have:
            skipped += 1
            continue
        dpath = os.path.join(base, rid, 'decision.json')
        rpath = os.path.join(base, rid, 'report.json')
        if not os.path.exists(dpath):
            continue
        try:
            with open(dpath, 'r', encoding='utf-8') as f:
                dec = json.load(f)
            rep = {}
            if os.path.exists(rpath):
                with open(rpath, 'r', encoding='utf-8') as f:
                    rep = json.load(f)
            votes = None
            try:
                sim_id = rep.get('simulation_id') or ''
                sp = os.path.join(Config.UPLOAD_FOLDER, 'stock_simulations', sim_id, 'result.json')
                if sim_id and os.path.exists(sp):
                    with open(sp, 'r', encoding='utf-8') as f:
                        sim = json.load(f)
                    sm = sim.get('summary') or {}
                    tot = max(1, (sm.get('buy_count') or 0) + (sm.get('sell_count') or 0) + (sm.get('hold_count') or 0))
                    votes = {'buy_pct': sm.get('buy_pct', (sm.get('buy_count') or 0) / tot * 100),
                             'sell_pct': sm.get('sell_pct', (sm.get('sell_count') or 0) / tot * 100),
                             'hold_pct': sm.get('hold_pct', (sm.get('hold_count') or 0) / tot * 100)}
                    mc = sim.get('monte_carlo')
                else:
                    mc = None
            except Exception:
                mc = None
            if record_decision(rid, dec, rep, votes, mc):
                added += 1
        except Exception as e:
            logger.warning(f"원장 백필 실패 {rid}: {e}")
    return {'added': added, 'skipped': skipped}


_SCHED: Dict[str, Any] = {'thread': None}
SCORE_TIME = (16, 40)       # KST, 장 마감 뒤


def _loop():
    time.sleep(90)
    try:
        backfill()
        score_pending()
    except Exception as e:
        logger.warning(f"원장 초기 채점 실패: {e}")
    while True:
        try:
            now = datetime.now(KST)
            with _DB_LOCK:
                c = _conn()
                try:
                    last_day = _meta_get(c, 'last_daily_score_day')
                finally:
                    c.close()
            if now.weekday() < 5 and (now.hour, now.minute) >= SCORE_TIME and last_day != now.strftime('%Y-%m-%d'):
                score_pending()
                with _DB_LOCK:
                    c = _conn()
                    try:
                        _meta_set(c, 'last_daily_score_day', now.strftime('%Y-%m-%d'))
                        c.commit()
                    finally:
                        c.close()
        except Exception as e:
            logger.warning(f"원장 정기 채점 실패: {e}")
        time.sleep(600)


def start_scheduler(debug: bool) -> bool:
    if debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return False
    if (os.environ.get('LEDGER_SCHEDULE') or 'on').strip().lower() == 'off':
        return False
    t = _SCHED.get('thread')
    if t is not None and t.is_alive():
        return True
    t = threading.Thread(target=_loop, daemon=True, name='ledger-scheduler')
    _SCHED['thread'] = t
    t.start()
    return True
