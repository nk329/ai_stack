"""
AI 자동매매 프로그램 - 메인 실행 파일
국내주식 + 미국주식 + 암호화폐 통합 자동매매
"""
import sys
import logging
import os
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (
    INITIAL_CAPITAL, KIS_IS_PAPER_TRADING,
    STOP_LOSS_RATIO, TAKE_PROFIT_RATIO,
    MAX_DRAWDOWN_LIMIT, LOG_FILE, LOG_DIR
)


def setup_logging():
    """로깅 설정"""
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ]
    )


def print_banner():
    """시작 배너 출력"""
    mode = "모의투자" if KIS_IS_PAPER_TRADING else "🔴 실전투자"
    print("=" * 60)
    print("       AI 자동매매 프로그램 v0.1")
    print("       국내주식 + 미국주식 + 암호화폐")
    print("=" * 60)
    print(f"  KIS 모드       : {mode}")
    print(f"  초기 자본금    : {INITIAL_CAPITAL:>12,.0f} 원")
    print(f"  손절 기준      : -{STOP_LOSS_RATIO*100:.1f}%")
    print(f"  익절 기준      : +{TAKE_PROFIT_RATIO*100:.1f}%")
    print(f"  최대 낙폭 한도 : -{MAX_DRAWDOWN_LIMIT*100:.0f}%")
    print("=" * 60)
    print()
    print("사용법:")
    print("  python main.py backtest     # 백테스트 실행")
    print("  python main.py trade        # 자동매매 시작 (Dry Run)")
    print("  python main.py trade --live # 실전 매매 시작")
    print("  python main.py status       # 현재 상태 조회")
    print("=" * 60)


def run_backtest():
    """백테스트 실행"""
    from data.collector import DataCollector
    from strategy.rsi_bb import RSIBollingerStrategy
    from strategy.base import Market
    from backtest.engine import BacktestEngine
    from config.settings import CRYPTO_WATCHLIST, KR_WATCHLIST

    print("\n백테스트를 시작합니다...")
    collector = DataCollector()
    strategy = RSIBollingerStrategy()
    engine = BacktestEngine(initial_capital=INITIAL_CAPITAL, fee_rate=0.0005)

    results = []

    # 암호화폐 백테스트
    print("\n[암호화폐]")
    for market in CRYPTO_WATCHLIST:
        df = collector.get_crypto_ohlcv(market, count=200)
        if not df.empty:
            result = engine.run(df, strategy, market, Market.CRYPTO)
            result.print_report()
            results.append(result)

    # 국내주식 백테스트
    print("\n[국내주식]")
    for code in KR_WATCHLIST[:2]:  # 처음엔 2개만
        df = collector.get_kr_ohlcv(code, days=300)
        if not df.empty:
            result = engine.run(df, strategy, code, Market.KR,
                                stop_loss=0.03, take_profit=0.07)
            result.print_report()
            results.append(result)

    # 요약
    if results:
        print("\n" + "=" * 60)
        print("  백테스트 종합 요약")
        print("=" * 60)
        print(f"  {'종목':<15} {'수익률':>8} {'승률':>7} {'MDD':>7} {'등급'}")
        print("  " + "-" * 50)
        for r in results:
            print(f"  {r.symbol:<15} {r.total_return:>+7.2%} {r.win_rate:>6.1%} "
                  f"{r.max_drawdown:>6.2%} {r._get_grade()[:6]}")


def run_status():
    """현재 상태 조회"""
    from api.upbit_api import UpbitAPI
    from db.database import TradingDB
    from risk.manager import RiskManager

    print("\n현재 상태 조회 중...")

    upbit = UpbitAPI()
    db = TradingDB()

    # 업비트 전체 보유 자산 출력 (참고용)
    print("\n[업비트 보유 자산]")
    balances = upbit.get_balances()
    for b in balances:
        bal = float(b.get("balance", 0))
        if bal > 0:
            currency = b.get("currency")
            avg_price = float(b.get("avg_buy_price", 0))
            print(f"  {currency:<8}: {bal:.6f}  (평균매수가: {avg_price:,.2f})")

    # 프로그램 내부 자본금 추적 (DB 기반)
    # 실제 거래 손익 누적으로 관리 (업비트 잔고와 독립)
    perf = db.get_performance_summary()
    tracked_capital = INITIAL_CAPITAL + perf["total_pnl"]

    rm = RiskManager(INITIAL_CAPITAL)
    rm.current_capital = tracked_capital
    rm.peak_capital = max(INITIAL_CAPITAL, tracked_capital)
    rm.print_status()
    db.print_summary()


def run_trade(live: bool = False):
    """자동매매 실행"""
    from scheduler import AutoTrader
    trader = AutoTrader(dry_run=not live)
    trader.start()


def main():
    setup_logging()
    print_banner()

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", nargs="?", default="help",
                        choices=["backtest", "trade", "status", "help"])
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    if args.command == "backtest":
        run_backtest()
    elif args.command == "trade":
        run_trade(live=args.live)
    elif args.command == "status":
        run_status()
    else:
        pass  # 배너에서 이미 사용법 출력됨


if __name__ == "__main__":
    main()
