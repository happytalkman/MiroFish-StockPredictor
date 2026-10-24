"""
WeAidStock 백엔드 실행 진입점
"""

import os
import sys

# Windows 콘솔 한글 깨짐 문제 해결: 모든 import 전에 UTF-8 인코딩 설정
if sys.platform == 'win32':
    # Python이 UTF-8을 사용하도록 환경 변수 설정
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    # 표준 출력 스트림을 UTF-8로 재설정
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 프로젝트 루트 디렉터리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.config import Config


def main():
    """메인 함수"""
    # 설정 검증
    errors = Config.validate()
    if errors:
        print("설정 오류:")
        for err in errors:
            print(f"  - {err}")
        print("\n.env 파일의 설정을 확인하세요")
        sys.exit(1)
    
    # 앱 생성
    app = create_app()
    
    # 실행 설정 가져오기
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5001))
    debug = Config.DEBUG

    # 설정 화면의 '백엔드 재시작'으로 띄워진 경우: 이전 프로세스가 포트를 놓을 때까지 최대 30초 대기
    if os.environ.pop('WEAID_WAIT_PORT', None):
        import socket
        import time
        for _ in range(60):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(('0.0.0.0' if host in ('0.0.0.0', '') else host, port))
                break
            except OSError:
                time.sleep(0.5)
            finally:
                s.close()
    
    # 서버 시작 — Werkzeug 대화형 디버거는 항상 끔 (공개 주소·같은 와이파이에서 오류 화면으로 코드가 드러나지 않도록)
    app.run(host=host, port=port, debug=debug, use_debugger=False, threaded=True)


if __name__ == '__main__':
    main()

