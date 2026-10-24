"""
운영 지표 — LLM 호출량·토큰·추정 비용, 단계별 소요 시간

수집 방법
  - LLM: OpenAI 호환 SDK 의 chat.completions.create 를 감싸 모든 호출(보고서·지식그래프·위원회·판단·리서치…)을 기록
         응답의 usage(입력·출력·추론 토큰)를 쓰고, 스트리밍에서 usage 가 오지 않으면 글자 수로 추정(est=1)
  - 단계: with stage('report', report_id): … 블록의 소요 시간·성공 여부 기록.
          작업 스레드풀(ThreadPoolExecutor)에 넘긴 일도 같은 단계로 묶이도록 contextvars 를 전달
저장: uploads/ops/metrics.db (SQLite) — llm_calls · stage_runs, 90일 지난 기록은 정리
비용: 모델 이름으로 1M 토큰당 단가(USD)를 찾아 계산 — 설정 LLM_PRICES(JSON)로 덮어쓰기, 환율 USD_KRW
      기본 단가는 공개 가격표 기준 추정치이며 실제 청구액과 다를 수 있음
"""

import contextlib
import contextvars
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.ops')
KST = timezone(timedelta(hours=9))
_lock = threading.RLock()
_stage: contextvars.ContextVar = contextvars.ContextVar('weaidstock_stage', default=None)
_installed = {'llm': False, 'pool': False}
RETENTION_DAYS = 90
STAGE_KO = {'analysis': '1 데이터·리서치', 'research': '1 리서치', 'simulation': '2 시뮬레이션', 'report': '3 보고서',
            'kg': '4 지식그래프', 'insight': '4 인사이트', 'committee': '5 투자위원회', 'decision': '5 판단',
            'theme': '오늘의 테마', 'watchlist': '관심종목 일괄', 'other': '기타'}

# 1M 토큰당 USD (입력, 출력) — 이름에 포함된 문자열로 찾고, 가장 긴 키가 우선. 추론 토큰은 출력 단가.
DEFAULT_PRICES = {
    'grok-4.1-fast': (0.20, 0.50), 'grok-4-1-fast': (0.20, 0.50), 'grok-4-fast': (0.20, 0.50), 'fast': (0.20, 0.50),
    'grok-3-mini': (0.30, 0.50), 'mini': (0.30, 0.50),
    'grok-4.2': (1.25, 2.50), 'grok-4.3': (1.25, 2.50),
    'grok-4.5': (2.00, 6.00), 'grok-4.6': (2.00, 6.00), 'grok-4.7': (2.00, 6.00),
    'grok-4': (3.00, 15.00), 'grok': (2.00, 6.00),
}


