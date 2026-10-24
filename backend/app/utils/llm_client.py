"""
LLM 클라이언트 래퍼
OpenAI 형식으로 통일하여 호출
"""

import json
import os
import re
import time
import random
import logging
import threading
from typing import Optional, Dict, Any, List

import httpx
from openai import OpenAI

from ..config import Config
from .resilience import retry_call, repair_json, is_retryable

logger = logging.getLogger('mirofish.llm')

# ── 시간 한도 ────────────────────────────────────────────────────────────────
# 스트리밍: 조각(chunk) 사이가 STREAM_GAP_SEC 이상 끊기면 중단, 전체는 호출별 timeout(초) 안에서 끝냄
# 비스트리밍: 응답 전체를 timeout 안에 받지 못하면 중단 (재시도도 같은 예산 안에서만)
STREAM_GAP_SEC = float(os.environ.get('LLM_STREAM_GAP_SEC', '45') or 45)
DEFAULT_CALL_TIMEOUT = float(os.environ.get('LLM_CALL_TIMEOUT', '240') or 240)


class LLMTimeout(TimeoutError):
    """호출별 시간 예산 초과 — 같은 모델로 다시 부르지 말고 더 빠른 경로로 넘어가라는 신호"""


def is_timeout_error(e: BaseException) -> bool:
    if isinstance(e, (LLMTimeout, TimeoutError)):
        return True
    n = type(e).__name__.lower()
    return 'timeout' in n or 'timed out' in str(e).lower()


def _httpx_timeout(read: float) -> httpx.Timeout:
    return httpx.Timeout(connect=10.0, read=max(5.0, read), write=30.0, pool=10.0)


# ── 스트리밍 진행 표시 ────────────────────────────────────────────────────────
# 현재 스레드에 진행 콜백(sink)을 걸어 두면 chat() 이 스트리밍으로 받아 수신 글자 수를 알려 줍니다.
_tls = threading.local()


def set_stream_sink(fn):
    """fn(content_chars:int, reasoning_chars:int, elapsed_sec:float) — None 이면 해제"""
    _tls.sink = fn


def get_stream_sink():
    return getattr(_tls, 'sink', None)


_NO_STREAM_USAGE: set = set()


def stream_completion(client, kwargs: Dict[str, Any], on_tick=None, tick_sec: float = 1.0,
                      deadline_sec: Optional[float] = None, gap_sec: Optional[float] = None) -> str:
    """OpenAI 호환 스트리밍 호출. 본문 텍스트를 반환하고 진행 중 on_tick 으로 수신량을 알립니다.
    deadline_sec: 전체 시간 한도(초) — 넘으면 스트림을 닫고 LLMTimeout
    gap_sec: 조각 사이 최대 대기(초) — 서버가 멈춰 있으면 이 시간 뒤 중단"""
    t0 = time.time()
    parts, n_content, n_reason, last = [], 0, 0, 0.0
    extra = {'timeout': _httpx_timeout(gap_sec or STREAM_GAP_SEC)}   # 조각 사이 대기 한도 (멈춘 스트림 방지)
    host = str(getattr(client, 'base_url', '') or '')
    # 스트림 끝에 실제 토큰 사용량을 받도록 요청 (운영 지표·비용) — 거절하는 공급자는 기억해 두고 다시 붙이지 않음
    want_usage = 'stream_options' not in kwargs and host not in _NO_STREAM_USAGE
    if want_usage:
        extra['stream_options'] = {'include_usage': True}
    try:
        stream = client.chat.completions.create(stream=True, **kwargs, **extra)
    except Exception as e:
        if not want_usage or is_timeout_error(e) or 'stream_options' not in str(e) and '400' not in str(e):
            raise
        _NO_STREAM_USAGE.add(host)
        extra.pop('stream_options', None)
        stream = client.chat.completions.create(stream=True, **kwargs, **extra)
    try:
        for chunk in stream:
            now = time.time()
            if deadline_sec and now - t0 > deadline_sec:
                raise LLMTimeout(f'스트리밍 {deadline_sec:.0f}초 한도 초과 (본문 {n_content:,}자 · 추론 {n_reason:,}자)')
            choices = getattr(chunk, 'choices', None)
            if not choices:
                continue
            delta = choices[0].delta
            c = getattr(delta, 'content', None)
            if c:
                parts.append(c)
                n_content += len(c)
            r = getattr(delta, 'reasoning_content', None)
            if r:
                n_reason += len(r)
            if on_tick and now - last >= tick_sec:
                last = now
                try:
                    on_tick(n_content, n_reason, now - t0)
                except Exception:
                    pass
    except LLMTimeout:
        raise
    except Exception as e:
        if is_timeout_error(e):
            raise LLMTimeout(f'스트리밍 응답이 {gap_sec or STREAM_GAP_SEC:.0f}초 넘게 멈춤 ({type(e).__name__})') from e
        raise
    finally:
        close = getattr(stream, 'close', None)
        if close:
            try:
                close()
            except Exception:
                pass
    if on_tick:
        try:
            on_tick(n_content, n_reason, time.time() - t0)
        except Exception:
            pass
    return ''.join(parts)


