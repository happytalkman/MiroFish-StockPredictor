"""시장 데이터 캐시의 동시 요청 병합과 장애 시 stale fallback."""

import time
from concurrent.futures import ThreadPoolExecutor

from tests.base import TempUploadCase


class MarketCacheTest(TempUploadCase):
    def setUp(self):
        super().setUp()
        from app.services import market_data

        self.md = market_data
        with self.md._cache_lock:
            self.md._cache.clear()
            self.md._cache_inflight.clear()
            self.md._cache_failures.clear()

    def tearDown(self):
        with self.md._cache_lock:
            self.md._cache.clear()
            self.md._cache_inflight.clear()
            self.md._cache_failures.clear()
        super().tearDown()

    def test_concurrent_misses_share_one_refresh(self):
        calls = 0

        def load():
            nonlocal calls
            calls += 1
            time.sleep(0.05)
            return {'price': 123}

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.md.cached('same', 60, load), range(8)))

        self.assertEqual(calls, 1)
        self.assertEqual(results, [{'price': 123}] * 8)

    def test_refresh_failure_uses_recent_stale_value(self):
        self.md._cache['stale'] = (time.time() - 2, {'price': 100})
        calls = 0

        def fail():
            nonlocal calls
            calls += 1
            raise ConnectionError('source unavailable')

        self.assertEqual(self.md.cached('stale', 1, fail), {'price': 100})
        self.assertEqual(self.md.cached('stale', 1, fail), {'price': 100})
        self.assertEqual(calls, 1)

    def test_refresh_failure_rejects_overaged_value(self):
        self.md._cache['expired'] = (time.time() - 70, {'price': 100})

        with self.assertRaisesRegex(ConnectionError, 'source unavailable'):
            self.md.cached('expired', 1, lambda: (_ for _ in ()).throw(ConnectionError('source unavailable')))
