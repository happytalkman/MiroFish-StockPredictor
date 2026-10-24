"""
시장 대시보드 API (첫 화면)
/api/market/*
"""

import re
import traceback
from flask import Blueprint, jsonify, request

from ..services import market_data as md
from ..services import stock_screener as sc
from ..services import theme_service as ts
from ..services import theme_scheduler
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.market')
market_bp = Blueprint('market', __name__, url_prefix='/api/market')
_MARKETS = {'KOSPI', 'KOSDAQ'}
_SCREENER_MARKETS = _MARKETS | {'ALL'}
_CHART_SYMBOL = re.compile(r'(?:\d{6}|KOSPI|KOSDAQ|KPI200)')


def _ok(data):
    return jsonify({"success": True, "data": data})


def _err(e, code=500):
    logger.error(f"시장 API 오류: {e}")
    return jsonify({"success": False, "error": str(e)}), code


def _bounded_int(name, default, minimum, maximum):
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} 값은 정수여야 합니다') from exc
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} 값은 {minimum}~{maximum} 범위여야 합니다')
    return value


@market_bp.route('/overview', methods=['GET'])
def overview():
    """국내 지수(스파크라인) + 해외 지수·환율·금리·원자재 + 암호화폐(CMC) + 장 상태"""
    try:
        return _ok(md.overview())
    except Exception as e:
        return _err(e)


@market_bp.route('/movers', methods=['GET'])
def movers():
    market = (request.args.get('market') or 'KOSPI').upper()
    if market not in _MARKETS:
        return _err('market 값은 KOSPI 또는 KOSDAQ 이어야 합니다', 400)
    try:
        return _ok(md.cached(f'movers:{market}', 60, md.korea_movers, market))
    except Exception as e:
        return _err(e)


@market_bp.route('/news', methods=['GET'])
def news():
    cat = request.args.get('cat', 'domestic')
    if cat not in md.NEWS_QUERIES:
        return _err(f"cat 값은 {', '.join(md.NEWS_QUERIES)} 중 하나여야 합니다", 400)
    try:
        return _ok(md.cached(f'news:{cat}', 300, md.news, cat))
    except Exception as e:
        return _err(e)


@market_bp.route('/calendar', methods=['GET'])
def calendar():
    try:
        days = _bounded_int('days', 45, 1, 180)
        return _ok({'events': md.market_calendar(days), 'note': '옵션 만기·고용지표 등은 규칙으로 계산하고, FOMC 는 연준 공표 일정입니다. 실적 시즌은 대략적 시점입니다.'})
    except ValueError as e:
        return _err(e, 400)
    except Exception as e:
        return _err(e)


@market_bp.route('/chart/<symbol>', methods=['GET'])
def chart(symbol):
    """국내 종목·지수 일봉 (대시보드 미니차트용)"""
    symbol = symbol.upper()
    if not _CHART_SYMBOL.fullmatch(symbol):
        return _err('symbol 값은 6자리 종목코드 또는 KOSPI·KOSDAQ·KPI200 이어야 합니다', 400)
    try:
        n = _bounded_int('count', 120, 2, 500)
        return _ok(md.cached(f'chart:{symbol}:{n}', 300, md.fchart, symbol, n))
    except ValueError as e:
        return _err(e, 400)
    except Exception as e:
        return _err(e)


@market_bp.route('/screener/definitions', methods=['GET'])
def screener_defs():
    return _ok(sc.definitions())


@market_bp.route('/screener', methods=['POST'])
def screener_run():
    """body: {market: ALL|KOSPI|KOSDAQ, size: 200, conditions: {A: {type, params}}, expr: "A and (B or C)",
              codes?: ["005930", …]}  — codes 가 있으면 그 종목들만 검사 (오늘의 테마 구성 종목 등, 최대 200개)"""
    try:
        b = request.get_json(silent=True) or {}
        size = max(30, min(400, int(b.get('size', 200))))
        codes = b.get('codes')
        if codes is not None and not isinstance(codes, list):
            raise ValueError('codes 는 종목코드 배열이어야 합니다')
        if codes is not None and not sc.normalize_codes(codes):
            raise ValueError('유효한 종목코드(6자리)가 없습니다')
        r = sc.run((b.get('market') or 'ALL').upper(), size, b.get('conditions') or {}, b.get('expr') or '',
                   limit=int(b.get('limit', 40)), codes=codes or None)
        return _ok(r)
    except ValueError as e:
        return _err(e, 400)
    except Exception as e:
        logger.error(traceback.format_exc())
        return _err(e)


