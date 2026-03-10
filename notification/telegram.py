"""
텔레그램 알림 모듈
매수/매도 신호, 손절/익절, 일일 리포트 알림 전송
"""
import logging
import requests
from datetime import datetime
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """텔레그램 알림 전송 클래스"""

    BASE_URL = "https://api.telegram.org/bot"

    def __init__(self):
        self.token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id
                            and self.token != "your_bot_token_here")

        if not self.enabled:
            logger.warning("텔레그램 설정이 없습니다. .env에 BOT_TOKEN과 CHAT_ID를 설정하세요")

    def send(self, message: str) -> bool:
        """텔레그램 메시지 전송"""
        if not self.enabled:
            logger.info(f"[텔레그램 미설정] {message[:50]}...")
            return False
        try:
            url = f"{self.BASE_URL}{self.token}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
            }
            res = requests.post(url, data=data, timeout=10)
            res.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"텔레그램 전송 실패: {e}")
            return False

    def send_buy_signal(self, symbol: str, price: float, amount: float,
                        reason: str, strategy: str = ""):
        """매수 신호 알림"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        msg = (
            f"🟢 <b>매수 신호</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📌 종목: <b>{symbol}</b>\n"
            f"💰 가격: {price:,.0f}원\n"
            f"💵 투자금액: {amount:,.0f}원\n"
            f"📊 전략: {strategy}\n"
            f"📝 이유: {reason}\n"
            f"🕐 시간: {now}"
        )
        self.send(msg)

    def send_sell_signal(self, symbol: str, price: float, entry_price: float,
                         profit_loss: float, reason: str):
        """매도 신호 알림"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return_pct = (price - entry_price) / entry_price
        icon = "🔴" if profit_loss < 0 else "💚"
        msg = (
            f"{icon} <b>매도 신호</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📌 종목: <b>{symbol}</b>\n"
            f"💰 매도가: {price:,.0f}원\n"
            f"📈 매수가: {entry_price:,.0f}원\n"
            f"{'📉' if profit_loss < 0 else '📈'} 손익: {profit_loss:+,.0f}원 ({return_pct:+.2%})\n"
            f"📝 사유: {reason}\n"
            f"🕐 시간: {now}"
        )
        self.send(msg)

    def send_daily_report(self, capital: float, daily_pnl: float,
                          total_return: float, mdd: float, trades: int):
        """일일 리포트 알림"""
        today = datetime.now().strftime("%Y-%m-%d")
        icon = "📈" if daily_pnl >= 0 else "📉"
        msg = (
            f"{icon} <b>일일 리포트 ({today})</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 현재 자본금: {capital:,.0f}원\n"
            f"{'📈' if daily_pnl >= 0 else '📉'} 오늘 손익: {daily_pnl:+,.0f}원\n"
            f"📊 총 수익률: {total_return:+.2%}\n"
            f"⚠️ 최대 낙폭: {mdd:.2%}\n"
            f"🔄 오늘 거래: {trades}회"
        )
        self.send(msg)

    def send_alert(self, title: str, message: str, level: str = "INFO"):
        """일반 알림 전송"""
        icons = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "🚨", "SUCCESS": "✅"}
        icon = icons.get(level, "ℹ️")
        now = datetime.now().strftime("%H:%M")
        msg = f"{icon} <b>{title}</b>\n{message}\n🕐 {now}"
        self.send(msg)
