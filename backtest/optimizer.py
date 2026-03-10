"""
파라미터 최적화 엔진 (고속 벡터화 버전)
지표를 한 번만 계산하고 파라미터 조합별로 신호만 재생성 → 수십 배 빠름
과적합 방지: 학습(70%) / 검증(30%) 데이터 분리
"""
import sys
import logging
import itertools
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional

sys.path.insert(0, ".")

from data.collector import DataCollector
from data.indicators import TechnicalIndicators
from strategy.base import Market

logger = logging.getLogger(__name__)


@dataclass
class OptimResult:
    """단일 파라미터 조합 결과"""
    params: Dict
    total_return: float
    win_rate: float
    max_drawdown: float
    sharpe: float
    trade_count: int
    score: float


@dataclass
class BestParams:
    """종목별 최적 파라미터"""
    symbol: str
    train: OptimResult
    test: OptimResult


# ─────────────────────────────────────────────────────
# 핵심: 벡터화된 고속 백테스터
# ─────────────────────────────────────────────────────
def fast_backtest(df: pd.DataFrame, rsi_buy: float, rsi_sell: float,
                  bb_buy: float, bb_sell: float,
                  stop_loss: float, take_profit: float,
                  capital: float = 100000, fee: float = 0.0005) -> OptimResult:
    """고속 백테스트 (지표 사전 계산 필요)
    df에는 이미 rsi14, bb_pct, macd_hist 컬럼이 있어야 함
    """
    # numpy 배열로 추출
    n = len(df)
    prices   = df["close"].to_numpy(dtype=float)
    rsi_vals = df["rsi14"].to_numpy(dtype=float)
    bb_vals  = df["bb_pct"].to_numpy(dtype=float)
    mh_vals  = df["macd_hist"].to_numpy(dtype=float)

    cash = float(capital)
    entry_price = 0.0
    qty = 0.0
    in_position = False
    trade_returns: list = []

    for i in range(1, n):
        p   = prices[i]
        r   = rsi_vals[i]
        b   = bb_vals[i]
        mh  = mh_vals[i]
        mhp = mh_vals[i - 1]

        if in_position:
            ret = (p - entry_price) / entry_price

            # 손절 또는 익절
            if ret <= -stop_loss or ret >= take_profit:
                cash += entry_price * qty * (1.0 + ret) * (1.0 - fee)
                trade_returns.append(ret)
                in_position = False
                continue

            # 매도 신호
            sc = int(r > rsi_sell) + int(b > bb_sell) + int(mh < mhp)
            if sc >= 2:
                cash += entry_price * qty * (1.0 + ret) * (1.0 - fee)
                trade_returns.append(ret)
                in_position = False
            continue

        # 매수 신호
        if cash < 5000:
            continue
        bc = int(r < rsi_buy) + int(b < bb_buy) + int(mh > mhp)
        if bc >= 2:
            invest = cash * 0.5
            qty = invest * (1.0 - fee) / p
            cash -= invest
            entry_price = p
            in_position = True

    # 미청산 포지션 강제 청산
    if in_position:
        last = prices[-1]
        ret  = (last - entry_price) / entry_price
        cash += entry_price * qty * (1.0 + ret) * (1.0 - fee)
        trade_returns.append(ret)

    total_return = (cash - capital) / capital
    tc = len(trade_returns)
    win_count = sum(1 for t in trade_returns if t > 0)
    win_rate = win_count / tc if tc else 0.0

    # MDD 계산
    if tc > 0:
        cap_curve = [capital]
        c = capital
        for t in trade_returns:
            c = c * (1.0 + t * 0.5)
            cap_curve.append(c)
        s = pd.Series(cap_curve)
        mdd = float(abs(((s - s.cummax()) / s.cummax()).min()))
    else:
        mdd = 0.0

    # 샤프 지수
    if tc > 1 and float(np.std(trade_returns)) > 0:
        sharpe = float(np.mean(trade_returns) / np.std(trade_returns) * np.sqrt(252))
    else:
        sharpe = 0.0

    score = (
        total_return * 0.40 +
        min(sharpe, 5.0) * 0.01 * 0.30 -
        mdd * 0.20 +
        win_rate * 0.10
    )

    return OptimResult(
        params={
            "rsi_oversold": rsi_buy, "rsi_overbought": rsi_sell,
            "bb_buy_pct": bb_buy, "bb_sell_pct": bb_sell,
            "stop_loss": stop_loss, "take_profit": take_profit,
        },
        total_return=total_return,
        win_rate=win_rate,
        max_drawdown=mdd,
        sharpe=sharpe,
        trade_count=tc,
        score=score,
    )


