"""시장 API 입력 검증."""

from unittest.mock import patch

from flask import Flask

from tests.base import TempUploadCase


class MarketApiTest(TempUploadCase):
    def setUp(self):
        super().setUp()
        from app.api.market import market_bp

        app = Flask(__name__)
        app.register_blueprint(market_bp)
        app.testing = True
        self.client = app.test_client()

    def test_chart_accepts_normalized_symbol_and_bounded_count(self):
        with patch('app.api.market.md.cached', return_value=[{'c': 70000}]) as cached:
            response = self.client.get('/api/market/chart/kospi?count=120')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(cached.call_args.args[:3], ('chart:KOSPI:120', 300, cached.call_args.args[2]))
        self.assertEqual(cached.call_args.args[3:], ('KOSPI', 120))

    def test_chart_rejects_invalid_symbol_and_count(self):
        for url in (
            '/api/market/chart/not-a-symbol',
            '/api/market/chart/005930?count=1',
            '/api/market/chart/005930?count=501',
            '/api/market/chart/005930?count=many',
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()['success'])

    def test_calendar_rejects_unbounded_days(self):
        for value in ('0', '181', 'soon'):
            with self.subTest(value=value):
                response = self.client.get(f'/api/market/calendar?days={value}')
                self.assertEqual(response.status_code, 400)

    def test_enum_parameters_are_explicit(self):
        for url in (
            '/api/market/movers?market=NYSE',
            '/api/market/recommend?market=NYSE',
            '/api/market/news?cat=unknown',
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 400)
