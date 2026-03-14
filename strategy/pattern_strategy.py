"""
차트 패턴 기반 전략 (Pattern Strategy)
사용자가 제공한 차트 패턴 이미지 분석 기반

매수 조건 (점수제, 60점 이상 매수):
  - MA 상승추세 확인 (MA20 > MA60)          : 25점
  - 쌍바닥 패턴 (Double Bottom)              : 30점
  - 핀버/망치형 캔들 (Pin Bar)               : 25점
  - 거래량 급증 확인                         : 20점
  - RSI 과매도 반등 (40~55 구간)            : 15점
  - MACD 골든크로스 / 히스토그램 반등        : 15점
  - 피보나치 되돌림 구간 (38~65%)           : 20점

매도 조건 (하나라도 해당 시 매도):
  - 쌍봉 패턴 (Double Top)                  : 즉시 매도
  - 큰 음봉 출현 (역V자 전환)               : 즉시 매도
  - MA 하향 돌파 (MA20 < MA60)              : 즉시 매도
  - RSI 과매수 (70 이상) + MACD 하락        : 즉시 매도
  - 흑삼병 패턴 (Three Black Crows)         : 즉시 매도
"""
import pandas as pd
import numpy as np
from data.indicators import TechnicalIndicators
from strategy.base import BaseStrategy, TradeSignal, Signal, Market