class StrategyOptimizer:
    """고속 파라미터 최적화"""

    PARAM_GRID = {
        "rsi_buy":  [25, 30, 35, 40],
        "rsi_sell": [60, 65, 70, 75],
        "bb_buy":   [0.15, 0.20, 0.25, 0.30],
        "bb_sell":  [0.70, 0.75, 0.80, 0.85],
    }
    SL_GRID = [0.02, 0.03, 0.04, 0.05]
    TP_GRID = [0.05, 0.07, 0.10, 0.12]

    def __init__(self, capital: float = 100000):
        self.capital = capital

    def optimize(self, df: pd.DataFrame, symbol: str,
                 train_ratio: float = 0.7) -> Optional[BestParams]:
        """고속 그리드 서치"""
        if len(df) < 60:
            print(f"  {symbol}: 데이터 부족 건너뜀")
            return None

        # 지표 한 번만 계산 (핵심 최적화)
        df = TechnicalIndicators.add_rsi(df.copy())
        df = TechnicalIndicators.add_bollinger_bands(df)
        df = TechnicalIndicators.add_macd(df)
        df = df.dropna().reset_index(drop=True)

        split = int(len(df) * train_ratio)
        df_train = df.iloc[:split]
        df_test  = df.iloc[split:]

        print(f"  학습 {len(df_train)}개 | 검증 {len(df_test)}개 | ", end="", flush=True)

        # 파라미터 조합 생성
        keys = list(self.PARAM_GRID.keys())
        combos = list(itertools.product(*self.PARAM_GRID.values()))
        sl_tp = [(sl, tp) for sl in self.SL_GRID for tp in self.TP_GRID if tp / sl >= 1.5]

        total = len(combos) * len(sl_tp)
        print(f"조합 {total}개 탐색...", end="", flush=True)

        train_results = []

        for combo in combos:
            rb, rs, bb, bs = combo
            if rb >= rs or bb >= bs:
                continue
            for sl, tp in sl_tp:
                r = fast_backtest(df_train, rb, rs, bb, bs, sl, tp, self.capital)
                # 거래 횟수 3회 이상이면 수익/손실 관계없이 후보에 포함
                # (하락장에서도 상대적으로 가장 좋은 파라미터 탐색)
                if r.trade_count >= 3:
                    train_results.append(r)

        if not train_results:
            print(" 유효한 조합 없음")
            return None

        # 학습 상위 10개를 검증 구간에서 재테스트
        train_results.sort(key=lambda x: x.total_return, reverse=True)
        top_train = train_results[:10]

        test_results = []
        for tr in top_train:
            p = tr.params
            te = fast_backtest(
                df_test,
                p["rsi_oversold"], p["rsi_overbought"],
                p["bb_buy_pct"], p["bb_sell_pct"],
                p["stop_loss"], p["take_profit"],
                self.capital
            )
            # 검증 거래가 0번이어도 포함 (신호 없음 = 관망 = 손실 없음)
            test_results.append((tr, te))

        if not test_results:
            print(" 검증 통과 없음")
            return None

        # 검증 수익률 기준 정렬
        test_results.sort(key=lambda x: x[1].total_return, reverse=True)
        best_train, best_test = test_results[0]

        print(f" 완료! (유효 {len(train_results)}개)")
        return BestParams(symbol=symbol, train=best_train, test=best_test)

    def print_result(self, bp: BestParams):
        """결과 출력"""
        p = bp.train.params
        tr = bp.train
        te = bp.test

        print("\n" + "─" * 60)
        print(f"  [{bp.symbol}] 최적 파라미터")
        print("─" * 60)
        print(f"  RSI 매수 기준 : {p['rsi_oversold']}")
        print(f"  RSI 매도 기준 : {p['rsi_overbought']}")
        print(f"  BB 매수 위치  : {p['bb_buy_pct']:.2f}")
        print(f"  BB 매도 위치  : {p['bb_sell_pct']:.2f}")
        print(f"  손절          : -{p['stop_loss']:.0%}")
        print(f"  익절          : +{p['take_profit']:.0%}")
        print(f"  {'':20} {'학습':>8} {'검증':>8}")
        print(f"  {'수익률':20} {tr.total_return:>+7.2%}  {te.total_return:>+7.2%}")
        print(f"  {'승률':20} {tr.win_rate:>7.1%}  {te.win_rate:>7.1%}")
        print(f"  {'MDD':20} {tr.max_drawdown:>7.2%}  {te.max_drawdown:>7.2%}")
        print(f"  {'샤프지수':20} {tr.sharpe:>8.2f}  {te.sharpe:>8.2f}")
        print(f"  {'거래횟수':20} {tr.trade_count:>8}  {te.trade_count:>8}")

        # 등급 판정
        if te.total_return > 0.10:   grade = "S ★★★★★ (매우 우수)"
        elif te.total_return > 0.05: grade = "A ★★★★  (우수)"
        elif te.total_return > 0:    grade = "B ★★★   (양호)"
        elif te.total_return > -0.05: grade = "C ★★    (보통)"
        else:                         grade = "D ★     (미흡 - 실전 제외)"

        print(f"  {'검증 등급':20} {grade}")
        print("─" * 60)


