"""
투자위원회 토론 API (5단계 판단의 입력)

  GET  /api/stock/committee/<report_id>   저장된 committee.json (없으면 404)
  POST /api/stock/committee/<report_id>   body {force: bool, rounds: 1~3}
        → force 가 아니고 저장본이 있으면 그대로 반환, 아니면 백그라운드 작업 시작 후 {task_id}
          (진행 상황: /api/stock/task/<task_id> 또는 /stream — 판단 작업과 같은 방식)
"""

import re

from flask import Blueprint, request, jsonify

from ..services.stock_committee import InvestmentCommittee
from ..utils.logger import get_logger

committee_bp = Blueprint('committee', __name__, url_prefix='/api/stock')
logger = get_logger('mirofish.api.committee')

_ID_RE = re.compile(r'^[\w\-]{1,128}$')


def _bad_id(report_id: str):
    if not _ID_RE.match(report_id or ''):
        return jsonify({"success": False, "error": "잘못된 보고서 ID 입니다"}), 400
    return None


@committee_bp.route('/committee/<report_id>', methods=['GET'])
def get_committee(report_id: str):
    bad = _bad_id(report_id)
    if bad:
        return bad
    c = InvestmentCommittee.load_cached(report_id)
    if not c:
        return jsonify({"success": False, "error": "투자위원회 토론 결과가 없습니다"}), 404
    return jsonify({"success": True, "data": c})


@committee_bp.route('/committee/<report_id>', methods=['POST'])
def run_committee(report_id: str):
    bad = _bad_id(report_id)
    if bad:
        return bad
    try:
        from .stock import _start_task
        data = request.get_json(silent=True) or {}
        try:
            rounds = max(1, min(3, int(data.get('rounds') or 2)))
        except (TypeError, ValueError):
            rounds = 2
        if not data.get('force'):
            c = InvestmentCommittee.load_cached(report_id)
            if c:
                return jsonify({"success": True, "data": c, "cached": True})
        task_id = _start_task('committee', report_id,
                              lambda progress: InvestmentCommittee().run(report_id, progress, rounds))
        return jsonify({"success": True, "data": {"task_id": task_id}, "async": True})
    except Exception as e:
        logger.error(f"투자위원회 토론 시작 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
