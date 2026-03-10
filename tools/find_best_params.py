"""
실제 Dry Run 거래 종목 기반 최적 파라미터 탐색
- DB에서 거래된 종목 목록 추출
- 각 종목 최근 1년 데이터로 백테스트
- 수익 낼 수 있는 최적 RSI / BB / 손절 / 익절 도출
- scheduler.py 업데이트 제안 및 자동 적용
"""
import sys
import sqlite3
import json
import itertools
from pathlib import Path

sys.path.insert(0, ".")

import pandas as pd
import numpy as np

from data.collector import DataCollector
from data.indicators import TechnicalIndicators
from backtest.optimizer import fast_backtest, StrategyOptimizer, OptimResult

DB_FILE = Path("db/trading.db")

# ─────────────────────────────────────────────────────
# 파라미터 그리드 (넓게 탐색)
# ─────────────────────────────────────────────────────
PARAM_GRID = {
    "rsi_buy":  [25, 30, 35, 40, 45],
    "rsi_sell": [55, 60, 65, 70],
    "bb_buy":   [0.15, 0.20, 0.25, 0.30, 0.35],
    "bb_sell":  [0.60, 0.65, 0.70, 0.75, 0.80],
}
SL_GRID = [0.03, 0.05, 0.07, 0.10]
TP_GRID = [0.08, 0.10, 0.12, 0.15, 0.20]


def get_traded_symbols() -> list:
    """DB에서 실제 거래된 종목 목록 추출"""
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT DISTINCT market, symbol FROM trades ORDER BY symbol"
    ).fetchall()
    conn.close()
    return [(r[0], r[1]) for r in rows]


def optimize_symbol(collector: DataCollector, market: str, symbol: str) -> dict | None:
    """종목별 전체 파라미터 그리드 탐색"""

    # 데이터 수집
    if market == "CRYPTO":
        df = collector.get_crypto_ohlcv(symbol, interval="day", count=365)
    elif market == "US":
        df = collector.get_us_ohlcv(symbol, days=365)
    else:
        df = collector.get_kr_ohlcv(symbol, days=365)

    if df is None or len(df) < 60:
        print(f"    데이터 부족 ({len(df) if df is not None else 0}개) — 건너뜀")
        return None

    # 지표 계산 (한 번만)
    df = TechnicalIndicators.add_rsi(df.copy())
    df = TechnicalIndicators.add_bollinger_bands(df)
    df = TechnicalIndicators.add_macd(df)
    df = df.dropna().reset_index(drop=True)

    if len(df) < 60:
        print(f"    지표 계산 후 데이터 부족 — 건너뜀")
        return None

    # 학습 70% / 검증 30% 분리
    split = int(len(df) * 0.7)
    df_train = df.iloc[:split]
    df_test  = df.iloc[split:]

    print(f"    데이터: 전체 {len(df)}개 | 학습 {len(df_train)} | 검증 {len(df_test)}")

    # 그리드 탐색
    combos  = list(itertools.product(*PARAM_GRID.values()))
    sl_tp   = [(sl, tp) for sl in SL_GRID for tp in TP_GRID if tp / sl >= 1.5]
    keys    = list(PARAM_GRID.keys())

    train_results = []
    for combo in combos:
        rb, rs, bb, bs = combo
        if rb >= rs or bb >= bs:
            continue
        for sl, tp in sl_tp:
            r = fast_backtest(df_train, rb, rs, bb, bs, sl, tp)
            if r.trade_count >= 3:
                train_results.append(r)

    if not train_results:
        print(f"    유효한 파라미터 조합 없음")
        return None

    # 상위 15개 → 검증
    train_results.sort(key=lambda x: x.total_return, reverse=True)
    top15 = train_results[:15]

    test_results = []
    for tr in top15:
        p = tr.params
        te = fast_backtest(
            df_test,
            p["rsi_oversold"], p["rsi_overbought"],
            p["bb_buy_pct"], p["bb_sell_pct"],
            p["stop_loss"], p["take_profit"],
        )
        test_results.append((tr, te))

    # 검증 수익률 기준 정렬
    test_results.sort(key=lambda x: x[1].total_return, reverse=True)
    best_train, best_test = test_results[0]
    p = best_train.params

    # 현재 파라미터(RSI45, BB0.35, SL5%, TP12%)로도 테스트
    current = fast_backtest(df, 45, 60, 0.35, 0.65, 0.05, 0.12)
    # 최적 파라미터로 전체 기간 테스트
    optimal_full = fast_backtest(
        df,
        p["rsi_oversold"], p["rsi_overbought"],
        p["bb_buy_pct"], p["bb_sell_pct"],
        p["stop_loss"], p["take_profit"],
    )

    return {
        "symbol":  symbol,
        "market":  market,
        "best_params": p,
        "train":   best_train,
        "test":    best_test,
        "full_optimal": optimal_full,
        "current_params": current,
        "data_len": len(df),
    }


