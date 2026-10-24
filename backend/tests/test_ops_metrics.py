"""운영 지표 — LLM 호출 계측(비스트리밍·스트리밍·실패), 스레드풀 단계 전달, 단계 소요 시간, 요약"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS

from tests.base import TempUploadCase


class _Dummy:
    _client = NS(base_url='https://api.x.ai/v1')

    def create(self, **kw):
        if kw.get('fail'):
            raise RuntimeError('boom')
        if kw.get('stream'):
            def gen():
                for t in ('안녕', '하세요'):
                    yield NS(choices=[NS(delta=NS(content=t, reasoning_content=None))], usage=None)
                if kw.get('with_usage'):
                    yield NS(choices=[], usage=NS(prompt_tokens=10, completion_tokens=4, total_tokens=14, completion_tokens_details=None))
            return gen()
        return NS(usage=NS(prompt_tokens=1000, completion_tokens=200, total_tokens=1500,
                           completion_tokens_details=NS(reasoning_tokens=300)), choices=[])


class OpsMetricsTest(TempUploadCase):
    def setUp(self):
        super().setUp()
        from app.services import ops_metrics as O
        self.O = O
        self.cls = type('Dummy', (_Dummy,), {})
        self.cls.create = O._wrap_create(_Dummy.create)

    def rows(self):
        c = self.O._db()
        try:
            return [dict(r) for r in c.execute('SELECT * FROM llm_calls ORDER BY id')]
        finally:
            c.close()

    def test_prices(self):
        self.assertEqual(self.O.price_for('grok-4-1-fast-non-reasoning'), (0.20, 0.50))
        self.assertEqual(self.O.price_for('xai/grok-4.7'), (2.00, 6.00))
        self.assertIsNone(self.O.price_for('llama-3'))
        os.environ['LLM_PRICES'] = json.dumps({'llama': [0.1, 0.2]})
        try:
            self.assertEqual(self.O.price_for('llama-3'), (0.1, 0.2))
        finally:
            os.environ.pop('LLM_PRICES')
        self.assertEqual(self.O.cost_usd('grok-4.7', 1_000_000, 500_000), 5.0)

    def test_llm_calls_recorded_with_stage(self):
        self.O.install()
        d = self.cls()
        with self.O.stage('report', 'r1'):
            d.create(model='grok-4.7', messages=[{'role': 'user', 'content': 'x'}])
            with self.assertRaises(RuntimeError):
                d.create(model='grok-4.7', fail=True)
            with ThreadPoolExecutor(2) as ex:
                ex.submit(lambda: d.create(model='grok-4-1-fast', messages=[])).result()
            out = ''.join(c.choices[0].delta.content for c in d.create(model='grok-4-1-fast', stream=True,
                                                                       messages=[{'role': 'user', 'content': 'z' * 22}]) if c.choices)
            for _ in d.create(model='grok-4-1-fast', stream=True, with_usage=True, messages=[]):
                pass
        self.assertEqual(out, '안녕하세요')
        rows = self.rows()
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r['stage'] == 'report' for r in rows))
        self.assertEqual(rows[0]['host'], 'api.x.ai')
        self.assertAlmostEqual(rows[0]['cost_usd'], 1000 / 1e6 * 2 + 500 / 1e6 * 6, places=9)   # xAI: 추론 토큰 별도 청구
        self.assertEqual(rows[1]['ok'], 0)
        self.assertEqual((rows[3]['stream'], rows[3]['est'], rows[3]['prompt_tokens']), (1, 1, 10))
        self.assertEqual((rows[4]['est'], rows[4]['completion_tokens']), (0, 4))

    def test_stage_runs_and_summary(self):
        def boom():
            raise ValueError('x')
        with self.assertRaises(ValueError):
            self.O.staged('decision', 'r1', boom)()
        self.O.staged('kg', 'r1', lambda: time.sleep(0.02))()
        with self.O.stage('report', 'r1'):
            self.cls().create(model='grok-4.7', messages=[])
        s = self.O.summary(7)
        st = {x['stage']: x for x in s['stages']}
        self.assertEqual(st['decision']['failed'], 1)
        self.assertEqual([x['stage'] for x in s['stages']], ['report', 'kg', 'decision'])
        self.assertEqual(s['total']['calls'], 1)
        self.assertEqual(s['total']['cost_krw'], round(s['total']['cost_usd'] * s['usd_krw']))
        self.assertEqual(s['daily'][-1]['calls'], 1)
        self.assertTrue(s['recent_stage_failures'])


class StreamUsageRequestTest(TempUploadCase):
    """스트리밍 호출에 include_usage 를 요청하고, 거절하는 공급자는 기억해 빼고 다시 부름"""

    def _client(self, reject):
        calls = []

        class Comp:
            def create(self, **kw):
                calls.append(dict(kw))
                if reject and 'stream_options' in kw:
                    raise RuntimeError("Error code: 400 - unknown field 'stream_options'")
                return iter([NS(choices=[NS(delta=NS(content='ok', reasoning_content=None))], usage=None)])
        client = NS(base_url=f'https://example-{reject}.test/v1', chat=NS(completions=Comp()))
        return client, calls

    def test_include_usage_and_fallback(self):
        from app.utils import llm_client as LC
        c, calls = self._client(False)
        self.assertEqual(LC.stream_completion(c, {'model': 'm', 'messages': []}), 'ok')
        self.assertEqual(calls[0].get('stream_options'), {'include_usage': True})
        c, calls = self._client(True)
        self.assertEqual(LC.stream_completion(c, {'model': 'm', 'messages': []}), 'ok')
        self.assertEqual(len(calls), 2)
        self.assertNotIn('stream_options', calls[1])
        LC.stream_completion(c, {'model': 'm', 'messages': []})       # 두 번째부터는 바로 빼고 호출
        self.assertEqual(len(calls), 3)
        self.assertNotIn('stream_options', calls[2])
