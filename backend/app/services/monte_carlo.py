"""
몬테카를로 주가 경로 — 과거 일간 수익률을 블록 단위로 다시 뽑아 이어 붙이는 부트스트랩 (정규분포 가정 없음)

방법
  1) 최근 최대 250거래일 일봉에서 하루 단위 (종가 수익률, 고가/전일종가−1, 저가/전일종가−1) 표본을 만듦
  2) 변동성 체제 반영: 최근 20일 변동성 ÷ 표본 전체 변동성 (0.6~1.8 로 제한) 만큼 수익률 편차를 키우거나 줄임
  3) 추세 절반만 유지: 과거 평균 수익률을 그대로 미래로 늘이지 않도록 평균의 절반만 드리프트로 남김
  4) 5일 블록(원형) 부트스트랩으로 경로 5,000개 × 20거래일 — 변동성 군집·단기 자기상관을 어느 정도 보존
  5) 같은 시드로 다시 돌리면 같은 결과 (시뮬레이션 ID 로 시드 고정 → 재현 가능)

결과 (시뮬레이션 result.json 의 monte_carlo)
  direction_probs  상승(+3%↑)·보합·하락(−3%↓) 확률 (%)  ← 예측 원장의 'mc' 엔진
  return_pct       기간 수익률 분위수 p5·p25·p50·p75·p95·평균 (%)
  var95_pct        95% VaR (기간 수익률 5% 분위, 음수) · cvar95_pct (그보다 나쁜 경우의 평균)
  mdd_p50_pct      경로별 최대 낙폭의 중앙값
  fan              거래일별 누적 수익률 분위 (팬 차트)
매매계획 평가 (판단 단계): plan_probabilities() — 일중 고가·저가 표본으로 목표가·손절가 중 무엇이 먼저 닿는지,
  기대 손익비(R). 원장 채점 규칙과 같음: 같은 날 둘 다 닿으면 손절 우선, 기간이 끝나면 종가 청산.
"""

import hashlib
import math
import os
import json
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.montecarlo')

PATHS = 5000
HORIZON = 20
BLOCK = 5
LOOKBACK = 250
MIN_ROWS = 60
UP_TH, DOWN_TH = 3.0, -3.0
VOL_SCALE_RANGE = (0.6, 1.8)
DRIFT_KEEP = 0.5
METHOD_LABEL = '블록 부트스트랩 · 변동성 체제 조정'
QUANTS = (('p5', .05), ('p25', .25), ('p50', .5), ('p75', .75), ('p95', .95))


def _num(x) -> Optional[float]:
    try:
        v = float(str(x).replace(',', ''))
        return v if math.isfinite(v) and v > 0 else None
    except (TypeError, ValueError):
        return None


def sample_from_ohlcv(ohlcv: Sequence[Dict[str, Any]], lookback: int = LOOKBACK) -> List[Tuple[float, float, float]]:
    """일봉 → [(종가 수익률, 고가 변동, 저가 변동)] (전일 종가 대비 비율). 한국어·영문 키 모두 지원"""
    bars = []
    for r in ohlcv or []:
        if not isinstance(r, dict):
            continue
        c = _num(r.get('종가', r.get('close', r.get('c'))))
        h = _num(r.get('고가', r.get('high', r.get('h')))) or c
        lo = _num(r.get('저가', r.get('low', r.get('l')))) or c
        if c:
            bars.append((c, max(h, c), min(lo, c)))
    out = []
    for (pc, _, _), (c, h, lo) in zip(bars, bars[1:]):
        r = c / pc - 1
        if abs(r) > 0.35:           # 액면분할·병합 등 비정상 점프는 제외
            continue
        out.append((r, max(r, h / pc - 1), min(r, lo / pc - 1)))
    return out[-lookback:]