def print_result(res: dict):
    p  = res["best_params"]
    tr = res["train"]
    te = res["test"]
    fo = res["full_optimal"]
    cu = res["current_params"]

    print(f"\n  ── 최적 파라미터 ──")
    print(f"  RSI 매수: {p['rsi_oversold']}   RSI 매도: {p['rsi_overbought']}")
    print(f"  BB 매수: {p['bb_buy_pct']}   BB 매도: {p['bb_sell_pct']}")
    print(f"  손절: {p['stop_loss']:.0%}   익절: {p['take_profit']:.0%}")
    print(f"\n  ── 성과 비교 ──")
    print(f"  {'구분':<18} {'수익률':>8} {'승률':>7} {'거래수':>6} {'샤프':>7} {'MDD':>7}")
    print(f"  {'학습(최적)':18} {tr.total_return:>8.2%} {tr.win_rate:>7.1%} {tr.trade_count:>6} {tr.sharpe:>7.2f} {tr.max_drawdown:>7.2%}")
    print(f"  {'검증(최적)':18} {te.total_return:>8.2%} {te.win_rate:>7.1%} {te.trade_count:>6} {te.sharpe:>7.2f} {te.max_drawdown:>7.2%}")
    print(f"  {'전체기간(최적)':18} {fo.total_return:>8.2%} {fo.win_rate:>7.1%} {fo.trade_count:>6} {fo.sharpe:>7.2f} {fo.max_drawdown:>7.2%}")
    print(f"  {'전체기간(현재)':18} {cu.total_return:>8.2%} {cu.win_rate:>7.1%} {cu.trade_count:>6} {cu.sharpe:>7.2f} {cu.max_drawdown:>7.2%}")


def compute_consensus(results: list) -> dict:
    """여러 종목의 최적 파라미터 평균 → 공통 설정 도출"""
    if not results:
        return {}

    rsi_buys  = [r["best_params"]["rsi_oversold"]  for r in results]
    rsi_sells = [r["best_params"]["rsi_overbought"] for r in results]
    bb_buys   = [r["best_params"]["bb_buy_pct"]     for r in results]
    bb_sells  = [r["best_params"]["bb_sell_pct"]    for r in results]
    sls       = [r["best_params"]["stop_loss"]      for r in results]
    tps       = [r["best_params"]["take_profit"]    for r in results]

    # 평균 + 반올림
    def avg(lst, step):
        v = sum(lst) / len(lst)
        return round(round(v / step) * step, 4)

    consensus = {
        "rsi_oversold":  avg(rsi_buys,  5),
        "rsi_overbought": avg(rsi_sells, 5),
        "bb_buy_pct":    avg(bb_buys,   0.05),
        "bb_sell_pct":   avg(bb_sells,  0.05),
        "stop_loss":     avg(sls,       0.01),
        "take_profit":   avg(tps,       0.01),
    }

    # 유효성 보정
    if consensus["rsi_oversold"] >= consensus["rsi_overbought"]:
        consensus["rsi_overbought"] = consensus["rsi_oversold"] + 10
    if consensus["bb_buy_pct"] >= consensus["bb_sell_pct"]:
        consensus["bb_sell_pct"] = consensus["bb_buy_pct"] + 0.20

    return consensus


