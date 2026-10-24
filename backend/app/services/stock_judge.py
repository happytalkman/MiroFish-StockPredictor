"""
AI 투자 판단 (Judgment) 모듈

예측(시뮬레이션 + 보고서) 결과를 받아 xAI Grok 모델이 최종 판단을 내립니다.
  - verdict:     매수 / 분할매수 / 관망 / 비중축소 / 매도
  - conviction:  판단 확신도 (0~100)
  - agree:       예측 방향에 동의하는지 여부
  - reasons / risks / triggers(진입·청산 조건) / horizon

xAI 는 OpenAI 호환 API 이므로 openai SDK 를 base_url 만 바꿔 사용합니다.
키가 없거나 호출이 실패하면 규칙 기반 판단으로 대체합니다.
"""

import os
import json
import re
import time
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import OpenAI

from ..config import Config
from ..utils.logger import get_logger
from ..utils.resilience import retry_call, repair_json

logger = get_logger('mirofish.stock_judge')

XAI_BASE_URL = os.environ.get('XAI_BASE_URL', 'https://api.x.ai/v1')
GATEWAY_BASE_URL = os.environ.get('AI_GATEWAY_BASE_URL', 'https://ai-gateway.vercel.sh/v1')

# 사용 가능한 모델 중 앞에서부터 먼저 발견되는 것을 사용
# 기본 폴백 이름 (모델 목록 조회 실패 시). 평소에는 목록에서 최신 grok 자동 선택
MODEL_PREFERENCE = ['grok-4.7']

VERDICTS = ['매수', '분할매수', '관망', '비중축소', '매도']

_model_cache: Dict[str, Dict[str, Any]] = {}
_model_lock = threading.Lock()


def _reports_dir() -> str:
    return os.path.join(Config.UPLOAD_FOLDER, 'stock_reports')


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


