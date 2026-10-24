"""
오늘의 테마 스케줄러 — 평일 아침 자동 추출 (데몬 스레드, 30초 주기)

실행 규칙 (tick 마다 decide() 로 판정)
  1. THEME_SCHEDULE 환경변수가 off/0/false/no 가 아닐 것 (기본 on)
  2. KST 기준 평일(월~금)이고 market_data.KRX_HOLIDAYS(date 키 dict)에 없는 날
  3. KST 현재 시각 ≥ THEME_SCHEDULE_TIME (기본 08:40, 'HH:MM')
  4. 오늘 파일(uploads/themes/YYYY-MM-DD.json)이 없거나, 예정 시각 이전에 만든 파일(새벽 수동 추출)일 때
  5. 진행 중인 테마 작업(수동 포함)이 없을 때
  → TaskManager 작업(task_type='themes')으로 theme_service.build_today 실행 → 프론트는 /api/stock/task/<id>(/stream) 로 추적
  실패 시 10분 간격으로 최대 2회 더 재시도(하루 최대 3회). 이후에는 다음 영업일 예정 시각까지 대기.
  시도 횟수·마지막 실행 정보는 uploads/themes/.scheduler_state.json 에 남겨 재시작 후에도 폭주 재시도를 막습니다.

프로세스 규칙 (Flask 디버그 리로더)
  debug 모드의 리로더는 '감시용 부모' + '실제 서버 자식' 두 프로세스를 띄우고, 자식에만 WERKZEUG_RUN_MAIN=true 가 설정됩니다.
  → debug 이면 WERKZEUG_RUN_MAIN == 'true' 인 프로세스에서만 시작, debug 가 아니면 항상 시작.
  → debug 이면서 리로더를 끈 실행(use_reloader=False)이라면 start(force=True) 로 호출하세요.
  start() 는 같은 프로세스에서 여러 번 불려도 스레드를 하나만 띄웁니다(idempotent).
"""

import os
import re
import json
import threading
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

from ..config import Config
from ..models.task import TaskManager, TaskStatus
from ..utils.logger import get_logger
from . import market_data as md
from . import theme_service as ts

logger = get_logger('mirofish.themes.scheduler')

KST = ts.KST
DEFAULT_TIME = (8, 40)
TICK_SEC = 30
MAX_ATTEMPTS = 3            # 최초 1회 + 재시도 2회
RETRY_GAP = timedelta(minutes=10)

_lock = threading.RLock()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_state: Dict[str, Any] = {
    'last_run': None, 'last_ok': None, 'last_error': None, 'last_trigger': None,
    'attempt_date': None, 'attempts': 0, 'next_retry_at': None, 'running_task_id': None,
}
_loaded = [False]


# ═══════════════════════════════════════════════════════════════ 설정
def enabled() -> bool:
    return (os.environ.get('THEME_SCHEDULE') or 'on').strip().lower() not in ('off', '0', 'false', 'no')


def sched_time() -> Tuple[int, int]:
    m = re.fullmatch(r'\s*(\d{1,2}):(\d{2})\s*', os.environ.get('THEME_SCHEDULE_TIME') or '')
    if m and 0 <= int(m.group(1)) < 24 and 0 <= int(m.group(2)) < 60:
        return int(m.group(1)), int(m.group(2))
    return DEFAULT_TIME


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in md.KRX_HOLIDAYS


def scheduled_at(d: date) -> datetime:
    h, m = sched_time()
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def _fmt(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(KST).strftime('%Y-%m-%d %H:%M:%S') if isinstance(dt, datetime) else dt


def _parse(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s), '%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════════ 상태 영속화
def _state_path() -> str:
    return os.path.join(ts._themes_dir(), '.scheduler_state.json')


def _load_state() -> None:
    if _loaded[0]:
        return
    _loaded[0] = True
    try:
        with open(_state_path(), encoding='utf-8') as f:
            saved = json.load(f)
        for k in ('last_run', 'last_ok', 'last_error', 'last_trigger', 'attempt_date', 'attempts', 'next_retry_at'):
            if k in saved:
                _state[k] = saved[k]
    except (OSError, ValueError):
        pass


def _save_state() -> None:
    try:
        data = {k: v for k, v in _state.items() if k != 'running_task_id'}
        tmp = _state_path() + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _state_path())
    except OSError as e:
        logger.warning(f'스케줄러 상태 저장 실패: {e}')


