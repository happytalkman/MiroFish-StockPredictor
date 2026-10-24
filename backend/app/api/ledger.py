"""
예측 원장 API — 기록된 예측·채점 결과·엔진별 성적·보정 파라미터

GET  /api/ledger/summary[?ticker=]        엔진별 적중률·Brier·보정 곡선·매매계획 성적
GET  /api/ledger/predictions[?ticker=&status=&limit=]
GET  /api/ledger/prediction/<report_id>
POST /api/ledger/score                    지금 채점 (장 마감 후 자동으로도 실행)
POST /api/ledger/backfill                 기존 판단(decision.json) 가져오기
"""

import re
import threading

from flask import Blueprint, jsonify, request

from ..services import prediction_ledger as ledger
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.ledger')
ledger_bp = Blueprint('ledger', __name__, url_prefix='/api/ledger')
_ID = re.compile(r'^[A-Za-z0-9_\-]{4,80}$')
_TICKER = re.compile(r'^[0-9A-Z]{6}$')
_busy = threading.Lock()


def _ok(data):
    return jsonify({'success': True, 'data': data})


def _err(msg, code=400):
    return jsonify({'success': False, 'error': msg}), code


@ledger_bp.route('/summary', methods=['GET'])
def summary():
    t = (request.args.get('ticker') or '').strip().upper()
    if t and not _TICKER.match(t):
        return _err('종목코드 형식 오류')
    try:
        return _ok(ledger.summary(t or None))
    except Exception as e:
        logger.error(f"원장 요약 실패: {e}")
        return _err(str(e), 500)


@ledger_bp.route('/predictions', methods=['GET'])
def predictions():
    t = (request.args.get('ticker') or '').strip().upper()
    st = (request.args.get('status') or '').strip()
    if t and not _TICKER.match(t):
        return _err('종목코드 형식 오류')
    if st and st not in ('pending', 'partial', 'scored'):
        return _err('상태 값 오류')
    try:
        limit = int(request.args.get('limit') or 200)
    except ValueError:
        limit = 200
    return _ok(ledger.list_predictions(t or None, st or None, limit))


@ledger_bp.route('/prediction/<report_id>', methods=['GET'])
def prediction(report_id):
    if not _ID.match(report_id):
        return _err('ID 형식 오류')
    p = ledger.get_prediction(report_id)
    return _ok(p) if p else _err('원장에 없는 예측입니다', 404)


@ledger_bp.route('/score', methods=['POST'])
def score():
    if not _busy.acquire(blocking=False):
        return _err('채점이 이미 진행 중입니다', 409)
    try:
        body = request.get_json(silent=True) or {}
        rid = body.get('report_id')
        if rid and not _ID.match(str(rid)):
            return _err('ID 형식 오류')
        return _ok(ledger.score_pending(only_id=rid or None))
    except Exception as e:
        logger.error(f"원장 채점 실패: {e}")
        return _err(str(e), 500)
    finally:
        _busy.release()


@ledger_bp.route('/backfill', methods=['POST'])
def backfill():
    try:
        return _ok(ledger.backfill())
    except Exception as e:
        return _err(str(e), 500)
