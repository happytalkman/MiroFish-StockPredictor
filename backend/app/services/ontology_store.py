"""
누적 온톨로지 저장소 — 보고서마다 만든 지식그래프를 하나의 그래프로 쌓고, 과거 분석을 새 분석의 근거로 되돌려 씀

IRI 규칙 (prefix wa: = https://weaidstock.local/ont#)
  wa:stock/<종목코드>           종목 (여러 보고서에서 공유)
  wa:concept/<정규화 라벨>       의미 계층의 개념(ObjectType·Entity·LinkType) — 종목 사이를 잇는 다리
  wa:theme/<테마명>             오늘의 테마 (theme hasMember stock)
  wa:report/<보고서 ID>          보고서 · wa:prediction/<보고서 ID> 판단 · wa:adr/<보고서>/<id> · wa:cq/<보고서>/<id>
  wa:r/<보고서>/<노드>           그 보고서에만 해당하는 관측값·신호·사건 (값이 있는 노드, kinetic·dynamic 계층)

저장: SQLite (uploads/ontology/ontology.db) — triples(s, p, o, literal, layer, report_id, ticker, created_at)
검증: 내장 형태 규칙(SHACL 과 같은 제약) + pyshacl 이 설치돼 있으면 shapes.ttl 로 표준 SHACL 검증
질의: 구조 질의 API + rdflib 이 설치돼 있으면 SPARQL(읽기 전용)
Graph RAG: history_facts(ticker) → 같은 종목의 과거 판단·결과·ADR, 공유 개념으로 이어진 다른 종목 (사실표 H#)
"""

import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.ontology')
NS = 'https://weaidstock.local/ont#'
_lock = threading.RLock()
CONCEPT_TYPES = {'ObjectType', 'Entity', 'LinkType'}
PRED_RE = re.compile(r'^[a-z][A-Za-z0-9]{1,59}$')
VERDICTS = {'매수', '분할매수', '관망', '비중축소', '매도'}
# 모든 보고서에 똑같이 들어가는 기본 노드 — 종목 사이를 잇는 '공유 개념'으로 쓰면 잡음이라 보고서 범위로 둠
GENERIC_PREFIXES = ('보고서 · ', '공시 · ', 'AI 판단', 'AI 투자자')
GENERIC_LABELS = {'한국주식시장', '국내주식시장', '주식시장', '코스피', '코스닥', 'kospi', 'kosdaq', '투자자', '시장'}
# 개념 라벨은 보고서마다 표현이 달라(예: 'HBM 테마' vs '2027년 HBM 역전 전망') 그대로는 잘 안 겹침 →
# 라벨 속 약어·업종 키워드를 wa:keyword/<키워드> 로 뽑아 종목 사이를 잇는 다리로 씀
ACRONYM_RE = re.compile(r'(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,7})(?![A-Za-z0-9])')
ACRONYM_STOP = {'PER', 'PBR', 'EPS', 'BPS', 'DPS', 'ROE', 'ROA', 'RSI', 'MACD', 'YOY', 'QOQ', 'TTM', 'KRW', 'USD', 'KOSPI',
                'KOSDAQ', 'ETF', 'ETN', 'IPO', 'CEO', 'CFO', 'CQ', 'ADR', 'JEV', 'MA', 'EMA', 'OBV', 'VAR', 'KRX', 'DART',
                'IR', 'PR', 'Q1', 'Q2', 'Q3', 'Q4', 'H1', 'H2', 'FY', 'EBIT', 'EBITDA', 'OPM', 'NPM', 'TP', 'SK', 'LG', 'HD'}
DOMAIN_KEYWORDS = ('반도체', '메모리', '파운드리', '2차전지', '배터리', '양극재', '음극재', '리튬', '전기차', '자율주행', '바이오',
                   '신약', '임상', '면역항암', '의료기기', '헬스케어', '자동차', '조선', '방산', '원전', '전력', '송전', '수소',
                   '태양광', '풍력', '로봇', '게임', '화장품', '건설', '금리', '환율', '수출', '디스플레이', '통신', '플랫폼',
                   '광고', '엔터', '항공', '해운', '철강', '화학', '정유', '데이터센터', '클라우드', '보안', '우주', '드론')


def _keywords(label: str) -> List[str]:
    out = [m for m in ACRONYM_RE.findall(label or '') if m not in ACRONYM_STOP and not m.isdigit()]
    out += [k for k in DOMAIN_KEYWORDS if k in (label or '')]
    return list(dict.fromkeys(out))[:4]

