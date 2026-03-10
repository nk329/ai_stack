"""
리스크 관리 모듈
손절/익절 자동화, 포지션 사이징, MDD 모니터링
참고: 마켓 위저드, 켈리 공식, 반 타프의 'Trade Your Way to Financial Freedom'
"""
import logging
from datetime import datetime, date
from config.settings import (
    INITIAL_CAPITAL, MAX_POSITION_RATIO,
    STOP_LOSS_RATIO, TAKE_PROFIT_RATIO,
    DAILY_LOSS_LIMIT, MAX_DRAWDOWN_LIMIT
)

logger = logging.getLogger(__name__)


class RiskManager:
    """리스크 관리 클래스
    
    핵심 원칙:
    1. 단일 포지션 최대 손실: 자본의 1~2%
    2. 일일 최대 손실: 자본의 5%
    3. 최대 낙폭(MDD): 30% 초과 시 전체 거래 중단
    4. 켈리 공식으로 최적 포지션 크기 결정
    """

    def __init__(self, initial_capital: float = None):
        self.initial_capital = initial_capital or INITIAL_CAPITAL
        self.current_capital = self.initial_capital
        self.peak_capital = self.initial_capital  # 최고점 자본 (MDD 계산용)

        self.daily_loss = 0.0          # 오늘 손실액
        self.daily_loss_date = date.today()
        self.is_trading_halted = False  # 거래 중단 여부

        logger.info(f"리스크 관리 초기화: 자본금 {self.initial_capital:,.0f}원")

    # ─────────────────────────────────────────
    # 자본금 업데이트
    # ─────────────────────────────────────────
    def update_capital(self, new_capital: float):
        """자본금 업데이트 및 MDD 체크"""
        self.current_capital = new_capital

        # 최고점 갱신
        if new_capital > self.peak_capital:
            self.peak_capital = new_capital

        # MDD 체크
        mdd = self.get_current_mdd()
        if mdd >= MAX_DRAWDOWN_LIMIT:
            self.is_trading_halted = True
            logger.critical(
                f"⛔ 최대 낙폭 초과! MDD={mdd:.1%} → 모든 거래 중단"
            )

    def record_trade_result(self, profit_loss: float):
        """거래 결과 기록 (손익 반영)"""
        # 날짜가 바뀌면 일일 손실 초기화
        today = date.today()
        if today != self.daily_loss_date:
            self.daily_loss = 0.0
            self.daily_loss_date = today

        # 손실인 경우에만 일일 손실에 누적
        if profit_loss < 0:
            self.daily_loss += abs(profit_loss)

        # 일일 손실 한도 체크
        daily_limit = self.current_capital * DAILY_LOSS_LIMIT
        if self.daily_loss >= daily_limit:
            logger.warning(
                f"⚠️ 일일 손실 한도 도달: {self.daily_loss:,.0f}원 / 한도 {daily_limit:,.0f}원"
            )

        # 자본금 반영
        self.update_capital(self.current_capital + profit_loss)

    # ─────────────────────────────────────────
    # 매매 가능 여부 체크
    # ─────────────────────────────────────────
    def can_trade(self) -> tuple[bool, str]:
        """거래 가능 여부 확인
        Returns:
            (가능 여부, 불가 사유)
        """
        # 거래 중단 상태
        if self.is_trading_halted:
            return False, f"MDD 초과로 거래 중단 (현재 MDD: {self.get_current_mdd():.1%})"

        # 일일 손실 한도 초과
        daily_limit = self.current_capital * DAILY_LOSS_LIMIT
        if self.daily_loss >= daily_limit:
            return False, f"일일 손실 한도 초과: {self.daily_loss:,.0f}원"

        # 자본금 부족 (1,000원 이하)
        if self.current_capital < 1000:
            return False, f"자본금 부족: {self.current_capital:,.0f}원"

        return True, "거래 가능"

    # ─────────────────────────────────────────
    # 포지션 사이징 (얼마나 살까?)
    # ─────────────────────────────────────────
    def calc_position_size(self, current_price: float, stop_loss_price: float = None) -> float:
        """포지션 크기 계산 (투자할 금액)
        
        방법 1: 자본 비율 방식 (기본)
          → 자본의 MAX_POSITION_RATIO 이하
        
        방법 2: ATR 기반 방식 (stop_loss_price 제공 시)
          → 단일 거래 최대 손실 = 자본의 2%
          → 투자금액 = (자본 * 2%) / (진입가 - 손절가)
        """
        max_amount = self.current_capital * MAX_POSITION_RATIO

        if stop_loss_price and stop_loss_price < current_price:
            # ATR 기반 포지션 사이징
            risk_per_trade = self.current_capital * 0.02   # 자본의 2% 위험 허용
            risk_per_unit = current_price - stop_loss_price
            if risk_per_unit > 0:
                units = risk_per_trade / risk_per_unit
                amount = units * current_price
                # 최대 비중 초과 방지
                amount = min(amount, max_amount)
                logger.debug(f"ATR 기반 포지션: {amount:,.0f}원 (위험허용: {risk_per_trade:,.0f}원)")
                return amount

        # 기본 방식: 최대 비중의 50%로 보수적 진입
        amount = max_amount * 0.5
        logger.debug(f"기본 포지션: {amount:,.0f}원")
        return amount

    def calc_kelly_position(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """켈리 공식으로 최적 포지션 계산
        Kelly % = W - (1-W)/R
        W: 승률, R: 손익비(평균수익/평균손실)
        
        참고: 실전에서는 Half-Kelly(켈리의 절반)를 사용 권장
        """
        if avg_loss == 0:
            return 0
        r = avg_win / avg_loss  # 손익비
        kelly = win_rate - (1 - win_rate) / r
        half_kelly = kelly * 0.5  # Half-Kelly (안전 마진)

        # 최대 비중 제한
        position_ratio = max(0, min(half_kelly, MAX_POSITION_RATIO))
        amount = self.current_capital * position_ratio

        logger.info(f"켈리 포지션: 승률={win_rate:.1%}, 손익비={r:.2f}, Kelly={kelly:.1%}, Half-Kelly={half_kelly:.1%}")
        return amount

    # ─────────────────────────────────────────
    # 손절/익절 가격 계산
    # ─────────────────────────────────────────
    def calc_stop_loss(self, entry_price: float, atr: float = None) -> float:
        """손절가 계산
        - 기본: 진입가의 -STOP_LOSS_RATIO%
        - ATR 방식: 진입가 - ATR * 2 (변동성 반영)
        """
        if atr:
            stop = entry_price - (atr * 2)
            # ATR 손절이 너무 좁으면 기본 방식 사용
            basic_stop = entry_price * (1 - STOP_LOSS_RATIO)
            return min(stop, basic_stop)  # 더 넓은 손절 적용
        return entry_price * (1 - STOP_LOSS_RATIO)

    def calc_take_profit(self, entry_price: float, atr: float = None) -> float:
        """익절가 계산
        - 기본: 진입가의 +TAKE_PROFIT_RATIO%
        - ATR 방식: 진입가 + ATR * 3 (손익비 1.5 이상)
        """
        if atr:
            take = entry_price + (atr * 3)
            basic_take = entry_price * (1 + TAKE_PROFIT_RATIO)
            return max(take, basic_take)
        return entry_price * (1 + TAKE_PROFIT_RATIO)

    def should_stop_loss(self, entry_price: float, current_price: float, atr: float = None) -> bool:
        """손절 여부 판단"""
        stop_price = self.calc_stop_loss(entry_price, atr)
        return current_price <= stop_price

    def should_take_profit(self, entry_price: float, current_price: float, atr: float = None) -> bool:
        """익절 여부 판단"""
        take_price = self.calc_take_profit(entry_price, atr)
        return current_price >= take_price

    # ─────────────────────────────────────────
    # MDD 및 통계
    # ─────────────────────────────────────────
    def get_current_mdd(self) -> float:
        """현재 최대 낙폭(MDD) 계산"""
        if self.peak_capital == 0:
            return 0
        return (self.peak_capital - self.current_capital) / self.peak_capital

    def get_total_return(self) -> float:
        """총 수익률"""
        return (self.current_capital - self.initial_capital) / self.initial_capital

    def get_status(self) -> dict:
        """현재 리스크 상태 요약"""
        can, reason = self.can_trade()
        return {
            "initial_capital": self.initial_capital,
            "current_capital": self.current_capital,
            "total_return": self.get_total_return(),
            "current_mdd": self.get_current_mdd(),
            "daily_loss": self.daily_loss,
            "can_trade": can,
            "reason": reason,
            "is_halted": self.is_trading_halted,
        }

    def print_status(self):
        """리스크 상태 출력"""
        s = self.get_status()
        print("\n── 리스크 현황 ──────────────────────")
        print(f"  초기 자본금  : {s['initial_capital']:>12,.0f} 원")
        print(f"  현재 자본금  : {s['current_capital']:>12,.0f} 원")
        print(f"  총 수익률    : {s['total_return']:>+11.2%}")
        print(f"  현재 MDD     : {s['current_mdd']:>11.2%}  (한도: -{MAX_DRAWDOWN_LIMIT:.0%})")
        print(f"  오늘 손실    : {s['daily_loss']:>12,.0f} 원  (한도: {self.current_capital * DAILY_LOSS_LIMIT:,.0f}원)")
        status = "⛔ 거래 중단" if s['is_halted'] else ("✅ 거래 가능" if s['can_trade'] else "⚠️ 거래 불가")
        print(f"  거래 상태    : {status}")
        if not s['can_trade']:
            print(f"  사유         : {s['reason']}")
        print("─────────────────────────────────────")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")

    print("\n리스크 관리 모듈 테스트")
    rm = RiskManager(initial_capital=100000)
    rm.print_status()

    # 포지션 사이징 테스트
    price = 103_000_000  # BTC 현재가
    stop = 100_000_000   # 손절가
    amount = rm.calc_position_size(price, stop)
    print(f"\n  BTC 포지션 크기: {amount:,.0f}원")
    print(f"  손절가: {rm.calc_stop_loss(price):,.0f}원")
    print(f"  익절가: {rm.calc_take_profit(price):,.0f}원")

    # 켈리 공식 테스트
    kelly_amount = rm.calc_kelly_position(win_rate=0.55, avg_win=0.07, avg_loss=0.03)
    print(f"\n  켈리 공식 포지션: {kelly_amount:,.0f}원")
