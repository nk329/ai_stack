"""
현재 보유 포지션의 실제 업비트 가격과 비교
- 진입가 vs 현재가 vs 손절가 vs 익절가
"""
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, ".")

import pyupbit
from api.kis_api import KISAPI

DB_FILE = Path("db/trading.db")

def get_current_price_crypto(market: str) -> float:
    try:
        price = pyupbit.get_current_price(market)
        return float(price) if price else None
    except Exception as e:
        return None

def main():
    if not DB_FILE.exists():
        print("DB 파일 없음")
        return

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    positions = conn.execute("SELECT * FROM positions ORDER BY market, entry_date").fetchall()
    conn.close()

    if not positions:
        print("보유 포지션 없음")
        return

    print("=" * 90)
    print(f"{'시장':<8} {'종목':<14} {'진입가':>12} {'현재가':>12} {'수익률':>8} {'손절가':>12} {'익절가':>12} {'상태'}")
    print("-" * 90)

    for p in positions:
        market   = p["market"]
        symbol   = p["symbol"]
        entry    = float(p["entry_price"])
        qty      = float(p["quantity"])
        stop_p   = float(p["stop_loss"]) if p["stop_loss"] else entry * 0.95
        take_p   = float(p["take_profit"]) if p["take_profit"] else entry * 1.07

        # 현재가 조회
        current = None
        if market == "CRYPTO":
            current = get_current_price_crypto(symbol)
        elif market == "US":
            try:
                import yfinance as yf
                current = yf.Ticker(symbol).fast_info["last_price"]
            except Exception:
                pass
        elif market == "KR":
            try:
                kis = KISAPI()
                info = kis.get_kr_stock_price(symbol)
                current = info["price"] if info else None
            except Exception:
                pass

        if current is None:
            print(f"{market:<8} {symbol:<14} {entry:>12,.4f} {'조회실패':>12} {'':>8} {stop_p:>12,.4f} {take_p:>12,.4f}")
            continue

        pct = (current - entry) / entry * 100

        # 상태 판단
        if current <= stop_p:
            status = "🔴 손절대기"
        elif current >= take_p:
            status = "🟢 익절대기"
        elif pct >= 0:
            status = "📈 수익중"
        else:
            status = "📉 손실중"

        print(
            f"{market:<8} {symbol:<14} "
            f"{entry:>12,.4f} {current:>12,.4f} "
            f"{pct:>+7.2f}% "
            f"{stop_p:>12,.4f} {take_p:>12,.4f} {status}"
        )

    print("=" * 90)
    print("\n※ 현재가는 업비트 실시간 기준")

if __name__ == "__main__":
    main()
