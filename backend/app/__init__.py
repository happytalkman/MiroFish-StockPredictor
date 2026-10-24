"""
WeAidStock 백엔드 - Flask 애플리케이션 팩토리
"""

import os
import warnings

# multiprocessing resource_tracker 경고 억제 (transformers 등 서드파티 라이브러리에서 발생)
# 다른 모든 import보다 먼저 설정해야 함
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Flask 애플리케이션 팩토리 함수"""
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # JSON 인코딩 설정: 한글 등 비ASCII 문자를 그대로 표시 (\uXXXX 형식 대신)
    # Flask >= 2.3은 app.json.ensure_ascii 사용, 구버전은 JSON_AS_ASCII 설정 사용
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # 로깅 설정
    logger = setup_logger('mirofish')
    
    # reloader 자식 프로세스에서만 시작 정보 출력 (debug 모드에서 두 번 출력되는 것 방지)
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("WeAidStock 백엔드 시작 중...")
        logger.info("=" * 50)
    
    # 원격 접속 게이트(stock.webig.ai) + 빌드된 화면 서빙 — 다른 before_request 보다 먼저 등록
    from .remote_access import init_remote_access, public_url
    init_remote_access(app)

    # CORS — 화면은 같은 출처(Vite 프록시 또는 이 백엔드가 직접 서빙)로 부르므로 로컬 화면 주소와 공개 주소만 허용
    _web_port = int(os.environ.get('WEAID_WEB_PORT', '3300') or 3300)
    _origins = [f'http://{h}:{p}' for h in ('localhost', '127.0.0.1')
                for p in [3000, 5001] + list(range(_web_port, _web_port + 10))] + [public_url()]
    CORS(app, resources={r"/api/*": {"origins": _origins}})
    
    # 시뮬레이션 프로세스 정리 함수 등록 (서버 종료 시 모든 시뮬레이션 프로세스 종료 보장)
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("시뮬레이션 프로세스 정리 함수 등록 완료")
    
    # 요청 로깅 미들웨어
    @app.before_request
    def log_request():
        logger = get_logger('mirofish.request')
        logger.debug(f"요청: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            logger.debug(f"요청 본문: {request.get_json(silent=True)}")
    
    @app.after_request
    def log_response(response):
        logger = get_logger('mirofish.request')
        logger.debug(f"응답: {response.status_code}")
        return response
    
    # 블루프린트 등록
    from .api import graph_bp, simulation_bp, report_bp, stock_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    # 주식 예측 Blueprint 등록
    app.register_blueprint(stock_bp)
    # 시장 대시보드 Blueprint (첫 화면)
    from .api.market import market_bp
    app.register_blueprint(market_bp)
    # 로컬 전용 관리 API (설정·재시작·연결 테스트·감성 모델 설치)
    from .api.admin import admin_bp
    app.register_blueprint(admin_bp)
    # 투자위원회 토론 (5단계 판단 입력) — GET/POST /api/stock/committee/<report_id>
    from .api.committee import committee_bp
    app.register_blueprint(committee_bp)
    # 예측 원장 (기록·채점·보정)
    from .api.ledger import ledger_bp
    app.register_blueprint(ledger_bp)
    # 보관함·관심종목·알림
    from .api.library import library_bp
    app.register_blueprint(library_bp)
    # 누적 온톨로지 (SHACL·SPARQL·Graph RAG)
    from .api.ontology import ontology_bp
    app.register_blueprint(ontology_bp)
    # Cloudflare Worker Cron 전용 내부 엔드포인트 (CRON_SECRET 헤더로 보호)
    from .api.internal import internal_bp
    app.register_blueprint(internal_bp)

    # 운영 지표 (LLM 호출·토큰·비용, 단계별 소요 시간) — OPS_METRICS=off 로 끔
    try:
        from .services import ops_metrics
        ops_metrics.install()
    except Exception as e:
        logger.warning(f"운영 지표 계측 설치 실패: {e}")

    # 백그라운드 작업(스케줄러·모델 로딩·터널)은 자체 점검(WEAIDSTOCK_TESTING=1)에서는 시작하지 않음
    if os.environ.get('WEAIDSTOCK_TESTING') != '1':
        # 오늘의 테마 스케줄러 (평일 08:40 KST 자동 추출) — 디버그 리로더의 부모(감시) 프로세스에서는 시작하지 않음
        try:
            from .services import theme_scheduler
            theme_scheduler.start(debug=debug_mode)
        except Exception as e:
            logger.warning(f"테마 스케줄러 시작 실패: {e}")

        # 예측 원장 정기 채점 (평일 16:40 KST, 시작 90초 뒤 1회 백필·채점)
        try:
            from .services import prediction_ledger
            prediction_ledger.start_scheduler(debug=debug_mode)
        except Exception as e:
            logger.warning(f"예측 원장 스케줄러 시작 실패: {e}")

        # 관심종목 아침 일괄 분석 · 매매계획 가격 알림
        try:
            from .services import watchlist_service, alerts_service
            watchlist_service.start_scheduler(debug=debug_mode)
            alerts_service.start_scheduler(debug=debug_mode)
        except Exception as e:
            logger.warning(f"관심종목·알림 스케줄러 시작 실패: {e}")

        # 로컬 감성 모델 미리 로딩 (서빙 프로세스에서만, 백그라운드) — 첫 분석 대기 시간 단축
        if not debug_mode or is_reloader_process:
            try:
                from .services import sentiment
                if sentiment.warmup():
                    logger.info("로컬 감성 모델 미리 로딩 예약")
            except Exception as e:
                logger.warning(f"감성 모델 미리 로딩 준비 실패: {e}")

        # Cloudflare Tunnel 커넥터 자동 실행 (토큰이 설정돼 있고 REMOTE_TUNNEL 이 off 가 아닐 때, Windows)
        try:
            from .services import tunnel_manager
            tunnel_manager.autostart(debug=debug_mode)
        except Exception as e:
            logger.warning(f"터널 커넥터 자동 실행 준비 실패: {e}")
    

    # 헬스 체크
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'WeAidStock Backend'}
    
    if should_log_startup:
        logger.info("WeAidStock 백엔드 시작 완료")
    
    return app