def apply_to_scheduler(consensus: dict):
    """scheduler.py의 Dry Run 파라미터를 최적값으로 업데이트"""
    sched_path = Path("scheduler.py")
    content = sched_path.read_text(encoding="utf-8")

    # _build_crypto_targets 의 dry_run 파라미터 교체
    old_block = (
        "        if self.dry_run:\n"
        "            rsi_buy, rsi_sell = 45, 60\n"
        "            bb_buy,  bb_sell  = 0.35, 0.65\n"
        "        else:\n"
        "            rsi_buy, rsi_sell = 35, 65\n"
        "            bb_buy,  bb_sell  = 0.20, 0.80"
    )
    new_block = (
        "        if self.dry_run:\n"
        f"            rsi_buy, rsi_sell = {int(consensus['rsi_oversold'])}, {int(consensus['rsi_overbought'])}\n"
        f"            bb_buy,  bb_sell  = {consensus['bb_buy_pct']:.2f}, {consensus['bb_sell_pct']:.2f}\n"
        "        else:\n"
        "            rsi_buy, rsi_sell = 35, 65\n"
        "            bb_buy,  bb_sell  = 0.20, 0.80"
    )

    if old_block not in content:
        print("  [경고] scheduler.py 자동 교체 실패 — 수동으로 확인하세요")
        return False

    content = content.replace(old_block, new_block)

    # 손절/익절 교체 (코인 타겟)
    old_sl = '                "stop_loss":   0.05,   # 5% 손절 (v2: 3%→5% 여유 확보)'
    new_sl = f'                "stop_loss":   {consensus["stop_loss"]:.2f},   # 백테스트 최적값'
    content = content.replace(old_sl, new_sl)

    old_tp_crypto = '                "take_profit": 0.15,'
    new_tp_crypto = f'                "take_profit": {consensus["take_profit"]:.2f},'
    content = content.replace(old_tp_crypto, new_tp_crypto, 1)  # 첫 번째만 (crypto)

    sched_path.write_text(content, encoding="utf-8")
    return True


def main():
    print("=" * 65)
    print("  Dry Run 거래 종목 최적 파라미터 탐색")
    print("=" * 65)

    symbols = get_traded_symbols()
    print(f"\n거래된 종목: {[s[1] for s in symbols]}")

    collector = DataCollector()
    all_results = []

    for market, symbol in symbols:
        # BTC는 구버전 버그 거래 제외, 데이터는 유효하므로 포함
        print(f"\n[{market}] {symbol} 최적화 중...")
        try:
            res = optimize_symbol(collector, market, symbol)
            if res:
                print_result(res)
                all_results.append(res)
        except Exception as e:
            print(f"    오류: {e}")

    if not all_results:
        print("\n최적화 결과 없음")
        return

    # ── 공통 파라미터 도출 ──
    print("\n" + "=" * 65)
    print("  종목별 최적 파라미터 요약")
    print("=" * 65)
    print(f"\n  {'종목':<14} {'RSI매수':>7} {'RSI매도':>7} {'BB매수':>7} {'BB매도':>7} {'손절':>6} {'익절':>6} {'전체수익':>9}")
    print(f"  {'-'*14} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*6} {'-'*6} {'-'*9}")
    for r in all_results:
        p  = r["best_params"]
        fo = r["full_optimal"]
        print(
            f"  {r['symbol']:<14} {p['rsi_oversold']:>7} {p['rsi_overbought']:>7}"
            f" {p['bb_buy_pct']:>7.2f} {p['bb_sell_pct']:>7.2f}"
            f" {p['stop_loss']:>6.0%} {p['take_profit']:>6.0%}"
            f" {fo.total_return:>9.2%}"
        )

    # 코인만 consensus (주식 제외)
    crypto_results = [r for r in all_results if r["market"] == "CRYPTO"]
    consensus = compute_consensus(crypto_results if crypto_results else all_results)

    print(f"\n{'='*65}")
    print(f"  코인 공통 최적 파라미터 (평균)")
    print(f"{'='*65}")
    print(f"  RSI 매수 기준 : {consensus['rsi_oversold']}")
    print(f"  RSI 매도 기준 : {consensus['rsi_overbought']}")
    print(f"  BB  매수 기준 : {consensus['bb_buy_pct']:.2f}")
    print(f"  BB  매도 기준 : {consensus['bb_sell_pct']:.2f}")
    print(f"  손절          : {consensus['stop_loss']:.0%}")
    print(f"  익절          : {consensus['take_profit']:.0%}")

    # scheduler.py 적용
    print(f"\nscheduler.py에 자동 적용 중...")
    ok = apply_to_scheduler(consensus)
    if ok:
        print("  ✓ 적용 완료 — 스케줄러를 재시작해야 적용됩니다")
    else:
        print("  ✗ 수동 적용 필요")

    # JSON으로 결과 저장
    out = {
        "consensus": consensus,
        "per_symbol": [
            {
                "symbol": r["symbol"],
                "market": r["market"],
                "best_params": r["best_params"],
                "full_return": r["full_optimal"].total_return,
                "full_win_rate": r["full_optimal"].win_rate,
                "current_return": r["current_params"].total_return,
            }
            for r in all_results
        ]
    }
    Path("db/best_params.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n결과 저장: db/best_params.json")


if __name__ == "__main__":
    main()
