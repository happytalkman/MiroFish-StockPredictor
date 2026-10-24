"""
한국 주식 예측 전용 API 라우트
종목 분석, 시뮬레이션, 보고서 생성 등 주식 예측 관련 엔드포인트를 제공합니다.
"""

import uuid
import traceback
import threading
from flask import request, jsonify

from flask import Blueprint
stock_bp = Blueprint('stock', __name__, url_prefix='/api/stock')

from ..config import Config
from ..services.simulation_manager import SimulationManager, SimulationStatus
from ..services.simulation_runner import SimulationRunner, RunnerStatus
from ..services.stock_report_agent import StockReportAgent, StockReportManager, StockReportStatus
from ..services.stock_judge import StockJudge
from ..services.stock_knowledge_graph import StockKnowledgeGraph, synthesize_speech, TTS_VOICES
from ..services.stock_decision import StockDecision
from ..models.task import TaskManager, TaskStatus
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.stock')


# ============== 종목 분석 인터페이스 ==============

class _NoPriceData(RuntimeError):
    """주가 데이터를 전혀 못 가져온 경우 — 빈 데이터로 뒤 단계를 진행하지 않고 분석을 실패 처리"""


_PYKRX = {'mod': None, 'failed_at': 0.0}


def _load_pykrx(timeout: float = 20.0):
    """pykrx 를 제한 시간 안에서만 import (네트워크가 나쁘면 import 가 수 분간 멈추는 경우가 있음). 실패 시 None"""
    import time as _t
    import concurrent.futures as _cf
    if _PYKRX['mod'] is not None:
        return _PYKRX['mod']
    if _t.time() - _PYKRX['failed_at'] < 600:
        return None

    def _imp():
        from pykrx import stock as _s
        return _s
    ex = _cf.ThreadPoolExecutor(max_workers=1)
    try:
        _PYKRX['mod'] = ex.submit(_imp).result(timeout=timeout)
        return _PYKRX['mod']
    except Exception as e:
        logger.warning(f"pykrx 불러오기 실패/지연 — 네이버 데이터만 사용: {e}")
        _PYKRX['failed_at'] = _t.time()
        return None
    finally:
        ex.shutdown(wait=False)


def _net_hint(e: Exception) -> str:
    s = str(e)
    if 'SSL' in s or '10053' in s or 'EOF occurred' in s or 'Connection aborted' in s:
        return 'PC 의 인터넷 암호화 연결이 끊기고 있습니다 (보안 프로그램·VPN·와이파이 확인)'
    if 'timed out' in s.lower() or 'Timeout' in type(e).__name__:
        return '응답 시간 초과 (네트워크 지연)'
    return s[:120]


