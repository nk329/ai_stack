"""
기술적 지표 계산 모듈
RSI, MACD, 볼린저밴드, 이동평균선 등
유명 트레이더와 서적에서 검증된 지표들만 선별
"""
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class TechnicalIndicators:
    """기술적 지표 계산 클래스
    
    참고: 
    - RSI: J. Welles Wilder (New Concepts in Technical Trading Systems)
    - MACD: Gerald Appel
    - 볼린저밴드: John Bollinger (Bollinger on Bollinger Bands)
    - 이동평균: 마켓 위저드 시리즈
    """

    @staticmethod
    def add_all(df: pd.DataFrame) -> pd.DataFrame:
        """모든 지표를 한번에 추가"""
        df = TechnicalIndicators.add_moving_averages(df)
        df = TechnicalIndicators.add_rsi(df)
        df = TechnicalIndicators.add_macd(df)
        df = TechnicalIndicators.add_bollinger_bands(df)
        df = TechnicalIndicators.add_atr(df)
        df = TechnicalIndicators.add_volume_indicators(df)
        return df

    # ─────────────────────────────────────────
    # 이동평균선 (Moving Average)
    # ─────────────────────────────────────────
    @staticmethod
    def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
        """이동평균선 추가
        - MA5  : 단기 (1주)
        - MA20 : 중기 (1개월)
        - MA60 : 장기 (3개월)
        - MA120: 장기 (6개월)
        """
        df["ma5"] = df["close"].rolling(window=5).mean()
        df["ma20"] = df["close"].rolling(window=20).mean()
        df["ma60"] = df["close"].rolling(window=60).mean()
        df["ma120"] = df["close"].rolling(window=120).mean()

        # 골든크로스/데드크로스 신호
        # 골든크로스: MA5가 MA20을 상향 돌파 → 매수 신호
        df["golden_cross"] = (
            (df["ma5"] > df["ma20"]) &
            (df["ma5"].shift(1) <= df["ma20"].shift(1))
        )
        # 데드크로스: MA5가 MA20을 하향 돌파 → 매도 신호
        df["dead_cross"] = (
            (df["ma5"] < df["ma20"]) &
            (df["ma5"].shift(1) >= df["ma20"].shift(1))
        )
        return df

    # ─────────────────────────────────────────
    # RSI (Relative Strength Index)
    # ─────────────────────────────────────────
    @staticmethod
    def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """RSI 추가 (Wilder의 RSI)
        - RSI < 30: 과매도 → 매수 신호
        - RSI > 70: 과매수 → 매도 신호
        """
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        # Wilder's Smoothing (EMA 방식)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

        # avg_loss=0 (전구간 상승) 시 divide-by-zero 방지
        avg_loss_safe = avg_loss.replace(0, 1e-10)
        rs = avg_gain / avg_loss_safe
        df[f"rsi{period}"] = 100 - (100 / (1 + rs))

        # RSI 신호
        df["rsi_oversold"] = df[f"rsi{period}"] < 30    # 과매도 (매수 신호)
        df["rsi_overbought"] = df[f"rsi{period}"] > 70  # 과매수 (매도 신호)
        return df

    # ─────────────────────────────────────────
    # MACD (Moving Average Convergence Divergence)
    # ─────────────────────────────────────────
    @staticmethod
    def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        """MACD 추가 (Gerald Appel)
        - MACD가 시그널선 상향 돌파 → 매수 신호
        - MACD가 시그널선 하향 돌파 → 매도 신호
        """
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()

        df["macd"] = ema_fast - ema_slow
        df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]

        # MACD 크로스 신호
        df["macd_golden"] = (
            (df["macd"] > df["macd_signal"]) &
            (df["macd"].shift(1) <= df["macd_signal"].shift(1))
        )
        df["macd_dead"] = (
            (df["macd"] < df["macd_signal"]) &
            (df["macd"].shift(1) >= df["macd_signal"].shift(1))
        )
        return df

    # ─────────────────────────────────────────
    # 볼린저밴드 (Bollinger Bands)
    # ─────────────────────────────────────────
    @staticmethod
    def add_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
        """볼린저밴드 추가 (John Bollinger)
        - 하단밴드 근처: 매수 신호
        - 상단밴드 근처: 매도 신호
        - %B: 현재가의 밴드 내 위치 (0=하단, 1=상단)
        """
        df["bb_mid"] = df["close"].rolling(window=period).mean()
        std = df["close"].rolling(window=period).std()
        df["bb_upper"] = df["bb_mid"] + (std * std_dev)
        df["bb_lower"] = df["bb_mid"] - (std * std_dev)

        # %B 지표 (밴드 내 위치) - band_width=0 시 divide-by-zero 방지
        band_width = (df["bb_upper"] - df["bb_lower"]).replace(0, 1e-10)
        df["bb_pct"] = (df["close"] - df["bb_lower"]) / band_width

        # 밴드폭 (변동성 측정)
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

        # 신호
        df["bb_buy_signal"] = df["close"] <= df["bb_lower"]   # 하단 터치 → 매수
        df["bb_sell_signal"] = df["close"] >= df["bb_upper"]  # 상단 터치 → 매도
        return df

    # ─────────────────────────────────────────
    # ATR (Average True Range) - 변동성 측정
    # ─────────────────────────────────────────
    @staticmethod
    def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """ATR 추가 (손절폭 계산에 활용)
        - ATR이 클수록 변동성이 큼
        - 손절: 진입가 - (ATR * 2) 방식으로 활용
        """
        high_low = df["high"] - df["low"]
        high_close = abs(df["high"] - df["close"].shift(1))
        low_close = abs(df["low"] - df["close"].shift(1))

        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["atr"] = true_range.ewm(com=period - 1, min_periods=period).mean()
        return df

    # ─────────────────────────────────────────
    # 거래량 지표
    # ─────────────────────────────────────────
    @staticmethod
    def add_volume_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """거래량 관련 지표 추가"""
        # 거래량 이동평균
        df["vol_ma5"] = df["volume"].rolling(window=5).mean()
        df["vol_ma20"] = df["volume"].rolling(window=20).mean()

        # 거래량 급증 여부 (평균 대비 2배 이상)
        df["vol_surge"] = df["volume"] > df["vol_ma20"] * 2
        return df

    # ─────────────────────────────────────────
    # 종합 신호 생성
    # ─────────────────────────────────────────
    @staticmethod
    def get_signal_score(df: pd.DataFrame) -> pd.Series:
        """매수/매도 종합 점수 계산 (-3 ~ +3)
        양수: 매수 신호 강도
        음수: 매도 신호 강도
        """
        score = pd.Series(0, index=df.index, dtype=float)

        # RSI 신호 (+1/-1)
        if "rsi14" in df.columns:
            score += df["rsi_oversold"].astype(int)
            score -= df["rsi_overbought"].astype(int)

        # MACD 신호 (+1/-1)
        if "macd" in df.columns:
            score += df["macd_golden"].astype(int)
            score -= df["macd_dead"].astype(int)

        # 볼린저밴드 신호 (+1/-1)
        if "bb_lower" in df.columns:
            score += df["bb_buy_signal"].astype(int)
            score -= df["bb_sell_signal"].astype(int)

        return score

    @staticmethod
    def get_latest_signal(df: pd.DataFrame) -> dict:
        """최신 캔들의 신호 요약 반환"""
        if df.empty:
            return {}

        df = TechnicalIndicators.add_all(df.copy())
        latest = df.iloc[-1]

        rsi = latest.get("rsi14", 0)
        macd = latest.get("macd", 0)
        macd_signal = latest.get("macd_signal", 0)
        bb_pct = latest.get("bb_pct", 0.5)
        score = TechnicalIndicators.get_signal_score(df).iloc[-1]

        # 신호 판단
        if score >= 2:
            signal = "강한 매수"
        elif score == 1:
            signal = "약한 매수"
        elif score <= -2:
            signal = "강한 매도"
        elif score == -1:
            signal = "약한 매도"
        else:
            signal = "중립"

        return {
            "close": latest["close"],
            "rsi": round(rsi, 2),
            "macd": round(macd, 4),
            "macd_signal": round(macd_signal, 4),
            "bb_pct": round(bb_pct, 3),
            "score": score,
            "signal": signal,
        }


