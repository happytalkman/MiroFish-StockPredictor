"""
관심종목 — 목록 저장, 평일 아침 일괄 5단계 분석, 실행 기록

일괄 실행은 이 백엔드의 공개 API(분석 → 시뮬레이션 → 보고서 → 지식그래프 → 판단)를 차례로 호출합니다.
화면에서 누르는 것과 같은 경로라 결과가 보관함·예측 원장·온톨로지에 똑같이 쌓입니다.
설정: WATCHLIST_SCHEDULE(on/off, 기본 off) · WATCHLIST_TIME(HH:MM KST, 기본 07:40)
"""

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.watchlist')
KST = timezone(timedelta(hours=9))
_lock = threading.RLock()
_run_lock = threading.Lock()
_state: Dict[str, Any] = {'current': None, 'thread': None}
MAX_ITEMS = 40


def _dir() -> str:
    d = os.path.join(Config.UPLOAD_FOLDER, 'watchlist')
    os.makedirs(os.path.join(d, 'runs'), exist_ok=True)
    return d


def _path() -> str:
    return os.path.join(_dir(), 'watchlist.json')


def _read_json(p, default):
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(p, data):
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


# ─────────────────────────────────────────────────────────── 목록
def items() -> List[Dict[str, Any]]:
    with _lock:
        return _read_json(_path(), {'items': []}).get('items', [])


def add(ticker: str, name: str, market: str = '') -> Dict[str, Any]:
    if not re.fullmatch(r'\d{6}', ticker or ''):
        raise ValueError('종목코드는 숫자 6자리입니다')
    with _lock:
        data = _read_json(_path(), {'items': []})
        lst = data.setdefault('items', [])
        if any(x['ticker'] == ticker for x in lst):
            return {'added': False, 'items': lst}
        if len(lst) >= MAX_ITEMS:
            raise ValueError(f'관심종목은 최대 {MAX_ITEMS}개까지입니다')
        lst.append({'ticker': ticker, 'name': (name or ticker)[:40], 'market': (market or '')[:10],
                    'added_at': datetime.now(KST).isoformat(timespec='seconds')})
        _write_json(_path(), data)
        return {'added': True, 'items': lst}


def remove(ticker: str) -> Dict[str, Any]:
    with _lock:
        data = _read_json(_path(), {'items': []})
        before = len(data.get('items', []))
        data['items'] = [x for x in data.get('items', []) if x['ticker'] != ticker]
        _write_json(_path(), data)
        return {'removed': before != len(data['items']), 'items': data['items']}


# ─────────────────────────────────────────────────────────── 일괄 실행
def _base_url() -> str:
    return f"http://127.0.0.1:{int(os.environ.get('FLASK_PORT', 5001))}"


def _post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(_base_url() + path, json=body, timeout=60)
    j = r.json()
    if not j.get('success'):
        raise RuntimeError(j.get('error') or f'HTTP {r.status_code}')
    return j


def _follow(task_id: str, limit_sec: int, on_msg=None) -> Dict[str, Any]:
    t0 = time.time()
    while time.time() - t0 < limit_sec:
        time.sleep(3)
        try:
            t = requests.get(f"{_base_url()}/api/stock/task/{task_id}", timeout=15).json().get('data') or {}
        except Exception:
            continue
        if on_msg and t.get('message'):
            on_msg(t.get('progress'), t.get('message'))
        if t.get('status') == 'completed':
            return t
        if t.get('status') == 'failed':
            raise RuntimeError(t.get('error') or t.get('message') or '작업 실패')
    raise TimeoutError(f'{limit_sec}초 안에 끝나지 않음')


def _save_run(run: Dict[str, Any]) -> None:
    _write_json(os.path.join(_dir(), 'runs', f"{run['id']}.json"), run)


def _run_one(run: Dict[str, Any], it: Dict[str, Any]) -> None:
    rec = it
    t0 = time.time()

    def stage(name, msg=''):
        rec['stage'], rec['message'] = name, (msg or '')[:160]
        _save_run(run)

    try:
        stage('분석', '데이터·리서치 수집 시작')
        a = _post('/api/stock/analyze', {'ticker': rec['ticker'], 'company_name': rec['name']})['data']
        _follow(a['task_id'], 900, lambda p, m: stage('분석', m))
        stage('시뮬레이션')
        s = _post('/api/stock/simulate', {'task_id': a['task_id'], 'num_agents': 50, 'simulation_rounds': 10})['data']
        _follow(s.get('sim_task_id') or s.get('task_id'), 600, lambda p, m: stage('시뮬레이션', m))
        stage('보고서')
        r = _post(f"/api/stock/report/{s['simulation_id']}", {})['data']
        rec['report_id'] = r['report_id']
        if r.get('task_id') and not r.get('already_generated'):
            _follow(r['task_id'], 1200, lambda p, m: stage('보고서', m))
        stage('지식그래프')
        k = _post(f"/api/stock/kg/{r['report_id']}", {})
        if k.get('async'):
            _follow(k['data']['task_id'], 900, lambda p, m: stage('지식그래프', m))
        stage('판단')
        d = _post(f"/api/stock/decision/{r['report_id']}", {})
        if d.get('async'):
            _follow(d['data']['task_id'], 1200, lambda p, m: stage('판단', m))
        dec = requests.get(f"{_base_url()}/api/stock/decision/{r['report_id']}", timeout=20).json().get('data') or {}
        rec.update(status='done', stage='완료', verdict=dec.get('verdict'), verdict_pct=dec.get('verdict_pct'),
                   direction=dec.get('direction'), message=dec.get('headline') or '')
    except Exception as e:
        rec.update(status='failed', message=str(e)[:200])
        logger.warning(f"[관심종목] {rec['ticker']} 실패: {e}")
    rec['sec'] = round(time.time() - t0, 1)
    _save_run(run)


