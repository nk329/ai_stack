"""
백테스팅 엔진
과거 데이터로 전략 성과를 검증
참고: 퀀트 투자 바이블, 켈리 공식, 샤프 지수
"""
import logging
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from data.indicators import TechnicalIndicators
from strategy.base import Signal, Market

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """백테스트 개별 거래 기록"""
    entry_date: str
    exit_date: str
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    profit_loss: float       # 손익 금액
    return_pct: float        # 수익률 (%)
    exit_reason: str         # 'TAKE_PROFIT', 'STOP_LOSS', 'SIGNAL'


@dataclass
class BacktestResult:
    """백테스트 결과"""
    symbol: str
    strategy_name: str
    initial_capital: float
    final_capital: float
    trades: List[BacktestTrade] = field(default_factory=list)

    @property
    def total_return(self) -> float:
        return (self.final_capital - self.initial_capital) / self.initial_capital

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def win_count(self) -> int:
        return sum(1 for t in self.trades if t.profit_loss > 0)

    @property
    def lose_count(self) -> int:
        return sum(1 for t in self.trades if t.profit_loss <= 0)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0
        return self.win_count / self.total_trades

    @property
    def avg_win(self) -> float:
        wins = [t.return_pct for t in self.trades if t.profit_loss > 0]
        return np.mean(wins) if wins else 0

    @property
    def avg_loss(self) -> float:
        losses = [abs(t.return_pct) for t in self.trades if t.profit_loss <= 0]
        return np.mean(losses) if losses else 0

    @property
    def profit_factor(self) -> float:
        """수익 팩터 = 총 수익 / 총 손실 (2.0 이상이면 우수)"""
        total_win = sum(t.profit_loss for t in self.trades if t.profit_loss > 0)
        total_loss = abs(sum(t.profit_loss for t in self.trades if t.profit_loss < 0))
        return total_win / total_loss if total_loss > 0 else float("inf")

    @property
    def max_drawdown(self) -> float:
        """최대 낙폭 (MDD)"""
        if not self.trades:
            return 0
        capitals = [self.initial_capital]
        cap = self.initial_capital
        for t in self.trades:
            cap += t.profit_loss
            capitals.append(cap)
        capital_series = pd.Series(capitals)
        rolling_max = capital_series.cummax()
        drawdowns = (capital_series - rolling_max) / rolling_max
        return abs(drawdowns.min())

    @property
    def sharpe_ratio(self) -> float:
        """샤프 지수 (1.0 이상이면 양호, 2.0 이상이면 우수)"""
        if not self.trades:
            return 0
        returns = [t.return_pct for t in self.trades]
        if np.std(returns) == 0:
            return 0
        return np.mean(returns) / np.std(returns) * np.sqrt(252)

    def print_report(self):
        """백테스트 결과 리포트 출력"""
        print("\n" + "=" * 60)
        print(f"  백테스트 결과: {self.symbol} | 전략: {self.strategy_name}")
        print("=" * 60)
        print(f"  초기 자본금   : {self.initial_capital:>15,.0f} 원")
        print(f"  최종 자본금   : {self.final_capital:>15,.0f} 원")
        print(f"  총 수익률     : {self.total_return:>+14.2%}")
        print(f"  총 거래 횟수  : {self.total_trades:>15}회")
        print(f"  승률          : {self.win_rate:>14.1%}  ({self.win_count}승 {self.lose_count}패)")
        print(f"  평균 수익률   : {self.avg_win:>+13.2%}")
        print(f"  평균 손실률   : {self.avg_loss:>+13.2%}")
        print(f"  손익비        : {(self.avg_win / self.avg_loss) if self.avg_loss > 0 else 0:>14.2f}")
        print(f"  수익 팩터     : {self.profit_factor:>14.2f}  (2.0 이상 우수)")
        print(f"  샤프 지수     : {self.sharpe_ratio:>14.2f}  (1.0 이상 양호)")
        print(f"  최대 낙폭     : {self.max_drawdown:>14.2%}  (30% 이하 권장)")

        # 평가
        print("\n  ── 전략 평가 ───────────────────────")
        grade = self._get_grade()
        print(f"  종합 등급     : {grade}")
        print("=" * 60)

        # 최근 거래 5건
        if self.trades:
            print("\n  [최근 거래 5건]")
            print(f"  {'진입일':12} {'청산일':12} {'진입가':>12} {'청산가':>12} {'수익률':>8} {'사유':10}")
            print("  " + "-" * 70)
            for t in self.trades[-5:]:
                print(
                    f"  {t.entry_date[:10]:12} {t.exit_date[:10]:12} "
                    f"{t.entry_price:>12,.0f} {t.exit_price:>12,.0f} "
                    f"{t.return_pct:>+7.2%} {t.exit_reason:10}"
                )

    def _get_grade(self) -> str:
        score = 0
        if self.total_return > 0.1:   score += 2
        elif self.total_return > 0:   score += 1
        if self.win_rate > 0.55:      score += 2
        elif self.win_rate > 0.45:    score += 1
        if self.profit_factor > 2.0:  score += 2
        elif self.profit_factor > 1.5: score += 1
        if self.sharpe_ratio > 1.0:   score += 2
        elif self.sharpe_ratio > 0.5: score += 1
        if self.max_drawdown < 0.15:  score += 2
        elif self.max_drawdown < 0.30: score += 1

        if score >= 8:   return "S (매우 우수) ★★★★★"
        elif score >= 6: return "A (우수)      ★★★★"
        elif score >= 4: return "B (양호)      ★★★"
        elif score >= 2: return "C (보통)      ★★"
        else:            return "D (미흡)      ★"