SHAPES_TTL = """@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix wa: <https://weaidstock.local/ont#> .

wa:StockShape a sh:NodeShape ;
  sh:targetClass wa:Stock ;
  sh:property [ sh:path wa:ticker ; sh:minCount 1 ; sh:maxCount 1 ; sh:datatype xsd:string ; sh:pattern "^[0-9A-Z]{6}$" ] ;
  sh:property [ sh:path wa:name ; sh:minCount 1 ; sh:datatype xsd:string ] .

wa:ReportShape a sh:NodeShape ;
  sh:targetClass wa:Report ;
  sh:property [ sh:path wa:hasSubject ; sh:minCount 1 ; sh:maxCount 1 ; sh:class wa:Stock ] ;
  sh:property [ sh:path wa:createdAt ; sh:minCount 1 ; sh:datatype xsd:string ] ;
  sh:property [ sh:path wa:rating ; sh:maxCount 1 ; sh:in ( "매수" "중립" "매도" ) ] .

wa:PredictionShape a sh:NodeShape ;
  sh:targetClass wa:Prediction ;
  sh:property [ sh:path wa:forReport ; sh:minCount 1 ; sh:class wa:Report ] ;
  sh:property [ sh:path wa:verdict ; sh:minCount 1 ; sh:in ( "매수" "분할매수" "관망" "비중축소" "매도" ) ] ;
  sh:property [ sh:path wa:probUp ; sh:maxCount 1 ; sh:datatype xsd:decimal ; sh:minInclusive 0 ; sh:maxInclusive 100 ] .

wa:ADRShape a sh:NodeShape ;
  sh:targetClass wa:ADR ;
  sh:property [ sh:path wa:title ; sh:minCount 1 ] ;
  sh:property [ sh:path wa:decision ; sh:minCount 1 ] ;
  sh:property [ sh:path wa:confidence ; sh:maxCount 1 ; sh:datatype xsd:decimal ; sh:minInclusive 0 ; sh:maxInclusive 100 ] .
"""


