"""
4단계: 지식그래프 (Palantir Foundry 온톨로지 방식)

예측 보고서·시뮬레이션·가격 데이터를 Foundry 온톨로지 3계층으로 분해합니다.
  - Semantic(의미) : Object Type · Property · Link Type  — 무엇이 존재하는가 (종목, 지표, 섹터, 에이전트 집단 …)
  - Kinetic(동적 행위) : Action Type · Function — 무엇을 할 수 있는가 (매수, 분할매수, 손절, 리밸런싱 …)
  - Dynamic(변화·시나리오) : Event · Scenario · Decision — 시간에 따라 무엇이 변하고 무엇을 결정하는가

생성물
  - nodes / edges            : d3 지식그래프용 (편집 가능)
  - cqs  (Competency Question): 온톨로지가 답해야 하는 핵심 질문과 답
  - adrs (강화된 의사결정 레코드): 맥락·결정·근거·결과·확신도·증거 노드
  - ontology.ttl             : RDF / OWL 2 (Turtle)
  - insight_report           : 의사결정 인사이트 보고서 (Markdown)
  - spoken_summary           : 음성으로 읽을 종합 의견
"""

import os
import re
import io
import json
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..config import Config
from ..utils.logger import get_logger
from ..utils.resilience import retry_call, repair_json
from .stock_judge import StockJudge, _reports_dir, _read_json

logger = get_logger('mirofish.stock_kg')

LAYERS = ('semantic', 'kinetic', 'dynamic')
LAYER_LABEL = {'semantic': 'Semantic(의미)', 'kinetic': 'Kinetic(행위)', 'dynamic': 'Dynamic(변화·결정)'}
NODE_TYPES = {
    'semantic': ['ObjectType', 'Property', 'LinkType', 'Metric', 'Entity'],
    'kinetic': ['ActionType', 'Function', 'Rule', 'Signal'],
    'dynamic': ['Event', 'Scenario', 'Decision', 'Risk', 'State'],
}
ADR_STATUS = ['제안', '채택', '보류', '기각']
BASE_IRI = 'https://weaidstock.ai/ontology#'
_FAST_CACHE: Dict[str, str] = {}


def _kg_dir(report_id: str) -> str:
    d = os.path.join(_reports_dir(), report_id)
    if not os.path.isdir(d):
        raise FileNotFoundError(f"보고서를 찾을 수 없습니다: {report_id}")
    return d


# ─────────────────────────────────────────────────────────────── 정규화
def _http_url(url: str) -> bool:
    return bool(re.match(r'^https?://[^\s<>"\']+$', str(url or ''), re.I))


def _iri_ref(url: str) -> Optional[str]:
    """Turtle IRIREF 로 안전하게 — http(s) 만, 금지 문자(공백 < > " { } | ^ ` \\)와 비ASCII 는 퍼센트 인코딩"""
    from urllib.parse import quote
    u = str(url or '').strip()
    if not _http_url(u):
        return None
    q = quote(u, safe=":/?#[]@!$&'()*+,;=%-._~")
    return q if re.fullmatch(r'[^\x00-\x20<>"{}|^`\\]+', q) else None


def _slug(text: str, prefix: str) -> str:
    s = re.sub(r'[^0-9A-Za-z_]+', '_', str(text or '')).strip('_')
    if not s or not s[0].isalpha():
        s = f"{prefix}_{s}" if s else prefix
    return s[:60]