def _db() -> sqlite3.Connection:
    d = os.path.join(Config.UPLOAD_FOLDER, 'ops')
    os.makedirs(d, exist_ok=True)
    c = sqlite3.connect(os.path.join(d, 'metrics.db'), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS llm_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, stage TEXT, model TEXT, host TEXT, stream INTEGER,
        prompt_tokens INTEGER, completion_tokens INTEGER, reasoning_tokens INTEGER, est INTEGER,
        sec REAL, ok INTEGER, error TEXT, cost_usd REAL)''')
    c.execute('CREATE INDEX IF NOT EXISTS ix_llm_ts ON llm_calls(ts)')
    c.execute('''CREATE TABLE IF NOT EXISTS stage_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, stage TEXT, key TEXT, sec REAL, ok INTEGER, error TEXT)''')
    c.execute('CREATE INDEX IF NOT EXISTS ix_stage_ts ON stage_runs(ts)')
    return c


def _write(sql: str, args: tuple) -> None:
    try:
        with _lock:
            c = _db()
            try:
                c.execute(sql, args)
                c.commit()
            finally:
                c.close()
    except Exception as e:          # 지표 기록 실패가 본 작업을 막지 않게
        logger.debug(f"지표 기록 실패: {e}")


# ─────────────────────────────────────────────────────────── 단가
def prices() -> Dict[str, tuple]:
    out = dict(DEFAULT_PRICES)
    raw = (os.environ.get('LLM_PRICES') or '').strip()
    if raw:
        try:
            for k, v in json.loads(raw).items():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    out[str(k).lower()] = (float(v[0]), float(v[1]))
        except Exception as e:
            logger.warning(f"LLM_PRICES 형식 오류 (무시): {e}")
    return out


def price_for(model: str) -> Optional[tuple]:
    m = (model or '').lower().split('/')[-1]
    best = None
    for k, v in prices().items():
        if k in m and (best is None or len(k) > len(best[0])):
            best = (k, v)
    return best[1] if best else None


def cost_usd(model: str, pin: int, pout: int) -> Optional[float]:
    p = price_for(model)
    if not p:
        return None
    return round((pin or 0) / 1e6 * p[0] + (pout or 0) / 1e6 * p[1], 6)


def usd_krw() -> float:
    try:
        return float(os.environ.get('USD_KRW') or 1400)
    except ValueError:
        return 1400.0


# ─────────────────────────────────────────────────────────── 단계
def current_stage() -> Optional[str]:
    return _stage.get()


@contextlib.contextmanager
def stage(name: str, key: str = ''):
    """with stage('report', report_id): … — 이 블록 안(스레드풀 포함)의 LLM 호출을 이 단계로 묶고 소요 시간을 기록"""
    tok = _stage.set(name)
    t0 = time.time()
    err = None
    try:
        yield
    except BaseException as e:
        err = f'{type(e).__name__}: {str(e)[:160]}'
        raise
    finally:
        _stage.reset(tok)
        _write('INSERT INTO stage_runs(ts, stage, key, sec, ok, error) VALUES (?,?,?,?,?,?)',
               (t0, name, (key or '')[:80], round(time.time() - t0, 2), 0 if err else 1, err))


def staged(name: str, key: str, fn, task_id: Optional[str] = None):
    """스레드 대상 함수를 단계로 감쌈 — 함수가 오류를 삼키고 작업만 실패 처리해도 task 상태로 실패를 기록"""
    def run(*a, **kw):
        t0 = time.time()
        tok = _stage.set(name)
        err = None
        try:
            return fn(*a, **kw)
        except BaseException as e:
            err = f'{type(e).__name__}: {str(e)[:160]}'
            raise
        finally:
            _stage.reset(tok)
            if err is None and task_id:
                try:
                    from ..models.task import TaskManager
                    t = TaskManager().get_task(task_id)
                    if t is not None and getattr(t.status, 'value', t.status) == 'failed':
                        err = str(getattr(t, 'error', '') or '작업 실패')[:200]
                except Exception:
                    pass
            record_stage(name, key, time.time() - t0, err is None, err, ts=t0)
    return run


def record_stage(name: str, key: str, sec: float, ok: bool = True, error: Optional[str] = None, ts: Optional[float] = None) -> None:
    _write('INSERT INTO stage_runs(ts, stage, key, sec, ok, error) VALUES (?,?,?,?,?,?)',
           (ts or time.time() - sec, name, (key or '')[:80], round(sec, 2), 1 if ok else 0, (error or '')[:200] or None))


# ─────────────────────────────────────────────────────────── LLM 호출 기록
def _host(client) -> str:
    try:
        u = str(getattr(client, 'base_url', '') or '')
        return re.sub(r'^https?://', '', u).split('/')[0][:60]
    except Exception:
        return ''


def record_llm(model: str, host: str, sec: float, ok: bool, stream: bool = False, pin: Optional[int] = None,
               pout: Optional[int] = None, reasoning: Optional[int] = None, est: bool = False,
               error: Optional[str] = None, stage_name: Optional[str] = None, reasoning_separate: bool = True) -> None:
    """reasoning_separate: 추론 토큰이 completion_tokens 에 포함되지 않은 경우(xAI 형식: total = 입력+출력+추론) True"""
    pin, pout, reasoning = int(pin or 0), int(pout or 0), int(reasoning or 0)
    billed_out = pout + (reasoning if reasoning_separate else 0)      # 추론 토큰도 출력 단가로 청구됨
    cost = cost_usd(model, pin, billed_out) if (ok or pin) else None
    _write('''INSERT INTO llm_calls(ts, stage, model, host, stream, prompt_tokens, completion_tokens, reasoning_tokens, est,
              sec, ok, error, cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
           (time.time() - sec, stage_name or current_stage() or 'other', (model or '?')[:80], host, 1 if stream else 0,
            pin, pout, reasoning, 1 if est else 0, round(sec, 2), 1 if ok else 0, (error or '')[:200] or None, cost))


def _usage_of(obj) -> Optional[Dict[str, int]]:
    u = getattr(obj, 'usage', None)
    if u is None:
        return None
    get = (lambda k: u.get(k)) if isinstance(u, dict) else (lambda k: getattr(u, k, None))
    det = get('completion_tokens_details')
    r = (det.get('reasoning_tokens') if isinstance(det, dict) else getattr(det, 'reasoning_tokens', None)) if det else None
    pin, pout, r = get('prompt_tokens') or 0, get('completion_tokens') or 0, r or 0
    total = get('total_tokens') or 0
    # OpenAI 형식은 completion 에 추론이 포함, xAI 형식은 별도(total = 입력+출력+추론)
    sep = bool(r) and total >= pin + pout + r - 2
    return {'in': pin, 'out': pout, 'reason': r, 'sep': sep}


