"""
주식 예측 보고서 생성 에이전트 — 증권사 리서치 리포트 템플릿 (report_version 2)

보고서 구조 (report["sections"] 키 · 제목, 표시 순서):
    investment_summary   투자 요약            헤드라인·투자의견·목표주가·상승여력
    investment_points    투자 포인트          3개, 근거 수치 + [F#]/[R#] 인용
    earnings_analysis    실적 분석            EPS·배당·ROE (가용 데이터만, 없으면 '데이터 없음')
    valuation            밸류에이션           PER/PBR 밴드 → 적정가·목표주가, 52주 밴드
    technical_analysis   기술적 분석          (하위 호환 키 — stock_decision 이 사용)
    consensus_forecast   시뮬레이션·컨센서스  (하위 호환 키 — 에이전트 투표)
    risk_factors         리스크 요인          (하위 호환 키)
    investment_opinion   투자의견             (하위 호환 키)

섹션 dict (기존 소비자 호환: title·content 가 앞쪽에 위치):
    {"title": str, "content": markdown, "data": {...구조화...},
     "validation": {"valid": bool, "attempts": n, "engine": "llm|rule", "errors": [...], "fallback_reason"?}}

생성 흐름:
    1) 가격·재무·투표·리서치(research.json, 있으면)를 결정론적으로 계산 → 사실표(F#/R#/C#)
    2) 1차 병렬(≤4 스레드): 투자 포인트·실적·밸류에이션·기술적·컨센서스·리스크
       → 목표주가·상승여력 확정 → 하우스 기준으로 투자의견(매수/중립/매도) 확정
    3) 2차 병렬: 투자 요약·투자의견 (확정된 의견·목표가를 고정값으로 전달해 모순 방지)
    각 섹션: LLM JSON → 스키마 검증(필수 키·타입·유한 수치·목표가 0.5~2배·근거 ID 실재)
             → 실패 시 오류 목록을 담아 수정 재요청(최대 2회) → 그래도 실패하면 규칙 기반 섹션

최상위 추가 필드: summary, charts, facts, sources, prediction, research_used, report_version

면책 조항:
    본 보고서는 AI 시뮬레이션 결과이며 투자 조언이 아닙니다.
"""

import os
import re
import json
import math
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from typing import Dict, Any, List, Optional, Callable, Tuple
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.llm_client import LLMClient, get_stream_sink, set_stream_sink, is_timeout_error
from ..utils.resilience import repair_json
from ..utils.logger import get_logger
from .stock_evidence import build_fact_sheet, fact_sheet_text, load_research

logger = get_logger('mirofish.stock_report_agent')


class StockReportStatus(str, Enum):
    """주식 보고서 상태"""
    GENERATING = "generating"   # 생성 중
    COMPLETED = "completed"     # 완료
    FAILED = "failed"           # 실패


# ═══════════════════════════════════════════════════════════════════ 공통 도우미
RATINGS = ("매수", "중립", "매도")
SIGNALS = ("강한 매수", "약한 매수", "보합/관망", "약한 매도", "강한 매도")
AGENT_TYPE_KO = {"conservative": "보수형", "moderate": "중립형", "aggressive": "공격형", "unknown": "기타"}
MAX_REPAIRS = 2          # 검증 실패 시 수정 재요청 횟수 (총 시도 = 1 + 2)
MAX_WORKERS = 6          # 1차 분석 섹션 6개를 한 번에 병렬 생성

# ── 속도 (REPORT_SPEED)
#   balanced(기본): 목표 배수·적정가를 판단하는 밸류에이션만 추론 모델, 나머지는 빠른 비추론 모델
#                   (투자의견·목표주가 숫자는 밸류에이션 결과로 규칙이 확정하므로 서술 섹션은 빠른 모델로 충분)
#   quality: 모든 섹션 추론 모델 · fast: 모든 섹션 빠른 모델
#   어떤 방식이든 섹션마다 시간 한도가 있고, 추론 모델이 멈추거나 느리면 빠른 모델 → 규칙 기반 순으로 대체합니다.
#   실측 (HLB, 2026-09-28): 빠른 모델 섹션 4~8초, 추론 모델 섹션 57~150초
REASONING_SECTIONS = ('valuation',)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def _report_speed() -> str:
    v = (os.environ.get('REPORT_SPEED') or 'balanced').strip().lower()
    return v if v in ('balanced', 'quality', 'fast') else 'balanced'


def _section_budget(tier: str) -> float:
    # 섹션 하나(수정 재요청 포함)의 시간 한도
    return _env_float('REPORT_REASONING_BUDGET', 120) if tier == 'reasoning' else _env_float('REPORT_FAST_BUDGET', 60)


PHASE_BUDGET = {1: 300.0, 2: 240.0}   # 단계 전체 감시 한도 — 넘으면 남은 섹션은 규칙 기반으로 마무리
CHART_ROWS = 120
UNAVAILABLE_BASE = ["매출액", "영업이익", "순이익", "분기별 실적 추이", "증권사 실적 컨센서스"]
RATING_POLICY = (
    "하우스 투자의견 기준: 목표주가 대비 상승여력 +15% 이상 '매수', −10% 이하 '매도', 그 사이 '중립'. "
    "AI 에이전트 투표 차(매수−매도)가 +20%p 이상이고 상승여력이 +5% 이상이면 중립→매수, "
    "−20%p 이하이고 상승여력이 0% 이하이면 중립→매도로 한 단계 조정하며, "
    "의견과 반대 방향의 투표 우위가 20%p 이상이면 매수·매도를 중립으로 낮춘다."
)
DISCLAIMER_LINE = "본 보고서는 AI 시뮬레이션 결과이며 투자 조언이 아닙니다."
METHOD_LABEL = {'PER': 'PER 밴드', 'PBR': 'PBR 밴드', '혼합': 'PER·PBR 밴드 혼합', '가격밴드': '52주 가격 밴드',
                '선행PER': '선행 PER(컨센서스 선행 EPS)', '데이터 부족': '데이터 부족'}
FWD_LLM_BAND = 0.35        # LLM 목표가 허용 범위: 규칙 선행 목표가 ±35%
CONSENSUS_GAP_NOTE = 30.0  # 컨센서스 목표주가 평균 대비 괴리가 이 %를 넘으면 설명 문구 추가
_CONS_NA_RE = re.compile(r'(컨센서스|선행\s*EPS|목표주가\s*평균)[^.\n。]{0,30}데이터\s*없음|데이터\s*없음[^.\n。]{0,12}(컨센서스|선행\s*EPS)')


def _recomm_label(m) -> Optional[str]:
    """네이버 투자의견 평균(1~5: 5 강력매수 · 4 매수 · 3 중립 · 2 매도 · 1 강력매도) → 한국어"""
    m = _f(m)
    if m is None:
        return None
    return '강력매수' if m >= 4.5 else '매수' if m >= 3.5 else '중립' if m >= 2.5 else '매도' if m >= 1.5 else '강력매도'


def _own_target_mentions(text: str):
    """'목표주가 N원' 언급 중 컨센서스·증권사·후행 참고값이 아닌 것(=당사 목표가)만"""
    for m in re.finditer(r'목표\s*주?가[^0-9\n]{0,12}([\d][\d,]*)\s*원', text or ''):
        before = (text or '')[max(0, m.start() - 14):m.start()]
        if '평균' in m.group(0) or any(k in before for k in ('컨센서스', '증권사', '후행', '참고')):
            continue
        yield m
_REF_RE = re.compile(r'\[([FRCKH])\s*(\d{1,3})\]')
_FATAL_MARKERS = ('insufficient_quota', 'credit_balance_exhausted', '401', 'invalid_api_key',
                  'Incorrect API key', 'permission_denied')


def _f(x) -> Optional[float]:
    """유한한 float 로 변환 (숫자 문자열의 쉼표·원·%·배 허용). 실패·NaN·inf → None"""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        v = float(x)
    else:
        s = re.sub(r'[,\s원%배주]', '', str(x))
        if not s:
            return None
        try:
            v = float(s)
        except ValueError:
            return None
    return v if math.isfinite(v) else None


def _pos(x) -> Optional[float]:
    """양수만 (pykrx 는 결측을 0 으로 채움)"""
    v = _f(x)
    return v if (v is not None and v > 0) else None


def _won(v, suffix='원') -> str:
    v = _f(v)
    return '데이터 없음' if v is None else f"{v:,.0f}{suffix}"


def _eok(v) -> str:
    """억원 단위 금액 → '1.71조원' / '894억원'"""
    v = _f(v)
    if v is None:
        return '데이터 없음'
    return f"{v / 10000:,.2f}조원" if abs(v) >= 10000 else f"{v:,.0f}억원"


def _pct(v, signed=True, nd=1) -> str:
    v = _f(v)
    if v is None:
        return '데이터 없음'
    return f"{v:+.{nd}f}%" if signed else f"{v:.{nd}f}%"


def _mult(v) -> str:
    v = _f(v)
    return '데이터 없음' if v is None else f"{v:.2f}배"


def _clip(v, lo, hi):
    return max(lo, min(hi, v))


def _round_price(v: Optional[float]) -> Optional[float]:
    """목표주가 표기 단위로 반올림 (1만원 미만 10원, 10만원 미만 100원, 100만원 미만 1,000원, 이상 10,000원)"""
    v = _f(v)
    if v is None or v <= 0:
        return None
    unit = 10 if v < 10_000 else 100 if v < 100_000 else 1_000 if v < 1_000_000 else 10_000
    return float(round(v / unit) * unit)


def _r(v, nd=2):
    v = _f(v)
    return None if v is None else round(v, nd)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _percentile(xs: List[float], q: float) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _band_at(lo: float, mid: float, hi: float, q: float) -> float:
    """밴드(하단·중앙·상단)에서 분위 q(0~1) 위치 값 (구간 선형 보간)"""
    if q <= 0.5:
        return lo + (mid - lo) * (q / 0.5)
    return mid + (hi - mid) * ((q - 0.5) / 0.5)


def _norm_date(v) -> str:
    s = str(v or '').strip()
    if re.fullmatch(r'\d{8}', s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


def _pick(row: Dict[str, Any], keys) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ''):
            return row[k]
    return None


