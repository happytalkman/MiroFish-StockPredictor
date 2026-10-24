"""
알림 — 매매계획 가격 도달 감시 + 텔레그램 발송 + 화면 알림함

감시 대상: 예측 원장에서 아직 끝나지 않은 매수·분할매수 계획(진입가·목표가·손절가)
감시 시간: 평일(휴장일 제외) 09:00~15:40 KST, ALERT_INTERVAL 분마다(기본 5분)
알림 종류: entry(진입가 근접 ±0.5%) · target(목표가 도달) · stop(손절가 이탈) — 같은 계획·종류는 한 번만
발송: 화면 알림함(항상) + 텔레그램(TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID 설정 시)
설정: ALERTS(on/off, 기본 on)
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.alerts')
KST = timezone(timedelta(hours=9))
_lock = threading.RLock()
_state: Dict[str, Any] = {'thread': None, 'last_check': None, 'last_error': None, 'checked': 0}
KIND_KO = {'entry': '진입가 근접', 'target': '목표가 도달', 'stop': '손절가 이탈', 'watchlist_run': '관심종목 분석',
           'test': '테스트'}


def _db() -> sqlite3.Connection:
    d = os.path.join(Config.UPLOAD_FOLDER, 'ledger')
    os.makedirs(d, exist_ok=True)
    c = sqlite3.connect(os.path.join(d, 'ledger.db'), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, kind TEXT, report_id TEXT, ticker TEXT,
        title TEXT, body TEXT, price REAL, level REAL, read INTEGER DEFAULT 0, sent TEXT,
        UNIQUE(report_id, kind))''')
    return c


def enabled() -> bool:
    return (os.environ.get('ALERTS') or 'on').strip().lower() not in ('off', '0', 'false', 'no')


def telegram_ready() -> bool:
    return bool((os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip() and (os.environ.get('TELEGRAM_CHAT_ID') or '').strip())


def send_telegram(text: str) -> Dict[str, Any]:
    token = (os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip()
    chat = (os.environ.get('TELEGRAM_CHAT_ID') or '').strip()
    if not token or not chat:
        return {'ok': False, 'message': '텔레그램 봇 토큰·채팅 ID 가 설정되지 않았습니다'}
    try:
        r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                          json={'chat_id': chat, 'text': text[:3900], 'disable_web_page_preview': True}, timeout=12)
        j = r.json() if r.headers.get('content-type', '').startswith('application/json') else {}
        if r.status_code == 200 and j.get('ok'):
            return {'ok': True, 'message': '발송됨'}
        return {'ok': False, 'message': f"HTTP {r.status_code}: {str(j.get('description') or r.text)[:160]}"}
    except Exception as e:
        return {'ok': False, 'message': f'{type(e).__name__}: {str(e)[:160]}'}


def notify(kind: str, title: str, body: str = '', report_id: Optional[str] = None, ticker: Optional[str] = None,
           price: Optional[float] = None, level: Optional[float] = None) -> Optional[int]:
    """알림함에 기록하고(같은 계획·종류는 1회) 텔레그램으로도 보냄. 새로 기록했으면 id"""
    rid = report_id or f"{kind}:{datetime.now(KST).strftime('%Y%m%d%H%M%S%f')}"
    with _lock:
        c = _db()
        try:
            try:
                cur = c.execute('INSERT INTO alerts(created_at, kind, report_id, ticker, title, body, price, level) VALUES (?,?,?,?,?,?,?,?)',
                                (datetime.now(KST).isoformat(timespec='seconds'), kind, rid, ticker, title, body, price, level))
                aid = cur.lastrowid
                c.commit()
            except sqlite3.IntegrityError:
                return None
        finally:
            c.close()
    sent = None
    if telegram_ready():
        res = send_telegram(f"[WeAidStock] {title}\n{body}".strip())
        sent = 'telegram' if res['ok'] else f"실패: {res['message']}"
        with _lock:
            c = _db()
            try:
                c.execute('UPDATE alerts SET sent=? WHERE id=?', (sent, aid))
                c.commit()
            finally:
                c.close()
    logger.info(f"알림: {title}" + (f" (텔레그램 {sent})" if sent else ''))
    return aid


def list_alerts(unread_only: bool = False, limit: int = 50) -> Dict[str, Any]:
    with _lock:
        c = _db()
        try:
            rows = c.execute('SELECT * FROM alerts ' + ('WHERE read = 0 ' if unread_only else '') +
                             'ORDER BY id DESC LIMIT ?', (max(1, min(500, limit)),)).fetchall()
            unread = c.execute('SELECT COUNT(*) FROM alerts WHERE read = 0').fetchone()[0]
        finally:
            c.close()
    return {'items': [dict(r, kind_label=KIND_KO.get(r['kind'], r['kind'])) for r in rows], 'unread': unread}


def mark_read(ids: Optional[List[int]] = None) -> int:
    with _lock:
        c = _db()
        try:
            if ids:
                q = ','.join('?' * len(ids))
                n = c.execute(f'UPDATE alerts SET read = 1 WHERE id IN ({q})', ids).rowcount
            else:
                n = c.execute('UPDATE alerts SET read = 1 WHERE read = 0').rowcount
            c.commit()
            return n
        finally:
            c.close()


# ─────────────────────────────────────────────────────────── 가격 감시
def _quote(ticker: str) -> Optional[float]:
    from . import market_data as md
    try:
        d = md._get(f'{md.NAVER_M}/stock/{ticker}/basic').json()
        return float(str(d.get('closePrice')).replace(',', ''))
    except Exception:
        try:
            rows = md.fchart(ticker, 1)
            return rows[-1]['c'] if rows else None
        except Exception:
            return None


def check_once(now: Optional[datetime] = None, quote=None) -> Dict[str, Any]:
    """진행 중인 매수 계획의 현재가를 확인해 도달한 가격대마다 알림"""
    from . import prediction_ledger as ledger
    quote = quote or _quote
    preds = [p for p in ledger.list_predictions(limit=500)
             if p['status'] != 'scored' and p.get('strategy') in ('매수', '분할매수')
             and (p.get('plan_outcome') or {}).get('outcome') not in ('target', 'stop', 'horizon')]
    fired, prices = [], {}
    for p in preds:
        plan = p.get('plan') or {}
        t, s, e = plan.get('target_price'), plan.get('stop_price'), plan.get('entry_price')
        if not (t and s):
            continue
        if p['ticker'] not in prices:
            prices[p['ticker']] = quote(p['ticker'])
        px = prices[p['ticker']]
        if not px:
            continue
        name = f"{p['company']}({p['ticker']})"
        if px >= t:
            aid = notify('target', f"{name} 목표가 도달", f"현재가 {px:,.0f}원 ≥ 목표가 {t:,.0f}원 · 전략 {p['strategy']} ({p['as_of']} 판단)",
                         p['id'], p['ticker'], px, t)
            aid and fired.append(aid)
        elif px <= s:
            aid = notify('stop', f"{name} 손절가 이탈", f"현재가 {px:,.0f}원 ≤ 손절가 {s:,.0f}원 · 계획 점검 필요 ({p['as_of']} 판단)",
                         p['id'], p['ticker'], px, s)
            aid and fired.append(aid)
        elif e and abs(px / e - 1) <= 0.005:
            aid = notify('entry', f"{name} 진입가 근접", f"현재가 {px:,.0f}원 · 진입가 {e:,.0f}원 (±0.5%) · 목표 {t:,.0f} / 손절 {s:,.0f}",
                         p['id'], p['ticker'], px, e)
            aid and fired.append(aid)
    _state.update(last_check=datetime.now(KST).isoformat(timespec='seconds'), checked=len(preds))
    return {'checked': len(preds), 'fired': fired, 'prices': prices}


def _market_open(now: datetime) -> bool:
    from .theme_scheduler import is_business_day
    return is_business_day(now.date()) and (9, 0) <= (now.hour, now.minute) <= (15, 40)


def _loop():
    time.sleep(45)
    while True:
        try:
            now = datetime.now(KST)
            if enabled() and _market_open(now):
                check_once(now)
                _state['last_error'] = None
        except Exception as e:
            _state['last_error'] = str(e)[:200]
            logger.warning(f"가격 알림 확인 실패: {e}")
        try:
            iv = max(1, int(os.environ.get('ALERT_INTERVAL') or 5))
        except ValueError:
            iv = 5
        time.sleep(iv * 60)


def status() -> Dict[str, Any]:
    return {'enabled': enabled(), 'telegram': telegram_ready(), 'last_check': _state.get('last_check'),
            'checked': _state.get('checked'), 'last_error': _state.get('last_error'),
            'interval_min': os.environ.get('ALERT_INTERVAL') or '5'}


def start_scheduler(debug: bool) -> bool:
    if debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return False
    t = _state.get('thread')
    if t is not None and t.is_alive():
        return True
    t = threading.Thread(target=_loop, daemon=True, name='alerts-monitor')
    _state['thread'] = t
    t.start()
    return True