# ═══════════════════════════════════════════════════════════════ 판정 (순수 함수에 가깝게 — 테스트용 주입 가능)
def _running_task_id() -> Optional[str]:
    tid = _state.get('running_task_id')
    if not tid:
        return None
    t = TaskManager().get_task(tid)
    if t and t.status.value in (TaskStatus.PENDING.value, TaskStatus.PROCESSING.value):
        return tid
    return None


def decide(now: datetime, file_time: Callable[[str], Optional[datetime]] = None) -> str:
    """now(KST aware) 기준 행동: disabled | non_business | before_time | running | done | gave_up | waiting_retry | run"""
    _load_state()
    file_time = file_time or ts.generated_at
    if not enabled():
        return 'disabled'
    now = now.astimezone(KST)
    d = now.date()
    if not is_business_day(d):
        return 'non_business'
    due = scheduled_at(d)
    if now < due:
        return 'before_time'
    if _running_task_id():
        return 'running'
    ds = d.isoformat()
    g = file_time(ds)
    if g is not None and g >= due:
        return 'done'
    attempts = _state['attempts'] if _state.get('attempt_date') == ds else 0
    if attempts >= MAX_ATTEMPTS:
        return 'gave_up'
    retry_at = _parse(_state.get('next_retry_at')) if _state.get('attempt_date') == ds else None
    if retry_at and now < retry_at:
        return 'waiting_retry'
    return 'run'


def tick(now: datetime = None, runner: Callable[..., str] = None, file_time: Callable = None) -> str:
    """한 번 판정하고 필요하면 작업 시작. 반환: decide() 결과"""
    now = (now or ts.now_kst()).astimezone(KST)
    with _lock:
        action = decide(now, file_time)
        if action != 'run':
            return action
        ds = now.date().isoformat()
        if _state.get('attempt_date') != ds:
            _state['attempt_date'], _state['attempts'] = ds, 0
        _state['attempts'] += 1
        _state['next_retry_at'] = None
        _save_state()
        n = _state['attempts']
    logger.info(f'오늘의 테마 자동 추출 시작 ({ds} {n}/{MAX_ATTEMPTS}회차)')
    (runner or start_task)('schedule')
    return action


# ═══════════════════════════════════════════════════════════════ 작업 실행 (수동·자동 공용)
def _on_finish(trigger: str, ok: bool, err: Optional[str], now: datetime = None) -> None:
    now = now or ts.now_kst()
    with _lock:
        if ok:
            _state['last_ok'] = _fmt(now)
            _state['last_error'] = None
            _state['next_retry_at'] = None
        else:
            _state['last_error'] = f'{_fmt(now)} · {err or "알 수 없는 오류"}'
            if trigger == 'schedule':
                if (_state.get('attempts') or 0) < MAX_ATTEMPTS:
                    _state['next_retry_at'] = _fmt(now + RETRY_GAP)
                else:
                    _state['next_retry_at'] = None
        _save_state()


def start_task(trigger: str = 'manual', spawn: Callable[[Callable[[], None]], None] = None) -> str:
    """테마 추출 작업 시작 → task_id. 진행 중인 작업이 있으면 그 id 재사용."""
    tm = TaskManager()
    with _lock:
        _load_state()
        running = _running_task_id()
        if running:
            return running
        now = ts.now_kst()
        task_id = tm.create_task(task_type='themes', metadata={'trigger': trigger, 'date': now.strftime('%Y-%m-%d')})
        _state['running_task_id'] = task_id
        _state['last_run'] = _fmt(now)
        _state['last_trigger'] = trigger
        _save_state()

    def progress(pct, msg, detail=None):
        tm.update_task(task_id, status=TaskStatus.PROCESSING, progress=int(pct), message=msg,
                       progress_detail=detail or {})

    def run():
        ok, err = False, None
        try:
            progress(1, '오늘의 테마 추출을 시작합니다', {'stage': 'list'})
            from . import ops_metrics
            with ops_metrics.stage('theme', trigger):
                data = ts.build_today(progress)
            tm.complete_task(task_id, {'date': data.get('date'), 'count': len(data.get('themes') or []),
                                       'engine': data.get('engine')})
            ok = True
        except Exception as e:
            err = str(e)[:300]
            logger.error(f'오늘의 테마 추출 실패 ({trigger}): {err}')
            tm.fail_task(task_id, err)
        finally:
            _on_finish(trigger, ok, err)

    if spawn:
        spawn(run)
    else:
        threading.Thread(target=run, daemon=True, name='theme-build').start()
    return task_id


