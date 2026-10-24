"""
다국어 금융 감성 분석 (뉴스 제목·공시·커뮤니티 글)

엔진 (우선순위 순, 실패 시 자동 폴백)
  1. local   : Hugging Face `tabularisai/multilingual-sentiment-analysis` (5단계 → 3단계 + 점수)
               torch·transformers 가 설치된 PC 에서만, 첫 사용 시 한 번 지연 로딩 (CPU 10~30초)
  2. llm     : Grok 일괄 판정 (StockKnowledgeGraph()._llm_json, 호출당 최대 40문장)
  3. lexicon : 한국어·영어 금융 사전 + 부정어·강조어 처리 (결정적, 예외 없음)
  off        : 모두 중립

공개 API
  analyze(texts, mode=None) -> (results, engine)
      results = [{"label": "positive|negative|neutral", "score": -1..1, "confidence": 0..1, "engine": "..."}]
  summarize(results) -> {"pos", "neg", "neu", "n", "score"}
  status() -> {"mode", "local_available", "model", "loaded", "engine_last", "load_error", ...}

환경 변수
  SENTIMENT_MODE  = auto(기본) | local | llm | lexicon | off
  SENTIMENT_MODEL = 로컬 모델 이름/경로 (기본 tabularisai/multilingual-sentiment-analysis)
"""

import hashlib
import importlib.util
import math
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from ..utils.logger import get_logger

logger = get_logger('mirofish.sentiment')

DEFAULT_MODEL = 'tabularisai/multilingual-sentiment-analysis'
MODES = ('auto', 'ensemble', 'local', 'llm', 'lexicon', 'off')
LABELS = ('positive', 'negative', 'neutral')
# 실측: 범용 로컬 모델은 한국어 금융 헤드라인 톤을 잘 못 잡아 Grok(llm)을 더 높은 품질로 취급
ENGINE_RANK = {'llm': 0, 'local': 1, 'lexicon': 2}

MAX_CHARS = 400          # 문장당 최대 길이 (모델·LLM 입력)
LOCAL_MAX_TOKENS = 256
LOCAL_BATCH = 16
LLM_CHUNK = 40
LOAD_FAIL_TTL = 600      # 로컬 모델 로딩 실패 기억 (초)
LLM_FAIL_TTL = 300       # LLM 전체 실패 기억 (초)
CACHE_MAX = 5000

# 5단계 모델 라벨 → 점수
_FIVE = (-1.0, -0.5, 0.0, 0.5, 1.0)

_state: Dict[str, Any] = {
    'pipe': None, 'loading': False, 'load_error': None, 'load_error_at': 0.0,
    'loaded_at': None, 'load_sec': None,
    'llm_error': None, 'llm_error_at': 0.0, 'llm_model': None,
    'engine_last': None,
}
_load_lock = threading.Lock()
_flag_lock = threading.Lock()
_infer_lock = threading.Lock()
_cache: 'OrderedDict[str, Dict[str, Any]]' = OrderedDict()
_cache_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════ 공통
def _model_name() -> str:
    return (os.environ.get('SENTIMENT_MODEL') or '').strip() or DEFAULT_MODEL


def _resolve_mode(mode: Optional[str]) -> str:
    m = (mode if mode is not None else os.environ.get('SENTIMENT_MODE', 'auto')) or 'auto'
    m = str(m).strip().lower()
    return m if m in MODES else 'auto'


def _clean(t: Any) -> str:
    if t is None:
        return ''
    s = re.sub(r'\s+', ' ', str(t)).strip()
    return s[:MAX_CHARS]


def _neutral(engine: str, confidence: float = 0.0) -> Dict[str, Any]:
    return {'label': 'neutral', 'score': 0.0, 'confidence': round(confidence, 3), 'engine': engine}


def _label_from_score(score: float, th: float = 0.15) -> str:
    if score >= th:
        return 'positive'
    if score <= -th:
        return 'negative'
    return 'neutral'


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _key(text: str) -> str:
    return hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()


def _cache_get(engine: str, h: str) -> Optional[Dict[str, Any]]:
    with _cache_lock:
        v = _cache.get(f'{engine}:{h}')
        if v is not None:
            _cache.move_to_end(f'{engine}:{h}')
            return dict(v)
    return None


def _cache_put(engine: str, h: str, v: Dict[str, Any]) -> None:
    with _cache_lock:
        _cache[f'{engine}:{h}'] = dict(v)
        _cache.move_to_end(f'{engine}:{h}')
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def _llm_key_available() -> bool:
    for k in ('XAI_API_KEY', 'AI_GATEWAY_API_KEY'):
        if (os.environ.get(k) or '').strip():
            return True
    try:
        from ..config import Config
        return bool((getattr(Config, 'XAI_API_KEY', None) or '').strip()
                    or (getattr(Config, 'AI_GATEWAY_API_KEY', None) or '').strip())
    except Exception:
        return False