def _est_tokens(chars: int) -> int:
    return int(chars / 2.2)      # 한국어·영문 혼합 대략치 (글자 2.2개 ≈ 1토큰)


def _prompt_chars(kwargs) -> int:
    try:
        return sum(len(str(m.get('content') or '')) for m in kwargs.get('messages') or [])
    except Exception:
        return 0


class _StreamProxy:
    """스트리밍 응답을 그대로 흘려보내면서 끝(또는 close) 시점에 사용량을 한 번 기록"""

    def __init__(self, inner, model, host, t0, prompt_chars, stage_name):
        self._inner, self._model, self._host, self._t0 = inner, model, host, t0
        self._pchars, self._stage = prompt_chars, stage_name
        self._chars, self._rchars, self._usage, self._done = 0, 0, None, False

    def _finish(self, ok=True, error=None):
        if self._done:
            return
        self._done = True
        sec = time.time() - self._t0
        if self._usage:
            record_llm(self._model, self._host, sec, ok, True, self._usage['in'], self._usage['out'], self._usage['reason'],
                       False, error, self._stage, self._usage['sep'])
        else:
            record_llm(self._model, self._host, sec, ok, True, _est_tokens(self._pchars), _est_tokens(self._chars),
                       _est_tokens(self._rchars), True, error, self._stage)

    def __iter__(self):
        try:
            for chunk in self._inner:
                u = _usage_of(chunk)
                if u and (u['in'] or u['out']):
                    self._usage = u
                for ch in getattr(chunk, 'choices', None) or []:
                    d = getattr(ch, 'delta', None)
                    if d is not None:
                        self._chars += len(getattr(d, 'content', None) or '')
                        self._rchars += len(getattr(d, 'reasoning_content', None) or '')
                yield chunk
        except BaseException as e:
            self._finish(False, f'{type(e).__name__}: {str(e)[:120]}')
            raise
        self._finish(True)

    def close(self):
        try:
            c = getattr(self._inner, 'close', None)
            if c:
                c()
        finally:
            self._finish(True)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def __getattr__(self, k):
        return getattr(self._inner, k)


def _wrap_create(orig):
    def create(self, *args, **kwargs):
        t0 = time.time()
        model = kwargs.get('model') or '?'
        host = _host(getattr(self, '_client', None))
        st = current_stage()
        try:
            res = orig(self, *args, **kwargs)
        except BaseException as e:
            record_llm(model, host, time.time() - t0, False, bool(kwargs.get('stream')), error=f'{type(e).__name__}: {str(e)[:120]}',
                       stage_name=st)
            raise
        if kwargs.get('stream'):
            return _StreamProxy(res, model, host, t0, _prompt_chars(kwargs), st)
        try:
            u = _usage_of(res)
            if u:
                record_llm(model, host, time.time() - t0, True, False, u['in'], u['out'], u['reason'], stage_name=st,
                           reasoning_separate=u['sep'])
            else:
                txt = ''.join(str(getattr(getattr(c, 'message', None), 'content', '') or '') for c in getattr(res, 'choices', None) or [])
                record_llm(model, host, time.time() - t0, True, False, _est_tokens(_prompt_chars(kwargs)), _est_tokens(len(txt)),
                           est=True, stage_name=st)
        except Exception:
            pass
        return res
    create._weaidstock_wrapped = True
    return create


def install() -> Dict[str, bool]:
    """앱 시작 시 1회 — LLM 호출 계측 + 스레드풀로 단계 정보 전달"""
    if (os.environ.get('OPS_METRICS') or 'on').strip().lower() in ('off', '0', 'false', 'no'):
        return dict(_installed)
    if not _installed['llm']:
        try:
            from openai.resources.chat.completions import Completions
            if not getattr(Completions.create, '_weaidstock_wrapped', False):
                Completions.create = _wrap_create(Completions.create)
            _installed['llm'] = True
        except Exception as e:
            logger.warning(f"LLM 호출 계측 설치 실패 (지표 없이 동작): {e}")
    if not _installed['pool']:
        orig_submit = ThreadPoolExecutor.submit
        if not getattr(orig_submit, '_weaidstock_ctx', False):
            def submit(self, fn, /, *args, **kwargs):
                ctx = contextvars.copy_context()
                return orig_submit(self, ctx.run, fn, *args, **kwargs)
            submit._weaidstock_ctx = True
            ThreadPoolExecutor.submit = submit
        _installed['pool'] = True
    return dict(_installed)


