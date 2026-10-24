"""
투자위원회 토론 (Investment Committee Debate) — 5단계 판단의 입력

참가자
  - bull      : 강세 애널리스트 — 상승 논거
  - bear      : 약세 애널리스트 — 하락·위험 논거
  - risk      : 리스크 매니저   — 핵심 리스크·비중 한도·손절 규칙·양측 질문
  - moderator : 의장            — 사실 검증(정정)·쟁점 정리·최종 요약

진행
  1라운드 : 강세·약세 모두발언(병렬) → 리스크 매니저 검토 → 의장 사실 검증(+ 코드로 사실 ID 자동 점검)
  2라운드 : 강세·약세 반박(병렬, 상대 논거·의장 정정·리스크 질문 반영) → 의장 쟁점 갱신
  3라운드 : rounds=3 이고 미해결 쟁점이 남았을 때만
  최종    : 의장 요약 (점수·합의·쟁점별 판단·권고 전략)

근거 규칙: stock_evidence.build_fact_sheet 로 만든 번호 사실표([F#]/[R#]/[C#]/[K#])만 인정.
결과: stock_reports/<report_id>/committee.json
"""

import os
import re
import json
import time
import hashlib
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..utils.logger import get_logger
from .stock_judge import StockJudge, _reports_dir
from .stock_knowledge_graph import StockKnowledgeGraph
from . import stock_evidence as sev

logger = get_logger('mirofish.stock_committee')

ROLES: Dict[str, str] = {
    'bull': '강세 애널리스트',
    'bear': '약세 애널리스트',
    'risk': '리스크 매니저',
    'moderator': '의장',
}
KIND_LABEL = {'opening': '모두발언', 'review': '리스크 검토', 'factcheck': '사실 검증',
              'rebuttal': '반박', 'summary': '최종 요약'}
STRATEGIES = {'buy': '매수', 'accumulate': '분할매수', 'hold': '관망', 'reduce': '비중축소', 'sell': '매도'}
CONSENSUS = {'bull': '강세 우위', 'bear': '약세 우위', 'split': '의견 양분'}
WHO_LABEL = {'bull': '강세', 'bear': '약세', 'risk': '리스크', 'moderator': '의장'}

# 사실 ID: 목록 항목 정규화용(느슨) / 본문 안 [F1, R2] 추출용(대괄호만)
_LOOSE_ID = re.compile(r'(?<![A-Z0-9])([FRCKH])\s*-?\s*(\d{1,3})(?!\d)')
_BRACKET_IDS = re.compile(r'\[\s*([FRCKHfrckh]\s*\d[\dFRCKHfrckh\s,·/\-]{0,60})\]')
# 사실표 대조용 수치 (단위가 붙은 값만 검사)
_CLAIM_NUM = re.compile(r'(?<![\w.])([+-]?\d[\d,]*(?:\.\d+)?)\s*(%p|%|％|원|배)')
_ANY_NUM = re.compile(r'\d[\d,]*(?:\.\d+)?')

# 발언 한 번의 예상 글자 수 (진행률 추정용)
_EXPECTED_CHARS = {'opening': 1600, 'review': 1100, 'factcheck': 1600, 'rebuttal': 1400, 'summary': 1500}


# ─────────────────────────────────────────────────────────────── 정규화 도우미
def _norm_ids(v: Any) -> List[str]:
    if v is None:
        return []
    items = [v] if isinstance(v, (str, int)) else (list(v) if isinstance(v, (list, tuple)) else [])
    out: List[str] = []
    for it in items:
        if not isinstance(it, (str, int)):
            continue
        for letter, n in _LOOSE_ID.findall(str(it).upper()):
            fid = f"{letter}{int(n)}"
            if fid not in out:
                out.append(fid)
    return out


def _ids_in_text(text: str) -> List[str]:
    out: List[str] = []
    for grp in _BRACKET_IDS.findall(text or ''):
        for fid in _norm_ids(grp):
            if fid not in out:
                out.append(fid)
    return out


def _s(v: Any, n: int = 400) -> str:
    if v is None:
        return ''
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    return re.sub(r'\s+', ' ', str(v)).strip()[:n]


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v == v else None
    if isinstance(v, str):
        m = re.search(r'-?\d[\d,]*(?:\.\d+)?', v)
        if m:
            try:
                return float(m.group(0).replace(',', ''))
            except ValueError:
                return None
    return None


def _clamp(v: Any, lo: float, hi: float, default: Optional[float] = None) -> Optional[float]:
    x = _num(v)
    if x is None:
        return default
    return round(max(lo, min(hi, x)), 1)


def _int_clamp(v: Any, lo: int, hi: int, default: int) -> int:
    x = _num(v)
    return default if x is None else int(max(lo, min(hi, round(x))))


def _norm_who(v: Any) -> str:
    s = str(v or '').strip().lower()
    if s in ROLES:
        return s
    if '강세' in s or 'bull' in s:
        return 'bull'
    if '약세' in s or 'bear' in s:
        return 'bear'
    if '리스크' in s or 'risk' in s:
        return 'risk'
    if '의장' in s or 'moder' in s:
        return 'moderator'
    return ''


def _norm_strategy(v: Any) -> Optional[str]:
    s = str(v or '').strip().lower()
    if s in STRATEGIES:
        return s
    for key, words in (('accumulate', ('분할', 'accum')), ('reduce', ('축소', 'reduce', 'trim')),
                       ('sell', ('매도', 'sell')), ('buy', ('매수', 'buy')), ('hold', ('관망', '보유', 'hold'))):
        if any(w in s for w in words):
            return key
    return None


def _norm_consensus(v: Any) -> Optional[str]:
    s = str(v or '').strip().lower()
    if s in CONSENSUS:
        return s
    if '강세' in s or 'bull' in s:
        return 'bull'
    if '약세' in s or 'bear' in s:
        return 'bear'
    if any(w in s for w in ('양분', '중립', 'split', 'mixed', 'neutral')):
        return 'split'
    return None


def _norm_leaning(v: Any) -> str:
    s = str(v or '').strip().lower()
    if s in ('bull', 'bear', 'neutral'):
        return s
    if '강세' in s or 'bull' in s:
        return 'bull'
    if '약세' in s or 'bear' in s:
        return 'bear'
    return 'neutral'


def _norm_issue_id(v: Any, idx: int) -> str:
    m = re.search(r'(\d+)', str(v or ''))
    return f"I{int(m.group(1))}" if m else f"I{idx + 1}"


def _numbers(text: str) -> List[float]:
    out = []
    for m in _ANY_NUM.findall(text or ''):
        try:
            out.append(abs(float(m.replace(',', ''))))
        except ValueError:
            pass
    return out