def _llm_failed_recently() -> bool:
    return bool(_state['llm_error']) and time.time() - _state['llm_error_at'] < LLM_FAIL_TTL


# ═══════════════════════════════════════════════════════════════ 1) 로컬 모델
def _local_importable() -> bool:
    """torch·transformers 설치 여부만 확인 (실제 import·로딩 없음)."""
    for name in ('torch', 'transformers'):
        if name in sys.modules:
            if sys.modules[name] is None:
                return False
            continue
        try:
            if importlib.util.find_spec(name) is None:
                return False
        except (ImportError, ValueError, AttributeError):
            return False
    return True


def _local_failed_recently() -> bool:
    return bool(_state['load_error']) and time.time() - _state['load_error_at'] < LOAD_FAIL_TTL


def _model_in_hf_cache(model: str) -> Optional[bool]:
    """True=로컬 캐시에 있음, False=없음(다운로드 필요), None=확인 불가."""
    if os.path.isdir(model):
        return True
    try:
        from huggingface_hub import try_to_load_from_cache
    except Exception:
        return None
    try:
        return isinstance(try_to_load_from_cache(model, 'config.json'), str)
    except Exception:
        return None


def _do_load() -> None:
    """_load_lock 을 잡은 상태에서 호출."""
    if _state['pipe'] is not None or _local_failed_recently():
        return
    model = _model_name()
    with _flag_lock:
        _state['loading'] = True
    t0 = time.time()
    try:
        import torch  # noqa: F401  (설치·DLL 로딩 확인)
        from transformers import pipeline
        try:
            pipe = pipeline('text-classification', model=model, device=-1,
                            truncation=True, max_length=LOCAL_MAX_TOKENS)
        except (TypeError, ValueError):   # 생성자에서 토크나이저 인자를 받지 않는 버전 → 호출 시 전달
            pipe = pipeline('text-classification', model=model, device=-1)
        _state.update(pipe=pipe, loaded_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                      load_sec=round(time.time() - t0, 1), load_error=None, load_error_at=0.0)
        logger.info(f"로컬 감성 모델 로딩 완료: {model} ({_state['load_sec']}초)")
    except BaseException as e:  # noqa: BLE001 — DLL 오류 등 비정상 예외까지 흡수
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        _state.update(load_error=f'{type(e).__name__}: {str(e)[:200]}', load_error_at=time.time())
        logger.warning(f"로컬 감성 모델 로딩 실패 → 10분간 폴백 사용: {_state['load_error']}")
    finally:
        with _flag_lock:
            _state['loading'] = False


def _bg_load() -> None:
    try:
        with _load_lock:
            _do_load()
    finally:
        with _flag_lock:
            _state['loading'] = False


def _start_bg_load() -> None:
    with _flag_lock:
        if _state['loading'] or _state['pipe'] is not None:
            return
        _state['loading'] = True
    threading.Thread(target=_bg_load, name='sentiment-model-load', daemon=True).start()


def warmup(delay_sec: float = 8.0) -> bool:
    """백엔드 시작 직후 로컬 감성 모델을 미리 올려 둠 (첫 분석에서 25초가량 기다리지 않도록).
    모델이 이미 내려받아져 있고 SENTIMENT_MODE 가 auto·local 일 때만. 백그라운드에서 실행."""
    mode = (os.environ.get('SENTIMENT_MODE') or 'auto').strip().lower()
    if mode not in ('auto', 'local') or _state['pipe'] is not None or not _local_importable():
        return False
    if _model_in_hf_cache(_model_name()) is False:
        return False

    def run():
        time.sleep(max(0.0, delay_sec))
        _start_bg_load()
    threading.Thread(target=run, name='sentiment-warmup', daemon=True).start()
    return True


def _get_local(wait: bool):
    """로컬 파이프라인 반환 (없으면 None).
    wait=False(auto): 모델이 캐시에 없으면 백그라운드 다운로드를 시작하고 이번 호출은 폴백.
                      다른 스레드가 로딩 중이면 기다리지 않음.
    wait=True(local): 로딩이 끝날 때까지 대기."""
    if _state['pipe'] is not None:
        return _state['pipe']
    if _local_failed_recently() or not _local_importable():
        return None
    if not wait:
        if _state['loading']:
            return None
        if _model_in_hf_cache(_model_name()) is False:
            logger.info("로컬 감성 모델이 캐시에 없어 백그라운드 다운로드를 시작합니다 (이번 요청은 폴백 엔진 사용)")
            _start_bg_load()
            return None
    with _load_lock:
        _do_load()
    return _state['pipe']


def _norm_label_value(label: Any) -> Optional[float]:
    s = re.sub(r'[_\-]+', ' ', str(label or '')).strip().lower()
    m = re.fullmatch(r'label\s*(\d+)', s)
    if m:
        i = int(m.group(1))
        return _FIVE[i] if 0 <= i < 5 else None
    m = re.match(r'(\d)\s*stars?', s)
    if m:
        n = int(m.group(1))
        return (n - 3) / 2.0 if 1 <= n <= 5 else None
    strong = any(w in s for w in ('very', 'extremely', 'strongly', 'highly'))
    if 'neg' in s or '부정' in s:
        return -1.0 if strong or '매우' in s else -0.5
    if 'pos' in s or '긍정' in s:
        return 1.0 if strong or '매우' in s else 0.5
    if 'neu' in s or '중립' in s:
        return 0.0
    return None


