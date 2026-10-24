"""WeAidStock 회귀 테스트 — 표준 unittest (pytest 로도 실행 가능)

실행:  cd backend && python -m unittest discover -s tests -t . -v
      (또는 설정 화면 › 운영 지표 › 자체 점검)
네트워크·LLM 키 없이 돌아가도록 외부 호출은 모두 가짜로 바꿔 둡니다. 실제 데이터(uploads/)는 건드리지 않습니다.
"""
import os

os.environ['WEAIDSTOCK_TESTING'] = '1'