def _unverified_numbers(claim: str, fact_nums: List[float]) -> List[str]:
    """주장 속 단위(%·원·배) 수치 중 사실표 어디에도 없는 값 — 참고용(정정으로 세지 않음)"""
    bad = []
    for raw, unit in _CLAIM_NUM.findall(claim or ''):
        try:
            x = abs(float(raw.replace(',', '')))
        except ValueError:
            continue
        if x == 0:
            continue
        if not any(abs(x - y) <= max(0.051, y * 0.002) for y in fact_nums):
            tok = f"{raw}{unit}"
            if tok not in bad:
                bad.append(tok)
    return bad[:4]


def facts_signature(facts: List[Dict[str, Any]]) -> str:
    raw = json.dumps([[f.get('id'), f.get('text')] for f in facts or []], ensure_ascii=False)
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────── 서비스
_ID_LIST_RE = re.compile(r'\[\s*["\']?([FRCKH]\d{1,3})["\']?(?:\s*,\s*["\']?[FRCKH]\d{1,3}["\']?)*\s*\]')


def _tidy_ids(obj: Any) -> Any:
    """LLM 이 문장에 넣은 ["F6","R4"] 같은 JSON 배열 표기를 [F6][R4] 로 정리 (제자리 수정)"""
    def fix(t: str) -> str:
        return _ID_LIST_RE.sub(lambda m: ''.join(f'[{x}]' for x in re.findall(r'[FRCKH]\d{1,3}', m.group(0))), t)
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, str):
                obj[k] = fix(v)
            else:
                _tidy_ids(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = fix(v)
            else:
                _tidy_ids(v)
    return obj


# 진행 중인 토론 (report_id → 끝나면 set 되는 Event) — 판단 단계가 같은 토론을 중복으로 열지 않도록
_INFLIGHT: Dict[str, threading.Event] = {}
_INFLIGHT_LOCK = threading.Lock()


def inflight_event(report_id: str) -> Optional[threading.Event]:
    with _INFLIGHT_LOCK:
        return _INFLIGHT.get(report_id)


class InvestmentCommittee:

    def __init__(self):
        self.kgsvc = StockKnowledgeGraph()
        self.judge: StockJudge = self.kgsvc.judge

    # ---------------------------------------------------------- 캐시
    @staticmethod
    def path(report_id: str) -> str:
        return os.path.join(_reports_dir(), report_id, 'committee.json')

    @staticmethod
    def load_cached(report_id: str) -> Optional[Dict[str, Any]]:
        p = InvestmentCommittee.path(report_id)
        if not os.path.exists(p):
            return None
        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.warning(f"committee.json 읽기 실패 [{report_id}]: {e}")
            return None

    @staticmethod
    def _save(report_id: str, data: Dict[str, Any]) -> None:
        _tidy_ids(data)
        p = InvestmentCommittee.path(report_id)
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    # ---------------------------------------------------------- 사실표
    def build_facts(self, report_id: str, ctx: Optional[Dict[str, Any]] = None,
                    kg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if ctx is None:
            ctx = self.judge.collect_context(report_id)
        if kg is None:
            kg = StockKnowledgeGraph.load(report_id) or {}
        research, fundamentals = None, None
        try:
            research = sev.load_research(report_id=report_id)
        except Exception as e:
            logger.warning(f"[{report_id}] research.json 읽기 실패: {e}")
        try:
            an = sev.load_analysis(report_id=report_id) or {}
            fund = (an.get('financial_data') or {}).get('fundamental')
            if isinstance(fund, list) and fund and isinstance(fund[-1], dict):
                fundamentals = fund[-1]
            elif isinstance(fund, dict):
                fundamentals = fund
        except Exception as e:
            logger.warning(f"[{report_id}] 재무 데이터 읽기 실패: {e}")
        extended = None
        try:
            extended = sev.load_extended(report_id=report_id)
        except Exception as e:
            logger.warning(f"[{report_id}] 확장 데이터 읽기 실패(무시): {e}")
        return sev.build_fact_sheet(ctx, research, kg or None, fundamentals, extended=extended)

    # ---------------------------------------------------------- 프롬프트
    @staticmethod
    def _system(role: str) -> str:
        persona = {
            'bull': ('상승 시나리오를 옹호합니다. 사실표에서 상승 근거를 찾아 가장 설득력 있게 논증하되, '
                     '불리한 사실을 숨기거나 왜곡하지 않습니다.'),
            'bear': ('하락·위험 시나리오를 옹호합니다. 사실표에서 하락 근거와 약점을 찾아 가장 설득력 있게 논증하되, '
                     '유리한 사실을 숨기거나 왜곡하지 않습니다.'),
            'risk': ('어느 편도 들지 않고 손실 관리를 책임집니다. 최악의 경우, 손절 규칙, 계좌 대비 비중 한도를 따지고 '
                     '양측 논리의 빈틈을 질문합니다.'),
            'moderator': ('어느 편도 들지 않는 공정한 진행자입니다. 모든 주장을 사실표와 한 줄씩 대조해 틀린 수치·잘못된 인용·'
                          '사실표에 없는 내용을 바로잡고, 핵심 쟁점을 정리하며, 사실 인용의 정확성과 논증의 질로만 평가합니다.'),
        }[role]
        return (f"당신은 한국 주식 투자위원회의 {ROLES[role]}입니다. {persona}\n"
                "규칙:\n"
                "1) [사실표]에 번호가 붙은 사실(F#·R#·C#·K#)만 근거로 인정됩니다. 사실표에 없는 수치·사실·뉴스는 쓰지 말 것.\n"
                "2) 모든 주장에 사실 ID 를 인용할 것 (예: [\"F1\", \"R2\"]). 사실표에 없는 ID 를 만들지 말 것.\n"
                "3) 수치는 사실표 값을 그대로 옮기고, 계산한 값이면 계산 근거를 짧게 밝힐 것.\n"
                "4) 모든 텍스트는 자연스러운 한국어. JSON 객체 한 개만 출력.")

    @staticmethod
    def _args_text(args: List[Dict[str, Any]], prefix: str, n: int = 6, claim_len: int = 220) -> str:
        if not args:
            return '(발언 없음)'
        return "\n".join(
            f"{prefix}-{i + 1}. {_s(a.get('claim'), claim_len)} [{', '.join(a.get('facts') or []) or 'ID 없음'}]"
            f" (강도 {a.get('strength')})" for i, a in enumerate(args[:n]))

    @staticmethod
    def _rebuttal_text(reb: Optional[Dict[str, Any]], n: int = 4) -> str:
        if not reb or not reb.get('rebuttals'):
            return '(반박 없음)'
        lines = [f"- 반박 대상 「{_s(r.get('target_claim'), 120)}」 → {_s(r.get('response'), 220)} "
                 f"[{', '.join(r.get('facts') or []) or 'ID 없음'}]" for r in reb['rebuttals'][:n]]
        if reb.get('concessions'):
            lines.append("- 인정: " + " / ".join(_s(c, 120) for c in reb['concessions'][:3]))
        if reb.get('answer_to_risk'):
            lines.append("- 리스크 질문 답변: " + _s(reb['answer_to_risk'], 200))
        return "\n".join(lines)

    @staticmethod
    def _corrections_text(corrs: List[Dict[str, Any]], n: int = 8) -> str:
        if not corrs:
            return '(정정 없음)'
        return "\n".join(
            f"- ({WHO_LABEL.get(c.get('who'), '공통')}) 「{_s(c.get('claim'), 100)}」 문제: {_s(c.get('problem'), 100)}"
            f" → 정정: {_s(c.get('correction'), 140)}"
            + (f" [{', '.join(c.get('correct_fact_ids') or [])}]" if c.get('correct_fact_ids') else '')
            for c in corrs[-n:])

    @staticmethod
    def _issues_text(issues: List[Dict[str, Any]]) -> str:
        if not issues:
            return '(정리된 쟁점 없음)'
        return "\n".join(f"- {i['id']} [{'해결' if i.get('status') == 'resolved' else '미해결'}] {_s(i.get('issue'), 140)}"
                         f" (강세: {_s(i.get('bull_view'), 110)} / 약세: {_s(i.get('bear_view'), 110)})"
                         for i in issues)

    # ---------------------------------------------------------- 정규화
    def _norm_args(self, raw: Any, valid: set, fact_nums: List[float], text_key: str = 'claim',
                   keep: Tuple[str, ...] = ()) -> List[Dict[str, Any]]:
        out = []
        for it in (raw if isinstance(raw, list) else []):
            if isinstance(it, str):
                it = {text_key: it}
            if not isinstance(it, dict):
                continue
            text = _s(it.get(text_key) or it.get('claim') or it.get('text'), 400)
            if not text:
                continue
            ids = _norm_ids(it.get('facts') or it.get('fact_ids') or it.get('refs') or it.get('evidence'))
            for fid in _ids_in_text(text):
                if fid not in ids:
                    ids.append(fid)
            # 본문 속 [F1, R2] 표기는 facts 로 옮기고 본문에서는 지움 (화면에서 칩으로 따로 표시)
            text = re.sub(r'\s{2,}', ' ', _BRACKET_IDS.sub('', text)).strip(' ,·')
            if not text:
                continue
            item = {text_key: text, 'facts': ids,
                    'valid_facts': [x for x in ids if x in valid],
                    'invalid_facts': [x for x in ids if x not in valid]}
            item['flag'] = 'no_facts' if not ids else ('bad_ids' if item['invalid_facts'] else None)
            if not item['valid_facts'] and ids:
                item['flag'] = 'bad_ids'
            nums = _unverified_numbers(text, fact_nums)
            if nums:
                item['unverified_numbers'] = nums
            for k in keep:
                if k == 'strength':
                    item['strength'] = _int_clamp(it.get('strength'), 1, 5, 3)
                elif k == 'severity':
                    item['severity'] = _int_clamp(it.get('severity'), 1, 5, 3)
                elif k == 'target_claim':
                    item['target_claim'] = _s(it.get('target_claim') or it.get('target'), 300)
            out.append(item)
        return out[:8]

    def _norm_opening(self, role: str, d: Optional[Dict[str, Any]], valid: set, fact_nums: List[float]) -> Dict[str, Any]:
        d = d if isinstance(d, dict) else {}
        pv = d.get('price_view') if isinstance(d.get('price_view'), dict) else {}
        thesis = _s(d.get('thesis'), 500)
        return {
            'stance': role,
            'thesis': thesis,
            'thesis_facts': _ids_in_text(thesis),
            'arguments': self._norm_args(d.get('arguments'), valid, fact_nums, 'claim', ('strength',)),
            'price_view': {'target': _num(pv.get('target')), 'horizon': _s(pv.get('horizon'), 40)},
            'confidence': _clamp(d.get('confidence'), 0, 100),
        }

    def _norm_risk(self, d: Optional[Dict[str, Any]], valid: set, fact_nums: List[float]) -> Dict[str, Any]:
        d = d if isinstance(d, dict) else {}
        q = d.get('questions') if isinstance(d.get('questions'), dict) else {}
        return {
            'key_risks': self._norm_args(d.get('key_risks'), valid, fact_nums, 'risk', ('severity',)),
            'position_limit_pct': _clamp(d.get('position_limit_pct'), 0, 100),
            'stop_rule': _s(d.get('stop_rule'), 300),
            'questions': {'bull': _s(q.get('bull'), 300), 'bear': _s(q.get('bear'), 300)},
        }

    def _norm_rebuttal(self, d: Optional[Dict[str, Any]], valid: set, fact_nums: List[float]) -> Dict[str, Any]:
        d = d if isinstance(d, dict) else {}
        return {
            'rebuttals': self._norm_args(d.get('rebuttals'), valid, fact_nums, 'response', ('target_claim',)),
            'concessions': [_s(x, 300) for x in (d.get('concessions') or []) if _s(x)][:5]
            if isinstance(d.get('concessions'), list) else [],
            'answer_to_risk': _s(d.get('answer_to_risk'), 400),
            'updated_confidence': _clamp(d.get('updated_confidence'), 0, 100),
        }

    @staticmethod
    def _norm_corrections(d: Optional[Dict[str, Any]], valid: set, rnd: int) -> List[Dict[str, Any]]:
        out = []
        for c in ((d or {}).get('corrections') or []):
            if not isinstance(c, dict):
                continue
            claim, problem, corr = _s(c.get('claim'), 300), _s(c.get('problem'), 300), _s(c.get('correction'), 400)
            if not (claim or problem or corr):
                continue
            ids = [x for x in _norm_ids(c.get('correct_fact_ids') or c.get('facts')) if x in valid]
            out.append({'round': rnd, 'who': _norm_who(c.get('who')), 'claim': claim, 'problem': problem,
                        'correct_fact_ids': ids, 'correction': corr, 'source': 'moderator'})
        return out[:12]

    @staticmethod
    def _merge_issues(prev: List[Dict[str, Any]], d: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cur = [dict(i) for i in prev]
        by_id = {i['id']: i for i in cur}
        for it in ((d or {}).get('issues') or []):
            if not isinstance(it, dict):
                continue
            m = re.search(r'(\d+)', str(it.get('id') or ''))
            if m:
                iid = f"I{int(m.group(1))}"
            else:   # id 가 없으면 기존 번호와 겹치지 않는 다음 번호
                nums = [int(x.group(1)) for x in (re.search(r'(\d+)', i['id']) for i in cur) if x]
                iid = f"I{max(nums + [0]) + 1}"
            status = 'resolved' if str(it.get('status', '')).lower() in ('resolved', '해결') else 'open'
            fields = {'issue': _s(it.get('issue'), 300), 'bull_view': _s(it.get('bull_view'), 300),
                      'bear_view': _s(it.get('bear_view'), 300)}
            if iid in by_id:
                tgt = by_id[iid]
                for k, v in fields.items():
                    if v:
                        tgt[k] = v
                tgt['status'] = status
            elif fields['issue']:
                new = {'id': iid, **fields, 'status': status}
                cur.append(new)
                by_id[iid] = new
        return cur[:8]

    def _norm_final(self, d: Optional[Dict[str, Any]], issues: List[Dict[str, Any]], risk: Dict[str, Any],
                    corrections: List[Dict[str, Any]]) -> Dict[str, Any]:
        d = d if isinstance(d, dict) else {}
        bull = _clamp(d.get('bull_score'), 0, 100, 50.0)
        bear = _clamp(d.get('bear_score'), 0, 100, 50.0)
        consensus = _norm_consensus(d.get('consensus'))
        if not consensus:
            consensus = 'bull' if bull - bear >= 10 else 'bear' if bear - bull >= 10 else 'split'
        imap = {i['id']: i for i in issues}
        key_issues = []
        for idx, k in enumerate(d.get('key_issues') or []):
            if not isinstance(k, dict):
                continue
            iid = _norm_issue_id(k.get('id'), idx)
            base = imap.get(iid, {})
            text = _s(k.get('issue'), 300) or base.get('issue', '')
            if not text:
                continue
            key_issues.append({'id': iid, 'issue': text, 'leaning': _norm_leaning(k.get('leaning')),
                               'why': _s(k.get('why'), 400),
                               'bull_view': base.get('bull_view', ''), 'bear_view': base.get('bear_view', ''),
                               'status': base.get('status', 'open')})
        if not key_issues:
            for i in sorted(issues, key=lambda x: x.get('status') != 'open')[:4]:
                key_issues.append({'id': i['id'], 'issue': i['issue'], 'leaning': 'neutral', 'why': '',
                                   'bull_view': i.get('bull_view', ''), 'bear_view': i.get('bear_view', ''),
                                   'status': i.get('status', 'open')})
        unresolved = [_s(x, 300) for x in (d.get('unresolved') or []) if _s(x)] if isinstance(d.get('unresolved'), list) else []
        if not unresolved:
            unresolved = [i['issue'] for i in issues if i.get('status') == 'open']
        strat = _norm_strategy(d.get('recommended_strategy')) or {'bull': 'accumulate', 'bear': 'reduce', 'split': 'hold'}[consensus]
        cap = _clamp(d.get('position_cap_pct'), 0, 100)
        if cap is None:
            cap = risk.get('position_limit_pct')
        return {
            'summary': _s(d.get('summary'), 1500),
            'bull_score': bull, 'bear_score': bear,
            'consensus': consensus, 'consensus_label': CONSENSUS[consensus],
            'key_issues': key_issues[:5],
            'unresolved': unresolved[:6],
            'recommended_strategy': strat, 'recommended_label': STRATEGIES[strat],
            'position_cap_pct': cap,
            'fact_corrections_count': len(corrections),
        }

    def _fallback_final(self, openings: Dict[str, Dict[str, Any]], issues: List[Dict[str, Any]],
                        risk: Dict[str, Any], corrections: List[Dict[str, Any]]) -> Dict[str, Any]:
        def score(role):
            args = openings.get(role, {}).get('arguments') or []
            if not args:
                return 0.0
            good = [a for a in args if a.get('valid_facts')]
            base = sum(a.get('strength', 3) for a in good) / (5 * len(args)) * 100
            pen = 6 * sum(1 for c in corrections if c.get('who') == role)
            return float(max(0, min(100, round(base - pen))))
        b, s = score('bull'), score('bear')
        nb = sum(1 for a in openings.get('bull', {}).get('arguments') or [] if a.get('valid_facts'))
        ns = sum(1 for a in openings.get('bear', {}).get('arguments') or [] if a.get('valid_facts'))
        out = self._norm_final({'bull_score': b, 'bear_score': s}, issues, risk, corrections)
        out['summary'] = (f"의장 최종 요약을 받지 못해 규칙으로 집계했습니다 — 사실 인용이 유효한 논거: 강세 {nb}개 · 약세 {ns}개, "
                          f"정정 {len(corrections)}건. 점수는 유효 논거의 강도에서 정정 건수를 감점한 값입니다.")
        out['fallback'] = True
        return out

    # ---------------------------------------------------------- 사실 ID 자동 점검 (코드)
    @staticmethod
    def _audit(items: List[Dict[str, Any]], who: str, rnd: int, text_key: str,
               label: str) -> Tuple[List[Dict[str, Any]], List[str]]:
        corrs, notes = [], []
        for i, a in enumerate(items):
            text = a.get(text_key, '')
            if a.get('flag') == 'no_facts':
                problem = '사실 ID 인용 없음'
                fix = '사실표로 뒷받침되지 않은 주장 — 근거로 인정하지 않습니다.'
            elif a.get('flag') == 'bad_ids' or a.get('invalid_facts'):
                bad = ', '.join(a.get('invalid_facts') or [])
                problem = f"사실표에 없는 ID 인용: {bad}"
                fix = (f"유효한 인용({', '.join(a['valid_facts'])})만 인정합니다." if a.get('valid_facts')
                       else '인용이 모두 무효 — 근거로 인정하지 않습니다.')
            else:
                continue
            corrs.append({'round': rnd, 'who': who, 'claim': _s(text, 300), 'problem': problem,
                          'correct_fact_ids': [], 'correction': fix, 'source': 'auto'})
            notes.append(f"- {label}-{i + 1}: {problem}")
        return corrs, notes

    @staticmethod
    def _merge_corrections(moderator: List[Dict[str, Any]], auto: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def key(c):
            return (c.get('who'), re.sub(r'\W+', '', c.get('claim') or '')[:24])
        seen = {key(c) for c in moderator}
        out = list(moderator)
        for c in auto:
            k = key(c)
            # 의장이 같은 주장을 이미 정정했으면 자동 점검 결과는 생략
            if k in seen or any(k[0] == m.get('who') and k[1] and k[1][:12] in re.sub(r'\W+', '', m.get('claim') or '')
                                for m in moderator):
                continue
            seen.add(k)
            out.append(c)
        return out

    # ---------------------------------------------------------- 발언 → 한국어 글
    @staticmethod
    def _chips(ids: List[str]) -> str:
        return f" [{', '.join(ids)}]" if ids else ''

    def _render(self, kind: str, c: Dict[str, Any]) -> str:
        if not c:
            return ''
        L: List[str] = []
        if kind == 'opening':
            if c.get('thesis'):
                L.append(c['thesis'])
            for i, a in enumerate(c.get('arguments') or []):
                L.append(f"{i + 1}. {a['claim']}{self._chips(a.get('facts'))} (강도 {a.get('strength', 3)}/5)")
            pv = c.get('price_view') or {}
            tail = []
            if pv.get('target'):
                tail.append(f"목표가 {pv['target']:,.0f}원")
            if pv.get('horizon'):
                tail.append(f"기간 {pv['horizon']}")
            if c.get('confidence') is not None:
                tail.append(f"확신 {c['confidence']:.0f}%")
            if tail:
                L.append(' · '.join(tail))
        elif kind == 'review':
            for i, r in enumerate(c.get('key_risks') or []):
                L.append(f"리스크 {i + 1}. {r['risk']}{self._chips(r.get('facts'))} (심각도 {r.get('severity', 3)}/5)")
            if c.get('position_limit_pct') is not None:
                L.append(f"비중 한도: 계좌의 {c['position_limit_pct']:g}% 이내")
            if c.get('stop_rule'):
                L.append(f"손절 규칙: {c['stop_rule']}")
            q = c.get('questions') or {}
            if q.get('bull'):
                L.append(f"강세 측에 질문: {q['bull']}")
            if q.get('bear'):
                L.append(f"약세 측에 질문: {q['bear']}")
        elif kind == 'factcheck':
            corrs = c.get('corrections') or []
            L.append(f"정정 {len(corrs)}건" if corrs else '정정할 사실 오류가 없습니다.')
            for x in corrs:
                L.append(f"- ({WHO_LABEL.get(x.get('who'), '공통')}) 「{x.get('claim')}」 — {x.get('problem')} → "
                         f"{x.get('correction')}{self._chips(x.get('correct_fact_ids'))}")
            for i in c.get('issues') or []:
                L.append(f"쟁점 {i['id']} ({'해결' if i.get('status') == 'resolved' else '미해결'}): {i.get('issue')}")
            if c.get('focus_next'):
                L.append(f"다음 초점: {c['focus_next']}")
        elif kind == 'rebuttal':
            for i, r in enumerate(c.get('rebuttals') or []):
                tgt = f"「{r['target_claim']}」에 대해 — " if r.get('target_claim') else ''
                L.append(f"{i + 1}. {tgt}{r['response']}{self._chips(r.get('facts'))}")
            for x in c.get('concessions') or []:
                L.append(f"인정: {x}")
            if c.get('answer_to_risk'):
                L.append(f"리스크 매니저 질문에 대한 답: {c['answer_to_risk']}")
            if c.get('updated_confidence') is not None:
                L.append(f"수정 확신 {c['updated_confidence']:.0f}%")
        elif kind == 'summary':
            if c.get('summary'):
                L.append(c['summary'])
            L.append(f"강세 {c.get('bull_score'):.0f} : 약세 {c.get('bear_score'):.0f} · {c.get('consensus_label')} · "
                     f"권고 {c.get('recommended_label')}"
                     + (f" (비중 상한 {c['position_cap_pct']:g}%)" if c.get('position_cap_pct') is not None else ''))
            for k in c.get('key_issues') or []:
                L.append(f"{k['id']} {k['issue']} → {WHO_LABEL.get(k['leaning'], '중립')} 쪽"
                         + (f": {k['why']}" if k.get('why') else ''))
        return "\n".join(L)

    # ---------------------------------------------------------- LLM 호출 (실패해도 토론은 계속)
    def _call(self, system: str, user: str, max_tokens: int, on_tick=None,
              required: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
        err, best = None, None
        for attempt in range(2):
            try:
                data, model = self.kgsvc._llm_json(system, user, max_tokens=max_tokens, fast=True, on_tick=on_tick)
            except Exception as e:
                # _llm_json 이 공급자(xAI → Vercel)별 재시도를 이미 했으므로 여기서 반복하지 않음
                if best:
                    return best[0], best[1], f"{err} — 재요청 실패: {str(e)[:200]}"
                return None, None, str(e)[:300]
            if isinstance(data, dict) and (not required or data.get(required)):
                return data, model, None
            err = f"응답 형식 불완전 ('{required}' 없음)" if isinstance(data, dict) else '응답이 JSON 객체가 아님'
            if isinstance(data, dict) and best is None:
                best = (data, model)
            if attempt == 0:
                logger.warning(f"투자위원회 발언 형식 불완전, 1회 재요청: {err}")
        # 불완전해도 쓸 수 있는 부분은 사용
        return (best[0], best[1], err) if best else (None, None, err)

    # ---------------------------------------------------------- 실행
    def run(self, report_id: str, progress: Optional[Callable] = None, rounds: int = 2, *,
            ctx: Optional[Dict[str, Any]] = None, kg: Optional[Dict[str, Any]] = None,
            facts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        with _INFLIGHT_LOCK:
            ev = _INFLIGHT.get(report_id)
            mine = ev is None
            if mine:
                ev = threading.Event()
                _INFLIGHT[report_id] = ev
        try:
            return self._run_impl(report_id, progress, rounds, ctx=ctx, kg=kg, facts=facts)
        finally:
            if mine:
                with _INFLIGHT_LOCK:
                    if _INFLIGHT.get(report_id) is ev:
                        _INFLIGHT.pop(report_id, None)
                ev.set()

    def _run_impl(self, report_id: str, progress: Optional[Callable] = None, rounds: int = 2, *,
                  ctx: Optional[Dict[str, Any]] = None, kg: Optional[Dict[str, Any]] = None,
                  facts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        t_start = time.time()
        try:
            rounds = int(rounds or 2)
        except (TypeError, ValueError):
            rounds = 2
        rounds = max(1, min(3, rounds))
        emit_lock = threading.Lock()

        def emit(pct, msg, **detail):
            if not progress:
                return
            try:
                with emit_lock:
                    progress(int(max(0, min(100, pct))), msg, {'stage': 'committee', **detail})
            except Exception:
                pass

        # ── 사실표
        if facts is None:
            emit(2, '사실표 구성 중 — 가격·재무·투표·리서치·지식그래프', round=0, speaker='moderator')
        if ctx is None:
            ctx = self.judge.collect_context(report_id)
        if kg is None:
            kg = StockKnowledgeGraph.load(report_id) or {}
        if facts is None:
            facts = self.build_facts(report_id, ctx, kg)
        if not facts:
            raise RuntimeError('사실표가 비어 있어 투자위원회를 열 수 없습니다 — 1·2단계 분석 데이터를 먼저 확인하세요.')
        valid = {f['id'] for f in facts}
        fact_nums = _numbers(' '.join(str(f.get('text', '')) for f in facts))
        ft = sev.fact_sheet_text(facts)
        cnt = {p: sum(1 for f in facts if f['id'].startswith(p)) for p in 'FRCKH'}
        head = f"[종목] {ctx.get('company_name', '')} ({ctx.get('ticker', '')})\n[사실표]\n{ft}\n"
        emit(6, f"사실표 준비 완료 — 사실 {len(facts)}개 (수치 {cnt['F']} · 리서치 {cnt['R']} · 커뮤니티 {cnt['C']} · 지식그래프 {cnt['K']})",
             round=0, speaker='moderator')

        # 진행률: 발언 슬롯별로 8~96% 를 균등 배분
        slots = ['r1', 'risk', 'mod1']
        for r in range(2, rounds + 1):
            slots += [f'reb{r}', f'mod{r}']
        slots.append('final')
        share = (96 - 8) / len(slots)

        def spct(slot, frac=0.0):
            return 8 + share * (slots.index(slot) + max(0.0, min(0.97, frac)))

        transcript: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        models: List[str] = []
        n_ok = 0

        def record(rnd, role, kind, content, model, err, sec):
            nonlocal n_ok
            entry = {'round': rnd, 'speaker': role, 'speaker_label': ROLES[role], 'kind': kind,
                     'kind_label': KIND_LABEL[kind], 'content': content, 'text': self._render(kind, content),
                     'sec': round(sec, 1)}
            if model:
                entry['model'] = model
                if model not in models:
                    models.append(model)
            if err:
                entry['error'] = err
                errors.append({'round': rnd, 'speaker': role, 'kind': kind, 'error': err})
                if not model:
                    # 호출 자체가 실패 — 자리표시 발언 (최종 요약은 규칙 집계 결과를 함께 보여 줌)
                    entry['failed'] = True
                    note = f"(발언 실패 — {err[:160]})"
                    entry['text'] = note + ('\n' + entry['text'] if kind == 'summary' and entry['text'] else '')
                elif not entry['text']:
                    entry['text'] = f"(응답 불완전 — {err[:160]})"
            if model:
                n_ok += 1
            transcript.append(entry)

        def single(slot, rnd, role, kind, user, max_tokens, required, label):
            t0 = time.time()

            def tick(nc, nr, sec):
                emit(spct(slot, nc / _EXPECTED_CHARS[kind]), f"{label} — 수신 {nc:,}자 ({sec:.0f}초)",
                     round=rnd, speaker=role)
            emit(spct(slot), f"{label} — 준비 중", round=rnd, speaker=role)
            data, model, err = self._call(self._system(role), user, max_tokens, tick, required)
            if err:
                logger.warning(f"[{report_id}] 투자위원회 {ROLES[role]} {rnd}라운드 {KIND_LABEL[kind]} 문제: {err}")
            return data, model, err, time.time() - t0

        def pair(slot, rnd, kind, users, max_tokens, required, title):
            """강세·약세 동시 발언. 글자 수를 한 줄로 합쳐 표시(로그가 번갈아 쌓이지 않게)."""
            chars = {'bull': 0, 'bear': 0}
            lk = threading.Lock()
            t0 = time.time()

            def mk_tick(role):
                def tick(nc, nr, sec):
                    with lk:
                        chars[role] = nc
                        b, s = chars['bull'], chars['bear']
                    emit(spct(slot, (b + s) / (2 * _EXPECTED_CHARS[kind])),
                         f"{title} (강세·약세 동시) — 강세 {b:,}자 · 약세 {s:,}자 ({time.time() - t0:.0f}초)",
                         round=rnd, speaker='bull+bear', speakers=['bull', 'bear'])
                return tick

            emit(spct(slot), f"{title} (강세·약세 동시) — 준비 중", round=rnd, speaker='bull+bear', speakers=['bull', 'bear'])

            def go(role):
                ts = time.time()
                data, model, err = self._call(self._system(role), users[role], max_tokens, mk_tick(role), required)
                return role, data, model, err, time.time() - ts
            with ThreadPoolExecutor(max_workers=2) as ex:
                res = {r[0]: r[1:] for r in ex.map(go, ['bull', 'bear'])}
            for role, (_, _, err, _) in res.items():
                if err:
                    logger.warning(f"[{report_id}] 투자위원회 {ROLES[role]} {rnd}라운드 {KIND_LABEL[kind]} 문제: {err}")
            return res

        # ══════════ 1라운드 — 모두발언 (병렬)
        schema_open = ('{"stance": "%s", "thesis": "핵심 논지 1~2문장(사실 ID 인용)", '
                       '"arguments": [{"claim": "주장 한 문장(수치 포함)", "facts": ["F1", "R2"], "strength": 1~5}], '
                       '"price_view": {"target": 목표가(원, 숫자, 사실표 가격에서 도출) 또는 null, "horizon": "예: 4주"}, '
                       '"confidence": 0~100}')
        users = {
            role: (f"{head}\n[과제] 1라운드 모두발언 — {'상승(강세)' if role == 'bull' else '하락·위험(약세)'} 논거를 제시하세요. "
                   f"논거 3~5개, 강한 것부터. 각 논거는 사실표 ID 를 1개 이상 인용.\n출력 스키마:\n{schema_open % role}")
            for role in ('bull', 'bear')
        }
        res = pair('r1', 1, 'opening', users, 2600, 'arguments', '1라운드 모두발언')
        openings: Dict[str, Dict[str, Any]] = {}
        for role in ('bull', 'bear'):
            data, model, err, sec = res[role]
            openings[role] = self._norm_opening(role, data, valid, fact_nums)
            record(1, role, 'opening', openings[role], model, err, sec)
        if not openings['bull']['arguments'] and not openings['bear']['arguments']:
            why = ' | '.join(e['error'] for e in errors[-2:]) or '응답 없음'
            raise RuntimeError(f"투자위원회 토론을 진행할 수 없습니다 — 강세·약세 애널리스트가 모두 발언하지 못했습니다 ({why})")
        def conf(role):
            v = openings[role]['confidence']
            return '-' if v is None else f"{v:.0f}"
        emit(spct('r1', 1.0), f"1라운드 모두발언 완료 — 강세 논거 {len(openings['bull']['arguments'])}개(확신 {conf('bull')}) · "
             f"약세 논거 {len(openings['bear']['arguments'])}개(확신 {conf('bear')})",
             round=1, speaker='bull+bear', speakers=['bull', 'bear'])

        bull_txt = self._args_text(openings['bull']['arguments'], '강세')
        bear_txt = self._args_text(openings['bear']['arguments'], '약세')

        # ── 리스크 매니저
        user = (f"{head}\n[강세 애널리스트] 논지: {openings['bull']['thesis'] or '-'}\n{bull_txt}\n\n"
                f"[약세 애널리스트] 논지: {openings['bear']['thesis'] or '-'}\n{bear_txt}\n\n"
                "[과제] 양측 논거를 검토해 핵심 리스크 3~4개, 계좌 대비 비중 한도, 손절 규칙을 정하고 "
                "강세·약세 측에 가장 날카로운 질문을 1개씩 던지세요.\n출력 스키마:\n"
                '{"key_risks": [{"risk": "리스크(수치 포함)", "facts": ["F3"], "severity": 1~5}], '
                '"position_limit_pct": 계좌 대비 최대 비중(%), "stop_rule": "손절 규칙(사실표 가격 기준)", '
                '"questions": {"bull": "강세 측 질문", "bear": "약세 측 질문"}}')
        data, model, err, sec = single('risk', 1, 'risk', 'review', user, 1800, 'key_risks', '리스크 매니저 검토 중')
        risk = self._norm_risk(data, valid, fact_nums)
        record(1, 'risk', 'review', risk, model, err, sec)
        emit(spct('risk', 1.0), f"리스크 매니저 검토 완료 — 핵심 리스크 {len(risk['key_risks'])}개"
             + (f" · 비중 한도 {risk['position_limit_pct']:g}%" if risk['position_limit_pct'] is not None else ''),
             round=1, speaker='risk')

        # ── 의장 사실 검증 (1라운드) + 코드 자동 점검
        corrections: List[Dict[str, Any]] = []
        issues: List[Dict[str, Any]] = []
        focus_next = ''

        def auto_check(rnd, groups):
            corrs, notes = [], []
            for items, who, key, label in groups:
                c, n = self._audit(items, who, rnd, key, label)
                corrs += c
                notes += n
            return corrs, notes

        auto1, notes1 = auto_check(1, [(openings['bull']['arguments'], 'bull', 'claim', '강세'),
                                       (openings['bear']['arguments'], 'bear', 'claim', '약세'),
                                       (risk['key_risks'], 'risk', 'risk', '리스크')])
        risk_txt = "\n".join(f"- 리스크-{i + 1}. {r['risk']} [{', '.join(r.get('facts') or []) or 'ID 없음'}]"
                             for i, r in enumerate(risk['key_risks'])) or '(검토 없음)'
        schema_fc = ('{"corrections": [{"who": "bull|bear|risk", "claim": "문제 주장 요약", "problem": "무엇이 틀렸는지(수치 불일치·인용 오류·사실표에 없음)", '
                     '"correct_fact_ids": ["F2"], "correction": "사실표 기준 올바른 내용"}], '
                     '"issues": [{"id": "I1", "issue": "핵심 쟁점", "bull_view": "강세 측 입장", "bear_view": "약세 측 입장", "status": "open|resolved"}], '
                     '"focus_next": "다음 라운드에서 양측이 답해야 할 초점"}')

        def moderator_check(rnd, speech_block, auto_notes, instructions):
            user_ = (f"{head}\n{speech_block}\n\n[코드 자동 점검 결과 — 사실 ID 인용 오류]\n"
                     f"{chr(10).join(auto_notes) or '- 인용 ID 오류 없음'}\n\n[기존 쟁점]\n{self._issues_text(issues)}\n\n"
                     f"[과제] {instructions}\n출력 스키마:\n{schema_fc}")
            return single(f'mod{rnd}', rnd, 'moderator', 'factcheck', user_, 2600, 'issues',
                          f"의장 사실 검증 중 ({rnd}라운드)")

        speech1 = (f"[1라운드 발언]\n강세 애널리스트 논지: {openings['bull']['thesis'] or '-'}\n{bull_txt}\n\n"
                   f"약세 애널리스트 논지: {openings['bear']['thesis'] or '-'}\n{bear_txt}\n\n리스크 매니저:\n{risk_txt}")
        data, model, err, sec = moderator_check(
            1, speech1, notes1,
            "사실 검증 — 모든 주장을 사실표와 대조하세요. 수치가 사실표와 다르거나, 인용한 사실이 주장을 뒷받침하지 않거나, "
            "사실표에 없는 내용이면 정정하세요(없으면 빈 배열). 이어서 양측이 부딪히는 핵심 쟁점 3~4개를 I1, I2… 로 정리하세요.")
        mod_corr = self._norm_corrections(data, valid, 1)
        round_corr = self._merge_corrections(mod_corr, auto1)
        corrections += round_corr
        issues = self._merge_issues(issues, data)
        focus_next = _s((data or {}).get('focus_next'), 400)
        record(1, 'moderator', 'factcheck', {'corrections': round_corr, 'issues': [dict(i) for i in issues],
                                             'focus_next': focus_next}, model, err, sec)
        n_open = sum(1 for i in issues if i['status'] == 'open')
        emit(spct('mod1', 1.0), f"1라운드 사실 검증 완료 — 정정 {len(round_corr)}건(자동 {sum(1 for c in round_corr if c['source'] == 'auto')}) · "
             f"쟁점 {len(issues)}개 (미해결 {n_open})", round=1, speaker='moderator')

        # ══════════ 2·3라운드 — 반박 (병렬) → 의장 갱신
        rebuttals: Dict[str, Dict[str, Dict[str, Any]]] = {}
        held = 1
        schema_reb = ('{"rebuttals": [{"target_claim": "반박하는 상대 주장", "response": "반박(수치·사실 ID 인용)", "facts": ["R3"]}], '
                      '"concessions": ["인정하는 상대 논점"], "answer_to_risk": "리스크 매니저 질문에 대한 답", "updated_confidence": 0~100}')
        for rnd in range(2, rounds + 1):
            if rnd >= 3 and not any(i['status'] == 'open' for i in issues):
                emit(spct(f'reb{rnd}'), f"미해결 쟁점이 없어 {rnd}라운드를 생략합니다", round=rnd, speaker='moderator')
                break
            prev_reb = rebuttals.get(str(rnd - 1), {})
            users = {}
            for role in ('bull', 'bear'):
                opp = 'bear' if role == 'bull' else 'bull'
                mine = bull_txt if role == 'bull' else bear_txt
                theirs = bear_txt if role == 'bull' else bull_txt
                block = (f"{head}\n[나의 1라운드 논거]\n{mine}\n\n[상대({ROLES[opp]}) 1라운드 논거]\n{theirs}\n")
                if prev_reb:
                    block += (f"\n[직전 라운드 나의 반박]\n{self._rebuttal_text(prev_reb.get(role))}\n"
                              f"\n[직전 라운드 상대의 반박]\n{self._rebuttal_text(prev_reb.get(opp))}\n")
                block += (f"\n[의장 정정 — 모든 참가자 대상]\n{self._corrections_text(corrections)}\n"
                          f"\n[리스크 매니저의 질문] {risk['questions'].get(role) or '(없음)'}\n"
                          f"\n[쟁점]\n{self._issues_text(issues)}\n[의장이 제시한 초점] {focus_next or '-'}\n\n"
                          f"[과제] {rnd}라운드 반박 — 상대 논거 중 가장 중요한 2~3개를 사실 ID 로 반박하고, 정정받은 내 주장은 바로잡고, "
                          f"인정할 점은 솔직히 인정하세요. 리스크 매니저 질문에 답하세요.\n출력 스키마:\n{schema_reb}")
                users[role] = block
            res = pair(f'reb{rnd}', rnd, 'rebuttal', users, 2400, 'rebuttals', f"{rnd}라운드 반박")
            rebuttals[str(rnd)] = {}
            for role in ('bull', 'bear'):
                data, model, err, sec = res[role]
                rebuttals[str(rnd)][role] = self._norm_rebuttal(data, valid, fact_nums)
                record(rnd, role, 'rebuttal', rebuttals[str(rnd)][role], model, err, sec)
            held = rnd
            rb, rs = rebuttals[str(rnd)]['bull'], rebuttals[str(rnd)]['bear']
            emit(spct(f'reb{rnd}', 1.0), f"{rnd}라운드 반박 완료 — 강세 반박 {len(rb['rebuttals'])}개·인정 {len(rb['concessions'])}개 · "
                 f"약세 반박 {len(rs['rebuttals'])}개·인정 {len(rs['concessions'])}개",
                 round=rnd, speaker='bull+bear', speakers=['bull', 'bear'])

            autoN, notesN = auto_check(rnd, [(rb['rebuttals'], 'bull', 'response', '강세 반박'),
                                             (rs['rebuttals'], 'bear', 'response', '약세 반박')])
            speech = (f"[{rnd}라운드 발언]\n강세 애널리스트 반박:\n{self._rebuttal_text(rb)}\n\n"
                      f"약세 애널리스트 반박:\n{self._rebuttal_text(rs)}")
            data, model, err, sec = moderator_check(
                rnd, speech, notesN,
                f"{rnd}라운드 사실 검증과 쟁점 갱신 — 반박의 수치·인용을 사실표와 대조해 정정하고(없으면 빈 배열), "
                "기존 쟁점의 id 를 유지한 채 bull_view·bear_view·status 를 갱신하세요. 한쪽이 인정했거나 사실로 결론 난 쟁점은 resolved.")
            mod_corr = self._norm_corrections(data, valid, rnd)
            round_corr = self._merge_corrections(mod_corr, autoN)
            corrections += round_corr
            issues = self._merge_issues(issues, data)
            focus_next = _s((data or {}).get('focus_next'), 400) or focus_next
            record(rnd, 'moderator', 'factcheck', {'corrections': round_corr, 'issues': [dict(i) for i in issues],
                                                   'focus_next': focus_next}, model, err, sec)
            n_open = sum(1 for i in issues if i['status'] == 'open')
            emit(spct(f'mod{rnd}', 1.0), f"{rnd}라운드 쟁점 정리 완료 — 정정 {len(round_corr)}건 · 미해결 쟁점 {n_open}개",
                 round=rnd, speaker='moderator')

        # ══════════ 최종 요약
        record_txt = [f"[강세 1라운드] 논지: {openings['bull']['thesis'] or '-'}\n{self._args_text(openings['bull']['arguments'], '강세', 5, 160)}",
                      f"[약세 1라운드] 논지: {openings['bear']['thesis'] or '-'}\n{self._args_text(openings['bear']['arguments'], '약세', 5, 160)}",
                      f"[리스크 매니저] 비중 한도 {risk['position_limit_pct'] if risk['position_limit_pct'] is not None else '-'}% · "
                      f"손절 {risk['stop_rule'] or '-'}\n{risk_txt}"]
        for r, rb in rebuttals.items():
            record_txt.append(f"[강세 {r}라운드 반박]\n{self._rebuttal_text(rb.get('bull'), 3)}")
            record_txt.append(f"[약세 {r}라운드 반박]\n{self._rebuttal_text(rb.get('bear'), 3)}")
        user = (f"{head}\n[토론 기록]\n" + "\n\n".join(record_txt)
                + f"\n\n[정정 누적 {len(corrections)}건]\n{self._corrections_text(corrections, 10)}"
                + f"\n\n[쟁점]\n{self._issues_text(issues)}\n\n"
                "[과제] 최종 요약 — 확신도나 말의 세기가 아니라 사실 인용의 정확성과 논증의 질로 양측을 0~100 으로 채점하세요. "
                "정정을 많이 받은 쪽은 감점. 쟁점별로 어느 쪽 논리가 사실에 더 부합하는지 판단하세요.\n출력 스키마:\n"
                '{"summary": "토론 요약 3~5문장(사실 ID 인용)", "bull_score": 0~100, "bear_score": 0~100, "consensus": "bull|bear|split", '
                '"key_issues": [{"id": "I1", "issue": "쟁점", "leaning": "bull|bear|neutral", "why": "판단 이유(사실 ID)"}], '
                '"unresolved": ["미해결 쟁점"], "recommended_strategy": "buy|accumulate|hold|reduce|sell", '
                '"position_cap_pct": 계좌 대비 최대 비중(%), "fact_corrections_count": 정수}')
        data, model, err, sec = single('final', held, 'moderator', 'summary', user, 2400, 'summary', '의장 최종 요약 중')
        if data and (data.get('summary') or data.get('bull_score') is not None):
            final = self._norm_final(data, issues, risk, corrections)
        else:
            final = self._fallback_final(openings, issues, risk, corrections)
        record(held, 'moderator', 'summary', final, model, err, sec)

        if n_ok == 0:
            raise RuntimeError("투자위원회 토론 실패 — 모든 발언자의 AI 호출이 실패했습니다 ("
                               + (' | '.join(e['error'] for e in errors[-3:]) or '원인 불명') + ")")

        all_items = (openings['bull']['arguments'] + openings['bear']['arguments'] + risk['key_risks']
                     + [x for rb in rebuttals.values() for side in rb.values() for x in side['rebuttals']])
        stats = {
            'facts': len(facts),
            'arguments': {'bull': len(openings['bull']['arguments']), 'bear': len(openings['bear']['arguments'])},
            'citations': sum(len(a.get('facts') or []) for a in all_items),
            'invalid_citations': sum(len(a.get('invalid_facts') or []) for a in all_items),
            'uncited_claims': sum(1 for a in all_items if a.get('flag') == 'no_facts'),
            'unverified_numbers': sum(len(a.get('unverified_numbers') or []) for a in all_items),
            'corrections': {'total': len(corrections),
                            'moderator': sum(1 for c in corrections if c['source'] == 'moderator'),
                            'auto': sum(1 for c in corrections if c['source'] == 'auto')},
            'llm_calls_ok': n_ok, 'llm_errors': len(errors),
        }
        out = {
            'report_id': report_id,
            'ticker': ctx.get('ticker', ''), 'company_name': ctx.get('company_name', ''),
            'created_at': datetime.now().isoformat(timespec='seconds'),
            'elapsed_sec': round(time.time() - t_start, 1),
            'model': ', '.join(models) or None,
            'rounds_requested': rounds, 'rounds_held': held,
            'participants': [{'key': k, 'label': v} for k, v in ROLES.items()],
            'facts': facts, 'facts_hash': facts_signature(facts),
            'openings': openings, 'risk_review': risk, 'rebuttals': rebuttals,
            'corrections': corrections, 'issues': issues, 'focus_next': focus_next,
            'final': final,
            'transcript': transcript,
            'errors': errors,
            'stats': stats,
        }
        self._save(report_id, out)
        emit(100, f"투자위원회 토론 완료 — 강세 {final['bull_score']:.0f} : 약세 {final['bear_score']:.0f} · "
             f"{final['consensus_label']} · 권고 {final['recommended_label']} · 정정 {len(corrections)}건",
             round=held, speaker='moderator')
        return out
