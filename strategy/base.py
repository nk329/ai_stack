"""
전략 기본 클래스
모든 매매 전략의 공통 인터페이스 정의
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import pandas as pd

logger = logging.getLogger(__name__)


class Signal(Enum):
    """매매 신호"""
    BUY = "BUY"       # 매수
    SELL = "SELL"     # 매도
    HOLD = "HOLD"     # 관망


class Market(Enum):
    """시장 구분"""
    KR = "KR"         # 국내주식
    US = "US"         # 미국주식
    CRYPTO = "CRYPTO" # 암호화폐


@dataclass
class TradeSignal:
    """매매 신호 데이터"""
    signal: Signal
    market: Market
    symbol: str
    price: float
    reason: str                  # 신호 발생 이유
    confidence: float = 0.5      # 신뢰도 (0~1)
    strategy_name: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    def __str__(self):
        return (
            f"[{self.signal.value}] {self.symbol} @ {self.price:,.2f} "
            f"| 신뢰도: {self.confidence:.0%} | 이유: {self.reason}"
        )


class BaseStrategy(ABC):
    """전략 기본 클래스 (추상 클래스)
    
    모든 전략은 이 클래스를 상속받아 구현
    """

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"strategy.{name}")

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, symbol: str, market: Market) -> TradeSignal:
        """매매 신호 생성 (반드시 구현)
        Args:
            df: OHLCV + 지표가 포함된 DataFrame
            symbol: 종목코드/티커/마켓코드
            market: 시장 구분
        Returns:
            TradeSignal 객체
        """
        pass

    def get_current_price(self, df: pd.DataFrame) -> float:
        """DataFrame에서 현재가 추출"""
        if df.empty:
            return 0.0
        return float(df["close"].iloc[-1])

    def _make_signal(self, signal: Signal, symbol: str, market: Market,
                     price: float, reason: str, confidence: float = 0.5) -> TradeSignal:
        """신호 객체 생성 헬퍼"""
        ts = TradeSignal(
            signal=signal,
            market=market,
            symbol=symbol,
            price=price,
            reason=reason,
            confidence=confidence,
            strategy_name=self.name,
        )
        self.logger.info(f"신호 생성: {ts}")
        return ts
