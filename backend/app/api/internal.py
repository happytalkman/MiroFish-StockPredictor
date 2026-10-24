"""
예약 작업 진입점 — 클라우드(Cloudflare Worker Cron 등)가 컨테이너 안의 정기 작업을 깨울 때 사용

POST /api/internal/cron/<job>    헤더 X-WeAid-Cron: <CRON_SECRET>
  wake       컨테이너를 깨워 두기만 함 (안의 스케줄러가 스스로 판단)
  themes     오늘의 테마 추출
  ledger     예측 원장 채점
  alerts     매매계획 가격 확인 1회
  watchlist  관심종목 일괄 5단계 분석
  snapshot   uploads 상태 스냅샷(SNAPSHOT_DIR, 기본 /tmp/weaid_snapshot) — entrypoint 가 R2 로 올림

CRON_SECRET(24자 이상)이 설정돼 있지 않으면 모든 요청이 404 입니다. PC 에서는 쓰지 않습니다.
"""

import os
import threading

from flask import Blueprint, jsonify

from ..remote_access import internal_authorized
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.internal')
internal_bp = Blueprint('internal', __name__, url_prefix='/api/internal')
_busy = threading.Lock()


@internal_bp.before_request
def _guard():
    if not internal_authorized():
        return jsonify({'success': False, 'error': '찾을 수 없습니다'}), 404


def _bg(name, fn):
    def run():
        try:
            fn()
        except Exception as e:
            logger.warning(f"예약 작업 {name} 실패: {e}")
    threading.Thread(target=run, daemon=True, name=f'cron-{name}').start()


@internal_bp.route('/cron/<job>', methods=['POST'])
def cron(job: str):
    if job == 'wake':
        return jsonify({'success': True, 'data': {'job': job}})
    if job == 'themes':
        from ..services import theme_scheduler
        return jsonify({'success': True, 'data': {'job': job, 'task_id': theme_scheduler.start_task('cron')}})
    if job == 'ledger':
        from ..services import prediction_ledger
        _bg(job, prediction_ledger.score_pending)
    elif job == 'alerts':
        from ..services import alerts_service
        _bg(job, alerts_service.check_once)
    elif job == 'watchlist':
        from ..services import watchlist_service
        return jsonify({'success': True, 'data': watchlist_service.start_run(trigger='cron')})
    elif job == 'snapshot':
        if not _busy.acquire(blocking=False):
            return jsonify({'success': False, 'error': '스냅샷 진행 중'}), 409
        try:
            from ..services import state_snapshot
            dest = os.environ.get('SNAPSHOT_DIR') or '/tmp/weaid_snapshot'
            return jsonify({'success': True, 'data': state_snapshot.snapshot(dest)})
        finally:
            _busy.release()
    else:
        return jsonify({'success': False, 'error': f'알 수 없는 작업: {job}'}), 400
    return jsonify({'success': True, 'data': {'job': job, 'started': True}})