def _map_five(scores: List[Dict[str, Any]]) -> Dict[str, Any]:
    """[{label, score}] (5개 클래스 확률) → 3단계 라벨 + 기대값 점수."""
    probs: List[Tuple[float, float]] = []
    for d in scores or []:
        if not isinstance(d, dict):
            continue
        v = _norm_label_value(d.get('label'))
        try:
            p = float(d.get('score'))
        except (TypeError, ValueError):
            continue
        if v is None or not math.isfinite(p):
            continue
        probs.append((v, max(0.0, p)))
    if not probs:
        return _neutral('local')
    total = sum(p for _, p in probs)
    if len(probs) == 1:          # top-1 만 받은 경우
        v, p = probs[0]
        score = v * p
        conf = p
    else:
        if total <= 0:
            return _neutral('local')
        score = sum(v * p for v, p in probs) / total
        conf = max(p for _, p in probs) / total
    pos = sum(p for v, p in probs if v > 0)
    neg = sum(p for v, p in probs if v < 0)
    neu = sum(p for v, p in probs if v == 0)
    best = max(pos, neg, neu)
    if len(probs) == 1:
        v1 = probs[0][0]
        label = 'positive' if v1 > 0 else 'negative' if v1 < 0 else 'neutral'
    elif (pos == best) and (neg == best or neu == best) or (neg == best and neu == best):
        label = 'neutral'        # 동률은 중립
    else:
        label = 'positive' if pos == best else 'negative' if neg == best else 'neutral'
    # 라벨과 점수 부호가 어긋나면(양극단 혼재) 중립 처리
    if (label == 'positive' and score < 0) or (label == 'negative' and score > 0):
        label = 'neutral'
    return {'label': label, 'score': round(_clip(score), 4), 'confidence': round(_clip(conf, 0.0, 1.0), 4),
            'engine': 'local'}


def _normalize_pipe_output(out: Any, n: int) -> List[List[Dict[str, Any]]]:
    if isinstance(out, dict):
        out = [out]
    out = list(out or [])
    if n == 1 and out and all(isinstance(x, dict) for x in out):
        return [out]
    rows: List[List[Dict[str, Any]]] = []
    for x in out:
        if isinstance(x, dict):
            rows.append([x])
        elif isinstance(x, (list, tuple)):
            rows.append([d for d in x if isinstance(d, dict)])
        else:
            rows.append([])
    return rows


def _local_predict(pipe, texts: List[str]) -> List[Optional[Dict[str, Any]]]:
    attempts = (
        dict(batch_size=LOCAL_BATCH, top_k=None, truncation=True, max_length=LOCAL_MAX_TOKENS),
        dict(batch_size=LOCAL_BATCH, return_all_scores=True, truncation=True, max_length=LOCAL_MAX_TOKENS),
        dict(truncation=True, max_length=LOCAL_MAX_TOKENS),
    )
    last: Optional[Exception] = None
    out = None
    with _infer_lock:
        for kw in attempts:
            try:
                out = pipe(list(texts), **kw)
                break
            except Exception as e:  # noqa: BLE001
                last = e
    if out is None:
        raise RuntimeError(f'로컬 감성 추론 실패: {last}')
    rows = _normalize_pipe_output(out, len(texts))
    if len(rows) != len(texts):
        raise RuntimeError(f'로컬 감성 추론 결과 개수 불일치 ({len(rows)} != {len(texts)})')
    return [_map_five(r) for r in rows]


# ═══════════════════════════════════════════════════════════════ 2) LLM (Grok)
_LLM_SYSTEM = """당신은 한국 주식시장 전문 금융 감성 분석가입니다. 번호가 매겨진 각 문장(뉴스 제목·공시·증권 커뮤니티 글)이
해당 종목 주가에 주는 신호를 판정하세요.

판정 기준
- positive: 주가·실적에 우호적. 예) 목표가 상향, 투자의견 상향, 어닝 서프라이즈, 호실적, 흑자전환, 대규모 수주·공급계약,
  자사주 매입·소각, 배당 확대, 신고가, 외국인·기관 순매수, 급등, 규제 완화, 우려 해소
- negative: 불리. 예) 목표가 하향, 투자의견 하향, 어닝 쇼크, 실적 부진, 적자전환·적자 확대, 소송·피소, 과징금·제재,
  유상증자, 전환사채(CB) 발행, 감자, 횡령·배임, 리콜, 거래정지, 급락·신저가, 순매도
- neutral: 단순 사실 전달, 일정·행사 공지, 질문, 방향성이 불분명하거나 긍정·부정이 상쇄되는 경우
- 부정문과 반어를 반영하세요(예: "실적 개선되지 않았다" → negative, "우려와 달리 선방" → positive).
- 커뮤니티 은어도 해석하세요(떡상·가즈아 → positive, 떡락·물렸다·손절 → negative).
- score: -1.0(매우 부정) ~ 1.0(매우 긍정). neutral 은 -0.2 ~ 0.2 사이.
- 입력된 모든 번호에 대해 정확히 하나의 항목을 반환하세요.

출력은 JSON 하나만: {"items":[{"i":1,"label":"positive|negative|neutral","score":0.0}]}"""

