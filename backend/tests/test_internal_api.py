"""Cloudflare Worker Cron 내부 API 인증."""

import os

from flask import Flask

from tests.base import TempUploadCase


class InternalApiTest(TempUploadCase):
    def setUp(self):
        super().setUp()
        from app.api.internal import internal_bp

        self._secret = os.environ.get('CRON_SECRET')
        os.environ['CRON_SECRET'] = 'test-cron-secret-that-is-long-enough'
        app = Flask(__name__)
        app.register_blueprint(internal_bp)
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        if self._secret is None:
            os.environ.pop('CRON_SECRET', None)
        else:
            os.environ['CRON_SECRET'] = self._secret
        super().tearDown()

    def test_wake_requires_matching_secret(self):
        self.assertEqual(self.client.post('/api/internal/cron/wake').status_code, 404)
        self.assertEqual(
            self.client.post('/api/internal/cron/wake', headers={'X-WeAid-Cron': 'wrong'}).status_code,
            404,
        )

        response = self.client.post(
            '/api/internal/cron/wake',
            headers={'X-WeAid-Cron': os.environ['CRON_SECRET']},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['data']['job'], 'wake')

    def test_short_configured_secret_disables_endpoint(self):
        os.environ['CRON_SECRET'] = 'too-short'

        response = self.client.post(
            '/api/internal/cron/wake',
            headers={'X-WeAid-Cron': 'too-short'},
        )

        self.assertEqual(response.status_code, 404)
