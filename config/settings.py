"""
전역 설정 모듈
환경변수(.env)에서 설정값을 로드하고 전체 프로그램에서 사용
"""
import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# ─────────────────────────────────────────
# 한국투자증권 KIS API 설정
# ─────────────────────────────────────────
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
KIS_IS_PAPER_TRADING = os.getenv("KIS_IS_PAPER_TRADING", "true").lower() == "true"

# 모의투자 vs 실전투자 URL 분기
if KIS_IS_PAPER_TRADING:
    KIS_BASE_URL = "https://openapivts.koreainvestment.com:29443"   # 모의투자
else:
    KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"        # 실전투자

# ─────────────────────────────────────────
# 업비트 API 설정
# ─────────────────────────────────────────
UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY", "")
UPBIT_SECRET_KEY = os.getenv("UPBIT_SECRET_KEY", "")

# ─────────────────────────────────────────
# OpenAI 설정
# ─────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ─────────────────────────────────────────
# 텔레그램 알림 설정
# ─────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─────────────────────────────────────────
# 자본금 및 리스크 관리 설정
# ─────────────────────────────────────────
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100000"))       # 초기 자본금 (원)
MAX_POSITION_RATIO = float(os.getenv("MAX_POSITION_RATIO", "0.2"))    # 단일 종목 최대 비중
STOP_LOSS_RATIO = float(os.getenv("STOP_LOSS_RATIO", "0.03"))         # 손절 기준
TAKE_PROFIT_RATIO = float(os.getenv("TAKE_PROFIT_RATIO", "0.07"))     # 익절 기준
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "0.05"))       # 일일 최대 손실 한도
MAX_DRAWDOWN_LIMIT = float(os.getenv("MAX_DRAWDOWN_LIMIT", "0.30"))   # 최대 낙폭 한도

# ─────────────────────────────────────────
# 거래 대상 설정
# ─────────────────────────────────────────
# 국내주식 - 관심 종목 (코드)
KR_WATCHLIST = [
    "005930",   # 삼성전자
    "000660",   # SK하이닉스
    "035420",   # NAVER
    "035720",   # 카카오
    "051910",   # LG화학
]

# 미국주식 - 관심 종목
US_WATCHLIST = [
    "AAPL",     # 애플
    "MSFT",     # 마이크로소프트
    "NVDA",     # 엔비디아
    "GOOGL",    # 구글
    "TSLA",     # 테슬라
]

# 암호화폐 - 관심 마켓 (업비트 KRW 마켓)
CRYPTO_WATCHLIST = [
    "KRW-BTC",  # 비트코인
    "KRW-ETH",  # 이더리움
    "KRW-XRP",  # 리플
    "KRW-SOL",  # 솔라나
]

# ─────────────────────────────────────────
# 시간대 설정
# ─────────────────────────────────────────
TIMEZONE = "Asia/Seoul"

# 국내주식 거래 시간 (KST)
KR_MARKET_OPEN = "09:00"
KR_MARKET_CLOSE = "15:30"

# 미국주식 거래 시간 (KST 기준, 서머타임 적용 전)
US_MARKET_OPEN_KST = "23:30"    # 23:30 KST (서머타임: 22:30)
US_MARKET_CLOSE_KST = "06:00"   # 06:00 KST 다음날

# ─────────────────────────────────────────
# 로그 설정
# ─────────────────────────────────────────
LOG_DIR = "logs"
LOG_FILE = "logs/trading.log"
LOG_LEVEL = "INFO"


def validate_settings():
    """필수 설정값 유효성 검사"""
    errors = []

    if not KIS_APP_KEY:
        errors.append("KIS_APP_KEY가 설정되지 않았습니다")
    if not KIS_APP_SECRET:
        errors.append("KIS_APP_SECRET이 설정되지 않았습니다")
    if not UPBIT_ACCESS_KEY:
        errors.append("UPBIT_ACCESS_KEY가 설정되지 않았습니다")

    if errors:
        print("⚠️  설정 오류:")
        for e in errors:
            print(f"  - {e}")
        print("\n.env.example을 참고하여 .env 파일을 생성하세요")
        return False

    return True


if __name__ == "__main__":
    validate_settings()
    print(f"✅ 설정 로드 완료")
    print(f"  - 초기 자본금: {INITIAL_CAPITAL:,.0f}원")
    print(f"  - 모의투자 모드: {KIS_IS_PAPER_TRADING}")
    print(f"  - 손절 기준: -{STOP_LOSS_RATIO*100}%")
    print(f"  - 익절 기준: +{TAKE_PROFIT_RATIO*100}%")