_LABEL_ALIASES = {
    'positive': 'positive', 'pos': 'positive', 'bullish': 'positive', '긍정': 'positive', '호재': 'positive',
    'negative': 'negative', 'neg': 'negative', 'bearish': 'negative', '부정': 'negative', '악재': 'negative',
    'neutral': 'neutral', 'neu': 'neutral', 'mixed': 'neutral', '중립': 'neutral',
}

_kg_singleton = None
_kg_lock = threading.Lock()


def _kg():
    global _kg_singleton
    with _kg_lock:
        if _kg_singleton is None:
            from .stock_knowledge_graph import StockKnowledgeGraph  # 지연 import (순환 참조 방지)
            _kg_singleton = StockKnowledgeGraph()
        return _kg_singleton


def _parse_llm_items(data: Any, k: int) -> Dict[int, Dict[str, Any]]:
    items = None
    if isinstance(data, dict):
        for key in ('items', 'results', 'data', 'sentiments'):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    elif isinstance(data, list):
        items = data
    out: Dict[int, Dict[str, Any]] = {}
    for pos, it in enumerate(items or []):
        if not isinstance(it, dict):
            continue
        idx = it.get('i', it.get('id', it.get('index', it.get('n'))))
        try:
            i = int(str(idx).strip().lstrip('#'))
        except (TypeError, ValueError):
            continue
        if not 1 <= i <= k or i in out:
            continue
        label = _LABEL_ALIASES.get(str(it.get('label') or it.get('sentiment') or '').strip().lower())
        score: Optional[float]
        try:
            score = float(it.get('score'))
            if not math.isfinite(score):
                score = None
        except (TypeError, ValueError):
            score = None
        if label is None and score is None:
            continue
        if score is not None:
            score = _clip(score)
        if label is None:
            label = _label_from_score(score)
        if score is None:
            score = 0.6 if label == 'positive' else -0.6 if label == 'negative' else 0.0
        # 라벨·점수 정합성 (라벨 우선)
        if label == 'positive' and score <= 0:
            score = max(0.3, abs(score))
        elif label == 'negative' and score >= 0:
            score = -max(0.3, abs(score))
        elif label == 'neutral':
            score = _clip(score, -0.25, 0.25)
        conf = 0.6 if label == 'neutral' else min(0.95, 0.55 + 0.4 * abs(score))
        out[i] = {'label': label, 'score': round(score, 4), 'confidence': round(conf, 3), 'engine': 'llm'}
    return out


def _llm_predict(texts: List[str]) -> List[Optional[Dict[str, Any]]]:
    res: List[Optional[Dict[str, Any]]] = [None] * len(texts)
    if not texts or _llm_failed_recently():
        return res
    try:
        kg = _kg()
    except Exception as e:  # noqa: BLE001
        _state.update(llm_error=f'{type(e).__name__}: {str(e)[:200]}', llm_error_at=time.time())
        logger.warning(f"감성 LLM 초기화 실패 → 사전 기반 폴백: {e}")
        return res
    for start in range(0, len(texts), LLM_CHUNK):
        chunk = texts[start:start + LLM_CHUNK]
        k = len(chunk)
        lines = '\n'.join(f'{j + 1}. {t.replace(chr(10), " ")}' for j, t in enumerate(chunk))
        user = f"다음 {k}개 문장의 감성을 판정하세요. 번호 1~{k} 모두 반환.\n\n{lines}"
        try:
            data, model = kg._llm_json(_LLM_SYSTEM, user, max_tokens=min(3000, 300 + 32 * k), fast=True)
            _state['llm_model'] = model
        except Exception as e:  # noqa: BLE001
            _state.update(llm_error=f'{type(e).__name__}: {str(e)[:200]}', llm_error_at=time.time())
            logger.warning(f"감성 LLM 호출 실패 → 남은 문장은 사전 기반 폴백: {str(e)[:200]}")
            break
        parsed = _parse_llm_items(data, k)
        for j in range(k):
            if j + 1 in parsed:
                res[start + j] = parsed[j + 1]
    return res