def _norm_rows(ohlcv: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """pykrx/네이버 OHLCV → [{date,o,h,l,c,v}] (종가 없는 행 제외)"""
    rows = []
    for r in ohlcv or []:
        if not isinstance(r, dict):
            continue
        c = _pos(_pick(r, ('종가', 'close', 'Close', 'c')))
        if c is None:
            continue
        rows.append({
            'date': _norm_date(_pick(r, ('날짜', 'date', 'Date', 'index', 'd'))),
            'o': _pos(_pick(r, ('시가', 'open', 'Open', 'o'))),
            'h': _pos(_pick(r, ('고가', 'high', 'High', 'h'))),
            'l': _pos(_pick(r, ('저가', 'low', 'Low', 'l'))),
            'c': c,
            'v': _f(_pick(r, ('거래량', 'volume', 'Volume', 'v'))),
        })
    return rows


def _sma_series(closes: List[float], n: int) -> List[Optional[float]]:
    out, s = [], 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        out.append(round(s / n, 1) if i >= n - 1 else None)
    return out


def _price_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """StockJudge._price_stats 와 동일 공식 (+ 거래량·기간 수익률)"""
    closes = [r['c'] for r in rows]
    if len(closes) < 2:
        return {"has_data": False}
    last = closes[-1]

    def ma(n):
        return round(sum(closes[-n:]) / n, 1) if len(closes) >= n else None

    def chg(n):
        return round((last / closes[-n - 1] - 1) * 100, 2) if len(closes) > n else None

    rsi = None
    if len(closes) > 15:
        gains = losses = 0.0
        for a, b in zip(closes[-15:-1], closes[-14:]):
            d = b - a
            gains += max(d, 0)
            losses += max(-d, 0)
        rsi = 100.0 if losses == 0 else round(100 - 100 / (1 + gains / losses), 1)
    rets = [(b / a - 1) * 100 for a, b in zip(closes[-21:-1], closes[-20:])]
    vol = None
    if len(rets) > 1:
        m = sum(rets) / len(rets)
        vol = round((sum((x - m) ** 2 for x in rets) / (len(rets) - 1)) ** 0.5, 2)
    vols = [r['v'] for r in rows if r.get('v') is not None]
    v5, v20 = _mean(vols[-5:]), _mean(vols[-20:])
    return {
        "has_data": True,
        "last_close": last,
        "ma5": ma(5), "ma20": ma(20), "ma60": ma(60),
        "chg_5d_pct": chg(5), "chg_20d_pct": chg(20), "chg_60d_pct": chg(60),
        "high_60d": max(closes[-60:]), "low_60d": min(closes[-60:]),
        "rsi14": rsi,
        "daily_vol_20d_pct": vol,
        "recent_closes": closes[-20:],
        "avg_volume_5d": _r(v5, 0), "avg_volume_20d": _r(v20, 0),
        "vol_ratio_5_20": _r(v5 / v20, 2) if (v5 is not None and v20) else None,
        "period_return_pct": round((last / closes[0] - 1) * 100, 2),
        "as_of": rows[-1].get('date') or '',
    }


def _trend_label(p: Dict[str, Any]) -> str:
    c, m20, m60 = p.get('last_close'), p.get('ma20'), p.get('ma60')
    if c is None or m20 is None:
        chg = p.get('chg_20d_pct') or p.get('period_return_pct') or 0
        return '상승' if chg > 5 else '하락' if chg < -5 else '횡보'
    if m60 is not None:
        if c > m20 > m60:
            return '상승'
        if c < m20 < m60:
            return '하락'
        return '횡보'
    chg = p.get('chg_20d_pct') or 0
    if c > m20 and chg > 0:
        return '상승'
    if c < m20 and chg < 0:
        return '하락'
    return '횡보'


def _consensus_signal(buy_pct: float, sell_pct: float) -> str:
    if buy_pct >= 50:
        return "강한 매수"
    if buy_pct >= 40:
        return "약한 매수"
    if sell_pct >= 50:
        return "강한 매도"
    if sell_pct >= 40:
        return "약한 매도"
    return "보합/관망"


def _rating_for(upside: Optional[float], buy_pct: float, sell_pct: float) -> str:
    """하우스 기준(RATING_POLICY)"""
    if upside is None:
        return "중립"
    diff = buy_pct - sell_pct
    if upside >= 15:
        rating = "매수"
    elif upside <= -10:
        rating = "매도"
    else:
        rating = "중립"
    if rating == "중립":
        if diff >= 20 and upside >= 5:
            rating = "매수"
        elif diff <= -20 and upside <= 0:
            rating = "매도"
    elif rating == "매수" and diff <= -20:
        rating = "중립"
    elif rating == "매도" and diff >= 20:
        rating = "중립"
    return rating


def _refs_text(ids: List[str]) -> str:
    return ' '.join(f"[{i}]" for i in ids)


# ═══════════════════════════════════════════════════════════════════ 검증 도구
class _Chk:
    """스키마 검증 오류 수집기 — 오류 메시지는 수정 재요청 프롬프트에 그대로 들어간다"""

    def __init__(self, fact_ids: set):
        self.errors: List[str] = []
        self.ids = fact_ids

    def e(self, msg: str):
        if msg not in self.errors:
            self.errors.append(msg)

    def refs_in(self, text: str, label: str):
        for m in _REF_RE.finditer(text or ''):
            rid = f"{m.group(1)}{m.group(2)}"
            if rid not in self.ids:
                self.e(f"'{label}'에 사실표에 없는 근거 ID [{rid}] 가 있음")

    def s(self, obj, key, label, lo=1, hi=None, required=True) -> Optional[str]:
        v = obj.get(key) if isinstance(obj, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        if v is None or (isinstance(v, str) and not v.strip()):
            if required:
                self.e(f"'{label}' 누락 (문자열 필요)")
                return ''
            return None
        if not isinstance(v, str):
            self.e(f"'{label}' 는 문자열이어야 함")
            return ''
        v = v.strip()
        if len(v) < lo:
            self.e(f"'{label}' 가 너무 짧음 ({len(v)}자 < {lo}자)")
        if hi and len(v) > hi:
            self.e(f"'{label}' 가 너무 김 ({len(v)}자 > {hi}자)")
        self.refs_in(v, label)
        return v

    def n(self, obj, key, label, required=True, lo=None, hi=None) -> Optional[float]:
        v = obj.get(key) if isinstance(obj, dict) else None
        if v is None:
            if required:
                self.e(f"'{label}' 누락 (숫자 필요)")
            return None
        fv = None if isinstance(v, bool) else _f(v)
        if fv is None:
            self.e(f"'{label}' 가 유한한 숫자가 아님: {str(v)[:30]}")
            return None
        if lo is not None and fv < lo:
            self.e(f"'{label}' 값 {fv:,.2f} 가 허용 범위 하한 {lo:,.2f} 미만")
        if hi is not None and fv > hi:
            self.e(f"'{label}' 값 {fv:,.2f} 가 허용 범위 상한 {hi:,.2f} 초과")
        return fv

    def enum(self, obj, key, label, options) -> Optional[str]:
        v = obj.get(key) if isinstance(obj, dict) else None
        v = v.strip() if isinstance(v, str) else v
        if v not in options:
            self.e(f"'{label}' 는 {'|'.join(options)} 중 하나여야 함 (받은 값: {str(v)[:20]})")
            return None
        return v

    def lst(self, obj, key, label, lo=0, hi=None) -> list:
        v = obj.get(key) if isinstance(obj, dict) else None
        if not isinstance(v, list):
            self.e(f"'{label}' 는 배열이어야 함")
            return []
        if len(v) < lo:
            self.e(f"'{label}' 항목 수 {len(v)}개 < 최소 {lo}개")
        if hi is not None and len(v) > hi:
            self.e(f"'{label}' 항목 수 {len(v)}개 > 최대 {hi}개")
        return v

    def strs(self, obj, key, label, lo=0, hi=None, min_len=2) -> List[str]:
        out = []
        for i, it in enumerate(self.lst(obj, key, label, lo, hi)):
            if not isinstance(it, str) or len(it.strip()) < min_len:
                self.e(f"'{label}[{i}]' 는 {min_len}자 이상 문자열이어야 함")
                continue
            self.refs_in(it, f"{label}[{i}]")
            out.append(it.strip())
        return out

    def evidence(self, obj, key, label, required=True) -> List[str]:
        v = obj.get(key) if isinstance(obj, dict) else None
        if v is None:
            v = []
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            self.e(f"'{label}' 는 근거 ID 배열이어야 함")
            return []
        out = []
        for it in v:
            m = re.search(r'([FRCKHfrckh])\s*(\d{1,3})', str(it))
            if not m:
                self.e(f"'{label}' 의 '{str(it)[:20]}' 는 근거 ID 형식(F1, R2)이 아님")
                continue
            rid = f"{m.group(1).upper()}{m.group(2)}"
            if rid not in self.ids:
                self.e(f"'{label}' 의 근거 ID {rid} 가 사실표에 없음")
            elif rid not in out:
                out.append(rid)
        if required and not out:
            self.e(f"'{label}' 에 사실표 근거 ID 가 1개 이상 필요")
        return out

    def cons_na(self, texts, has_cons: bool, label: str):
        """컨센서스 데이터가 있는데 '데이터 없음'으로 서술하면 오류"""
        if has_cons and any(t and _CONS_NA_RE.search(t) for t in texts):
            self.e(f"'{label}': 증권사 컨센서스(선행 EPS·목표주가 평균)가 제공됐으므로 '데이터 없음'으로 쓰면 안 됨")

    def target_mentions(self, text: str, target: Optional[float], label: str):
        """본문에서 '목표주가 N원' 언급이 확정 목표가와 다르면 오류"""
        if not text or target is None:
            return
        for m in _own_target_mentions(text):
            v = _f(m.group(1))
            if v and abs(v / target - 1) > 0.01:
                self.e(f"'{label}' 에 확정 목표주가({target:,.0f}원)와 다른 목표가 {v:,.0f}원 언급")


# ═══════════════════════════════════════════════════════════════════ 에이전트
class StockReportAgent:
    """
    주식 예측 보고서 생성 에이전트 — 증권사 리서치 리포트 템플릿.
    섹션별 JSON 스키마 생성 → 검증 → 수정 재요청 → 규칙 기반 대체.
    """

    # (key, 제목, 단계) — 표시 순서
    SECTION_SPECS = [
        ("investment_summary", "투자 요약", 2),
        ("investment_points", "투자 포인트", 1),
        ("earnings_analysis", "실적 분석", 1),
        ("valuation", "밸류에이션", 1),
        ("technical_analysis", "기술적 분석", 1),
        ("consensus_forecast", "시뮬레이션·컨센서스", 1),
        ("risk_factors", "리스크 요인", 1),
        ("investment_opinion", "투자의견", 2),
    ]
    # 하위 호환 (key, title) 목록
    REPORT_SECTIONS = [(k, t) for k, t, _ in SECTION_SPECS]

    SYSTEM_PROMPT = (
        "당신은 국내 대형 증권사 리서치센터의 시니어 애널리스트입니다. 제공된 계산값과 사실표(F#·R#·C#·H#)에만 근거해 "
        "증권사 리서치 리포트의 한 섹션을 작성합니다.\n"
        "규칙:\n"
        "1) 출력은 지정된 스키마의 JSON 객체 하나뿐입니다. 코드펜스·설명·주석을 붙이지 마세요.\n"
        "2) 모든 수치는 제공된 값을 그대로 쓰고, 제공되지 않은 수치(매출액·영업이익·순이익·증권사 추정치 등)는 절대 지어내지 마세요. "
        "없으면 문장에는 '데이터 없음'이라고 쓰고 숫자 필드는 null 로 둡니다.\n"
        "3) 근거를 인용할 때는 사실표 ID 를 [F1], [R2] 형식으로 본문에 표기하고 evidence 배열에도 넣습니다. 사실표에 없는 ID 는 쓰지 마세요.\n"
        "   H# 는 같은 종목의 과거 판단·실제 결과·이전 분석 ADR·같은 개념으로 이어진 종목 기록입니다. 인용할 때는 '과거 분석에서'처럼 "
        "시점을 밝히고 현재 사실처럼 쓰지 않습니다.\n"
        "4) 문체는 간결한 한국어 리서치 문체(예: '~로 판단한다', '~할 전망이다')입니다. '반드시·무조건' 같은 확정적 표현은 피합니다.\n"
        "5) 숫자 필드는 따옴표 없는 JSON number 로 씁니다."
    )

    def __init__(self):
        """StockReportAgent 초기화"""
        self._lock = threading.Lock()
        self._fast = None
        self._fast_name = None
        try:
            self.llm = LLMClient()
            self._llm_available = True
            logger.info(f"StockReportAgent 초기화 완료 (LLM 활성화 · 속도 {_report_speed()})")
        except Exception as e:
            logger.warning(f"LLMClient 초기화 실패 - LLM 없이 동작: {str(e)}")
            self.llm = None
            self._llm_available = False

    # ───────────────────────────────────────────────────────────── 모델 선택
    def _fast_llm(self):
        """빠른 비추론 모델 클라이언트 (xAI 일 때만 별도 모델, 아니면 기본 모델 그대로)"""
        if self._fast is not None:
            return self._fast
        with self._lock:
            if self._fast is not None:
                return self._fast
            fast = self.llm
            try:
                name = (os.environ.get('XAI_FAST_MODEL') or '').strip().split('/')[-1]
                if os.environ.get('LLM_PROVIDER_ACTIVE') == 'xai' and self.llm is not None:
                    if not name:
                        from .stock_knowledge_graph import StockKnowledgeGraph
                        kg = StockKnowledgeGraph()
                        prov = next((p for p in kg.judge.providers if p['name'] == 'xai'), None)
                        name = kg._fast_model(prov).split('/')[-1] if prov else ''
                    if name and name != self.llm.model:
                        fast = LLMClient(model=name)
                        self._fast_name = name
            except Exception as e:
                logger.warning(f"빠른 모델 준비 실패 — 기본 모델 사용: {e}")
            self._fast = fast
            return fast

    def _has_distinct_fast(self) -> bool:
        return self._fast_llm() is not self.llm

    def _tier(self, key: str) -> str:
        speed = _report_speed()
        if speed == 'quality':
            return 'reasoning'
        if speed == 'fast':
            return 'fast'
        return 'reasoning' if key in REASONING_SECTIONS else 'fast'

    def _client(self, tier: str):
        return self.llm if tier == 'reasoning' else self._fast_llm()

    # ───────────────────────────────────────────────────────────── 진입점
    def generate_report(
        self,
        simulation_id: str,
        ticker: str,
        company_name: str,
        simulation_data: Dict[str, Any],
        financial_data: Dict[str, Any],
        report_id: Optional[str] = None,
        progress_callback: Optional[Callable[[str, int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        주식 예측 보고서 생성

        Args:
            simulation_id: 시뮬레이션 ID
            ticker: 종목 코드 (예: "005930")
            company_name: 회사명 (예: "삼성전자")
            simulation_data: 시뮬레이션 결과(result.json)
            financial_data: 분석 결과(analysis.json)의 financial_data
            report_id: 보고서 ID (None이면 자동 생성)
            progress_callback: 진행 콜백 함수 (stage, progress, message)
        """
        import uuid

        if not report_id:
            report_id = f"stock_report_{uuid.uuid4().hex[:12]}"
        start_time = datetime.now()
        logger.info(f"[{report_id}] 보고서 생성 시작: {ticker} ({company_name})")

        def emit(stage, progress, message):
            if progress_callback:
                try:
                    progress_callback(stage, progress, message)
                except Exception as cb_err:  # 콜백 오류로 보고서가 실패하지 않도록
                    logger.warning(f"[{report_id}] progress_callback 오류: {cb_err}")

        report: Dict[str, Any] = {
            "report_id": report_id,
            "simulation_id": simulation_id,
            "ticker": ticker,
            "company_name": company_name,
            "status": StockReportStatus.GENERATING.value,
            "report_version": 2,
            "template": "securities_research",
            "sections": {},
            "markdown_content": "",
            "created_at": start_time.isoformat(),
            "completed_at": None,
            "error": None,
            "disclaimer": (
                "⚠️ 본 보고서는 AI 시뮬레이션 결과이며 투자 조언이 아닙니다. "
                "투자 결정은 반드시 전문가의 조언과 개인의 판단에 따라 이루어져야 합니다. "
                "본 보고서의 내용으로 인한 투자 손실에 대해 어떠한 책임도 지지 않습니다."
            )
        }

        try:
            emit("preparing", 5, "가격·재무·투표·리서치 데이터 집계 중...")
            aggregated = self._aggregate_simulation_data(simulation_data)
            market_context = self._extract_market_context(financial_data)
            research = None
            try:
                task_id = (simulation_data or {}).get('task_id') or ''
                research = load_research(task_id=task_id) if task_id else None
            except Exception as re_err:
                logger.warning(f"[{report_id}] research.json 로드 실패(무시): {re_err}")
            ds = self._build_dataset(ticker, company_name, simulation_data or {}, financial_data or {},
                                     aggregated, research)
            emit("preparing", 8,
                 f"사실표 {len(ds['facts'])}개 준비 (리서치 발견 {len(ds['findings'])}건 반영)"
                 if ds['findings'] else f"사실표 {len(ds['facts'])}개 준비 (리서치 데이터 없음)")

            results: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
            titles = {k: t for k, t, _ in self.SECTION_SPECS}
            total = len(self.SECTION_SPECS)
            done = [0]
            outer_sink = get_stream_sink()

            def finish(key, data, meta):
                content = self._render_section(key, data, ds)
                report["sections"][key] = {"title": titles[key], "content": content,
                                           "data": data, "validation": meta}
                done[0] += 1
                logger.info(f"[{report_id}] 섹션 완료: {titles[key]} ({len(content)}자, "
                            f"{meta['engine']}{'/' + meta['model'] if meta.get('model') else ''}, "
                            f"시도 {meta['attempts']}, {meta.get('sec', '?')}초)")
                emit("section_done", int(10 + done[0] / total * 80),
                     f"섹션 완료: {titles[key]} ({len(content):,}자) — {done[0]}/{total}")

            # ── 1차: 개별 분석 섹션 병렬 생성
            phase1 = [k for k, _, ph in self.SECTION_SPECS if ph == 1]
            emit("generating", 10, f"섹션 병렬 생성 중: {' · '.join(titles[k] for k in phase1)} "
                                   f"({len(phase1)}개, 1~{len(phase1)}/{total})")
            self._run_phase(phase1, ds, {}, outer_sink, results, finish, report_id, PHASE_BUDGET[1])

            # ── 목표주가·투자의견 확정
            fixed = self._fix_opinion(ds, results)
            ds['fixed'] = fixed
            phase2 = [k for k, _, ph in self.SECTION_SPECS if ph == 2]
            emit("generating", int(10 + done[0] / total * 80),
                 f"종합 섹션 생성 중: {' · '.join(titles[k] for k in phase2)} — 투자의견 {fixed['rating']}"
                 + (f", 목표주가 {fixed['target_price']:,.0f}원" if fixed.get('target_price') else ''))
            self._run_phase(phase2, ds, fixed, outer_sink, results, finish, report_id, PHASE_BUDGET[2])

            # 표시 순서로 정렬
            report["sections"] = {k: report["sections"][k] for k, _, _ in self.SECTION_SPECS
                                  if k in report["sections"]}

            emit("assembling", 92, "보고서 최종 조합 중 (요약·차트·출처)...")
            summary_data = (report["sections"].get("investment_summary") or {}).get("data") or {}
            report["summary"] = {
                "rating": fixed['rating'],
                "target_price": fixed.get('target_price'),
                "last_close": fixed.get('last_close'),
                "upside_pct": fixed.get('upside_pct'),
                "headline": summary_data.get('headline') or self._rule_headline(ds, fixed),
                "fair_value": fixed.get('fair_value'),
                "valuation_method": fixed.get('method_type'),
                "consensus_signal": aggregated['consensus_signal'],
                "as_of": ds['price'].get('as_of') or '',
                "rating_policy": RATING_POLICY,
                "consensus_target_mean": fixed.get('consensus_target'),
                "consensus_gap_pct": fixed.get('consensus_gap_pct'),
                "consensus_note": fixed.get('consensus_note'),
            }
            cs = ds['cons']
            report["consensus"] = dict(cs, gap_pct=fixed.get('consensus_gap_pct'), note=fixed.get('consensus_note'),
                                       fact_id=ds['fid'].get('cons')) if cs.get('has') else None
            report["broker_reports"] = [{k: b.get(k) for k in ('id', 'broker', 'title', 'date', 'url')} for b in ds['brokers']]
            report["prediction"] = self._prediction(aggregated, fixed)
            report["charts"] = self._build_charts(ds, fixed)
            report["facts"] = [{k: (v[:300] if isinstance(v, str) else v) for k, v in f.items()}
                               for f in ds['facts']]
            report["sources"] = self._sources(ds)
            report["research_used"] = bool(ds['findings'])
            if research and research.get('summary'):
                report["research_summary"] = self._remap_refs(str(research.get('summary')), ds)[:1200]
            engines = [s['validation']['engine'] for s in report["sections"].values()]
            report["generation"] = {
                "llm_sections": engines.count('llm'), "rule_sections": engines.count('rule'),
                "model": getattr(self.llm, 'model', None) if self.llm else None,
                "fast_model": self._fast_name, "speed": _report_speed(),
                "section_sec": {k: s['validation'].get('sec') for k, s in report["sections"].items()},
            }
            report["markdown_content"] = self._assemble_markdown(report, ds, aggregated, market_context)

            completed_at = datetime.now()
            report["status"] = StockReportStatus.COMPLETED.value
            report["completed_at"] = completed_at.isoformat()
            report["generation_time_seconds"] = (completed_at - start_time).total_seconds()
            emit("completed", 100, "보고서 생성 완료")
            logger.info(f"[{report_id}] 보고서 생성 완료: {ticker}, 소요 시간: "
                        f"{report['generation_time_seconds']:.1f}초 (LLM {engines.count('llm')} · 규칙 {engines.count('rule')})")

        except Exception as e:
            import traceback
            report["status"] = StockReportStatus.FAILED.value
            report["error"] = str(e)
            logger.error(f"[{report_id}] 보고서 생성 실패: {str(e)}\n{traceback.format_exc()}")
            emit("failed", 0, f"보고서 생성 실패: {str(e)}")

        return report

    # ───────────────────────────────────────────────────────────── 병렬 실행
    def _run_phase(self, keys, ds, fixed, outer_sink, results, finish, report_id, budget: float = 300.0):
        mux = _StreamMux(outer_sink) if outer_sink else None

        def work(key):
            if mux:
                set_stream_sink(mux.sink_for(key))
            try:
                return self._run_section(key, ds, fixed)
            finally:
                if mux:
                    set_stream_sink(None)

        workers = max(1, min(MAX_WORKERS, len(keys)))
        ex = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='report-sec')
        futs = {ex.submit(work, k): k for k in keys}
        try:
            for fut in as_completed(futs, timeout=budget):
                key = futs[fut]
                try:
                    data, meta = fut.result()
                except Exception as sec_err:  # 규칙 기반까지 실패한 경우에도 전체 보고서는 계속
                    logger.warning(f"[{report_id}] 섹션 생성 실패 ({key}): {sec_err}")
                    data = {"error": str(sec_err)}
                    meta = {"valid": False, "attempts": 0, "engine": "rule",
                            "errors": [f"섹션 생성 중 오류 발생: {str(sec_err)[:200]}"]}
                results[key] = (data, meta)
                finish(key, data, meta)
        except FuturesTimeout:
            # 감시 한도 초과 — 끝나지 않은 섹션은 기다리지 않고 규칙 기반으로 마무리 (늦게 끝난 결과는 버림)
            for key in keys:
                if key in results:
                    continue
                logger.warning(f"[{report_id}] 섹션 시간 한도({budget:.0f}초) 초과 → 규칙 기반: {key}")
                data, meta = self._rule_fallback(key, ds, fixed, [f"단계 시간 한도 {budget:.0f}초 초과"],
                                                 f"시간 한도 {budget:.0f}초 초과 — 규칙 기반으로 대체", time.time())
                results[key] = (data, meta)
                finish(key, data, meta)
        finally:
            if mux:
                mux.close()
            ex.shutdown(wait=False, cancel_futures=True)

    def _run_section(self, key: str, ds: Dict[str, Any], fixed: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        validate = getattr(self, f"_validate_{key}")
        finalize = getattr(self, f"_finalize_{key}", None)
        t0 = time.time()
        meta: Dict[str, Any] = {"valid": False, "attempts": 0, "engine": "rule", "errors": []}
        history: List[str] = []

        skip_reason = self._skip_llm_reason(key, ds)
        if skip_reason:
            return self._rule_fallback(key, ds, fixed, history, skip_reason, t0)
        if not (self._llm_available and self.llm):
            return self._rule_fallback(key, ds, fixed, history,
                                       "LLM 사용 불가 (API 키 없음 또는 크레딧·인증 오류)", t0)

        tier = self._tier(key)
        client = self._client(tier)
        deadline = t0 + _section_budget(tier)
        base_messages = [{"role": "system", "content": self.SYSTEM_PROMPT},
                         {"role": "user", "content": self._section_prompt(key, ds, fixed)}]
        messages = list(base_messages)
        switched = False
        reason = None
        attempt = 0
        while attempt < MAX_REPAIRS + 1:
            if not self._llm_available:
                reason = "LLM 사용 불가로 전환됨 (크레딧·인증 오류)"
                break
            remaining = deadline - time.time()
            if remaining < 12:
                if tier == 'reasoning' and not switched and self._has_distinct_fast():
                    history.append("추론 모델 시간 한도 도달 → 빠른 모델로 다시 작성")
                    tier, client, switched = 'fast', self._fast_llm(), True
                    deadline = time.time() + _section_budget('fast')
                    messages = list(base_messages)
                    continue
                reason = "시간 한도 초과 — 규칙 기반으로 대체"
                break
            attempt += 1
            meta["attempts"] = attempt
            try:
                raw = self._llm_raw(messages, client, timeout=remaining)
            except Exception as llm_err:
                err = str(llm_err)
                if is_timeout_error(llm_err):
                    history.append(f"시도 {attempt}: {'추론' if client is self.llm else '빠른'} 모델 응답 지연 — {err[:120]}")
                    if not switched and client is self.llm and self._has_distinct_fast():
                        tier, client, switched = 'fast', self._fast_llm(), True
                        deadline = time.time() + _section_budget('fast')
                        messages = list(base_messages)
                        continue
                    reason = "LLM 응답 지연 — 규칙 기반으로 대체"
                    break
                if any(k in err for k in _FATAL_MARKERS):
                    with self._lock:
                        self._llm_available = False
                    logger.warning(f"LLM 사용 불가 → 규칙 기반 보고서로 전환: {err[:200]}")
                history.append(f"시도 {attempt}: LLM 호출 오류 — {err[:160]}")
                reason = "LLM 호출 오류"
                break
            try:
                obj = repair_json(raw)
                if not isinstance(obj, dict):
                    raise ValueError("최상위가 JSON 객체가 아님")
            except (ValueError, TypeError) as pe:
                errors = [f"JSON 파싱 실패 — {str(pe)[:120]}"]
                clean = None
            else:
                clean, errors = validate(obj, ds, fixed)
            if not errors:
                data = finalize(clean, ds, fixed) if finalize else clean
                meta.update(valid=True, engine="llm", errors=history,
                            model='reasoning' if client is self.llm else 'fast',
                            sec=round(time.time() - t0, 1))
                return data, meta
            history.append(f"시도 {attempt}: " + " / ".join(errors[:8]))
            if attempt <= MAX_REPAIRS:
                messages = messages + [
                    {"role": "assistant", "content": (raw or '')[:4000]},
                    {"role": "user", "content": self._repair_prompt(errors)},
                ]
                # 수정 재요청은 빠른 모델로 — 오류 목록만 고치면 되므로 추론 모델 재호출보다 몇 배 빠름
                if client is self.llm and self._has_distinct_fast():
                    client = self._fast_llm()
                    deadline = max(deadline, time.time() + 45)
        else:
            reason = f"검증 실패 {MAX_REPAIRS + 1}회 — 규칙 기반으로 대체"
        return self._rule_fallback(key, ds, fixed, history, reason or "규칙 기반 작성", t0)

    def _rule_fallback(self, key: str, ds: Dict[str, Any], fixed: Dict[str, Any], history: List[str],
                       reason: str, t0: float) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        rule = getattr(self, f"_rule_{key}")
        validate = getattr(self, f"_validate_{key}")
        finalize = getattr(self, f"_finalize_{key}", None)
        rule_data = rule(ds, fixed)
        clean, errors = validate(rule_data, ds, fixed)
        if errors:
            logger.warning(f"규칙 기반 섹션 검증 경고 ({key}): {errors[:3]}")
            clean = rule_data
        data = finalize(clean, ds, fixed) if finalize else clean
        meta = {"valid": not errors, "engine": "rule", "attempts": len([h for h in history if h.startswith('시도')]),
                "fallback_reason": reason, "sec": round(time.time() - t0, 1),
                "errors": history + ([f"규칙 데이터 검증 경고: {' / '.join(errors[:4])}"] if errors else [])}
        return data, meta

    def _llm_raw(self, messages: List[Dict[str, str]], client=None, timeout: Optional[float] = None) -> str:
        cl = client or self.llm
        kw = dict(messages=messages, temperature=min(0.4, float(Config.REPORT_AGENT_TEMPERATURE)), max_tokens=2048,
                  timeout=timeout)
        try:
            return cl.chat(response_format={"type": "json_object"}, **kw)
        except Exception as e:
            if not is_timeout_error(e) and ('response_format' in str(e) or 'json_object' in str(e)):
                return cl.chat(**kw)   # JSON 모드 미지원 공급자
            raise

    @staticmethod
    def _repair_prompt(errors: List[str]) -> str:
        lines = "\n".join(f"- {e}" for e in errors[:12])
        return (f"직전 출력이 다음 검증을 통과하지 못했습니다:\n{lines}\n\n"
                "위 오류를 모두 고쳐, 처음 지시한 스키마에 맞는 JSON 객체 하나만 다시 출력하세요. "
                "제공된 수치만 사용하고 설명·코드펜스는 붙이지 마세요.")

    @staticmethod
    def _skip_llm_reason(key: str, ds: Dict[str, Any]) -> Optional[str]:
        if key in ('valuation', 'technical_analysis') and not ds['price'].get('has_data'):
            return "가격 데이터 없음 — 규칙 기반 작성"
        if key == 'earnings_analysis' and not ds['fund']:
            return "재무 데이터 없음 — 규칙 기반 작성"
        return None

    # ───────────────────────────────────────────────────────────── 데이터셋
    def _build_dataset(self, ticker, company_name, simulation_data, financial_data, aggregated, research) -> Dict[str, Any]:
        rows = _norm_rows(financial_data.get('ohlcv') or [])
        price = _price_stats(rows)
        closes = [r['c'] for r in rows]
        trend = _trend_label(price) if price.get('has_data') else '데이터 없음'

        # 펀더멘털 (최신 값 + 기간 이력)
        fund_rows = [r for r in (financial_data.get('fundamental') or []) if isinstance(r, dict)]
        fkeys = {
            'PER': ('PER', 'per'), 'PBR': ('PBR', 'pbr'), 'EPS': ('EPS', 'eps'), 'BPS': ('BPS', 'bps'),
            'DIV': ('DIV', 'div', 'div_yield'), 'DPS': ('DPS', 'dps'),
            '외인소진율': ('외인소진율', 'foreign_pct'), '52주최고': ('52주최고', 'high52'), '52주최저': ('52주최저', 'low52'),
            '선행EPS': ('선행EPS', 'fwd_eps'), '선행PER': ('선행PER', 'fwd_per'),
            '목표주가평균': ('목표주가평균', 'target_mean'), '투자의견평균': ('투자의견평균', 'recomm_mean'),
        }
        fund: Dict[str, float] = {}
        for name, keys in fkeys.items():
            for r in reversed(fund_rows):
                raw = _pick(r, keys)
                v = _f(raw) if name == 'DIV' or name == '외인소진율' else _pos(raw)
                if v is not None:
                    fund[name] = v
                    break
        hist = []
        for r in fund_rows:
            hist.append({'date': _norm_date(_pick(r, ('날짜', 'date', 'index'))),
                         'per': _pos(_pick(r, ('PER', 'per'))), 'pbr': _pos(_pick(r, ('PBR', 'pbr'))),
                         'eps': _f(_pick(r, ('EPS', 'eps'))), 'bps': _pos(_pick(r, ('BPS', 'bps')))})

        last = price.get('last_close')
        # 52주 밴드
        if fund.get('52주최고') and fund.get('52주최저') and fund['52주최저'] < fund['52주최고']:
            w52 = {'low': fund['52주최저'], 'high': fund['52주최고'], 'source': '네이버 증권 52주 최고·최저'}
        elif rows:
            recent = rows[-250:]
            w52 = {'low': min((r['l'] or r['c']) for r in recent), 'high': max((r['h'] or r['c']) for r in recent),
                   'source': f"수집 주가 {len(recent)}거래일 고가·저가"}
        else:
            w52 = {'low': None, 'high': None, 'source': '데이터 없음'}
        w52['avg'] = _r(_mean(closes[-250:]), 0) if closes else None
        if last and w52.get('low') and w52.get('high') and w52['high'] > w52['low']:
            w52['position_pct'] = round(_clip((last - w52['low']) / (w52['high'] - w52['low']) * 100, 0, 100), 1)
        else:
            w52['position_pct'] = None

        cons_date = next((_norm_date(_pick(r, ('컨센서스일', 'consensus_date'))) for r in reversed(fund_rows)
                          if _pick(r, ('컨센서스일', 'consensus_date'))), None)
        cons = self._consensus(fund, last, cons_date)
        qfin = financial_data.get('quarterly') if isinstance(financial_data.get('quarterly'), dict) else None
        earn = self._earnings(fund, hist, last, cons, qfin)
        votes = dict(aggregated)
        votes['by_type'] = self._votes_by_type(simulation_data)

        ds: Dict[str, Any] = {
            'ticker': ticker, 'company_name': company_name,
            'period': financial_data.get('period', '1y'),
            'rows': rows, 'closes': closes, 'price': price, 'trend': trend,
            'fund': fund, 'fund_hist': hist, 'w52': w52, 'earn': earn, 'votes': votes, 'cons': cons,
            'brokers': self._broker_reports(financial_data.get('broker_reports')),
            'research': research if isinstance(research, dict) else None,
            'qfin': qfin if (qfin or {}).get('latest') else None,
            'flows': financial_data.get('flows') if isinstance(financial_data.get('flows'), dict) else None,
            'short': financial_data.get('short') if isinstance(financial_data.get('short'), dict) else None,
        }
        # 사실표 확장: 분기 실적·수급·공매도·몬테카를로·같은 종목의 과거 기록
        ext = {k: ds[k] for k in ('flows', 'short') if ds.get(k)}
        if ds['qfin']:
            ext['quarterly'] = ds['qfin']
        mc = (simulation_data or {}).get('monte_carlo')
        if isinstance(mc, dict) and mc.get('direction_probs'):
            ext['monte_carlo'] = mc
            ds['mc'] = mc
        try:
            from . import ontology_store
            ext['history'] = ontology_store.history_facts(ticker)
        except Exception as h_err:
            logger.debug(f"과거 기록 사실 생략: {h_err}")
        ds['ext'] = ext
        ds['val'] = self._rule_valuation_calc(ds)
        ds['tech'] = self._tech_levels(ds)
        self._facts(ds)
        return ds

    @staticmethod
    def _consensus(fund, last, date) -> Dict[str, Any]:
        fwd_eps, fwd_per = fund.get('선행EPS'), fund.get('선행PER')
        fwd_per_derived = False
        if fwd_per is None and fwd_eps and last:
            fwd_per, fwd_per_derived = round(last / fwd_eps, 2), True
        tm, rm = fund.get('목표주가평균'), fund.get('투자의견평균')
        return {
            'has': any(x is not None for x in (fwd_eps, tm, rm)),
            'fwd_eps': fwd_eps, 'fwd_per': fwd_per, 'fwd_per_derived': fwd_per_derived,
            'target_mean': tm, 'upside_pct': round((tm / last - 1) * 100, 1) if (tm and last) else None,
            'recomm_mean': rm, 'recomm_label': _recomm_label(rm), 'date': date or None,
            'implied_per': round(tm / fwd_eps, 2) if (tm and fwd_eps) else None,
        }

    @staticmethod
    def _broker_reports(raw) -> List[Dict[str, Any]]:
        out = []
        for b in raw if isinstance(raw, list) else []:
            if not isinstance(b, dict) or not str(b.get('title') or '').strip():
                continue
            url = b.get('url') if isinstance(b.get('url'), str) and re.match(r'^https?://[^\s<>"]+$', b.get('url')) else None
            out.append({'broker': str(b.get('broker') or '증권사')[:30], 'title': str(b.get('title')).strip()[:120],
                        'date': _norm_date(b.get('date')) or None, 'url': url, 'report_no': b.get('id')})
        out.sort(key=lambda x: x['date'] or '', reverse=True)
        return out[:6]

    @staticmethod
    def _earnings(fund, hist, last, cons=None, qfin=None) -> Dict[str, Any]:
        eps = fund.get('EPS')
        if eps is None:
            eps_raw = next((h['eps'] for h in reversed(hist) if h.get('eps') not in (None, 0)), None)
            eps = eps_raw
        eps_derived = False
        if eps is None and fund.get('PER') and last:
            eps, eps_derived = round(last / fund['PER'], 0), True
        eps_series = [h['eps'] for h in hist if h.get('eps') not in (None, 0)]
        trend, chg, first = '데이터 없음', None, None
        distinct = []
        for v in eps_series:
            if not distinct or v != distinct[-1]:
                distinct.append(v)
        if len(distinct) >= 2 and distinct[0] != 0:
            first = distinct[0]
            chg = round((distinct[-1] / first - 1) * 100 * (1 if first > 0 else -1), 1)
            trend = '증가' if chg > 3 else '감소' if chg < -3 else '유지'
        bps, bps_derived = fund.get('BPS'), False
        if bps is None and fund.get('PBR') and last:
            bps, bps_derived = round(last / fund['PBR'], 0), True
        div = fund.get('DIV')
        dps, dps_derived = fund.get('DPS'), False
        if dps is None and div is not None and last:
            dps, dps_derived = round(last * div / 100, 0), True
        roe = round(eps / bps * 100, 1) if (eps is not None and bps) else None
        cons = cons or {}
        fwd_eps = cons.get('fwd_eps')
        fwd_growth = round((fwd_eps / eps - 1) * 100, 1) if (fwd_eps and eps and eps > 0) else None
        unavailable = [u for u in UNAVAILABLE_BASE if not (cons.get('has') and '컨센서스' in u)]
        q = qfin if (qfin or {}).get('latest') else None
        if q:     # 분기 손익을 수집했으면 매출·영업이익·순이익·분기 추이는 '없는 데이터'가 아님
            unavailable = [u for u in unavailable if u not in ('매출액', '영업이익', '순이익', '분기별 실적 추이')]
            if q.get('next_estimate'):
                unavailable = [u for u in unavailable if '컨센서스' not in u]
        if eps is None:
            unavailable.append('EPS')
        if div is None:
            unavailable.append('배당수익률')
        if bps is None:
            unavailable.append('BPS')
        return {'eps': eps, 'eps_derived': eps_derived, 'eps_trend': trend, 'eps_first': first, 'eps_change_pct': chg,
                'bps': bps, 'bps_derived': bps_derived, 'div_yield': div, 'dps': dps, 'dps_derived': dps_derived,
                'roe': roe, 'fwd_eps': fwd_eps, 'fwd_growth_pct': fwd_growth, 'unavailable': unavailable,
                'q': ({'period': q['latest']['period'], 'source': q.get('source'),
                       'revenue': q['latest'].get('revenue'), 'op_income': q['latest'].get('op_income'),
                       'net_income': q['latest'].get('net_income'), 'op_margin': q['latest'].get('op_margin'),
                       'revenue_yoy_pct': q.get('revenue_yoy_pct'), 'op_income_yoy_pct': q.get('op_income_yoy_pct'),
                       'net_income_yoy_pct': q.get('net_income_yoy_pct'), 'revenue_qoq_pct': q.get('revenue_qoq_pct'),
                       'op_income_qoq_pct': q.get('op_income_qoq_pct'), 'yoy_base': q.get('yoy_base'),
                       'qoq_base': q.get('qoq_base'), 'ttm': q.get('ttm'), 'ttm_periods': q.get('ttm_periods'),
                       'next_estimate': q.get('next_estimate'), 'quarters': q.get('quarters') or [],
                       'op_loss_quarters': q.get('op_loss_quarters')} if q else None)}

    @staticmethod
    def _votes_by_type(sim: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {}
        for d in sim.get('agents_decisions') or []:
            if not isinstance(d, dict):
                continue
            t = str(d.get('agent_type') or 'unknown')
            dec = d.get('decision')
            if dec not in ('buy', 'hold', 'sell'):
                continue
            out.setdefault(t, {'buy': 0, 'hold': 0, 'sell': 0})[dec] += 1
        order = ['aggressive', 'moderate', 'conservative']
        return {k: out[k] for k in sorted(out, key=lambda x: order.index(x) if x in order else 9)}

    def _outlook_score(self, ds) -> float:
        v = ds['votes']
        s = _clip((v['buy_pct'] - v['sell_pct']) / 100 * 2, -1, 1) * 0.5
        s += {'상승': 0.3, '하락': -0.3}.get(ds['trend'], 0.0)
        r = ds.get('research') or {}
        ov = _f(((r.get('sentiment') or {}).get('overall_score')))
        if ov is not None:
            s += _clip(ov, -1, 1) * 0.2
        return round(_clip(s, -1, 1), 2)

    def _rule_valuation_calc(self, ds) -> Dict[str, Any]:
        """규칙 기반 밸류에이션: PER/PBR 밴드(이력이 있으면 실제 밴드, 없으면 52주 주가 ÷ 현재 EPS·BPS 내재 밴드)"""
        last = ds['price'].get('last_close')
        e, w = ds['earn'], ds['w52']
        out: Dict[str, Any] = {'last_close': last, 'per_band': None, 'pbr_band': None,
                               'band_52w': {k: w.get(k) for k in ('low', 'avg', 'high')},
                               'eps': e['eps'], 'bps': e['bps']}
        if not last:
            out.update(method_type='데이터 부족', fair_value=None, target_price=None, upside_pct=None,
                       target_multiple=None, outlook_score=0.0, percentile=0.5, clamped=False,
                       method="가격 데이터가 없어 목표주가를 산정하지 않았다.")
            return out

        def band(series, base, kind):
            vals = [x for x in series if x]
            if len(vals) >= 20 and (max(vals) - min(vals)) / max(vals) > 0.01:
                lo, mid, hi = _percentile(vals, 0.05), _percentile(vals, 0.5), _percentile(vals, 0.95)
                src = f"{len(vals)}거래일 {kind} 이력(5·50·95 분위)"
            elif w.get('low') and w.get('high') and w.get('avg'):
                lo, mid, hi = w['low'] / base, w['avg'] / base, w['high'] / base
                src = f"52주 주가 ÷ 현재 {'EPS' if kind == 'PER' else 'BPS'} 내재 {kind}"
            else:
                return None
            return {'low': round(lo, 2), 'mid': round(mid, 2), 'high': round(hi, 2),
                    'price_low': round(lo * base, 0), 'price_mid': round(mid * base, 0), 'price_high': round(hi * base, 0),
                    'base': base, 'source': src}

        if e['eps'] and e['eps'] > 0:
            out['per_band'] = band([h['per'] for h in ds['fund_hist']], e['eps'], 'PER')
        if e['bps'] and e['bps'] > 0:
            out['pbr_band'] = band([h['pbr'] for h in ds['fund_hist']], e['bps'], 'PBR')
        # 두 밴드 모두 52주 주가에서 역산한 내재 밴드면 적용 주가가 같으므로 PBR 은 생략 (중복 방지)
        if out['per_band'] and out['pbr_band'] and '내재' in out['per_band']['source'] and '내재' in out['pbr_band']['source']:
            out['pbr_band'] = None
        s = self._outlook_score(ds)
        q = round(0.5 + 0.35 * s, 3)   # 전망 점수 −1~+1 → 밴드 15~85% 분위
        bands = [(k, b) for k, b in (('PER', out['per_band']), ('PBR', out['pbr_band'])) if b]
        if bands:
            fair = _mean([b['price_mid'] for _, b in bands])
            tgt = _mean([_band_at(b['price_low'], b['price_mid'], b['price_high'], q) for _, b in bands])
            mtype = bands[0][0] if len(bands) == 1 else '혼합'
        elif w.get('low') and w.get('high') and w.get('avg'):
            fair = w['avg']
            tgt = _band_at(w['low'], w['avg'], w['high'], q)
            mtype = '가격밴드'
        else:
            fair, tgt, mtype = last, last, '가격밴드'
        raw_t = tgt
        tgt = _clip(tgt, last * 0.7, last * 1.5)
        tgt, fair = _round_price(tgt), _round_price(fair)
        mult = None
        if mtype == 'PER':
            mult = round(tgt / e['eps'], 2)
        elif mtype == 'PBR':
            mult = round(tgt / e['bps'], 2)
        desc = {
            'PER': f"PER 밴드 방식: 목표 PER {_mult(mult)}(밴드 {q * 100:.0f}% 분위) × EPS {_won(e['eps'])}",
            'PBR': f"PBR 밴드 방식: 목표 PBR {_mult(mult)}(밴드 {q * 100:.0f}% 분위) × BPS {_won(e['bps'])}",
            '혼합': f"PER·PBR 밴드 혼합: 두 밴드의 {q * 100:.0f}% 분위 적용 주가를 평균",
            '가격밴드': f"52주 가격 밴드 방식(재무 데이터 없음): 밴드 {q * 100:.0f}% 분위 주가",
        }[mtype]
        trailing = dict(method_type=mtype, fair_value=fair, target_price=tgt,
                        upside_pct=round((tgt / last - 1) * 100, 1), target_multiple=mult,
                        clamped=abs(raw_t - tgt) / last > 0.01, method=desc)
        out.update(trailing, outlook_score=s, percentile=q, trailing=dict(trailing), forward=None, fwd_band=None)
        fwd = self._forward_valuation(ds, out, s)
        if fwd:
            out.update(fwd)   # 선행 EPS 가 있으면 선행 PER 이 1차 방식, 후행 밴드는 참고
        return out

    def _forward_valuation(self, ds, out, s) -> Optional[Dict[str, Any]]:
        """
        선행 PER 규칙 (컨센서스 선행 EPS > 0 일 때):
          기준 PER  = (현재 후행 PER + 1년 평균 후행 PER[밴드 중앙]) ÷ 2   (둘 중 하나만 있으면 그 값,
                      둘 다 없으면 현재 선행 PER)
          사이클 할인 = clip((선행 EPS 성장률 − 50%) × 0.2, 0, 20%)   — 이익 급증기 고점 배수 경계
          적정 PER  = 기준 PER × (1 − 사이클 할인)          → 적정주가 = 적정 PER × 선행 EPS
          목표 PER  = 적정 PER × (1 + 0.1 × 전망 점수)       (전망 점수 −1~+1 → ±10%)
          두 배수 모두 후행 PER 밴드 하단~상단 안으로 제한, 목표주가는 현재가 0.6~1.8배로 제한
        """
        c = ds['cons']
        fwd, last = c.get('fwd_eps'), out.get('last_close')
        if not (fwd and fwd > 0 and last):
            return None
        e, f = ds['earn'], ds['fund']
        eps_t = e['eps'] if (e['eps'] and e['eps'] > 0) else None
        m_now = f.get('PER') or (round(last / eps_t, 2) if eps_t else None)
        pb = out.get('per_band')
        m_hist = pb['mid'] if pb else None
        refs = [m for m in (m_now, m_hist) if m]
        if refs:
            base = sum(refs) / len(refs)
            base_src = ('현재 후행 PER과 1년 평균 PER의 중간' if len(refs) == 2 else
                        '현재 후행 PER' if m_now else '1년 평균 PER')
        else:
            base, base_src = last / fwd, '현재 선행 PER(후행 PER 산출 불가)'
        g = e.get('fwd_growth_pct')
        disc = _clip((g / 100 - 0.5) * 0.2, 0.0, 0.2) if g is not None else 0.0
        fair_per = base * (1 - disc)
        tper = fair_per * (1 + 0.1 * s)
        if pb:
            fair_per = _clip(fair_per, pb['low'], pb['high'])
            tper = _clip(tper, pb['low'], pb['high'])
        raw_t = tper * fwd
        tgt = _round_price(_clip(raw_t, last * 0.6, last * 1.8))
        fair = _round_price(fair_per * fwd)
        mult = round(tgt / fwd, 2)
        tm = c.get('target_mean')
        gap = round((tgt / tm - 1) * 100, 1) if tm else None
        method = (f"선행 PER 방식: 목표 PER {mult:.2f}배 × 선행 EPS {_won(fwd)} "
                  f"(기준 {base:.2f}배 = {base_src}"
                  + (f", 이익 급증 사이클 할인 {disc * 100:.0f}%" if disc > 0 else '')
                  + f", 전망 점수 {s:+.2f} 반영)")
        fwd_band = None
        if pb:
            fwd_band = {'low': round(pb['low'] * fwd), 'mid': round(pb['mid'] * fwd), 'high': round(pb['high'] * fwd),
                        'multiple_low': pb['low'], 'multiple_mid': pb['mid'], 'multiple_high': pb['high'],
                        'source': f"선행 EPS × 후행 PER 밴드({pb['source']})"}
        lo = _round_price(max(last * 0.5, tgt * (1 - FWD_LLM_BAND)))
        hi = _round_price(min(last * 2.0, tgt * (1 + FWD_LLM_BAND)))
        return {
            'method_type': '선행PER', 'fair_value': fair, 'target_price': tgt,
            'upside_pct': round((tgt / last - 1) * 100, 1), 'target_multiple': mult,
            'clamped': abs(raw_t - tgt) / last > 0.01, 'method': method, 'fwd_band': fwd_band,
            'forward': {'fwd_eps': fwd, 'trailing_eps': eps_t, 'growth_pct': g, 'per_now': m_now, 'per_hist_mid': m_hist,
                        'base_per': round(base, 2), 'base_source': base_src, 'cycle_discount_pct': round(disc * 100, 1),
                        'fair_per': round(fair_per, 2), 'target_per': mult, 'fwd_per_now': c.get('fwd_per'),
                        'consensus_target': tm, 'consensus_gap_pct': gap, 'consensus_implied_per': c.get('implied_per'),
                        'llm_range': [lo, hi], 'rule_target': tgt},
        }

    @staticmethod
    def _tech_levels(ds) -> Dict[str, Any]:
        p = ds['price']
        last = p.get('last_close')
        if not last:
            return {'support': None, 'resistance': None}
        levels = [x for x in (p.get('ma20'), p.get('ma60'), p.get('low_60d'), p.get('high_60d'),
                              ds['w52'].get('low'), ds['w52'].get('high')) if x]
        below = [x for x in levels if x <= last]
        above = [x for x in levels if x >= last]
        support = max(below) if below else min(levels + [last])
        resistance = min(above) if above else max(levels + [last])
        if support >= last * 0.995:          # 현재가와 사실상 같으면 한 단계 아래/위 레벨 사용
            lower = [x for x in below if x < last * 0.995]
            support = max(lower) if lower else last * 0.95
        if resistance <= last * 1.005:
            upper = [x for x in above if x > last * 1.005]
            resistance = min(upper) if upper else last * 1.05
        support = _clip(support, last * 0.6, last)
        resistance = _clip(resistance, last, last * 1.4)
        return {'support': _round_price(support), 'resistance': _round_price(resistance)}

    def _facts(self, ds):
        """stock_evidence.build_fact_sheet(공통 사실표) + 리포트 전용 F# 추가, 역할→ID 매핑"""
        p, v = ds['price'], ds['votes']
        ctx = {'price': p, 'votes': {k: v.get(k) for k in ('num_agents', 'buy_pct', 'hold_pct', 'sell_pct')}}
        fund_in = {k: ds['fund'][k] for k in ('PER', 'PBR', 'EPS', 'DIV', '외인소진율', '52주최고', '52주최저')
                   if ds['fund'].get(k) is not None}
        facts = build_fact_sheet(ctx, ds['research'], fundamentals=fund_in or None, max_research=16,
                                 extended=ds.get('ext'))
        fid: Dict[str, str] = {}
        price_roles = iter(['price_level', 'returns', 'rsi_vol', 'range60'])
        src_map: Dict[str, str] = {}
        findings_by_orig = {str(it.get('id')): it for it in ((ds['research'] or {}).get('findings') or [])
                            if isinstance(it, dict)}
        findings = []
        for f in facts:
            if f['kind'] == 'price':
                fid[next(price_roles, 'price_x')] = f['id']
            elif f['kind'] in ('fund', 'vote'):
                fid[f['kind']] = f['id']
            elif f['kind'] in ('earnings', 'flow', 'short', 'mc'):
                fid[f.get('role') or f['kind']] = f['id']
            elif f['kind'] == 'history':
                fid.setdefault('history_ids', [])
                fid['history_ids'].append(f['id'])
            elif f['kind'] == 'research':
                if f.get('ref') is not None:
                    src_map[str(f['ref'])] = f['id']
                    it = findings_by_orig.get(str(f['ref'])) or {}
                    src = it.get('source') or {}
                    findings.append({
                        'id': f['id'], 'orig': f.get('ref'), 'claim': str(it.get('claim') or '')[:300],
                        'kind': it.get('kind'), 'topic': it.get('topic'), 'importance': it.get('importance'),
                        'sentiment': (it.get('sentiment') or {}).get('label'),
                        'score': _f((it.get('sentiment') or {}).get('score')),
                        'date': it.get('date'), 'url': src.get('url'), 'title': src.get('title'),
                        'publisher': src.get('publisher'),
                    })
                else:
                    fid['news_sent'] = f['id']
            elif f['kind'] == 'community':
                fid['community'] = f['id']

        def add(role, text, **kw):
            n = sum(1 for x in facts if x['id'].startswith('F')) + 1
            facts.append({'id': f'F{n}', 'kind': kw.pop('kind', 'report'), 'text': text,
                          **{k: v for k, v in kw.items() if v is not None}})
            fid[role] = f'F{n}'
            return f'F{n}'

        w, e, val = ds['w52'], ds['earn'], ds['val']
        if w.get('low') and w.get('high'):
            add('band52', f"52주 가격 범위 {_won(w['low'])} ~ {_won(w['high'])} · 평균 {_won(w.get('avg'))} · "
                          f"현재가 위치 {w.get('position_pct') if w.get('position_pct') is not None else '-'}% ({w['source']})")
        c = ds['cons']
        if c.get('has'):
            cb = []
            if c.get('target_mean'):
                cb.append(f"목표주가 평균 {_won(c['target_mean'])} (현재가 대비 {_pct(c['upside_pct'])})")
            if c.get('recomm_mean') is not None:
                cb.append(f"투자의견 평균 {c['recomm_mean']:.2f}/5 ({c['recomm_label']})")
            if c.get('fwd_eps'):
                cb.append(f"선행 EPS {_won(c['fwd_eps'])}")
            if c.get('fwd_per'):
                cb.append(f"선행 PER {c['fwd_per']:.2f}배{' (현재가÷선행 EPS 산출)' if c['fwd_per_derived'] else ''}")
            if c.get('implied_per'):
                cb.append(f"목표주가 평균 내재 선행 PER {c['implied_per']:.2f}배")
            add('cons', f"증권사 컨센서스{' (' + c['date'] + ')' if c.get('date') else ''}: " + ' · '.join(cb),
                kind='consensus', date=c.get('date'))
        for i, b in enumerate(ds['brokers']):
            b['id'] = add(f'broker{i}', f"증권사 리포트: {b['broker']} '{b['title']}' ({b.get('date') or '날짜 없음'})",
                          kind='broker_report', url=b.get('url'), date=b.get('date'), publisher=b['broker'])
        bits = []
        if e['eps'] is not None:
            bits.append(f"EPS {_won(e['eps'])}{' (PER로 산출)' if e['eps_derived'] else ''}"
                        + (f" · 기간 추이 {e['eps_trend']} ({_won(e['eps_first'])}→{_won(e['eps'])}, {_pct(e['eps_change_pct'])})"
                           if e['eps_change_pct'] is not None else ''))
        if e['fwd_eps'] is not None:
            bits.append(f"선행 EPS(컨센서스) {_won(e['fwd_eps'])}"
                        + (f" · 선행 EPS 성장률 {_pct(e['fwd_growth_pct'])} (선행 EPS÷EPS−1)" if e['fwd_growth_pct'] is not None else ''))
        if e['bps'] is not None:
            bits.append(f"BPS {_won(e['bps'])}{' (현재가÷PBR 산출)' if e['bps_derived'] else ''}")
        if e['roe'] is not None:
            bits.append(f"ROE {e['roe']:.1f}% (EPS÷BPS 산출)")
        if e['dps'] is not None:
            bits.append(f"주당배당금 {_won(e['dps'])}{' (현재가×배당수익률 산출)' if e['dps_derived'] else ''}")
        if bits:
            add('earn', ' · '.join(bits))
        if val.get('forward'):
            fw, tr = val['forward'], val.get('trailing') or {}
            add('val', f"밸류에이션(규칙): {val['method']} → 적정주가 {_won(val['fair_value'])}, 규칙 목표가 "
                       f"{_won(val['target_price'])} (상승여력 {_pct(val['upside_pct'])})"
                       + (f" · 컨센서스 평균 대비 {_pct(fw['consensus_gap_pct'])}" if fw.get('consensus_gap_pct') is not None else '')
                       + (f" · 참고: 후행 {METHOD_LABEL.get(tr.get('method_type'), '')} 방식 산출값 {_won(tr.get('target_price'))}"
                          if tr.get('target_price') else ''))
        elif val.get('target_price'):
            bb = []
            for k in ('per_band', 'pbr_band'):
                b = val.get(k)
                if b:
                    bb.append(f"{k[:3].upper()} 밴드 {b['low']:.2f}~{b['high']:.2f}배(중앙 {b['mid']:.2f}배, {b['source']})")
            add('val', ' · '.join(bb + [f"밴드 중앙 적정가 {_won(val['fair_value'])}",
                                         f"규칙 목표가 {_won(val['target_price'])} (상승여력 {_pct(val['upside_pct'])}, "
                                         f"전망 점수 {val['outlook_score']:+.2f})"]))
        if v.get('by_type'):
            parts = []
            for t, c in v['by_type'].items():
                parts.append(f"{AGENT_TYPE_KO.get(t, t)} 매수 {c['buy']}·보합 {c['hold']}·매도 {c['sell']}")
            add('types', "에이전트 유형별 투표(명): " + ' / '.join(parts))
        if p.get('avg_volume_20d'):
            add('volume', f"거래량: 최근 5일 평균 {_won(p.get('avg_volume_5d'), '주')} · 20일 평균 "
                          f"{_won(p.get('avg_volume_20d'), '주')} (5일/20일 {p.get('vol_ratio_5_20') or '-'}배)")

        ds['facts'] = facts
        ds['fact_ids'] = {f['id'] for f in facts}
        ds['fid'] = fid
        ds['src_map'] = src_map
        ds['findings'] = findings

    def ev(self, ds, *roles) -> List[str]:
        return [ds['fid'][r] for r in roles if ds['fid'].get(r)]

    def _remap_refs(self, text: str, ds) -> str:
        """research.json 의 원래 [R#] 를 리포트 사실표 ID 로 변환 (없는 ID 는 제거)"""
        def rep(m):
            new = ds['src_map'].get(f"R{m.group(2)}") if m.group(1) == 'R' else None
            return f"[{new}]" if new else ''
        return re.sub(r'\[(R)\s*(\d{1,3})\]', rep, text or '').strip()

    def _research_items(self, ds, key) -> List[Dict[str, Any]]:
        out = []
        for it in ((ds['research'] or {}).get(key) or [])[:6]:
            if not isinstance(it, dict) or not it.get('text'):
                continue
            refs = [ds['src_map'][str(r)] for r in (it.get('refs') or []) if str(r) in ds['src_map']]
            out.append({'text': self._remap_refs(str(it['text']), ds)[:300], 'refs': refs})
        return out

    # ───────────────────────────────────────────────────────────── 프롬프트
    def _context_text(self, ds) -> str:
        p, e, v, w, val, t = ds['price'], ds['earn'], ds['votes'], ds['w52'], ds['val'], ds['tech']
        lines = [f"[종목] {ds['company_name']} ({ds['ticker']}) · 기준일 {p.get('as_of') or '-'} · 분석 기간 {ds['period']}"]
        if p.get('has_data'):
            lines.append(
                f"[가격] 현재가 {_won(p['last_close'])} · MA5 {_won(p.get('ma5'))} · MA20 {_won(p.get('ma20'))} · "
                f"MA60 {_won(p.get('ma60'))} · 5/20/60일 수익률 {_pct(p.get('chg_5d_pct'), nd=2)}/{_pct(p.get('chg_20d_pct'), nd=2)}/"
                f"{_pct(p.get('chg_60d_pct'), nd=2)} · RSI(14) {p.get('rsi14') if p.get('rsi14') is not None else '데이터 없음'} · "
                f"20일 일간 변동성 {_pct(p.get('daily_vol_20d_pct'), signed=False, nd=2)} · 추세 판정 {ds['trend']}")
            lines.append(f"[52주 밴드] 최저 {_won(w.get('low'))} · 평균 {_won(w.get('avg'))} · 최고 {_won(w.get('high'))} · "
                         f"현재 위치 {w.get('position_pct')}% · 참고 지지 {_won(t.get('support'))} / 저항 {_won(t.get('resistance'))}")
        else:
            lines.append("[가격] 데이터 없음")
        f = ds['fund']
        lines.append(
            f"[재무] PER {_mult(f.get('PER'))} · PBR {_mult(f.get('PBR'))} · EPS {_won(e['eps'])}{'(산출)' if e['eps_derived'] else ''} "
            f"(추이 {e['eps_trend']}) · BPS {_won(e['bps'])}{'(산출)' if e['bps_derived'] else ''} · ROE "
            f"{_pct(e['roe'], signed=False) if e['roe'] is not None else '데이터 없음'}{'(산출)' if e['roe'] is not None else ''} · "
            f"배당수익률 {_pct(e['div_yield'], signed=False, nd=2) if e['div_yield'] is not None else '데이터 없음'} · "
            f"DPS {_won(e['dps'])}{'(산출)' if e['dps_derived'] else ''}")
        lines.append(f"[에이전트 시뮬레이션] {v['num_agents']}명 · 매수 {v['buy_pct']}% / 보합 {v['hold_pct']}% / 매도 {v['sell_pct']}% · "
                     f"합의 신호 {v['consensus_signal']} · 평균 확신도 {v['avg_confidence']}")
        c = ds['cons']
        if c.get('has'):
            lines.append(
                f"[증권사 컨센서스 {c.get('date') or ''}] 목표주가 평균 {_won(c.get('target_mean'))} (현재가 대비 {_pct(c.get('upside_pct'))}) · "
                f"투자의견 평균 {c['recomm_label'] or '-'}({c['recomm_mean'] if c.get('recomm_mean') is not None else '-'}/5) · "
                f"선행 EPS {_won(c.get('fwd_eps'))} · 선행 PER {_mult(c.get('fwd_per'))} · 선행 EPS 성장률 {_pct(e.get('fwd_growth_pct'))}"
                f" [{ds['fid'].get('cons')}] — 컨센서스 데이터가 있으므로 '데이터 없음'이라고 쓰지 말 것")
        if ds['brokers']:
            lines.append("[증권사 리포트] " + " / ".join(f"{b['broker']} '{b['title']}' ({b.get('date') or '-'}) [{b['id']}]"
                                                         for b in ds['brokers']))
        if val.get('target_price'):
            lines.append(f"[규칙 기반 밸류에이션 참고] {val['method']} → 적정가 {_won(val['fair_value'])}, 목표가 "
                         f"{_won(val['target_price'])} (상승여력 {_pct(val['upside_pct'])})"
                         + (f" · 후행 밴드 방식 참고값 {_won((val.get('trailing') or {}).get('target_price'))}" if val.get('forward') else ''))
        lines.append(f"[없는 데이터 — 지어내지 말 것] {', '.join(e['unavailable'])}")
        lines.append("\n[사실표 — 인용 가능한 ID]\n" + fact_sheet_text(ds['facts'], limit=6500))
        r = ds['research'] or {}
        if r.get('summary'):
            lines.append("\n[리서치 요약] " + self._remap_refs(str(r['summary']), ds)[:900])
        for label, key in (("리서치 핵심", 'key_points'), ("리서치 리스크", 'risks')):
            items = self._research_items(ds, key)
            if items:
                lines.append(f"[{label}] " + " / ".join(f"{it['text']} {_refs_text(it['refs'])}".strip() for it in items))
        return "\n".join(lines)

    def _section_prompt(self, key: str, ds: Dict[str, Any], fixed: Dict[str, Any]) -> str:
        title = dict((k, t) for k, t, _ in self.SECTION_SPECS)[key]
        last = ds['price'].get('last_close')
        e, val, t, v = ds['earn'], ds['val'], ds['tech'], ds['votes']
        num = lambda x: 'null' if x is None else (f"{x:.2f}" if abs(x) < 1000 else f"{x:.0f}")  # noqa: E731
        fid = ds.get('fid') or {}
        h_ids = fid.get('history_ids') or []
        ext_hint = ''.join([
            f" 투자자별 순매수 사실 [{fid['flows']}] 이 있으면 수급 관점에 활용한다." if fid.get('flows') else '',
            f" 공매도 잔고 [{fid['short']}] 이 있으면 참고한다." if fid.get('short') else '',
            f" 몬테카를로 분포 [{fid['mc']}] 가 있으면 기간 수익률 범위·하방 위험의 근거로 쓸 수 있다." if fid.get('mc') else '',
            (f" 과거 기록 [{'], ['.join(h_ids[:5])}] 이 있으면 이전 판단이 맞았는지·이전 ADR 이 여전히 유효한지를 한 번 짚는다."
             if h_ids else ''),
        ])
        spec = {
            'investment_points': (
                "서로 다른 관점(예: 추세·모멘텀 / 밸류에이션 / 수급·뉴스·에이전트 심리)의 투자 포인트 3개를 작성한다. "
                "각 본문에는 근거 수치와 [F#]/[R#] 인용을 넣는다. 당사 목표주가 수치는 언급하지 않는다(밸류에이션 섹션에서 산출). "
                "증권사 컨센서스(선행 EPS·목표주가 평균)가 제공되면 반영하고 '데이터 없음'이라고 쓰지 않는다." + ext_hint,
                '{"points": [{"title": "4~40자 제목", "body": "60~500자 본문", "evidence": ["F1", "R2"]}, …정확히 3개]}',
                "points 정확히 3개 · title 4~40자 · body 60~500자 · evidence 는 사실표 ID 1개 이상"),
            'earnings_analysis': (
                ("분기 손익(매출액·영업이익·순이익)과 전년 동기·직전 분기 증감률이 제공됐다. 최근 분기 실적의 방향(성장·둔화·적자 여부)과 "
                 "영업이익률을 핵심으로 분석하고, EPS·선행 EPS·BPS·ROE·배당은 보조로 다룬다. 분기 실적 사실 [" + str(ds['fid'].get('qfin')) + "] 을 인용한다."
                 if e.get('q') else
                 "가용 데이터(EPS·선행 EPS·BPS·ROE·배당) 기준으로 실적을 분석한다. 선행 EPS(컨센서스)가 있으면 선행 EPS 성장률"
                 "(선행 EPS÷EPS−1)을 핵심 수치로 삼는다. 매출액·영업이익 등 수집되지 않은 항목만 '데이터 없음'으로 명시한다."),
                '{"eps": {"value": 숫자|null, "trend": "증가|감소|유지|데이터 없음", "comment": "문장"}, '
                '"forward": {"eps": 숫자|null, "growth_pct": 숫자|null, "comment": "문장"}, '
                '"dividend": {"yield_pct": 숫자|null, "dps": 숫자|null, "comment": "문장"}, '
                '"profitability": {"roe_pct": 숫자|null, "comment": "문장"}, '
                + ('"quarterly": {"period": "YYYY.MM", "revenue_yoy_pct": 숫자|null, "op_income_yoy_pct": 숫자|null, '
                   '"op_margin_pct": 숫자|null, "comment": "40~400자"}, ' if e.get('q') else '')
                + '"summary": "80~700자", "unavailable": [' + ('' if e.get('q') else '"매출액", "영업이익", ') + '…], "evidence": ["F5"]}',
                f"eps.value = {num(e['eps'])} · eps.trend = \"{e['eps_trend']}\" · dividend.yield_pct = {num(e['div_yield'])} · "
                f"dividend.dps = {num(e['dps'])} 또는 null · profitability.roe_pct = {num(e['roe'])} 또는 null · "
                f"forward.eps = {num(e['fwd_eps'])} · forward.growth_pct = {num(e['fwd_growth_pct'])} · "
                + (f"quarterly.period = \"{e['q']['period']}\" · quarterly.revenue_yoy_pct = {num(e['q']['revenue_yoy_pct'])} · "
                   f"quarterly.op_income_yoy_pct = {num(e['q']['op_income_yoy_pct'])} · quarterly.op_margin_pct = {num(e['q']['op_margin'])} · "
                   "unavailable 에 매출액·영업이익·순이익을 넣지 말 것 (분기 실적이 제공됨)"
                   if e.get('q') else "unavailable 에 '매출액'·'영업이익' 포함")
                + (" (컨센서스는 제공됐으므로 unavailable 에 넣지 말 것)" if ds['cons'].get('has') else '')
                + " · 데이터가 없는 수치는 null"),
            'valuation': (
                ("선행 EPS(컨센서스)가 있으므로 1차 방식은 선행 PER(목표 PER × 선행 EPS)이다. 규칙 계산값을 검토해 목표 PER 을 정하고, "
                 "증권사 목표주가 평균과의 괴리율을 notes 에 설명한다. 후행 PER 밴드는 참고로만 언급한다."
                 if val.get('forward') else
                 "밸류에이션 방식과 적정주가·목표주가를 산출한다. 참고 밴드와 규칙 계산값을 검토해 목표 배수를 정하고 근거를 설명한다."),
                '{"method": "산정 방식 20~300자", "target_multiple": {"type": "선행PER|PER|PBR|혼합|가격밴드", "value": 숫자|null}, '
                '"fair_value": 숫자, "target_price": 숫자, "upside_pct": 숫자, "notes": "50~600자 해설", "evidence": ["F5"]}',
                (self._fwd_rules_text(ds, num) if val.get('forward') else '') +
                (f"현재가 {num(last)} · target_price 는 {num((last or 0) * 0.5)}~{num((last or 0) * 2)} 범위 · "
                 "upside_pct = (target_price ÷ 현재가 − 1) × 100 (±0.5) · target_price ÷ fair_value 는 0.5~2.0 · "
                 f"type=PER 이면 target_price ≈ value × EPS({num(e['eps'])}) ±3% (EPS 없거나 음수면 PER 금지) · "
                 f"type=PBR 이면 target_price ≈ value × BPS({num(e['bps'])}) ±3% · "
                 f"참고 PER 밴드 {json.dumps(val.get('per_band'), ensure_ascii=False)} · "
                 f"참고 PBR 밴드 {json.dumps(val.get('pbr_band'), ensure_ascii=False)} · "
                 f"규칙 적정가 {num(val.get('fair_value'))} · 규칙 목표가 {num(val.get('target_price'))}")),
            'technical_analysis': (
                "이동평균·RSI·수익률·거래량·변동성으로 기술적 분석을 작성하고 지지/저항 가격을 제시한다."
                + (f" 투자자별 순매수 [{fid['flows']}] 는 signals 에 '수급' 항목으로 넣는다." if fid.get('flows') else '')
                + (f" 몬테카를로 [{fid['mc']}] 의 20거래일 상승·하락 확률을 summary 에서 한 문장으로 언급한다." if fid.get('mc') else ''),
                '{"trend": "상승|하락|횡보", "signals": [{"name": "지표명", "value": "수치·상태", "reading": "긍정|중립|부정"}, …2~6개], '
                '"support": 숫자, "resistance": 숫자, "summary": "80~600자", "evidence": ["F1", "F3"]}',
                (f"현재가 {num(last)} · support ≤ 현재가×1.02, resistance ≥ 현재가×0.98, support ≤ resistance, "
                 f"둘 다 현재가의 0.5~1.5배 · 참고 지지 {num(t.get('support'))} / 저항 {num(t.get('resistance'))} · 규칙 추세 판정 {ds['trend']}")),
            'consensus_forecast': (
                "AI 에이전트 시뮬레이션 투표(성향별 분포 포함)와 뉴스·커뮤니티 심리를 해석한다.",
                '{"signal": "강한 매수|약한 매수|보합/관망|약한 매도|강한 매도", "buy_pct": 숫자, "hold_pct": 숫자, '
                '"sell_pct": 숫자, "interpretation": "80~700자", "evidence": ["F6"]}',
                f"signal = \"{v['consensus_signal']}\" · buy_pct = {v['buy_pct']} · hold_pct = {v['hold_pct']} · sell_pct = {v['sell_pct']} (그대로)"),
            'risk_factors': (
                "투자 시 유의할 리스크 3~5개를 영향도(상/중/하)와 함께 제시한다. 가능한 한 수치·리서치 근거를 인용한다."
                + (f" 몬테카를로 VaR95 [{fid['mc']}] 로 하방 위험을 수치화할 수 있다." if fid.get('mc') else '')
                + (f" 과거 판단이 빗나갔거나 손절에 가까웠던 기록([{h_ids[0]}] 등)이 있으면 리스크로 반영한다." if h_ids else ''),
                '{"risks": [{"title": "4~40자", "body": "30~400자", "severity": "상|중|하", "evidence": ["F3"]}, …3~5개]}',
                "risks 3~5개 · severity 는 상|중|하 · evidence 는 사실표 ID 배열(거시 리스크 등 근거가 없으면 빈 배열 허용)"),
            'investment_opinion': (
                "확정된 투자의견과 목표주가의 논거, 매매 전략, 주가 촉매를 정리한다. 투자의견·목표주가는 이미 하우스 기준으로 확정되었으므로 바꾸지 않는다.",
                f'{{"rating": "{fixed.get("rating")}", "target_price": {num(fixed.get("target_price"))}, '
                '"rationale": ["근거 문장", …2~4개], "strategy": "매매 전략 30~400자", "catalysts": ["주가 촉매", …0~4개], "horizon": "12개월"}',
                f"rating = \"{fixed.get('rating')}\" · target_price = {num(fixed.get('target_price'))} · "
                f"상승여력 {_pct(fixed.get('upside_pct'))} · {RATING_POLICY}"),
            'investment_summary': (
                "리포트 첫 페이지의 헤드라인과 요약을 작성한다. 확정된 투자의견·목표주가·상승여력과 모순되지 않아야 한다.",
                '{"headline": "8~60자 헤드라인", "summary": "100~700자 요약", "highlights": ["핵심 포인트", …2~4개]}',
                f"투자의견 {fixed.get('rating')} · 목표주가 {_won(fixed.get('target_price'))} · 현재가 {_won(fixed.get('last_close'))} · "
                f"상승여력 {_pct(fixed.get('upside_pct'))} (목표주가를 언급하면 이 값 그대로) · headline 8~80자 · highlights 2~4개"),
        }[key]
        extra = ''
        if key in ('investment_summary', 'investment_opinion'):
            extra = ("\n[1차 섹션 결과]\n"
                     f"- 밸류에이션: {fixed.get('method')}\n"
                     f"- 투자 포인트: {' / '.join(fixed.get('points') or [])}\n"
                     f"- 주요 리스크: {' / '.join(fixed.get('risks') or [])}\n"
                     f"- 기술적: 추세 {fixed.get('tech_trend')} · 지지 {_won(fixed.get('support'))} · 저항 {_won(fixed.get('resistance'))}"
                     + (f"\n- 컨센서스 대비: 당사 목표주가는 증권사 평균 대비 {_pct(fixed.get('consensus_gap_pct'))}"
                        if fixed.get('consensus_gap_pct') is not None else '')
                     + (f"\n- 괴리 설명(반영할 것): {fixed['consensus_note']}" if fixed.get('consensus_note') else ''))
        return (f"{self._context_text(ds)}{extra}\n\n"
                f"[작성할 섹션: {title}] (섹션키: {key})\n{spec[0]}\n\n"
                f"[출력 스키마]\n{spec[1]}\n\n[검증 조건]\n{spec[2]}")

    def _fwd_rules_text(self, ds, num) -> str:
        val, last = ds['val'], ds['price'].get('last_close')
        fw = val['forward']
        lo, hi = fw['llm_range']
        return (f"선행 EPS {num(fw['fwd_eps'])} 가 있으므로 type 은 '선행PER'(또는 '혼합') · type=선행PER 이면 "
                f"target_price ≈ value × 선행 EPS ±3% · target_price 는 {num(lo)}~{num(hi)} 범위(규칙 선행 목표가 "
                f"{num(fw['rule_target'])} ±35% ∩ 현재가 0.5~2배) · evidence 에 컨센서스 사실 {ds['fid'].get('cons')} 포함 · "
                f"규칙: 기준 PER {fw['base_per']}배({fw['base_source']}), 사이클 할인 {fw['cycle_discount_pct']}%, "
                f"목표 PER {fw['target_per']}배 · 컨센서스 목표주가 평균 {num(fw.get('consensus_target'))}"
                f"(내재 선행 PER {num(fw.get('consensus_implied_per'))}배) · 후행 방식 참고값 "
                f"{num((val.get('trailing') or {}).get('target_price'))} · ")

    def _consensus_note(self, ds, val, target, gap) -> str:
        """당사 목표주가가 컨센서스 목표주가 평균과 30% 넘게 벌어질 때의 설명"""
        c = ds['cons']
        mt = val.get('target_multiple') if isinstance(val.get('target_multiple'), dict) else {}
        mtype = mt.get('type') or ds['val'].get('method_type')
        side = '낮다' if gap < 0 else '높다'
        note = f"당사 목표주가 {_won(target)}은 증권사 목표주가 평균 {_won(c['target_mean'])}보다 {abs(gap):.1f}% {side}. "
        if mtype in ('선행PER', '혼합') and c.get('fwd_eps'):
            tp = target / c['fwd_eps']
            ip = c.get('implied_per')
            fw = ds['val'].get('forward') or {}
            if gap < 0:
                note += (f"선행 EPS에 적용한 목표 PER {tp:.2f}배가 컨센서스 목표가에 내재된 선행 PER {ip:.2f}배보다 보수적이기 때문이다"
                         if ip else f"선행 EPS에 적용한 목표 PER {tp:.2f}배를 보수적으로 잡았기 때문이다")
                if fw.get('cycle_discount_pct'):
                    note += f"(이익 급증에 따른 사이클 할인 {fw['cycle_discount_pct']:.0f}% 포함)"
                note += "."
            else:
                note += (f"목표 PER {tp:.2f}배가 컨센서스 내재 선행 PER {ip:.2f}배보다 높기 때문이다(에이전트·추세 전망 반영)."
                         if ip else f"목표 PER {tp:.2f}배에 에이전트·추세 전망을 반영했기 때문이다.")
        elif c.get('fwd_eps'):
            note += "후행 EPS·과거 밴드 기준으로 산정해 컨센서스가 반영한 선행 이익 성장이 목표주가에 들어가지 않았기 때문이다."
        else:
            note += "선행 EPS 데이터가 없어 후행 EPS·과거 밴드 기준으로 산정했기 때문이다(증권사 목표가는 선행 이익 기반)."
        house = ds.get('fixed_rating')
        cl = c.get('recomm_label')
        if cl and house:
            simple = {'강력매수': '매수', '매수': '매수', '중립': '중립', '매도': '매도', '강력매도': '매도'}[cl]
            if simple != house:
                note += (f" 컨센서스 투자의견은 {cl}({c['recomm_mean']:.2f})로 당사 의견({house})과 다르며, "
                         "하우스 기준은 당사 목표주가 대비 상승여력으로만 의견을 정한다.")
        return note

    # ───────────────────────────────────────────────────────────── 투자의견 확정
    def _fix_opinion(self, ds, results) -> Dict[str, Any]:
        val = (results.get('valuation') or ({}, {}))[0] or {}
        last = ds['price'].get('last_close')
        target = _f(val.get('target_price'))
        if target is None and ds['val'].get('target_price'):
            target = ds['val']['target_price']
        upside = round((target / last - 1) * 100, 1) if (target and last) else None
        v = ds['votes']
        rating = _rating_for(upside, v['buy_pct'], v['sell_pct'])
        tech = (results.get('technical_analysis') or ({}, {}))[0] or {}
        points = ((results.get('investment_points') or ({}, {}))[0] or {}).get('points') or []
        risks = ((results.get('risk_factors') or ({}, {}))[0] or {}).get('risks') or []
        mt = val.get('target_multiple') if isinstance(val.get('target_multiple'), dict) else {}
        c = ds['cons']
        gap = round((target / c['target_mean'] - 1) * 100, 1) if (target and c.get('target_mean')) else None
        ds['fixed_rating'] = rating
        note = self._consensus_note(ds, val, target, gap) if (gap is not None and abs(gap) > CONSENSUS_GAP_NOTE) else None
        return {
            'consensus_target': c.get('target_mean'), 'consensus_gap_pct': gap, 'consensus_note': note,
            'consensus_label': c.get('recomm_label'), 'consensus_recomm': c.get('recomm_mean'),
            'rating': rating, 'target_price': target, 'last_close': last, 'upside_pct': upside,
            'fair_value': _f(val.get('fair_value')) or ds['val'].get('fair_value'),
            'method': val.get('method') or ds['val'].get('method'),
            'method_type': mt.get('type') or ds['val'].get('method_type'),
            'points': [str(p.get('title')) for p in points if isinstance(p, dict)][:3],
            'risks': [str(r.get('title')) for r in risks if isinstance(r, dict)][:5],
            'tech_trend': tech.get('trend') or ds['trend'],
            'support': _f(tech.get('support')) or ds['tech'].get('support'),
            'resistance': _f(tech.get('resistance')) or ds['tech'].get('resistance'),
        }

    # ═════════════════════════════════════════════════════════════ 섹션별: 검증 · 규칙 · 마무리
    # --- 투자 포인트
    def _validate_investment_points(self, obj, ds, fixed):
        c = _Chk(ds['fact_ids'])
        pts = []
        for i, p in enumerate(c.lst(obj, 'points', 'points', 3, 3)):
            if not isinstance(p, dict):
                c.e(f"'points[{i}]' 는 객체여야 함")
                continue
            title = c.s(p, 'title', f'points[{i}].title', 4, 40)
            body = c.s(p, 'body', f'points[{i}].body', 60, 500)
            evid = c.evidence(p, 'evidence', f'points[{i}].evidence')
            if ds['val'].get('target_price'):  # 당사 목표가 수치는 밸류에이션 섹션에서만 (컨센서스 평균 인용은 허용)
                if any(True for _ in _own_target_mentions(body or '')):
                    c.e(f"'points[{i}].body' 에 당사 목표주가 수치를 언급하지 말 것 (밸류에이션 섹션에서 산출)")
            c.cons_na([title, body], ds['cons'].get('has'), f'points[{i}]')
            pts.append({'title': title, 'body': body, 'evidence': evid})
        return {'points': pts}, c.errors

    def _rule_investment_points(self, ds, fixed):
        p, v, e, w, f = ds['price'], ds['votes'], ds['earn'], ds['w52'], ds['fund']
        name = ds['company_name']
        pts = []
        # 1) 추세·모멘텀
        if p.get('has_data'):
            rsi = p.get('rsi14')
            rsi_txt = ('데이터 없음' if rsi is None else f"{rsi:.1f} 수준으로 " +
                       ('과매수권(70 이상)' if rsi >= 70 else '과매도권(30 이하)' if rsi <= 30 else '중립 구간'))
            ma_txt = (f"현재가 {_won(p['last_close'])}은 20일선({_won(p.get('ma20'))})과 60일선({_won(p.get('ma60'))}) 대비 "
                      + {'상승': '위에 위치해 정배열 흐름을 유지하고 있다',
                         '하락': '아래에 있어 역배열 흐름이 이어지고 있다',
                         '횡보': '엇갈린 위치로 이동평균이 수렴하는 모습이다'}[ds['trend']])
            title = {'상승': '중기 상승 추세 유지', '하락': '하락 추세 속 반등 여부 점검',
                     '횡보': '이동평균 수렴, 방향성 탐색 구간'}[ds['trend']]
            ev = self.ev(ds, 'price_level', 'returns', 'rsi_vol')
            pts.append({'title': title, 'evidence': ev,
                        'body': f"{ma_txt}. 최근 20거래일 수익률은 {_pct(p.get('chg_20d_pct'), nd=2)}, 60거래일 수익률은 "
                                f"{_pct(p.get('chg_60d_pct'), nd=2)}이며 RSI(14)는 {rsi_txt}이다. {_refs_text(ev)}"})
        else:
            pts.append({'title': '가격 데이터 부족', 'evidence': self.ev(ds, 'vote') or sorted(ds['fact_ids'])[:1],
                        'body': f"{name}의 주가 데이터가 수집되지 않아 추세·모멘텀 판단은 보류한다. 가격 데이터가 확보되면 "
                                "이동평균·RSI 기반 점검이 필요하다(데이터 없음)."})
        # 2) 밸류에이션 위치 (선행 EPS 가 있으면 선행 PER·이익 성장·컨센서스 중심)
        b = ds['val'].get('per_band') or ds['val'].get('pbr_band')
        cs = ds['cons']
        if cs.get('fwd_eps') and cs.get('fwd_per') and p.get('has_data'):
            g = e.get('fwd_growth_pct')
            ev = self.ev(ds, 'cons', 'earn', 'fund')
            per = f.get('PER')
            body = f"증권사 컨센서스 기준 선행 EPS는 {_won(cs['fwd_eps'])}으로"
            if g is not None:
                body += f" 후행 EPS {_won(e['eps'])} 대비 {_pct(g)} 증가가 예상되며,"
            body += f" 이를 적용한 선행 PER은 {cs['fwd_per']:.2f}배다."
            if per:
                body += (f" 후행 PER {per:.2f}배보다 낮아 이익 성장을 감안한 밸류에이션 부담은 완화된다."
                         if cs['fwd_per'] < per else f" 후행 PER {per:.2f}배보다 높아 이익 감소 위험을 점검해야 한다.")
            if cs.get('target_mean'):
                body += (f" 증권사 목표주가 평균은 {_won(cs['target_mean'])}(현재가 대비 {_pct(cs['upside_pct'])})"
                         + (f", 투자의견 평균은 {cs['recomm_label']}({cs['recomm_mean']:.2f})" if cs.get('recomm_label') else '') + "이다.")
            body += f" {_refs_text(ev)}"
            title = (f"선행 PER {cs['fwd_per']:.2f}배, 이익 성장 {_pct(g, nd=0)}" if g is not None
                     else f"선행 PER {cs['fwd_per']:.2f}배, 컨센서스 반영")
            pts.append({'title': title, 'evidence': ev, 'body': body})
        elif b and (f.get('PER') or f.get('PBR')):
            kind = 'PER' if ds['val'].get('per_band') else 'PBR'
            cur = f.get(kind) or (p.get('last_close') / b['base'] if p.get('last_close') else None)
            pos = (_clip((cur - b['low']) / (b['high'] - b['low']) * 100, 0, 100)
                   if (cur and b['high'] > b['low']) else None)
            judge = ('밴드 하단에 가까워 밸류에이션 부담이 낮다' if pos is not None and pos < 35 else
                     '밴드 상단에 근접해 추가 멀티플 확장 여지는 제한적이다' if pos is not None and pos > 65 else
                     '밴드 중간 수준으로 밸류에이션은 중립적이다')
            ev = self.ev(ds, 'fund', 'val', 'earn')
            pts.append({'title': f"{kind} {_mult(cur)}, 밴드 내 {pos:.0f}% 위치" if pos is not None else f"{kind} 밴드 점검",
                        'evidence': ev,
                        'body': f"현재 {kind} {_mult(cur)}는 최근 1년 {kind} 밴드({b['low']:.2f}~{b['high']:.2f}배)의 "
                                f"{(f'{pos:.0f}%' if pos is not None else '-')} 지점으로 {judge}. PBR {_mult(f.get('PBR'))}, "
                                f"배당수익률 {_pct(e['div_yield'], signed=False, nd=2) if e['div_yield'] is not None else '데이터 없음'}. "
                                f"{_refs_text(ev)}"})
        elif w.get('position_pct') is not None:
            ev = self.ev(ds, 'band52', 'range60')
            pos = w['position_pct']
            pts.append({'title': f"52주 가격 범위 {pos:.0f}% 지점", 'evidence': ev,
                        'body': f"재무 배수 데이터가 없어 가격 위치로 대신 판단한다. 현재가는 52주 범위({_won(w['low'])}~{_won(w['high'])})의 "
                                f"{pos:.0f}% 지점으로 " + ('저점권에 가깝다' if pos < 35 else '고점권에 가깝다' if pos > 65 else '중간 수준이다')
                                + f". PER·PBR 등 밸류에이션 지표는 데이터 없음. {_refs_text(ev)}"})
        else:
            ev = self.ev(ds, 'vote')
            pts.append({'title': '밸류에이션 판단 보류', 'evidence': ev or sorted(ds['fact_ids'])[:1],
                        'body': "PER·PBR·EPS와 52주 가격 범위가 모두 수집되지 않아 밸류에이션 판단을 보류한다(데이터 없음). "
                                "재무 데이터 확보 후 재평가가 필요하다."})
        # 3) 에이전트·뉴스 심리
        ev = self.ev(ds, 'vote', 'types')
        body = (f"{v['num_agents']}명 AI 에이전트 시뮬레이션에서 매수 {v['buy_pct']}% · 보합 {v['hold_pct']}% · "
                f"매도 {v['sell_pct']}%로 '{v['consensus_signal']}' 신호가 도출됐다(평균 확신도 {v['avg_confidence']}).")
        ns = ((ds['research'] or {}).get('sentiment') or {}).get('news') or {}
        if ns.get('n') and ds['fid'].get('news_sent'):
            sc = _f(ns.get('score')) or 0
            body += f" 뉴스 {ns['n']}건의 감성은 {'우호적' if sc > 0.1 else '비우호적' if sc < -0.1 else '중립적'}이다(점수 {sc:+.2f})."
            ev = ev + self.ev(ds, 'news_sent')
        pos_f = sorted([x for x in ds['findings'] if x['sentiment'] == 'positive'],
                       key=lambda x: -(x['importance'] or 0))
        if pos_f:
            body += f" 투자심리에 우호적인 재료로는 '{pos_f[0]['claim'][:80]}' [{pos_f[0]['id']}] 등이 있다."
            ev.append(pos_f[0]['id'])
        body += f" {_refs_text([x for x in ev if f'[{x}]' not in body])}"
        title = ('AI 에이전트 매수 우위' if v['buy_pct'] - v['sell_pct'] >= 10 else
                 'AI 에이전트 매도 우위' if v['sell_pct'] - v['buy_pct'] >= 10 else 'AI 에이전트 관망 우세')
        pts.append({'title': title, 'body': body.strip(), 'evidence': ev or sorted(ds['fact_ids'])[:1]})
        for pt in pts:  # 최소 길이 보정
            if len(pt['body']) < 60:
                pt['body'] += " 추가 데이터 확보 시 판단을 갱신할 필요가 있다."
        return {'points': pts[:3]}

    # --- 실적 분석
    def _validate_earnings_analysis(self, obj, ds, fixed):
        c, e = _Chk(ds['fact_ids']), ds['earn']

        def sub(name):
            s = obj.get(name) if isinstance(obj, dict) else None
            if not isinstance(s, dict):
                c.e(f"'{name}' 는 객체여야 함")
                return {}
            return s

        def match(val, ref, label, must_present):
            if ref is None:
                if val is not None:
                    c.e(f"'{label}' 데이터가 없는데 수치({val})를 제시함 — null 로 두고 '데이터 없음'으로 서술")
                return None
            if val is None:
                if must_present:
                    c.e(f"'{label}' 는 제공값 {ref:,.2f} 를 그대로 써야 함 (null 불가)")
                return None
            if abs(val - ref) > max(abs(ref) * 0.01, 0.011):
                c.e(f"'{label}' 값 {val:,.2f} 가 제공값 {ref:,.2f} 와 다름")
            return val

        eps, div, prof = sub('eps'), sub('dividend'), sub('profitability')
        eps_v = match(c.n(eps, 'value', 'eps.value', required=False), e['eps'], 'eps.value', True)
        trend = c.enum(eps, 'trend', 'eps.trend', ('증가', '감소', '유지', '데이터 없음'))
        if trend and trend != e['eps_trend']:
            c.e(f"'eps.trend' 는 데이터 기준 \"{e['eps_trend']}\" 이어야 함 (받은 값: {trend})")
        y = match(c.n(div, 'yield_pct', 'dividend.yield_pct', required=False), e['div_yield'], 'dividend.yield_pct', True)
        dps = match(c.n(div, 'dps', 'dividend.dps', required=False), e['dps'], 'dividend.dps', False)
        roe = match(c.n(prof, 'roe_pct', 'profitability.roe_pct', required=False), e['roe'], 'profitability.roe_pct', False)
        out = {
            'eps': {'value': eps_v, 'trend': trend, 'comment': c.s(eps, 'comment', 'eps.comment', 5, 400)},
            'dividend': {'yield_pct': y, 'dps': dps, 'comment': c.s(div, 'comment', 'dividend.comment', 5, 400)},
            'profitability': {'roe_pct': roe, 'comment': c.s(prof, 'comment', 'profitability.comment', 3, 400, required=False) or ''},
            'summary': c.s(obj, 'summary', 'summary', 80, 800),
            'unavailable': c.strs(obj, 'unavailable', 'unavailable', 0 if e.get('q') else 1, 12, 2),
            'evidence': c.evidence(obj, 'evidence', 'evidence', required=bool(ds['fund'])),
        }
        if e.get('q'):
            qd = sub('quarterly')
            qq = e['q']
            per = c.s(qd, 'period', 'quarterly.period', 4, 10) if qd else None
            if per and per.rstrip('.') != qq['period']:
                c.e(f"'quarterly.period' 는 \"{qq['period']}\" 이어야 함")
            out['quarterly'] = {
                'period': qq['period'],
                'revenue_yoy_pct': match(c.n(qd, 'revenue_yoy_pct', 'quarterly.revenue_yoy_pct', required=False),
                                         qq['revenue_yoy_pct'], 'quarterly.revenue_yoy_pct', True),
                'op_income_yoy_pct': match(c.n(qd, 'op_income_yoy_pct', 'quarterly.op_income_yoy_pct', required=False),
                                           qq['op_income_yoy_pct'], 'quarterly.op_income_yoy_pct', True),
                'op_margin_pct': match(c.n(qd, 'op_margin_pct', 'quarterly.op_margin_pct', required=False),
                                       qq['op_margin'], 'quarterly.op_margin_pct', True),
                'comment': c.s(qd, 'comment', 'quarterly.comment', 20, 500) if qd else '',
            }
        fw = obj.get('forward') if isinstance(obj, dict) else None
        if e['fwd_eps'] is not None and not isinstance(fw, dict):
            c.e("'forward' 객체(선행 EPS·성장률·comment)가 필요함")
        fw = fw if isinstance(fw, dict) else {}
        out['forward'] = {
            'eps': match(c.n(fw, 'eps', 'forward.eps', required=False), e['fwd_eps'], 'forward.eps', True),
            'growth_pct': match(c.n(fw, 'growth_pct', 'forward.growth_pct', required=False), e['fwd_growth_pct'],
                                'forward.growth_pct', True),
            'comment': c.s(fw, 'comment', 'forward.comment', 5, 400, required=e['fwd_eps'] is not None) or '',
        }
        joined = ' '.join(out['unavailable'] or [])
        if e.get('q'):
            for bad in ('매출', '영업이익', '순이익'):
                if bad in joined:
                    c.e(f"분기 실적이 제공됐으므로 'unavailable' 에서 '{bad}' 을(를) 빼야 함")
        else:
            for must in ('매출', '영업이익'):
                if must not in joined:
                    c.e(f"'unavailable' 에 '{must}' 항목이 포함되어야 함 (수집되지 않은 데이터)")
        if ds['cons'].get('has') and '컨센서스' in joined:
            c.e("증권사 컨센서스가 제공됐으므로 'unavailable' 에서 컨센서스를 빼야 함")
        c.cons_na([out['summary'], out['eps']['comment'], out['forward']['comment']], ds['cons'].get('has'), 'earnings')
        return out, c.errors

    def _rule_earnings_analysis(self, ds, fixed):
        e = ds['earn']
        eps = e['eps']
        if eps is None:
            eps_c = "EPS 데이터 없음."
        elif e['eps_change_pct'] is not None:
            eps_c = (f"분석 기간 중 EPS가 {_won(e['eps_first'])}에서 {_won(eps)}으로 {_pct(e['eps_change_pct'])} 변해 "
                     f"'{e['eps_trend']}' 추세다.")
        else:
            eps_c = (f"최근 기준 EPS는 {_won(eps)}{'(현재가÷PER로 산출)' if e['eps_derived'] else ''}이며, "
                     "기간별 EPS 이력은 수집되지 않아 추이는 판단하지 않는다(데이터 없음).")
        if eps is not None and eps < 0:
            eps_c += " 적자 상태로 PER 적용이 어렵다."
        div = e['div_yield']
        if div is None:
            div_c = "배당 데이터 없음."
        elif div == 0:
            div_c = "배당수익률 0%로 현재 배당을 하지 않는 것으로 집계된다."
        else:
            div_c = (f"배당수익률은 {div:.2f}%" + (f", 주당배당금은 {_won(e['dps'])}" + ('(현재가 기준 산출)' if e['dps_derived'] else '')
                                              if e['dps'] is not None else '') + "이다.")
        roe = e['roe']
        if roe is None:
            roe_c = "ROE 산출에 필요한 EPS·BPS 데이터 없음."
        else:
            lvl = '높은 자본효율' if roe >= 15 else '양호한 수준' if roe >= 8 else '낮은 수준' if roe >= 0 else '적자'
            roe_c = f"EPS÷BPS 기준 산출 ROE는 {roe:.1f}%로 {lvl}이다."
        fe, g, cs = e['fwd_eps'], e['fwd_growth_pct'], ds['cons']
        cid = _refs_text(self.ev(ds, 'cons'))
        if fe is not None:
            fwd_c = (f"증권사 컨센서스 선행 EPS는 {_won(fe)}이다"
                     + (f". 후행 EPS {_won(eps)} 대비 {_pct(g)} 증가가 예상된다" if g is not None else '')
                     + (f"(선행 PER {cs['fwd_per']:.2f}배)" if cs.get('fwd_per') else '') + f". {cid}").strip()
            summary = (f"{ds['company_name']}의 핵심 실적 지표는 선행 EPS 성장률 {_pct(g)}이다. " if g is not None
                       else f"{ds['company_name']}의 실적은 컨센서스 선행 EPS {_won(fe)}을 중심으로 점검했다. ")
            summary += f"{fwd_c} {eps_c} {div_c} {roe_c} "
            summary += "매출액·영업이익·순이익 등 손익계산서 세부 항목은 수집 데이터에 없어 분석에서 제외했다(데이터 없음)."
        else:
            fwd_c = ''
            summary = f"{ds['company_name']}의 실적은 수집된 지표(EPS·BPS·배당) 범위에서만 점검했다. {eps_c} {div_c} {roe_c} "
            if cs.get('has'):
                summary += ("매출액·영업이익·순이익 등 손익계산서 항목은 수집 데이터에 없어 분석에서 제외했다(데이터 없음). "
                            f"증권사 컨센서스 목표주가 평균은 {_won(cs.get('target_mean'))}으로 집계된다. {cid}".strip())
            else:
                summary += "매출액·영업이익·순이익 등 손익계산서 항목과 증권사 실적 컨센서스는 수집 데이터에 없어 분석에서 제외했다(데이터 없음)."
        q_block = None
        if e.get('q'):
            q = e['q']
            qid = _refs_text(self.ev(ds, 'qfin'))
            growth = (f"전년 동기({q['yoy_base']}) 대비 매출 {_pct(q['revenue_yoy_pct'])}, 영업이익 {_pct(q['op_income_yoy_pct'])}"
                      if q.get('yoy_base') else "전년 동기 비교 데이터 없음")
            loss = " 영업적자 분기가 있어 수익성 회복 여부가 관건이다." if (q.get('op_loss_quarters') or 0) > 0 else ''
            q_comment = (f"{q['period']} 분기 매출 {_eok(q.get('revenue'))}, 영업이익 {_eok(q.get('op_income'))}"
                         + (f"(영업이익률 {q['op_margin']:.1f}%)" if q.get('op_margin') is not None else '')
                         + f"으로 {growth}을 기록했다.{loss} {qid}").strip()
            q_block = {'period': q['period'], 'revenue_yoy_pct': q.get('revenue_yoy_pct'),
                       'op_income_yoy_pct': q.get('op_income_yoy_pct'), 'op_margin_pct': q.get('op_margin'),
                       'comment': q_comment}
            summary = summary.replace("매출액·영업이익·순이익 등 손익계산서 세부 항목은 수집 데이터에 없어 분석에서 제외했다(데이터 없음).", '')
            summary = summary.replace("매출액·영업이익·순이익 등 손익계산서 항목은 수집 데이터에 없어 분석에서 제외했다(데이터 없음). ", '')
            summary = summary.replace("매출액·영업이익·순이익 등 손익계산서 항목과 증권사 실적 컨센서스는 수집 데이터에 없어 분석에서 제외했다(데이터 없음).", '')
            summary = q_comment + ' ' + summary
        earn_news = [x for x in ds['findings'] if x.get('topic') == '실적'][:2]
        for x in earn_news:
            summary += f" 관련 보도: {x['claim'][:90]} [{x['id']}]."
        if q_block:
            return {
                'eps': {'value': eps, 'trend': e['eps_trend'], 'comment': eps_c},
                'dividend': {'yield_pct': div, 'dps': e['dps'], 'comment': div_c},
                'profitability': {'roe_pct': roe, 'comment': roe_c},
                'forward': {'eps': fe, 'growth_pct': g, 'comment': fwd_c},
                'quarterly': q_block,
                'summary': re.sub(r'\s+', ' ', summary).strip(),
                'unavailable': list(e['unavailable']),
                'evidence': self.ev(ds, 'qfin', 'qfin_ttm', 'cons', 'fund', 'earn') + [x['id'] for x in earn_news],
            }
        return {
            'eps': {'value': eps, 'trend': e['eps_trend'], 'comment': eps_c},
            'dividend': {'yield_pct': div, 'dps': e['dps'], 'comment': div_c},
            'profitability': {'roe_pct': roe, 'comment': roe_c},
            'forward': {'eps': fe, 'growth_pct': g, 'comment': fwd_c},
            'summary': summary.strip(),
            'unavailable': list(e['unavailable']),
            'evidence': self.ev(ds, 'cons', 'fund', 'earn') + [x['id'] for x in earn_news],
        }

    def _finalize_earnings_analysis(self, data, ds, fixed):
        e = ds['earn']
        data = dict(data)
        data['computed'] = {k: e[k] for k in ('eps', 'eps_derived', 'eps_trend', 'eps_first', 'eps_change_pct', 'bps',
                                              'bps_derived', 'div_yield', 'dps', 'dps_derived', 'roe',
                                              'fwd_eps', 'fwd_growth_pct')}
        data['consensus'] = ds['cons'] if ds['cons'].get('has') else None
        if e.get('q'):
            data['quarters'] = e['q'].get('quarters') or []
            data['quarter_source'] = e['q'].get('source')
            data['ttm'] = e['q'].get('ttm')
            data['ttm_periods'] = e['q'].get('ttm_periods')
        data['per'] = ds['fund'].get('PER')
        data['pbr'] = ds['fund'].get('PBR')
        return data

    # --- 밸류에이션
    def _validate_valuation(self, obj, ds, fixed):
        c, e, last = _Chk(ds['fact_ids']), ds['earn'], ds['price'].get('last_close')
        method = c.s(obj, 'method', 'method', 10, 400)
        tm = obj.get('target_multiple') if isinstance(obj, dict) else None
        mtype, mval = None, None
        if not isinstance(tm, dict):
            c.e("'target_multiple' 는 {\"type\", \"value\"} 객체여야 함")
        else:
            mtype = c.enum(tm, 'type', 'target_multiple.type', ('선행PER', 'PER', 'PBR', '혼합', '가격밴드'))
            mval = c.n(tm, 'value', 'target_multiple.value', required=mtype in ('선행PER', 'PER', 'PBR'), lo=0.01, hi=500)
        fair = c.n(obj, 'fair_value', 'fair_value', required=bool(last), lo=0.01)
        tgt = c.n(obj, 'target_price', 'target_price', required=bool(last),
                  lo=(last * 0.5 if last else None), hi=(last * 2.0 if last else None))
        up = c.n(obj, 'upside_pct', 'upside_pct', required=bool(last))
        if not last and (tgt is not None or fair is not None):
            c.e("가격 데이터가 없으므로 fair_value·target_price 는 null 이어야 함")
        if tgt and last and up is not None:
            real = (tgt / last - 1) * 100
            if abs(real - up) > 0.5:
                c.e(f"'upside_pct' {up:.1f} 가 (target_price÷현재가−1)×100 = {real:.1f} 와 불일치")
        if tgt and fair and not (0.5 <= tgt / fair <= 2.0):
            c.e(f"target_price÷fair_value = {tgt / fair:.2f} 가 허용 범위(0.5~2.0) 밖")
        if mtype == 'PER':
            if not (e['eps'] and e['eps'] > 0):
                c.e("EPS 가 없거나 음수라 PER 방식을 쓸 수 없음 — PBR 또는 가격밴드 사용")
            elif mval and tgt and abs(mval * e['eps'] / tgt - 1) > 0.03:
                c.e(f"target_price {tgt:,.0f} 가 목표 PER {mval} × EPS {e['eps']:,.0f} = {mval * e['eps']:,.0f} 와 3% 이상 차이")
        if mtype == 'PBR':
            if not e['bps']:
                c.e("BPS 데이터가 없어 PBR 방식을 쓸 수 없음")
            elif mval and tgt and abs(mval * e['bps'] / tgt - 1) > 0.03:
                c.e(f"target_price {tgt:,.0f} 가 목표 PBR {mval} × BPS {e['bps']:,.0f} = {mval * e['bps']:,.0f} 와 3% 이상 차이")
        if mtype == '혼합' and not (ds['val'].get('per_band') or ds['val'].get('pbr_band')):
            c.e("PER·PBR 밴드 데이터가 없어 '혼합' 방식을 쓸 수 없음")
        fe = ds['cons'].get('fwd_eps')
        if mtype == '선행PER':
            if not fe:
                c.e("선행 EPS(컨센서스) 데이터가 없어 '선행PER' 방식을 쓸 수 없음")
            elif mval and tgt and abs(mval * fe / tgt - 1) > 0.03:
                c.e(f"target_price {tgt:,.0f} 가 목표 PER {mval} × 선행 EPS {fe:,.0f} = {mval * fe:,.0f} 와 3% 이상 차이")
        notes = c.s(obj, 'notes', 'notes', 30, 700)
        c.target_mentions(notes, tgt, 'notes')
        evid = c.evidence(obj, 'evidence', 'evidence', required=bool(last))
        fw = ds['val'].get('forward')
        if fw and last:
            lo, hi = fw['llm_range']
            if tgt and not (lo * 0.999 <= tgt <= hi * 1.001):
                c.e(f"target_price {tgt:,.0f} 는 허용 범위 {lo:,.0f}~{hi:,.0f} 밖 (규칙 선행 목표가 {fw['rule_target']:,.0f} ±35%, "
                    "현재가 0.5~2배)")
            if mtype and mtype not in ('선행PER', '혼합'):
                c.e("선행 EPS(컨센서스)가 있으므로 target_multiple.type 은 '선행PER' 또는 '혼합'이어야 함")
        cons_id = ds['fid'].get('cons')
        if cons_id and last and cons_id not in evid and f"[{cons_id}]" not in f"{notes or ''} {method or ''}":
            c.e(f"증권사 컨센서스 사실 {cons_id} 를 evidence 에 인용해야 함")
        c.cons_na([notes, method], ds['cons'].get('has'), 'valuation')
        return {'method': method, 'target_multiple': {'type': mtype, 'value': mval}, 'fair_value': fair,
                'target_price': tgt, 'upside_pct': up, 'notes': notes, 'evidence': evid}, c.errors

    def _rule_valuation(self, ds, fixed):
        val, last, w = ds['val'], ds['price'].get('last_close'), ds['w52']
        if not val.get('target_price'):
            return {'method': val['method'], 'target_multiple': {'type': '가격밴드', 'value': None},
                    'fair_value': None, 'target_price': None, 'upside_pct': None,
                    'notes': "가격·재무 데이터가 없어 적정주가와 목표주가를 산정하지 않았다(데이터 없음). 투자의견은 중립으로 둔다.",
                    'evidence': []}
        s = val['outlook_score']
        tone = '긍정적' if s > 0.15 else '부정적' if s < -0.15 else '중립적'
        notes = (f"밴드 중앙값 기준 적정주가는 {_won(val['fair_value'])}이다. 에이전트 투표·추세"
                 + ("·뉴스 감성" if ds['research'] else '') +
                 f"을 합산한 전망 점수({s:+.2f}, {tone})를 반영해 밴드의 {val['percentile'] * 100:.0f}% 분위를 목표 배수로 적용했고, "
                 f"목표주가는 {_won(val['target_price'])}(상승여력 {_pct(val['upside_pct'])})으로 산출된다. "
                 f"52주 가격 범위는 {_won(w.get('low'))}~{_won(w.get('high'))}이다.")
        if val.get('forward'):
            fw, tr, cs = val['forward'], val.get('trailing') or {}, ds['cons']
            cid = _refs_text(self.ev(ds, 'cons'))
            notes = (f"컨센서스 선행 EPS {_won(fw['fwd_eps'])}"
                     + (f"은 후행 EPS {_won(fw['trailing_eps'])} 대비 {_pct(fw['growth_pct'])} 증가가 예상된다" if fw.get('growth_pct') is not None
                        else "을 적용한다") + f" {cid}. "
                     f"기준 PER은 {fw['base_source']}인 {fw['base_per']:.2f}배이며"
                     + (f", 이익 급증기 고점 배수를 경계해 사이클 할인 {fw['cycle_discount_pct']:.0f}%를 적용했다"
                        if fw.get('cycle_discount_pct') else '')
                     + f". 전망 점수({s:+.2f}, {tone})를 반영한 목표 PER {fw['target_per']:.2f}배로 목표주가는 "
                     f"{_won(val['target_price'])}(상승여력 {_pct(val['upside_pct'])})이다.")
            if fw.get('consensus_target'):
                notes += (f" 증권사 목표주가 평균 {_won(fw['consensus_target'])}과의 괴리는 {_pct(fw['consensus_gap_pct'])}"
                          + (f"(평균 내재 선행 PER {fw['consensus_implied_per']:.2f}배)" if fw.get('consensus_implied_per') else '') + "이다.")
            if tr.get('target_price'):
                notes += (f" 참고로 후행 {METHOD_LABEL.get(tr.get('method_type'), '밴드')} 방식 산출값은 {_won(tr['target_price'])}으로, "
                          "선행 이익 성장이 반영되지 않는다.")
            if val.get('clamped'):
                notes += " 산출값이 현재가 대비 과도해 0.6~1.8배 범위로 보정했다."
            return {'method': val['method'], 'target_multiple': {'type': '선행PER', 'value': val['target_multiple']},
                    'fair_value': val['fair_value'], 'target_price': val['target_price'], 'upside_pct': val['upside_pct'],
                    'notes': notes, 'evidence': self.ev(ds, 'cons', 'earn', 'val', 'fund', 'band52')}
        if val.get('clamped'):
            notes += " 산출값이 현재가 대비 과도해 0.7~1.5배 범위로 보정했다."
        if ds['cons'].get('target_mean'):
            notes += (f" 증권사 목표주가 평균은 {_won(ds['cons']['target_mean'])}이다"
                      f"(선행 EPS 없음, 괴리 {_pct((val['target_price'] / ds['cons']['target_mean'] - 1) * 100)}).")
        if val['method_type'] == '가격밴드':
            notes += " PER·PBR 데이터가 없어 가격 밴드로 대체한 추정치로, 신뢰도가 낮다."
        return {'method': val['method'], 'target_multiple': {'type': val['method_type'], 'value': val['target_multiple']},
                'fair_value': val['fair_value'], 'target_price': val['target_price'], 'upside_pct': val['upside_pct'],
                'notes': notes, 'evidence': (self.ev(ds, 'cons', 'fund', 'val', 'band52', 'earn') or self.ev(ds, 'range60'))}

    def _finalize_valuation(self, data, ds, fixed):
        data = dict(data)
        last = ds['price'].get('last_close')
        if data.get('target_price'):
            data['target_price'] = _round_price(data['target_price'])
            data['upside_pct'] = round((data['target_price'] / last - 1) * 100, 1) if last else None
        if data.get('fair_value'):
            data['fair_value'] = _round_price(data['fair_value'])
        v = ds['val']
        data.update(last_close=last, per_band=v.get('per_band'), pbr_band=v.get('pbr_band'), band_52w=v.get('band_52w'),
                    eps=ds['earn']['eps'], bps=ds['earn']['bps'], per=ds['fund'].get('PER'), pbr=ds['fund'].get('PBR'),
                    rule_reference={'fair_value': v.get('fair_value'), 'target_price': v.get('target_price'),
                                    'outlook_score': v.get('outlook_score'), 'percentile': v.get('percentile')},
                    forward=v.get('forward'), fwd_band=v.get('fwd_band'),
                    trailing_reference=v.get('trailing') if v.get('forward') else None)
        tm = ds['cons'].get('target_mean')
        data['consensus_target'] = tm
        data['consensus_gap_pct'] = (round((data['target_price'] / tm - 1) * 100, 1)
                                     if (tm and data.get('target_price')) else None)
        return data

    # --- 기술적 분석
    def _validate_technical_analysis(self, obj, ds, fixed):
        c, last = _Chk(ds['fact_ids']), ds['price'].get('last_close')
        trend = c.enum(obj, 'trend', 'trend', ('상승', '하락', '횡보'))
        sigs = []
        for i, sg in enumerate(c.lst(obj, 'signals', 'signals', 2, 6)):
            if not isinstance(sg, dict):
                c.e(f"'signals[{i}]' 는 객체여야 함")
                continue
            sigs.append({'name': c.s(sg, 'name', f'signals[{i}].name', 2, 30),
                         'value': c.s(sg, 'value', f'signals[{i}].value', 1, 80),
                         'reading': c.enum(sg, 'reading', f'signals[{i}].reading', ('긍정', '중립', '부정'))})
        sup = c.n(obj, 'support', 'support', required=bool(last),
                  lo=last * 0.5 if last else None, hi=last * 1.02 if last else None)
        res = c.n(obj, 'resistance', 'resistance', required=bool(last),
                  lo=last * 0.98 if last else None, hi=last * 1.5 if last else None)
        if not last and (sup is not None or res is not None):
            c.e("가격 데이터가 없으므로 support·resistance 는 null 이어야 함")
        if sup and res and sup > res:
            c.e(f"support {sup:,.0f} 가 resistance {res:,.0f} 보다 큼")
        summary = c.s(obj, 'summary', 'summary', 60, 700)
        evid = c.evidence(obj, 'evidence', 'evidence', required=bool(last))
        return {'trend': trend, 'signals': sigs, 'support': sup, 'resistance': res,
                'summary': summary, 'evidence': evid}, c.errors

    def _rule_technical_analysis(self, ds, fixed):
        p, t = ds['price'], ds['tech']
        if not p.get('has_data'):
            return {'trend': '횡보', 'signals': [{'name': '가격 데이터', 'value': '데이터 없음', 'reading': '중립'},
                                                 {'name': '거래량', 'value': '데이터 없음', 'reading': '중립'}],
                    'support': None, 'resistance': None,
                    'summary': "주가 데이터가 수집되지 않아 이동평균·RSI·거래량 기반 기술적 분석을 수행하지 못했다(데이터 없음). "
                               "가격 데이터 확보 후 재분석이 필요하다.", 'evidence': []}
        last, m20, m60, rsi = p['last_close'], p.get('ma20'), p.get('ma60'), p.get('rsi14')
        sigs = []
        if m20 and m60:
            order = sorted([('종가', last), ('20일선', m20), ('60일선', m60)], key=lambda x: -x[1])
            sigs.append({'name': '이동평균 배열', 'value': ' > '.join(k for k, _ in order),
                         'reading': {'상승': '긍정', '하락': '부정'}.get(ds['trend'], '중립')})
        elif m20:
            sigs.append({'name': '20일 이동평균', 'value': f"종가 {'>' if last > m20 else '<'} 20일선({m20:,.0f})",
                         'reading': '긍정' if last > m20 else '부정'})
        if rsi is not None:
            sigs.append({'name': 'RSI(14)', 'value': f"{rsi:.1f}",
                         'reading': '부정' if rsi >= 70 else '긍정' if rsi <= 30 else '중립'})
        c20 = p.get('chg_20d_pct')
        if c20 is not None:
            sigs.append({'name': '20일 수익률', 'value': _pct(c20, nd=2),
                         'reading': '긍정' if c20 > 3 else '부정' if c20 < -3 else '중립'})
        vr = p.get('vol_ratio_5_20')
        if vr is not None:
            c5 = p.get('chg_5d_pct') or 0
            sigs.append({'name': '거래량(5일/20일)', 'value': f"{vr:.2f}배",
                         'reading': ('긍정' if c5 > 0 else '부정') if vr >= 1.3 else '중립'})
        vol = p.get('daily_vol_20d_pct')
        if vol is not None:
            sigs.append({'name': '20일 변동성', 'value': f"일간 {vol:.2f}%", 'reading': '부정' if vol >= 3 else '중립'})
        while len(sigs) < 2:
            sigs.append({'name': '기간 수익률', 'value': _pct(p.get('period_return_pct'), nd=2), 'reading': '중립'})
        ev = self.ev(ds, 'price_level', 'returns', 'rsi_vol', 'range60', 'volume')
        rsi_txt = f"RSI(14)는 {rsi:.1f} 수준이다" if rsi is not None else "RSI(14)는 데이터 없음"
        summary = (f"추세는 {ds['trend']} 국면으로 판단한다. 현재가 {_won(last)} 기준 20일선 {_won(m20)}, 60일선 {_won(m60)}이며 {rsi_txt}. "
                   f"단기 지지선은 {_won(t['support'])}, 저항선은 {_won(t['resistance'])}으로 설정하며, "
                   + ("지지선 이탈 시 추세 훼손 가능성에 유의해야 한다." if ds['trend'] != '하락'
                      else "저항선 돌파 전까지는 보수적 접근이 필요하다.") + f" {_refs_text(ev[:3])}")
        return {'trend': ds['trend'], 'signals': sigs[:6], 'support': t['support'], 'resistance': t['resistance'],
                'summary': summary, 'evidence': ev}

    def _finalize_technical_analysis(self, data, ds, fixed):
        data = dict(data)
        p = ds['price']
        data['indicators'] = {k: p.get(k) for k in ('last_close', 'ma5', 'ma20', 'ma60', 'rsi14', 'daily_vol_20d_pct',
                                                     'chg_5d_pct', 'chg_20d_pct', 'chg_60d_pct', 'high_60d', 'low_60d',
                                                     'avg_volume_20d', 'vol_ratio_5_20')}
        data['rule_trend'] = ds['trend']
        return data

    # --- 시뮬레이션·컨센서스
    def _validate_consensus_forecast(self, obj, ds, fixed):
        c, v = _Chk(ds['fact_ids']), ds['votes']
        sig = c.enum(obj, 'signal', 'signal', SIGNALS)
        if sig and sig != v['consensus_signal']:
            c.e(f"'signal' 은 데이터 기준 \"{v['consensus_signal']}\" 이어야 함")
        out = {'signal': sig}
        for k in ('buy_pct', 'hold_pct', 'sell_pct'):
            x = c.n(obj, k, k, lo=0, hi=100)
            if x is not None and abs(x - v[k]) > 0.15:
                c.e(f"'{k}' {x} 가 제공값 {v[k]} 와 다름")
            out[k] = x
        out['interpretation'] = c.s(obj, 'interpretation', 'interpretation', 60, 800)
        out['evidence'] = c.evidence(obj, 'evidence', 'evidence')
        return out, c.errors

    def _rule_consensus_forecast(self, ds, fixed):
        v = ds['votes']
        txt = (f"총 {v['num_agents']}명의 AI 에이전트가 {v['simulation_rounds']}라운드 시뮬레이션에 참여해 매수 {v['buy_count']}명"
               f"({v['buy_pct']}%), 보합 {v['hold_count']}명({v['hold_pct']}%), 매도 {v['sell_count']}명({v['sell_pct']}%)으로 "
               f"집계됐다. 합의 신호는 '{v['consensus_signal']}'이며 평균 확신도는 {v['avg_confidence']}이다.")
        bt = v.get('by_type') or {}
        if bt:
            def share(t, k):
                c = bt[t]
                n = max(1, c['buy'] + c['hold'] + c['sell'])
                return c[k] / n * 100
            top_buy = max(bt, key=lambda t: share(t, 'buy'))
            top_sell = max(bt, key=lambda t: share(t, 'sell'))
            txt += (f" 성향별로는 {AGENT_TYPE_KO.get(top_buy, top_buy)}의 매수 비중이 {share(top_buy, 'buy'):.0f}%로 가장 높고, "
                    f"{AGENT_TYPE_KO.get(top_sell, top_sell)}의 매도 비중이 {share(top_sell, 'sell'):.0f}%로 가장 높다.")
        ev = self.ev(ds, 'vote', 'types')
        sent = (ds['research'] or {}).get('sentiment') or {}
        for key, label, role in (('news', '뉴스', 'news_sent'), ('community', '커뮤니티', 'community')):
            s = sent.get(key) or {}
            if s.get('n') and ds['fid'].get(role):
                sc = _f(s.get('score')) or 0
                txt += (f" {label} {s['n']}건의 심리는 긍정 {s.get('pos', 0)}건 · 중립 {s.get('neu', 0)}건 · 부정 {s.get('neg', 0)}건으로 "
                        f"{'우호적' if sc > 0.1 else '비우호적' if sc < -0.1 else '중립적'}이다(점수 {sc:+.2f}) [{ds['fid'][role]}].")
                ev.append(ds['fid'][role])
        txt += " 시뮬레이션 결과는 통계적 경향으로, 실제 수급과 다를 수 있다."
        return {'signal': v['consensus_signal'], 'buy_pct': v['buy_pct'], 'hold_pct': v['hold_pct'],
                'sell_pct': v['sell_pct'], 'interpretation': txt, 'evidence': ev or sorted(ds['fact_ids'])[:1]}

    def _finalize_consensus_forecast(self, data, ds, fixed):
        data = dict(data)
        v = ds['votes']
        data.update(num_agents=v['num_agents'], avg_confidence=v['avg_confidence'],
                    counts={'buy': v['buy_count'], 'hold': v['hold_count'], 'sell': v['sell_count']},
                    by_type=v.get('by_type') or {}, simulation_rounds=v.get('simulation_rounds'))
        sent = (ds['research'] or {}).get('sentiment') or {}
        if sent:
            data['sentiment'] = {k: sent.get(k) for k in ('news', 'community', 'overall_score', 'engine') if k in sent}
        return data

    # --- 리스크 요인
    def _validate_risk_factors(self, obj, ds, fixed):
        c = _Chk(ds['fact_ids'])
        out = []
        for i, r in enumerate(c.lst(obj, 'risks', 'risks', 3, 5)):
            if not isinstance(r, dict):
                c.e(f"'risks[{i}]' 는 객체여야 함")
                continue
            out.append({'title': c.s(r, 'title', f'risks[{i}].title', 4, 40),
                        'body': c.s(r, 'body', f'risks[{i}].body', 30, 400),
                        'severity': c.enum(r, 'severity', f'risks[{i}].severity', ('상', '중', '하')),
                        'evidence': c.evidence(r, 'evidence', f'risks[{i}].evidence', required=False)})
        return {'risks': out}, c.errors

    def _rule_risk_factors(self, ds, fixed):
        p, v, e, val = ds['price'], ds['votes'], ds['earn'], ds['val']
        risks = []
        vol = p.get('daily_vol_20d_pct')
        if vol is not None:
            sev = '상' if vol >= 3 else '중' if vol >= 1.8 else '하'
            risks.append({'title': '가격 변동성', 'severity': sev, 'evidence': self.ev(ds, 'rsi_vol'),
                          'body': f"최근 20일 일간 수익률 표준편차가 {vol:.2f}%로 "
                                  + {'상': '높아 단기 손실 폭이 커질 수 있다.', '중': '보통 수준이나 이벤트 발생 시 확대될 수 있다.',
                                     '하': '낮은 편이지만 변동성 급등 가능성은 상존한다.'}[sev] + f" {_refs_text(self.ev(ds, 'rsi_vol'))}"})
        rsi = p.get('rsi14')
        if ds['trend'] == '하락':
            risks.append({'title': '하락 추세 지속', 'severity': '상', 'evidence': self.ev(ds, 'price_level', 'returns'),
                          'body': f"현재가가 20일선·60일선 아래에 있어 추세 전환 확인 전까지 추가 하락 위험이 있다. "
                                  f"60거래일 수익률은 {_pct(p.get('chg_60d_pct'), nd=2)}이다. {_refs_text(self.ev(ds, 'price_level', 'returns'))}"})
        elif rsi is not None and rsi >= 70:
            risks.append({'title': '단기 과열', 'severity': '중', 'evidence': self.ev(ds, 'rsi_vol'),
                          'body': f"RSI(14)가 {rsi:.1f} 수준으로 과매수권에 있어 단기 차익실현 매물이 나올 수 있다. {_refs_text(self.ev(ds, 'rsi_vol'))}"})
        elif ds['tech'].get('support'):
            risks.append({'title': '지지선 이탈 위험', 'severity': '하', 'evidence': self.ev(ds, 'range60', 'price_level'),
                          'body': f"지지선 {_won(ds['tech']['support'])} 이탈 시 60일 저가 {_won(p.get('low_60d'))}까지 "
                                  f"조정이 확대될 수 있다. {_refs_text(self.ev(ds, 'range60'))}"})
        if e['eps'] is not None and e['eps'] < 0:
            risks.append({'title': '적자 지속', 'severity': '상', 'evidence': self.ev(ds, 'fund', 'earn'),
                          'body': f"EPS가 {_won(e['eps'])}으로 적자 상태여서 실적 개선이 지연되면 밸류에이션 근거가 약해진다."})
        else:
            b = val.get('per_band')
            per = ds['fund'].get('PER')
            if b and per and b['high'] > b['low'] and (per - b['low']) / (b['high'] - b['low']) > 0.65:
                risks.append({'title': '밸류에이션 부담', 'severity': '중', 'evidence': self.ev(ds, 'fund', 'val'),
                              'body': f"PER {_mult(per)}가 1년 밴드({b['low']:.2f}~{b['high']:.2f}배) 상단에 가까워 "
                                      f"실적 기대가 꺾이면 멀티플 축소 위험이 있다. {_refs_text(self.ev(ds, 'fund', 'val'))}"})
            else:
                risks.append({'title': '실적 데이터 제한', 'severity': '중', 'evidence': self.ev(ds, 'fund'),
                              'body': "매출액·영업이익 등 손익 데이터가 없어(데이터 없음) 실적 서프라이즈·쇼크에 대한 사전 점검이 제한된다. "
                                      "분기 실적 발표 일정과 컨센서스를 별도로 확인해야 한다."})
        if v['sell_pct'] >= 35:
            risks.append({'title': '매도 의견 비중', 'severity': '중' if v['sell_pct'] < 50 else '상',
                          'evidence': self.ev(ds, 'vote'),
                          'body': f"시뮬레이션 에이전트의 {v['sell_pct']}%가 매도를 선택해 투자심리가 약화될 수 있다. {_refs_text(self.ev(ds, 'vote'))}"})
        for it in self._research_items(ds, 'risks')[:2]:
            if len(risks) >= 5:
                break
            plain = re.sub(r'\s*\[[FRCKH]\d+\]', '', it['text']).strip().rstrip('.')
            title = (plain[:20].rstrip() + '…') if len(plain) > 22 else plain
            body = f"리서치 점검 결과 '{plain}' 우려가 제기됐다. 관련 보도 흐름과 실적 영향을 지속 점검할 필요가 있다."
            refs = _refs_text(it['refs'])
            risks.append({'title': title if len(title) >= 4 else '리서치 리스크', 'severity': '중', 'evidence': it['refs'],
                          'body': f"{body} {refs}".strip()})
        if len(risks) < 3:
            risks.append({'title': '대외 변수', 'severity': '하', 'evidence': [],
                          'body': "금리·환율·글로벌 수요 등 거시 변수와 업종 수급 변화가 주가에 영향을 줄 수 있다(근거 데이터는 수집 범위 밖)."})
        if len(risks) < 3:
            risks.append({'title': '유동성·수급', 'severity': '하', 'evidence': self.ev(ds, 'volume'),
                          'body': "거래량 변동과 외국인·기관 수급 불균형이 발생하면 가격 괴리가 커질 수 있다."})
        return {'risks': risks[:5]}

    # --- 투자의견
    def _validate_investment_opinion(self, obj, ds, fixed):
        c = _Chk(ds['fact_ids'])
        rating = c.enum(obj, 'rating', 'rating', RATINGS)
        if rating and rating != fixed.get('rating'):
            c.e(f"'rating' 은 하우스 기준으로 확정된 \"{fixed.get('rating')}\" 이어야 함")
        tgt = c.n(obj, 'target_price', 'target_price', required=fixed.get('target_price') is not None)
        if fixed.get('target_price') and tgt and abs(tgt / fixed['target_price'] - 1) > 0.01:
            c.e(f"'target_price' 는 확정 목표주가 {fixed['target_price']:,.0f} 이어야 함")
        if fixed.get('target_price') is None and tgt is not None:
            c.e("목표주가를 산정하지 않았으므로 'target_price' 는 null 이어야 함")
        rationale = c.strs(obj, 'rationale', 'rationale', 2, 4, 10)
        strategy = c.s(obj, 'strategy', 'strategy', 20, 500)
        catalysts = c.strs(obj, 'catalysts', 'catalysts', 0, 4, 4) if isinstance(obj, dict) and 'catalysts' in obj else []
        horizon = c.s(obj, 'horizon', 'horizon', 2, 20, required=False) or '12개월'
        for i, t in enumerate(rationale + [strategy or '']):
            c.target_mentions(t, fixed.get('target_price'), 'rationale/strategy')
        return {'rating': rating, 'target_price': fixed.get('target_price'), 'rationale': rationale,
                'strategy': strategy, 'catalysts': catalysts, 'horizon': horizon}, c.errors

    def _rule_investment_opinion(self, ds, fixed):
        v, rating = ds['votes'], fixed['rating']
        up, tgt = fixed.get('upside_pct'), fixed.get('target_price')
        rationale = []
        if tgt:
            rationale.append(f"목표주가 {_won(tgt)}은 {METHOD_LABEL.get(fixed.get('method_type'), '밸류에이션')} 방식으로 산출했으며 "
                             f"현재가 대비 상승여력은 {_pct(up)}이다.")
        else:
            rationale.append("가격·재무 데이터 부족으로 목표주가를 산정하지 않아 투자의견을 중립으로 둔다.")
        rationale.append(f"AI 에이전트 {v['num_agents']}명 중 매수 {v['buy_pct']}%, 매도 {v['sell_pct']}%로 "
                         f"'{v['consensus_signal']}' 신호가 나왔다. {_refs_text(self.ev(ds, 'vote'))}".strip())
        if ds['price'].get('has_data'):
            rationale.append(f"기술적 추세는 '{fixed.get('tech_trend')}'이며 지지선 {_won(fixed.get('support'))}, "
                             f"저항선 {_won(fixed.get('resistance'))}을 주요 가격대로 본다.")
        sup, res = _won(fixed.get('support')), _won(fixed.get('resistance'))
        strategy = {
            '매수': f"현재가 부근에서 분할 매수로 접근하되 지지선 {sup} 이탈 시 비중을 줄이고, 목표주가 도달 시 차익 실현을 검토한다.",
            '중립': f"목표주가 대비 여력이 제한적이어서 신규 진입보다 보유 관점이 적절하며, {sup}~{res} 구간에서 박스권 대응을 권한다.",
            '매도': f"반등 시 비중 축소를 권하며, 저항선 {res} 돌파 여부를 확인하기 전까지 신규 매수는 보류한다.",
        }[rating]
        cats = []
        for x in sorted([x for x in ds['findings'] if x['sentiment'] == 'positive'], key=lambda x: -(x['importance'] or 0))[:2]:
            cats.append(f"{x['claim'][:70]} [{x['id']}]")
        if fixed.get('resistance'):
            cats.append(f"저항선 {res} 돌파 여부")
        return {'rating': rating, 'target_price': tgt, 'rationale': rationale[:4], 'strategy': strategy,
                'catalysts': cats[:4], 'horizon': '12개월'}

    def _finalize_investment_opinion(self, data, ds, fixed):
        data = dict(data)
        data.update(rating=fixed['rating'], target_price=fixed.get('target_price'), upside_pct=fixed.get('upside_pct'),
                    last_close=fixed.get('last_close'), rating_policy=RATING_POLICY, disclaimer=DISCLAIMER_LINE,
                    consensus_target=fixed.get('consensus_target'), consensus_gap_pct=fixed.get('consensus_gap_pct'),
                    consensus_label=fixed.get('consensus_label'), consensus_recomm=fixed.get('consensus_recomm'),
                    consensus_note=fixed.get('consensus_note'))
        return data

    # --- 투자 요약
    def _validate_investment_summary(self, obj, ds, fixed):
        c = _Chk(ds['fact_ids'])
        headline = c.s(obj, 'headline', 'headline', 8, 80)
        summary = c.s(obj, 'summary', 'summary', 80, 800)
        highlights = c.strs(obj, 'highlights', 'highlights', 2, 4, 5)
        for t in [headline or '', summary or ''] + highlights:
            c.target_mentions(t, fixed.get('target_price'), 'summary/headline')
        c.cons_na([headline, summary] + highlights, ds['cons'].get('has'), 'summary')
        return {'headline': headline, 'summary': summary, 'highlights': highlights}, c.errors

    def _rule_headline(self, ds, fixed) -> str:
        up = fixed.get('upside_pct')
        trend = {'상승': '상승 추세 유지', '하락': '하락 추세 속 저점 확인 필요', '횡보': '박스권 흐름'}.get(ds['trend'], '데이터 부족')
        if up is None:
            return f"{ds['company_name']}: 데이터 부족으로 투자의견 산정 제한"
        tail = (f"목표주가까지 {_pct(up)} 여력" if up >= 0 else f"목표주가 대비 {_pct(up)} 하방 위험")
        return f"{trend}, {tail}"

    def _rule_investment_summary(self, ds, fixed):
        v, name = ds['votes'], ds['company_name']
        tgt, up = fixed.get('target_price'), fixed.get('upside_pct')
        if tgt:
            s = (f"{name}에 대해 투자의견 '{fixed['rating']}', 목표주가 {_won(tgt)}을 제시한다. 현재가 {_won(fixed.get('last_close'))} 대비 "
                 f"상승여력은 {_pct(up)}이다. 목표주가는 {METHOD_LABEL.get(fixed.get('method_type'), '밸류에이션')} 방식으로 산출했다. ")
        else:
            s = f"{name}에 대해서는 가격·재무 데이터가 부족해 목표주가를 산정하지 않고 투자의견을 '중립'으로 둔다. "
        s += (f"AI 에이전트 {v['num_agents']}명 시뮬레이션의 합의 신호는 '{v['consensus_signal']}'(매수 {v['buy_pct']}% · 매도 "
              f"{v['sell_pct']}%)이고, 기술적 추세는 '{fixed.get('tech_trend')}'이다.")
        cs = ds['cons']
        if cs.get('target_mean'):
            s += (f" 증권사 컨센서스 목표주가 평균은 {_won(cs['target_mean'])}"
                  + (f", 투자의견 평균은 {cs['recomm_label']}({cs['recomm_mean']:.2f})" if cs.get('recomm_label') else '')
                  + f"이며 당사 목표주가와의 괴리는 {_pct(fixed.get('consensus_gap_pct'))}이다."
                  if fixed.get('consensus_gap_pct') is not None else f" 증권사 컨센서스 목표주가 평균은 {_won(cs['target_mean'])}이다.")
        highlights = [t for t in (fixed.get('points') or []) if t and len(t) >= 5][:3]
        if fixed.get('risks'):
            highlights.append(f"주요 리스크: {fixed['risks'][0]}")
        while len(highlights) < 2:
            highlights.append(f"에이전트 합의 신호 '{v['consensus_signal']}'")
        return {'headline': self._rule_headline(ds, fixed), 'summary': s, 'highlights': highlights[:4]}

    def _finalize_investment_summary(self, data, ds, fixed):
        data = dict(data)
        data.update(rating=fixed['rating'], target_price=fixed.get('target_price'), last_close=fixed.get('last_close'),
                    upside_pct=fixed.get('upside_pct'), fair_value=fixed.get('fair_value'),
                    consensus_signal=ds['votes']['consensus_signal'],
                    consensus_target=fixed.get('consensus_target'), consensus_gap_pct=fixed.get('consensus_gap_pct'),
                    consensus_note=fixed.get('consensus_note'))
        return data

    # ───────────────────────────────────────────────────────────── 마크다운 렌더링
    def _render_section(self, key: str, d: Dict[str, Any], ds: Dict[str, Any]) -> str:
        try:
            return getattr(self, f"_md_{key}")(d, ds).strip()
        except Exception as e:  # 렌더 실패 시에도 content 는 채운다
            logger.warning(f"섹션 렌더링 실패 ({key}): {e}")
            return f"[섹션 생성 중 오류 발생: {str(e)[:200]}]"

    @staticmethod
    def _ev_line(ids) -> str:
        return f"근거: {_refs_text(ids)}" if ids else ''

    def _md_investment_summary(self, d, ds):
        lines = [f"**{d.get('headline') or ''}**", "",
                 "| 투자의견 | 목표주가 | 현재주가 | 상승여력 |", "|---|---|---|---|",
                 f"| {d.get('rating')} | {_won(d.get('target_price'))} | {_won(d.get('last_close'))} | {_pct(d.get('upside_pct'))} |",
                 "", d.get('summary') or '', ""]
        lines += [f"- {h}" for h in d.get('highlights') or []]
        return "\n".join(lines)

    def _md_investment_points(self, d, ds):
        out = []
        for i, p in enumerate(d.get('points') or [], 1):
            out += [f"### {i}. {p.get('title')}", "", p.get('body') or '']
            ids = [x for x in (p.get('evidence') or []) if f"[{x}]" not in (p.get('body') or '')]
            if ids:
                out += ["", self._ev_line(ids)]
            out.append("")
        return "\n".join(out)

    def _md_earnings_analysis(self, d, ds):
        c = d.get('computed') or {}
        eps, div, prof = d.get('eps') or {}, d.get('dividend') or {}, d.get('profitability') or {}
        trend = eps.get('trend') or '데이터 없음'
        if c.get('eps_change_pct') is not None:
            trend += f" ({_won(c.get('eps_first'))} → {_won(c.get('eps'))}, {_pct(c.get('eps_change_pct'))})"
        fwd = d.get('forward') or {}
        rows = ["| 항목 | 값 | 비고 |", "|---|---|---|"]
        if fwd.get('eps') is not None:
            rows += [f"| 선행 EPS(컨센서스) | {_won(fwd.get('eps'))} | 증권사 컨센서스 |",
                     f"| 선행 EPS 성장률 | {_pct(fwd.get('growth_pct'))} | 선행 EPS ÷ 후행 EPS − 1 (핵심 지표) |"]
        rows += [
            f"| EPS(후행) | {_won(eps.get('value'))} | {'현재가÷PER 산출 · ' if c.get('eps_derived') else ''}추이: {trend} |",
            f"| PER | {_mult(d.get('per'))} | |",
            f"| BPS | {_won(c.get('bps'))} | {'현재가÷PBR 산출' if c.get('bps_derived') else ''} |",
            f"| PBR | {_mult(d.get('pbr'))} | |",
            f"| ROE | {_pct(prof.get('roe_pct'), signed=False) if prof.get('roe_pct') is not None else '데이터 없음'} | "
            f"{'EPS÷BPS 산출' if prof.get('roe_pct') is not None else ''} |",
            f"| 배당수익률 | {_pct(div.get('yield_pct'), signed=False, nd=2) if div.get('yield_pct') is not None else '데이터 없음'} | |",
            f"| 주당배당금(DPS) | {_won(div.get('dps'))} | {'현재가×배당수익률 산출' if c.get('dps_derived') and div.get('dps') is not None else ''} |",
        ]
        if not d.get('quarters'):
            rows.append("| 매출액·영업이익·순이익 | 데이터 없음 | 수집 범위 밖 |")
        out = rows + [""]
        if d.get('quarters'):
            src = 'OpenDART 공시' if d.get('quarter_source') == 'dart' else '네이버 금융'
            out += [f"**분기 실적** (단위 억원 · 출처 {src})", "",
                    "| 분기 | 매출액 | 영업이익 | 영업이익률 | 순이익 |", "|---|---:|---:|---:|---:|"]
            for qq in d['quarters']:
                num0 = lambda v: '-' if v is None else f"{v:,.0f}"  # noqa: E731
                out.append(f"| {qq.get('period')}{' (추정)' if qq.get('consensus') else ''} | {num0(qq.get('revenue'))} | "
                           f"{num0(qq.get('op_income'))} | {'-' if qq.get('op_margin') is None else format(qq['op_margin'], '.1f') + '%'} | "
                           f"{num0(qq.get('net_income'))} |")
            out.append("")
            qd = d.get('quarterly') or {}
            if qd.get('comment'):
                out.append(f"- **분기 실적**: {qd['comment']}")
        if fwd.get('comment'):
            out.append(f"- **선행 이익**: {fwd.get('comment')}")
        out += [f"- **EPS**: {eps.get('comment') or ''}", f"- **배당**: {div.get('comment') or ''}"]
        if prof.get('comment'):
            out.append(f"- **수익성**: {prof.get('comment')}")
        out += ["", d.get('summary') or '', ""]
        if d.get('unavailable'):
            out.append(f"*데이터 없음 항목: {', '.join(d['unavailable'])}*")
        if d.get('evidence'):
            out += ["", self._ev_line(d['evidence'])]
        return "\n".join(out)

    def _md_valuation(self, d, ds):
        out = [f"**산정 방식:** {d.get('method') or ''}", ""]
        fw = d.get('forward')
        if fw:
            out += ["| 후행 EPS | 선행 EPS | 선행 EPS 성장률 | 기준 PER | 사이클 할인 | 목표 PER |", "|---|---|---|---|---|---|",
                    f"| {_won(fw.get('trailing_eps'))} | {_won(fw.get('fwd_eps'))} | {_pct(fw.get('growth_pct'))} | "
                    f"{_mult(fw.get('base_per'))} | {_pct(fw.get('cycle_discount_pct'), signed=False, nd=0)} | "
                    f"{_mult((d.get('target_multiple') or {}).get('value') or fw.get('target_per'))} |", ""]
        band_rows = []
        fb = d.get('fwd_band')
        if fb:
            band_rows.append(f"| 선행 EPS × PER 밴드 | {_won(fb['low'])} | {_won(fb['mid'])} | {_won(fb['high'])} |")
        for k, label in (('per_band', '후행 PER' if fw else 'PER'), ('pbr_band', 'PBR')):
            b = d.get(k)
            if b:
                band_rows.append(f"| {label} 밴드(배) | {b['low']:.2f} | {b['mid']:.2f} | {b['high']:.2f} |")
                band_rows.append(f"| {label} 적용 주가 | {_won(b['price_low'])} | {_won(b['price_mid'])} | {_won(b['price_high'])} |")
        w = d.get('band_52w') or {}
        if w.get('low'):
            band_rows.append(f"| 52주 주가 | {_won(w.get('low'))} | {_won(w.get('avg'))} | {_won(w.get('high'))} |")
        if band_rows:
            out += ["| 구분 | 하단 | 중앙 | 상단 |", "|---|---|---|---|"] + band_rows + [""]
            srcs = [f"{lab} 밴드: {d[k]['source']}" for k, lab in (('per_band', 'PER'), ('pbr_band', 'PBR')) if d.get(k)]
            if w.get('low'):
                srcs.append(f"52주 주가: {ds['w52'].get('source', '-')}")
            out += [f"*밴드 산출 근거 — {' · '.join(srcs)}*", ""]
        tm = d.get('target_multiple') or {}
        mult = (f"{tm.get('type')} {_mult(tm.get('value'))}" if tm.get('value') is not None else (tm.get('type') or '-'))
        out += ["| 적정주가 | 목표주가 | 현재주가 | 상승여력 | 목표 배수 |", "|---|---|---|---|---|",
                f"| {_won(d.get('fair_value'))} | {_won(d.get('target_price'))} | {_won(d.get('last_close'))} | "
                f"{_pct(d.get('upside_pct'))} | {mult} |", ""]
        cs = ds['cons']
        if cs.get('target_mean'):
            rec = (cs.get('recomm_label') or '-') + (f" ({cs['recomm_mean']:.2f})" if cs.get('recomm_mean') is not None else '')
            out += ["| 증권사 목표주가 평균 | 당사 대비 괴리 | 투자의견 평균 | 선행 PER | 기준일 |", "|---|---|---|---|---|",
                    f"| {_won(cs['target_mean'])} | {_pct(d.get('consensus_gap_pct'))} | {rec} | "
                    f"{_mult(cs.get('fwd_per'))} | {cs.get('date') or '-'} |", ""]
        tr = d.get('trailing_reference')
        if tr and tr.get('target_price'):
            out += [f"*참고(후행 방식): {tr.get('method')} → {_won(tr['target_price'])} (상승여력 {_pct(tr.get('upside_pct'))})*", ""]
        out.append(d.get('notes') or '')
        if d.get('evidence'):
            out += ["", self._ev_line(d['evidence'])]
        return "\n".join(out)

    def _md_technical_analysis(self, d, ds):
        ind = d.get('indicators') or {}
        out = ["| 지표 | 값 |", "|---|---|",
               f"| 현재가 | {_won(ind.get('last_close'))} |",
               f"| 이동평균(5/20/60일) | {_won(ind.get('ma5'))} / {_won(ind.get('ma20'))} / {_won(ind.get('ma60'))} |",
               f"| 수익률(5/20/60일) | {_pct(ind.get('chg_5d_pct'), nd=2)} / {_pct(ind.get('chg_20d_pct'), nd=2)} / {_pct(ind.get('chg_60d_pct'), nd=2)} |",
               f"| RSI(14) | {ind.get('rsi14') if ind.get('rsi14') is not None else '데이터 없음'} |",
               f"| 20일 일간 변동성 | {_pct(ind.get('daily_vol_20d_pct'), signed=False, nd=2)} |",
               f"| 60일 고가/저가 | {_won(ind.get('high_60d'))} / {_won(ind.get('low_60d'))} |", ""]
        sigs = d.get('signals') or []
        if sigs:
            out += ["| 신호 | 상태 | 판단 |", "|---|---|---|"] + [
                f"| {s.get('name')} | {s.get('value')} | {s.get('reading')} |" for s in sigs] + [""]
        out += [f"**추세:** {d.get('trend')} · **지지선:** {_won(d.get('support'))} · **저항선:** {_won(d.get('resistance'))}",
                "", d.get('summary') or '']
        fl, sh = ds.get('flows') or {}, ds.get('short') or {}
        if fl.get('sum5') or fl.get('sum20'):
            src = 'KRX' if fl.get('source') == 'krx' else '네이버(수량×종가 근사)'
            sg = lambda v: '-' if v is None else f"{v:+,.1f}"  # noqa: E731
            out += ["", f"**투자자별 순매수** (억원 · 출처 {src})", "", "| 기간 | 외국인 | 기관 | 개인 |", "|---|---:|---:|---:|"]
            for lbl, key in (('5거래일', 'sum5'), (f"{(fl.get('sum20') or {}).get('days', 20)}거래일", 'sum20')):
                sm = fl.get(key) or {}
                if sm:
                    out.append(f"| {lbl} | {sg(sm.get('foreign'))} | {sg(sm.get('organ'))} | {sg(sm.get('individual'))} |")
            extra = []
            if fl.get('foreign_ratio') is not None:
                extra.append(f"외국인 보유 비중 {fl['foreign_ratio']:.2f}%"
                             + (f" ({fl['foreign_ratio_chg']:+.2f}%p)" if fl.get('foreign_ratio_chg') is not None else ''))
            if fl.get('foreign_streak'):
                extra.append(f"외국인 {abs(fl['foreign_streak'])}일 연속 순{'매수' if fl['foreign_streak'] > 0 else '매도'}")
            if extra:
                out += ["", ' · '.join(extra) + (f" {_refs_text(self.ev(ds, 'flows'))}" if self.ev(ds, 'flows') else '')]
        if sh.get('ratio_pct') is not None:
            out += ["", f"**공매도 잔고 비중** {sh['ratio_pct']:.2f}% ({sh.get('date')})"
                    + (f" · 20일 변화 {sh['ratio_chg_pp']:+.2f}%p" if sh.get('ratio_chg_pp') is not None else '')
                    + (f" {_refs_text(self.ev(ds, 'short'))}" if self.ev(ds, 'short') else '')]
        ids = [x for x in (d.get('evidence') or []) if f"[{x}]" not in (d.get('summary') or '')]
        if ids:
            out += ["", self._ev_line(ids)]
        return "\n".join(out)

    def _md_consensus_forecast(self, d, ds):
        cnt = d.get('counts') or {}
        out = ["| 구분 | 매수 | 보합 | 매도 |", "|---|---|---|---|",
               f"| 비율 | {d.get('buy_pct')}% | {d.get('hold_pct')}% | {d.get('sell_pct')}% |",
               f"| 인원 | {cnt.get('buy', '-')}명 | {cnt.get('hold', '-')}명 | {cnt.get('sell', '-')}명 |", ""]
        bt = d.get('by_type') or {}
        if bt:
            out += ["| 에이전트 성향 | 매수 | 보합 | 매도 |", "|---|---|---|---|"] + [
                f"| {AGENT_TYPE_KO.get(t, t)} | {c['buy']} | {c['hold']} | {c['sell']} |" for t, c in bt.items()] + [""]
        out += [f"**합의 신호:** {d.get('signal')} · **참여 에이전트:** {d.get('num_agents')}명 · **평균 확신도:** {d.get('avg_confidence')}",
                "", d.get('interpretation') or '']
        ids = [x for x in (d.get('evidence') or []) if f"[{x}]" not in (d.get('interpretation') or '')]
        if ids:
            out += ["", self._ev_line(ids)]
        return "\n".join(out)

    def _md_risk_factors(self, d, ds):
        out = []
        for i, r in enumerate(d.get('risks') or [], 1):
            out += [f"### {i}. {r.get('title')} (영향도: {r.get('severity')})", "", r.get('body') or '']
            ids = [x for x in (r.get('evidence') or []) if f"[{x}]" not in (r.get('body') or '')]
            if ids:
                out += ["", self._ev_line(ids)]
            out.append("")
        return "\n".join(out)

    def _md_investment_opinion(self, d, ds):
        out = [f"**투자의견 {d.get('rating')} · 목표주가 {_won(d.get('target_price'))} · 상승여력 {_pct(d.get('upside_pct'))}** "
               f"(투자기간 {d.get('horizon') or '12개월'})", "", "**투자 논거**"]
        out += [f"- {r}" for r in d.get('rationale') or []]
        out += ["", f"**매매 전략:** {d.get('strategy') or ''}"]
        if d.get('catalysts'):
            out += ["", "**주가 촉매**"] + [f"- {c}" for c in d['catalysts']]
        if d.get('consensus_target'):
            out += ["", f"**증권사 컨센서스:** 목표주가 평균 {_won(d['consensus_target'])} · 당사 목표주가 괴리 "
                        f"{_pct(d.get('consensus_gap_pct'))}"
                        + (f" · 투자의견 평균 {d['consensus_label']}({d['consensus_recomm']:.2f})" if d.get('consensus_label') else '')]
        if d.get('consensus_note'):
            out += ["", f"> **컨센서스 괴리 설명:** {d['consensus_note']}"]
        out += ["", f"> {RATING_POLICY}", "", DISCLAIMER_LINE]
        return "\n".join(out)

    def _assemble_markdown(self, report, ds, aggregated, market_context) -> str:
        s = report['summary']
        now = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
        lines = [
            f"# {report['company_name']}({report['ticker']}) 리서치 리포트",
            "",
            f"> **투자의견:** {s['rating']} · **목표주가:** {_won(s.get('target_price'))} · "
            f"**현재주가:** {_won(s.get('last_close'))} · **상승여력:** {_pct(s.get('upside_pct'))}  ",
            f"> **보고서 ID:** {report['report_id']}  ",
            f"> **작성 일시:** {now} · **기준일:** {s.get('as_of') or '-'} · **분석 기간:** {market_context.get('period', '1y')}",
        ]
        cs = report.get('consensus') or {}
        if cs.get('has'):
            lines[-1] += "  "
            lines.append(f"> **증권사 컨센서스{(' (' + cs['date'] + ')') if cs.get('date') else ''}:** 목표주가 평균 "
                         f"{_won(cs.get('target_mean'))} · 투자의견 평균 {cs.get('recomm_label') or '-'}"
                         f"{(' (%.2f)' % cs['recomm_mean']) if cs.get('recomm_mean') is not None else ''} · 선행 PER "
                         f"{_mult(cs.get('fwd_per'))} · 선행 EPS {_won(cs.get('fwd_eps'))}")
        lines += [
            "",
            f"## {s.get('headline') or '핵심 요약'}",
            "",
            "| 지표 | 값 |",
            "|------|-----|",
            f"| 투자의견 | {s['rating']} |",
            f"| 목표주가 | {_won(s.get('target_price'))} |",
            f"| 현재 주가 | {_won(s.get('last_close'))} |",
            f"| 상승여력 | {_pct(s.get('upside_pct'))} |",
        ]
        if cs.get('target_mean'):
            lines += [f"| 컨센서스 목표주가(평균) | {_won(cs['target_mean'])} |",
                      f"| 컨센서스 대비 괴리 | {_pct(s.get('consensus_gap_pct'))} |"]
        if cs.get('fwd_per'):
            lines.append(f"| 선행 PER | {_mult(cs['fwd_per'])} |")
        lines += [
            f"| 기간 수익률 | {_pct(market_context.get('price_change_pct'), nd=2)} |",
            f"| 주가 추세 | {market_context.get('price_trend', 'N/A')} |",
            f"| 에이전트 합의 | {aggregated['consensus_signal']} |",
            f"| 매수 비율 | {aggregated['buy_pct']}% |",
            f"| 매도 비율 | {aggregated['sell_pct']}% |",
            f"| 보합 비율 | {aggregated['hold_pct']}% |",
            "",
            "---",
            "",
        ]
        for i, (key, sec) in enumerate(report['sections'].items(), 1):
            lines += [f"## {i}. {sec.get('title', key)}", "", sec.get('content', ''), "", "---", ""]
        srcs = [x for x in (report.get('sources') or []) if x.get('kind') != 'broker_report']
        brokers = report.get('broker_reports') or []
        if srcs or brokers:
            lines += ["## 참고 자료", ""]
        if brokers:
            lines += ["### 증권사 리포트", ""]
            for n, b in enumerate(brokers, 1):
                t = f"[{b['title']}]({b['url']})" if b.get('url') else b['title']
                lines.append(f"{n}. [{b.get('id')}] {t} — {b['broker']}{(' · ' + b['date']) if b.get('date') else ''}")
            lines += [""] + (["### 뉴스·공시", ""] if srcs else [])
        if srcs:
            for n, src in enumerate(srcs, 1):
                label = src.get('title') or src.get('claim') or src['id']
                meta = ' · '.join(x for x in (src.get('publisher'), src.get('date')) if x)
                link = f"[{label}]({src['url']})" if src.get('url') else label
                lines.append(f"{n}. [{src['id']}] {link}" + (f" — {meta}" if meta else ''))
        if srcs or brokers:
            lines += ["", "---", ""]
        lines += ["## ⚠️ 면책 조항", "", f"> {report['disclaimer']}", ""]
        return "\n".join(str(x) for x in lines)

    # ───────────────────────────────────────────────────────────── 차트 · 출처 · 예측
    def _build_charts(self, ds, fixed) -> Dict[str, Any]:
        rows, closes = ds['rows'], ds['closes']
        ma20, ma60 = _sma_series(closes, 20), _sma_series(closes, 60)
        tail = rows[-CHART_ROWS:]
        off = len(rows) - len(tail)
        v = ds['votes']
        val = ds['val']
        sec_val = fixed  # 확정값
        per_b, pbr_b = val.get('per_band'), val.get('pbr_band')

        def band_prices(b):
            return None if not b else {'low': b['price_low'], 'mid': b['price_mid'], 'high': b['price_high'],
                                       'multiple_low': b['low'], 'multiple_mid': b['mid'], 'multiple_high': b['high'],
                                       'source': b['source']}
        return {
            'ohlcv': [{'date': r['date'], 'o': r['o'], 'h': r['h'], 'l': r['l'], 'c': r['c'],
                       'v': None if r['v'] is None else int(r['v'])} for r in tail],
            'ma20': ma20[off:], 'ma60': ma60[off:],
            'votes': {
                'by_decision': {'buy': v['buy_count'], 'hold': v['hold_count'], 'sell': v['sell_count']},
                'pct': {'buy': v['buy_pct'], 'hold': v['hold_pct'], 'sell': v['sell_pct']},
                'by_agent_type': v.get('by_type') or {},
            },
            'valuation_band': {
                'last_close': sec_val.get('last_close'), 'target_price': sec_val.get('target_price'),
                'fair_value': sec_val.get('fair_value'),
                'w52_low': ds['w52'].get('low'), 'w52_high': ds['w52'].get('high'), 'w52_avg': ds['w52'].get('avg'),
                'per': band_prices(per_b), 'pbr': band_prices(pbr_b), 'fwd': val.get('fwd_band'),
                'consensus_target': ds['cons'].get('target_mean'),
                'support': sec_val.get('support'), 'resistance': sec_val.get('resistance'),
            },
        }

    @staticmethod
    def _sources(ds) -> List[Dict[str, Any]]:
        out = []
        for x in ds['findings']:
            url = x.get('url') if isinstance(x.get('url'), str) and re.match(r'^https?://', x.get('url') or '') else None
            out.append({'id': x['id'], 'title': (x.get('title') or '')[:200], 'publisher': x.get('publisher'),
                        'url': url, 'date': x.get('date'), 'kind': x.get('kind'), 'topic': x.get('topic'),
                        'sentiment': x.get('sentiment'), 'claim': x.get('claim')})
        for b in ds['brokers']:
            if b.get('url'):
                out.append({'id': b['id'], 'title': b['title'], 'publisher': b['broker'], 'url': b['url'],
                            'date': b.get('date'), 'kind': 'broker_report', 'topic': None, 'sentiment': None, 'claim': None})
        return out

    @staticmethod
    def _prediction(agg, fixed) -> Dict[str, Any]:
        """기존 3단계 화면(parsePredictionSummary)이 읽는 예측 요약 — 방향은 StockJudge._derive_prediction 과 동일 규칙"""
        buy, sell, hold = agg['buy_pct'], agg['sell_pct'], agg['hold_pct']
        if buy - sell >= 10:
            d, prob = "상승", buy
        elif sell - buy >= 10:
            d, prob = "하락", sell
        else:
            d, prob = "보합", hold
        tgt = fixed.get('target_price')
        return {"direction": d, "probability": prob, "price_target": f"{tgt:,.0f}원" if tgt else None,
                "rating": fixed.get('rating')}

    # ───────────────────────────────────────────────────────────── 기존 집계 (호환 유지)
    def _aggregate_simulation_data(self, simulation_data: Dict[str, Any]) -> Dict[str, Any]:
        """시뮬레이션 데이터에서 에이전트들의 투자 결정을 집계합니다."""
        simulation_data = simulation_data or {}
        agents_decisions = simulation_data.get('agents_decisions', []) or []
        summary = simulation_data.get('summary', {}) or {}
        num_agents = simulation_data.get('num_agents', len(agents_decisions))

        buy_count = int(summary.get('buy_count', 0) or 0)
        sell_count = int(summary.get('sell_count', 0) or 0)
        hold_count = int(summary.get('hold_count', 0) or 0)
        if not summary and agents_decisions:
            buy_count = sum(1 for d in agents_decisions if d.get('decision') == 'buy')
            sell_count = sum(1 for d in agents_decisions if d.get('decision') == 'sell')
            hold_count = sum(1 for d in agents_decisions if d.get('decision') == 'hold')
        total = max(1, buy_count + sell_count + hold_count)

        type_distribution: Dict[str, int] = {}
        avg_confidence = 0.0
        if agents_decisions:
            for d in agents_decisions:
                agent_type = d.get('agent_type', 'unknown')
                type_distribution[agent_type] = type_distribution.get(agent_type, 0) + 1
            confidences = [d.get('confidence', 0) for d in agents_decisions if isinstance(d.get('confidence'), (int, float))]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        buy_pct = buy_count / total * 100
        sell_pct = sell_count / total * 100
        hold_pct = hold_count / total * 100
        return {
            "num_agents": num_agents or total,
            "buy_count": buy_count, "sell_count": sell_count, "hold_count": hold_count,
            "buy_pct": round(buy_pct, 1), "sell_pct": round(sell_pct, 1), "hold_pct": round(hold_pct, 1),
            "consensus_signal": _consensus_signal(buy_pct, sell_pct),
            "type_distribution": type_distribution,
            "avg_confidence": round(avg_confidence, 2),
            "simulation_rounds": simulation_data.get('simulation_rounds', 10)
        }

    def _extract_market_context(self, financial_data: Dict[str, Any]) -> Dict[str, Any]:
        """재무 데이터에서 시장 컨텍스트 정보를 추출합니다 (마크다운 핵심 요약 표용)."""
        rows = _norm_rows((financial_data or {}).get('ohlcv', []) or [])
        context = {
            "has_data": bool(rows), "data_points": len(rows),
            "period": (financial_data or {}).get('period', '1y'),
            "current_price": None, "price_change_pct": None, "avg_volume": None,
            "price_trend": "데이터 없음", "volatility": "데이터 없음"
        }
        if not rows:
            return context
        prices = [r['c'] for r in rows]
        context["current_price"] = prices[-1]
        if len(prices) >= 2:
            context["price_change_pct"] = round((prices[-1] - prices[0]) / prices[0] * 100, 2)
        if len(prices) >= 20:
            ma20 = sum(prices[-20:]) / 20
            if prices[-1] > ma20 * 1.03:
                context["price_trend"] = "강한 상승 추세"
            elif prices[-1] > ma20:
                context["price_trend"] = "약한 상승 추세"
            elif prices[-1] < ma20 * 0.97:
                context["price_trend"] = "강한 하락 추세"
            else:
                context["price_trend"] = "약한 하락 추세"
        else:
            pct = context.get("price_change_pct") or 0
            context["price_trend"] = "상승 추세" if pct > 5 else "하락 추세" if pct < -5 else "횡보 추세"
        if len(prices) >= 5:
            recent = prices[-min(20, len(prices)):]
            avg = sum(recent) / len(recent)
            std_pct = ((sum((p - avg) ** 2 for p in recent) / len(recent)) ** 0.5) / avg * 100 if avg > 0 else 0
            label = "고변동성" if std_pct > 5 else "중변동성" if std_pct > 2 else "저변동성"
            context["volatility"] = f"{label} ({std_pct:.1f}%)"
        vols = [r['v'] for r in rows if r.get('v')]
        if vols:
            context["avg_volume"] = int(sum(vols) / len(vols))
        return context


class _StreamMux:
    """병렬 섹션의 Grok 스트리밍 수신량을 합산해 하나의 진행 표시(sink)로 전달"""

    def __init__(self, outer):
        self.outer = outer
        self.lock = threading.Lock()
        self.counts: Dict[str, Tuple[int, int]] = {}
        self.t0 = time.time()
        self.last = 0.0

    def close(self):
        """단계가 끝난 뒤 늦게 도착한 스트림 수신량은 진행 표시에 반영하지 않음"""
        with self.lock:
            self.closed = True

    def sink_for(self, key):
        def _sink(n_content, n_reason, _sec):
            with self.lock:
                if getattr(self, 'closed', False):
                    return
                self.counts[key] = (n_content, n_reason)
                now = time.time()
                if now - self.last < 0.8:
                    return
                self.last = now
                tc = sum(c for c, _ in self.counts.values())
                tr = sum(r for _, r in self.counts.values())
            try:
                self.outer(tc, tr, now - self.t0)
            except Exception:
                pass
        return _sink


class StockReportManager:
    """
    주식 보고서 파일 시스템 관리자

    보고서를 파일 시스템에 저장하고 조회하는 기능을 제공합니다.
    기존 ReportManager 패턴을 참고하여 주식 보고서에 맞게 구성합니다.
    """

    # 보고서 저장 기본 디렉토리
    REPORTS_BASE_DIR = os.path.join(
        os.path.dirname(__file__),
        '../../uploads/stock_reports'
    )

    @classmethod
    def _get_report_dir(cls, report_id: str) -> str:
        """보고서 디렉토리 경로를 반환합니다."""
        report_dir = os.path.join(cls.REPORTS_BASE_DIR, report_id)
        os.makedirs(report_dir, exist_ok=True)
        return report_dir

    @classmethod
    def save_report(cls, report: Dict[str, Any]) -> bool:
        """
        보고서를 파일 시스템에 저장합니다.

        Args:
            report: 보고서 딕셔너리

        Returns:
            저장 성공 여부
        """
        try:
            report_id = report.get('report_id')
            if not report_id:
                logger.error("보고서 ID가 없습니다")
                return False

            report_dir = cls._get_report_dir(report_id)

            # 보고서 메타데이터 저장 (JSON)
            meta_path = os.path.join(report_dir, 'report.json')
            # markdown_content는 별도 파일로 저장하여 JSON 크기 최소화
            report_meta = {k: v for k, v in report.items() if k != 'markdown_content'}
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(report_meta, f, ensure_ascii=False, indent=2)

            # Markdown 보고서 별도 저장
            markdown_content = report.get('markdown_content', '')
            if markdown_content:
                md_path = os.path.join(report_dir, 'report.md')
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(markdown_content)

            logger.info(f"보고서 저장 완료: {report_id}")
            return True

        except Exception as e:
            logger.error(f"보고서 저장 실패: {str(e)}")
            return False

    @classmethod
    def get_report(cls, report_id: str) -> Optional[Dict[str, Any]]:
        """
        보고서를 파일 시스템에서 로드합니다.

        Args:
            report_id: 보고서 ID

        Returns:
            보고서 딕셔너리 또는 None
        """
        try:
            report_dir = os.path.join(cls.REPORTS_BASE_DIR, report_id)
            meta_path = os.path.join(report_dir, 'report.json')

            if not os.path.exists(meta_path):
                return None

            with open(meta_path, 'r', encoding='utf-8') as f:
                report = json.load(f)

            # Markdown 내용 로드
            md_path = os.path.join(report_dir, 'report.md')
            if os.path.exists(md_path):
                with open(md_path, 'r', encoding='utf-8') as f:
                    report['markdown_content'] = f.read()
            else:
                report['markdown_content'] = ''

            return report

        except Exception as e:
            logger.error(f"보고서 로드 실패 ({report_id}): {str(e)}")
            return None

    @classmethod
    def get_report_by_simulation(cls, simulation_id: str) -> Optional[Dict[str, Any]]:
        """
        시뮬레이션 ID로 보고서를 검색합니다.

        Args:
            simulation_id: 시뮬레이션 ID

        Returns:
            보고서 딕셔너리 또는 None
        """
        try:
            if not os.path.exists(cls.REPORTS_BASE_DIR):
                return None

            # 모든 보고서 디렉토리 순회
            for report_id in os.listdir(cls.REPORTS_BASE_DIR):
                report_path = os.path.join(cls.REPORTS_BASE_DIR, report_id)
                # 숨김 파일 및 비디렉토리 건너뜀
                if report_id.startswith('.') or not os.path.isdir(report_path):
                    continue

                meta_path = os.path.join(report_path, 'report.json')
                if not os.path.exists(meta_path):
                    continue

                try:
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        report_meta = json.load(f)

                    if report_meta.get('simulation_id') == simulation_id:
                        # Markdown 내용 추가
                        md_path = os.path.join(report_path, 'report.md')
                        if os.path.exists(md_path):
                            with open(md_path, 'r', encoding='utf-8') as f:
                                report_meta['markdown_content'] = f.read()
                        else:
                            report_meta['markdown_content'] = ''
                        return report_meta
                except Exception:
                    continue

            return None

        except Exception as e:
            logger.error(f"시뮬레이션 ID로 보고서 검색 실패 ({simulation_id}): {str(e)}")
            return None

    @classmethod
    def list_reports(
        cls,
        ticker: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        보고서 목록을 반환합니다.

        Args:
            ticker: 종목 코드 필터 (선택)
            limit: 반환 최대 건수

        Returns:
            보고서 요약 목록
        """
        reports = []

        try:
            if not os.path.exists(cls.REPORTS_BASE_DIR):
                return []

            for report_id in os.listdir(cls.REPORTS_BASE_DIR):
                report_path = os.path.join(cls.REPORTS_BASE_DIR, report_id)
                if report_id.startswith('.') or not os.path.isdir(report_path):
                    continue

                meta_path = os.path.join(report_path, 'report.json')
                if not os.path.exists(meta_path):
                    continue

                try:
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        report_meta = json.load(f)

                    # 종목 코드 필터 적용
                    if ticker and report_meta.get('ticker') != ticker:
                        continue

                    # 무거운 필드(본문·차트·사실표) 제외한 요약 정보만 반환
                    summary = {
                        k: v for k, v in report_meta.items()
                        if k not in ('markdown_content', 'sections', 'charts', 'facts', 'sources')
                    }
                    reports.append(summary)

                except Exception:
                    continue

            # 생성 시각 기준 내림차순 정렬
            reports.sort(
                key=lambda r: r.get('created_at', ''),
                reverse=True
            )
            return reports[:limit]

        except Exception as e:
            logger.error(f"보고서 목록 조회 실패: {str(e)}")
            return []

    @classmethod
    def delete_report(cls, report_id: str) -> bool:
        """
        보고서를 삭제합니다.

        Args:
            report_id: 보고서 ID

        Returns:
            삭제 성공 여부
        """
        import shutil

        try:
            report_dir = os.path.join(cls.REPORTS_BASE_DIR, report_id)
            if not os.path.exists(report_dir):
                return False

            shutil.rmtree(report_dir)
            logger.info(f"보고서 삭제 완료: {report_id}")
            return True

        except Exception as e:
            logger.error(f"보고서 삭제 실패 ({report_id}): {str(e)}")
            return False