def _stdev(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _params(sample: Sequence[Tuple[float, float, float]]) -> Dict[str, float]:
    rets = [s[0] for s in sample]
    sd_all = _stdev(rets)
    sd_recent = _stdev(rets[-20:])
    scale = min(VOL_SCALE_RANGE[1], max(VOL_SCALE_RANGE[0], sd_recent / sd_all)) if sd_all > 0 else 1.0
    mean = sum(rets) / len(rets)
    return {'mean': mean, 'scale': scale, 'drift': mean * DRIFT_KEEP, 'sd_all': sd_all, 'sd_recent': sd_recent}


def _adjusted(sample, prm) -> List[Tuple[float, float, float]]:
    out = []
    for r, h, lo in sample:
        r2 = (r - prm['mean']) * prm['scale'] + prm['drift']
        out.append((r2, r2 + (h - r) * prm['scale'], r2 + (lo - r) * prm['scale']))
    return out


def _seed(key: str) -> int:
    return int(hashlib.sha256((key or 'weaidstock').encode('utf-8')).hexdigest()[:12], 16)


def _paths(adj, n_paths: int, horizon: int, block: int, seed: int):
    """원형 블록 부트스트랩 경로 생성기 — 경로마다 [(r, hi, lo)] × horizon"""
    rng = random.Random(seed)
    n = len(adj)
    for _ in range(n_paths):
        path = []
        while len(path) < horizon:
            s = rng.randrange(n)
            for k in range(block):
                path.append(adj[(s + k) % n])
                if len(path) >= horizon:
                    break
        yield path


def _q(sorted_xs: List[float], p: float) -> float:
    if not sorted_xs:
        return float('nan')
    i = (len(sorted_xs) - 1) * p
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (i - lo)


def _pct3(counts: Dict[str, int], total: int) -> Dict[str, float]:
    raw = {k: counts[k] / total * 100 for k in ('상승', '보합', '하락')}
    out = {k: round(v, 1) for k, v in raw.items()}
    diff = round(100 - sum(out.values()), 1)
    if diff:
        k = max(raw, key=raw.get)
        out[k] = round(out[k] + diff, 1)
    return out


def simulate(ohlcv: Sequence[Dict[str, Any]], seed_key: str = '', paths: int = PATHS, horizon: int = HORIZON,
             block: int = BLOCK) -> Optional[Dict[str, Any]]:
    sample = sample_from_ohlcv(ohlcv)
    if len(sample) < MIN_ROWS:
        logger.info(f"몬테카를로 생략: 일봉 {len(sample)}개 (최소 {MIN_ROWS}개 필요)")
        return None
    prm = _params(sample)
    adj = _adjusted(sample, prm)
    seed = _seed(seed_key)
    finals, mdds = [], []
    by_day: List[List[float]] = [[] for _ in range(horizon)]
    counts = {'상승': 0, '보합': 0, '하락': 0}
    for path in _paths(adj, paths, horizon, block, seed):
        m, peak, mdd = 1.0, 1.0, 0.0
        for d, (r, _h, _l) in enumerate(path):
            m *= 1 + r
            by_day[d].append(m)
            peak = max(peak, m)
            mdd = min(mdd, m / peak - 1)
        ret = (m - 1) * 100
        finals.append(ret)
        mdds.append(mdd * 100)
        counts['상승' if ret >= UP_TH else '하락' if ret <= DOWN_TH else '보합'] += 1
    finals.sort()
    mdds.sort()
    p5 = _q(finals, 0.05)
    tail = [x for x in finals if x <= p5]
    fan = []
    for d, col in enumerate(by_day):
        col.sort()
        fan.append({'day': d + 1, **{k: round((_q(col, p) - 1) * 100, 2)
                                      for k, p in QUANTS}})
    return {
        'method': 'block_bootstrap', 'method_label': METHOD_LABEL,
        'paths': paths, 'horizon_days': horizon, 'block': block, 'lookback_days': len(sample), 'seed': seed,
        'direction_probs': _pct3(counts, paths),
        'thresholds_pct': {'up': UP_TH, 'down': DOWN_TH},
        'return_pct': dict({k: round(_q(finals, p), 2) for k, p in QUANTS}, mean=round(sum(finals) / len(finals), 2)),
        'var95_pct': round(p5, 2),
        'cvar95_pct': round(sum(tail) / len(tail), 2) if tail else None,
        'mdd_p50_pct': round(_q(mdds, 0.5), 2),
        'daily_vol_pct': round(prm['sd_recent'] * 100, 2),
        'vol_scale': round(prm['scale'], 2),
        'drift_bp': round(prm['drift'] * 10000, 1),
        'fan': fan,
    }


def plan_probabilities(sample: Sequence[Sequence[float]], seed_key: str, base_price: float, entry: float,
                       target: float, stop: float, horizon_days: int, paths: int = PATHS,
                       block: int = BLOCK) -> Optional[Dict[str, Any]]:
    """매수 계획이 목표가·손절가 중 어디에 먼저 닿는지 (원장 채점 규칙과 동일)"""
    if not (sample and base_price and entry and target and stop) or not (stop < entry < target or stop < base_price < target):
        return None
    if len(sample) < MIN_ROWS:
        return None
    sample = [tuple(x) for x in sample]
    prm = _params(sample)
    adj = _adjusted(sample, prm)
    horizon = max(5, min(120, int(horizon_days or HORIZON)))
    risk = entry - stop
    if risk <= 0:
        return None
    n_t = n_s = 0
    rs, days_t = [], []
    for path in _paths(adj, paths, horizon, block, _seed(seed_key + ':plan')):
        px = base_price
        hit, exit_px = None, None
        for d, (r, h, lo) in enumerate(path, 1):
            if px * (1 + lo) <= stop:
                hit, exit_px = 'stop', stop
                break
            if px * (1 + h) >= target:
                hit, exit_px = 'target', target
                days_t.append(d)
                break
            px *= 1 + r
        if hit == 'target':
            n_t += 1
        elif hit == 'stop':
            n_s += 1
        else:
            exit_px = px
        rs.append((exit_px - entry) / risk)
    days_t.sort()
    return {
        'horizon_days': horizon, 'paths': paths,
        'p_target_first': round(n_t / paths * 100, 1),
        'p_stop_first': round(n_s / paths * 100, 1),
        'p_horizon': round((paths - n_t - n_s) / paths * 100, 1),
        'expected_r': round(sum(rs) / len(rs), 2),
        'median_days_to_target': days_t[len(days_t) // 2] if days_t else None,
        'entry': entry, 'target': target, 'stop': stop,
    }


# ─────────────────────────────────────────────────────────── 시뮬레이션 결과와 연결
def _sim_dir(simulation_id: str) -> str:
    return os.path.join(Config.UPLOAD_FOLDER, 'stock_simulations', simulation_id)


def attach_to_result(result: Dict[str, Any], ohlcv: Sequence[Dict[str, Any]], simulation_id: str) -> Optional[Dict[str, Any]]:
    """시뮬레이션 결과 dict 에 monte_carlo(요약)·monte_carlo_sample(계획 평가용 표본)을 넣음"""
    try:
        mc = simulate(ohlcv, seed_key=simulation_id)
    except Exception as e:
        logger.warning(f"[{simulation_id}] 몬테카를로 실패(생략): {e}")
        return None
    if mc:
        result['monte_carlo'] = mc
        result['monte_carlo_sample'] = [[round(a, 6), round(b, 6), round(c, 6)] for a, b, c in sample_from_ohlcv(ohlcv)]
        logger.info(f"[{simulation_id}] 몬테카를로 {mc['paths']}경로·{mc['horizon_days']}일: 상승 {mc['direction_probs']['상승']}% "
                    f"· 중앙값 {mc['return_pct']['p50']:+.1f}% · VaR95 {mc['var95_pct']:+.1f}%")
    return mc


def ensure_for_simulation(simulation_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[List[List[float]]]]:
    """이전에 만든 시뮬레이션에 몬테카를로가 없으면 1단계 일봉으로 계산해 result.json 에 채워 넣음"""
    if not simulation_id:
        return None, None
    path = os.path.join(_sim_dir(simulation_id), 'result.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            sim = json.load(f)
    except Exception:
        return None, None
    if isinstance(sim.get('monte_carlo'), dict) and sim['monte_carlo'].get('direction_probs') and sim.get('monte_carlo_sample'):
        return sim['monte_carlo'], sim['monte_carlo_sample']
    task_id = sim.get('task_id')
    try:
        with open(os.path.join(Config.UPLOAD_FOLDER, 'stock_analyses', task_id or '-', 'analysis.json'), 'r', encoding='utf-8') as f:
            ana = json.load(f)
    except Exception:
        return sim.get('monte_carlo'), sim.get('monte_carlo_sample')
    mc = attach_to_result(sim, (ana.get('financial_data') or {}).get('ohlcv') or [], simulation_id)
    if mc:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(sim, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return sim.get('monte_carlo'), sim.get('monte_carlo_sample')
