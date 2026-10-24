"""검색용 KOSPI/KOSDAQ 종목 목록 (일별 시세와 무관)

마스터 파일 형식: https://github.com/koreainvestment/open-trading-api/tree/main/stocks_info

네트워크가 불안정해도 검색이 되도록 3단계로 대체합니다.
  1) 메모리 캐시(6시간)  2) 새로 내려받기 → 디스크 저장(uploads/cache/stock_master_*.json)
  3) 내려받기 실패 시 디스크 사본(기간 무관) → 그것도 없으면 내장 주요 종목 목록
"""
import json
import os
from io import BytesIO
from threading import Lock
from time import monotonic, time
from zipfile import ZipFile

import requests

_cache = {}
_lock = Lock()
_TTL = 6 * 60 * 60
_RETRY_AFTER_FAIL = 5 * 60          # 내려받기 실패 후 5분간은 다시 시도하지 않음 (검색이 느려지지 않도록)
_last_fail = {}
_FORMATS = {'KOSPI': ('kospi', 228), 'KOSDAQ': ('kosdaq', 222)}

# 내려받기·디스크 사본이 모두 없을 때 쓰는 주요 종목 (시가총액 상위·자주 찾는 종목)
_SEED = {
    'KOSPI': """005930 삼성전자|005935 삼성전자우|000660 SK하이닉스|373220 LG에너지솔루션|207940 삼성바이오로직스|005380 현대차|000270 기아|
068270 셀트리온|005490 POSCO홀딩스|035420 NAVER|035720 카카오|051910 LG화학|006400 삼성SDI|105560 KB금융|055550 신한지주|
086790 하나금융지주|316140 우리금융지주|024110 기업은행|138040 메리츠금융지주|012330 현대모비스|028260 삼성물산|066570 LG전자|
003550 LG|032830 삼성생명|000810 삼성화재|096770 SK이노베이션|017670 SK텔레콤|030200 KT|032640 LG유플러스|034730 SK|
015760 한국전력|018260 삼성에스디에스|009150 삼성전기|010950 S-Oil|011200 HMM|034020 두산에너빌리티|012450 한화에어로스페이스|
329180 HD현대중공업|009540 HD한국조선해양|267250 HD현대|267260 HD현대일렉트릭|042660 한화오션|010140 삼성중공업|010130 고려아연|
003670 포스코퓨처엠|047050 포스코인터내셔널|352820 하이브|259960 크래프톤|036570 엔씨소프트|251270 넷마블|323410 카카오뱅크|
377300 카카오페이|011170 롯데케미칼|047810 한국항공우주|064350 현대로템|079550 LIG넥스원|272210 한화시스템|000880 한화|
010120 LS ELECTRIC|006260 LS|000100 유한양행|128940 한미약품|302440 SK바이오사이언스|326030 SK바이오팜|090430 아모레퍼시픽|
097950 CJ제일제당|001040 CJ|033780 KT&G|004020 현대제철|000720 현대건설|011780 금호석유|006800 미래에셋증권|071050 한국금융지주|
042700 한미반도체|454910 두산로보틱스|450080 에코프로머티|003490 대한항공|180640 한진칼|021240 코웨이|139480 이마트|023530 롯데쇼핑|
004170 신세계|282330 BGF리테일|078930 GS|000150 두산|241560 두산밥캣|034220 LG디스플레이|011070 LG이노텍|051900 LG생활건강|
035250 강원랜드|036460 한국가스공사|161390 한국타이어앤테크놀로지|086280 현대글로비스|000120 CJ대한통운|271560 오리온|004370 농심|
402340 SK스퀘어|000990 DB하이텍|003230 삼양식품|012750 에스원|009830 한화솔루션|005830 DB손해보험|001450 현대해상""",
    'KOSDAQ': """247540 에코프로비엠|086520 에코프로|196170 알테오젠|028300 HLB|263750 펄어비스|293490 카카오게임즈|035900 JYP Ent.|
041510 에스엠|122870 와이지엔터테인먼트|058470 리노공업|240810 원익IPS|357780 솔브레인|039030 이오테크닉스|403870 HPSP|145020 휴젤|
214150 클래시스|214450 파마리서치|277810 레인보우로보틱스|036930 주성엔지니어링|095340 ISC|077360 덕산하이메탈|141080 리가켐바이오|
068760 셀트리온제약|087010 펩트론|298380 에이비엘바이오|112040 위메이드|348370 엔켐|039200 오스코텍|310210 보로노이|089030 테크윙|
067310 하나마이크론|222800 심텍|140860 파크시스템스|036540 SFA반도체""",
}


class CatalogUnavailable(RuntimeError):
    pass


