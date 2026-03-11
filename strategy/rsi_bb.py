"""
RSI + 볼린저밴드 전략
- 과매도 구간(RSI < 30) + 볼린저밴드 하단 터치 → 매수
- 과매수 구간(RSI > 70) + 볼린저밴드 상단 터치 → 매도

참고: 존 볼린저 'Bollinger on Bollinger Bands' + Wilder RSI
암호화폐 / 국내주식 / 미국주식 모두 적용 가능
"""
import pandas as pd
from data.indicators import TechnicalIndicators
from strategy.base import BaseStrategy, TradeSignal, Signal, Market


class RSIBollingerStrategy(BaseStrategy):
    """RSI + 볼린저밴드 복합 전략"""

    def __init__(self,
                 rsi_oversold: float = 35,
                 rsi_overbought: float = 65,
                 bb_buy_pct: float = 0.25,
                 bb_sell_pct: float = 0.75):
        """
        Args:
            rsi_oversold: RSI 과매도 기준 (기본 35 - 암호화폐 변동성 고려)
            rsi_overbought: RSI 과매수 기준 (기본 65)
            bb_buy_pct: 볼린저밴드 매수 위치 (0.25 = 하위 25%)
            bb_sell_pct: 볼린저밴드 매도 위치 (0.75 = 상위 75%)
        """
        super().__init__("RSI_BB")
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.bb_buy_pct = bb_buy_pct
        self.bb_sell_pct = bb_sell_pct

    def generate_signal(self, df: pd.DataFrame, symbol: str, market: Market) -> TradeSignal:
        """RSI + 볼린저밴드 기반 매매 신호 생성"""
        if len(df) < 30:
            return self._make_signal(Signal.HOLD, symbol, market,
                                     self.get_current_price(df), "데이터 부족")

        # 지표 계산
        df = TechnicalIndicators.add_rsi(df.copy())
        df = TechnicalIndicators.add_bollinger_bands(df)
        df = TechnicalIndicators.add_macd(df)

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        price = float(latest["close"])
        rsi = float(latest.get("rsi14", 50))
        bb_pct = float(latest.get("bb_pct", 0.5))
        macd_hist = float(latest.get("macd_hist", 0))
        prev_macd_hist = float(prev.get("macd_hist", 0))

        # ── 매수 조건 ──────────────────────────────
        buy_conditions = {
            "RSI 과매도": rsi < self.rsi_oversold,
            "볼린저 하단": bb_pct < self.bb_buy_pct,
            "MACD 반등": macd_hist > prev_macd_hist,  # MACD 히스토그램 상승
        }
        buy_score = sum(buy_conditions.values())

        # ── 매도 조건 ──────────────────────────────
        sell_conditions = {
            "RSI 과매수": rsi > self.rsi_overbought,
            "볼린저 상단": bb_pct > self.bb_sell_pct,
            "MACD 하락": macd_hist < prev_macd_hist,  # MACD 히스토그램 하락
        }
        sell_score = sum(sell_conditions.values())

        # ── 신호 결정 ──────────────────────────────
        # 매수: 3개 조건 중 1개 이상 충족 (공격적 모드 - 더 자주 매수)
        if buy_score >= 1:
            reasons = [k for k, v in buy_conditions.items() if v]
            confidence = buy_score / 3
            return self._make_signal(
                Signal.BUY, symbol, market, price,
                f"매수조건: {', '.join(reasons)} (RSI:{rsi:.1f}, BB%:{bb_pct:.2f})",
                confidence
            )

        # 매도: 3개 조건 중 2개 이상 충족
        if sell_score >= 2:
            reasons = [k for k, v in sell_conditions.items() if v]
            confidence = sell_score / 3
            return self._make_signal(
                Signal.SELL, symbol, market, price,
                f"매도조건: {', '.join(reasons)} (RSI:{rsi:.1f}, BB%:{bb_pct:.2f})",
                confidence
            )

        return self._make_signal(
            Signal.HOLD, symbol, market, price,
            f"관망 (RSI:{rsi:.1f}, BB%:{bb_pct:.2f})"
        )


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    import pyupbit
    from data.collector import DataCollector

    print("\nRSI + 볼린저밴드 전략 테스트")
    print("=" * 50)

    collector = DataCollector()
    strategy = RSIBollingerStrategy()

    test_targets = [
        ("KRW-BTC", Market.CRYPTO),
        ("KRW-ETH", Market.CRYPTO),
        ("KRW-XRP", Market.CRYPTO),
    ]

    for symbol, market in test_targets:
        df = collector.get_crypto_ohlcv(symbol, count=100)
        if not df.empty:
            signal = strategy.generate_signal(df, symbol, market)
            icon = "🟢" if signal.signal == Signal.BUY else "🔴" if signal.signal == Signal.SELL else "⚪"
            print(f"\n{icon} {symbol}")
            print(f"   신호: {signal.signal.value} | 신뢰도: {signal.confidence:.0%}")
            print(f"   이유: {signal.reason}")