class LLMClient:
    """LLM 클라이언트"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model = model or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY가 설정되지 않았습니다")
        
        # SDK 기본값(10분·자동 재시도 2회)이면 멈춘 요청 하나가 수십 분을 잡아먹으므로 재시도는 직접 관리
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=_httpx_timeout(DEFAULT_CALL_TIMEOUT),
            max_retries=0,
        )
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """
        채팅 요청 전송
        
        Args:
            messages: 메시지 목록
            temperature: 온도 파라미터
            max_tokens: 최대 토큰 수
            response_format: 응답 형식 (예: JSON 모드)
            timeout: 이 호출 전체의 시간 예산(초). 넘으면 LLMTimeout (기본 LLM_CALL_TIMEOUT=240)
            
        Returns:
            모델 응답 텍스트
        """
        budget = float(timeout or DEFAULT_CALL_TIMEOUT)
        t_end = time.time() + budget
        # 추론형 모델(grok-4.x 등)은 내부 추론에 토큰을 쓰므로 출력 한도를 넉넉히 확보
        if 'x.ai' in (self.base_url or '') or str(self.model).startswith('grok'):
            max_tokens = max(max_tokens, 4096)
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if response_format:
            kwargs["response_format"] = response_format
        
        sink = get_stream_sink()
        content = None
        if sink:
            try:
                content = stream_completion(self.client, kwargs, sink, deadline_sec=budget)
            except LLMTimeout:
                raise                  # 멈춘 모델을 같은 조건으로 다시 부르지 않음 — 호출한 쪽이 빠른 경로로 대체
            except Exception as e:
                if not is_retryable(e) and 'stream' not in str(e).lower():
                    raise
                content = None  # 스트리밍 미지원·연결 오류 → 남은 예산 안에서 일반 호출
        if content is None:
            # 429·5xx·연결 오류만 재시도 — 모두 남은 시간 예산 안에서 (인증·요청 오류는 즉시 실패)
            attempt = 0
            while True:
                attempt += 1
                remaining = t_end - time.time()
                if remaining < 5:
                    raise LLMTimeout(f'LLM {self.model} 시간 예산 {budget:.0f}초 소진')
                try:
                    response = self.client.chat.completions.create(timeout=_httpx_timeout(remaining), **kwargs)
                    break
                except Exception as e:
                    if is_timeout_error(e):
                        raise LLMTimeout(f'LLM {self.model} 응답 없음 ({remaining:.0f}초)') from e
                    if attempt >= 3 or not is_retryable(e) or t_end - time.time() < 10:
                        raise
                    delay = min(8.0, 1.5 * (2 ** (attempt - 1))) * (0.8 + random.random() * 0.4)
                    logger.warning(f"[재시도 {attempt}/2] LLM {self.model} — {str(e)[:120]} → {delay:.1f}초 후")
                    time.sleep(delay)
            content = response.choices[0].message.content or ''
        # 일부 모델(예: MiniMax M2.5)은 content에 <think> 사고 내용을 포함하므로 제거 필요
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content
    
    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        채팅 요청을 전송하고 JSON 반환
        
        Args:
            messages: 메시지 목록
            temperature: 온도 파라미터
            max_tokens: 최대 토큰 수
            
        Returns:
            파싱된 JSON 객체
        """
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )
        # 코드펜스·사고과정·후행 쉼표·잘린 출력까지 복구해 파싱
        try:
            return repair_json(response)
        except ValueError:
            raise ValueError(f"LLM이 반환한 JSON 형식이 올바르지 않습니다: {response[:500]}")