# ─────────────────────────────────────────────────────────── 요약
def _pctl(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    i = (len(xs) - 1) * p
    lo, hi = int(i), min(len(xs) - 1, int(i) + 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (i - lo), 1)


def summary(days: int = 7) -> Dict[str, Any]:
    days = max(1, min(90, int(days or 7)))
    since = time.time() - days * 86400
    with _lock:
        c = _db()
        try:
            calls = [dict(r) for r in c.execute('SELECT * FROM llm_calls WHERE ts >= ? ORDER BY ts', (since,))]
            runs = [dict(r) for r in c.execute('SELECT * FROM stage_runs WHERE ts >= ? ORDER BY ts', (since,))]
            c.execute('DELETE FROM llm_calls WHERE ts < ?', (time.time() - RETENTION_DAYS * 86400,))
            c.execute('DELETE FROM stage_runs WHERE ts < ?', (time.time() - RETENTION_DAYS * 86400,))
            c.commit()
        finally:
            c.close()
    fx = usd_krw()

    def agg(rows):
        n = len(rows)
        ok = sum(1 for r in rows if r['ok'])
        cost = sum(r['cost_usd'] or 0 for r in rows)
        return {'calls': n, 'errors': n - ok, 'error_rate': round((n - ok) / n * 100, 1) if n else None,
                'prompt_tokens': sum(r['prompt_tokens'] or 0 for r in rows),
                'completion_tokens': sum(r['completion_tokens'] or 0 for r in rows),
                'reasoning_tokens': sum(r['reasoning_tokens'] or 0 for r in rows),
                'estimated_share': round(sum(1 for r in rows if r['est']) / n * 100) if n else 0,
                'cost_usd': round(cost, 4), 'cost_krw': round(cost * fx),
                'sec_p50': _pctl([r['sec'] for r in rows if r['ok']], .5), 'sec_p90': _pctl([r['sec'] for r in rows if r['ok']], .9),
                'unpriced': sum(1 for r in rows if r['cost_usd'] is None and r['ok'])}

    by_model, by_stage = {}, {}
    for r in calls:
        by_model.setdefault(r['model'], []).append(r)
        by_stage.setdefault(r['stage'] or 'other', []).append(r)
    stages: Dict[str, List[Dict[str, Any]]] = {}
    for r in runs:
        stages.setdefault(r['stage'], []).append(r)
    daily: Dict[str, Dict[str, Any]] = {}
    for i in range(min(days, 30)):
        d = (datetime.now(KST) - timedelta(days=i)).strftime('%Y-%m-%d')
        daily[d] = {'date': d, 'calls': 0, 'tokens': 0, 'cost_usd': 0.0}
    for r in calls:
        d = datetime.fromtimestamp(r['ts'], KST).strftime('%Y-%m-%d')
        if d in daily:
            daily[d]['calls'] += 1
            daily[d]['tokens'] += (r['prompt_tokens'] or 0) + (r['completion_tokens'] or 0) + (r['reasoning_tokens'] or 0)
            daily[d]['cost_usd'] = round(daily[d]['cost_usd'] + (r['cost_usd'] or 0), 4)
    fails = [{'at': datetime.fromtimestamp(r['ts'], KST).isoformat(timespec='seconds'), 'stage': r['stage'], 'model': r['model'],
              'error': r['error'], 'sec': r['sec']} for r in calls if not r['ok']][-10:][::-1]
    stage_fails = [{'at': datetime.fromtimestamp(r['ts'], KST).isoformat(timespec='seconds'), 'stage': r['stage'], 'key': r['key'],
                    'error': r['error'], 'sec': r['sec']} for r in runs if not r['ok']][-10:][::-1]
    return {
        'days': days, 'usd_krw': fx, 'installed': dict(_installed),
        'total': agg(calls),
        'by_model': sorted([dict(agg(v), model=k, price=price_for(k)) for k, v in by_model.items()], key=lambda x: -x['cost_usd']),
        'by_stage': sorted([dict(agg(v), stage=k, label=STAGE_KO.get(k, k)) for k, v in by_stage.items()], key=lambda x: -x['calls']),
        'stages': sorted([{'stage': k, 'label': STAGE_KO.get(k, k), 'runs': len(v), 'failed': sum(1 for r in v if not r['ok']),
                           'sec_p50': _pctl([r['sec'] for r in v if r['ok']], .5), 'sec_p90': _pctl([r['sec'] for r in v if r['ok']], .9),
                           'sec_max': max((r['sec'] for r in v if r['ok']), default=None)} for k, v in stages.items()],
                         key=lambda x: list(STAGE_KO).index(x['stage']) if x['stage'] in STAGE_KO else 99),
        'daily': sorted(daily.values(), key=lambda x: x['date']),
        'recent_llm_errors': fails, 'recent_stage_failures': stage_fails,
    }
