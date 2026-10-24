"""누적 온톨로지 — 적재·공유 개념·Graph RAG 과거 맥락·SHACL 검증·Turtle 내보내기"""
import json
import os

from tests.base import TempUploadCase

HBM = {'id': 'hbm', 'label': 'HBM', 'layer': 'semantic', 'type': 'Entity', 'value': ''}
AI = {'id': 'ai_srv', 'label': 'AI 서버 수요', 'layer': 'semantic', 'type': 'ObjectType', 'value': ''}
PEER = {'id': 'skh', 'label': 'SK하이닉스', 'layer': 'semantic', 'type': 'Entity', 'value': ''}
DEC = {'created_at': '2026-06-01T10:00:00', 'verdict': '분할매수', 'direction_probs': {'상승': 55, '보합': 30, '하락': 15},
       'plan': {'entry_price': 80000, 'target_price': 90000, 'stop_price': 75000, 'position_pct': 30, 'horizon': '4~8주'}}


class OntologyTest(TempUploadCase):
    def setUp(self):
        super().setUp()
        from app.services import ontology_store as O
        self.O = O
        self._orig_by_name = O._stock_by_name
        O._stock_by_name = lambda label: {'SK하이닉스': '000660'}.get(label.strip())
        self.mk('stock_report_a1', '005930', '삼성전자', '2026-06-01T09:00:00', [HBM, AI, PEER],
                adrs=[{'id': 'ADR_001', 'title': 'HBM 점유율 회복', 'decision': '비중 확대', 'confidence': 72, 'status': '채택'}], dec=DEC)
        self.mk('stock_report_a2', '005930', '삼성전자', '2026-09-20T09:00:00', [HBM])
        self.mk('stock_report_b1', '000660', 'SK하이닉스', '2026-06-02T09:00:00', [dict(HBM), dict(AI)])
        self.mk('stock_report_c1', '035420', 'NAVER', '2026-06-03T09:00:00',
                [{'id': 'ad', 'label': '광고 경기', 'layer': 'semantic', 'type': 'Entity', 'value': ''}])

    def tearDown(self):
        self.O._stock_by_name = self._orig_by_name
        super().tearDown()

    def mk(self, rid, ticker, name, created, extra, adrs=None, dec=None):
        d = os.path.join(self.tmp, 'stock_reports', rid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'report.json'), 'w', encoding='utf-8') as f:
            json.dump({'ticker': ticker, 'company_name': name, 'created_at': created,
                       'summary': {'rating': '매수', 'target_price': 100000, 'last_close': 80000, 'upside_pct': 25.0,
                                   'valuation_method': 'PER', 'as_of': created[:10]}}, f, ensure_ascii=False)
        nodes = [{'id': 'stock', 'label': f'{name} ({ticker})', 'layer': 'semantic', 'type': 'ObjectType', 'value': ''},
                 {'id': 'market_kr', 'label': '한국 주식시장', 'layer': 'semantic', 'type': 'Entity', 'value': ''},
                 {'id': 'r_val', 'label': '보고서 · 밸류에이션', 'layer': 'semantic', 'type': 'Entity', 'value': ''},
                 {'id': 's_rsi', 'label': 'RSI 신호', 'layer': 'kinetic', 'type': 'Signal', 'value': '중립'}] + extra
        edges = [{'source': 'stock', 'target': 'market_kr', 'predicate': 'listedOn', 'layer': 'semantic'},
                 {'source': 'r_val', 'target': 'stock', 'predicate': 'describes', 'layer': 'semantic'},
                 {'source': 's_rsi', 'target': 'stock', 'predicate': 'signals', 'layer': 'kinetic'}]
        edges += [{'source': 'stock', 'target': n['id'], 'predicate': 'exposed to', 'layer': 'semantic'} for n in extra]
        with open(os.path.join(d, 'kg.json'), 'w', encoding='utf-8') as f:
            json.dump({'ticker': ticker, 'company_name': name, 'nodes': nodes, 'edges': edges,
                       'cqs': [{'id': 'CQ1', 'question': '실적 개선은 지속되나?', 'answer': '예'}], 'adrs': adrs or []}, f, ensure_ascii=False)
        if dec:
            with open(os.path.join(d, 'decision.json'), 'w', encoding='utf-8') as f:
                json.dump(dec, f, ensure_ascii=False)

    def test_ingest_idempotent_and_backfill(self):
        r = self.O.ingest_report('stock_report_a1')
        self.assertTrue(r['ok'])
        self.assertGreater(r['triples'], 20)
        self.assertTrue(self.O.ingest_report('stock_report_a1').get('skipped'))
        self.assertEqual(self.O.ingest_report('stock_report_a1', force=True)['triples'], r['triples'])
        b = self.O.backfill()
        self.assertEqual((b['ingested'], b['skipped']), (3, 1))

    def test_shared_concepts_and_generic_filter(self):
        self.O.backfill()
        st = self.O.stats()
        self.assertEqual(st['stocks'], 3)
        labels = {x['label']: x['stocks'] for x in st['shared_concepts']}
        self.assertEqual(labels.get('HBM'), 2)
        self.assertFalse(any(v < 2 for v in labels.values()))                    # 두 종목 이상만
        nb_kw = self.O.neighborhood('keyword/hbm')
        self.assertTrue({'stock/005930', 'stock/000660'} <= {n['id'] for n in nb_kw['nodes']})   # 약어 키워드 다리
        self.assertFalse(any('주식시장' in k or k.startswith('보고서') for k in labels))
        ids = [x['id'] for x in self.O.search('SK하이닉스')]
        self.assertIn('stock/000660', ids)
        nb = self.O.neighborhood('concept/hbm')
        self.assertTrue({'stock/005930', 'stock/000660'} <= {n['id'] for n in nb['nodes']})
        self.assertTrue(all(self.O.PRED_RE.match(e['p']) for e in nb['edges']))

    def test_history_facts_graph_rag(self):
        from app.services import prediction_ledger as L
        self.O.backfill()
        L.record_decision('stock_report_a1', dict(DEC, ticker='005930', company_name='삼성전자', direction_source='jev',
                                                  engines={'jev': {'ok': True}}, verdict_key='accumulate'),
                          {'summary': {'as_of': '2026-06-01', 'last_close': 80000}}, {})
        os.makedirs(os.path.join(self.tmp, 'themes'), exist_ok=True)
        with open(os.path.join(self.tmp, 'themes', '2026-09-25.json'), 'w', encoding='utf-8') as f:
            json.dump({'themes': [{'name': 'HBM 반도체', 'members': [{'code': '005930', 'name': '삼성전자'},
                                                                   {'code': '000660', 'name': 'SK하이닉스'}]}]}, f, ensure_ascii=False)
        self.assertEqual(self.O.ingest_themes(), 2)
        h = self.O.history_facts('005930', exclude_report='stock_report_a2', limit=8)
        roles = {x['role'] for x in h}
        self.assertTrue({'hist_pred', 'hist_adr', 'hist_peer', 'hist_theme'} <= roles, [x['text'] for x in h])
        self.assertTrue(any('72%' in x['text'] for x in h if x['role'] == 'hist_adr'))
        self.assertEqual(self.O.ingest_outcomes(), 1)

    def test_validate_and_export(self):
        self.O.backfill()
        v = self.O.validate()
        self.assertTrue(v['conforms'], v['violations'][:3])
        c = self.O._conn()
        c.execute("INSERT INTO triples VALUES ('prediction/x','rdf:type','wa:Prediction',0,'dynamic','bad','005930','2026')")
        c.execute("INSERT INTO triples VALUES ('prediction/x','verdict','몰라',1,'dynamic','bad','005930','2026')")
        c.execute("INSERT INTO triples VALUES ('prediction/x','Bad Pred','stock/005930',0,'dynamic','bad','005930','2026')")
        c.commit()
        c.close()
        shapes = {x['shape'] for x in self.O.validate()['violations']}
        self.assertTrue({'PredictionShape', 'NamingRule'} <= shapes, shapes)
        ttl = self.O.export_ttl()
        self.assertIn('@prefix wa:', ttl)
        self.assertIn('wa:ticker "005930"', ttl)
        self.assertNotIn('"005930"^^xsd:decimal', ttl)
        self.assertIn('wa:probUp "55"^^xsd:decimal', ttl)

    def test_sparql_read_only(self):
        with self.assertRaises((ValueError, RuntimeError)):
            self.O.sparql('DELETE WHERE { ?s ?p ?o }')
