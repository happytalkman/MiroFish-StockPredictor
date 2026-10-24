"""
5단계: 판단 — 1~4단계에서 쌓인 모든 근거를 모아 매매전략·기술적 분석 결론을 % 로 제출합니다.

  0) 투자위원회 : 번호 사실표(F#/R#/C#/K#)만으로 강세·약세·리스크·의장이 토론 (stock_committee)
                  — committee.json 이 있고 사실표가 같으면 재사용, 없거나 사실표가 바뀌면 Grok 계획과 병렬 실행
  1) Grok(xAI)  : 1~4단계 근거(가격 지표·에이전트 투표·보고서·지식그래프 CQ/ADR)를 읽고
                  구체적 매매 계획(진입·목표·손절·비중)과 기술적 신호 값을 설계
  2) JEV(typesafe-ai/jev, Vercel AI Gateway /v1/evaluate)
                : 같은 근거 + 위원회 토론 요지(양측 논거·정정·쟁점, 점수/결론 제외)를 '상태(state)'로 넘겨
                  방향·전략 선택지 확률, 기술적 명제·CQ·ADR·위원회 쟁점의 참 확률을 채점
  3) 결합       : JEV 확률을 1차 근거로, Grok 계획을 실행안으로 묶어 % 형태의 최종 판단 생성
                  (위원회 결과는 수치에 섞지 않고 나란히 보고 + 일치 여부)
"""

import os
import json
import re
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..config import Config
from ..utils.logger import get_logger
from ..utils.resilience import retry_call
from .stock_judge import StockJudge, _reports_dir, _read_json
from .stock_knowledge_graph import StockKnowledgeGraph
from .stock_committee import InvestmentCommittee, facts_signature, inflight_event, WHO_LABEL, CONSENSUS

logger = get_logger('mirofish.stock_decision')

GATEWAY_EVAL_URL = os.environ.get('JEV_GATEWAY_URL', 'https://ai-gateway.vercel.sh/v1/evaluate')
TYPESAFE_URL = os.environ.get('TYPESAFE_URL', 'https://api.typesafe.ai/v1/systemone')

DIRECTIONS = {'up': '상승', 'flat': '보합', 'down': '하락'}
STRATEGIES = {'buy': '매수', 'accumulate': '분할매수', 'hold': '관망', 'reduce': '비중축소', 'sell': '매도'}

TECH_PROPS = [
    ('trend', '추세', '현재가가 20일·60일 이동평균 위에 있어 중기 상승 추세가 유효하다.'),
    ('momentum', '모멘텀', '최근 5일·20일 수익률과 RSI(14)가 추가 상승 여력(과매수 아님)을 시사한다.'),
    ('overbought', '과열 위험', 'RSI·단기 급등률로 볼 때 단기 과열로 조정 가능성이 높다.'),
    ('volatility', '변동성', '20일 일간 변동성이 신규 진입을 감당할 수 있는 수준이다.'),
    ('support', '지지선', '현재가가 20일선·60일 저점 등 의미 있는 지지 구간에 가깝다.'),
    ('consensus', '수급·심리', '에이전트 시뮬레이션 투표와 뉴스 심리가 매수 쪽으로 기울어 있다.'),
]


