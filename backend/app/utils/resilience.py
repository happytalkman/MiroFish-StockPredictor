"""
안정성 유틸리티
  - with_retry : 429·5xx·타임아웃·연결 오류만 지수 백오프로 재시도 (4xx 인증·요청 오류는 즉시 실패)
  - repair_json: LLM 이 낸 깨진 JSON 복구 (코드펜스·사고과정 제거, 후행 쉼표, 문자열 내 개행, 괄호 균형, 잘린 출력)
"""

import json
import random
import re
import time
import functools
from typing import Any, Callable, Optional

from .logger import get_logger

logger = get_logger('mirofish.resilience')

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}


def _status_of(exc: Exception) -> Optional[int]:
    for attr in ('status_code', 'status', 'http_status'):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, 'response', None)
    if resp is not None:
        v = getattr(resp, 'status_code', None)
        if isinstance(v, int):
            return v
    m = re.search(r'\b(4\d\d|5\d\d)\b', str(exc)[:200])
    return int(m.group(1)) if m else None


def is_retryable(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    if any(k in name for k in ('timeout', 'connection', 'remotedisconnected', 'chunkedencoding', 'apiconnection')):
        return True
    st = _status_of(exc)
    return st in RETRY_STATUS


def retry_call(fn: Callable, *args, tries: int = 4, base: float = 1.5, cap: float = 30.0, label: str = '', **kwargs):
    last = None
    for attempt in range(1, tries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt >= tries or not is_retryable(e):
                raise
            delay = min(cap, base * (2 ** (attempt - 1))) * (0.8 + random.random() * 0.4)
            logger.warning(f"[재시도 {attempt}/{tries - 1}] {label or getattr(fn, '__name__', 'call')} — {str(e)[:120]} → {delay:.1f}초 후")
            time.sleep(delay)
    raise last  # pragma: no cover


def with_retry(tries: int = 4, base: float = 1.5, cap: float = 30.0):
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **k):
            return retry_call(fn, *a, tries=tries, base=base, cap=cap, label=fn.__name__, **k)
        return wrap
    return deco


# ─────────────────────────────────────────────────────────── JSON 복구
def _strip_wrappers(text: str) -> str:
    t = (text or '').strip()
    t = re.sub(r'<think>[\s\S]*?</think>', '', t).strip()
    t = re.sub(r'^```(?:json|JSON)?\s*', '', t)
    t = re.sub(r'\s*```\s*$', '', t)
    # 앞쪽 설명문 제거: 첫 { 또는 [ 부터
    starts = [i for i in (t.find('{'), t.find('[')) if i >= 0]
    if starts:
        t = t[min(starts):]
    return t.strip()


def _escape_controls_in_strings(t: str) -> str:
    out, in_str, esc = [], False, False
    for ch in t:
        if in_str:
            if esc:
                esc = False
                out.append(ch)
                continue
            if ch == '\\':
                esc = True
                out.append(ch)
                continue
            if ch == '"':
                in_str = False
                out.append(ch)
                continue
            if ch == '\n':
                out.append('\\n')
                continue
            if ch == '\r':
                continue
            if ch == '\t':
                out.append('\\t')
                continue
            out.append(ch)
        else:
            if ch == '"':
                in_str = True
            out.append(ch)
    return ''.join(out)


def _balance(t: str) -> str:
    stack, in_str, esc = [], False, False
    last_safe = 0
    for i, ch in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in '{[':
            stack.append('}' if ch == '{' else ']')
        elif ch in '}]':
            if stack:
                stack.pop()
            if not stack:
                last_safe = i + 1
        elif ch == ',' and len(stack) >= 1:
            last_safe = i
    if not stack and not in_str:
        return t
    # 잘린 출력: 문자열이 열려 있으면 닫고, 마지막 쉼표 이후 불완전 요소 제거 후 괄호 닫기
    body = t
    if in_str:
        body += '"'
    body = re.sub(r',\s*"[^"]*"\s*:\s*$', '', body)
    body = re.sub(r',\s*$', '', body.rstrip())
    body = re.sub(r':\s*$', ': null', body)
    # 스택 재계산
    stack, in_str, esc = [], False, False
    for ch in body:
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in '{[':
            stack.append('}' if ch == '{' else ']')
        elif ch in '}]' and stack:
            stack.pop()
    return body + ''.join(reversed(stack))


def repair_json(text: str) -> Any:
    """깨진 JSON 문자열을 최대한 복구해 파싱. 실패 시 ValueError."""
    t = _strip_wrappers(text)
    for attempt in range(4):
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            pass
        if attempt == 0:
            t = t.replace('“', '"').replace('”', '"').replace('‘', "'").replace('’', "'")
            t = _escape_controls_in_strings(t)
        elif attempt == 1:
            t = re.sub(r',\s*([}\]])', r'\1', t)           # 후행 쉼표
            t = re.sub(r'([}\]"\d])\s*\n\s*(")', r'\1,\n\2', t)  # 누락된 쉼표(줄바꿈 사이)
        elif attempt == 2:
            t = _balance(t)
            t = re.sub(r',\s*([}\]])', r'\1', t)
    # 마지막 수단: 가장 큰 {...} 블록
    m = re.search(r'\{[\s\S]*\}', t)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"JSON 복구 실패: {t[:200]}")