# ═══════════════════════════════════════════════════════════════ 3) 사전 기반 (lexicon)
# 가중치: +1 매우 긍정 … -1 매우 부정. 공백은 선택적으로 매칭("흑자 전환" == "흑자전환").
_KO_LEX: Dict[str, float] = {
    # ── 긍정
    '급등': 1.0, '폭등': 1.0, '상한가': 1.0, '신고가': 0.9, '최고가': 0.7, '사상 최고': 0.8, '급반등': 0.9,
    '강세': 0.7, '상승': 0.6, '상승세': 0.6, '반등': 0.6, '오름세': 0.5, '올랐': 0.5, '오른다': 0.5, '오르': 0.4, '오를': 0.3, '돌파': 0.6,
    '호재': 0.9, '호실적': 0.9, '어닝 서프라이즈': 1.0, '깜짝 실적': 0.9, '실적 개선': 0.8, '최대 실적': 0.9,
    '사상 최대': 0.8, '역대 최대': 0.8, '흑자 전환': 1.0, '흑자': 0.6, '턴어라운드': 0.8, '호조': 0.7, '선방': 0.5,
    '목표가 상향': 0.9, '목표주가 상향': 0.9, '투자의견 상향': 0.9, '상향 조정': 0.7, '상향': 0.6,
    '매수 의견': 0.6, '순매수': 0.5, '매수세': 0.5, '수주': 0.6, '대규모 수주': 0.9, '공급 계약': 0.6,
    '계약 체결': 0.5, '수혜': 0.7, '기대감': 0.5, '성장': 0.5, '개선': 0.5, '회복': 0.5, '증가': 0.4, '확대': 0.3,
    '자사주 매입': 0.8, '자사주 소각': 0.9, '배당 확대': 0.7, '배당 증액': 0.7, '승인': 0.6, '허가 획득': 0.6,
    '저평가': 0.5, '긍정적': 0.6, '낙관': 0.5, '떡상': 0.9, '가즈아': 0.8, '줍줍': 0.4, '대박': 0.7,
    '적자 축소': 0.6, '적자폭 축소': 0.6, '적자 감소': 0.6, '손실 축소': 0.6, '낙폭 축소': 0.4, '하락폭 축소': 0.4,
    '우려 해소': 0.6, '리스크 해소': 0.6, '불확실성 해소': 0.6, '악재 해소': 0.6, '리스크 완화': 0.5,
    '규제 완화': 0.5, '승소': 0.7, '비용 감소': 0.4, '부채 감소': 0.4, '재고 감소': 0.3, '하락 제한': 0.2,
    '상승 전환': 0.7, '공매도 금지': 0.5,
    # ── 부정
    '급락': -1.0, '폭락': -1.0, '하한가': -1.0, '신저가': -0.9, '반토막': -0.9, '약세': -0.7, '하락': -0.6,
    '하락세': -0.6, '내림세': -0.5, '떨어': -0.5, '내렸': -0.4, '빠졌': -0.4, '이탈': -0.4,
    '악재': -0.9, '어닝 쇼크': -1.0, '실적 부진': -0.8, '부진': -0.6, '적자 전환': -1.0, '적자 확대': -0.9,
    '적자 증가': -0.8, '적자 지속': -0.7, '적자': -0.7, '손실 확대': -0.8, '손실 증가': -0.7, '손실': -0.6,
    '목표가 하향': -0.9, '목표주가 하향': -0.9, '투자의견 하향': -0.9, '하향 조정': -0.7, '하향': -0.6,
    '매도 의견': -0.6, '순매도': -0.5, '매도세': -0.5, '매도': -0.4, '공매도': -0.4, '투매': -0.8,
    '소송': -0.7, '피소': -0.8, '패소': -0.8, '제재': -0.7, '과징금': -0.8, '벌금': -0.7, '리콜': -0.8,
    '유상증자': -0.8, '전환사채': -0.5, '감자': -0.8, '상장폐지': -1.0, '거래정지': -0.9, '관리종목': -0.9,
    '횡령': -1.0, '배임': -1.0, '압수수색': -0.9, '검찰 수사': -0.8, '분식': -1.0, '감사의견 거절': -1.0,
    '감소': -0.4, '급감': -0.8, '둔화': -0.5, '우려': -0.5, '리스크': -0.4, '위기': -0.6, '쇼크': -0.7,
    '충격': -0.6, '불확실성': -0.4, '부정적': -0.6, '비관': -0.5, '경고': -0.5, '파업': -0.6, '지연': -0.4,
    '철회': -0.5, '취소': -0.5, '수주 취소': -0.8, '계약 해지': -0.8, '반등 실패': -0.6, '상승폭 축소': -0.3,
    '비용 증가': -0.4, '부채 증가': -0.4, '재고 증가': -0.4, '손절': -0.6, '물렸': -0.6, '떡락': -0.9,
    '폭망': -0.9, '망했': -0.8, '개미 털기': -0.5, '설거지': -0.7,
    '하락 전환': -0.7, '하락폭 확대': -0.8, '낙폭 확대': -0.7, '변동성 확대': -0.4, '성장 둔화': -0.6,
    '실적 둔화': -0.6, '수요 둔화': -0.6, '공매도 재개': -0.5,
}
_EN_LEX: Dict[str, float] = {
    r'surg(e|es|ed|ing)': 0.9, r'soar(s|ed|ing)?': 0.9, r'jump(s|ed)?': 0.6, r'rall(y|ies|ied)': 0.6,
    r'gain(s|ed)?': 0.5, r'ris(e|es|ing)|rose': 0.5, r'beat(s)?': 0.7, r'outperform(s|ed)?': 0.7,
    r'upgrad(e|es|ed)': 0.8, r'record (high|profit|revenue|earnings)': 0.8, r'strong(er)?': 0.5,
    r'good': 0.4, r'bad': -0.5, r'growth': 0.4, r'profit(s|able)?': 0.4, r'buyback(s)?': 0.7, r'bullish': 0.7, r'optimis(m|tic)': 0.5,
    r'rais(e|es|ed) (guidance|target|price target|forecast|dividend)': 0.8, r'approv(al|ed)': 0.6,
    r'(win|wins|won) (a )?(contract|deal|order)': 0.7, r'turnaround': 0.7, r'rebound(s|ed)?': 0.6,
    r'plung(e|es|ed)': -1.0, r'slump(s|ed)?': -0.8, r'tumbl(e|es|ed)': -0.8, r'crash(es|ed)?': -1.0,
    r'fall(s|ing)?|fell': -0.5, r'drop(s|ped)?': -0.5, r'declin(e|es|ed)': -0.5, r'miss(es|ed)?': -0.7,
    r'downgrad(e|es|ed)': -0.8, r'underperform(s|ed)?': -0.7, r'loss(es)?': -0.6, r'weak(er|ness)?': -0.5,
    r'lawsuit(s)?|sued': -0.7, r'probe|investigation': -0.6, r'fined': -0.7, r'recall(s|ed)?': -0.7,
    r'bearish': -0.7, r'pessimis(m|tic)': -0.5,
    r'cut(s)? (guidance|target|price target|forecast|outlook|dividend)': -0.8, r'layoff(s)?': -0.5,
    r'bankrupt(cy)?': -1.0, r'default(s|ed)?': -0.8, r'dilution': -0.6, r'(share|rights) offering': -0.7,
    r'delist(ing|ed)?': -1.0, r'fraud': -1.0, r'halt(s|ed)?': -0.6, r'warning(s)?': -0.5,
    r'concern(s)?': -0.4, r'risk(s)?': -0.3, r'sell-?off': -0.7, r'shortfall': -0.6,
}
_KO_INTENS = {'대폭': 1.5, '크게': 1.4, '매우': 1.3, '역대급': 1.4, '강력': 1.3, '급격히': 1.4, '초강세': 1.3,
              '소폭': 0.6, '다소': 0.7, '약간': 0.6, '살짝': 0.6, '일부': 0.8}