def start_run(tickers: Optional[List[str]] = None, trigger: str = 'manual') -> Dict[str, Any]:
    if not _run_lock.acquire(blocking=False):
        cur = _state.get('current')
        return {'started': False, 'run': cur, 'message': '이미 일괄 분석이 진행 중입니다'}
    try:
        lst = items()
        if tickers:
            lst = [x for x in lst if x['ticker'] in set(tickers)] or \
                  [{'ticker': t, 'name': t} for t in tickers if re.fullmatch(r'\d{6}', t)]
        if not lst:
            _run_lock.release()
            return {'started': False, 'message': '관심종목이 없습니다'}
        run = {'id': datetime.now(KST).strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:4], 'trigger': trigger,
               'started_at': datetime.now(KST).isoformat(timespec='seconds'), 'finished_at': None, 'status': 'running',
               'items': [{'ticker': x['ticker'], 'name': x.get('name') or x['ticker'], 'status': 'pending', 'stage': '대기',
                          'message': '', 'report_id': None} for x in lst]}
        _state['current'] = run
        _save_run(run)
    except Exception:
        _run_lock.release()
        raise

    def worker():
        try:
            from . import ops_metrics
            for it in run['items']:
                it['status'] = 'running'
                ops_metrics.staged('watchlist', it['ticker'], _run_one)(run, it)
            run['status'] = 'done'
        except Exception as e:
            run['status'] = 'failed'
            run['error'] = str(e)[:200]
        finally:
            run['finished_at'] = datetime.now(KST).isoformat(timespec='seconds')
            _save_run(run)
            _state['current'] = None
            _run_lock.release()
            ok = sum(1 for x in run['items'] if x['status'] == 'done')
            logger.info(f"[관심종목] 일괄 분석 종료 ({trigger}): {ok}/{len(run['items'])} 성공")
            try:
                from . import alerts_service
                lines = [f"· {x['name']}({x['ticker']}): {x.get('verdict') or '실패'}"
                         + (f" {x.get('verdict_pct')}%" if x.get('verdict_pct') is not None else '') for x in run['items']]
                alerts_service.notify('watchlist_run', f"관심종목 일괄 분석 완료 {ok}/{len(run['items'])}", '\n'.join(lines))
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True, name='watchlist-run').start()
    return {'started': True, 'run': run}


def runs(limit: int = 10) -> List[Dict[str, Any]]:
    d = os.path.join(_dir(), 'runs')
    files = sorted((f for f in os.listdir(d) if f.endswith('.json')), reverse=True)[:limit]
    out = [_read_json(os.path.join(d, f), None) for f in files]
    return [r for r in out if r]


# ─────────────────────────────────────────────────────────── 스케줄
def schedule_enabled() -> bool:
    return (os.environ.get('WATCHLIST_SCHEDULE') or 'off').strip().lower() in ('on', '1', 'true', 'yes')


def schedule_time():
    m = re.fullmatch(r'\s*(\d{1,2}):(\d{2})\s*', os.environ.get('WATCHLIST_TIME') or '')
    if m and 0 <= int(m.group(1)) < 24 and 0 <= int(m.group(2)) < 60:
        return int(m.group(1)), int(m.group(2))
    return 7, 40


def status() -> Dict[str, Any]:
    st = _read_json(os.path.join(_dir(), 'schedule_state.json'), {})
    h, m = schedule_time()
    return {'enabled': schedule_enabled(), 'time': f'{h:02d}:{m:02d}', 'last_auto_day': st.get('last_day'),
            'running': _state.get('current'), 'count': len(items())}


def _loop():
    from .theme_scheduler import is_business_day
    time.sleep(60)
    while True:
        try:
            now = datetime.now(KST)
            st_path = os.path.join(_dir(), 'schedule_state.json')
            st = _read_json(st_path, {})
            h, m = schedule_time()
            day = now.strftime('%Y-%m-%d')
            if (schedule_enabled() and is_business_day(now.date()) and (now.hour, now.minute) >= (h, m)
                    and now.hour < 15 and st.get('last_day') != day and items()):
                res = start_run(trigger='schedule')
                if res.get('started'):
                    _write_json(st_path, {'last_day': day, 'run_id': res['run']['id']})
        except Exception as e:
            logger.warning(f"관심종목 스케줄 확인 실패: {e}")
        time.sleep(60)


def start_scheduler(debug: bool) -> bool:
    if debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return False
    t = _state.get('thread')
    if t is not None and t.is_alive():
        return True
    t = threading.Thread(target=_loop, daemon=True, name='watchlist-scheduler')
    _state['thread'] = t
    t.start()
    return True
