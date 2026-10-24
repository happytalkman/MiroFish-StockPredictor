"""
설정 관리
프로젝트 루트 디렉터리의 .env 파일에서 설정을 일괄 로드
"""

import os
from dotenv import load_dotenv

# 프로젝트 루트 디렉터리의 .env 파일 로드
# 경로: MiroFish/.env (backend/app/config.py 기준 상대 경로)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # 루트 디렉터리에 .env가 없으면 환경 변수에서 로드 시도 (운영 환경용)
    load_dotenv(override=True)


# ── LLM 공급자 선택 ─────────────────────────────────────────────────────────
# LLM_PROVIDER=auto(기본) : XAI_API_KEY 가 있고 LLM_BASE_URL 이 OpenAI(또는 미설정)이면 xAI Grok 사용
# LLM_PROVIDER=xai        : 항상 xAI 사용
# LLM_PROVIDER=openai     : .env 의 LLM_* 값을 그대로 사용
# os.environ 에 직접 반영하므로 서브프로세스(시뮬레이션 스크립트)에도 동일하게 적용됩니다.
_llm_provider = os.environ.get('LLM_PROVIDER', 'auto').strip().lower()
_xai_key = (os.environ.get('XAI_API_KEY') or '').strip()
_cur_base = (os.environ.get('LLM_BASE_URL') or 'https://api.openai.com/v1').lower()
if _xai_key and (_llm_provider == 'xai' or (_llm_provider == 'auto' and 'openai.com' in _cur_base)):
    os.environ['LLM_API_KEY'] = _xai_key
    os.environ['LLM_BASE_URL'] = os.environ.get('XAI_BASE_URL') or 'https://api.x.ai/v1'
    os.environ['LLM_MODEL_NAME'] = os.environ.get('XAI_LLM_MODEL') or 'grok-4.7'
    os.environ['LLM_PROVIDER_ACTIVE'] = 'xai'
else:
    os.environ['LLM_PROVIDER_ACTIVE'] = 'custom'


class Config:
    """Flask 설정 클래스"""
    
    # Flask 설정
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    
    # JSON 설정 - ASCII 이스케이프 비활성화, 한글 등을 그대로 표시 (\uXXXX 형식 대신)
    JSON_AS_ASCII = False
    
    # LLM 설정 (OpenAI 형식으로 통일)
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    
    # 투자 판단(Judgment) - xAI Grok 직접 호출 → Vercel AI Gateway 대체
    XAI_API_KEY = os.environ.get('XAI_API_KEY')
    AI_GATEWAY_API_KEY = os.environ.get('AI_GATEWAY_API_KEY')
    XAI_JUDGE_MODEL = os.environ.get('XAI_JUDGE_MODEL')  # 비우면 자동 선택

    # Zep 설정
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
    # 파일 업로드 설정
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # 텍스트 처리 설정
    DEFAULT_CHUNK_SIZE = 500  # 기본 청크 크기
    DEFAULT_CHUNK_OVERLAP = 50  # 기본 중첩 크기
    
    # OASIS 시뮬레이션 설정
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASIS 플랫폼 사용 가능 액션 설정
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent 설정
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls):
        """필수 설정 검증"""
        errors = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY가 설정되지 않았습니다")
        if not cls.ZEP_API_KEY:
            errors.append("ZEP_API_KEY가 설정되지 않았습니다")
        return errors