@market_bp.route('/recommend', methods=['GET'])
def recommend():
    market = (request.args.get('market') or 'ALL').upper()
    if market not in _SCREENER_MARKETS:
        return _err('market 값은 ALL, KOSPI 또는 KOSDAQ 이어야 합니다', 400)
    try:
        return _ok(md.cached(f'recommend:{market}', 600, sc.recommend, market))
    except Exception as e:
        return _err(e)


# ═══════════════════════════════════════════════════════════════ 오늘의 테마
def _sched_status():
    try:
        return theme_scheduler.status()
    except Exception as e:
        return {'error': str(e)[:160]}


@market_bp.route('/themes', methods=['GET'])
def themes():
    """오늘의 테마 (date 없으면 최신). 응답에 scheduler 상태(running_task_id 포함)를 덧붙임"""
    date = (request.args.get('date') or '').strip() or None
    if date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        return _err('날짜 형식은 YYYY-MM-DD 입니다', 400)
    try:
        data = ts.load(date)
        sched = _sched_status()
        if not data:
            msg = f'{date} 에 추출된 테마가 없습니다' if date else '아직 추출된 테마가 없습니다'
            return jsonify({"success": False, "error": msg, "scheduler": sched}), 404
        return _ok({**data, 'scheduler': sched})
    except Exception as e:
        return _err(e)


@market_bp.route('/themes/dates', methods=['GET'])
def theme_dates():
    try:
        return _ok({'dates': ts.list_dates(), 'today': ts.today_kst()})
    except Exception as e:
        return _err(e)


@market_bp.route('/themes/refresh', methods=['POST'])
def theme_refresh():
    """지금 새로 추출 → {task_id} (진행 중이면 그 작업 재사용). 진행은 /api/stock/task/<id>(/stream)"""
    try:
        return _ok({'task_id': theme_scheduler.start_task('manual')})
    except Exception as e:
        logger.error(traceback.format_exc())
        return _err(e)


@market_bp.route('/themes/status', methods=['GET'])
def theme_status():
    return _ok(_sched_status())


@market_bp.route('/debug', methods=['GET'])
def debug():
    """데이터 소스 점검용: 각 소스 원본 일부"""
    src = request.args.get('src', 'all')
    out = {}
    probes = {
        'naver_index': lambda: md._get(f'{md.NAVER_M}/index/KOSPI/basic').json(),
        'naver_list': lambda: (lambda d: (d.get('stocks') or d)[:1] if isinstance(d, dict) else d[:1])(md._get(f'{md.NAVER_M}/stocks/marketValue/KOSPI?page=1&pageSize=2').json()),
        'naver_up': lambda: str(md._get(f'{md.NAVER_M}/stocks/up/KOSPI?page=1&pageSize=2').text)[:600],
        'naver_quant': lambda: str(md._get(f'{md.NAVER_M}/stocks/quantTop/KOSPI?page=1&pageSize=2').text)[:600],
        'naver_integration': lambda: (lambda d: d.get('totalInfos'))(md._get(f'{md.NAVER_M}/stock/005930/integration').json()),
        'fchart': lambda: md.fchart('005930', 3),
        'fchart_index': lambda: md.fchart('KOSPI', 3),
        'yahoo': lambda: md.yahoo_chart('^GSPC'),
        'cmc': lambda: md.crypto_overview(2),
        'news': lambda: md.google_news('코스피', 2),
        'naver_news': lambda: md.naver_main_news(2),
        'naver_theme': lambda: ts.debug_probe_list(),
        'naver_theme_detail': lambda: ts.debug_probe_detail(request.args.get('no')),
    }
    for k, f in probes.items():
        if src != 'all' and src != k:
            continue
        try:
            out[k] = {'ok': True, 'sample': f()}
        except Exception as e:
            out[k] = {'ok': False, 'error': str(e)[:300]}
    return _ok(out)