def normalize_graph(data: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 출력 또는 사용자 편집본을 일관된 구조로 정리합니다."""
    nodes_in = data.get('nodes') or []
    edges_in = data.get('edges') or []
    nodes, seen = [], set()
    for i, n in enumerate(nodes_in):
        if not isinstance(n, dict):
            continue
        nid = _slug(n.get('id') or n.get('label') or f'n{i}', 'n')
        base, k = nid, 2
        while nid in seen:
            nid = f"{base}_{k}"
            k += 1
        seen.add(nid)
        layer = str(n.get('layer', 'semantic')).lower()
        layer = layer if layer in LAYERS else 'semantic'
        ntype = str(n.get('type') or NODE_TYPES[layer][0])
        row = {
            'id': nid,
            '_orig': str(n.get('id') or ''),
            'label': str(n.get('label') or nid)[:80],
            'layer': layer,
            'type': ntype[:40],
            'description': str(n.get('description') or '')[:500],
            'value': str(n.get('value') or '')[:120],
        }
        url = str(n.get('url') or '').strip()
        if url and _http_url(url):          # 리서치 근거 노드의 출처 (http/https 만)
            row['url'] = url[:600]
        nodes.append(row)
    # 원래 id → 정규화 id 매핑 (LLM 이 쓴 id 로 엣지가 연결되도록)
    idmap = {}
    for n in nodes:
        idmap[n['id']] = n['id']
        if n['_orig']:
            idmap[n['_orig']] = n['id']
            idmap[_slug(n['_orig'], 'n')] = n['id']
        idmap[n['label']] = n['id']
    for n in nodes:
        n.pop('_orig', None)

    def ref(x):
        if isinstance(x, dict):
            x = x.get('id')
        return idmap.get(str(x)) or idmap.get(_slug(str(x), 'n'))

    edges, eseen = [], set()
    for i, e in enumerate(edges_in):
        if not isinstance(e, dict):
            continue
        s, t = ref(e.get('source')), ref(e.get('target'))
        if not s or not t or s == t:
            continue
        pred = str(e.get('predicate') or e.get('label') or 'relatedTo')[:60]
        key = (s, t, pred)
        if key in eseen:
            continue
        eseen.add(key)
        layer = str(e.get('layer', '')).lower()
        if layer not in LAYERS:
            layer = next((n['layer'] for n in nodes if n['id'] == s), 'semantic')
        edges.append({
            'id': _slug(e.get('id') or f'e{i}', 'e') if e.get('id') else f'e{i}',
            'source': s, 'target': t, 'predicate': pred, 'layer': layer,
        })
    # 엣지 id 중복 제거
    used = set()
    for i, e in enumerate(edges):
        if e['id'] in used:
            e['id'] = f"e{i}_{len(used)}"
        used.add(e['id'])

    node_ids = {n['id'] for n in nodes}
    cqs = []
    for i, c in enumerate(data.get('cqs') or []):
        if not isinstance(c, dict) or not c.get('question'):
            continue
        layer = str(c.get('layer', 'semantic')).lower()
        cqs.append({
            'id': _slug(c.get('id') or f'CQ{i + 1}', 'CQ'),
            'question': str(c['question'])[:300],
            'answer': str(c.get('answer') or '')[:800],
            'layer': layer if layer in LAYERS else 'semantic',
            'node_ids': [x for x in (ref(v) for v in (c.get('node_ids') or [])) if x in node_ids],
        })
    adrs = []
    for i, a in enumerate(data.get('adrs') or []):
        if not isinstance(a, dict) or not a.get('title'):
            continue
        status = str(a.get('status') or '제안')
        try:
            conf = max(0, min(100, int(float(a.get('confidence', 50)))))
        except (TypeError, ValueError):
            conf = 50
        layer = str(a.get('layer', 'dynamic')).lower()
        adrs.append({
            'id': _slug(a.get('id') or f'ADR_{i + 1:03d}', 'ADR'),
            'title': str(a['title'])[:120],
            'status': status if status in ADR_STATUS else '제안',
            'context': str(a.get('context') or '')[:800],
            'decision': str(a.get('decision') or '')[:600],
            'rationale': str(a.get('rationale') or '')[:800],
            'consequences': str(a.get('consequences') or '')[:600],
            'confidence': conf,
            'layer': layer if layer in LAYERS else 'dynamic',
            'evidence': [x for x in (ref(v) for v in (a.get('evidence') or [])) if x in node_ids],
        })
    return {'nodes': nodes, 'edges': edges, 'cqs': cqs, 'adrs': adrs}


# ─────────────────────────────────────────────────────────────── RDF / OWL 2
def _lit(s: Any) -> str:
    s = str(s if s is not None else '')
    s = s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
    return f'"{s}"'


def _pred_iri(pred: str) -> str:
    """술어를 camelCase ASCII IRI 로. 한글 술어는 해시 기반 IRI + rdfs:label 로 보존."""
    ascii_part = re.sub(r'[^0-9A-Za-z]+', ' ', pred).strip()
    if ascii_part and len(ascii_part) >= max(2, len(pred) // 2):
        words = ascii_part.split()
        name = words[0][:1].lower() + words[0][1:] + ''.join(w[:1].upper() + w[1:] for w in words[1:])
    else:
        name = 'rel_' + hashlib.md5(pred.encode('utf-8')).hexdigest()[:8]
    if not name[0].isalpha():
        name = 'rel_' + name
    return 'ws:' + name[:60]


def to_turtle(kg: Dict[str, Any], meta: Dict[str, Any]) -> str:
    L = []
    L.append(f"@prefix ws: <{BASE_IRI}> .")
    L.append("@prefix owl: <http://www.w3.org/2002/07/owl#> .")
    L.append("@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .")
    L.append("@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .")
    L.append("@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .")
    L.append("@prefix dcterms: <http://purl.org/dc/terms/> .")
    L.append("")
    L.append(f"<{BASE_IRI.rstrip('#')}/{meta.get('report_id', '')}> a owl:Ontology ;")
    L.append(f"    rdfs:label {_lit(meta.get('company_name', '') + ' 의사결정 온톨로지')} ;")
    L.append(f"    dcterms:created \"{meta.get('created_at', '')}\"^^xsd:dateTime ;")
    L.append(f"    rdfs:comment {_lit('Palantir Foundry 3계층(Semantic/Kinetic/Dynamic) 기반 · WeAidStock 생성')} .")
    L.append("")
    L.append("# ── 상위 스키마 (TBox) ──")
    for layer in LAYERS:
        cls = 'ws:' + layer.capitalize() + 'Layer'
        L.append(f"{cls} a owl:Class ; rdfs:label {_lit(LAYER_LABEL[layer])} .")
    L.append("ws:OntologyElement a owl:Class ; rdfs:label \"온톨로지 요소\" .")
    types = sorted({n['type'] for n in kg['nodes']})
    for t in types:
        L.append(f"ws:{_slug(t, 'T')} a owl:Class ; rdfs:subClassOf ws:OntologyElement ; rdfs:label {_lit(t)} .")
    L.append("ws:CompetencyQuestion a owl:Class ; rdfs:label \"핵심 질문(CQ)\" .")
    L.append("ws:DecisionRecord a owl:Class ; rdfs:label \"강화된 의사결정 레코드(ADR)\" .")
    L.append("ws:inLayer a owl:ObjectProperty ; rdfs:range [ owl:unionOf ( ws:SemanticLayer ws:KineticLayer ws:DynamicLayer ) ] .")
    for p in ('description', 'value', 'answer', 'context', 'decision', 'rationale', 'consequences', 'status'):
        L.append(f"ws:{p} a owl:DatatypeProperty ; rdfs:range xsd:string .")
    L.append("ws:confidence a owl:DatatypeProperty ; rdfs:range xsd:integer .")
    L.append("ws:about a owl:ObjectProperty ; rdfs:domain ws:CompetencyQuestion .")
    L.append("ws:evidence a owl:ObjectProperty ; rdfs:domain ws:DecisionRecord .")
    preds = {}
    for e in kg['edges']:
        preds.setdefault(_pred_iri(e['predicate']), e['predicate'])
    for iri, label in sorted(preds.items()):
        L.append(f"{iri} a owl:ObjectProperty ; rdfs:label {_lit(label)} .")
    L.append("")
    L.append("# ── 개체 (ABox) ──")
    for n in kg['nodes']:
        L.append(f"ws:{n['id']} a owl:NamedIndividual , ws:{_slug(n['type'], 'T')} ;")
        L.append(f"    rdfs:label {_lit(n['label'])} ;")
        L.append(f"    ws:inLayer ws:{n['layer'].capitalize()}Layer ;")
        if n.get('value'):
            L.append(f"    ws:value {_lit(n['value'])} ;")
        iri = _iri_ref(n.get('url')) if n.get('url') else None
        if iri:
            L.append(f"    rdfs:seeAlso <{iri}> ;")
        L.append(f"    ws:description {_lit(n.get('description', ''))} .")
    L.append("")
    L.append("# ── 관계 ──")
    for e in kg['edges']:
        L.append(f"ws:{e['source']} {_pred_iri(e['predicate'])} ws:{e['target']} .")
    L.append("")
    L.append("# ── 핵심 질문 (CQ) ──")
    for c in kg['cqs']:
        L.append(f"ws:{c['id']} a owl:NamedIndividual , ws:CompetencyQuestion ;")
        L.append(f"    rdfs:label {_lit(c['question'])} ;")
        L.append(f"    ws:inLayer ws:{c['layer'].capitalize()}Layer ;")
        for nid in c['node_ids']:
            L.append(f"    ws:about ws:{nid} ;")
        L.append(f"    ws:answer {_lit(c['answer'])} .")
    L.append("")
    L.append("# ── 강화된 의사결정 레코드 (ADR) ──")
    for a in kg['adrs']:
        L.append(f"ws:{a['id']} a owl:NamedIndividual , ws:DecisionRecord ;")
        L.append(f"    rdfs:label {_lit(a['title'])} ;")
        L.append(f"    ws:inLayer ws:{a['layer'].capitalize()}Layer ;")
        L.append(f"    ws:status {_lit(a['status'])} ;")
        L.append(f"    ws:context {_lit(a['context'])} ;")
        L.append(f"    ws:decision {_lit(a['decision'])} ;")
        L.append(f"    ws:rationale {_lit(a['rationale'])} ;")
        L.append(f"    ws:consequences {_lit(a['consequences'])} ;")
        for nid in a['evidence']:
            L.append(f"    ws:evidence ws:{nid} ;")
        L.append(f"    ws:confidence \"{a['confidence']}\"^^xsd:integer .")
    return "\n".join(L) + "\n"


# ─────────────────────────────────────────────────────────────── 서비스
class StockKnowledgeGraph:

    def __init__(self):
        self.judge = StockJudge()

    # ---------------------------------------------------------- 모델 선택 (빠른 비추론 모델 우선)
    def _fast_model(self, provider: Dict[str, Any]) -> str:
        forced = (os.environ.get('XAI_FAST_MODEL') or '').strip()
        name = provider['name']
        key = f"fast:{name}"
        hit = _FAST_CACHE.get(key)
        if hit:
            return hit
        if forced:
            _FAST_CACHE[key] = provider['prefix'] + forced.split('/')[-1]
            return _FAST_CACHE[key]
        avail = self.judge.list_models(provider)
        best, best_v = None, -1.0
        for m in avail:
            base = m.split('/')[-1]
            if not base.startswith('grok') or 'multi-agent' in base or 'beta' in base:
                continue
            if 'non-reasoning' not in base:
                continue
            mv = re.match(r'grok-(\d+(?:\.\d+)?)', base)
            v = float(mv.group(1)) if mv else 0
            if v > best_v:
                best, best_v = m, v
        _FAST_CACHE[key] = best or self.judge.resolve_model(provider)
        return _FAST_CACHE[key]

    # LLM 호출 (xAI → Vercel Gateway 순, 스트리밍으로 진행 표시)
    def _llm_json(self, system: str, user: str, max_tokens: int = 8000, fast: bool = True,
                  on_tick=None) -> Tuple[Dict[str, Any], str]:
        from ..utils.llm_client import stream_completion
        errors = []
        for provider in self.judge.providers:
            model = self._fast_model(provider) if fast else self.judge.resolve_model(provider)
            try:
                client = provider['client'].with_options(timeout=300, max_retries=1)
                kwargs = dict(model=model, temperature=0.2, max_tokens=max_tokens,
                              messages=[{"role": "system", "content": system},
                                        {"role": "user", "content": user}])
                raw = None
                try:
                    raw = stream_completion(client, dict(kwargs, response_format={"type": "json_object"}), on_tick)
                except Exception as e:
                    logger.warning(f"스트리밍 JSON 호출 실패, 일반 호출 재시도: {e}")
                if not raw:
                    try:
                        resp = retry_call(client.chat.completions.create, tries=3, label=f'KG {model}',
                                          response_format={"type": "json_object"}, **kwargs)
                    except Exception as e:
                        if 'response_format' not in str(e) and 'json' not in str(e).lower():
                            raise
                        resp = retry_call(client.chat.completions.create, tries=3, label=f'KG {model}', **kwargs)
                    raw = resp.choices[0].message.content or ''
                # 코드펜스·사고과정·후행 쉼표·잘린 출력 복구
                return repair_json(raw), f"{provider['name']}:{model}"
            except Exception as e:
                errors.append(f"{provider['name']}: {str(e)[:200]}")
                logger.error(f"지식그래프 LLM 호출 실패 [{provider['name']}]: {e}")
        raise RuntimeError("LLM 호출 실패 — " + " | ".join(errors or ["API 키 없음"]))

    # ---------------------------------------------------------- 즉시 생성되는 기본 그래프 (LLM 없음)
    @staticmethod
    def baseline(ctx: Dict[str, Any], judgment: Optional[Dict[str, Any]] = None,
                 research: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """research = 2단계 research.json (있으면 상위 6개 발견 + 감성 지수 노드를 추가)"""
        name, tk = ctx.get('company_name') or '종목', ctx.get('ticker') or ''
        v, p = ctx.get('votes') or {}, ctx.get('price') or {}
        N, E = [], []

        def node(i, label, layer, typ, value='', desc=''):
            N.append({'id': i, 'label': label, 'layer': layer, 'type': typ, 'value': str(value) if value not in (None, '') else '', 'description': desc})

        def edge(s_, p_, o_, layer):
            E.append({'source': s_, 'target': o_, 'predicate': p_, 'layer': layer})

        def f(x, unit='', nd=2):
            return '' if x is None else (f"{x:,.{nd}f}{unit}" if isinstance(x, (int, float)) else str(x))

        # Semantic
        node('stock', f"{name} ({tk})", 'semantic', 'ObjectType', '', '분석 대상 종목')
        node('market_kr', '한국 주식시장', 'semantic', 'Entity', '')
        edge('stock', 'listedOn', 'market_kr', 'semantic')
        if p.get('has_data'):
            metrics = [
                ('m_close', '현재가', f(p.get('last_close'), '원', 0), '최근 종가'),
                ('m_ret20', '20일 수익률', f(p.get('chg_20d_pct'), '%'), ''),
                ('m_ret60', '60일 수익률', f(p.get('chg_60d_pct'), '%'), ''),
                ('m_rsi', 'RSI(14)', f(p.get('rsi14'), '', 1), '상대강도지수'),
                ('m_vol', '20일 변동성', f(p.get('daily_vol_20d_pct'), '%'), '일간 수익률 표준편차'),
                ('m_ma20', '20일 이동평균', f(p.get('ma20'), '원', 0), ''),
                ('m_ma60', '60일 이동평균', f(p.get('ma60'), '원', 0), ''),
                ('m_high60', '60일 최고가', f(p.get('high_60d'), '원', 0), ''),
                ('m_low60', '60일 최저가', f(p.get('low_60d'), '원', 0), ''),
            ]
            for mid, lab, val, desc in metrics:
                if val:
                    node(mid, lab, 'semantic', 'Metric', val, desc)
                    edge('stock', 'hasMetric', mid, 'semantic')
        node('agents', f"AI 투자자 에이전트 {v.get('num_agents') or ''}명", 'semantic', 'Entity', f"평균 확신 {v.get('avg_confidence')}", '시뮬레이션 참여 집단')
        edge('agents', 'evaluates', 'stock', 'semantic')
        for vid, lab, key in (('v_buy', '매수 투표', 'buy_pct'), ('v_sell', '매도 투표', 'sell_pct'), ('v_hold', '보합 투표', 'hold_pct')):
            node(vid, lab, 'semantic', 'Property', f(v.get(key), '%', 1))
            edge('agents', 'casts', vid, 'semantic')
        sec = ctx.get('sections') or {}
        if isinstance(sec, dict):
            for k, val in sec.items():
                title = val.get('title') if isinstance(val, dict) else k
                node(f"r_{k}", f"보고서 · {title}", 'semantic', 'Entity', '', '예측 보고서 섹션')
                edge(f"r_{k}", 'describes', 'stock', 'semantic')
        # Kinetic
        node('f_trend', '추세 판정 함수', 'kinetic', 'Function', '', '종가 vs 20·60일선')
        node('f_rsi', 'RSI 판정 함수', 'kinetic', 'Function', '', '과매수 70 / 과매도 30')
        node('f_vote', '에이전트 합의 함수', 'kinetic', 'Function', '', '매수-매도 투표 차이')
        for m_ in ('m_close', 'm_ma20', 'm_ma60'):
            if any(n['id'] == m_ for n in N):
                edge(m_, 'feeds', 'f_trend', 'kinetic')
        if any(n['id'] == 'm_rsi' for n in N):
            edge('m_rsi', 'feeds', 'f_rsi', 'kinetic')
        for vid in ('v_buy', 'v_sell', 'v_hold'):
            edge(vid, 'feeds', 'f_vote', 'kinetic')
        trend = '상승' if (p.get('last_close') and p.get('ma20') and p['last_close'] > p['ma20']) else '하락/약세'
        rsi = p.get('rsi14')
        rsi_state = '과매수' if rsi and rsi >= 70 else '과매도' if rsi and rsi <= 30 else '중립'
        spread = (v.get('buy_pct') or 0) - (v.get('sell_pct') or 0)
        node('s_trend', '추세 신호', 'kinetic', 'Signal', trend)
        node('s_rsi', 'RSI 신호', 'kinetic', 'Signal', rsi_state)
        node('s_vote', '합의 신호', 'kinetic', 'Signal', f"매수-매도 {spread:+.1f}%p")
        edge('f_trend', 'produces', 's_trend', 'kinetic')
        edge('f_rsi', 'produces', 's_rsi', 'kinetic')
        edge('f_vote', 'produces', 's_vote', 'kinetic')
        acts = [('a_buy', '매수'), ('a_acc', '분할매수'), ('a_hold', '관망'), ('a_reduce', '비중축소'), ('a_sell', '매도')]
        for aid, lab in acts:
            node(aid, lab, 'kinetic', 'ActionType')
        bull = spread >= 10 or trend == '상승'
        edge('s_trend', 'supports', 'a_acc' if bull else 'a_reduce', 'kinetic')
        edge('s_vote', 'supports', 'a_buy' if spread >= 15 else 'a_hold' if abs(spread) < 10 else 'a_sell', 'kinetic')
        edge('s_rsi', 'supports', 'a_reduce' if rsi_state == '과매수' else 'a_acc' if rsi_state == '과매도' else 'a_hold', 'kinetic')
        # Dynamic
        for sid, lab, key in (('sc_up', '상승 시나리오', 'buy_pct'), ('sc_flat', '보합 시나리오', 'hold_pct'), ('sc_down', '하락 시나리오', 'sell_pct')):
            node(sid, lab, 'dynamic', 'Scenario', f"확률 {f(v.get(key), '%', 1)}")
        edge('v_buy', 'weights', 'sc_up', 'dynamic')
        edge('v_hold', 'weights', 'sc_flat', 'dynamic')
        edge('v_sell', 'weights', 'sc_down', 'dynamic')
        edge('a_buy', 'leadsTo', 'sc_up', 'dynamic')
        edge('a_acc', 'leadsTo', 'sc_up', 'dynamic')
        edge('a_reduce', 'hedges', 'sc_down', 'dynamic')
        edge('a_sell', 'hedges', 'sc_down', 'dynamic')
        edge('a_hold', 'waitsFor', 'sc_flat', 'dynamic')
        node('risk_vol', '변동성 리스크', 'dynamic', 'Risk', f(p.get('daily_vol_20d_pct'), '%'))
        edge('risk_vol', 'threatens', 'stock', 'dynamic')
        if rsi_state == '과매수':
            node('risk_ob', '단기 과열 리스크', 'dynamic', 'Risk', f"RSI {f(rsi, '', 1)}")
            edge('risk_ob', 'threatens', 'sc_up', 'dynamic')
        if judgment and judgment.get('verdict'):
            node('d_judge', f"AI 판단: {judgment['verdict']}", 'dynamic', 'Decision', f"확신 {judgment.get('conviction')}")
            edge('d_judge', 'basedOn', 's_vote', 'dynamic')
            edge('d_judge', 'basedOn', 's_trend', 'dynamic')
        node('st_now', '현재 시장 상태', 'dynamic', 'State', f"{trend} · RSI {rsi_state}")
        edge('stock', 'inState', 'st_now', 'dynamic')

        # 2단계 리서치: 상위 발견 6개(뉴스·해외 = Dynamic Event, 공시 = Semantic Entity) + 감성 지수(Metric)
        if isinstance(research, dict):
            fnd = [x for x in (research.get('findings') or []) if isinstance(x, dict) and x.get('claim')]
            fnd = sorted(fnd, key=lambda x: (-(x.get('importance') or 0),
                                             int(re.sub(r'\D', '', str(x.get('id') or '')) or 999)))[:6]
            tone = {'positive': '긍정', 'negative': '부정', 'neutral': '중립'}
            for it in fnd:
                src = it.get('source') or {}
                kind = it.get('kind') or 'news'
                claim = str(it.get('claim') or '')
                label = claim if len(claim) <= 40 else claim[:39] + '…'
                nid = f"{'disc' if kind == 'disclosure' else 'news'}_{it.get('id') or len(N)}"
                meta = ' · '.join(x for x in (it.get('date'), src.get('publisher')) if x)
                s_lab = tone.get((it.get('sentiment') or {}).get('label'), '')
                desc = f"[{it.get('id')}] {claim}" + (f" (감성 {s_lab})" if s_lab else '') + (f" — {src.get('publisher')}" if src.get('publisher') else '')
                if kind == 'disclosure':
                    N.append({'id': nid, 'label': f"공시 · {label}", 'layer': 'semantic', 'type': 'Entity',
                              'value': meta, 'description': desc, 'url': src.get('url') or ''})
                    edge('stock', 'hasDisclosure', nid, 'semantic')
                else:
                    N.append({'id': nid, 'label': label, 'layer': 'dynamic', 'type': 'Event',
                              'value': meta, 'description': desc, 'url': src.get('url') or ''})
                    edge('stock', 'hasNews', nid, 'dynamic')
            sent = research.get('sentiment') or {}
            ov = sent.get('overall_score')
            if isinstance(ov, (int, float)):
                ns, cs = sent.get('news') or {}, sent.get('community') or {}
                parts = [f"뉴스 {float(ns.get('score') or 0):+.2f} ({ns.get('n', 0)}건)"] if ns.get('n') else []
                if cs.get('n'):
                    parts.append(f"커뮤니티 {float(cs.get('score') or 0):+.2f} ({cs.get('n', 0)}건)")
                node('m_sentiment', '감성 지수(Sentiment)', 'semantic', 'Metric', f"{ov:+.2f}",
                     '뉴스·공시·해외 발견과 커뮤니티 글의 감성 종합 (−1~+1). ' + ' · '.join(parts) + f" · 엔진 {sent.get('engine', '')}")
                edge('stock', 'hasSentiment', 'm_sentiment', 'semantic')
                edge('m_sentiment', 'weights', 'sc_up' if ov >= 0.15 else 'sc_down' if ov <= -0.15 else 'sc_flat', 'dynamic')
        return {'nodes': N, 'edges': E, 'cqs': [], 'adrs': []}

    # ---------------------------------------------------------- 생성 (기본 그래프 즉시 저장 → 병렬 확장)
    def generate(self, report_id: str, progress=None) -> Dict[str, Any]:
        import threading as _th
        from concurrent.futures import ThreadPoolExecutor

        def emit(pct, msg, detail=None):
            if progress:
                try:
                    progress(pct, msg, detail or {})
                except Exception:
                    pass

        emit(3, '1~3단계 근거(가격 지표·투표·보고서) 수집 중')
        ctx = self.judge.collect_context(report_id)
        judgment = StockJudge.load_cached(report_id) or {}
        research = None
        try:
            from .stock_evidence import load_research
            research = load_research(report_id=report_id)
        except Exception as e:
            logger.warning(f"[{report_id}] research.json 로드 실패(무시): {e}")
        base = normalize_graph(self.baseline(ctx, judgment, research))
        base['insight_report'] = ''
        base['spoken_summary'] = ''
        base['enriching'] = True
        self._save(report_id, base, ctx, model='rule-based')
        emit(10, f"기본 S-P-O 그래프 생성 완료 (노드 {len(base['nodes'])} · 엣지 {len(base['edges'])})", {'baseline': True})

        sections = ctx['sections']
        sec_text = "\n\n".join(
            f"### {(v.get('title') if isinstance(v, dict) else k)}\n{(v.get('content') if isinstance(v, dict) else v)}"[:1400]
            for k, v in sections.items() if v
        ) if isinstance(sections, dict) else ''
        if len(sec_text) < 300:
            sec_text = ctx['markdown'][:8000]
        facts = (f"[종목] {ctx['company_name']} ({ctx['ticker']})\n"
                 f"[에이전트 투표] {json.dumps(ctx['votes'], ensure_ascii=False)}\n"
                 f"[가격 지표] {json.dumps({k: v for k, v in ctx['price'].items() if k != 'recent_closes'}, ensure_ascii=False)}\n"
                 f"[에이전트 근거] {' / '.join(ctx['agent_samples'][:6]) or '없음'}\n"
                 + (("[2단계 리서치 발견]\n" + "\n".join(
                     f"[{x.get('id')}] ({x.get('kind')}/{x.get('topic')}) {x.get('claim')}"
                     for x in (research.get('findings') or [])[:10] if isinstance(x, dict)) + "\n")
                    if isinstance(research, dict) and research.get('findings') else '')
                 + f"\n[예측 보고서]\n{sec_text[:8000]}")
        base_ids = ', '.join(f"{n['id']}({n['label']})" for n in base['nodes'])
        sys_common = ("당신은 Palantir Foundry 온톨로지 아키텍트이자 한국 주식 리서치 책임자입니다. "
                      "Semantic(대상·속성·링크) / Kinetic(행위·함수·규칙·신호) / Dynamic(이벤트·시나리오·리스크·결정) 3계층으로 사고합니다. "
                      "데이터에 없는 사실은 만들지 말고 수치를 인용하세요. 모든 텍스트는 한국어. JSON 한 개만 출력.")

        jobs = {
            'triples': ('보고서 → S-P-O 트리플 추출', sys_common, f"""{facts}

[기존 노드 id] {base_ids}
보고서 내용에서 기존 그래프에 없는 개념(뉴스 테마·업황·경쟁사·촉매·리스크·행위 규칙·시나리오 조건 등)을 뽑아 S-P-O 트리플로 확장하세요.
새 노드 18~28개, 트리플 30~45개. 가능한 한 기존 노드 id 와 연결하세요.
출력: {{"nodes": [["id(영문 snake_case)", "한국어 이름", "semantic|kinetic|dynamic", "ObjectType|Property|Metric|Entity|ActionType|Function|Rule|Signal|Event|Scenario|Decision|Risk|State", "값(없으면 빈칸)"]],
"triples": [["주어 id", "술어(영문 camelCase)", "목적어 id", "semantic|kinetic|dynamic"]]}}""", 5000),
            'cqs': ('핵심 질문(CQ) 작성', sys_common, f"""{facts}

[노드 id] {base_ids}
의사결정에 필요한 핵심 질문(Competency Question) 12개와 근거 있는 답을 작성하세요 (Semantic·Kinetic·Dynamic 각 4개).
출력: {{"cqs": [{{"id": "CQ1", "question": "...", "answer": "수치 인용 답", "layer": "semantic|kinetic|dynamic", "node_ids": ["관련 노드 id"]}}]}}""", 4000),
            'adrs': ('강화된 의사결정 레코드(ADR) 작성', sys_common, f"""{facts}

[노드 id] {base_ids}
강화된 의사결정 레코드(ADR) 6개를 작성하세요 (진입·비중·손절·익절·관망 조건·리스크 대응 등).
출력: {{"adrs": [{{"id": "ADR_001", "title": "...", "status": "제안|채택|보류|기각", "context": "...", "decision": "...", "rationale": "수치 인용",
"consequences": "...", "confidence": 0~100, "layer": "dynamic", "evidence": ["근거 노드 id"]}}]}}""", 4000),
            'insight': ('인사이트 보고서·음성 원고 작성', sys_common, f"""{facts}

출력: {{"insight_report": "Markdown 의사결정 인사이트 보고서 (## 핵심 결론 / ## 3계층 분석 (Semantic·Kinetic·Dynamic) / ## 시나리오별 대응 / ## 모니터링 지표)",
"spoken_summary": "음성으로 읽을 종합 의견. 뉴스 앵커 같은 존댓말 구어체 450~600자, 숫자는 읽기 쉽게, 기호 금지"}}""", 4000),
        }
        state = {k: {'label': v[0], 'status': 'running', 'chars': 0, 'reason': 0, 'sec': 0} for k, v in jobs.items()}
        lock = _th.Lock()

        def report():
            done = sum(1 for x in state.values() if x['status'] != 'running')
            chars = sum(x['chars'] for x in state.values())
            pct = 12 + int(done / len(state) * 78)
            def mark(x):
                if x['status'] == 'done':
                    return '✓'
                if x['status'] == 'error':
                    return '✗'
                return '{:,}자'.format(x['chars'])
            live = ' · '.join(x['label'] + ' ' + mark(x) for x in state.values())
            emit(pct, f"AI 온톨로지 확장 {done}/{len(state)} — 수신 {chars:,}자", {'jobs': state, 'live': live})

        def run(key):
            label, sysmsg, user, mt = jobs[key]

            def tick(nc, nr, sec):
                with lock:
                    state[key].update(chars=nc, reason=nr, sec=round(sec))
                report()
            try:
                data, model = self._llm_json(sysmsg, user, max_tokens=mt, fast=True, on_tick=tick)
                with lock:
                    state[key]['status'] = 'done'
                report()
                return key, data, model
            except Exception as e:
                with lock:
                    state[key]['status'] = 'error'
                    state[key]['error'] = str(e)[:200]
                report()
                return key, {}, None

        emit(12, f"AI 온톨로지 확장 시작 — 4개 작업 병렬 실행")
        with ThreadPoolExecutor(max_workers=4) as ex:
            results = {k: (d, m) for k, d, m in ex.map(run, list(jobs))}

        emit(92, '그래프 병합·정규화 및 RDF/OWL 2 변환 중')
        tri = results['triples'][0] or {}
        nodes = list(base['nodes'])
        for row in tri.get('nodes') or []:
            if isinstance(row, (list, tuple)) and len(row) >= 3:
                nodes.append({'id': row[0], 'label': row[1], 'layer': row[2], 'type': row[3] if len(row) > 3 else '',
                              'value': row[4] if len(row) > 4 else '', 'description': 'AI 추출'})
            elif isinstance(row, dict):
                nodes.append(row)
        edges = list(base['edges'])
        for t in tri.get('triples') or []:
            if isinstance(t, (list, tuple)) and len(t) >= 3:
                edges.append({'source': t[0], 'predicate': t[1], 'target': t[2], 'layer': t[3] if len(t) > 3 else ''})
            elif isinstance(t, dict):
                edges.append({'source': t.get('s') or t.get('source'), 'predicate': t.get('p') or t.get('predicate'),
                              'target': t.get('o') or t.get('target'), 'layer': t.get('layer', '')})
        kg = normalize_graph({'nodes': nodes, 'edges': edges,
                              'cqs': (results['cqs'][0] or {}).get('cqs') or [],
                              'adrs': (results['adrs'][0] or {}).get('adrs') or []})
        ins = results['insight'][0] or {}
        kg['insight_report'] = str(ins.get('insight_report') or '').strip()
        kg['spoken_summary'] = re.sub(r'[#*`>|_]', '', str(ins.get('spoken_summary') or '')).strip()
        kg['enriching'] = False
        kg['job_errors'] = {k: v.get('error') for k, v in state.items() if v['status'] == 'error'}
        models = sorted({m for _, m in results.values() if m})
        out = self._save(report_id, kg, ctx, model=', '.join(models) or 'rule-based')
        emit(100, f"완료 — 노드 {len(kg['nodes'])} · 엣지 {len(kg['edges'])} · CQ {len(kg['cqs'])} · ADR {len(kg['adrs'])}")
        return out

    def _save(self, report_id: str, kg: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None,
              model: Optional[str] = None, edited: bool = False) -> Dict[str, Any]:
        d = _kg_dir(report_id)
        prev = _read_json(os.path.join(d, 'kg.json')) or {}
        if ctx is None:
            report = _read_json(os.path.join(d, 'report.json')) or {}
            ctx = {'ticker': report.get('ticker', ''), 'company_name': report.get('company_name', '')}
        now = datetime.now().isoformat(timespec='seconds')
        kg.update({
            'report_id': report_id,
            'ticker': ctx.get('ticker') or prev.get('ticker', ''),
            'company_name': ctx.get('company_name') or prev.get('company_name', ''),
            'model': model or prev.get('model'),
            'created_at': prev.get('created_at') or now,
            'updated_at': now,
            'edited': edited or prev.get('edited', False),
        })
        kg['stats'] = {
            'nodes': len(kg['nodes']), 'edges': len(kg['edges']),
            'cqs': len(kg['cqs']), 'adrs': len(kg['adrs']),
            'by_layer': {l: sum(1 for n in kg['nodes'] if n['layer'] == l) for l in LAYERS},
        }
        ttl = to_turtle(kg, {'report_id': report_id, 'company_name': kg['company_name'], 'created_at': now})
        with open(os.path.join(d, 'kg.json'), 'w', encoding='utf-8') as f:
            json.dump(kg, f, ensure_ascii=False, indent=2)
        with open(os.path.join(d, 'ontology.ttl'), 'w', encoding='utf-8') as f:
            f.write(ttl)
        # 결정 결과는 그래프가 바뀌면 무효화
        dec = os.path.join(d, 'decision.json')
        if edited and os.path.exists(dec):
            os.remove(dec)
        kg['ttl'] = ttl
        return kg

    @staticmethod
    def load(report_id: str) -> Optional[Dict[str, Any]]:
        d = os.path.join(_reports_dir(), report_id)
        kg = _read_json(os.path.join(d, 'kg.json'))
        if kg:
            ttl_path = os.path.join(d, 'ontology.ttl')
            if os.path.exists(ttl_path):
                with open(ttl_path, 'r', encoding='utf-8') as f:
                    kg['ttl'] = f.read()
        return kg

    # ---------------------------------------------------------- 편집 저장
    def save_edit(self, report_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        prev = self.load(report_id) or {}
        kg = normalize_graph(payload)
        kg['insight_report'] = payload.get('insight_report', prev.get('insight_report', ''))
        kg['spoken_summary'] = payload.get('spoken_summary', prev.get('spoken_summary', ''))
        return self._save(report_id, kg, edited=True)

    # ---------------------------------------------------------- 인사이트 재작성 (편집된 그래프 기준)
    def regenerate_insight(self, report_id: str, progress=None) -> Dict[str, Any]:
        kg = self.load(report_id)
        if not kg:
            raise FileNotFoundError("지식그래프가 없습니다. 먼저 생성하세요.")
        compact = {
            'nodes': [{k: n[k] for k in ('id', 'label', 'layer', 'type', 'value')} for n in kg['nodes']],
            'edges': [[e['source'], e['predicate'], e['target']] for e in kg['edges']],
            'cqs': [{k: c[k] for k in ('id', 'question', 'answer')} for c in kg['cqs']],
            'adrs': [{k: a[k] for k in ('id', 'title', 'status', 'decision', 'rationale', 'confidence')} for a in kg['adrs']],
        }
        system = ("당신은 Palantir Foundry 온톨로지 기반 투자 의사결정 분석가입니다. "
                  "사용자가 편집한 지식그래프만을 근거로 인사이트 보고서와 음성 요약을 다시 씁니다. JSON 한 개만 출력.")
        user = f"""[종목] {kg.get('company_name')} ({kg.get('ticker')})
[지식그래프] {json.dumps(compact, ensure_ascii=False)[:14000]}
출력: {{"insight_report": "Markdown (## 핵심 결론 / 3계층 분석 / 핵심 질문 답변 요약 / 의사결정 레코드 요약 / 시나리오별 대응 / 모니터링 지표)",
"spoken_summary": "자연스러운 존댓말 구어체 450~650자, 기호 금지"}}"""
        if progress:
            progress(10, '편집된 그래프를 읽고 인사이트를 다시 쓰는 중', {})
        tick = (lambda nc, nr, sec: progress(10 + min(80, nc // 40), f'인사이트 작성 중 — 수신 {nc:,}자 ({sec:.0f}초)', {})) if progress else None
        data, model = self._llm_json(system, user, max_tokens=5000, fast=True, on_tick=tick)
        kg['insight_report'] = str(data.get('insight_report') or kg.get('insight_report', ''))
        kg['spoken_summary'] = re.sub(r'[#*`>|_]', '', str(data.get('spoken_summary') or kg.get('spoken_summary', '')))
        kg.pop('ttl', None)
        return self._save(report_id, {k: kg[k] for k in ('nodes', 'edges', 'cqs', 'adrs', 'insight_report', 'spoken_summary')},
                          model=model, edited=kg.get('edited', False))


# ─────────────────────────────────────────────────────────────── 음성 합성 (xAI TTS)
TTS_VOICES = ['eve', 'luna', 'orion', 'atlas', 'helix', 'perseus']


def _split_for_tts(text: str, limit: int = 450) -> List[str]:
    parts, cur = [], ''
    for sent in re.split(r'(?<=[.!?。…다요])\s+', text.strip()):
        if not sent:
            continue
        while len(sent) > limit:
            parts.append(sent[:limit])
            sent = sent[limit:]
        if len(cur) + len(sent) + 1 > limit and cur:
            parts.append(cur)
            cur = sent
        else:
            cur = (cur + ' ' + sent).strip()
    if cur:
        parts.append(cur)
    return parts


def synthesize_speech(text: str, voice: str = 'eve', cache_dir: Optional[str] = None) -> bytes:
    """xAI TTS 로 한국어 음성(mp3) 생성. 긴 글은 나눠 합성 후 이어 붙입니다."""
    key = (os.environ.get('XAI_API_KEY') or Config.XAI_API_KEY or '').strip()
    if not key:
        raise RuntimeError("XAI_API_KEY 가 없어 음성을 합성할 수 없습니다")
    voice = voice if voice in TTS_VOICES else 'eve'
    text = re.sub(r'\s+', ' ', text or '').strip()
    if not text:
        raise ValueError("읽을 텍스트가 없습니다")
    cache_path = None
    if cache_dir:
        h = hashlib.sha1(f"{voice}|{text}".encode('utf-8')).hexdigest()[:16]
        cache_path = os.path.join(cache_dir, f"insight_{voice}_{h}.mp3")
        if os.path.exists(cache_path):
            with open(cache_path, 'rb') as f:
                return f.read()
    buf = io.BytesIO()
    for chunk in _split_for_tts(text):
        def _tts(c=chunk):
            res = requests.post(
                'https://api.x.ai/v1/tts',
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                json={'text': c, 'voice_id': voice, 'language': 'ko'},
                timeout=90,
            )
            if res.status_code != 200:
                raise RuntimeError(f"xAI TTS {res.status_code}: {res.text[:200]}")
            return res.content
        buf.write(retry_call(_tts, tries=3, label='xAI TTS'))
    audio = buf.getvalue()
    if cache_path:
        with open(cache_path, 'wb') as f:
            f.write(audio)
    return audio
