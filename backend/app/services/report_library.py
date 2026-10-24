"""
보고서 보관함 — 저장된 보고서·지식그래프·판단을 한눈에 보고, 다시 열고, 비교

보고서 폴더(uploads/stock_reports/<id>/)의 report.json·decision.json·kg.json 에서 요약 필드만 읽고,
파일 수정 시각이 바뀌지 않으면 메모리 캐시를 씁니다.
"""

import json
import os
import threading
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.library')
_cache: Dict[str, Any] = {}
_lock = threading.Lock()


def _dir() -> str:
    return os.path.join(Config.UPLOAD_FOLDER, 'stock_reports')


def _read(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _mt(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _summary(rid: str) -> Optional[Dict[str, Any]]:
    d = os.path.join(_dir(), rid)
    rp, dp, kp = (os.path.join(d, n) for n in ('report.json', 'decision.json', 'kg.json'))
    sig = (_mt(rp), _mt(dp), _mt(kp))
    with _lock:
        hit = _cache.get(rid)
        if hit and hit[0] == sig:
            return hit[1]
    rep = _read(rp)
    if not rep:
        return None
    s = rep.get('summary') or {}
    item: Dict[str, Any] = {
        'report_id': rid, 'ticker': rep.get('ticker'), 'company_name': rep.get('company_name'),
        'created_at': rep.get('created_at'), 'completed_at': rep.get('completed_at'),
        'status': rep.get('status'), 'simulation_id': rep.get('simulation_id'),
        'rating': s.get('rating'), 'target_price': s.get('target_price'), 'last_close': s.get('last_close'),
        'upside_pct': s.get('upside_pct'), 'as_of': s.get('as_of'), 'headline': s.get('headline'),
        'valuation_method': s.get('valuation_method'), 'fair_value': s.get('fair_value'),
        'consensus_target': s.get('consensus_target_mean'), 'consensus_gap_pct': s.get('consensus_gap_pct'),
        'generation_sec': rep.get('generation_time_seconds'),
        'speed': (rep.get('generation') or {}).get('speed'),
        'has_kg': os.path.exists(kp), 'has_decision': os.path.exists(dp),
    }
    pred = rep.get('prediction') or {}
    item['agents'] = {'direction': pred.get('direction'), 'probability': pred.get('probability')}
    if item['has_decision']:
        dec = _read(dp) or {}
        plan = dec.get('plan') or {}
        com = dec.get('committee') or {}
        cal = dec.get('calibration') or {}
        item.update({
            'verdict': dec.get('verdict'), 'verdict_pct': dec.get('verdict_pct'),
            'direction': dec.get('direction'), 'direction_probs': dec.get('direction_probs'),
            'direction_source': dec.get('direction_source'),
            'calibrated': cal.get('blend'),
            'plan': {k: plan.get(k) for k in ('entry_price', 'target_price', 'stop_price', 'target_pct', 'stop_pct',
                                              'position_pct', 'horizon', 'risk_reward', 'expected_return_pct')},
            'committee': {k: com.get(k) for k in ('bull_score', 'bear_score', 'consensus_label', 'recommended_label')} if com else None,
            'decided_at': dec.get('created_at'), 'decision_headline': dec.get('headline'),
            'mc': {k: (dec.get('monte_carlo') or {}).get(k) for k in ('direction_probs', 'var95_pct')} if dec.get('monte_carlo') else None,
        })
    if item['has_kg']:
        kg = _read(kp) or {}
        st = kg.get('stats') or {}
        item['kg'] = {'nodes': st.get('nodes') or len(kg.get('nodes') or []), 'edges': st.get('edges') or len(kg.get('edges') or []),
                      'cqs': st.get('cqs') or len(kg.get('cqs') or []), 'adrs': st.get('adrs') or len(kg.get('adrs') or [])}
    with _lock:
        _cache[rid] = (sig, item)
    return item


def list_reports(ticker: Optional[str] = None, q: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    base = _dir()
    if not os.path.isdir(base):
        return []
    out = []
    for rid in os.listdir(base):
        if not rid.startswith('stock_report_'):
            continue
        it = _summary(rid)
        if not it:
            continue
        if ticker and it.get('ticker') != ticker:
            continue
        if q and q not in (it.get('company_name') or '') and q not in (it.get('ticker') or ''):
            continue
        out.append(it)
    out.sort(key=lambda x: x.get('created_at') or '', reverse=True)
    return out[:max(1, min(1000, int(limit or 200)))]


def meta(report_id: str) -> Optional[Dict[str, Any]]:
    it = _summary(report_id)
    if not it:
        return None
    from .stock_evidence import paths_for_report
    p = paths_for_report(report_id)
    return dict(it, task_id=p.get('task_id'))


def compare(ids: List[str]) -> Dict[str, Any]:
    items = [x for x in (_summary(i) for i in ids) if x]
    try:
        from . import prediction_ledger as ledger
        for it in items:
            p = ledger.get_prediction(it['report_id'])
            if p:
                it['ledger'] = {'status': p['status'], 'mtm_pct': p.get('mtm_pct'), 'realized': p.get('realized'),
                                'plan_outcome': (p.get('plan_outcome') or {}).get('outcome'), 'strategy_hit': p.get('strategy_hit')}
    except Exception as e:
        logger.debug(f"비교: 원장 조회 생략 {e}")
    # 같은 종목 여러 실행의 흔들림(최댓값−최솟값)
    spread = {}
    tickers = {it.get('ticker') for it in items}
    if len(items) >= 2 and len(tickers) == 1:
        def rng(vals):
            vals = [v for v in vals if isinstance(v, (int, float))]
            return {'min': min(vals), 'max': max(vals), 'range': round(max(vals) - min(vals), 2)} if len(vals) >= 2 else None
        spread = {
            'target_price': rng([it.get('target_price') for it in items]),
            'upside_pct': rng([it.get('upside_pct') for it in items]),
            'up_prob': rng([(it.get('direction_probs') or {}).get('상승') for it in items]),
            'plan_target': rng([(it.get('plan') or {}).get('target_price') for it in items]),
            'plan_stop': rng([(it.get('plan') or {}).get('stop_price') for it in items]),
            'ratings': sorted({it.get('rating') for it in items if it.get('rating')}),
            'verdicts': sorted({it.get('verdict') for it in items if it.get('verdict')}),
        }
    return {'items': items, 'same_ticker': len(tickers) == 1, 'spread': spread}