_EN_INTENS = {'sharply': 1.5, 'significantly': 1.4, 'strongly': 1.3, 'very': 1.3, 'massive': 1.4, 'huge': 1.3,
              'slightly': 0.6, 'modestly': 0.7, 'marginally': 0.6, 'somewhat': 0.7}
_EMOJI = {'🚀': 0.6, '📈': 0.5, '🔥': 0.3, '📉': -0.5, '😭': -0.4, 'ㅠㅠ': -0.3, 'ㅜㅜ': -0.3}
_EN_NEG = {'not', 'no', 'never', 'without', "n't", 'neither', 'nor', 'hardly', 'barely', 'fails', 'failed', 'fail'}

_KO_TERMS = sorted(_KO_LEX, key=len, reverse=True)
_KO_RE = re.compile('|'.join(r'\s*'.join(re.escape(p) for p in t.split(' ')) for t in _KO_TERMS))
_KO_NORM = {re.sub(r'\s+', '', t): w for t, w in _KO_LEX.items()}
_EN_PATTERNS = [(re.compile(r'\b(?:' + p + r')\b'), w) for p, w in _EN_LEX.items()]
_KO_NEG_AFTER = re.compile(r'^[^\s.,!?;·…]{0,4}\s?(?:지\s*)?(않|못|없)')
_KO_NEG_BEFORE = re.compile(r'(안|못)\s*$')
_KO_STOP = re.compile(r'[.,!?;·…\n]')
# 부정 명사 뒤의 감쇄 표현 ("영업손실 적자 축소", "하락 우려 해소") → 긍정 방향
_KO_REDUCER = re.compile(r'(축소|감소|해소|완화|줄|없)')


def _lexicon_one(text: str) -> Dict[str, Any]:
    try:
        return _lexicon_core(text)
    except Exception:  # noqa: BLE001 — 사전 엔진은 절대 예외를 내지 않음
        return _neutral('lexicon', 0.2)