def _cache_dir():
    try:
        from ..config import Config
        d = os.path.join(Config.UPLOAD_FOLDER, 'cache')
    except Exception:
        d = os.path.join(os.path.dirname(__file__), '..', '..', 'uploads', 'cache')
    os.makedirs(d, exist_ok=True)
    return d


def _disk_path(market):
    return os.path.join(_cache_dir(), f'stock_master_{market.lower()}.json')


def _save_disk(market, rows):
    try:
        tmp = _disk_path(market) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'saved_at': time(), 'rows': rows}, f, ensure_ascii=False)
        os.replace(tmp, _disk_path(market))
    except Exception:
        pass


def _load_disk(market):
    try:
        with open(_disk_path(market), 'r', encoding='utf-8') as f:
            rows = json.load(f).get('rows') or []
        return rows if rows else None
    except Exception:
        return None


def _seed(market):
    out = []
    for item in _SEED.get(market, '').replace('\n', '').split('|'):
        item = item.strip()
        if len(item) > 7 and item[:6].isalnum():
            out.append({'ticker': item[:6], 'name': item[7:].strip(), 'market': market})
    return out


def _download(market):
    prefix, tail_length = _FORMATS[market]
    filename = f'{prefix}_code.mst'
    response = requests.get(
        f'https://new.real.download.dws.co.kr/common/master/{filename}.zip',
        timeout=(5, 10),
    )
    response.raise_for_status()
    with ZipFile(BytesIO(response.content)) as archive:
        # The published tail lengths include the final newline.
        text = archive.read(filename).decode('cp949').replace('\r\n', '\n')
    result = []
    for line in text.splitlines():
        row = line + '\n'
        head = row[:-tail_length]
        ticker, name = head[:9].strip(), head[21:].strip()
        group = row[-tail_length:][:2]
        if len(ticker) == 6 and ticker.isalnum() and name and group == 'ST':
            result.append({'ticker': ticker, 'name': name, 'market': market})
    if not result:
        raise CatalogUnavailable('종목 목록 응답이 비어 있거나 형식이 변경되었습니다.')
    return result


_bg = {}


def _bg_download(market):
    """백그라운드 내려받기 — 성공하면 메모리·디스크 캐시 갱신"""
    try:
        rows = _download(market)
        _save_disk(market, rows)
        _cache[market] = (monotonic(), rows, 'download')
    except Exception:
        _last_fail[market] = monotonic()
    finally:
        _bg.pop(market, None)


def _rows_for(market, wait_sec=8.0):
    """메모리 → 내려받기(최대 wait_sec 대기, 넘으면 백그라운드 계속) → 디스크 사본 → 내장 목록. (rows, source)"""
    import threading
    cached = _cache.get(market)
    if cached is not None and monotonic() - cached[0] < _TTL:
        return cached[1], cached[2]
    if market not in _bg and monotonic() - _last_fail.get(market, -1e9) >= _RETRY_AFTER_FAIL:
        t = threading.Thread(target=_bg_download, args=(market,), daemon=True)
        _bg[market] = t
        t.start()
        disk = _load_disk(market)
        t.join(0.2 if disk else wait_sec)       # 디스크 사본이 있으면 기다리지 않음
        cached = _cache.get(market)
        if cached is not None and cached[2] == 'download' and monotonic() - cached[0] < _TTL:
            return cached[1], 'download'
    rows = _load_disk(market)
    if rows:
        return rows, 'disk'
    rows = _seed(market)
    if rows:
        return rows, 'seed'
    raise CatalogUnavailable('종목 목록을 불러오지 못했습니다. 잠시 후 다시 검색해 주세요.')


_RANK = {}


def _seed_rank():
    """내장 목록 순서(대략 시가총액 순)를 인기 순위로 사용"""
    if not _RANK:
        for m in ('KOSPI', 'KOSDAQ'):
            for i, r in enumerate(_seed(m)):
                _RANK.setdefault(r['ticker'], i if m == 'KOSPI' else 200 + i)
    return _RANK


def catalog_source():
    return {m: (_cache.get(m) or (0, [], 'none'))[2] for m in _FORMATS}


def search_catalog(query, market='ALL', limit=20):
    markets = list(_FORMATS) if market == 'ALL' else [market]
    rows = []
    with _lock:
        for i, current in enumerate(markets):
            got, _src = _rows_for(current, wait_sec=6.0 if i == 0 else 1.0)
            rows.extend(got)
    needle = query.casefold().replace(' ', '')
    matches = [r for r in rows if needle in r['name'].casefold().replace(' ', '') or needle in r['ticker'].casefold()]
    rank = _seed_rank()
    matches.sort(key=lambda r: (r['ticker'].casefold() != needle and r['name'].casefold() != needle,
                                not r['name'].casefold().startswith(needle), rank.get(r['ticker'], 9999),
                                len(r['name']), r['ticker']))
    return matches[:limit]
