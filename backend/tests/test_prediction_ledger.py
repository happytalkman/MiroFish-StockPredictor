"""예측 원장 — 채점(방향·매매계획), 보정 학습, 요약"""
import random
from datetime import datetime, timedelta

from tests.base import TempUploadCase


def candles(start, closes, highs=None, lows=None):
    day, out = datetime.strptime(start, '%Y-%m-%d'), []
    for k, c in enumerate(closes):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        out.append({'d': day.strftime('%Y%m%d'), 'o': c, 'h': (highs or closes)[k], 'l': (lows or closes)[k], 'c': c, 'v': 1})
        day += timedelta(days=1)
    return out


def decision(tid, dirp, strategy='accumulate', entry=100.0, target=110.0, stop=95.0):
    return {'ticker': tid, 'company_name': 'T' + tid, 'created_at': '2026-06-01T10:00:00', 'direction_source': 'jev',
            'engines': {'jev': {'ok': True}}, 'direction_probs': dirp, 'grok_direction_probs': {'상승': 50, '보합': 30, '하락': 20},
            'verdict_key': strategy, 'strategy_probs': {'분할매수': 100, '매수': 0, '관망': 0, '비중축소': 0, '매도': 0},
            'plan': {'current_price': entry, 'entry_price': entry, 'target_price': target, 'stop_price': stop,
                     'horizon': '4~8주', 'position_pct': 40},
            'committee': {'bull_score': 70, 'bear_score': 50, 'recommended_strategy': 'accumulate'}}


REP = {'summary': {'as_of': '2026-06-01', 'last_close': 100.0, 'rating': '매수', 'target_price': 130}}
VOTES = {'buy_pct': 50, 'hold_pct': 20, 'sell_pct': 30}


class LedgerTest(TempUploadCase):
    def setUp(self):
        super().setUp()
        from app.services import prediction_ledger as L
        self.L = L
        L.record_decision('r_up', decision('000001', {'상승': 70, '보합': 20, '하락': 10}), REP, VOTES,
                          {'direction_probs': {'상승': 55, '보합': 25, '하락': 20}})
        L.record_decision('r_dn', decision('000002', {'상승': 80, '보합': 15, '하락': 5}), REP, VOTES)
        L.record_decision('r_new', decision('000003', {'상승': 40, '보합': 40, '하락': 20}),
                          dict(REP, summary=dict(REP['summary'], as_of='2026-09-20')), VOTES)
        self.series = {
            '000001': candles('2026-06-01', [100] + [101 + i for i in range(45)], highs=[100] + [102 + i for i in range(45)],
                              lows=[100] + [100 + i for i in range(45)]),
            '000002': candles('2026-06-01', [100] + [99 - i * 0.5 for i in range(45)], lows=[100] + [98 - i * 0.5 for i in range(45)]),
            '000003': candles('2026-09-18', [100, 100, 101, 102, 103, 104]),
        }
        self.fetch = lambda t, n: self.series[t][-n:]

    def test_scoring_direction_and_plan(self):
        r = self.L.score_pending(fetch=self.fetch)
        self.assertEqual(r['updated'], 3)
        up, dn, nw = (self.L.get_prediction(x) for x in ('r_up', 'r_dn', 'r_new'))
        self.assertEqual(up['realized']['class'], '상승')
        self.assertTrue(up['engines']['jev']['hit'])
        self.assertEqual(up['plan_outcome']['outcome'], 'target')
        self.assertEqual(up['plan_outcome']['r_multiple'], 2.0)
        self.assertIn('mc', up['engines'])
        self.assertEqual(dn['realized']['class'], '하락')
        self.assertFalse(dn['engines']['jev']['hit'])
        self.assertIs(dn['strategy_hit'], False)
        self.assertEqual(dn['plan_outcome']['outcome'], 'stop')
        self.assertEqual(dn['plan_outcome']['r_multiple'], -1.0)
        self.assertIn(nw['status'], ('partial', 'pending'))
        self.assertEqual(nw['mtm_pct'], 4.0)
        self.assertEqual((up['status'], dn['status']), ('scored', 'scored'))

    def test_calibration_learns_overconfidence(self):
        random.seed(1)
        for i in range(16):
            tid = f'1{i:05d}'
            self.L.record_decision(f'r_{i}', decision(tid, {'상승': 90, '보합': 7, '하락': 3}), REP, VOTES)
            path = [100] + [100 + (1 if i % 3 == 0 else -0.3) * k * 0.5 for k in range(1, 46)]
            self.series[tid] = candles('2026-06-01', path)
        self.L.score_pending(fetch=self.fetch)
        params = self.L.calibration_params(force=True)
        jev = params['engines']['jev']
        self.assertEqual(jev['method'], 'temperature')
        self.assertGreater(jev['T'], 1.2)
        cal = self.L.calibrate({'jev': {'up': 0.99, 'flat': 0.005, 'down': 0.005}, 'agents': {'up': .5, 'flat': .2, 'down': .3}},
                               {'분할매수': 100, '매수': 0, '관망': 0, '비중축소': 0, '매도': 0})
        self.assertLess(cal['engines']['jev']['상승'], 95)
        self.assertAlmostEqual(sum(cal['weights'].values()), 1, delta=0.01)
        self.assertLess(max(cal['strategy'].values()), 100)
        sm = self.L.summary()
        self.assertEqual(sm['counts']['total'], 19)
        self.assertTrue(any(e['engine'] == 'jev' and e['reliability'] for e in sm['engines']))
        self.assertGreaterEqual(sm['plan']['n'], 2)

    def test_list_and_rerecord(self):
        lst = self.L.list_predictions(ticker='000001')
        self.assertEqual(len(lst), 1)
        self.assertEqual(lst[0]['sources']['jev']['상승'], 70.0)
        self.L.score_pending(fetch=self.fetch)
        self.L.record_decision('r_up', decision('000001', {'상승': 60, '보합': 30, '하락': 10}), REP, VOTES)
        self.assertEqual(self.L.get_prediction('r_up')['status'], 'pending')

    def test_realized_class_thresholds(self):
        self.assertEqual(self.L.realized_class(3.0), 'up')
        self.assertEqual(self.L.realized_class(-3.0), 'down')
        self.assertEqual(self.L.realized_class(2.9), 'flat')