class StockJudge:
    """예측 결과 → 최종 투자 판단"""

    def __init__(self):
        """
        공급자 체인: xAI 직접 호출 → Vercel AI Gateway(xai/*) → 규칙 기반.
        """
        self.forced_model = os.environ.get('XAI_JUDGE_MODEL') or Config.XAI_JUDGE_MODEL
        self.providers = []
        xai_key = os.environ.get('XAI_API_KEY') or Config.XAI_API_KEY
        gw_key = os.environ.get('AI_GATEWAY_API_KEY') or Config.AI_GATEWAY_API_KEY
        if xai_key:
            self.providers.append({
                "name": "xai", "prefix": "",
                "client": OpenAI(api_key=xai_key, base_url=XAI_BASE_URL, timeout=90, max_retries=1),
            })
        if gw_key:
            self.providers.append({
                "name": "vercel-gateway", "prefix": "xai/",
                "client": OpenAI(api_key=gw_key, base_url=GATEWAY_BASE_URL, timeout=90, max_retries=1),
            })

    # ------------------------------------------------------------------ 모델 선택
    @staticmethod
    def list_models(provider: Dict[str, Any]) -> List[str]:
        try:
            client = provider["client"].with_options(timeout=10, max_retries=0)
            return sorted(m.id for m in client.models.list().data)
        except Exception as e:
            logger.warning(f"[{provider['name']}] 모델 목록 조회 실패: {e}")
            return []

    @staticmethod
    def _pick_grok(available: List[str]) -> Optional[str]:
        """
        사용 가능한 모델 중 텍스트용 최신 grok 선택.
        prefix(xai/, spacexai/ 등)는 목록에 있는 그대로 유지합니다.
        """
        skip = ('image', 'imagine', 'video', 'vision', 'build', 'code', 'stt', 'tts', 'voice', 'multi-agent', 'beta')
        cands = []
        for full in available:
            base = full.split('/')[-1]
            if not base.startswith('grok') or any(x in base for x in skip):
                continue
            m = re.match(r'grok-(\d+(?:\.\d+)?)', base)
            if not m:
                continue
            ver = float(m.group(1))  # 4.20 == 4.2 < 4.7
            # 같은 버전이면: 접미사 없음 > reasoning > fast-reasoning > non-reasoning
            if 'non-reasoning' in base:
                rank = 0
            elif 'fast' in base:
                rank = 1
            elif 'reasoning' in base:
                rank = 2
            else:
                rank = 3
            cands.append((ver, rank, -len(base), full))
        if not cands:
            return None
        return max(cands)[3]

    def resolve_model(self, provider: Dict[str, Any]) -> str:
        name = provider["name"]
        with _model_lock:
            cached = _model_cache.get(name)
            if cached and time.time() - cached["checked_at"] < 3600:
                return cached["model"]
            available = self.list_models(provider)
            chosen = None
            if self.forced_model:
                want = self.forced_model.split('/')[-1]
                chosen = next((m for m in available if m.split('/')[-1] == want), None)
            if not chosen:
                chosen = next((m for m in available if m.split('/')[-1] in MODEL_PREFERENCE
                               and m.split('/')[-1] == MODEL_PREFERENCE[0]), None) or self._pick_grok(available)
            if not chosen:
                chosen = provider["prefix"] + (self.forced_model or MODEL_PREFERENCE[0]).split('/')[-1]
            if available:
                _model_cache[name] = {"model": chosen, "checked_at": time.time(), "available": available}
            logger.info(f"[{name}] 판단 모델 선택: {chosen} (사용 가능 {len(available)}개)")
            return chosen

    def diagnostics(self) -> Dict[str, Any]:
        out = []
        for p in self.providers:
            models = self.list_models(p)
            grok = [m for m in models if 'grok' in m]
            out.append({"provider": p["name"], "reachable": bool(models),
                        "grok_models": grok[:30], "selected": self.resolve_model(p) if models else None})
        return {"providers": out, "forced_model": self.forced_model}

    # ------------------------------------------------------------------ 입력 수집
    def collect_context(self, report_id: str) -> Dict[str, Any]:
        report_dir = os.path.join(_reports_dir(), report_id)
        report = _read_json(os.path.join(report_dir, 'report.json'))
        if not report:
            raise FileNotFoundError(f"보고서를 찾을 수 없습니다: {report_id}")

        md_path = os.path.join(report_dir, 'report.md')
        markdown = ''
        if os.path.exists(md_path):
            with open(md_path, 'r', encoding='utf-8') as f:
                markdown = f.read()

        simulation_id = report.get('simulation_id', '')
        sim = _read_json(os.path.join(Config.UPLOAD_FOLDER, 'stock_simulations', simulation_id, 'result.json')) or {}

        # 에이전트 투표 집계
        summary = sim.get('summary', {}) or {}
        decisions = sim.get('agents_decisions', []) or []
        buy = summary.get('buy_count', sum(1 for d in decisions if d.get('decision') == 'buy'))
        sell = summary.get('sell_count', sum(1 for d in decisions if d.get('decision') == 'sell'))
        hold = summary.get('hold_count', sum(1 for d in decisions if d.get('decision') == 'hold'))
        total = max(1, buy + sell + hold)
        confs = [d.get('confidence') for d in decisions if isinstance(d.get('confidence'), (int, float))]
        votes = {
            "buy_pct": round(summary.get('buy_pct', buy / total * 100), 1),
            "sell_pct": round(summary.get('sell_pct', sell / total * 100), 1),
            "hold_pct": round(summary.get('hold_pct', hold / total * 100), 1),
            "num_agents": sim.get('num_agents', len(decisions)),
            "avg_confidence": round(sum(confs) / len(confs), 2) if confs else None,
        }

        # 에이전트 근거 샘플 (유형별 최대 2개)
        samples, per_type = [], {}
        for d in decisions:
            t = d.get('agent_type', 'unknown')
            if per_type.get(t, 0) >= 2:
                continue
            reason = d.get('reasoning') or d.get('reason') or d.get('rationale') or ''
            if reason:
                samples.append(f"[{t}/{d.get('decision')}] {str(reason)[:200]}")
                per_type[t] = per_type.get(t, 0) + 1
            if len(samples) >= 10:
                break

        # 가격 데이터 (최근 60봉 종가)
        task_id = sim.get('task_id', '')
        analysis = _read_json(os.path.join(Config.UPLOAD_FOLDER, 'stock_analyses', task_id, 'analysis.json')) or {}
        ohlcv = (analysis.get('financial_data') or {}).get('ohlcv', []) or []
        price = self._price_stats(ohlcv)

        return {
            "report_id": report_id,
            "simulation_id": simulation_id,
            "ticker": report.get('ticker') or sim.get('ticker', ''),
            "company_name": report.get('company_name') or sim.get('company_name', ''),
            "votes": votes,
            "agent_samples": samples,
            "price": price,
            "sections": report.get('sections', {}) or {},
            "markdown": markdown,
        }

    @staticmethod
    def _price_stats(ohlcv: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not ohlcv:
            return {"has_data": False}
        key = next((k for k in ['종가', 'close', 'Close'] if k in ohlcv[0]), None)
        if not key:
            return {"has_data": False}
        closes = [float(r[key]) for r in ohlcv if r.get(key)]
        if len(closes) < 2:
            return {"has_data": False}
        last = closes[-1]

        def ma(n):
            return round(sum(closes[-n:]) / n, 1) if len(closes) >= n else None

        def chg(n):
            return round((last / closes[-n - 1] - 1) * 100, 2) if len(closes) > n else None

        # RSI(14)
        rsi = None
        if len(closes) > 15:
            gains = losses = 0.0
            for a, b in zip(closes[-15:-1], closes[-14:]):
                diff = b - a
                gains += max(diff, 0)
                losses += max(-diff, 0)
            rsi = 100.0 if losses == 0 else round(100 - 100 / (1 + gains / losses), 1)

        # 20일 변동성 (일간 수익률 표준편차, %)
        rets = [(b / a - 1) * 100 for a, b in zip(closes[-21:-1], closes[-20:])]
        vol = None
        if len(rets) > 1:
            m = sum(rets) / len(rets)
            vol = round((sum((x - m) ** 2 for x in rets) / (len(rets) - 1)) ** 0.5, 2)

        return {
            "has_data": True,
            "last_close": last,
            "ma5": ma(5), "ma20": ma(20), "ma60": ma(60),
            "chg_5d_pct": chg(5), "chg_20d_pct": chg(20), "chg_60d_pct": chg(60),
            "high_60d": max(closes[-60:]), "low_60d": min(closes[-60:]),
            "rsi14": rsi,
            "daily_vol_20d_pct": vol,
            "recent_closes": closes[-20:],
        }

    # ------------------------------------------------------------------ 판단
    def judge(self, report_id: str, prediction: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ctx = self.collect_context(report_id)
        prediction = prediction or self._derive_prediction(ctx)

        result = None
        errors = []
        for provider in self.providers:
            try:
                result = self._judge_with_llm(provider, ctx, prediction)
                break
            except Exception as e:
                errors.append(f"{provider['name']}: {str(e)[:300]}")
                logger.error(f"[{report_id}] {provider['name']} 판단 실패: {e}")
        if not self.providers:
            errors.append("XAI_API_KEY / AI_GATEWAY_API_KEY 미설정")
        error = " | ".join(errors) or None

        if not result:
            result = self._rule_based(ctx, prediction)
            result["fallback_reason"] = error

        result.update({
            "report_id": report_id,
            "ticker": ctx["ticker"],
            "company_name": ctx["company_name"],
            "prediction": prediction,
            "created_at": datetime.now().isoformat(timespec='seconds'),
        })

        out = os.path.join(_reports_dir(), report_id, 'judgment.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    @staticmethod
    def load_cached(report_id: str) -> Optional[Dict[str, Any]]:
        return _read_json(os.path.join(_reports_dir(), report_id, 'judgment.json'))

    @staticmethod
    def _derive_prediction(ctx: Dict[str, Any]) -> Dict[str, Any]:
        v = ctx["votes"]
        buy, sell, hold = v["buy_pct"], v["sell_pct"], v["hold_pct"]
        if buy - sell >= 10:
            return {"direction": "상승", "probability": buy}
        if sell - buy >= 10:
            return {"direction": "하락", "probability": sell}
        return {"direction": "보합", "probability": hold}

    def _judge_with_llm(self, provider: Dict[str, Any], ctx: Dict[str, Any],
                        prediction: Dict[str, Any]) -> Dict[str, Any]:
        model = self.resolve_model(provider)
        sections = ctx["sections"]
        section_text = "\n\n".join(
            f"### {k}\n{str(v)[:1200]}" for k, v in sections.items() if v
        ) or ctx["markdown"][:6000]

        system = (
            "당신은 한국 주식시장(KOSPI/KOSDAQ) 전문 리스크 매니저 겸 포트폴리오 매니저입니다. "
            "AI 에이전트 시뮬레이션이 만든 '예측'을 그대로 믿지 말고, 가격 데이터·에이전트 투표 분포·보고서 내용을 교차 검증해 "
            "독립적인 최종 '판단'을 내립니다. 예측과 가격 데이터가 충돌하면 그 사실을 명시하고 확신도를 낮춥니다. "
            "근거는 반드시 제공된 수치를 인용하세요. 데이터에 없는 뉴스·실적을 지어내지 마세요. "
            "출력은 오직 JSON 한 개입니다."
        )
        user = f"""[종목] {ctx['company_name']} ({ctx['ticker']})

[예측 결과]
방향: {prediction.get('direction')} / 예측 신뢰도: {prediction.get('probability')}

[에이전트 투표]
{json.dumps(ctx['votes'], ensure_ascii=False)}

[에이전트 근거 샘플]
{chr(10).join(ctx['agent_samples']) or '없음'}

[가격 지표]
{json.dumps(ctx['price'], ensure_ascii=False)}

[예측 보고서 발췌]
{section_text[:7000]}

다음 스키마의 JSON 으로만 답하세요:
{{
  "verdict": "매수|분할매수|관망|비중축소|매도 중 하나",
  "conviction": 0~100 정수 (판단 확신도),
  "agree_with_prediction": true/false,
  "stance_vs_prediction": "예측에 대한 동의/반박을 1문장으로",
  "horizon": "판단의 유효 기간 (예: 1~2주)",
  "headline": "한 줄 결론 (40자 이내)",
  "reasons": ["수치를 인용한 핵심 근거 3개"],
  "risks": ["판단을 뒤집을 수 있는 리스크 2~3개"],
  "entry_trigger": "진입/추가매수 조건 (가격·지표 기준)",
  "exit_trigger": "손절/청산 조건 (가격·지표 기준)",
  "position_size": "권장 비중 (예: 계좌의 5% 이내)"
}}"""

        kwargs = dict(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=4000,
        )
        create = provider["client"].chat.completions.create
        try:
            resp = retry_call(create, tries=3, label=f'판단 {model}', response_format={"type": "json_object"}, **kwargs)
        except Exception as e:
            if 'response_format' not in str(e) and 'json' not in str(e).lower():
                raise
            resp = retry_call(create, tries=3, label=f'판단 {model}', **kwargs)
        raw = (resp.choices[0].message.content or '').strip()
        data = repair_json(raw)

        verdict = str(data.get('verdict', '관망')).strip()
        if verdict not in VERDICTS:
            verdict = next((v for v in VERDICTS if v in verdict), '관망')
        try:
            conviction = max(0, min(100, int(float(data.get('conviction', 50)))))
        except (TypeError, ValueError):
            conviction = 50

        return {
            "source": provider["name"],
            "model": model,
            "verdict": verdict,
            "conviction": conviction,
            "agree_with_prediction": bool(data.get('agree_with_prediction', True)),
            "stance_vs_prediction": data.get('stance_vs_prediction', ''),
            "horizon": data.get('horizon', ''),
            "headline": data.get('headline', ''),
            "reasons": [str(x) for x in (data.get('reasons') or [])][:4],
            "risks": [str(x) for x in (data.get('risks') or [])][:3],
            "entry_trigger": data.get('entry_trigger', ''),
            "exit_trigger": data.get('exit_trigger', ''),
            "position_size": data.get('position_size', ''),
        }

    @staticmethod
    def _rule_based(ctx: Dict[str, Any], prediction: Dict[str, Any]) -> Dict[str, Any]:
        v, p = ctx["votes"], ctx["price"]
        spread = v["buy_pct"] - v["sell_pct"]
        score = spread  # -100 ~ +100
        reasons = [f"에이전트 매수 {v['buy_pct']}% / 매도 {v['sell_pct']}% / 보합 {v['hold_pct']}%"]
        risks = []

        if p.get("has_data"):
            last, ma20 = p["last_close"], p.get("ma20")
            if ma20:
                above = last >= ma20
                score += 15 if above else -15
                reasons.append(f"종가 {last:,.0f}원, 20일선 {ma20:,.0f}원 {'위' if above else '아래'}")
            rsi = p.get("rsi14")
            if rsi is not None:
                if rsi >= 70:
                    score -= 10
                    risks.append(f"RSI {rsi} 과매수 구간 — 단기 조정 가능")
                elif rsi <= 30:
                    score += 10
                    reasons.append(f"RSI {rsi} 과매도 구간 — 기술적 반등 여지")
                else:
                    reasons.append(f"RSI {rsi} 중립 구간")
        else:
            risks.append("가격 데이터 없음 — 기술적 검증 불가")

        if score >= 35:
            verdict = "매수"
        elif score >= 15:
            verdict = "분할매수"
        elif score <= -35:
            verdict = "매도"
        elif score <= -15:
            verdict = "비중축소"
        else:
            verdict = "관망"

        direction = prediction.get("direction")
        agree = (direction == "상승" and verdict in ("매수", "분할매수")) or \
                (direction == "하락" and verdict in ("매도", "비중축소")) or \
                (direction == "보합" and verdict == "관망")
        risks.append("규칙 기반 판단 — AI 판단 호출 실패 시의 대체 결과")

        exit_trigger = entry_trigger = ''
        if p.get("has_data") and p.get("ma20"):
            entry_trigger = f"20일선 {p['ma20']:,.0f}원 지지 확인 후 분할 진입"
            exit_trigger = f"60일 저점 {p['low_60d']:,.0f}원 이탈 시 손절"

        return {
            "source": "rule",
            "model": None,
            "verdict": verdict,
            "conviction": int(min(90, 40 + abs(score) / 2)),
            "agree_with_prediction": agree,
            "stance_vs_prediction": "예측과 같은 방향" if agree else "예측과 다른 판단 — 가격 지표가 뒷받침하지 않음",
            "horizon": "1~2주",
            "headline": f"{verdict} (점수 {score:+.0f})",
            "reasons": reasons[:4],
            "risks": risks[:3],
            "entry_trigger": entry_trigger,
            "exit_trigger": exit_trigger,
            "position_size": "계좌의 5% 이내",
        }