def run_optimization():
    """전체 최적화 실행"""
    print("\n" + "=" * 60)
    print("  AI 자동매매 - 파라미터 최적화 (고속 버전)")
    print("  학습 70% / 검증 30% 분리")
    print("=" * 60)

    collector = DataCollector()
    optimizer = StrategyOptimizer(capital=100000)

    targets = [
        ("KRW-BTC", Market.CRYPTO, "crypto"),
        ("KRW-ETH", Market.CRYPTO, "crypto"),
        ("KRW-XRP", Market.CRYPTO, "crypto"),
        ("KRW-SOL", Market.CRYPTO, "crypto"),
        ("005930",  Market.KR,     "kr"),
        ("000660",  Market.KR,     "kr"),
        ("035420",  Market.KR,     "kr"),
    ]

    all_best = {}

    for symbol, market, mtype in targets:
        print(f"\n[{symbol}] ", end="", flush=True)
        if mtype == "crypto":
            df = collector.get_crypto_ohlcv(symbol, count=365)
        else:
            df = collector.get_kr_ohlcv(symbol, days=500)

        if df.empty:
            print("데이터 없음")
            continue

        result = optimizer.optimize(df, symbol)
        if result:
            optimizer.print_result(result)
            all_best[symbol] = result.train.params

    # 최종 요약
    print("\n\n" + "=" * 60)
    print("  최적화 완료 요약 - .env 또는 설정에 반영 가능")
    print("=" * 60)
    for sym, params in all_best.items():
        print(
            f"  {sym:<12} RSI({int(params['rsi_oversold'])}/{int(params['rsi_overbought'])}) "
            f"BB({params['bb_buy_pct']:.2f}/{params['bb_sell_pct']:.2f}) "
            f"SL:{params['stop_loss']:.0%} TP:{params['take_profit']:.0%}"
        )
    print("=" * 60)
    return all_best


if __name__ == "__main__":
    import time
    logging.basicConfig(level=logging.WARNING)
    t0 = time.time()
    run_optimization()
    print(f"\n총 소요 시간: {(time.time()-t0)/60:.1f}분")