def test_indicators():
    """기술적 지표 테스트"""
    import sys
    sys.path.insert(0, ".")
    from data.collector import DataCollector

    print("\n" + "=" * 50)
    print("  기술적 지표 테스트")
    print("=" * 50)

    collector = DataCollector()
    ti = TechnicalIndicators()

    # 비트코인 데이터로 테스트
    print("\n[1] BTC 기술적 지표 분석...")
    df = collector.get_crypto_ohlcv("KRW-BTC", count=100)
    if not df.empty:
        signal = ti.get_latest_signal(df)
        print(f"    현재가   : {signal['close']:>15,.0f} 원")
        print(f"    RSI(14)  : {signal['rsi']:>8.2f}  {'⬆ 과매도(매수신호)' if signal['rsi'] < 30 else '⬇ 과매수(매도신호)' if signal['rsi'] > 70 else '➡ 중립'}")
        print(f"    MACD     : {signal['macd']:>10.2f}")
        print(f"    볼린저%B : {signal['bb_pct']:>8.3f}  {'(하단)' if signal['bb_pct'] < 0.2 else '(상단)' if signal['bb_pct'] > 0.8 else '(중간)'}")
        print(f"    종합신호 : {signal['signal']} (점수: {signal['score']:+.0f})")

    # 삼성전자 테스트
    print("\n[2] 삼성전자(005930) 기술적 지표...")
    df_kr = collector.get_kr_ohlcv("005930", days=150)
    if not df_kr.empty:
        signal_kr = ti.get_latest_signal(df_kr)
        print(f"    현재가   : {signal_kr['close']:>10,.0f} 원")
        print(f"    RSI(14)  : {signal_kr['rsi']:>8.2f}")
        print(f"    종합신호 : {signal_kr['signal']} (점수: {signal_kr['score']:+.0f})")

    print("\n✅ 지표 테스트 완료!")


if __name__ == "__main__":
    test_indicators()