def _dir() -> str:
    d = os.path.join(Config.UPLOAD_FOLDER, 'ontology')
    os.makedirs(d, exist_ok=True)
    return d


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(os.path.join(_dir(), 'ontology.db'), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('''CREATE TABLE IF NOT EXISTS triples (
        s TEXT, p TEXT, o TEXT, literal INTEGER, layer TEXT, report_id TEXT, ticker TEXT, created_at TEXT,
        PRIMARY KEY (s, p, o, report_id))''')
    c.execute('CREATE INDEX IF NOT EXISTS ix_t_s ON triples(s)')
    c.execute('CREATE INDEX IF NOT EXISTS ix_t_o ON triples(o)')
    c.execute('CREATE INDEX IF NOT EXISTS ix_t_ticker ON triples(ticker)')
    c.execute('''CREATE TABLE IF NOT EXISTS entities (
        id TEXT PRIMARY KEY, label TEXT, type TEXT, layer TEXT, scope TEXT, first_seen TEXT, last_seen TEXT, reports INTEGER DEFAULT 0)''')
    c.execute('CREATE INDEX IF NOT EXISTS ix_e_label ON entities(label)')
    c.execute('CREATE TABLE IF NOT EXISTS ingested (report_id TEXT PRIMARY KEY, kind TEXT, at TEXT, sig TEXT)')
    return c


def _norm(label: str) -> str:
    s = re.sub(r'[\s·ㆍ/\\,._\-()\[\]{}<>"\'`~!?:;]+', '', str(label or '').strip().lower())
    return s[:60] or 'x'


def stock_iri(t: str) -> str:
    return f'stock/{t}'


def _concept_iri(label: str) -> str:
    return f'concept/{_norm(label)}'


def _read(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _stock_by_name(label: str) -> Optional[str]:
    """라벨이 정확히 상장 종목명이면 그 종목코드"""
    try:
        from .stock_catalog import search_catalog
        for r in search_catalog(label.strip(), 'ALL', 3) or []:
            if (r.get('name') or '').strip() == label.strip():
                return r.get('ticker')
    except Exception:
        return None
    return None


class _Batch:
    def __init__(self, c, report_id, ticker, now):
        self.c, self.rid, self.ticker, self.now = c, report_id, ticker, now

    def ent(self, iri, label, typ, layer, scope):
        self.c.execute('''INSERT INTO entities(id, label, type, layer, scope, first_seen, last_seen, reports) VALUES (?,?,?,?,?,?,?,1)
            ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, label=COALESCE(entities.label, excluded.label),
            reports = entities.reports + (CASE WHEN excluded.last_seen != entities.last_seen THEN 1 ELSE 0 END)''',
                       (iri, str(label)[:120], typ, layer, scope, self.now, self.now))

    def t(self, s, p, o, literal=False, layer='semantic'):
        if o is None or o == '':
            return
        self.c.execute('INSERT OR IGNORE INTO triples(s, p, o, literal, layer, report_id, ticker, created_at) VALUES (?,?,?,?,?,?,?,?)',
                       (s, p, str(o)[:500], 1 if literal else 0, layer, self.rid, self.ticker, self.now))


# ─────────────────────────────────────────────────────────── 적재
def ingest_report(report_id: str, force: bool = False) -> Dict[str, Any]:
    base = os.path.join(Config.UPLOAD_FOLDER, 'stock_reports', report_id)
    rep = _read(os.path.join(base, 'report.json')) or {}
    kg = _read(os.path.join(base, 'kg.json')) or {}
    dec = _read(os.path.join(base, 'decision.json'))
    ticker = rep.get('ticker') or kg.get('ticker')
    if not ticker:
        return {'ok': False, 'reason': 'report.json 없음'}
    sig = '|'.join(str(os.path.getmtime(os.path.join(base, n))) if os.path.exists(os.path.join(base, n)) else '-'
                   for n in ('report.json', 'kg.json', 'decision.json'))
    now = (rep.get('created_at') or datetime.now().isoformat(timespec='seconds'))[:19]
    name = rep.get('company_name') or kg.get('company_name') or ticker
    with _lock:
        c = _conn()
        try:
            row = c.execute('SELECT sig FROM ingested WHERE report_id=?', (report_id,)).fetchone()
            if row and row['sig'] == sig and not force:
                return {'ok': True, 'skipped': True}
            c.execute('DELETE FROM triples WHERE report_id=?', (report_id,))
            b = _Batch(c, report_id, ticker, now)
            S = stock_iri(ticker)
            b.ent(S, name, 'Stock', 'semantic', 'global')
            b.t(S, 'rdf:type', 'wa:Stock')
            b.t(S, 'ticker', ticker, True)
            b.t(S, 'name', name, True)
            R = f'report/{report_id}'
            s = rep.get('summary') or {}
            b.ent(R, f"{name} 보고서 {now[:10]}", 'Report', 'dynamic', 'report')
            b.t(R, 'rdf:type', 'wa:Report', layer='dynamic')
            b.t(R, 'hasSubject', S, layer='dynamic')
            b.t(R, 'createdAt', now, True, 'dynamic')
            for k, p in (('rating', 'rating'), ('target_price', 'targetPrice'), ('last_close', 'basePrice'),
                         ('upside_pct', 'upsidePct'), ('valuation_method', 'valuationMethod'), ('as_of', 'asOf')):
                if s.get(k) is not None:
                    b.t(R, p, s[k], True, 'dynamic')
            # 지식그래프 노드·엣지 → 전역/보고서 범위 IRI
            idmap: Dict[str, str] = {}
            n_concepts = 0
            for n in kg.get('nodes') or []:
                nid, label = n.get('id'), str(n.get('label') or n.get('id'))
                if not nid:
                    continue
                if nid == 'stock':
                    idmap[nid] = S
                    continue
                typ = re.sub(r'[^0-9A-Za-z가-힣_]', '', str(n.get('type') or ''))[:40] or 'Entity'
                layer = n.get('layer') if n.get('layer') in ('semantic', 'kinetic', 'dynamic') else 'semantic'
                if (layer == 'semantic' and typ in CONCEPT_TYPES and not n.get('value')
                        and not label.startswith(GENERIC_PREFIXES) and _norm(label) not in GENERIC_LABELS):
                    other = _stock_by_name(label)
                    if other and other != ticker:
                        iri = stock_iri(other)
                        b.ent(iri, label, 'Stock', 'semantic', 'global')
                        b.t(iri, 'rdf:type', 'wa:Stock')
                        b.t(iri, 'ticker', other, True)
                        b.t(iri, 'name', label, True)
                    else:
                        iri = _concept_iri(label)
                        b.ent(iri, label, typ, layer, 'global')
                        b.t(iri, 'rdf:type', f'wa:{typ}')
                        b.t(iri, 'label', label, True)
                        n_concepts += 1
                        for kw in _keywords(label):
                            K = f'keyword/{_norm(kw)}'
                            b.ent(K, kw, 'Keyword', 'semantic', 'global')
                            b.t(K, 'rdf:type', 'wa:Keyword')
                            b.t(K, 'label', kw, True)
                            b.t(iri, 'hasKeyword', K)
                            b.t(S, 'mentions', K)
                else:
                    iri = f"r/{report_id}/{nid}"
                    b.ent(iri, label, typ, layer, 'report')
                    b.t(iri, 'rdf:type', f'wa:{typ}', layer=layer)
                    b.t(iri, 'label', label, True, layer)
                    if n.get('value'):
                        b.t(iri, 'value', n['value'], True, layer)
                    b.t(iri, 'inReport', R, layer=layer)
                idmap[nid] = iri
            for e in kg.get('edges') or []:
                s_, o_ = idmap.get(e.get('source')), idmap.get(e.get('target'))
                if s_ and o_ and s_ != o_:
                    pred = re.sub(r'[^A-Za-z0-9]', '', str(e.get('predicate') or 'relatedTo')) or 'relatedTo'
                    pred = pred[0].lower() + pred[1:]
                    b.t(s_, pred, o_, layer=e.get('layer') or 'semantic')
                    if s_ == S and o_.startswith('concept/'):
                        b.t(S, 'mentions', o_)
                    elif o_ == S and s_.startswith('concept/'):
                        b.t(S, 'mentions', s_)
            for a in kg.get('adrs') or []:
                A = f"adr/{report_id}/{_norm(a.get('id') or a.get('title'))}"
                b.ent(A, a.get('title') or 'ADR', 'ADR', 'dynamic', 'report')
                b.t(A, 'rdf:type', 'wa:ADR', layer='dynamic')
                b.t(A, 'title', a.get('title'), True, 'dynamic')
                b.t(A, 'decision', a.get('decision'), True, 'dynamic')
                if isinstance(a.get('confidence'), (int, float)):   # 0~100 (지식그래프 ADR 규칙)
                    b.t(A, 'confidence', int(round(float(a['confidence']))), True, 'dynamic')
                b.t(A, 'status', a.get('status'), True, 'dynamic')
                b.t(A, 'inReport', R, layer='dynamic')
            for q in kg.get('cqs') or []:
                Q = f"cq/{report_id}/{_norm(q.get('id') or q.get('question'))}"
                b.ent(Q, q.get('question') or 'CQ', 'CQ', 'semantic', 'report')
                b.t(Q, 'rdf:type', 'wa:CompetencyQuestion')
                b.t(Q, 'question', q.get('question'), True)
                b.t(Q, 'answer', str(q.get('answer') or '')[:400], True)
                b.t(Q, 'inReport', R)
            if dec:
                P = f'prediction/{report_id}'
                b.ent(P, f"{name} 판단 {str(dec.get('created_at') or '')[:10]}", 'Prediction', 'dynamic', 'report')
                b.t(P, 'rdf:type', 'wa:Prediction', layer='dynamic')
                b.t(P, 'forReport', R, layer='dynamic')
                b.t(P, 'verdict', dec.get('verdict'), True, 'dynamic')
                dp = dec.get('direction_probs') or {}
                for k, p in (('상승', 'probUp'), ('보합', 'probFlat'), ('하락', 'probDown')):
                    if dp.get(k) is not None:
                        b.t(P, p, dp[k], True, 'dynamic')
                plan = dec.get('plan') or {}
                for k, p in (('entry_price', 'entryPrice'), ('target_price', 'planTarget'), ('stop_price', 'stopPrice'),
                             ('position_pct', 'positionPct'), ('horizon', 'horizon')):
                    if plan.get(k) is not None:
                        b.t(P, p, plan[k], True, 'dynamic')
                b.t(P, 'decidedAt', dec.get('created_at'), True, 'dynamic')
            c.execute('INSERT OR REPLACE INTO ingested(report_id, kind, at, sig) VALUES (?,?,?,?)',
                      (report_id, 'report', datetime.now().isoformat(timespec='seconds'), sig))
            c.commit()
            n = c.execute('SELECT COUNT(*) FROM triples WHERE report_id=?', (report_id,)).fetchone()[0]
        finally:
            c.close()
    logger.info(f"[{report_id}] 온톨로지 누적: {name} 트리플 {n}개 (공유 개념 {n_concepts})")
    return {'ok': True, 'triples': n, 'concepts': n_concepts}


def ingest_outcomes() -> int:
    """예측 원장의 채점 결과를 prediction 노드에 반영"""
    try:
        from . import prediction_ledger as ledger
        preds = ledger.list_predictions(limit=2000)
    except Exception:
        return 0
    n = 0
    with _lock:
        c = _conn()
        try:
            for p in preds:
                P = f"prediction/{p['id']}"
                if not c.execute('SELECT 1 FROM entities WHERE id=?', (P,)).fetchone():
                    continue
                c.execute("DELETE FROM triples WHERE s=? AND report_id=?", (P, f"outcome:{p['id']}"))
                b = _Batch(c, f"outcome:{p['id']}", p['ticker'], datetime.now().isoformat(timespec='seconds'))
                b.t(P, 'ledgerStatus', p['status'], True, 'dynamic')
                if p.get('realized'):
                    b.t(P, 'realizedReturnPct', p['realized'].get('return_pct'), True, 'dynamic')
                    b.t(P, 'realizedDirection', p['realized'].get('class'), True, 'dynamic')
                    b.t(P, 'strategyHit', 'true' if p.get('strategy_hit') else 'false', True, 'dynamic')
                po = (p.get('plan_outcome') or {}).get('outcome')
                if po:
                    b.t(P, 'planOutcome', po, True, 'dynamic')
                n += 1
            c.commit()
        finally:
            c.close()
    return n


def ingest_themes(max_files: int = 5) -> int:
    d = os.path.join(Config.UPLOAD_FOLDER, 'themes')
    if not os.path.isdir(d):
        return 0
    files = sorted(f for f in os.listdir(d) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', f))[-max_files:]
    n = 0
    for f in files:
        data = _read(os.path.join(d, f)) or {}
        date = f[:10]
        rid = f'themes:{date}'
        with _lock:
            c = _conn()
            try:
                c.execute('DELETE FROM triples WHERE report_id=?', (rid,))
                b = _Batch(c, rid, None, date)
                for t in data.get('themes') or []:
                    name = t.get('name')
                    if not name:
                        continue
                    T = f"theme/{_norm(name)}"
                    b.ent(T, name, 'Theme', 'semantic', 'global')
                    b.t(T, 'rdf:type', 'wa:Theme')
                    b.t(T, 'label', name, True)
                    for m in t.get('members') or []:
                        code = m.get('code')
                        if code and re.fullmatch(r'\d{6}', str(code)):
                            S = stock_iri(code)
                            b.ent(S, m.get('name') or code, 'Stock', 'semantic', 'global')
                            b.t(S, 'rdf:type', 'wa:Stock')
                            b.t(S, 'ticker', code, True)
                            b.t(S, 'name', m.get('name') or code, True)
                            b.t(T, 'hasMember', S)
                            n += 1
                    for kw in t.get('keywords') or []:
                        b.t(T, 'keyword', kw, True)
                    b.t(T, 'featuredOn', date, True, 'dynamic')
                c.commit()
            finally:
                c.close()
    return n


def backfill(force: bool = False) -> Dict[str, Any]:
    base = os.path.join(Config.UPLOAD_FOLDER, 'stock_reports')
    done, skipped, failed = 0, 0, 0
    for rid in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        if not rid.startswith('stock_report_'):
            continue
        try:
            r = ingest_report(rid, force=force)
            if r.get('skipped'):
                skipped += 1
            elif r.get('ok'):
                done += 1
        except Exception as e:
            failed += 1
            logger.warning(f"온톨로지 백필 실패 {rid}: {e}")
    themes = ingest_themes()
    outcomes = ingest_outcomes()
    return {'ingested': done, 'skipped': skipped, 'failed': failed, 'theme_links': themes, 'outcomes': outcomes}


# ─────────────────────────────────────────────────────────── 조회
def stats() -> Dict[str, Any]:
    with _lock:
        c = _conn()
        try:
            t = c.execute('SELECT COUNT(*) FROM triples').fetchone()[0]
            by_type = {r['type']: r['n'] for r in c.execute('SELECT type, COUNT(*) n FROM entities GROUP BY type ORDER BY n DESC')}
            reports = c.execute("SELECT COUNT(*) FROM ingested").fetchone()[0]
            stocks = c.execute("SELECT COUNT(*) FROM entities WHERE type='Stock'").fetchone()[0]
            shared = c.execute('''SELECT e.id, e.label, COUNT(DISTINCT t.ticker) n FROM entities e JOIN triples t ON t.o = e.id
                                  WHERE (e.id LIKE 'concept/%' OR e.id LIKE 'keyword/%') AND t.ticker IS NOT NULL
                                  GROUP BY e.id HAVING n >= 2 ORDER BY n DESC, e.reports DESC LIMIT 16''').fetchall()
        finally:
            c.close()
    return {'triples': t, 'entities': sum(by_type.values()), 'by_type': by_type, 'reports': reports, 'stocks': stocks,
            'shared_concepts': [{'id': r['id'], 'label': r['label'], 'stocks': r['n']} for r in shared],
            'rdflib': _has('rdflib'), 'pyshacl': _has('pyshacl')}


def search(q: str, limit: int = 30) -> List[Dict[str, Any]]:
    q = (q or '').strip()
    if not q:
        return []
    with _lock:
        c = _conn()
        try:
            rows = c.execute('''SELECT id, label, type, layer, scope, reports FROM entities WHERE label LIKE ? OR id LIKE ?
                                ORDER BY (scope='global') DESC, reports DESC LIMIT ?''', (f'%{q}%', f'%{q}%', limit)).fetchall()
        finally:
            c.close()
    return [dict(r) for r in rows]


def neighborhood(iri: str, depth: int = 1, limit: int = 160) -> Dict[str, Any]:
    depth = max(1, min(2, int(depth or 1)))
    seen = {iri}
    frontier = [iri]
    edges = []
    literals: List[Dict[str, Any]] = []
    with _lock:
        c = _conn()
        try:
            for dstep in range(depth):
                nxt = []
                for node in frontier:
                    for r in c.execute('SELECT s, p, o, literal, layer, report_id FROM triples WHERE (s=? OR o=?) LIMIT 400', (node, node)):
                        if r['literal']:
                            if r['s'] == iri and dstep == 0:
                                literals.append({'p': r['p'], 'o': r['o'], 'report_id': r['report_id']})
                            continue
                        if r['p'] == 'rdf:type':
                            continue
                        other = r['o'] if r['s'] == node else r['s']
                        edges.append({'s': r['s'], 'p': r['p'], 'o': r['o'], 'layer': r['layer']})
                        if other not in seen and len(seen) < limit:
                            seen.add(other)
                            nxt.append(other)
                frontier = nxt
            ents = {}
            for i in range(0, len(seen), 400):
                chunk = list(seen)[i:i + 400]
                q = ','.join('?' * len(chunk))
                for r in c.execute(f'SELECT id, label, type, layer, scope, reports FROM entities WHERE id IN ({q})', chunk):
                    ents[r['id']] = dict(r)
        finally:
            c.close()
    uniq, es = set(), []
    for e in edges:
        k = (e['s'], e['p'], e['o'])
        if k in uniq or e['s'] not in seen or e['o'] not in seen:
            continue
        uniq.add(k)
        es.append(e)
    nodes = [ents.get(n) or {'id': n, 'label': n.split('/')[-1], 'type': '?', 'layer': 'semantic', 'scope': '?'} for n in seen]
    return {'center': iri, 'nodes': nodes, 'edges': es[:600], 'literals': literals[:80]}


def history_facts(ticker: str, exclude_report: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
    """같은 종목의 과거 판단·결과, 이전 보고서의 ADR, 공유 개념으로 이어진 다른 종목 → 사실표 H#"""
    out: List[Dict[str, Any]] = []
    try:
        from . import prediction_ledger as ledger
        preds = [p for p in ledger.list_predictions(ticker=ticker, limit=20) if p['id'] != exclude_report][:3]
        for p in preds:
            up = (p.get('sources') or {}).get('jev', {}).get('상승')
            plan = p.get('plan') or {}
            head = (f"{p['as_of']} 과거 판단: {p.get('strategy') or '-'}"
                    + (f" (JEV 상승 {up}%)" if up is not None else '')
                    + (f" · 목표 {plan['target_price']:,.0f} / 손절 {plan['stop_price']:,.0f}원" if plan.get('target_price') and plan.get('stop_price') else ''))
            if p.get('realized'):
                r = p['realized']
                tail = f" → 20거래일 결과 {r['return_pct']:+.1f}% ({r['class']}, {'적중' if p.get('strategy_hit') else '빗나감'})"
                po = (p.get('plan_outcome') or {}).get('outcome')
                if po in ('target', 'stop'):
                    tail += f" · 계획 {'목표가 먼저' if po == 'target' else '손절가 먼저'} 도달"
            elif p.get('mtm_pct') is not None:
                tail = f" → 진행 중 {p.get('days_elapsed')}거래일째 {p['mtm_pct']:+.1f}%"
            else:
                tail = ' → 채점 대기'
            out.append({'text': head + tail, 'date': p['as_of'], 'role': 'hist_pred'})
    except Exception as e:
        logger.debug(f"과거 판단 조회 생략: {e}")
    with _lock:
        c = _conn()
        try:
            prev = c.execute('''SELECT report_id, MAX(created_at) at FROM triples WHERE ticker=? AND report_id LIKE 'stock_report_%'
                                AND report_id != ? GROUP BY report_id ORDER BY at DESC LIMIT 1''',
                             (ticker, exclude_report or '')).fetchone()
            if prev:
                adrs = c.execute('''SELECT s, MAX(CASE WHEN p='title' THEN o END) title, MAX(CASE WHEN p='decision' THEN o END) decision,
                                    MAX(CASE WHEN p='confidence' THEN o END) conf FROM triples
                                    WHERE report_id=? AND s LIKE 'adr/%' GROUP BY s ORDER BY CAST(conf AS REAL) DESC LIMIT 2''',
                                 (prev['report_id'],)).fetchall()
                for a in adrs:
                    if a['title']:
                        out.append({'text': f"{prev['at'][:10]} 이전 분석 ADR: {a['title']} — {str(a['decision'] or '')[:120]}"
                                            + (f" (확신 {float(a['conf']):.0f}%)" if a['conf'] else ''),
                                    'date': prev['at'][:10], 'role': 'hist_adr'})
            n_stocks = c.execute("SELECT COUNT(DISTINCT ticker) FROM triples WHERE p='mentions'").fetchone()[0] or 0
            cap = max(3, int(n_stocks * 0.4))       # 너무 많은 종목에 걸친 개념은 변별력이 없어 뺌
            peers = c.execute('''SELECT e.label, GROUP_CONCAT(DISTINCT s2.o) names, COUNT(DISTINCT t2.ticker) n
                                 FROM triples t1 JOIN entities e ON e.id = t1.o
                                 JOIN triples t2 ON t2.o = t1.o AND t2.ticker != t1.ticker
                                 LEFT JOIN triples s2 ON s2.s = ('stock/' || t2.ticker) AND s2.p = 'name'
                                 WHERE t1.ticker = ? AND t1.p = 'mentions' AND t2.p = 'mentions'
                                 GROUP BY e.id HAVING n <= ? ORDER BY n DESC, e.reports DESC LIMIT 2''', (ticker, cap)).fetchall()
            for r in peers:
                names = ', '.join(sorted(set((r['names'] or '').split(',')))[:4]) if r['names'] else f"{r['n']}개 종목"
                out.append({'text': f"공유 개념 '{r['label']}' — 다른 종목 분석에도 등장: {names}", 'role': 'hist_peer'})
            themes = c.execute('''SELECT DISTINCT e.label FROM triples t JOIN entities e ON e.id = t.s
                                  WHERE t.p='hasMember' AND t.o=? ORDER BY t.created_at DESC LIMIT 3''', (stock_iri(ticker),)).fetchall()
            if themes:
                out.append({'text': f"최근 오늘의 테마 편입: {', '.join(r['label'] for r in themes)}", 'role': 'hist_theme'})
        finally:
            c.close()
    return out[:limit]


# ─────────────────────────────────────────────────────────── 내보내기 · 검증 · SPARQL
def _has(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def _ttl_term(x: str) -> str:
    if x.startswith('wa:') or x == 'rdf:type':
        return 'a' if x == 'rdf:type' else x
    safe = re.sub(r'[^A-Za-z0-9_\-/.]', lambda m: '%{:02X}'.format(ord(m.group(0))) if ord(m.group(0)) < 128 else m.group(0), x)
    return f'<{NS}{safe}>'


NUMERIC_PREDS = {'targetPrice', 'basePrice', 'upsidePct', 'probUp', 'probFlat', 'probDown', 'entryPrice', 'planTarget',
                 'stopPrice', 'positionPct', 'confidence', 'realizedReturnPct'}


def _ttl_lit(v: str, pred: str = '') -> str:
    if pred in NUMERIC_PREDS and re.fullmatch(r'-?\d+(\.\d+)?', v.strip()):
        return f'"{v.strip()}"^^xsd:decimal'
    esc = v.replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ').replace('\r', ' ')
    return f'"{esc}"'


def export_ttl() -> str:
    lines = ['@prefix wa: <https://weaidstock.local/ont#> .', '@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .', '']
    with _lock:
        c = _conn()
        try:
            for r in c.execute('SELECT DISTINCT s, p, o, literal FROM triples ORDER BY s, p'):
                p = 'a' if r['p'] == 'rdf:type' else f"wa:{r['p']}"
                o = (r['o'] if r['p'] == 'rdf:type' else (_ttl_lit(r['o'], r['p']) if r['literal'] else _ttl_term(r['o'])))
                lines.append(f"{_ttl_term(r['s'])} {p} {o} .")
        finally:
            c.close()
    ttl = '\n'.join(lines) + '\n'
    path = os.path.join(_dir(), 'weaidstock_global.ttl')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(ttl)
    with open(os.path.join(_dir(), 'shapes.ttl'), 'w', encoding='utf-8') as f:
        f.write(SHAPES_TTL)
    return ttl


def validate() -> Dict[str, Any]:
    """내장 형태 규칙 (SHACL shapes 와 같은 제약) + pyshacl 이 있으면 표준 SHACL 결과"""
    v: List[Dict[str, Any]] = []
    with _lock:
        c = _conn()
        try:
            def objs(s, p):
                return [r['o'] for r in c.execute('SELECT o FROM triples WHERE s=? AND p=?', (s, p))]
            for r in c.execute("SELECT DISTINCT s FROM triples WHERE p='rdf:type' AND o='wa:Stock'").fetchall():
                t = objs(r['s'], 'ticker')
                if len(set(t)) != 1 or not re.fullmatch(r'[0-9A-Z]{6}', t[0]):
                    v.append({'focus': r['s'], 'shape': 'StockShape', 'path': 'ticker', 'message': f'종목코드 1개(6자리) 필요 — {t}'})
            for r in c.execute("SELECT DISTINCT s FROM triples WHERE p='rdf:type' AND o='wa:Report'").fetchall():
                subj = set(objs(r['s'], 'hasSubject'))
                if len(subj) != 1:
                    v.append({'focus': r['s'], 'shape': 'ReportShape', 'path': 'hasSubject', 'message': f'대상 종목 1개 필요 — {len(subj)}개'})
                rt = objs(r['s'], 'rating')
                if rt and rt[0] not in ('매수', '중립', '매도'):
                    v.append({'focus': r['s'], 'shape': 'ReportShape', 'path': 'rating', 'message': f'투자의견 값 오류 — {rt[0]}'})
            for r in c.execute("SELECT DISTINCT s FROM triples WHERE p='rdf:type' AND o='wa:Prediction'").fetchall():
                vd = objs(r['s'], 'verdict')
                if not vd or vd[0] not in VERDICTS:
                    v.append({'focus': r['s'], 'shape': 'PredictionShape', 'path': 'verdict', 'message': f'전략 값 오류 — {vd}'})
                ps = [float(x) for p in ('probUp', 'probFlat', 'probDown') for x in objs(r['s'], p) if re.fullmatch(r'-?\d+(\.\d+)?', x)]
                if ps and abs(sum(ps) - 100) > 1.5:
                    v.append({'focus': r['s'], 'shape': 'PredictionShape', 'path': 'probUp/Flat/Down', 'message': f'방향 확률 합 {sum(ps):.1f}% (100% 필요)'})
            for r in c.execute("SELECT DISTINCT s FROM triples WHERE p='rdf:type' AND o='wa:ADR'").fetchall():
                if not objs(r['s'], 'title') or not objs(r['s'], 'decision'):
                    v.append({'focus': r['s'], 'shape': 'ADRShape', 'path': 'title/decision', 'message': '제목·결정 필수'})
                for x in objs(r['s'], 'confidence'):
                    try:
                        if not 0 <= float(x) <= 100:
                            raise ValueError
                    except ValueError:
                        v.append({'focus': r['s'], 'shape': 'ADRShape', 'path': 'confidence', 'message': f'확신도 0~100 필요 — {x}'})
            bad_p = c.execute("SELECT p, COUNT(*) n FROM triples WHERE p != 'rdf:type' GROUP BY p").fetchall()
            for r in bad_p:
                if not PRED_RE.match(r['p']):
                    v.append({'focus': f"predicate:{r['p']}", 'shape': 'NamingRule', 'path': 'predicate',
                              'message': f"술어 이름은 lowerCamelCase — '{r['p']}' ({r['n']}건)"})
            dangling = c.execute('''SELECT COUNT(*) FROM triples t WHERE t.literal = 0 AND t.p != 'rdf:type'
                                    AND NOT EXISTS (SELECT 1 FROM entities e WHERE e.id = t.o)''').fetchone()[0]
            if dangling:
                v.append({'focus': '(그래프)', 'shape': 'Integrity', 'path': 'object', 'message': f'정의되지 않은 대상 노드를 가리키는 트리플 {dangling}개'})
        finally:
            c.close()
    out: Dict[str, Any] = {'engine': 'builtin', 'conforms': not v, 'violations': v[:200], 'count': len(v)}
    if _has('pyshacl') and _has('rdflib'):
        try:
            from pyshacl import validate as shacl_validate
            import rdflib
            data = rdflib.Graph().parse(data=export_ttl(), format='turtle')
            shapes = rdflib.Graph().parse(data=SHAPES_TTL, format='turtle')
            conforms, _g, text = shacl_validate(data, shacl_graph=shapes, inference='none', abort_on_first=False)
            out['pyshacl'] = {'conforms': bool(conforms), 'report': str(text)[:4000]}
        except Exception as e:
            out['pyshacl'] = {'error': f'{type(e).__name__}: {str(e)[:200]}'}
    return out


_GRAPH: Dict[str, Any] = {'mtime': 0, 'g': None}


def sparql(query: str, limit: int = 500) -> Dict[str, Any]:
    if not _has('rdflib'):
        raise RuntimeError('SPARQL 을 쓰려면 rdflib 이 필요합니다 — 이 PC 의 설정 화면에서 "온톨로지 도구 설치"를 누르세요')
    q = (query or '').strip()
    if not q or len(q) > 5000:
        raise ValueError('질의가 비어 있거나 너무 깁니다')
    if re.search(r'\b(INSERT|DELETE|LOAD|CLEAR|DROP|CREATE|ADD|MOVE|COPY|SERVICE)\b', q, re.I):
        raise ValueError('읽기 전용 질의(SELECT·ASK·CONSTRUCT·DESCRIBE)만 허용됩니다')
    import rdflib
    path = os.path.join(_dir(), 'weaidstock_global.ttl')
    db_mtime = max((os.path.getmtime(os.path.join(_dir(), n)) for n in ('ontology.db', 'ontology.db-wal')
                    if os.path.exists(os.path.join(_dir(), n))), default=0)
    if _GRAPH['g'] is None or _GRAPH['mtime'] < db_mtime or not os.path.exists(path):
        export_ttl()
        g = rdflib.Graph()
        g.parse(path, format='turtle')
        _GRAPH.update(g=g, mtime=time.time())
    g = _GRAPH['g']
    if 'PREFIX wa:' not in q:
        q = f'PREFIX wa: <{NS}>\nPREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n' + q
    res = g.query(q)
    if res.type == 'ASK':
        return {'type': 'ASK', 'answer': bool(res.askAnswer)}
    if res.type in ('CONSTRUCT', 'DESCRIBE'):
        return {'type': res.type, 'turtle': res.graph.serialize(format='turtle')[:20000]}
    cols = [str(v) for v in res.vars]
    rows = []
    for i, r in enumerate(res):
        if i >= limit:
            break
        rows.append([str(x).replace(NS, 'wa:') if x is not None else None for x in r])
    return {'type': 'SELECT', 'columns': cols, 'rows': rows, 'truncated': len(rows) >= limit}
