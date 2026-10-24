"""
보관함 · 관심종목 · 알림 API

GET    /api/library/reports[?ticker=&q=&limit=]     저장된 보고서 목록 (판단·지식그래프 요약 포함)
GET    /api/library/reports/<id>/meta               다시 열기에 필요한 정보 (분석 task_id 등)
GET    /api/library/compare?ids=a,b,c                여러 실행 비교 (같은 종목이면 흔들림 폭)
GET    /api/watchlist                                관심종목 + 스케줄 상태
POST   /api/watchlist {ticker, name, market}
DELETE /api/watchlist/<ticker>
POST   /api/watchlist/run {tickers?}                 일괄 5단계 분석 시작
GET    /api/watchlist/runs                           최근 일괄 실행 기록
GET    /api/alerts[?unread=1]                        알림함
POST   /api/alerts/read {ids?}                       읽음 처리 (없으면 전부)
POST   /api/alerts/check                             지금 가격 확인
"""

import re

from flask import Blueprint, jsonify, request

from ..services import report_library, watchlist_service, alerts_service
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.library')
library_bp = Blueprint('library', __name__)
_RID = re.compile(r'^stock_report_[0-9a-f]{6,32}$')
_TICKER = re.compile(r'^\d{6}$')


def _ok(data):
    return jsonify({'success': True, 'data': data})


def _err(msg, code=400):
    return jsonify({'success': False, 'error': msg}), code


@library_bp.route('/api/library/reports', methods=['GET'])
def reports():
    t = (request.args.get('ticker') or '').strip()
    if t and not _TICKER.match(t):
        return _err('종목코드 형식 오류')
    q = (request.args.get('q') or '').strip()[:40]
    try:
        limit = int(request.args.get('limit') or 200)
    except ValueError:
        limit = 200
    return _ok(report_library.list_reports(t or None, q or None, limit))


@library_bp.route('/api/library/reports/<report_id>/meta', methods=['GET'])
def report_meta(report_id):
    if not _RID.match(report_id):
        return _err('보고서 ID 형식 오류')
    m = report_library.meta(report_id)
    return _ok(m) if m else _err('보고서를 찾을 수 없습니다', 404)


@library_bp.route('/api/library/compare', methods=['GET'])
def compare():
    ids = [x.strip() for x in (request.args.get('ids') or '').split(',') if x.strip()]
    ids = [x for x in ids if _RID.match(x)][:6]
    if len(ids) < 2:
        return _err('비교할 보고서를 2개 이상 고르세요')
    return _ok(report_library.compare(ids))


@library_bp.route('/api/watchlist', methods=['GET'])
def watchlist():
    lst = watchlist_service.items()
    latest = {}
    for it in report_library.list_reports(limit=1000):
        latest.setdefault(it['ticker'], it)
    return _ok({'items': [dict(x, latest=latest.get(x['ticker'])) for x in lst],
                'schedule': watchlist_service.status(), 'alerts': alerts_service.status()})


@library_bp.route('/api/watchlist', methods=['POST'])
def watchlist_add():
    b = request.get_json(silent=True) or {}
    try:
        return _ok(watchlist_service.add(str(b.get('ticker') or '').strip(), str(b.get('name') or ''), str(b.get('market') or '')))
    except ValueError as e:
        return _err(str(e))


@library_bp.route('/api/watchlist/<ticker>', methods=['DELETE'])
def watchlist_remove(ticker):
    if not _TICKER.match(ticker):
        return _err('종목코드 형식 오류')
    return _ok(watchlist_service.remove(ticker))


@library_bp.route('/api/watchlist/run', methods=['POST'])
def watchlist_run():
    b = request.get_json(silent=True) or {}
    tickers = [t for t in (b.get('tickers') or []) if isinstance(t, str) and _TICKER.match(t)] or None
    res = watchlist_service.start_run(tickers, trigger='manual')
    return (_ok(res) if res.get('started') else _err(res.get('message') or '시작하지 못했습니다', 409))


@library_bp.route('/api/watchlist/runs', methods=['GET'])
def watchlist_runs():
    return _ok(watchlist_service.runs(10))


@library_bp.route('/api/alerts', methods=['GET'])
def alerts():
    unread = request.args.get('unread') in ('1', 'true')
    return _ok(dict(alerts_service.list_alerts(unread, 50), status=alerts_service.status()))


@library_bp.route('/api/alerts/read', methods=['POST'])
def alerts_read():
    b = request.get_json(silent=True) or {}
    ids = [int(i) for i in (b.get('ids') or []) if str(i).isdigit()][:200]
    return _ok({'updated': alerts_service.mark_read(ids or None)})


@library_bp.route('/api/alerts/check', methods=['POST'])
def alerts_check():
    try:
        r = alerts_service.check_once()
        return _ok({'checked': r['checked'], 'fired': len(r['fired'])})
    except Exception as e:
        return _err(str(e), 500)
