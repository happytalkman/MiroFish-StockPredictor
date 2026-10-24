"""몬테카를로 — 분포 요약, 재현성, 계획 선도달 확률, 예전 시뮬레이션 채우기, 판단·원장 연결"""
import json
import os
import random

from tests.base import TempUploadCase


def ohlcv(n=250, mu=0.0, sd=0.02, seed=1, start=10000):
    rng, px, rows = random.Random(seed), start, []
    for i in range(n):
        c = px * (1 + rng.gauss(mu, sd))
        rows.append({'날짜': f'd{i}', '종가': round(c), '고가': round(max(c, px) * (1 + abs(rng.gauss(0, sd / 2)))),
                     '저가': round(min(c, px) * (1 - abs(rng.gauss(0, sd / 2))))})
        px = c
    return rows


class MonteCarloTest(TempUploadCase):
    def setUp(self):
        super().setUp()
        from app.services import monte_carlo as M
        self.M = M
        self.o = ohlcv()

    def test_summary_shape(self):
        mc = self.M.simulate(self.o, 'sim_a')
        self.assertEqual((mc['paths'], mc['horizon_days']), (5000, 20))
        dp = mc['direction_probs']
        self.assertEqual(set(dp), {'상승', '보합', '하락'})
        self.assertAlmostEqual(sum(dp.values()), 100, delta=0.05)
        rp = mc['return_pct']
        self.assertTrue(rp['p5'] < rp['p25'] < rp['p50'] < rp['p75'] < rp['p95'])
        self.assertEqual(mc['var95_pct'], rp['p5'])
        self.assertLessEqual(mc['cvar95_pct'], mc['var95_pct'])
        self.assertEqual(len(mc['fan']), 20)
        self.assertGreater(mc['fan'][-1]['p95'] - mc['fan'][-1]['p5'], mc['fan'][0]['p95'] - mc['fan'][0]['p5'])

    def test_reproducible_and_regimes(self):
        a = self.M.simulate(self.o, 'sim_a')
        self.assertEqual(a['direction_probs'], self.M.simulate(self.o, 'sim_a')['direction_probs'])
        lo = self.M.simulate(ohlcv(sd=0.004, seed=3), 'x')
        self.assertGreater(lo['direction_probs']['보합'], 70)
        up = self.M.simulate(ohlcv(mu=0.004, sd=0.015, seed=4), 'x')
        self.assertGreater(up['direction_probs']['상승'], up['direction_probs']['하락'])
        self.assertIsNone(self.M.simulate(ohlcv(n=40), 'x'))

    def test_sample_filters_splits(self):
        o2 = ohlcv()
        o2[100:] = [dict(r, 종가=r['종가'] / 50, 고가=r['고가'] / 50, 저가=r['저가'] / 50) for r in o2[100:]]
        s = self.M.sample_from_ohlcv(o2)
        self.assertEqual(len(s), 248)
        self.assertTrue(all(h >= r >= lo and abs(r) < 0.35 for r, h, lo in s))

    def test_plan_probabilities(self):
        sample = self.M.sample_from_ohlcv(self.o)
        last = self.o[-1]['종가']
        pp = self.M.plan_probabilities(sample, 'sim_a', last, last, last * 1.10, last * 0.95, 30)
        self.assertAlmostEqual(pp['p_target_first'] + pp['p_stop_first'] + pp['p_horizon'], 100, delta=0.2)
        self.assertGreater(pp['p_stop_first'], pp['p_target_first'])
        near = self.M.plan_probabilities(sample, 'sim_a', last, last, last * 1.03, last * 0.80, 30)
        self.assertGreater(near['p_target_first'], 70)
        self.assertIsNone(self.M.plan_probabilities(sample, 'x', last, last, last * 0.9, last * 0.8, 20))

    def test_backfill_old_simulation_and_decision(self):
        sim_id = 'stock_sim_test1'
        os.makedirs(os.path.join(self.tmp, 'stock_simulations', sim_id))
        os.makedirs(os.path.join(self.tmp, 'stock_analyses', 'task1'))
        with open(os.path.join(self.tmp, 'stock_simulations', sim_id, 'result.json'), 'w') as f:
            json.dump({'simulation_id': sim_id, 'task_id': 'task1', 'summary': {}}, f)
        with open(os.path.join(self.tmp, 'stock_analyses', 'task1', 'analysis.json'), 'w', encoding='utf-8') as f:
            json.dump({'financial_data': {'ohlcv': self.o}}, f, ensure_ascii=False)
        mc, sample = self.M.ensure_for_simulation(sim_id)
        self.assertTrue(mc and sample)
        with open(os.path.join(self.tmp, 'stock_simulations', sim_id, 'result.json'), encoding='utf-8') as f:
            saved = json.load(f)
        self.assertIn('monte_carlo_sample', saved)
        self.assertEqual(saved['monte_carlo']['direction_probs'], self.M.simulate(self.o, sim_id)['direction_probs'])

        from app.services.stock_decision import StockDecision
        last = self.o[-1]['종가']
        plan = {'current_price': last, 'entry_price': last, 'target_price': last * 1.1, 'stop_price': last * 0.94, 'horizon': '4~8주'}
        m = StockDecision._monte_carlo({'simulation_id': sim_id}, {'summary': {'last_close': last}}, plan, True)
        self.assertGreaterEqual(m['plan']['horizon_days'], 20)
        self.assertNotIn('plan', StockDecision._monte_carlo({'simulation_id': sim_id}, {}, plan, False))

        from app.services import stock_evidence as sev
        from app.services import prediction_ledger as L
        self.assertTrue(any(x['kind'] == 'mc' for x in sev.extended_fact_lines({'monte_carlo': mc})))
        src = L.sources_from_decision({'direction_probs': {'상승': 50, '보합': 30, '하락': 20}}, None, mc)
        self.assertAlmostEqual(sum(src['mc'].values()), 1, places=6)