@stock_bp.route('/analyze', methods=['POST'])
def start_analysis():
    """
    종목 분석 시작 (비동기 작업)

    주식 종목을 분석하기 위해 데이터 수집, 온톨로지 생성, 그래프 빌드를 순차 실행합니다.
    작업이 즉시 task_id를 반환하며, 진행 상태는 /analyze/<task_id>/status로 확인합니다.

    요청 (JSON):
        {
            "ticker": "005930",          // 필수, 종목 코드 (예: 삼성전자 005930)
            "company_name": "삼성전자",   // 필수, 회사명
            "period": "1y",              // 선택, 데이터 수집 기간 (기본값: 1y)
            "research": true             // 선택, false 면 뉴스·공시 리서치(research.json) 생략
        }

    진행 메시지: 가격·재무(10~36%) → 리서치(40~85%, progress_detail.stage='research', round·phase 포함) → 정리(95%)

    반환:
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "ticker": "005930",
                "company_name": "삼성전자",
                "status": "pending",
                "message": "종목 분석 작업이 시작되었습니다"
            }
        }
    """
    try:
        data = request.get_json(silent=True) or {}

        # 필수 파라미터 검증
        ticker = data.get('ticker')
        company_name = data.get('company_name')
        period = data.get('period', '1y')
        do_research = data.get('research', True) is not False   # {"research": false} 로 리서치 생략

        if not ticker:
            return jsonify({
                "success": False,
                "error": "ticker(종목 코드)를 입력해 주세요"
            }), 400

        if not company_name:
            return jsonify({
                "success": False,
                "error": "company_name(회사명)을 입력해 주세요"
            }), 400

        # 비동기 작업 생성
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="stock_analyze",
            metadata={
                "ticker": ticker,
                "company_name": company_name,
                "period": period
            }
        )

        # 백그라운드 작업 정의
        def run_analysis():
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="주식 데이터 수집 초기화 중..."
                )

                # 1단계: 주식 데이터 수집 (pykrx 활용)
                task_manager.update_task(
                    task_id,
                    progress=10,
                    message=f"[{ticker}] 주가 및 재무 데이터 수집 중..."
                )

                try:
                    krx_stock = _load_pykrx()          # 네트워크가 나쁘면 import 자체가 멈출 수 있어 제한 시간 적용
                    from datetime import datetime, timedelta

                    # 기간 계산
                    end_date = datetime.now().strftime("%Y%m%d")
                    if period == '1y':
                        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
                    elif period == '6m':
                        start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
                    elif period == '3m':
                        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
                    else:
                        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

                    # ── 1순위: 네이버 증권 (빠르고 로그인 불필요) ──
                    from ..services import market_data as _md
                    import concurrent.futures as _cf

                    def _pykrx(fn, *a, timeout=20):
                        """pykrx 는 KRX 응답이 없으면 무한 대기할 수 있어 제한 시간을 둠"""
                        ex = _cf.ThreadPoolExecutor(max_workers=1)
                        try:
                            return ex.submit(fn, *a).result(timeout=timeout)
                        finally:
                            ex.shutdown(wait=False)

                    days = {'1y': 250, '6m': 125, '3m': 65}.get(period, 250)
                    ohlcv_data, cap_data, fundamental_data, broker_reports = [], [], [], []
                    # 일봉·재무를 동시에 요청하고 각각 최대 60초만 기다림 (네트워크가 나빠도 무한 대기하지 않음)
                    _ex = _cf.ThreadPoolExecutor(max_workers=2)
                    f_chart = _ex.submit(_md.fchart, ticker, days)
                    f_fund = _ex.submit(_md.naver_fundamentals, ticker)
                    _ex.shutdown(wait=False)
                    try:
                        rows = f_chart.result(timeout=60)
                        ohlcv_data = [{'날짜': f"{r['d'][:4]}-{r['d'][4:6]}-{r['d'][6:]}", '시가': r['o'], '고가': r['h'],
                                       '저가': r['l'], '종가': r['c'], '거래량': r['v']} for r in rows]
                        task_manager.update_task(task_id, progress=20,
                                                 message=f"[{ticker}] 네이버 일봉 {len(ohlcv_data)}개 수집 완료 — 재무 지표 수집 중")
                    except Exception as e:
                        logger.warning(f"[{task_id}] 네이버 일봉 실패, pykrx 로 대체: {e}")
                        task_manager.update_task(task_id, message=f"[{ticker}] 네이버 일봉 실패 — {_net_hint(e)}")
                    try:
                        fund = f_fund.result(timeout=30)
                        if fund:
                            fundamental_data = [{'날짜': end_date, 'PER': fund.get('per'), 'PBR': fund.get('pbr'),
                                                 'EPS': fund.get('eps'), 'BPS': fund.get('bps'), 'DIV': fund.get('div_yield'),
                                                 'DPS': fund.get('dps'),
                                                 '외인소진율': fund.get('foreign_pct'), '52주최고': fund.get('high52'),
                                                 '52주최저': fund.get('low52'),
                                                 # 증권사 컨센서스 (선행 EPS/PER·목표주가 평균·투자의견 평균 1~5)
                                                 '선행EPS': fund.get('cns_eps'), '선행PER': fund.get('cns_per'),
                                                 '목표주가평균': fund.get('target_mean'), '투자의견평균': fund.get('recomm_mean'),
                                                 '컨센서스일': fund.get('consensus_date')}]
                            broker_reports = fund.get('researches') or []
                    except Exception as e:
                        logger.warning(f"[{task_id}] 네이버 재무 실패: {e}")

                    # ── 2순위: pykrx (제한 시간 20초) ──
                    if not ohlcv_data and krx_stock is not None:
                        task_manager.update_task(task_id, message=f"[{ticker}] KRX(pykrx) 에서 주가 수집 중 (최대 20초)")
                        try:
                            ohlcv_df = _pykrx(krx_stock.get_market_ohlcv_by_date, start_date, end_date, ticker)
                            ohlcv_data = ohlcv_df.reset_index().to_dict(orient='records') if not ohlcv_df.empty else []
                        except Exception as e:
                            logger.warning(f"[{task_id}] pykrx OHLCV 실패: {e}")
                    try:
                        if krx_stock is None:
                            raise RuntimeError('pykrx 사용 불가')
                        cap_df = _pykrx(krx_stock.get_market_cap_by_date, start_date, end_date, ticker, timeout=10)
                        cap_data = cap_df.reset_index().to_dict(orient='records') if not cap_df.empty else []
                    except Exception as e:
                        logger.warning(f"[{task_id}] pykrx 시가총액 건너뜀: {e}")
                    if not fundamental_data and krx_stock is not None:
                        try:
                            fundamental_df = _pykrx(krx_stock.get_market_fundamental_by_date, start_date, end_date, ticker, timeout=10)
                            fundamental_data = fundamental_df.reset_index().to_dict(orient='records') if not fundamental_df.empty else []
                        except Exception:
                            fundamental_data = []

                    financial_data = {
                        "ticker": ticker,
                        "company_name": company_name,
                        "period": period,
                        "start_date": start_date,
                        "end_date": end_date,
                        "ohlcv": ohlcv_data,
                        "market_cap": cap_data,
                        "fundamental": fundamental_data,
                        "broker_reports": broker_reports
                    }

                    # ── 분기 실적(DART·네이버 금융) · 투자자별 수급(KRX·네이버) · 공매도 잔고(KRX) — 동시에 최대 25초
                    try:
                        task_manager.update_task(task_id, progress=30,
                                                 message=f"[{ticker}] 분기 실적·투자자별 수급·공매도 잔고 수집 중")
                        from ..services import market_ext as _mx
                        ext = _mx.collect(ticker, krx_stock, timeout=25)
                        financial_data.update({k: v for k, v in ext.items() if v})
                        got = [lbl for k, lbl in (('quarterly', '분기 실적'), ('flows', '수급'), ('short', '공매도')) if ext.get(k)]
                        logger.info(f"[{task_id}] {ticker} 보강 데이터: {', '.join(got) or '없음'}"
                                    + (f" (실적 출처 {ext['quarterly'].get('source')})" if ext.get('quarterly') else ''))
                    except Exception as e:
                        logger.warning(f"[{task_id}] 실적·수급 보강 실패(무시): {e}")

                    # 날짜 직렬화 처리
                    import json
                    def default_serializer(obj):
                        if hasattr(obj, 'isoformat'):
                            return obj.isoformat()
                        return str(obj)

                    financial_data_str = json.dumps(financial_data, default=default_serializer, ensure_ascii=False)
                    financial_data = json.loads(financial_data_str)

                    logger.info(f"[{task_id}] {ticker} 데이터 수집 완료: OHLCV {len(ohlcv_data)}건")
                    if not ohlcv_data:
                        raise _NoPriceData(f"{company_name}({ticker}) 주가 데이터를 가져오지 못했습니다 — "
                                           "PC 의 인터넷 연결(보안 프로그램·VPN·와이파이)을 확인한 뒤 다시 시도해 주세요. "
                                           "설정 화면 > 연결 테스트로 상태를 볼 수 있습니다.")

                except _NoPriceData:
                    raise
                except ImportError:
                    logger.warning(f"[{task_id}] pykrx 미설치 - 빈 데이터로 진행")
                    financial_data = {
                        "ticker": ticker,
                        "company_name": company_name,
                        "period": period,
                        "ohlcv": [],
                        "market_cap": [],
                        "fundamental": [],
                        "warning": "pykrx 라이브러리가 설치되지 않아 실제 데이터를 가져오지 못했습니다"
                    }
                except Exception as e:
                    logger.warning(f"[{task_id}] 데이터 수집 부분 실패: {str(e)}")
                    financial_data = {
                        "ticker": ticker,
                        "company_name": company_name,
                        "period": period,
                        "ohlcv": [],
                        "market_cap": [],
                        "fundamental": [],
                        "error": str(e)
                    }

                task_manager.update_task(
                    task_id,
                    progress=36,
                    message="온톨로지 생성 중..."
                )

                # 2단계: 주식 분석용 온톨로지 생성
                ontology = _generate_stock_ontology(ticker, company_name)

                # 3단계: 분석 결과를 파일에 저장 (리서치보다 먼저 — 리서치가 실패해도 분석은 유지)
                import os
                import json

                analysis_dir = os.path.join(
                    Config.UPLOAD_FOLDER, 'stock_analyses', task_id
                )
                os.makedirs(analysis_dir, exist_ok=True)

                analysis_result = {
                    "task_id": task_id,
                    "ticker": ticker,
                    "company_name": company_name,
                    "period": period,
                    "ontology": ontology,
                    "financial_data": financial_data,
                    "status": "completed"
                }

                with open(os.path.join(analysis_dir, 'analysis.json'), 'w', encoding='utf-8') as f:
                    json.dump(analysis_result, f, ensure_ascii=False, indent=2)

                # 4단계: 리플렉션 리서치 (뉴스·공시·해외·커뮤니티, 출처 인용) — 진행률 40→85
                research_info = None
                if do_research:
                    task_manager.update_task(
                        task_id, progress=40,
                        message="분석 데이터 저장 완료 — 뉴스·공시 리서치를 시작합니다",
                        progress_detail={'stage': 'research', 'round': 0, 'phase': 'start'}
                    )

                    def research_progress(pct, msg, detail=None):
                        task_manager.update_task(task_id, progress=int(pct), message=msg,
                                                 progress_detail=detail or {'stage': 'research'})
                    try:
                        research_info = _run_research(task_id, ticker, company_name, research_progress, (40, 85))
                    except Exception as re_err:
                        logger.error(f"[{task_id}] 리서치 실패(분석은 계속): {re_err}\n{traceback.format_exc()}")
                        research_info = {'error': str(re_err)[:300]}
                        task_manager.update_task(task_id, progress=85,
                                                 message=f"리서치 실패 — 가격·재무 데이터만으로 계속합니다 ({str(re_err)[:80]})",
                                                 progress_detail={'stage': 'research', 'phase': 'error'})

                task_manager.update_task(
                    task_id,
                    progress=95,
                    message="분석 결과 정리 중...",
                    progress_detail={'stage': 'finalize'}
                )

                logger.info(f"[{task_id}] 종목 분석 완료: {ticker} ({company_name})")

                task_manager.complete_task(
                    task_id,
                    result={
                        "task_id": task_id,
                        "ticker": ticker,
                        "company_name": company_name,
                        "analysis_dir": analysis_dir,
                        "ontology_entity_types": len(ontology.get("entity_types", [])),
                        "data_points": len(financial_data.get("ohlcv", [])),
                        "research": research_info
                    }
                )

            except Exception as e:
                logger.error(f"[{task_id}] 종목 분석 실패: {str(e)}")
                logger.error(traceback.format_exc())
                task_manager.fail_task(task_id, str(e))

        # 백그라운드 스레드 시작
        from ..services import ops_metrics as _ops
        thread = threading.Thread(target=_ops.staged('analysis', ticker, run_analysis, task_id), daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "data": {
                "task_id": task_id,
                "ticker": ticker,
                "company_name": company_name,
                "status": "pending",
                "message": f"{company_name}({ticker}) 종목 분석 작업이 시작되었습니다. "
                           f"/api/stock/analyze/{task_id}/status 로 진행 상태를 확인하세요."
            }
        })

    except Exception as e:
        logger.error(f"종목 분석 시작 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@stock_bp.route('/analyze/<task_id>/status', methods=['GET'])
def get_analysis_status(task_id: str):
    """
    종목 분석 진행 상태 확인

    반환:
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|failed",
                "progress": 60,
                "message": "온톨로지 생성 중...",
                "result": { ... }   // 완료 시에만 포함
            }
        }
    """
    try:
        task_manager = TaskManager()
        task = task_manager.get_task(task_id)

        if not task:
            return jsonify({
                "success": False,
                "error": f"작업을 찾을 수 없습니다: {task_id}"
            }), 404

        return jsonify({
            "success": True,
            "data": task.to_dict()
        })

    except Exception as e:
        logger.error(f"분석 상태 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============== 2단계 리서치 (리플렉션 루프 · 출처 인용) ==============

def _safe_task_id(task_id: str) -> bool:
    import re as _re
    return bool(_re.fullmatch(r'[A-Za-z0-9_\-]{1,80}', task_id or ''))


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    import os
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


def _run_research(task_id: str, ticker: str, company_name: str, progress, pct_range=(40, 85)) -> dict:
    """리서치 에이전트 실행 → stock_analyses/<task_id>/research.json 저장 → 요약 반환.
    환경변수: RESEARCH_ROUNDS(리플렉션 횟수, 기본 2), RESEARCH_TIME_BUDGET(초, 기본 150)"""
    import os
    import json
    from ..services.stock_research import StockResearchAgent
    research = StockResearchAgent().run(
        ticker, company_name, progress=progress,
        rounds=_env_int('RESEARCH_ROUNDS', 2, 0, 4),
        time_budget_sec=_env_int('RESEARCH_TIME_BUDGET', 150, 30, 600),
        pct_range=pct_range,
    )
    d = os.path.join(Config.UPLOAD_FOLDER, 'stock_analyses', task_id)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, 'research.json')
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(research, f, ensure_ascii=False, indent=2)
    os.replace(path + '.tmp', path)
    sent = research.get('sentiment') or {}
    info = {
        "findings": len(research.get('findings') or []),
        "sources": research.get('sources_count', 0),
        "sentiment": sent.get('overall_score'),
        "engine": sent.get('engine'),
    }
    logger.info(f"[{task_id}] 리서치 저장: 발견 {info['findings']}건 · 출처 {info['sources']}곳 · "
                f"감성 {info['sentiment']} ({info['engine']}) · {research.get('elapsed_sec')}초")
    return info


@stock_bp.route('/research/<task_id>', methods=['GET'])
def get_research(task_id: str):
    """2단계 리서치 결과 (research.json). task_id = 분석(analyze) 작업 ID"""
    import os
    import json
    if not _safe_task_id(task_id):
        return jsonify({"success": False, "error": "잘못된 작업 ID 입니다"}), 400
    path = os.path.join(Config.UPLOAD_FOLDER, 'stock_analyses', task_id, 'research.json')
    if not os.path.exists(path):
        return jsonify({"success": False, "error": "리서치 결과가 없습니다"}), 404
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return jsonify({"success": True, "data": json.load(f)})
    except Exception as e:
        logger.error(f"리서치 조회 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@stock_bp.route('/research/<task_id>', methods=['POST'])
def rerun_research(task_id: str):
    """기존 분석에 대해 리서치를 (다시) 실행 — 비동기. body: {force}. 반환 {task_id} → /api/stock/task/<id>/stream 으로 추적"""
    import os
    import json
    try:
        if not _safe_task_id(task_id):
            return jsonify({"success": False, "error": "잘못된 작업 ID 입니다"}), 400
        data = request.get_json(silent=True) or {}
        adir = os.path.join(Config.UPLOAD_FOLDER, 'stock_analyses', task_id)
        apath = os.path.join(adir, 'analysis.json')
        if not os.path.exists(apath):
            return jsonify({"success": False, "error": f"분석 결과를 찾을 수 없습니다: {task_id}"}), 404
        rpath = os.path.join(adir, 'research.json')
        if not data.get('force') and os.path.exists(rpath):
            with open(rpath, 'r', encoding='utf-8') as f:
                return jsonify({"success": True, "data": json.load(f), "cached": True})
        with open(apath, 'r', encoding='utf-8') as f:
            analysis = json.load(f)
        ticker = analysis.get('ticker', '')
        company_name = analysis.get('company_name', '') or ticker

        def job(progress):
            info = _run_research(task_id, ticker, company_name, progress, (3, 97))
            progress(99, f"리서치 저장 완료 — 발견 {info['findings']}건 · 출처 {info['sources']}곳",
                     {'stage': 'research', 'phase': 'done'})

        rt = _start_task('research', task_id, job)
        return jsonify({"success": True, "data": {"task_id": rt}, "async": True})
    except Exception as e:
        logger.error(f"리서치 재실행 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============== 시뮬레이션 인터페이스 ==============

@stock_bp.route('/simulate', methods=['POST'])
def start_simulation():
    """
    주식 예측 시뮬레이션 시작 (비동기 작업)

    종목 분석 결과(task_id)를 기반으로 에이전트 시뮬레이션을 실행합니다.

    요청 (JSON):
        {
            "task_id": "task_xxxx",       // 필수, analyze 엔드포인트에서 반환한 task_id
            "num_agents": 50,              // 선택, 에이전트 수 (기본값: 50)
            "simulation_rounds": 10        // 선택, 시뮬레이션 라운드 수 (기본값: 10)
        }

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "stock_sim_xxxx",
                "task_id": "task_xxxx",
                "status": "pending",
                "message": "시뮬레이션 작업이 시작되었습니다"
            }
        }
    """
    try:
        data = request.get_json(silent=True) or {}

        # 필수 파라미터 검증
        task_id = data.get('task_id')
        if not task_id:
            return jsonify({
                "success": False,
                "error": "task_id(분석 작업 ID)를 입력해 주세요"
            }), 400

        num_agents = data.get('num_agents', 50)
        simulation_rounds = data.get('simulation_rounds', 10)

        # 분석 작업 결과 확인
        task_manager = TaskManager()
        analysis_task = task_manager.get_task(task_id)

        if not analysis_task:
            return jsonify({
                "success": False,
                "error": f"분석 작업을 찾을 수 없습니다: {task_id}"
            }), 404

        if analysis_task.status.value != 'completed':
            return jsonify({
                "success": False,
                "error": f"분석이 아직 완료되지 않았습니다. 현재 상태: {analysis_task.status.value}"
            }), 400

        # 분석 결과 로드
        import os
        import json

        analysis_dir = os.path.join(Config.UPLOAD_FOLDER, 'stock_analyses', task_id)
        analysis_file = os.path.join(analysis_dir, 'analysis.json')

        if not os.path.exists(analysis_file):
            return jsonify({
                "success": False,
                "error": "분석 결과 파일을 찾을 수 없습니다"
            }), 404

        with open(analysis_file, 'r', encoding='utf-8') as f:
            analysis_data = json.load(f)

        ticker = analysis_data.get('ticker', '')
        company_name = analysis_data.get('company_name', '')

        # 시뮬레이션 ID 생성
        simulation_id = f"stock_sim_{uuid.uuid4().hex[:12]}"

        # 시뮬레이션 메타데이터 저장
        sim_dir = os.path.join(Config.UPLOAD_FOLDER, 'stock_simulations', simulation_id)
        os.makedirs(sim_dir, exist_ok=True)

        sim_meta = {
            "simulation_id": simulation_id,
            "task_id": task_id,
            "ticker": ticker,
            "company_name": company_name,
            "num_agents": num_agents,
            "simulation_rounds": simulation_rounds,
            "status": "pending",
            "created_at": __import__('datetime').datetime.now().isoformat()
        }

        with open(os.path.join(sim_dir, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump(sim_meta, f, ensure_ascii=False, indent=2)

        # 시뮬레이션 작업 생성
        sim_task_id = task_manager.create_task(
            task_type="stock_simulate",
            metadata={
                "simulation_id": simulation_id,
                "ticker": ticker,
                "company_name": company_name,
                "task_id": task_id
            }
        )

        # 백그라운드 시뮬레이션 실행
        def run_simulation():
            try:
                task_manager.update_task(
                    sim_task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="에이전트 시뮬레이션 초기화 중..."
                )

                financial_data = analysis_data.get('financial_data', {})
                ohlcv = financial_data.get('ohlcv', [])

                # 2단계 리서치(research.json)를 근거로 사용 — 없으면 가격 모멘텀만
                research = None
                try:
                    rpath = os.path.join(analysis_dir, 'research.json')
                    if os.path.exists(rpath):
                        with open(rpath, 'r', encoding='utf-8') as f:
                            research = json.load(f)
                except Exception as re_err:
                    logger.warning(f"[{simulation_id}] research.json 로드 실패(무시): {re_err}")

                evidence = _simulation_evidence(ohlcv, financial_data.get('fundamental') or [], research)
                task_manager.update_task(
                    sim_task_id, progress=5,
                    message="근거 반영 — " + _evidence_text(evidence)
                )

                def sim_progress(done_n, total_n):
                    task_manager.update_task(
                        sim_task_id,
                        progress=int(done_n / max(1, total_n) * 80),
                        message=f"에이전트 시뮬레이션 진행 중 — {done_n}/{total_n}명"
                    )

                # 에이전트별 투자 결정 시뮬레이션 (simulation_id 시드 → 재현 가능)
                agents_decisions, evidence_used = _simulate_agents(
                    simulation_id, int(num_agents), evidence, on_progress=sim_progress
                )

                task_manager.update_task(
                    sim_task_id,
                    progress=90,
                    message="시뮬레이션 결과 집계 중..."
                )

                # 집계 결과 계산
                buy_count = sum(1 for d in agents_decisions if d['decision'] == 'buy')
                sell_count = sum(1 for d in agents_decisions if d['decision'] == 'sell')
                hold_count = sum(1 for d in agents_decisions if d['decision'] == 'hold')

                simulation_result = {
                    "simulation_id": simulation_id,
                    "task_id": task_id,
                    "ticker": ticker,
                    "company_name": company_name,
                    "num_agents": num_agents,
                    "simulation_rounds": simulation_rounds,
                    "agents_decisions": agents_decisions,
                    "summary": {
                        "buy_count": buy_count,
                        "sell_count": sell_count,
                        "hold_count": hold_count,
                        "buy_pct": round(buy_count / num_agents * 100, 1),
                        "sell_pct": round(sell_count / num_agents * 100, 1),
                        "hold_pct": round(hold_count / num_agents * 100, 1),
                        "evidence_used": evidence_used
                    },
                    "financial_data_summary": {
                        "ohlcv_count": len(ohlcv),
                        "ticker": ticker
                    },
                    "status": "completed",
                    "completed_at": __import__('datetime').datetime.now().isoformat()
                }

                # 몬테카를로 주가 경로 (과거 일봉 블록 부트스트랩 5,000경로 · 20거래일) — 예측 원장 'mc' 엔진
                task_manager.update_task(sim_task_id, progress=93, message="몬테카를로 주가 경로 5,000개 생성 중...")
                try:
                    from ..services import monte_carlo as _mcm
                    _mcm.attach_to_result(simulation_result, ohlcv, simulation_id)
                except Exception as mc_err:
                    logger.warning(f"[{simulation_id}] 몬테카를로 생략: {mc_err}")

                # 결과 저장
                with open(os.path.join(sim_dir, 'result.json'), 'w', encoding='utf-8') as f:
                    json.dump(simulation_result, f, ensure_ascii=False, indent=2)

                # 메타데이터 업데이트
                sim_meta['status'] = 'completed'
                with open(os.path.join(sim_dir, 'meta.json'), 'w', encoding='utf-8') as f:
                    json.dump(sim_meta, f, ensure_ascii=False, indent=2)

                logger.info(f"[{simulation_id}] 시뮬레이션 완료: 매수 {buy_count}, 매도 {sell_count}, 보합 {hold_count}")

                task_manager.complete_task(
                    sim_task_id,
                    result={
                        "simulation_id": simulation_id,
                        "ticker": ticker,
                        "company_name": company_name,
                        "summary": simulation_result["summary"]
                    }
                )

            except Exception as e:
                logger.error(f"[{simulation_id}] 시뮬레이션 실패: {str(e)}")
                logger.error(traceback.format_exc())
                task_manager.fail_task(sim_task_id, str(e))

                # 메타데이터 실패 상태 업데이트
                try:
                    sim_meta['status'] = 'failed'
                    sim_meta['error'] = str(e)
                    with open(os.path.join(sim_dir, 'meta.json'), 'w', encoding='utf-8') as f:
                        json.dump(sim_meta, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

        # 백그라운드 스레드 시작
        from ..services import ops_metrics as _ops
        thread = threading.Thread(target=_ops.staged('simulation', ticker, run_simulation, sim_task_id), daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "sim_task_id": sim_task_id,
                "task_id": task_id,
                "ticker": ticker,
                "company_name": company_name,
                "status": "pending",
                "message": f"{company_name}({ticker}) 시뮬레이션이 시작되었습니다. "
                           f"/api/stock/simulate/{simulation_id}/status 로 진행 상태를 확인하세요."
            }
        })

    except Exception as e:
        logger.error(f"시뮬레이션 시작 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@stock_bp.route('/simulate/<simulation_id>/status', methods=['GET'])
def get_simulation_status(simulation_id: str):
    """
    시뮬레이션 진행 상태 확인

    반환:
        {
            "success": true,
            "data": {
                "simulation_id": "stock_sim_xxxx",
                "status": "pending|running|completed|failed",
                "ticker": "005930",
                "company_name": "삼성전자",
                "summary": {
                    "buy_pct": 45.0,
                    "sell_pct": 30.0,
                    "hold_pct": 25.0
                }
            }
        }
    """
    try:
        import os
        import json

        sim_dir = os.path.join(Config.UPLOAD_FOLDER, 'stock_simulations', simulation_id)
        meta_file = os.path.join(sim_dir, 'meta.json')

        if not os.path.exists(meta_file):
            return jsonify({
                "success": False,
                "error": f"시뮬레이션을 찾을 수 없습니다: {simulation_id}"
            }), 404

        with open(meta_file, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        # 완료된 경우 결과 포함
        result_file = os.path.join(sim_dir, 'result.json')
        if os.path.exists(result_file):
            with open(result_file, 'r', encoding='utf-8') as f:
                result = json.load(f)
            meta['summary'] = result.get('summary', {})
            meta['num_agents'] = result.get('num_agents', 0)
            if isinstance(result.get('monte_carlo'), dict):
                meta['monte_carlo'] = {k: v for k, v in result['monte_carlo'].items() if k != 'fan'}

        return jsonify({
            "success": True,
            "data": meta
        })

    except Exception as e:
        logger.error(f"시뮬레이션 상태 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============== 예측 보고서 인터페이스 ==============

@stock_bp.route('/report/<simulation_id>', methods=['POST'])
def generate_report(simulation_id: str):
    """
    주식 예측 보고서 생성 (비동기 작업)

    시뮬레이션 결과를 분석하여 투자 예측 보고서를 생성합니다.

    요청 (JSON):
        {
            "force_regenerate": false   // 선택, 기존 보고서 강제 재생성 여부 (기본값: false)
        }

    반환:
        {
            "success": true,
            "data": {
                "report_id": "stock_report_xxxx",
                "simulation_id": "stock_sim_xxxx",
                "task_id": "task_xxxx",
                "status": "generating",
                "message": "보고서 생성 작업이 시작되었습니다"
            }
        }
    """
    try:
        import os
        import json

        data = request.get_json(silent=True) or {}
        force_regenerate = data.get('force_regenerate', False)

        # 시뮬레이션 결과 확인
        sim_dir = os.path.join(Config.UPLOAD_FOLDER, 'stock_simulations', simulation_id)
        result_file = os.path.join(sim_dir, 'result.json')

        if not os.path.exists(result_file):
            return jsonify({
                "success": False,
                "error": f"시뮬레이션 결과를 찾을 수 없습니다. 시뮬레이션이 완료되었는지 확인해 주세요: {simulation_id}"
            }), 404

        with open(result_file, 'r', encoding='utf-8') as f:
            simulation_data = json.load(f)

        ticker = simulation_data.get('ticker', '')
        company_name = simulation_data.get('company_name', '')
        task_id = simulation_data.get('task_id', '')

        # 기존 보고서 확인
        if not force_regenerate:
            existing_report = StockReportManager.get_report_by_simulation(simulation_id)
            if existing_report and existing_report.get('status') == StockReportStatus.COMPLETED.value:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "report_id": existing_report['report_id'],
                        "status": "completed",
                        "message": "이미 생성된 보고서가 있습니다",
                        "already_generated": True
                    }
                })

        # 분석 데이터 로드
        analysis_dir = os.path.join(Config.UPLOAD_FOLDER, 'stock_analyses', task_id)
        analysis_file = os.path.join(analysis_dir, 'analysis.json')
        financial_data = {}

        if os.path.exists(analysis_file):
            with open(analysis_file, 'r', encoding='utf-8') as f:
                analysis_data = json.load(f)
            financial_data = analysis_data.get('financial_data', {})

        # 보고서 ID 사전 생성 (즉시 반환을 위해)
        report_id = f"stock_report_{uuid.uuid4().hex[:12]}"

        # 비동기 작업 생성
        task_manager = TaskManager()
        report_task_id = task_manager.create_task(
            task_type="stock_report_generate",
            metadata={
                "report_id": report_id,
                "simulation_id": simulation_id,
                "ticker": ticker,
                "company_name": company_name
            }
        )

        # 백그라운드 보고서 생성
        def run_report_generation():
            try:
                task_manager.update_task(
                    report_task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="StockReportAgent 초기화 중..."
                )

                # 진행률 콜백 (+ Grok 스트리밍 수신량을 실시간 표시)
                from ..utils.llm_client import set_stream_sink
                live = {'msg': '', 'progress': 0}

                def progress_callback(stage: str, progress: int, message: str):
                    live['msg'], live['progress'] = message, progress
                    task_manager.update_task(
                        report_task_id,
                        progress=progress,
                        message=message,
                        progress_detail={'stage': stage}
                    )

                def sink(n_content, n_reason, sec):
                    extra = f"추론 {n_reason:,}자 · " if n_reason else ''
                    task_manager.update_task(
                        report_task_id,
                        message=f"{live['msg']} — Grok {extra}본문 {n_content:,}자 수신 ({sec:.0f}초)"
                    )

                set_stream_sink(sink)

                # StockReportAgent 실행
                agent = StockReportAgent()
                report = agent.generate_report(
                    simulation_id=simulation_id,
                    ticker=ticker,
                    company_name=company_name,
                    simulation_data=simulation_data,
                    financial_data=financial_data,
                    report_id=report_id,
                    progress_callback=progress_callback
                )

                # 보고서 저장
                StockReportManager.save_report(report)

                if report.get('status') == StockReportStatus.COMPLETED.value:
                    task_manager.complete_task(
                        report_task_id,
                        result={
                            "report_id": report_id,
                            "simulation_id": simulation_id,
                            "ticker": ticker,
                            "company_name": company_name,
                            "status": "completed"
                        }
                    )
                    logger.info(f"[{report_id}] 보고서 생성 완료: {ticker} ({company_name})")
                    _prefetch_next_steps(report_id)
                else:
                    error_msg = report.get('error', '보고서 생성 실패')
                    task_manager.fail_task(report_task_id, error_msg)

            except Exception as e:
                logger.error(f"[{report_id}] 보고서 생성 실패: {str(e)}")
                logger.error(traceback.format_exc())
                task_manager.fail_task(report_task_id, str(e))

        # 백그라운드 스레드 시작
        from ..services import ops_metrics as _ops
        thread = threading.Thread(target=_ops.staged('report', ticker, run_report_generation, report_task_id), daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "data": {
                "report_id": report_id,
                "simulation_id": simulation_id,
                "task_id": report_task_id,
                "ticker": ticker,
                "company_name": company_name,
                "status": "generating",
                "message": f"보고서 생성이 시작되었습니다. "
                           f"/api/stock/report/{report_id} 로 보고서를 조회하세요.",
                "already_generated": False
            }
        })

    except Exception as e:
        logger.error(f"보고서 생성 시작 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@stock_bp.route('/report/<report_id>', methods=['GET'])
def get_report(report_id: str):
    """
    주식 예측 보고서 조회

    반환:
        {
            "success": true,
            "data": {
                "report_id": "stock_report_xxxx",
                "simulation_id": "stock_sim_xxxx",
                "ticker": "005930",
                "company_name": "삼성전자",
                "status": "completed",
                "sections": {
                    "overview": "...",
                    "simulation_summary": "...",
                    "sentiment_analysis": "...",
                    "technical_analysis": "...",
                    "consensus_forecast": "...",
                    "risk_factors": "...",
                    "investment_opinion": "..."
                },
                "markdown_content": "...",
                "created_at": "...",
                "disclaimer": "본 보고서는 AI 시뮬레이션 결과이며 투자 조언이 아닙니다"
            }
        }
    """
    try:
        report = StockReportManager.get_report(report_id)

        if not report:
            return jsonify({
                "success": False,
                "error": f"보고서를 찾을 수 없습니다: {report_id}"
            }), 404

        return jsonify({
            "success": True,
            "data": report
        })

    except Exception as e:
        logger.error(f"보고서 조회 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 투자 판단 인터페이스 (예측 → 판단) ==============

@stock_bp.route('/judge/<report_id>', methods=['POST'])
def judge_report(report_id: str):
    """
    예측 보고서에 대한 AI 최종 판단 (xAI Grok → Vercel AI Gateway → 규칙 기반)

    요청 (JSON, 선택):
        {
            "force": false,                                   // 캐시 무시하고 재판단
            "prediction": {"direction": "상승", "probability": 45.0}
        }
    """
    try:
        data = request.get_json(silent=True) or {}
        if not data.get('force'):
            cached = StockJudge.load_cached(report_id)
            if cached:
                return jsonify({"success": True, "data": cached, "cached": True})

        result = StockJudge().judge(report_id, prediction=data.get('prediction'))
        return jsonify({"success": True, "data": result, "cached": False})

    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        logger.error(f"판단 생성 실패: {str(e)}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@stock_bp.route('/judge/<report_id>', methods=['GET'])
def get_judgment(report_id: str):
    cached = StockJudge.load_cached(report_id)
    if not cached:
        return jsonify({"success": False, "error": "판단 결과가 없습니다"}), 404
    return jsonify({"success": True, "data": cached})


@stock_bp.route('/judge-health', methods=['GET'])
def judge_health():
    """판단 모델 연결 상태 (키 값은 노출하지 않음)"""
    try:
        return jsonify({"success": True, "data": StockJudge().diagnostics()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============== 4단계: 지식그래프 (Foundry 온톨로지) ==============

@stock_bp.route('/kg/<report_id>', methods=['GET'])
def get_knowledge_graph(report_id: str):
    kg = StockKnowledgeGraph.load(report_id)
    if not kg:
        return jsonify({"success": False, "error": "지식그래프가 아직 없습니다"}), 404
    return jsonify({"success": True, "data": kg})


_RUNNING: dict = {}


def _start_task(kind: str, report_id: str, fn):
    """fn(progress) 를 백그라운드로 실행하고 task_id 반환. 같은 작업이 진행 중이면 그 task_id 재사용."""
    key = f"{kind}:{report_id}"
    tm = TaskManager()
    running = _RUNNING.get(key)
    if running:
        t = tm.get_task(running)
        if t and t.status.value in ('pending', 'processing'):
            return running
    task_id = tm.create_task(task_type=kind, metadata={'report_id': report_id})
    _RUNNING[key] = task_id

    def progress(pct, msg, detail=None):
        tm.update_task(task_id, status=TaskStatus.PROCESSING, progress=int(pct), message=msg,
                       progress_detail=detail or {})

    def run():
        try:
            progress(1, '작업을 시작합니다')
            from ..services import ops_metrics as _ops
            with _ops.stage(kind, report_id):
                fn(progress)
            tm.complete_task(task_id, result={'report_id': report_id})
            if kind in ('kg', 'insight', 'decision'):
                _ontology_ingest(report_id)
        except Exception as e:
            logger.error(f"[{key}] 작업 실패: {e}\n{traceback.format_exc()}")
            tm.fail_task(task_id, str(e))
        finally:
            _RUNNING.pop(key, None)

    threading.Thread(target=run, daemon=True).start()
    return task_id


def _ontology_ingest(report_id: str):
    """지식그래프·판단이 끝나면 누적 온톨로지에 반영 (실패해도 본 작업에는 영향 없음)"""
    def job():
        try:
            from ..services import ontology_store
            ontology_store.ingest_report(report_id)
        except Exception as e:
            logger.warning(f"[{report_id}] 온톨로지 누적 실패: {e}")
    threading.Thread(target=job, daemon=True, name='ontology-ingest').start()


def _prefetch_next_steps(report_id: str):
    """보고서가 끝나면 4단계 지식그래프 → 5단계 투자위원회 토론을 백그라운드로 미리 진행.
    사용자가 3단계 보고서를 읽는 동안 준비되므로 4·5단계가 바로 열립니다. (PREFETCH_AFTER_REPORT=off 로 끔)
    화면에서 같은 작업을 요청하면 진행 중인 작업을 그대로 이어서 보여 줍니다(중복 실행 없음)."""
    import os
    import time as _t
    if (os.environ.get('PREFETCH_AFTER_REPORT') or 'on').strip().lower() == 'off':
        return

    def chain():
        try:
            from ..services.stock_committee import InvestmentCommittee
            kg = StockKnowledgeGraph.load(report_id)
            if not kg or kg.get('enriching'):
                tid = _start_task('kg', report_id, lambda progress: StockKnowledgeGraph().generate(report_id, progress))
                tm, t0 = TaskManager(), _t.time()
                while _t.time() - t0 < 300:
                    t = tm.get_task(tid)
                    if not t or t.status.value not in ('pending', 'processing'):
                        break
                    _t.sleep(2)
                kg = StockKnowledgeGraph.load(report_id)
                if not kg or kg.get('enriching'):
                    logger.warning(f"[{report_id}] 미리 준비: 지식그래프가 끝나지 않아 토론은 건너뜀")
                    return
            if InvestmentCommittee.load_cached(report_id):
                return
            _start_task('committee', report_id, lambda progress: InvestmentCommittee().run(report_id, progress, 2))
            logger.info(f"[{report_id}] 미리 준비: 지식그래프 완료 → 투자위원회 토론 시작")
        except Exception as e:
            logger.warning(f"[{report_id}] 다음 단계 미리 준비 실패(무시): {e}")

    threading.Thread(target=chain, daemon=True, name=f'prefetch-{report_id[-6:]}').start()


@stock_bp.route('/task/<task_id>', methods=['GET'])
def get_task_status(task_id: str):
    """모든 백그라운드 작업(분석·시뮬레이션·보고서·지식그래프·판단)의 진행 상황"""
    t = TaskManager().get_task(task_id)
    if not t:
        return jsonify({"success": False, "error": "작업을 찾을 수 없습니다"}), 404
    return jsonify({"success": True, "data": t.to_dict()})


@stock_bp.route('/task/<task_id>/stream', methods=['GET'])
def stream_task_status(task_id: str):
    """SSE — 작업 상태가 바뀔 때마다 즉시 푸시 (폴링 대신). 완료·실패 시 스트림 종료."""
    import json as _json
    import time as _time
    from flask import Response, stream_with_context

    def gen():
        yield 'retry: 3000\n\n'
        last, idle, t0 = None, 0, _time.time()
        while True:
            t = TaskManager().get_task(task_id)
            if not t:
                yield 'event: missing\ndata: {"error": "작업을 찾을 수 없습니다"}\n\n'
                return
            d = t.to_dict()
            sig = (d['status'], d['progress'], d['message'],
                   _json.dumps(d.get('progress_detail'), ensure_ascii=False, sort_keys=True, default=str))
            if sig != last:
                last, idle = sig, 0
                yield 'data: ' + _json.dumps(d, ensure_ascii=False, default=str) + '\n\n'
            else:
                idle += 1
                if idle % 30 == 0:          # 약 12초마다 연결 유지 신호
                    yield ': ping\n\n'
            if d['status'] in ('completed', 'failed') or _time.time() - t0 > 3 * 3600:
                return
            _time.sleep(0.4)

    return Response(stream_with_context(gen()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'})


@stock_bp.route('/kg/<report_id>', methods=['POST'])
def generate_knowledge_graph(report_id: str):
    """기본 S-P-O 그래프를 즉시 만들고, AI 확장(트리플·CQ·ADR·인사이트)은 병렬 백그라운드 작업으로 진행"""
    try:
        data = request.get_json(silent=True) or {}
        if not data.get('force'):
            kg = StockKnowledgeGraph.load(report_id)
            if kg and not kg.get('enriching'):
                return jsonify({"success": True, "data": kg, "cached": True})
        task_id = _start_task('kg', report_id, lambda progress: StockKnowledgeGraph().generate(report_id, progress))
        return jsonify({"success": True, "data": {"task_id": task_id}, "async": True})
    except Exception as e:
        logger.error(f"지식그래프 생성 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@stock_bp.route('/kg/<report_id>', methods=['PUT'])
def save_knowledge_graph(report_id: str):
    """사용자가 편집한 노드·엣지·CQ·ADR 저장 (RDF/OWL2 도 다시 생성)"""
    try:
        payload = request.get_json(silent=True) or {}
        kg = StockKnowledgeGraph().save_edit(report_id, payload)
        return jsonify({"success": True, "data": kg})
    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except Exception as e:
        logger.error(f"지식그래프 저장 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@stock_bp.route('/kg/<report_id>/insight', methods=['POST'])
def regenerate_kg_insight(report_id: str):
    try:
        if not StockKnowledgeGraph.load(report_id):
            return jsonify({"success": False, "error": "지식그래프가 없습니다"}), 404
        task_id = _start_task('insight', report_id, lambda progress: StockKnowledgeGraph().regenerate_insight(report_id, progress))
        return jsonify({"success": True, "data": {"task_id": task_id}, "async": True})
    except Exception as e:
        logger.error(f"인사이트 재생성 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@stock_bp.route('/kg/<report_id>/ttl', methods=['GET'])
def download_kg_ttl(report_id: str):
    from flask import Response
    kg = StockKnowledgeGraph.load(report_id)
    if not kg or not kg.get('ttl'):
        return jsonify({"success": False, "error": "RDF/OWL2 파일이 없습니다"}), 404
    return Response(kg['ttl'], mimetype='text/turtle; charset=utf-8',
                    headers={"Content-Disposition": f"attachment; filename={kg.get('ticker') or 'stock'}_{report_id}_ontology.ttl"})


@stock_bp.route('/tts', methods=['POST'])
def text_to_speech():
    """종합 의견 음성 합성 (xAI TTS, mp3). body: {text, voice, report_id?}"""
    from flask import Response
    try:
        data = request.get_json(silent=True) or {}
        cache_dir = None
        rid = data.get('report_id')
        if rid:
            import os
            d = os.path.join(Config.UPLOAD_FOLDER, 'stock_reports', rid)
            cache_dir = d if os.path.isdir(d) else None
        audio = synthesize_speech(data.get('text', ''), data.get('voice', 'eve'), cache_dir=cache_dir)
        return Response(audio, mimetype='audio/mpeg',
                        headers={"Content-Disposition": "attachment; filename=weaidstock_insight.mp3"})
    except Exception as e:
        logger.error(f"음성 합성 실패: {e}")
        return jsonify({"success": False, "error": str(e), "voices": TTS_VOICES}), 502


# ============== 5단계: 판단 (Grok 계획 + JEV 확률 채점) ==============

@stock_bp.route('/decision/<report_id>', methods=['GET'])
def get_decision(report_id: str):
    d = StockDecision.load_cached(report_id)
    if not d:
        return jsonify({"success": False, "error": "판단 결과가 없습니다"}), 404
    return jsonify({"success": True, "data": d})


@stock_bp.route('/decision/<report_id>', methods=['POST'])
def make_decision(report_id: str):
    try:
        data = request.get_json(silent=True) or {}
        if not data.get('force'):
            d = StockDecision.load_cached(report_id)
            if d:
                return jsonify({"success": True, "data": d, "cached": True})
        task_id = _start_task('decision', report_id, lambda progress: StockDecision().decide(report_id, progress))
        return jsonify({"success": True, "data": {"task_id": task_id}, "async": True})
    except Exception as e:
        logger.error(f"판단 생성 실패: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============== 종목 검색 인터페이스 ==============

@stock_bp.route('/search', methods=['GET'])
def search_stocks():
    """
    한국 주식 종목 검색 (pykrx 활용)

    Query 파라미터:
        query: 검색어 (종목명 또는 종목 코드)
        market: 시장 구분 (KOSPI|KOSDAQ|ALL, 기본값: ALL)
        limit: 반환 최대 건수 (기본값: 20)

    반환:
        {
            "success": true,
            "data": [
                {
                    "ticker": "005930",
                    "name": "삼성전자",
                    "market": "KOSPI"
                },
                ...
            ],
            "count": 5
        }
    """
    try:
        query = request.args.get('query', '').strip()
        market = request.args.get('market', 'ALL').upper()
        limit = request.args.get('limit', 20, type=int)

        if not query:
            return jsonify({
                "success": False,
                "error": "검색어(query)를 입력해 주세요"
            }), 400

        if market not in ('KOSPI', 'KOSDAQ', 'ALL') or not 1 <= limit <= 100:
            return jsonify({"success": False, "error": "시장 또는 검색 개수 설정이 올바르지 않습니다."}), 400

        from ..services.stock_catalog import search_catalog, CatalogUnavailable
        try:
            results = search_catalog(query, market, limit)
        except CatalogUnavailable as exc:
            logger.warning("종목 목록 조회 실패: %s", exc.__cause__ or exc)
            return jsonify({"success": False, "error": str(exc)}), 503

        return jsonify({
            "success": True,
            "data": results[:limit],
            "count": len(results[:limit]),
            "query": query,
            "market": market
        })

    except Exception as e:
        logger.error(f"종목 검색 실패: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 내부 유틸리티 함수 ==============

# ── 근거 기반 에이전트 시뮬레이션 ──
# 매수 확률 +s / 매도 확률 −s 로 이동시키는 s 를 다음 항목의 합으로 정합니다 (모두 상한이 있어 |s| ≤ 0.25).
#   모멘텀   : 20거래일 수익률 1%p → 0.01 (기존 규칙과 동일), ±0.15 상한
#   뉴스 감성 : research.sentiment.news.score(−1~1) × 0.12
#   커뮤니티 : 공격형·중립형은 추종(+0.06×점수), 보수형은 |점수| ≥ 0.5 인 극단에서만 역발상(−0.06×점수)
#   밸류에이션: PER < 8 이면 +0.04(저평가), PER > 40 이면 −0.04(고평가), 그 외 0
#   성향별 가중치(모멘텀, 뉴스, 커뮤니티, 밸류): 공격형 (1.2, 1.1, 1.0, 0.5) · 중립형 (1, 1, 1, 1) · 보수형 (0.8, 0.8, 1.0, 1.5)
# 이후 성향별 기본 편향(공격형 매수 +0.15 등, 기존 규칙)과 정규화를 거쳐 simulation_id 로 시드된 난수로 결정합니다.
_AGENT_TYPES = ['conservative', 'moderate', 'aggressive']
_AGENT_TYPE_KO = {'conservative': '보수형', 'moderate': '중립형', 'aggressive': '공격형'}
_DECISION_KO = {'buy': '매수', 'sell': '매도', 'hold': '보합'}
_TYPE_WEIGHTS = {'aggressive': (1.2, 1.1, 1.0, 0.5), 'moderate': (1.0, 1.0, 1.0, 1.0), 'conservative': (0.8, 0.8, 1.0, 1.5)}


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _num_or_none(v):
    try:
        f = float(str(v).replace(',', ''))
        return f if f == f else None      # NaN 제외
    except (TypeError, ValueError):
        return None


def _simulation_evidence(ohlcv: list, fundamental: list, research) -> dict:
    """가격·재무·리서치에서 시뮬레이션 근거를 뽑습니다."""
    closes = []
    for row in (ohlcv or [])[-20:]:
        if isinstance(row, dict):
            c = _num_or_none(row.get('종가', row.get('close')))
            if c:
                closes.append(c)
    momentum = (closes[-1] - closes[0]) / closes[0] * 100 if len(closes) >= 2 and closes[0] else 0.0
    per = None
    for row in reversed(fundamental or []):
        if isinstance(row, dict) and _num_or_none(row.get('PER')) is not None:
            per = _num_or_none(row.get('PER'))
            break
    news_score = community_score = None
    findings = []
    if isinstance(research, dict):
        sent = research.get('sentiment') or {}
        ns, cs = sent.get('news') or {}, sent.get('community') or {}
        if ns.get('n'):
            news_score = _clip(_num_or_none(ns.get('score')) or 0.0, -1, 1)
        if cs.get('n'):
            community_score = _clip(_num_or_none(cs.get('score')) or 0.0, -1, 1)
        findings = [f for f in (research.get('findings') or []) if isinstance(f, dict) and f.get('id') and f.get('claim')]
    return {'momentum_pct': round(momentum, 2), 'per': per, 'news_score': news_score,
            'community_score': community_score, 'findings': findings, 'has_research': isinstance(research, dict)}


def _evidence_text(ev: dict) -> str:
    bits = [f"20일 모멘텀 {ev['momentum_pct']:+.1f}%"]
    if ev.get('has_research'):
        bits.insert(0, f"리서치 발견 {len(ev['findings'])}건")
        bits.append('뉴스 감성 ' + (f"{ev['news_score']:+.2f}" if ev['news_score'] is not None else '없음'))
        bits.append('커뮤니티 ' + (f"{ev['community_score']:+.2f}" if ev['community_score'] is not None else '없음'))
    else:
        bits.append('리서치 없음(가격 모멘텀만 반영)')
    if ev.get('per'):
        bits.append(f"PER {ev['per']:.1f}배")
    return ' · '.join(bits)


def _agent_shift(agent_type: str, ev: dict) -> float:
    wm, wn, wc, wv = _TYPE_WEIGHTS.get(agent_type, _TYPE_WEIGHTS['moderate'])
    mom = _clip(ev['momentum_pct'] * 0.01, -0.15, 0.15)
    news = 0.12 * (ev['news_score'] or 0.0)
    com, cs = 0.0, ev.get('community_score')
    if cs is not None:
        if agent_type == 'conservative':
            com = -0.06 * cs if abs(cs) >= 0.5 else 0.0
        else:
            com = 0.06 * cs
    per = ev.get('per')
    val = 0.0
    if per and per > 0:
        val = 0.04 if per < 8 else (-0.04 if per > 40 else 0.0)
    return _clip(wm * mom + wn * news + wc * com + wv * val, -0.25, 0.25)


def _short(text: str, n: int = 48) -> str:
    t = ' '.join(str(text or '').split())
    return t if len(t) <= n else t[: n - 1].rstrip() + '…'


def _simulate_agents(simulation_id: str, num_agents: int, ev: dict, on_progress=None):
    """근거 기반 에이전트 결정. 반환 (agents_decisions, evidence_used). 같은 simulation_id → 같은 결과."""
    import random
    rng = random.Random(str(simulation_id))
    shifts = {t: _agent_shift(t, ev) for t in _AGENT_TYPES}
    fnd = ev.get('findings') or []

    def lab(f):
        return (f.get('sentiment') or {}).get('label') or 'neutral'
    by_imp = lambda xs: sorted(xs, key=lambda f: -(f.get('importance') or 0))  # noqa: E731
    pools = {'positive': by_imp([f for f in fnd if lab(f) == 'positive'])[:5],
             'negative': by_imp([f for f in fnd if lab(f) == 'negative'])[:5],
             'neutral': by_imp([f for f in fnd if lab(f) == 'neutral'])[:5]}
    decisions = []
    step = max(1, num_agents // 10)
    for i in range(num_agents):
        agent_type = rng.choice(_AGENT_TYPES)
        s = shifts[agent_type]
        base_buy, base_sell = 0.33 + s, 0.33 - s
        if agent_type == 'aggressive':
            buy_p, sell_p = min(0.7, base_buy + 0.15), max(0.1, base_sell - 0.05)
        elif agent_type == 'conservative':
            buy_p, sell_p = max(0.1, base_buy - 0.1), min(0.5, base_sell + 0.1)
        else:
            buy_p, sell_p = max(0.15, min(0.65, base_buy)), max(0.15, min(0.5, base_sell))
        hold_p = max(0.05, 1.0 - buy_p - sell_p)
        tot = buy_p + sell_p + hold_p
        buy_p, sell_p = buy_p / tot, sell_p / tot
        r = rng.random()
        decision = 'buy' if r < buy_p else ('sell' if r < buy_p + sell_p else 'hold')

        # 근거 인용 (1~2개)
        cited = []
        k = 2 if rng.random() < 0.45 else 1
        if decision == 'buy':
            pool = pools['positive'] or pools['neutral']
            cited = rng.sample(pool, min(k, len(pool))) if pool else []
        elif decision == 'sell':
            pool = pools['negative'] or pools['neutral']
            cited = rng.sample(pool, min(k, len(pool))) if pool else []
        else:
            if pools['positive'] and pools['negative']:
                cited = [rng.choice(pools['positive']), rng.choice(pools['negative'])]
            elif pools['neutral'] or pools['positive'] or pools['negative']:
                pool = pools['neutral'] or pools['positive'] or pools['negative']
                cited = [rng.choice(pool)]
        dec_ko = _DECISION_KO[decision]
        mom_txt = f"20일 {ev['momentum_pct']:+.1f}%"
        if cited:
            joined = ' · '.join(f"[{f['id']}] {_short(f['claim'])}" for f in cited)
            prefix = '엇갈린 신호: ' if (decision == 'hold' and len(cited) == 2) else ''
            reasoning = f"{prefix}{joined} → {dec_ko} ({_AGENT_TYPE_KO[agent_type]} · {mom_txt})"
        else:
            news_txt = f" · 뉴스 심리 {ev['news_score']:+.2f}" if ev.get('news_score') is not None else ''
            reasoning = f"{_AGENT_TYPE_KO[agent_type]} 성향: {mom_txt} 추세{news_txt} → {dec_ko}"

        aligned = (decision == 'buy' and s > 0.03) or (decision == 'sell' and s < -0.03)
        conf = min(0.95, rng.uniform(0.5, 0.9) + (0.05 if aligned else 0.0))
        decisions.append({
            "agent_id": f"agent_{i + 1:04d}",
            "agent_type": agent_type,
            "decision": decision,
            "confidence": round(conf, 2),
            "reasoning": reasoning,
            "evidence": [f['id'] for f in cited],
        })
        if on_progress and (i + 1) % step == 0:
            try:
                on_progress(i + 1, num_agents)
            except Exception:
                pass
    evidence_used = {
        "news_score": ev.get('news_score'),
        "community_score": ev.get('community_score'),
        "momentum_pct": ev.get('momentum_pct'),
        "per": ev.get('per'),
        "research_findings": len(fnd),
        "shift_by_type": {t: round(v, 3) for t, v in shifts.items()},
    }
    return decisions, evidence_used


def _generate_stock_ontology(ticker: str, company_name: str) -> dict:
    """
    주식 분석을 위한 온톨로지 생성

    Args:
        ticker: 종목 코드
        company_name: 회사명

    Returns:
        온톨로지 딕셔너리 (entity_types, edge_types 포함)
    """
    return {
        "entity_types": [
            {
                "name": "Company",
                "description": f"{company_name}({ticker}) 및 관련 기업",
                "examples": [company_name, "경쟁사", "협력사"]
            },
            {
                "name": "Investor",
                "description": "개인 투자자 및 기관 투자자",
                "examples": ["개인투자자", "외국인투자자", "기관투자자"]
            },
            {
                "name": "FinancialMetric",
                "description": "재무 지표 및 주가 지표",
                "examples": ["PER", "PBR", "ROE", "EPS", "주가수익률"]
            },
            {
                "name": "MarketEvent",
                "description": "시장 이벤트 및 뉴스",
                "examples": ["실적발표", "배당공시", "유상증자", "대외경제이슈"]
            },
            {
                "name": "Sector",
                "description": "산업 섹터 및 업종",
                "examples": ["반도체", "전자", "IT", "금융"]
            }
        ],
        "edge_types": [
            {
                "name": "INVESTS_IN",
                "description": "투자자가 종목에 투자하는 관계",
                "source": "Investor",
                "target": "Company"
            },
            {
                "name": "AFFECTS",
                "description": "시장 이벤트가 기업 또는 지표에 영향을 주는 관계",
                "source": "MarketEvent",
                "target": "Company"
            },
            {
                "name": "HAS_METRIC",
                "description": "기업이 재무 지표를 보유하는 관계",
                "source": "Company",
                "target": "FinancialMetric"
            },
            {
                "name": "BELONGS_TO",
                "description": "기업이 특정 섹터에 속하는 관계",
                "source": "Company",
                "target": "Sector"
            },
            {
                "name": "COMPETES_WITH",
                "description": "기업 간 경쟁 관계",
                "source": "Company",
                "target": "Company"
            }
        ],
        "description": f"{company_name}({ticker}) 주식 분석 온톨로지"
    }