def _lexicon_core(text: str) -> Dict[str, Any]:
    t = (text or '').lower()
    if not t.strip():
        return _neutral('lexicon')
    hits: List[float] = []
    # ── 한국어 (긴 표현 우선, 겹침 없음)
    matches = list(_KO_RE.finditer(t))
    for mi, m in enumerate(matches):
        w = _KO_NORM.get(re.sub(r'\s+', '', m.group(0)))
        if w is None:
            continue
        start, end = m.start(), m.end()
        nxt = matches[mi + 1].start() if mi + 1 < len(matches) else len(t)
        after = t[end:min(nxt, end + 7)]
        stop = _KO_STOP.search(after)
        if stop:
            after = after[:stop.start()]
        b0 = max(0, start - 6)
        before = t[b0:start]
        nb = _KO_NEG_BEFORE.search(before)
        # '안/못' 은 독립 어절일 때만 부정어 ("불안 하락" 의 '안' 은 제외)
        neg_before = bool(nb) and (b0 + nb.start() == 0 or t[b0 + nb.start() - 1].isspace())
        raw_after = t[end:end + 8]
        stop = _KO_STOP.search(raw_after)
        if stop:
            raw_after = raw_after[:stop.start()]
        if _KO_NEG_AFTER.search(after) or neg_before:
            w = -0.6 * w
        elif w < 0 and _KO_REDUCER.search(raw_after):
            w = -0.6 * w
        for iw, mult in _KO_INTENS.items():
            if iw in before:
                w *= mult
                break
        hits.append(w)
    # ── 영어 (최근 3단어 부정어)
    for rx, w0 in _EN_PATTERNS:
        for m in rx.finditer(t):
            prev = re.findall(r"[a-z']+", t[max(0, m.start() - 40):m.start()])[-3:]
            w = w0
            if any(p in _EN_NEG or p.endswith("n't") for p in prev):
                w = -0.6 * w
            for p in prev:
                if p in _EN_INTENS:
                    w *= _EN_INTENS[p]
                    break
            hits.append(w)
    for e, w in _EMOJI.items():
        if e in t:
            hits.append(w)
    if not hits:
        return _neutral('lexicon', 0.3)
    raw = sum(hits)
    if '?' in t:            # 질문형은 방향성 약화
        raw *= 0.8
    score = _clip(raw / math.sqrt(raw * raw + 2.0))
    label = _label_from_score(score)
    conf = min(0.85, 0.4 + 0.5 * abs(score)) if label != 'neutral' else 0.45
    return {'label': label, 'score': round(score, 4), 'confidence': round(conf, 3), 'engine': 'lexicon'}


# ═══════════════════════════════════════════════════════════════ 공개 API
def _engine_plan(mode: str) -> List[Tuple[str, bool]]:
    """(engine, wait) 후보 목록. lexicon 은 항상 마지막."""
    plan: List[Tuple[str, bool]] = []
    if mode == 'auto':
        plan = [('local', False), ('llm', False)]
    elif mode == 'local':
        plan = [('local', True), ('llm', False)]
    elif mode == 'llm':
        plan = [('llm', False)]
    return plan + [('lexicon', False)]


def _viable(engine: str, wait: bool) -> bool:
    if engine == 'lexicon':
        return True
    if engine == 'llm':
        return _llm_key_available() and not _llm_failed_recently()
    if engine == 'local':
        return _get_local(wait) is not None
    return False


def _run_engine(engine: str, texts: List[str]) -> List[Optional[Dict[str, Any]]]:
    if engine == 'local':
        pipe = _state['pipe']
        if pipe is None:
            return [None] * len(texts)
        try:
            return _local_predict(pipe, texts)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"로컬 감성 추론 실패 → 폴백: {str(e)[:200]}")
            return [None] * len(texts)
    if engine == 'llm':
        return _llm_predict(texts)
    return [_lexicon_one(t) for t in texts]


def analyze(texts: List[str], mode: Optional[str] = None) -> Tuple[List[Dict[str, Any]], str]:
    """문장 목록의 감성 판정. 절대 예외를 내지 않음 (최악의 경우 사전 기반)."""
    if texts is None:
        texts = []
    elif isinstance(texts, str):
        texts = [texts]
    else:
        texts = list(texts)
    try:
        return _analyze(texts, mode)
    except Exception as e:  # noqa: BLE001
        logger.error(f"감성 분석 오류 → 사전 기반 폴백: {e}")
        res = [_lexicon_one(_clean(t)) for t in texts]
        _state['engine_last'] = 'lexicon'
        return res, 'lexicon'


# auto 모드 앙상블 가중치: 범용 다국어 모델은 한국어 금융 헤드라인(예: "영업익 100조 넘나")을 대부분 '중립'으로
# 판정하는 경향이 실측으로 확인되어, 금융 문맥을 아는 Grok 판정을 주(70%)로, 로컬 모델을 보조(30%)로 섞습니다.
ENSEMBLE_W_LLM = float(os.environ.get('SENTIMENT_LLM_WEIGHT', '0.7') or 0.7)