class PatternStrategy(BaseStrategy):
    """차트 패턴 기반 복합 전략"""

    def __init__(self,
                 buy_score_threshold: float = 55.0,  # 매수 발동 최소 점수
                 trailing_pct: float = 0.04,          # 트레일링 스탑 4%
                 rsi_oversold: float = 55,
                 rsi_overbought: float = 70):
        super().__init__("PATTERN")
        self.buy_threshold  = buy_score_threshold
        self.trailing_pct   = trailing_pct
        self.rsi_oversold   = rsi_oversold
        self.rsi_overbought = rsi_overbought

    # ─────────────────────────────────────────
    # 보조 패턴 감지 함수
    # ─────────────────────────────────────────
    def _is_double_bottom(self, df: pd.DataFrame, window: int = 20, tolerance: float = 0.03) -> bool:
        """쌍바닥 패턴 감지: 최근 window 캔들 내 두 저점이 비슷한 레벨 (±tolerance%)"""
        if len(df) < window:
            return False
        lows = df["low"].iloc[-window:].values
        # 로컬 최저점 찾기 (전후보다 낮은 점)
        local_mins = []
        for i in range(1, len(lows) - 1):
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                local_mins.append((i, lows[i]))
        if len(local_mins) < 2:
            return False
        # 마지막 두 저점이 비슷한 레벨인지 확인
        last_two = local_mins[-2:]
        p1, p2   = last_two[0][1], last_two[1][1]
        diff     = abs(p1 - p2) / max(p1, p2)
        # 두 저점 사이에 고점이 있어야 함 (V-V 형태)
        idx1, idx2  = last_two[0][0], last_two[1][0]
        mid_high    = lows[idx1:idx2+1].max()
        is_v_shape  = mid_high > max(p1, p2) * 1.02
        return diff < tolerance and is_v_shape

    def _is_double_top(self, df: pd.DataFrame, window: int = 20, tolerance: float = 0.03) -> bool:
        """쌍봉 패턴 감지: 최근 window 캔들 내 두 고점이 비슷한 레벨"""
        if len(df) < window:
            return False
        highs = df["high"].iloc[-window:].values
        local_maxs = []
        for i in range(1, len(highs) - 1):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                local_maxs.append((i, highs[i]))
        if len(local_maxs) < 2:
            return False
        last_two = local_maxs[-2:]
        p1, p2   = last_two[0][1], last_two[1][1]
        diff     = abs(p1 - p2) / max(p1, p2)
        # 두 고점 사이에 저점이 있어야 함
        idx1, idx2 = last_two[0][0], last_two[1][0]
        mid_low    = highs[idx1:idx2+1].min()
        is_m_shape = mid_low < min(p1, p2) * 0.98
        return diff < tolerance and is_m_shape

    def _is_pin_bar(self, candle: pd.Series, min_wick_ratio: float = 2.0) -> bool:
        """
        핀버(망치형) 캔들 감지
        - 아래꼬리 길이 > 몸통 × min_wick_ratio → 강세 반전 신호
        """
        body       = abs(float(candle["close"]) - float(candle["open"]))
        lower_wick = float(candle["open"])  - float(candle["low"]) if candle["close"] >= candle["open"] \
                     else float(candle["close"]) - float(candle["low"])
        upper_wick = float(candle["high"])  - float(candle["close"]) if candle["close"] >= candle["open"] \
                     else float(candle["high"]) - float(candle["open"])
        if body < 1e-9:
            return False
        # 아래꼬리가 몸통의 2배 이상, 위꼬리는 짧아야 함
        return lower_wick >= body * min_wick_ratio and upper_wick < lower_wick * 0.5

    def _is_shooting_star(self, candle: pd.Series, min_wick_ratio: float = 2.0) -> bool:
        """
        슈팅스타(역핀버) 감지
        - 위꼬리 길이 > 몸통 × min_wick_ratio → 하락 반전 신호
        """
        body       = abs(float(candle["close"]) - float(candle["open"]))
        upper_wick = float(candle["high"]) - max(float(candle["close"]), float(candle["open"]))
        lower_wick = min(float(candle["close"]), float(candle["open"])) - float(candle["low"])
        if body < 1e-9:
            return False
        return upper_wick >= body * min_wick_ratio and lower_wick < upper_wick * 0.5

    def _is_three_black_crows(self, df: pd.DataFrame) -> bool:
        """흑삼병 패턴: 연속 3개 음봉 + 각각 전봉보다 낮은 종가"""
        if len(df) < 3:
            return False
        last3 = df.iloc[-3:]
        conds = [
            last3.iloc[i]["close"] < last3.iloc[i]["open"]       # 음봉
            and last3.iloc[i]["close"] < last3.iloc[i-1]["close"] # 이전봉보다 낮은 종가
            for i in range(1, 3)
        ]
        first_bearish = last3.iloc[0]["close"] < last3.iloc[0]["open"]
        return first_bearish and all(conds)

    def _fibonacci_zone(self, df: pd.DataFrame, window: int = 30) -> float:
        """
        피보나치 되돌림 위치 반환 (0.0~1.0)
        0.382~0.650 구간 = 골든존 (매수 관심)
        """
        if len(df) < window:
            return 0.5
        recent = df.iloc[-window:]
        high   = float(recent["high"].max())
        low    = float(recent["low"].min())
        if high == low:
            return 0.5
        current = float(df.iloc[-1]["close"])
        # 되돌림 비율 (0 = 저점, 1 = 고점)
        return (current - low) / (high - low)

    def _is_bull_flag(self, df: pd.DataFrame) -> bool:
        """
        상승깃발 패턴: 급등 후 횡보 구간
        - 10일 전 대비 +10% 이상 급등 후
        - 최근 5일 변동성이 낮음
        """
        if len(df) < 15:
            return False
        surge  = (float(df.iloc[-10]["close"]) / float(df.iloc[-15]["close"]) - 1)
        recent_vol = float(df["close"].iloc[-5:].std() / df["close"].iloc[-5:].mean())
        return surge > 0.08 and recent_vol < 0.03

    # ─────────────────────────────────────────
    # 메인 신호 생성
    # ─────────────────────────────────────────
    def generate_signal(self, df: pd.DataFrame, symbol: str, market: Market) -> TradeSignal:
        if len(df) < 60:
            return self._make_signal(Signal.HOLD, symbol, market,
                                     self.get_current_price(df), "데이터 부족")

        # 지표 계산
        df = TechnicalIndicators.add_rsi(df.copy())
        df = TechnicalIndicators.add_bollinger_bands(df)
        df = TechnicalIndicators.add_macd(df)
        df = TechnicalIndicators.add_moving_averages(df)

        latest   = df.iloc[-1]
        prev     = df.iloc[-2]
        price    = float(latest["close"])
        rsi      = float(latest.get("rsi14", 50))
        bb_pct   = float(latest.get("bb_pct", 0.5))
        macd_h   = float(latest.get("macd_hist", 0))
        macd_h_p = float(prev.get("macd_hist", 0))
        ma20     = float(latest.get("ma20", price))
        ma60     = float(latest.get("ma60", price))
        vol_avg  = float(df["volume"].iloc[-20:].mean())
        vol_cur  = float(latest["volume"])

        # ─── 매도 조건 먼저 체크 ───────────────
        sell_reasons = []

        if self._is_double_top(df):
            sell_reasons.append("쌍봉패턴(100%)")

        if self._is_three_black_crows(df):
            sell_reasons.append("흑삼병(98%)")

        if self._is_shooting_star(latest):
            sell_reasons.append("슈팅스타(역핀버)")

        if ma20 < ma60 and prev.get("ma20", ma20) >= prev.get("ma60", ma60):
            sell_reasons.append("MA20 하향돌파")

        if rsi > self.rsi_overbought and macd_h < macd_h_p:
            sell_reasons.append(f"RSI과매수({rsi:.0f})+MACD하락")

        # 급락 캔들 (역V자): 당일 -4% 이상 하락
        daily_chg = (price - float(prev["close"])) / float(prev["close"])
        if daily_chg < -0.04:
            sell_reasons.append(f"급락캔들({daily_chg:.1%})")

        if sell_reasons:
            return self._make_signal(
                Signal.SELL, symbol, market, price,
                f"매도패턴: {', '.join(sell_reasons)} (RSI:{rsi:.1f})",
                confidence=0.85
            )

        # ─── 매수 점수 계산 ────────────────────
        buy_score = 0.0
        reasons   = []

        # 1. 상승추세 (MA20 > MA60) - 25점
        if ma20 > ma60:
            buy_score += 25
            reasons.append("상승추세")

        # 2. 쌍바닥 패턴 - 30점
        if self._is_double_bottom(df):
            buy_score += 30
            reasons.append("쌍바닥")

        # 3. 핀버(망치형) 캔들 - 25점
        if self._is_pin_bar(latest):
            buy_score += 25
            reasons.append("핀버(망치형)")

        # 4. 거래량 급증 (평균 1.5배 이상) - 20점
        if vol_avg > 0 and vol_cur >= vol_avg * 1.5:
            buy_score += 20
            reasons.append(f"거래량급증({vol_cur/vol_avg:.1f}x)")

        # 5. RSI 반등 구간 (35~55) - 15점
        if 35 <= rsi <= self.rsi_oversold:
            buy_score += 15
            reasons.append(f"RSI반등구간({rsi:.0f})")
        elif rsi < 35:
            buy_score += 10
            reasons.append(f"RSI과매도({rsi:.0f})")

        # 6. MACD 반등 (히스토그램 상승) - 15점
        if macd_h > macd_h_p:
            buy_score += 15
            reasons.append("MACD반등")

        # 7. 피보나치 골든존 (38~65%) - 20점
        fib = self._fibonacci_zone(df)
        if 0.35 <= fib <= 0.65:
            buy_score += 20
            reasons.append(f"피보나치골든존({fib:.0%})")
        elif 0.65 < fib <= 0.80:
            buy_score += 10

        # 8. 상승깃발 패턴 - 15점
        if self._is_bull_flag(df):
            buy_score += 15
            reasons.append("상승깃발")

        # 9. 볼린저 하단 근처 - 10점
        if bb_pct < 0.35:
            buy_score += 10
            reasons.append(f"BB하단({bb_pct:.2f})")

        confidence = min(buy_score / 100, 1.0)

        if buy_score >= self.buy_threshold:
            return self._make_signal(
                Signal.BUY, symbol, market, price,
                f"[{buy_score:.0f}점] {', '.join(reasons)}",
                confidence
            )

        return self._make_signal(
            Signal.HOLD, symbol, market, price,
            f"대기[{buy_score:.0f}점] RSI:{rsi:.0f} MA추세:{'↑' if ma20>ma60 else '↓'} 피보:{fib:.0%}"
        )


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from data.collector import DataCollector

    collector = DataCollector()
    strategy  = PatternStrategy()
    test_list = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]

    print("\n차트 패턴 전략 테스트")
    print("=" * 60)
    for symbol in test_list:
        df = collector.get_crypto_ohlcv(symbol, count=100)
        if df is not None and not df.empty:
            signal = strategy.generate_signal(df, symbol, Market.CRYPTO)
            icon   = "🟢" if signal.signal == Signal.BUY \
                     else "🔴" if signal.signal == Signal.SELL else "⚪"
            print(f"\n{icon} {symbol}")
            print(f"   신호: {signal.signal.value} | 신뢰도: {signal.confidence:.0%}")
            print(f"   이유: {signal.reason}")