# ═══════════════════════════════════════════════════════════════ 스레드
def _loop(interval: float) -> None:
    logger.info(f'오늘의 테마 스케줄러 시작 (평일 {"%02d:%02d" % sched_time()} KST, {interval:.0f}초 주기)')
    while not _stop.is_set():
        try:
            tick()
        except Exception as e:
            logger.error(f'테마 스케줄러 tick 오류: {e}')
        _stop.wait(interval)


def should_start(debug: bool, run_main: Optional[str]) -> bool:
    """리로더 부모 프로세스에서는 시작하지 않음 (debug 이면 WERKZEUG_RUN_MAIN=='true' 인 자식에서만)"""
    return (not debug) or run_main == 'true'


def start(debug: Optional[bool] = None, force: bool = False, interval: float = TICK_SEC) -> bool:
    """create_app() 에서 1회 호출. 스레드를 띄웠거나 이미 떠 있으면 True."""
    global _thread
    if debug is None:
        debug = bool(Config.DEBUG)
    if not force and not should_start(debug, os.environ.get('WERKZEUG_RUN_MAIN')):
        logger.debug('리로더 부모 프로세스 — 테마 스케줄러를 시작하지 않습니다')
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        _stop.clear()
        _thread = threading.Thread(target=_loop, args=(interval,), daemon=True, name='theme-scheduler')
        _thread.start()
        return True


def stop() -> None:
    _stop.set()


# ═══════════════════════════════════════════════════════════════ 상태 (설정 화면 · 테마 패널)
def next_run(now: datetime = None, file_time: Callable = None) -> Optional[str]:
    now = (now or ts.now_kst()).astimezone(KST)
    if not enabled():
        return None
    action = decide(now, file_time)
    if action == 'run':
        return _fmt(now)
    if action == 'running':
        return None
    if action == 'waiting_retry':
        return _state.get('next_retry_at')
    if action == 'before_time':
        return _fmt(scheduled_at(now.date()))
    d = now.date() + timedelta(days=1)
    for _ in range(30):
        if is_business_day(d):
            return _fmt(scheduled_at(d))
        d += timedelta(days=1)
    return None


def status(now: datetime = None) -> Dict[str, Any]:
    now = (now or ts.now_kst()).astimezone(KST)
    with _lock:
        _load_state()
        running = _running_task_id()
        if not running:
            _state['running_task_id'] = None
        ds = now.date().isoformat()
        try:
            decision = decide(now)
            nxt = next_run(now)
        except Exception as e:  # 상태 조회는 절대 실패하지 않게
            decision, nxt = f'error: {str(e)[:80]}', None
        last_ok = _state.get('last_ok')
        if not last_ok:
            try:
                latest = ts.list_dates()
                g = ts.generated_at(latest[0]) if latest else None
                last_ok = _fmt(g) if g else None
            except Exception:
                last_ok = None
        return {
            'enabled': enabled(),
            'time': '%02d:%02d' % sched_time(),
            'last_run': _state.get('last_run'),
            'last_ok': last_ok,
            'last_error': _state.get('last_error'),
            'last_trigger': _state.get('last_trigger'),
            'next_run': nxt,
            'running_task_id': running,
            'attempts_today': _state['attempts'] if _state.get('attempt_date') == ds else 0,
            'max_attempts': MAX_ATTEMPTS,
            'decision': decision,
            'thread_alive': bool(_thread is not None and _thread.is_alive()),
            'rule': '평일(휴장일 제외) KST 예정 시각 이후, 오늘 파일이 없으면 자동 추출 · 실패 시 10분 간격 최대 2회 재시도',
        }