def _ensemble(texts: List[str]) -> Optional[Tuple[List[Dict[str, Any]], str]]:
    """LLM 과 로컬 모델이 모두 가능하면 가중 평균. 하나라도 불가하면 None."""
    if not (_viable('llm', False) and _viable('local', False)):
        return None
    r_llm, e_llm = _analyze(texts, 'llm', exact=True)
    if e_llm != 'llm':          # LLM 이 실패해 사전으로 떨어졌으면 앙상블하지 않음
        return None
    r_loc, e_loc = _analyze(texts, 'local', exact=True)
    if e_loc != 'local':
        return None
    w = min(1.0, max(0.0, ENSEMBLE_W_LLM))
    out = []
    for a, b in zip(r_llm, r_loc):
        sa, sb = float(a.get('score') or 0.0), float(b.get('score') or 0.0)
        sc = _clip(w * sa + (1 - w) * sb)
        out.append({'label': _label_from_score(sc), 'score': round(sc, 3),
                    'confidence': round(max(float(a.get('confidence') or 0), float(b.get('confidence') or 0)), 3),
                    'engine': 'ensemble', 'components': {'llm': round(sa, 3), 'local': round(sb, 3)}})
    return out, 'ensemble'


def _analyze(texts: List[str], mode: Optional[str], exact: bool = False) -> Tuple[List[Dict[str, Any]], str]:
    """exact=True: 해당 엔진의 캐시만 재사용 (앙상블에서 다른 엔진 결과가 섞이지 않도록)"""
    m = _resolve_mode(mode)
    clean = [_clean(t) for t in texts]
    if m == 'off':
        _state['engine_last'] = 'off'
        return [_neutral('off') for _ in clean], 'off'
    if m in ('auto', 'ensemble'):
        ens = _ensemble(texts)
        if ens is not None:
            _state['engine_last'] = 'ensemble'
            return ens
        if m == 'ensemble':
            m = 'auto'

    viable = [(e, w) for e, w in _engine_plan(m) if _viable(e, w)]
    primary = viable[0][0]
    results: List[Optional[Dict[str, Any]]] = [None] * len(clean)
    hashes = [_key(t) if t else '' for t in clean]

    # 캐시: 현재 1순위 엔진 이상 품질의 결과만 재사용
    accept = ([primary] if exact else
              [e for e, r in sorted(ENGINE_RANK.items(), key=lambda x: x[1]) if r <= ENGINE_RANK[primary]])
    for i, t in enumerate(clean):
        if not t:
            continue
        for e in accept:
            hit = _cache_get(e, hashes[i])
            if hit:
                results[i] = hit
                break

    # 캐시 미스를 엔진 순서대로 처리 (같은 문장은 한 번만)
    for engine, _w in viable:
        pending: Dict[str, List[int]] = {}
        for i, t in enumerate(clean):
            if t and results[i] is None:
                pending.setdefault(hashes[i], []).append(i)
        if not pending:
            break
        order = list(pending.keys())
        uniq = [clean[pending[h][0]] for h in order]
        out = _run_engine(engine, uniq)
        for h, r in zip(order, out):
            if r is None:
                continue
            r = dict(r, engine=r.get('engine') or engine)
            _cache_put(r['engine'], h, r)
            for i in pending[h]:
                results[i] = dict(r)

    counts: Dict[str, int] = {}
    for r in results:
        if r is not None:
            counts[r['engine']] = counts.get(r['engine'], 0) + 1
    engine_used = (max(counts.items(), key=lambda kv: (kv[1], -ENGINE_RANK.get(kv[0], 9)))[0]
                   if counts else primary)
    final = [r if r is not None else (_neutral(engine_used) if not clean[i] else _lexicon_one(clean[i]))
             for i, r in enumerate(results)]
    _state['engine_last'] = engine_used
    return final, engine_used


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    pos = neg = neu = 0
    total = 0.0
    n = 0
    for r in results or []:
        if not isinstance(r, dict):
            continue
        lab = r.get('label')
        try:
            s = float(r.get('score') or 0.0)
            if not math.isfinite(s):
                s = 0.0
        except (TypeError, ValueError):
            s = 0.0
        if lab == 'positive':
            pos += 1
        elif lab == 'negative':
            neg += 1
        else:
            neu += 1
        total += _clip(s)
        n += 1
    return {'pos': pos, 'neg': neg, 'neu': neu, 'n': n, 'score': round(total / n, 3) if n else 0.0}


def status() -> Dict[str, Any]:
    return {
        'mode': _resolve_mode(None),
        'local_available': _local_importable(),
        'model': _model_name(),
        'loaded': _state['pipe'] is not None,
        'loading': bool(_state['loading']),
        'loaded_at': _state['loaded_at'],
        'load_sec': _state['load_sec'],
        'engine_last': _state['engine_last'],
        'load_error': _state['load_error'],
        'load_retry_in_sec': (max(0, int(LOAD_FAIL_TTL - (time.time() - _state['load_error_at'])))
                              if _local_failed_recently() else 0),
        'llm_available': _llm_key_available(),
        'llm_error': _state['llm_error'] if _llm_failed_recently() else None,
        'llm_model': _state['llm_model'],
        'cache_size': len(_cache),
    }
