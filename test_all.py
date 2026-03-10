"""전체 모듈 통합 테스트"""
import sys
sys.path.insert(0, ".")

print("=== 1. 데이터 수집 테스트 ===")
from data.collector import DataCollector
collector = DataCollector()
df_btc = collector.get_crypto_ohlcv("KRW-BTC", count=100)
print(f"BTC 데이터: {len(df_btc)}개 수집")

print("\n=== 2. 기술적 지표 테스트 ===")
from data.indicators import TechnicalIndicators
ti = TechnicalIndicators()
sig = ti.get_latest_signal(df_btc)
print(f"BTC 현재가: {sig['close']:,.0f}원")
print(f"RSI(14)  : {sig['rsi']}")
print(f"BB%B     : {sig['bb_pct']}")
print(f"종합신호 : {sig['signal']} (점수: {sig['score']:+.0f})")

print("\n=== 3. 리스크 관리 테스트 ===")
from risk.manager import RiskManager
rm = RiskManager(100000)
rm.print_status()
can, reason = rm.can_trade()
print(f"거래 가능 여부: {can} ({reason})")

print("\n=== 4. DB 테스트 ===")
from db.database import TradingDB
db = TradingDB()
db.record_trade("CRYPTO", "KRW-BTC", "BUY", sig["close"], 0.001, strategy="RSI_BB")
db.open_position("CRYPTO", "KRW-BTC", sig["close"], 0.001,
                 stop_loss=sig["close"] * 0.97,
                 take_profit=sig["close"] * 1.07)
db.print_summary()

print("\n=== 5. 전략 테스트 (RSI + 볼린저밴드) ===")
from strategy.rsi_bb import RSIBollingerStrategy
from strategy.base import Market, Signal
strategy = RSIBollingerStrategy()

for symbol in ["KRW-BTC", "KRW-ETH", "KRW-XRP"]:
    df = collector.get_crypto_ohlcv(symbol, count=100)
    if not df.empty:
        result = strategy.generate_signal(df, symbol, Market.CRYPTO)
        icon = "BUY" if result.signal == Signal.BUY else "SELL" if result.signal == Signal.SELL else "HOLD"
        print(f"  [{icon}] {symbol} | 신뢰도: {result.confidence:.0%} | {result.reason[:60]}")

print("\n" + "="*50)
print("✅ 모든 모듈 테스트 완료!")
print("="*50)
