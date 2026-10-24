"""
누적 온톨로지 API — 보고서마다 만든 지식그래프를 하나로 쌓은 전역 그래프 조회·검증·질의

GET  /api/ontology/stats                         트리플·개체 수, 여러 종목을 잇는 공유 개념
GET  /api/ontology/search?q=                     개체 검색 (라벨·IRI)
GET  /api/ontology/node?iri=&depth=1|2           이웃 그래프 (노드·엣지·속성값)
GET  /api/ontology/history/<ticker>              새 분석에 넣는 과거 맥락(H#) 미리보기
POST /api/ontology/backfill[?force=1]            저장된 보고서·테마·원장 결과를 모두 다시 적재 (force: 바뀌지 않은 것도)
GET  /api/ontology/validate                      SHACL 형태 규칙 검증 (내장 + pyshacl 이 있으면 표준)
GET  /api/ontology/export.ttl                    전역 그래프 Turtle 내려받기 (?shapes=1 이면 shapes.ttl)
POST /api/ontology/sparql   {query}              읽기 전용 SPARQL (rdflib 필요)
"""

import re
import threading

from flask import Blueprint, Response, jsonify, request

from ..services import ontology_store as store
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.ontology')
ontology_bp = Blueprint('ontology', __name__, url_prefix='/api/ontology')
_TICKER = re.compile(r'^[0-9A-Z]{6}$')
_IRI = re.compile(r'^[a-z]{1,12}/[^\s<>"{}|\\^`]{1,300}$')
_busy = threading.Lock()


def _ok(data):
    return jsonify({'success': True, 'data': data})


def _err(msg, code=400):
    return jsonify({'success': False, 'error': msg}), code


@ontology_bp.route('/stats', methods=['GET'])
def stats():
    try:
        return _ok(store.stats())
    except Exception as e:
        logger.error(f"온톨로지 통계 실패: {e}")
        return _err(str(e)[:300], 500)


@ontology_bp.route('/search', methods=['GET'])
def search():
    q = (request.args.get('q') or '').strip()[:60]
    if not q:
        return _ok([])
    return _ok(store.search(q, limit=min(60, int(request.args.get('limit') or 30))))


@ontology_bp.route('/node', methods=['GET'])
def node():
    iri = (request.args.get('iri') or '').strip()
    if not _IRI.match(iri):
        return _err('IRI 형식 오류 (예: stock/005930, concept/hbm)')
    try:
        depth = int(request.args.get('depth') or 1)
    except ValueError:
        depth = 1
    return _ok(store.neighborhood(iri, depth=depth))


@ontology_bp.route('/history/<ticker>', methods=['GET'])
def history(ticker):
    t = (ticker or '').strip().upper()
    if not _TICKER.match(t):
        return _err('종목코드 형식 오류')
    return _ok(store.history_facts(t, exclude_report=request.args.get('exclude') or None, limit=8))


@ontology_bp.route('/backfill', methods=['POST'])
def backfill():
    if not _busy.acquire(blocking=False):
        return _err('이미 적재 중입니다', 409)
    try:
        return _ok(store.backfill(force=bool(request.args.get('force'))))
    except Exception as e:
        logger.error(f"온톨로지 백필 실패: {e}")
        return _err(str(e)[:300], 500)
    finally:
        _busy.release()


@ontology_bp.route('/validate', methods=['GET'])
def validate():
    try:
        return _ok(store.validate())
    except Exception as e:
        logger.error(f"SHACL 검증 실패: {e}")
        return _err(str(e)[:300], 500)


@ontology_bp.route('/export.ttl', methods=['GET'])
def export_ttl():
    if request.args.get('shapes'):
        body, name = store.SHAPES_TTL, 'weaidstock_shapes.ttl'
    else:
        body, name = store.export_ttl(), 'weaidstock_global.ttl'
    return Response(body, mimetype='text/turtle; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


@ontology_bp.route('/sparql', methods=['POST'])
def sparql():
    q = ((request.get_json(silent=True) or {}).get('query') or '').strip()
    try:
        return _ok(store.sparql(q))
    except (ValueError, RuntimeError) as e:
        return _err(str(e)[:400])
    except Exception as e:
        return _err(f'질의 오류: {type(e).__name__}: {str(e)[:300]}')