class BacktestEngine:
    """백테스팅 엔진"""

    def __init__(self, initial_capital: float = 100000, fee_rate: float = 0.0005):
        """
        Args:
            initial_capital: 초기 자본금
            fee_rate: 수수료율 (업비트 기준 0.05%)
        """
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate

    def run(self, df: pd.DataFrame, strategy, symbol: str,
            market: Market, stop_loss: float = 0.03,
            take_profit: float = 0.07) -> BacktestResult:
        """백테스트 실행
        Args:
            df: OHLCV 데이터
            strategy: 전략 객체 (BaseStrategy 상속)
            symbol: 종목코드
            market: 시장 구분
            stop_loss: 손절 비율
            take_profit: 익절 비율
        """
        # 지표 사전 계산
        df = TechnicalIndicators.add_all(df.copy())
        df = df.dropna().reset_index(drop=False)

        capital = self.initial_capital
        position = None          # 현재 보유 포지션
        trades: List[BacktestTrade] = []

        # 최소 30개 데이터 이후부터 신호 생성
        warmup = 30

        for i in range(warmup, len(df)):
            row = df.iloc[i]
            current_price = float(row["close"])
            current_date = str(row.get("index", row.name))

            # ── 포지션 보유 중: 손절/익절 체크 ──
            if position is not None:
                entry_price = position["entry_price"]
                qty = position["quantity"]

                # 손절 체크
                if current_price <= entry_price * (1 - stop_loss):
                    pnl = (current_price - entry_price) * qty * (1 - self.fee_rate)
                    capital += entry_price * qty + pnl
                    trades.append(BacktestTrade(
                        entry_date=position["entry_date"],
                        exit_date=str(current_date),
                        symbol=symbol,
                        entry_price=entry_price,
                        exit_price=current_price,
                        quantity=qty,
                        profit_loss=pnl,
                        return_pct=(current_price - entry_price) / entry_price,
                        exit_reason="STOP_LOSS"
                    ))
                    position = None
                    continue

                # 익절 체크
                if current_price >= entry_price * (1 + take_profit):
                    pnl = (current_price - entry_price) * qty * (1 - self.fee_rate)
                    capital += entry_price * qty + pnl
                    trades.append(BacktestTrade(
                        entry_date=position["entry_date"],
                        exit_date=str(current_date),
                        symbol=symbol,
                        entry_price=entry_price,
                        exit_price=current_price,
                        quantity=qty,
                        profit_loss=pnl,
                        return_pct=(current_price - entry_price) / entry_price,
                        exit_reason="TAKE_PROFIT"
                    ))
                    position = None
                    continue

            # ── 신호 생성 (최근 i개 데이터 사용) ──
            window_df = df.iloc[max(0, i - 100):i + 1][["open", "high", "low", "close", "volume"]]
            signal = strategy.generate_signal(window_df, symbol, market)

            # ── 매수 신호: 포지션 없을 때 진입 ──
            if signal.signal == Signal.BUY and position is None:
                invest_amount = capital * 0.5        # 자본의 50% 투입
                fee = invest_amount * self.fee_rate
                qty = (invest_amount - fee) / current_price
                capital -= invest_amount
                position = {
                    "entry_price": current_price,
                    "quantity": qty,
                    "entry_date": str(current_date),
                }

            # ── 매도 신호: 포지션 있을 때 청산 ──
            elif signal.signal == Signal.SELL and position is not None:
                entry_price = position["entry_price"]
                qty = position["quantity"]
                pnl = (current_price - entry_price) * qty * (1 - self.fee_rate)
                capital += entry_price * qty + pnl
                trades.append(BacktestTrade(
                    entry_date=position["entry_date"],
                    exit_date=str(current_date),
                    symbol=symbol,
                    entry_price=entry_price,
                    exit_price=current_price,
                    quantity=qty,
                    profit_loss=pnl,
                    return_pct=(current_price - entry_price) / entry_price,
                    exit_reason="SIGNAL"
                ))
                position = None

        # 백테스트 종료 시 포지션 강제 청산
        if position is not None:
            last_price = float(df.iloc[-1]["close"])
            entry_price = position["entry_price"]
            qty = position["quantity"]
            pnl = (last_price - entry_price) * qty * (1 - self.fee_rate)
            capital += entry_price * qty + pnl
            trades.append(BacktestTrade(
                entry_date=position["entry_date"],
                exit_date=str(df.iloc[-1].get("index", df.iloc[-1].name)),
                symbol=symbol,
                entry_price=entry_price,
                exit_price=last_price,
                quantity=qty,
                profit_loss=pnl,
                return_pct=(last_price - entry_price) / entry_price,
                exit_reason="END"
            ))

        return BacktestResult(
            symbol=symbol,
            strategy_name=strategy.name,
            initial_capital=self.initial_capital,
            final_capital=capital,
            trades=trades,
        )


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from data.collector import DataCollector
    from strategy.rsi_bb import RSIBollingerStrategy

    print("백테스트 엔진 실행 중...")
    collector = DataCollector()
    strategy = RSIBollingerStrategy()
    engine = BacktestEngine(initial_capital=100000, fee_rate=0.0005)

    for symbol in ["KRW-BTC", "KRW-ETH", "KRW-XRP"]:
        print(f"\n{symbol} 백테스트 (최근 200일)...")
        df = collector.get_crypto_ohlcv(symbol, count=200)
        if not df.empty:
            result = engine.run(df, strategy, symbol, Market.CRYPTO)
            result.print_report()