def _clamp01(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return max(0.0, min(1.0, v))


def _read_prob(ans: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(ans, dict):
        return None
    for k in ('noul', 'boolean', 'probability', 'p'):
        if isinstance(ans.get(k), (int, float)):
            return _clamp01(ans[k])
    v = ans.get('value')
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return _clamp01(v)
    return None


def _norm_probs(probs: Dict[str, Any], keys: List[str], choice: Optional[str] = None) -> Dict[str, float]:
    vals = {k: max(0.0, float(probs.get(k) or 0)) for k in keys}
    s = sum(vals.values())
    if s <= 0:
        return {k: (1.0 if k == choice else 0.0) for k in keys}
    return {k: v / s for k, v in vals.items()}


def _pct(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x * 100, 1)


def _issue_key(issue_id: Any) -> str:
    return 'issue_' + (re.sub(r'[^0-9A-Za-z_]', '', str(issue_id or '')) or 'X')


# 전략 → 위원회 합의 방향
_STRATEGY_SIDE = {'buy': 'bull', 'accumulate': 'bull', 'hold': 'split', 'reduce': 'bear', 'sell': 'bear'}
_AGREE_LABEL = {'agree': '일치', 'partial': '부분 일치', 'disagree': '불일치'}


def _horizon_days(h: Any) -> int:
    """'2~4주', '4-8주', '1~3개월', '20거래일' → 거래일 수(상한 기준). 모르면 20"""
    t = str(h or '')
    nums = [float(x) for x in re.findall(r'\d+(?:\.\d+)?', t)]
    if not nums:
        return 20
    n = max(nums)
    if '개월' in t or '달' in t:
        d = n * 21
    elif '주' in t:
        d = n * 5
    elif '일' in t:
        d = n
    else:
        d = 20
    return int(max(5, min(250, d)))


def _tick(p: float, down: bool = True) -> float:
    """KRX 호가 단위(2023~) 로 반올림"""
    import math
    if not p:
        return p
    for lim, t in ((2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)):
        if p < lim:
            break
    else:
        t = 1000
    return float((math.floor if down else math.ceil)(p / t) * t)


class StockDecision:

    def __init__(self):
        self.judge = StockJudge()
        self.gw_key = (os.environ.get('AI_GATEWAY_API_KEY') or Config.AI_GATEWAY_API_KEY or '').strip()
        self.ts_key = next((os.environ.get(k, '').strip() for k in ('TYPESAFE_API_KEY', 'JEV_API_KEY', 'TYPESAFE_JEV_API_KEY')
                            if os.environ.get(k, '').strip()), '')

    @staticmethod
    def load_cached(report_id: str) -> Optional[Dict[str, Any]]:
        return _read_json(os.path.join(_reports_dir(), report_id, 'decision.json'))

    # ------------------------------------------------------------------ 근거 묶음(state)
    def _evidence(self, report_id: str) -> Dict[str, Any]:
        ctx = self.judge.collect_context(report_id)
        kg = StockKnowledgeGraph.load(report_id) or {}
        judgment = StockJudge.load_cached(report_id) or {}
        facts: List[Dict[str, Any]] = []
        try:
            facts = InvestmentCommittee().build_facts(report_id, ctx, kg)
        except Exception as e:
            logger.warning(f"[{report_id}] 사실표 구성 실패 (투자위원회 생략 가능): {e}")
        return {'ctx': ctx, 'kg': kg, 'judgment': judgment, 'facts': facts}

    @staticmethod
    def _monte_carlo(ctx: Dict[str, Any], report_json: Dict[str, Any],
                     plan: Optional[Dict[str, Any]] = None, long_side: bool = False) -> Optional[Dict[str, Any]]:
        """2단계 시뮬레이션의 몬테카를로 요약 (예전 시뮬레이션이면 1단계 일봉으로 지금 계산해 채움)
        + 매수 계획이면 같은 표본으로 목표가·손절가 선도달 확률·기대 R"""
        sim_id = ctx.get('simulation_id') or report_json.get('simulation_id') or ''
        if not sim_id:
            return None
        try:
            from . import monte_carlo as mcm
            mc, sample = mcm.ensure_for_simulation(sim_id)
        except Exception as e:
            logger.warning(f"몬테카를로 불러오기 실패: {e}")
            return None
        if not (isinstance(mc, dict) and mc.get('direction_probs')):
            return None
        mc = dict(mc)
        p = plan or {}
        if long_side and sample and p.get('target_price') and p.get('stop_price'):
            try:
                base = p.get('current_price') or (report_json.get('summary') or {}).get('last_close')
                pp = mcm.plan_probabilities(sample, sim_id, float(base), float(p.get('entry_price') or base),
                                            float(p['target_price']), float(p['stop_price']), _horizon_days(p.get('horizon')))
                if pp:
                    mc['plan'] = pp
            except Exception as e:
                logger.warning(f"몬테카를로 계획 평가 생략: {e}")
        return mc

    def _state_text(self, ev: Dict[str, Any], plan: Optional[Dict[str, Any]] = None,
                    committee: Optional[Dict[str, Any]] = None, limit: int = 6000) -> str:
        ctx, kg = ev['ctx'], ev['kg']
        digest = self._committee_digest(committee, ev.get('facts') or []) if committee else ''
        # 위원회 요지가 붙으면 다른 부분을 줄여 전체 길이 예산(6000자)을 지킴
        tight = bool(digest)
        n_samples, sample_len = (4, 100) if tight else (5, 120)
        sec_len = 280 if tight else 500
        n_kg, kg_len = (5, 160) if tight else (6, 200)
        p = {k: v for k, v in ctx['price'].items() if k != 'recent_closes'}
        lines = [
            f"종목: {ctx['company_name']} ({ctx['ticker']}) — 한국 주식",
            f"[1·2단계] 가격 지표: {json.dumps(p, ensure_ascii=False)}",
            f"[2단계] 에이전트 {ctx['votes'].get('num_agents')}명 투표: 매수 {ctx['votes']['buy_pct']}% / 매도 {ctx['votes']['sell_pct']}% / 보합 {ctx['votes']['hold_pct']}% (평균 확신 {ctx['votes'].get('avg_confidence')})",
        ]
        fund_line = next((f['text'] for f in (ev.get('facts') or []) if f.get('kind') == 'fund' and '컨센서스' in f.get('text', '')), None)
        val_line = next((f['text'] for f in (ev.get('facts') or []) if f.get('kind') == 'fund' and 'PER' in f.get('text', '') and '컨센서스' not in f.get('text', '')), None)
        if val_line:
            lines.append(f"[1단계] 재무 지표: {val_line[:300]}")
        if fund_line:
            lines.append(f"[1단계] 증권사 {fund_line[:300]}")
        # 분기 실적·수급·공매도·몬테카를로·과거 기록 (사실표 확장분)
        tags = {'earnings': '실적', 'flow': '수급', 'short': '공매도', 'mc': '몬테카를로', 'history': '과거 기록'}
        for f in (ev.get('facts') or []):
            if f.get('kind') in tags:
                lines.append(f"[{tags[f['kind']]} {f['id']}] {f['text'][:280 if not tight else 200]}")
        if ctx['agent_samples']:
            lines.append("[2단계] 에이전트 근거: " + " / ".join(s[:sample_len] for s in ctx['agent_samples'][:n_samples]))
        sections = ctx.get('sections') or {}
        if isinstance(sections, dict):
            for k in ('technical_analysis', 'consensus_forecast', 'risk_factors', 'investment_opinion'):
                v = sections.get(k)
                v = v.get('content') if isinstance(v, dict) else v
                if v:
                    lines.append(f"[3단계 보고서:{k}] {str(v)[:sec_len]}")
        if kg:
            adrs = kg.get('adrs', [])[:n_kg]
            if adrs:
                lines.append("[4단계 ADR] " + " / ".join(f"{a['title']}: {a['decision']} (확신 {a['confidence']})"[:kg_len] for a in adrs))
            cqs = kg.get('cqs', [])[:n_kg]
            if cqs:
                lines.append("[4단계 CQ] " + " / ".join(f"{c['question']} → {c['answer']}"[:kg_len] for c in cqs))
        if plan:
            lines.append(f"[Grok 매매계획] {json.dumps({k: plan.get(k) for k in ('strategy', 'entry_price', 'target_price', 'stop_price', 'position_pct', 'horizon')}, ensure_ascii=False)}")
        base = "\n".join(lines)
        if digest:
            return base[:max(0, limit - len(digest) - 1)] + "\n" + digest
        return base[:limit]

    # ------------------------------------------------------------------ 투자위원회 → JEV
    @staticmethod
    def _committee_issues(committee: Optional[Dict[str, Any]], n: int = 3) -> List[Dict[str, Any]]:
        """JEV 에 물을 핵심 쟁점 (의장의 쟁점별 판단 leaning/why 는 앵커링 방지를 위해 제외)"""
        if not committee:
            return []
        imap = {i.get('id'): i for i in (committee.get('issues') or []) if isinstance(i, dict)}
        out, seen = [], set()
        src = ((committee.get('final') or {}).get('key_issues') or [])
        if not src:
            src = sorted(imap.values(), key=lambda i: i.get('status') != 'open')
        for k in src:
            if not isinstance(k, dict) or not k.get('id') or k.get('id') in seen:
                continue
            base = imap.get(k['id'], {})
            text = k.get('issue') or base.get('issue')
            if not text:
                continue
            seen.add(k['id'])
            out.append({'id': k['id'], 'issue': str(text),
                        'bull_view': str(k.get('bull_view') or base.get('bull_view') or ''),
                        'bear_view': str(k.get('bear_view') or base.get('bear_view') or ''),
                        'status': base.get('status') or k.get('status') or 'open'})
            if len(out) >= n:
                break
        return out

    @staticmethod
    def _committee_sides(committee: Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        op = (committee or {}).get('openings') or {}

        def rank(role):
            args = [a for a in ((op.get(role) or {}).get('arguments') or []) if isinstance(a, dict) and a.get('claim')]
            return sorted(args, key=lambda a: (bool(a.get('valid_facts')), a.get('strength') or 0), reverse=True)
        return rank('bull'), rank('bear')

    def _committee_digest(self, committee: Optional[Dict[str, Any]], facts: List[Dict[str, Any]],
                          limit: int = 2200) -> str:
        """양측 균형 요지: 같은 개수의 강세·약세 논거(사실 ID), 의장 정정, 핵심 쟁점, 인용된 사실 원문.
        의장 점수·합의·권고 전략은 싣지 않음(앵커링 방지)."""
        if not committee:
            return ''
        bull, bear = self._committee_sides(committee)
        n = min(3, len(bull), len(bear))
        lines = ["[투자위원회 토론 요지 — 사실표 기반 양측 논거·의장 정정·핵심 쟁점 (점수·결론 제외)]"]
        cited: List[str] = []

        def arg_line(a):
            ids = [x for x in (a.get('valid_facts') or []) if isinstance(x, str)]
            for x in ids:
                if x not in cited:
                    cited.append(x)
            return f"- {str(a['claim'])[:130]}" + (f" [{', '.join(ids)}]" if ids else " [사실 인용 없음]")
        if n:
            lines.append("강세 논거:")
            lines += [arg_line(a) for a in bull[:n]]
            lines.append("약세 논거:")
            lines += [arg_line(a) for a in bear[:n]]
        corrs = sorted([c for c in (committee.get('corrections') or []) if isinstance(c, dict)],
                       key=lambda c: c.get('source') != 'moderator')[:4]
        if corrs:
            lines.append("사실 정정(의장·자동 점검):")
            lines += [f"- ({WHO_LABEL.get(c.get('who'), '공통')}) {str(c.get('problem') or '')[:70]} → {str(c.get('correction') or '')[:100]}"
                      for c in corrs]
        issues = self._committee_issues(committee, 3)
        if issues:
            lines.append("핵심 쟁점:")
            lines += [f"- {i['id']} {i['issue'][:90]} (강세: {i['bull_view'][:70]} / 약세: {i['bear_view'][:70]})" for i in issues]
        fmap = {f.get('id'): f for f in (committee.get('facts') or facts or []) if isinstance(f, dict)}
        fl = [f"[{fid}] {str(fmap[fid].get('text', ''))[:110]}" for fid in cited[:8] if fid in fmap]
        if fl:
            lines.append("인용된 사실:")
            lines += fl
        out = ''
        for ln in lines:
            if len(out) + len(ln) + 1 > limit:
                break
            out += ('\n' if out else '') + ln
        return out

    def _committee_questions(self, committee: Optional[Dict[str, Any]], bool_type: str) -> Dict[str, Any]:
        if not committee:
            return {}
        qs: Dict[str, Any] = {}
        bull, bear = self._committee_sides(committee)
        if bull and bear:
            qs['committee_bull'] = {'type': bool_type,
                                    'instructions': '투자위원회 토론에서 강세 측 핵심 논거가 사실에 근거하며 약세 측보다 설득력 있다.'}
        for i in self._committee_issues(committee, 3):
            views = ''
            if i['bull_view'] or i['bear_view']:
                views = f" (강세: {i['bull_view'][:150]} / 약세: {i['bear_view'][:150]})"
            qs[_issue_key(i['id'])] = {'type': bool_type,
                                       'instructions': f"쟁점 {i['id']}: {i['issue'][:200]} — 강세 측 견해가 옳다.{views}"}
        return qs

    @staticmethod
    def _agreement(final: Dict[str, Any], strategy: str, source: str) -> Optional[Dict[str, Any]]:
        if not final:
            return None
        cons = final.get('consensus')
        side = _STRATEGY_SIDE.get(strategy)
        if not cons or not side:
            return None
        if cons == side:
            status = 'agree'
        elif 'split' in (cons, side):
            status = 'partial'
        else:
            status = 'disagree'
        who = 'JEV' if source == 'jev' else 'Grok 추정'
        note = (f"위원회 {CONSENSUS.get(cons, cons)}(권고 {final.get('recommended_label') or '-'}) ↔ "
                f"{who} 최선 전략 {STRATEGIES.get(strategy, strategy)} — {_AGREE_LABEL[status]}")
        return {'status': status, 'label': _AGREE_LABEL[status], 'agree': status == 'agree',
                'committee_consensus': cons, 'committee_strategy': final.get('recommended_strategy'),
                'decision_strategy': strategy, 'decision_source': source,
                'strategy_match': final.get('recommended_strategy') == strategy, 'note': note}

    # ------------------------------------------------------------------ Grok 계획
    def _grok_plan(self, ev: Dict[str, Any], on_tick=None) -> Tuple[Dict[str, Any], str]:
        kgsvc = StockKnowledgeGraph()
        system = ("당신은 한국 주식 트레이딩 데스크 헤드입니다. 1~4단계 근거를 교차 검증해 실행 가능한 매매 계획을 숫자로 제시합니다. "
                  "모든 확률·비율은 0~100 숫자. 데이터에 없는 사실은 만들지 마세요. JSON 한 개만 출력.")
        user = f"""{self._state_text(ev)}

출력 스키마:
{{
 "strategy": "buy|accumulate|hold|reduce|sell",
 "direction_probs": {{"up": 0~100, "flat": 0~100, "down": 0~100}},
 "strategy_probs": {{"buy":0~100,"accumulate":0~100,"hold":0~100,"reduce":0~100,"sell":0~100}},
 "horizon": "예: 2~4주",
 "entry_price": 숫자(원), "target_price": 숫자(원), "stop_price": 숫자(원),
 "position_pct": 권장 비중(계좌 대비 %), "win_rate_pct": 예상 승률 %, "expected_return_pct": 기대수익률 %,
 "technical": {{"trend": {{"score": 0~100, "note": "근거 수치"}}, "momentum": {{...}}, "overbought": {{...}}, "volatility": {{...}}, "support": {{...}}, "consensus": {{...}}}},
 "headline": "한 줄 결론(40자 이내)",
 "reasons": ["수치를 인용한 근거 3~4개"],
 "risks": ["판단을 뒤집을 리스크 2~3개"],
 "execution": ["실행 순서 3~4단계 (예: 1차 30% 진입 …)"]
}}"""
        return kgsvc._llm_json(system, user, max_tokens=5000, fast=True, on_tick=on_tick)

    # ------------------------------------------------------------------ JEV 채점
    def _jev(self, state: str, kg: Dict[str, Any], committee: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # TypeSafe 직접 키가 있으면 보정된 확률(NOUL)을 주는 직접 경로를 우선 사용
        if self.ts_key:
            url, key, model, bool_type = TYPESAFE_URL, self.ts_key, 'jev-latest', 'noul'
        elif self.gw_key:
            url, key, model, bool_type = GATEWAY_EVAL_URL, self.gw_key, 'typesafe-ai/jev', 'boolean'
        else:
            raise RuntimeError("AI_GATEWAY_API_KEY / TYPESAFE_API_KEY 가 없습니다")

        questions: Dict[str, Any] = {
            'direction': {
                'type': 'choice',
                'instructions': '오늘 종가 기준으로 향후 20거래일(약 4주) 뒤 이 종목 종가의 방향을 고르시오.',
                'criteria': {'up': '상승: 현재가 대비 +3% 이상', 'flat': '보합: -3%~+3% 범위',
                             'down': '하락: 현재가 대비 -3% 이하'},
            },
            'strategy': {
                'type': 'choice',
                'instructions': '근거 전체를 감안할 때 지금 가장 합리적인 매매 전략을 고르시오.',
                'criteria': {'buy': '매수: 즉시 목표 비중까지 진입', 'accumulate': '분할매수: 나눠서 진입',
                             'hold': '관망: 신규 진입·청산 보류', 'reduce': '비중축소: 일부 차익실현·축소',
                             'sell': '매도: 전량 청산'},
            },
            'risk': {
                'type': 'score',
                'instructions': '지금 이 종목에 신규 진입할 때의 위험 수준을 평가하시오.',
                'criteria': ['낮음: 추세·수급 모두 안정', '보통: 일부 불확실성', '높음: 과열 또는 추세 훼손 신호',
                             '매우 높음: 급락 위험이 뚜렷함'],
            },
            'conviction': {
                'type': 'score',
                'instructions': '제시된 근거가 방향성 판단을 얼마나 강하게 뒷받침하는지 평가하시오.',
                'criteria': ['근거가 상충하거나 부족', '약한 근거', '분명한 근거', '매우 강하고 일관된 근거'],
            },
        }
        for key_, _, prop in TECH_PROPS:
            questions[f'tech_{key_}'] = {'type': bool_type, 'instructions': prop}
        for c in (kg.get('cqs') or [])[:5]:
            questions[f"cq_{c['id']}"] = {'type': bool_type,
                                         'instructions': f"다음 핵심 질문에 대한 답이 근거와 일치한다. 질문: {c['question']} / 답: {c['answer'][:200]}"}
        for a in (kg.get('adrs') or [])[:4]:
            questions[f"adr_{a['id']}"] = {'type': bool_type,
                                          'instructions': f"다음 의사결정은 근거로 정당화된다. 결정: {a['title']} — {a['decision'][:200]}"}
        # 투자위원회: 강세 논거 설득력(committee_bull) + 핵심 쟁점별 '강세 측 견해가 옳다'(issue_I#)
        questions.update(self._committee_questions(committee, bool_type))

        def call(qs):
            t0 = _time.time()
            ok, err, body = False, None, None
            try:
                res = requests.post(url, headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                                    json={'model': model, 'state': state, 'questions': qs}, timeout=60)
                if res.status_code != 200:
                    raise RuntimeError(f"JEV {res.status_code}: {res.text[:300]}")
                body = res.json()
                ok = True
                return body
            except Exception as e:
                err = f'{type(e).__name__}: {str(e)[:120]}'
                raise
            finally:
                try:                      # 운영 지표: JEV 는 OpenAI SDK 밖(HTTP 직접)이라 여기서 기록 — 단가는 LLM_PRICES 의 'jev'
                    from . import ops_metrics
                    u = (body or {}).get('usage') if isinstance(body, dict) else None
                    pin = (u or {}).get('prompt_tokens') or (u or {}).get('input_tokens')
                    pout = (u or {}).get('completion_tokens') or (u or {}).get('output_tokens')
                    host = url.split('//')[-1].split('/')[0]
                    ops_metrics.record_llm(f'jev:{model}', host, _time.time() - t0, ok, False,
                                           pin if pin is not None else int((len(state) + len(json.dumps(qs, ensure_ascii=False))) / 2.2),
                                           pout or 0, est=pin is None, error=err)
                except Exception:
                    pass

        try:
            body = retry_call(call, questions, tries=3, label='JEV')
        except RuntimeError as e:
            # 질문 수가 많아 거절되면 핵심 질문만 다시 시도
            logger.warning(f"JEV 전체 질문 실패, 핵심 질문으로 재시도: {e}")
            core = {k: v for k, v in questions.items() if not k.startswith(('cq_', 'adr_', 'issue_'))}
            body = retry_call(call, core, tries=3, label='JEV(핵심)')
        body['_model'] = body.get('model') or model
        body['_via'] = 'vercel-gateway' if url == GATEWAY_EVAL_URL else 'typesafe'
        return body

    # ------------------------------------------------------------------ 결합
    def decide(self, report_id: str, progress=None, force_committee: bool = False,
               committee_rounds: int = 2) -> Dict[str, Any]:
        import time as _t

        emit_lock = threading.Lock()
        last_pct = [0]

        def emit(pct, msg, detail=None):
            if not progress:
                return
            try:
                with emit_lock:
                    pct = max(last_pct[0], int(pct))   # 병렬 단계에서도 진행률이 뒤로 가지 않게
                    last_pct[0] = pct
                    progress(pct, msg, detail or {})
            except Exception:
                pass

        emit(5, '1~4단계 근거 수집 중 (가격 지표·투표·보고서·지식그래프 CQ/ADR·리서치 사실표)', {'stage': 'evidence'})
        ev = self._evidence(report_id)
        ctx, kg = ev['ctx'], ev['kg']
        facts = ev.get('facts') or []
        price = ctx['price']
        last = price.get('last_close')
        emit(12, f"근거 수집 완료 — CQ {len(kg.get('cqs') or [])}개 · ADR {len(kg.get('adrs') or [])}개 · 사실표 {len(facts)}개 반영",
             {'stage': 'evidence'})

        # ── 투자위원회: 저장본 재사용(사실표 동일) 또는 Grok 계획과 병렬 실행
        committee, com_err, com_cached = None, None, False
        com_svc = None
        try:
            com_svc = InvestmentCommittee()
        except Exception as e:
            com_err = f"투자위원회 초기화 실패: {str(e)[:200]}"
        sig = facts_signature(facts) if facts else None
        if com_svc is not None and not force_committee:
            com_event = inflight_event(report_id)      # (ev 는 근거 묶음 — 이름을 겹치지 않게)
            if com_event is not None and not com_event.is_set():
                # 보고서 직후 미리 시작된 토론이 진행 중이면 새로 열지 않고 끝나기를 기다림
                emit(13, '투자위원회 토론이 이미 진행 중입니다 — 결과를 기다립니다', {'stage': 'committee'})
                com_event.wait(timeout=180)
            cached = InvestmentCommittee.load_cached(report_id)
            if cached and (not sig or cached.get('facts_hash') == sig):
                committee, com_cached = cached, True
            elif cached:
                emit(13, '사실표가 바뀌어 투자위원회 토론을 다시 엽니다', {'stage': 'committee'})
        run_com = com_svc is not None and committee is None and bool(facts)
        if com_svc is not None and committee is None and not facts:
            com_err = '사실표가 비어 있어 투자위원회 토론을 생략했습니다'

        plan, plan_model = {}, None
        if run_com:
            par = {'com_msg': '투자위원회 소집 — 사실표 준비', 'com_frac': 0.0, 'com_det': {}, 'com_st': 'running',
                   'plan_nc': 0, 'plan_st': 'running'}
            par_lock = threading.Lock()

            def par_emit():
                # par_lock 을 잡은 상태에서 호출 — 위원회 발언 메시지 뒤에 매매계획 수신량을 붙여 한 줄로 표시
                pf = 1.0 if par['plan_st'] != 'running' else min(0.95, par['plan_nc'] / 2500)
                cf = 1.0 if par['com_st'] != 'running' else par['com_frac']
                pct = 14 + int(44 * (0.8 * cf + 0.2 * pf))
                tail = {'running': f"매매계획 {par['plan_nc']:,}자", 'done': '매매계획 완료',
                        'error': '매매계획 실패'}[par['plan_st']]
                m = par['com_msg']
                msg = f"{m} · {tail}" if ' — ' in m else f"{m} — {tail}"
                det = {'stage': 'committee' if par['com_st'] == 'running' else 'plan',
                       'stages': {'committee': par['com_st'], 'plan': par['plan_st']}}
                for k in ('round', 'speaker', 'speakers'):
                    if k in par['com_det']:
                        det[k] = par['com_det'][k]
                emit(pct, msg, det)

            def com_cb(pct, msg, detail=None):
                with par_lock:
                    par['com_frac'] = max(par['com_frac'], (pct or 0) / 100)
                    par['com_msg'] = msg or par['com_msg']
                    par['com_det'] = detail or {}
                    par_emit()

            def plan_tick(nc, nr, sec):
                with par_lock:
                    par['plan_nc'] = nc
                    par_emit()

            emit(14, f"투자위원회 토론({max(1, min(3, int(committee_rounds or 2)))}라운드)과 Grok 매매 계획을 동시에 시작합니다",
                 {'stage': 'committee', 'stages': {'committee': 'running', 'plan': 'running'}})
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_plan = ex.submit(self._grok_plan, ev, plan_tick)
                f_com = ex.submit(com_svc.run, report_id, com_cb, committee_rounds, ctx=ctx, kg=kg, facts=facts)
                for fut in as_completed([f_plan, f_com]):
                    if fut is f_plan:
                        try:
                            plan, plan_model = fut.result()
                            st = 'done'
                        except Exception as e:
                            logger.error(f"[{report_id}] Grok 매매계획 실패: {e}")
                            plan, plan_model, st = {}, None, 'error'
                        with par_lock:
                            par['plan_st'] = st
                            par_emit()
                    else:
                        try:
                            committee = fut.result()
                            st = 'done'
                        except Exception as e:
                            com_err = str(e)[:400]
                            logger.error(f"[{report_id}] 투자위원회 토론 실패: {e}")
                            committee, st = None, 'error'
                        with par_lock:
                            par['com_st'] = st
                            if st == 'error':
                                par['com_msg'] = f"투자위원회 토론 실패: {com_err[:160]}"
                            par_emit()
        else:
            if committee:
                fin = committee.get('final') or {}
                emit(14, f"투자위원회 토론 결과 재사용 — {str(committee.get('created_at') or '').replace('T', ' ')} · "
                         f"{committee.get('rounds_held')}라운드 · 정정 {len(committee.get('corrections') or [])}건 "
                         f"(사실표 동일)", {'stage': 'committee', 'stages': {'committee': 'done', 'plan': 'pending'}})
            else:
                emit(14, f"투자위원회 토론 생략 — {com_err or '알 수 없는 이유'}",
                     {'stage': 'committee', 'stages': {'committee': 'error', 'plan': 'pending'}})
            emit(15, 'Grok 이 매매 계획(진입·목표·손절·비중)을 설계하는 중', {'stage': 'plan'})
            try:
                plan, plan_model = self._grok_plan(ev, on_tick=lambda nc, nr, sec: emit(
                    15 + min(40, nc // 60), f"Grok 매매 계획 작성 중 — 수신 {nc:,}자 ({sec:.0f}초)", {'stage': 'plan'}))
            except Exception as e:
                logger.error(f"[{report_id}] Grok 매매계획 실패: {e}")
        plan = plan if isinstance(plan, dict) else {}
        emit(58, f"매매 계획 완료 — 전략 {plan.get('strategy', '?')} · 목표가 {plan.get('target_price', '?')}", {'stage': 'plan_done'})

        jev, jev_err = None, None
        n_com_q = len(self._committee_questions(committee, 'boolean'))
        n_q = 4 + len(TECH_PROPS) + min(5, len(kg.get('cqs') or [])) + min(4, len(kg.get('adrs') or [])) + n_com_q
        route = 'TypeSafe 직접(NOUL 보정 확률)' if self.ts_key else 'Vercel Gateway'
        emit(62, f"JEV 가 {n_q}개 질문을 독립 채점하는 중" + (f" (위원회 쟁점 {n_com_q}개 포함)" if n_com_q else '') + f" — {route}",
             {'stage': 'jev'})
        t0 = _t.time()
        try:
            # 앵커링 방지: JEV 에는 Grok 의 결론·위원회 점수/결론을 주지 않고 사실과 양측 논거만 전달
            jev = self._jev(self._state_text(ev, committee=committee), kg, committee)
        except Exception as e:
            jev_err = str(e)[:400]
            logger.error(f"[{report_id}] JEV 판단 실패: {e}")

        emit(88, (f"JEV 채점 완료 ({_t.time() - t0:.0f}초)" if jev else f"JEV 실패 — Grok 추정으로 대체: {jev_err}"), {'stage': 'combine'})
        answers = (jev or {}).get('answers') or {}

        def choice_probs(name, keys, fallback):
            a = answers.get(name)
            if isinstance(a, dict) and (a.get('probabilities') or a.get('choice')):
                p = _norm_probs(a.get('probabilities') or {}, keys, a.get('choice'))
                if sum(p.values()) > 0:
                    return p, 'jev', _clamp01(a.get('confidence'))
            # Grok 계획 값 — 영문 키(up/buy)·한글 키(상승/매수) 모두 허용
            labels = DIRECTIONS if name == 'direction' else STRATEGIES
            inv = {v: k for k, v in labels.items()}
            fb = {}
            for k, v in (fallback if isinstance(fallback, dict) else {}).items():
                kk = inv.get(str(k), str(k).lower())
                if kk in keys:
                    try:
                        fb[kk] = float(v or 0)
                    except (TypeError, ValueError):
                        pass
            choice = inv.get(str(plan.get('strategy') or ''), str(plan.get('strategy') or '').lower()) if name == 'strategy' else None
            p = _norm_probs(fb, keys, choice if choice in keys else None)
            if sum(p.values()) > 0:
                return p, 'grok', None
            # JEV·Grok 모두 확률이 없으면: 방향은 에이전트 투표, 그래도 없으면 균등 (0% 로 저장하지 않음)
            if name == 'direction':
                v = (ev.get('ctx') or {}).get('votes') or {}
                p = _norm_probs({'up': v.get('buy_pct') or 0, 'flat': v.get('hold_pct') or 0, 'down': v.get('sell_pct') or 0}, keys)
                if sum(p.values()) > 0:
                    logger.warning(f"[{report_id}] 방향 확률 없음 — 에이전트 투표로 대체")
                    return p, 'agents', None
            logger.warning(f"[{report_id}] {name} 확률 없음 — 균등 분포로 대체")
            return {k: 1.0 / len(keys) for k in keys}, 'prior', None

        dir_p, dir_src, dir_conf = choice_probs('direction', list(DIRECTIONS), plan.get('direction_probs'))
        str_p, str_src, str_conf = choice_probs('strategy', list(STRATEGIES), plan.get('strategy_probs'))

        def score_pct(name):
            a = answers.get(name)
            if isinstance(a, dict) and isinstance(a.get('score'), (int, float)):
                return round(float(a['score']) / 3 * 100, 1), _pct(_clamp01(a.get('confidence')))
            return None, None

        risk_pct, risk_conf = score_pct('risk')
        conv_pct, conv_conf = score_pct('conviction')

        technical = []
        tech_plan = plan.get('technical') or {}
        for key_, label, prop in TECH_PROPS:
            jp = _read_prob(answers.get(f'tech_{key_}'))
            gp = tech_plan.get(key_) or {}
            gscore = gp.get('score') if isinstance(gp, dict) else None
            technical.append({
                'key': key_, 'label': label, 'proposition': prop,
                'jev_pct': _pct(jp),
                'grok_pct': round(float(gscore), 1) if isinstance(gscore, (int, float)) else None,
                'note': (gp.get('note') if isinstance(gp, dict) else '') or '',
            })

        cq_checks = [{'id': c['id'], 'question': c['question'], 'jev_pct': _pct(_read_prob(answers.get(f"cq_{c['id']}")))}
                     for c in (kg.get('cqs') or [])[:5]]
        adr_checks = [{'id': a['id'], 'title': a['title'], 'confidence': a['confidence'],
                       'jev_pct': _pct(_read_prob(answers.get(f"adr_{a['id']}")))}
                      for a in (kg.get('adrs') or [])[:4]]

        # ── 최종 결론 (JEV 우선, 없으면 Grok)
        best_strategy = max(str_p, key=str_p.get)
        best_dir = max(dir_p, key=dir_p.get)
        # 기술적 종합 점수: 긍정 명제 평균 - 과열 위험 가중
        pos = [t['jev_pct'] if t['jev_pct'] is not None else t['grok_pct'] for t in technical if t['key'] != 'overbought']
        pos = [p for p in pos if p is not None]
        ob = next((t['jev_pct'] if t['jev_pct'] is not None else t['grok_pct'] for t in technical if t['key'] == 'overbought'), None)
        tech_score = None
        if pos:
            tech_score = round(sum(pos) / len(pos) * 0.8 + (100 - (ob if ob is not None else 50)) * 0.2, 1)
        buy_side = round((str_p['buy'] + str_p['accumulate']) * 100, 1)
        sell_side = round((str_p['reduce'] + str_p['sell']) * 100, 1)

        def num(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        entry, target, stop = num(plan.get('entry_price')), num(plan.get('target_price')), num(plan.get('stop_price'))
        base = entry or last
        plan_fixes = []
        long_target = None
        # ── 매매계획 상식 점검: 손절은 변동성의 1.5배(최소 2%) 이상 아래, 목표는 진입가보다 위, 호가 단위로 반올림
        if base:
            vol = num(price.get('daily_vol_20d_pct')) or 2.0
            min_gap = max(2.0, round(1.5 * vol, 2))
            if not stop or stop >= base * (1 - min_gap / 100) or stop <= base * 0.5:
                new_stop = _tick(base * (1 - min(max(min_gap, 3.0), 15.0) / 100), down=True)
                plan_fixes.append(f"손절가 {int(stop) if stop else '없음'} → {int(new_stop):,}원 (진입가 대비 변동성 1.5배·최소 2% 여유)")
                stop = new_stop
            if not target or target <= base * 1.01 or target >= base * 2.5:
                gap = (base - stop) / base
                new_target = _tick(base * (1 + max(2 * gap, 0.04)), down=False)
                plan_fixes.append(f"목표가 {int(target) if target else '없음'} → {int(new_target):,}원 (손익비 2:1 이상)")
                target = new_target
            # 매매 기간 안에서 통계적으로 가능한 범위(일간 변동성 × √거래일 × 1.5σ)를 넘는 목표는 기간 목표로 낮춤.
            # (12개월 목표주가는 리포트 값으로 따로 보고)
            days = _horizon_days(plan.get('horizon'))
            reach = 1.5 * (vol / 100) * (days ** 0.5)
            cap = _tick(base * (1 + reach), down=True)
            if target and target > cap and cap > base * 1.02:
                plan_fixes.append(f"목표가 {int(target):,} → {int(cap):,}원 (기간 {days}거래일 내 1.5σ 범위 · "
                                  f"12개월 목표는 리포트 {int(target):,}원)")
                long_target = target
                target = cap
            entry = _tick(entry, down=False) if entry else entry

        def rel(x):
            return round((x / base - 1) * 100, 2) if x and base else None

        up_pct, dn_pct = rel(target), rel(stop)
        rr = round(abs(up_pct / dn_pct), 2) if up_pct and dn_pct else None
        win = num(plan.get('win_rate_pct'))
        if dir_src == 'jev':
            win = round(dir_p['up'] * 100, 1) if best_strategy in ('buy', 'accumulate') else win
        exp_ret = round((win / 100) * up_pct + (1 - win / 100) * dn_pct, 2) if (win is not None and up_pct is not None and dn_pct is not None) else num(plan.get('expected_return_pct'))

        # ── 투자위원회 결과 (수치에 섞지 않고 나란히 보고)
        com_block, agreement = None, None
        if committee:
            fin = committee.get('final') or {}
            key_issues = []
            for k in fin.get('key_issues') or []:
                if not isinstance(k, dict):
                    continue
                item = {x: k.get(x) for x in ('id', 'issue', 'leaning', 'why', 'bull_view', 'bear_view', 'status')}
                item['jev_bull_pct'] = _pct(_read_prob(answers.get(_issue_key(k.get('id')))))
                key_issues.append(item)
            com_block = {
                'ok': True, 'cached': com_cached,
                'created_at': committee.get('created_at'),
                'rounds': committee.get('rounds_held'),
                'bull_score': fin.get('bull_score'), 'bear_score': fin.get('bear_score'),
                'consensus': fin.get('consensus'), 'consensus_label': fin.get('consensus_label'),
                'summary': fin.get('summary') or '',
                'recommended_strategy': fin.get('recommended_strategy'), 'recommended_label': fin.get('recommended_label'),
                'position_cap_pct': fin.get('position_cap_pct'),
                'key_issues': key_issues,
                'unresolved': fin.get('unresolved') or [],
                'corrections_count': len(committee.get('corrections') or []),
                'facts_count': len(committee.get('facts') or []),
                'jev_bull_pct': _pct(_read_prob(answers.get('committee_bull'))),
            }
            agreement = self._agreement(fin, best_strategy, str_src)

        result = {
            'report_id': report_id,
            'ticker': ctx['ticker'], 'company_name': ctx['company_name'],
            'created_at': datetime.now().isoformat(timespec='seconds'),
            'engines': {
                'jev': {'ok': jev is not None, 'model': (jev or {}).get('_model'), 'via': (jev or {}).get('_via'), 'error': jev_err},
                'grok': {'ok': bool(plan), 'model': plan_model},
                'committee': {'ok': committee is not None, 'model': (committee or {}).get('model'),
                              'cached': com_cached, 'error': com_err,
                              'rounds': (committee or {}).get('rounds_held'),
                              'elapsed_sec': (committee or {}).get('elapsed_sec')},
            },
            'verdict': STRATEGIES[best_strategy],
            'verdict_key': best_strategy,
            'verdict_pct': round(str_p[best_strategy] * 100, 1),
            'direction': DIRECTIONS[best_dir],
            'direction_probs': {DIRECTIONS[k]: round(v * 100, 1) for k, v in dir_p.items()},
            'direction_source': dir_src, 'direction_confidence_pct': _pct(dir_conf),
            'strategy_probs': {STRATEGIES[k]: round(v * 100, 1) for k, v in str_p.items()},
            'strategy_source': str_src, 'strategy_confidence_pct': _pct(str_conf),
            'buy_side_pct': buy_side, 'sell_side_pct': sell_side, 'hold_pct': round(str_p['hold'] * 100, 1),
            'risk_pct': risk_pct, 'risk_confidence_pct': risk_conf,
            'conviction_pct': conv_pct, 'conviction_confidence_pct': conv_conf,
            'technical_score_pct': tech_score,
            'technical': technical,
            'plan': {
                'horizon': plan.get('horizon'),
                'current_price': last,
                'entry_price': entry, 'target_price': target, 'stop_price': stop,
                'target_pct': up_pct, 'stop_pct': dn_pct, 'risk_reward': rr,
                'position_pct': num(plan.get('position_pct')),
                'win_rate_pct': win, 'expected_return_pct': exp_ret,
                'execution': plan.get('execution') or [],
                'fixes': plan_fixes,
                'long_target_price': long_target,
            },
            'headline': plan.get('headline') or f"{STRATEGIES[best_strategy]} {round(str_p[best_strategy] * 100)}%",
            'reasons': plan.get('reasons') or [],
            'risks': plan.get('risks') or [],
            'cq_checks': cq_checks, 'adr_checks': adr_checks,
            'kg_used': bool(kg),
            'committee': com_block,
            'agreement': agreement,
        }
        # Grok 계획 자체의 방향·전략 (JEV 와 따로 채점하기 위해 보존)
        gdp = plan.get('direction_probs') if isinstance(plan.get('direction_probs'), dict) else None
        if gdp:
            result['grok_direction_probs'] = {DIRECTIONS.get(k, k): v for k, v in gdp.items()}
        if plan.get('strategy'):
            result['grok_strategy'] = plan.get('strategy')
        # 몬테카를로 (2단계 시뮬레이션 결과에 있으면) · 과거 성적 기반 보정 확률
        report_json = _read_json(os.path.join(_reports_dir(), report_id, 'report.json')) or {}
        mc = self._monte_carlo(ctx, report_json, result.get('plan'),
                               result.get('verdict_key') in ('buy', 'accumulate'))
        if mc:
            result['monte_carlo'] = mc
            result['mc_direction_probs'] = mc.get('direction_probs')
        try:
            from . import prediction_ledger as ledger
            sources = ledger.sources_from_decision(result, ctx.get('votes'), mc)
            result['calibration'] = ledger.calibrate(sources, result.get('strategy_probs'))
        except Exception as e:
            logger.warning(f"[{report_id}] 보정 확률 계산 실패(무시): {e}")
        out = os.path.join(_reports_dir(), report_id, 'decision.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        try:
            from . import prediction_ledger as ledger
            ledger.record_decision(report_id, result, report_json, ctx.get('votes'), mc)
        except Exception as e:
            logger.warning(f"[{report_id}] 예측 원장 기록 실패(무시): {e}")
        emit(100, f"판단 완료 — {result['verdict']} {result['verdict_pct']}%"
             + (f" · 투자위원회와 {agreement['label']}" if agreement else ''), {'stage': 'done'})
        return result
