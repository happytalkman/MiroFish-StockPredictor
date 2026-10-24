"""
근거(Evidence) 공통 도우미 — 보고서·지식그래프·투자위원회·판단이 같은 사실표를 쓰도록 통일

경로 연결: report.json → simulation_id → stock_simulations/<sim>/result.json → task_id → stock_analyses/<task_id>/
  - analysis.json  : 가격·재무 (2단계)
  - research.json  : 리플렉션 리서치(뉴스·공시·해외, 출처 URL) + 커뮤니티 심리 (2단계)

사실표(fact sheet): [{id, kind, text, url?, date?}]
  F# : 가격·지표·재무·투표 등 수치 사실
  R# : 리서치 발견 (출처 URL 포함)
  K# : 지식그래프 CQ/ADR
  H# : 같은 종목의 과거 분석·예측 결과(온톨로지 누적 저장소 · 예측 원장)

확장 데이터(extended): analysis.json 의 quarterly(분기 실적)·flows(투자자별 수급)·short(공매도),
시뮬레이션 result.json 의 monte_carlo(주가 경로), 온톨로지 저장소의 history(과거 기록)
"""

import os
import json
from typing import Any, Dict, List, Optional

from ..config import Config


def _read(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def reports_dir() -> str:
    return os.path.join(Config.UPLOAD_FOLDER, 'stock_reports')


def analysis_dir(task_id: str) -> str:
    return os.path.join(Config.UPLOAD_FOLDER, 'stock_analyses', task_id)


def paths_for_report(report_id: str) -> Dict[str, str]:
    rdir = os.path.join(reports_dir(), report_id)
    report = _read(os.path.join(rdir, 'report.json')) or {}
    sim_id = report.get('simulation_id', '')
    sim_dir = os.path.join(Config.UPLOAD_FOLDER, 'stock_simulations', sim_id) if sim_id else ''
    sim = _read(os.path.join(sim_dir, 'result.json')) if sim_dir else None
    task_id = (sim or {}).get('task_id', '')
    if not task_id and sim_dir:
        meta = _read(os.path.join(sim_dir, 'meta.json')) or {}
        task_id = meta.get('task_id', '')
    return {
        'report_dir': rdir, 'simulation_id': sim_id, 'sim_dir': sim_dir,
        'task_id': task_id, 'analysis_dir': analysis_dir(task_id) if task_id else '',
    }


def load_analysis(report_id: Optional[str] = None, task_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not task_id and report_id:
        task_id = paths_for_report(report_id).get('task_id')
    return _read(os.path.join(analysis_dir(task_id), 'analysis.json')) if task_id else None


def load_research(report_id: Optional[str] = None, task_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not task_id and report_id:
        task_id = paths_for_report(report_id).get('task_id')
    return _read(os.path.join(analysis_dir(task_id), 'research.json')) if task_id else None


def load_extended(report_id: Optional[str] = None, task_id: Optional[str] = None,
                  include_history: bool = True) -> Dict[str, Any]:
    """사실표 확장 데이터 (없는 항목은 빠짐)"""
    out: Dict[str, Any] = {}
    paths = paths_for_report(report_id) if report_id else {}
    task_id = task_id or paths.get('task_id')
    an = _read(os.path.join(analysis_dir(task_id), 'analysis.json')) if task_id else None
    fd = (an or {}).get('financial_data') or {}
    for k in ('quarterly', 'flows', 'short'):
        if fd.get(k):
            out[k] = fd[k]
    if paths.get('sim_dir'):
        sim = _read(os.path.join(paths['sim_dir'], 'result.json')) or {}
        if isinstance(sim.get('monte_carlo'), dict):
            out['monte_carlo'] = sim['monte_carlo']
    ticker = fd.get('ticker') or (an or {}).get('ticker')
    if include_history and ticker:
        try:
            from . import ontology_store
            out['history'] = ontology_store.history_facts(ticker, exclude_report=report_id)
        except Exception:
            pass
    return out


def extended_fact_lines(extended: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """확장 데이터 → [{'kind', 'text', 'role'?, 'url'?, 'date'?}]"""
    if not extended:
        return []
    lines: List[Dict[str, Any]] = []
    try:
        from .market_ext import fact_lines
        lines += fact_lines(extended)
    except Exception:
        pass
    mc = extended.get('monte_carlo') or {}
    if mc.get('direction_probs'):
        dp, rp = mc['direction_probs'], mc.get('return_pct') or {}
        t = (f"몬테카를로 {mc.get('paths', 0):,}경로·{mc.get('horizon_days')}거래일: 상승(+3%↑) {dp.get('상승')}% · "
             f"보합 {dp.get('보합')}% · 하락(−3%↓) {dp.get('하락')}% · 수익률 중앙값 {_fmt(rp.get('p50'), '%', 1)} "
             f"(5~95%: {_fmt(rp.get('p5'), '%', 1)} ~ {_fmt(rp.get('p95'), '%', 1)}) · VaR95 {_fmt(mc.get('var95_pct'), '%', 1)}")
        lines.append({'kind': 'mc', 'role': 'mc', 'text': t})
    for h in (extended.get('history') or [])[:5]:
        if isinstance(h, dict) and h.get('text'):
            lines.append({'kind': 'history', 'text': h['text'], 'date': h.get('date'), 'role': h.get('role')})
    return lines


def _num(v):
    try:
        return float(str(v).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _recomm_label(v) -> str:
    x = _num(v)
    if x is None:
        return '-'
    return '강력매수' if x >= 4.5 else '매수' if x >= 3.5 else '중립' if x >= 2.5 else '매도' if x >= 1.5 else '강력매도'


def _fmt(v, unit='', nd=1):
    if v is None:
        return '-'
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(v) >= 1000:
        return f"{v:,.0f}{unit}"
    return f"{v:.{nd}f}{unit}"


def build_fact_sheet(ctx: Dict[str, Any], research: Optional[Dict[str, Any]] = None,
                     kg: Optional[Dict[str, Any]] = None, fundamentals: Optional[Dict[str, Any]] = None,
                     max_research: int = 14, extended: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """ctx = StockJudge.collect_context() 결과. 번호가 붙은 사실 목록을 만든다."""
    facts: List[Dict[str, Any]] = []

    def add(kind, text, **kw):
        prefix = {'price': 'F', 'fund': 'F', 'vote': 'F', 'earnings': 'F', 'flow': 'F', 'short': 'F', 'mc': 'F',
                  'research': 'R', 'kg': 'K', 'community': 'C', 'history': 'H'}[kind]
        n = sum(1 for f in facts if f['id'].startswith(prefix)) + 1
        facts.append({'id': f'{prefix}{n}', 'kind': kind, 'text': text, **kw})

    p = ctx.get('price') or {}
    if p.get('has_data'):
        add('price', f"현재가 {_fmt(p.get('last_close'), '원')} · 5일선 {_fmt(p.get('ma5'), '원')} · 20일선 {_fmt(p.get('ma20'), '원')} · 60일선 {_fmt(p.get('ma60'), '원')}")
        add('price', f"수익률 5일 {_fmt(p.get('chg_5d_pct'), '%', 2)} · 20일 {_fmt(p.get('chg_20d_pct'), '%', 2)} · 60일 {_fmt(p.get('chg_60d_pct'), '%', 2)}")
        add('price', f"RSI(14) {_fmt(p.get('rsi14'))} · 20일 일간 변동성 {_fmt(p.get('daily_vol_20d_pct'), '%', 2)}")
        add('price', f"60일 고가 {_fmt(p.get('high_60d'), '원')} · 60일 저가 {_fmt(p.get('low_60d'), '원')}")
    f = fundamentals or {}
    if f:
        bits = [f"{lbl} {_fmt(f.get(k), unit, 2)}" for k, lbl, unit in
                (('PER', 'PER', '배'), ('PBR', 'PBR', '배'), ('EPS', 'EPS', '원'), ('DIV', '배당수익률', '%'),
                 ('외인소진율', '외국인 보유', '%'), ('52주최고', '52주 최고', '원'), ('52주최저', '52주 최저', '원'))
                if f.get(k) not in (None, '')]
        if bits:
            add('fund', ' · '.join(bits))
        # 증권사 컨센서스 (있을 때만)
        cons = []
        if f.get('목표주가평균'):
            last = (ctx.get('price') or {}).get('last_close')
            up = f" (현재가 대비 {(_num(f['목표주가평균']) / last - 1) * 100:+.1f}%)" if last and _num(f.get('목표주가평균')) else ''
            cons.append(f"증권사 목표주가 평균 {_fmt(f['목표주가평균'], '원')}{up}")
        if f.get('투자의견평균'):
            cons.append(f"투자의견 평균 {_fmt(f['투자의견평균'], '', 2)}({_recomm_label(f['투자의견평균'])}, 5점=강력매수)")
        if f.get('선행EPS'):
            cons.append(f"선행 EPS {_fmt(f['선행EPS'], '원')}")
        if f.get('선행PER'):
            cons.append(f"선행 PER {_fmt(f['선행PER'], '배', 2)}")
        if cons:
            add('fund', '컨센서스' + (f"({f.get('컨센서스일')})" if f.get('컨센서스일') else '') + ': ' + ' · '.join(cons))
    v = ctx.get('votes') or {}
    if v:
        add('vote', f"에이전트 {v.get('num_agents')}명 시뮬레이션: 매수 {v.get('buy_pct')}% / 보합 {v.get('hold_pct')}% / 매도 {v.get('sell_pct')}%")
    # 분기 실적·수급·공매도·몬테카를로 (F#) · 과거 기록 (H#)
    for ln in extended_fact_lines(extended):
        add(ln['kind'], ln['text'], **{k: ln[k] for k in ('role', 'date', 'url') if ln.get(k)})

    if research:
        items = sorted(research.get('findings') or [], key=lambda x: -(x.get('importance') or 0))[:max_research]
        for it in items:
            src = it.get('source') or {}
            s = it.get('sentiment') or {}
            tone = {'positive': '긍정', 'negative': '부정', 'neutral': '중립'}.get(s.get('label'), '')
            add('research', f"{it.get('claim', '')}" + (f" ({tone})" if tone else ''),
                url=src.get('url'), date=it.get('date'), publisher=src.get('publisher') or src.get('title'),
                ref=it.get('id'))
        sent = (research.get('sentiment') or {})
        news = sent.get('news') or {}
        if news.get('n'):
            add('research', f"뉴스 {news.get('n')}건 감성: 긍정 {news.get('pos')} · 중립 {news.get('neu')} · 부정 {news.get('neg')} (점수 {_fmt(news.get('score'), '', 2)}, {sent.get('engine', '')})")
        com = sent.get('community') or {}
        if com.get('n'):
            add('community', f"커뮤니티 글 {com.get('n')}건 심리: 긍정 {com.get('pos')} · 중립 {com.get('neu')} · 부정 {com.get('neg')} (점수 {_fmt(com.get('score'), '', 2)})")

    if kg:
        for c in (kg.get('cqs') or [])[:4]:
            add('kg', f"CQ {c.get('question')} → {str(c.get('answer', ''))[:160]}")
        for a in (kg.get('adrs') or [])[:3]:
            add('kg', f"ADR {a.get('title')}: {str(a.get('decision', ''))[:160]} (확신 {a.get('confidence')})")
    return facts


def fact_sheet_text(facts: List[Dict[str, Any]], limit: int = 7000) -> str:
    lines = []
    for f in facts:
        extra = ''
        if f.get('publisher') or f.get('date'):
            extra = f" [{f.get('publisher') or ''}{' ' + str(f.get('date')) if f.get('date') else ''}]"
        lines.append(f"[{f['id']}] {f['text']}{extra}")
    return '\n'.join(lines)[:limit]
