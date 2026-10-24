"""
상태 스냅샷 — uploads/ 의 SQLite·JSON 을 일관된 사본으로 묶거나 되돌림 (클라우드 이전·백업용)

  snapshot(dest)  : SQLite 는 온라인 백업 API 로(쓰는 중이어도 일관), 나머지 파일은 그대로 복사
  restore(src)    : 스냅샷 폴더를 uploads/ 로 되돌림 (기존 파일은 덮어씀)
컨테이너는 디스크가 비휘발성이 아니므로 시작 시 restore, 주기적으로·종료 신호(SIGTERM) 때 snapshot 후
rclone 으로 R2 에 올립니다 (deploy/cloudflare/entrypoint.sh). PC 에서도 수동 백업에 쓸 수 있습니다.

제외: 캐시·임시 파일(*.tmp, -wal/-shm, __pycache__), 모델 캐시, 내려받은 실행 파일
"""

import json
import os
import shutil
import sqlite3
import sys
import time
from typing import Any, Dict

SKIP_DIRS = {'__pycache__', 'hf_cache', 'models', 'bin', 'tmp'}
SKIP_SUFFIX = ('.tmp', '-wal', '-shm', '-journal', '.lock')


def _upload_root() -> str:
    from ..config import Config
    return Config.UPLOAD_FOLDER


def snapshot(dest: str, src: str = None) -> Dict[str, Any]:
    src = src or _upload_root()
    t0 = time.time()
    n_db = n_file = size = 0
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(base, src)
        out_dir = os.path.join(dest, rel) if rel != '.' else dest
        os.makedirs(out_dir, exist_ok=True)
        for f in files:
            if f.endswith(SKIP_SUFFIX):
                continue
            s, d = os.path.join(base, f), os.path.join(out_dir, f)
            if f.endswith('.db'):
                try:
                    with sqlite3.connect(s, timeout=30) as a, sqlite3.connect(d) as b:
                        a.backup(b)
                    n_db += 1
                except sqlite3.Error:
                    shutil.copy2(s, d)
                    n_file += 1
            else:
                shutil.copy2(s, d)
                n_file += 1
            size += os.path.getsize(d)
    info = {'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'databases': n_db, 'files': n_file, 'bytes': size,
            'sec': round(time.time() - t0, 1)}
    with open(os.path.join(dest, '_snapshot.json'), 'w', encoding='utf-8') as f:
        json.dump(info, f)
    return info


def restore(src: str, dest: str = None) -> Dict[str, Any]:
    dest = dest or _upload_root()
    n = 0
    for base, _dirs, files in os.walk(src):
        rel = os.path.relpath(base, src)
        out_dir = os.path.join(dest, rel) if rel != '.' else dest
        os.makedirs(out_dir, exist_ok=True)
        for f in files:
            if f == '_snapshot.json':
                continue
            shutil.copy2(os.path.join(base, f), os.path.join(out_dir, f))
            n += 1
    return {'restored': n}


if __name__ == '__main__':      # python -m app.services.state_snapshot snapshot|restore <폴더>
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    if len(sys.argv) != 3 or sys.argv[1] not in ('snapshot', 'restore'):
        print('사용법: python -m app.services.state_snapshot snapshot|restore <폴더>')
        sys.exit(2)
    fn = snapshot if sys.argv[1] == 'snapshot' else restore
    print(json.dumps(fn(sys.argv[2]), ensure_ascii=False))
