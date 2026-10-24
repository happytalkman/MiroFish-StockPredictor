"""5단계 판단 결합 — 근거 묶음이 Grok 계획·JEV 로 그대로 전달되는지, 확률이 0% 로 저장되지 않는지

(2026-09-28 회귀: 위원회 진행 여부를 확인하던 변수가 근거 묶음 ev 를 덮어써 Grok 계획·JEV 가 모두 실패하고
 방향 확률 0% 가 저장됨 — 이 테스트가 같은 실수를 막음)
"""
import json
import os
import threading

from tests.base import TempUploadCase

EV = {
    'ctx': {'ticker': '005930', 'company_name': '삼성전자', 'simulation_id': '',
            'price': {'last_close': 80000.0, 'daily_vol_20d_pct': 2.0},
            'votes': {'buy_pct': 50, 'hold_pct': 30, 'sell_pct': 20, 'num_agents': 50}},
    'kg': {}, 'judgment': {}, 'facts': [],
}
PLAN = {'strategy': 'accumulate', 'direction_probs': {'up': 55, 'flat': 30, 'down': 15},
        'strategy_probs': {'buy': 20, 'accumulate': 50, 'hold': 20, 'reduce': 5, 'sell': 5},
        'entry_price': 80000, 'target_price': 88000, 'stop_price': 76000, 'horizon': '4~8주', 'position_pct': 30}


class _NoCommittee:
    def __init__(self, *a, **k):
        pass

    @staticmethod
    def load_cached(_rid):
        return None


class DecisionCombineTest(TempUploadCase):
    def setUp(self):
        super().setUp()
        import app.services.stock_decision as SD
        self.SD = SD
        self._orig = {k: getattr(SD, k) for k in ('inflight_event', 'InvestmentCommittee')}
        SD.InvestmentCommittee = _NoCommittee
        d = os.path.join(self.tmp, 'stock_reports', 'r1')
        os.makedirs(d)
        with open(os.path.join(d, 'report.json'), 'w', encoding='utf-8') as f:
            json.dump({'ticker': '005930', 'company_name': '삼성전자',
                       'summary': {'as_of': '2026-09-28', 'last_close': 80000}}, f, ensure_ascii=False)
        self.seen = []
        sd = SD.StockDecision.__new__(SD.StockDecision)
        sd.judge, sd.gw_key, sd.ts_key = None, '', ''
        sd._evidence = lambda rid: EV
        sd._state_text = lambda ev, *a, **k: (self.seen.append(('state', type(ev).__name__)), 'state')[1]

        def plan(ev, on_tick=None):
            self.seen.append(('plan', type(ev).__name__))
            return dict(PLAN), 'grok-test'
        sd._grok_plan = plan
        self.sd = sd

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(self.SD, k, v)
        super().tearDown()

    def run_decide(self, jev):
        self.sd._jev = jev
        return self.sd.decide('r1')

    def test_evidence_reaches_plan_and_jev(self):
        for inflight in (None, threading.Event()):
            if inflight is not None:
                inflight.set()
            self.SD.inflight_event = lambda rid, e=inflight: e
            self.seen.clear()
            res = self.run_decide(lambda state, kg, committee=None: {'answers': {
                'direction': {'probabilities': {'up': 0.6, 'flat': 0.25, 'down': 0.15}, 'confidence': 0.7},
                'strategy': {'probabilities': {'buy': 0.1, 'accumulate': 0.6, 'hold': 0.2, 'reduce': 0.05, 'sell': 0.05}}}})
            self.assertIn(('plan', 'dict'), self.seen)
            self.assertTrue(all(t == 'dict' for _k, t in self.seen), self.seen)
            self.assertEqual(res['direction_source'], 'jev')
            self.assertAlmostEqual(sum(res['direction_probs'].values()), 100, delta=0.2)
            self.assertEqual(res['verdict'], '분할매수')

    def test_jev_failure_falls_back_to_grok_then_votes(self):
        self.SD.inflight_event = lambda rid: None

        def boom(*a, **k):
            raise RuntimeError('JEV down')
        res = self.run_decide(boom)
        self.assertEqual(res['direction_source'], 'grok')
        self.assertEqual(res['direction_probs']['상승'], 55.0)
        # Grok 계획에도 확률이 없으면 에이전트 투표 → 절대 0% 로 저장하지 않음
        self.sd._grok_plan = lambda ev, on_tick=None: ({'strategy': 'hold'}, 'grok-test')
        res = self.run_decide(boom)
        self.assertEqual(res['direction_source'], 'agents')
        self.assertEqual(res['direction_probs']['상승'], 50.0)
        self.assertEqual(res['verdict'], '관망')
        self.assertGreater(res['verdict_pct'], 0)


class JevMetricsTest(TempUploadCase):
    """JEV(HTTP 직접 호출)도 운영 지표에 기록되는지"""

    def test_jev_call_recorded(self):
        import app.services.stock_decision as SD
        from app.services import ops_metrics

        class Resp:
            status_code = 200

            def json(self):
                return {'answers': {}, 'usage': {'input_tokens': 1200, 'output_tokens': 80}}
        orig = SD.requests.post
        SD.requests.post = lambda *a, **k: Resp()
        try:
            sd = SD.StockDecision.__new__(SD.StockDecision)
            sd.judge, sd.gw_key, sd.ts_key = None, '', 'test-key'
            sd._jev('상태 요약', {}, None)
        finally:
            SD.requests.post = orig
        c = ops_metrics._db()
        try:
            rows = [dict(r) for r in c.execute("SELECT * FROM llm_calls WHERE model LIKE 'jev:%'")]
        finally:
            c.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['prompt_tokens'], rows[0]['completion_tokens'], rows[0]['est'], rows[0]['ok']), (1200, 80, 0, 1))
