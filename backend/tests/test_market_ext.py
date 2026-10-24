"""시장 데이터 확장 — 네이버 분기 실적·투자자별 순매수 파싱, 사실표 F#/H# 번호"""
import os

from tests.base import TempUploadCase

KEYS = ['202506', '202509', '202512', '202603', '202606', '202609']
VALS = {'매출액': ['1,500,000', '1,600,000', '1,650,000', '1,700,000', '1,714,995', '-'],
        '영업이익': ['500,000', '600,000', '700,000', '800,000', '894,924', '900,000'],
        '당기순이익': ['400,000', '450,000', '500,000', '600,000', '716,245', '-'],
        '영업이익률': ['33.3', '37.5', '42.4', '47.1', '52.18', '-']}
QUARTER = {'financeInfo': {
    'trTitleList': [{'isConsensus': 'Y' if k == '202609' else 'N', 'title': f'{k[:4]}.{k[4:]}.', 'key': k} for k in KEYS],
    'rowList': [{'title': t, 'columns': {k: {'value': v[i]} for i, k in enumerate(KEYS)}} for t, v in VALS.items()]}}
TREND = [{'bizdate': f'202609{d:02d}', 'foreignerPureBuyQuant': f'{(-1) ** d * 1000 * d:+,}', 'organPureBuyQuant': '+500',
          'individualPureBuyQuant': '-800', 'closePrice': '31,050', 'foreignerHoldRatio': f'{16 + d / 100:.2f}%'} for d in range(1, 24)]


class _R:
    def __init__(self, j):
        self._j = j

    def json(self):
        return self._j


def _fake_get(url, **_k):
    if '/finance/quarter' in url:
        return _R(QUARTER)
    if '/finance/annual' in url:
        return _R({'financeInfo': {'trTitleList': [], 'rowList': []}})
    if '/trend' in url:
        return _R(TREND)
    raise RuntimeError(url)


class MarketExtTest(TempUploadCase):
    def setUp(self):
        super().setUp()
        from app.services import market_data as md
        from app.services import market_ext as mx
        self.md, self.mx = md, mx
        self._orig_get = md._get
        md._get = _fake_get
        self._dart = os.environ.pop('DART_API_KEY', None)

    def tearDown(self):
        self.md._get = self._orig_get
        if self._dart is not None:
            os.environ['DART_API_KEY'] = self._dart
        super().tearDown()

    def test_quarterly(self):
        q = self.mx.quarterly_financials('005930')
        self.assertEqual((q['source'], q['latest']['period'], q['latest']['revenue']), ('naver', '2026.06', 1714995))
        self.assertEqual((q['yoy_base'], q['revenue_yoy_pct'], q['op_income_yoy_pct']), ('2025.06', 14.3, 79.0))
        self.assertEqual((q['qoq_base'], q['revenue_qoq_pct']), ('2026.03', 0.9))
        self.assertEqual(q['ttm']['op_income'], 600000 + 700000 + 800000 + 894924)
        self.assertEqual(q['next_estimate']['period'], '2026.09')

    def test_flows_and_facts(self):
        fl = self.mx.investor_flows('005930', None)
        self.assertEqual((fl['source'], fl['sum5']['days'], fl['sum20']['days']), ('naver', 5, 20))
        self.assertEqual(fl['foreign_ratio'], 16.23)
        self.assertIsNone(self.mx.short_balance('005930', None))
        q = self.mx.quarterly_financials('005930')
        lines = self.mx.fact_lines({'quarterly': q, 'flows': fl})
        self.assertEqual(len(lines), 4)
        from app.services import stock_evidence as sev
        facts = sev.build_fact_sheet({'price': {}, 'votes': {}}, None, None, None, extended={
            'quarterly': q, 'flows': fl,
            'monte_carlo': {'paths': 5000, 'horizon_days': 20, 'direction_probs': {'상승': 40, '보합': 30, '하락': 30},
                            'return_pct': {'p5': -10, 'p50': 1, 'p95': 12}, 'var95_pct': -9.5},
            'history': [{'text': '2026-08-01 예측: 분할매수 → 실제 +5.2% (적중)', 'date': '2026-08-01'}]})
        ids = [f['id'] for f in facts]
        self.assertEqual(ids[:5], ['F1', 'F2', 'F3', 'F4', 'F5'])
        self.assertEqual(ids[-1], 'H1')
