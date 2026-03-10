"""
동적 포트폴리오 관리자
AI 스크리너가 발굴한 종목을 자동으로 포트폴리오에 편입/편출

핵심 원칙:
  - 상위 N개 종목만 보유 (집중 투자 vs 분산 투자 균형)
  - 점수 하락 시 자동 교체 (손익과 무관하게 AI 판단 우선)
  - 단일 종목 최대 비중 제한 (리스크 분산)

참고: 게리 안토나치 (모멘텀 투자), 제임스 오쇼네시 (정량적 선택)
"""
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime

from data.screener import AssetScore

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """보유 포지션"""
    symbol: str
    name: str
    market_type: str     # 'CRYPTO' / 'KR'
    entry_price: float
    entry_score: float   # 편입 당시 AI 점수
    entry_time: datetime = field(default_factory=datetime.now)
    quantity: float = 0.0
    allocated_capital: float = 0.0
    current_score: float = 0.0  # 현재 AI 점수

    @property
    def weight(self) -> float:
        """포트폴리오 내 비중"""
        return self.allocated_capital

    def __str__(self):
        return (
            f"{self.name}({self.symbol}) | "
            f"편입점수:{self.entry_score:.1f} → 현재:{self.current_score:.1f} | "
            f"할당:{self.allocated_capital:,.0f}원"
        )


class DynamicPortfolio:
    """
    동적 포트폴리오 관리자
    스크리너 결과를 기반으로 포트폴리오를 자동 조정
    """

    def __init__(
        self,
        total_capital: float,
        max_positions: int = 5,
        max_single_weight: float = 0.30,  # 단일 종목 최대 30%
        rotation_threshold: float = 15.0,  # 점수 차이가 이 이상이면 교체
        min_score: float = 40.0,           # 최소 진입 점수
        stable_coins: list = None,         # 제외할 스테이블코인
    ):
        self.total_capital = total_capital
        self.max_positions = max_positions
        self.max_single_weight = max_single_weight
        self.rotation_threshold = rotation_threshold
        self.min_score = min_score
        self.stable_coins = stable_coins or ["USDT", "USDC", "BUSD", "DAI", "TUSD"]

        # 현재 포지션 목록 (symbol → Position)
        self.positions: Dict[str, Position] = {}

        # 다음 스캔 결과 (스크리너가 채움)
        self.latest_scores: List[AssetScore] = []

    def update_capital(self, capital: float):
        """현재 자본금 업데이트"""
        self.total_capital = capital

    def update_scores(self, scores: List[AssetScore]):
        """스크리너 결과로 최신 점수 업데이트"""
        # 스테이블코인 제거
        self.latest_scores = [
            s for s in scores
            if s.name not in self.stable_coins
            and s.symbol.replace("KRW-", "") not in self.stable_coins
        ]
        logger.info(f"포트폴리오 점수 업데이트: {len(self.latest_scores)}개 후보")

    # ─────────────────────────────────────────
    # 리밸런싱 로직
    # ─────────────────────────────────────────
    def get_rebalance_actions(self) -> dict:
        """
        현재 포트폴리오와 최신 스크리너 결과를 비교해
        편입/편출/유지 결정을 반환

        Returns:
            {
              "add":    [AssetScore],  # 새로 편입할 종목
              "remove": [Position],   # 편출할 종목
              "hold":   [Position],   # 유지할 종목
            }
        """
        if not self.latest_scores:
            return {"add": [], "remove": [], "hold": list(self.positions.values())}

        # 상위 N개 후보 (최소 점수 이상)
        candidates = [
            s for s in self.latest_scores
            if s.score >= self.min_score
        ][:self.max_positions]

        candidate_symbols = {s.symbol for s in candidates}
        current_symbols   = set(self.positions.keys())

        # 현재 보유 종목 점수 업데이트
        score_map = {s.symbol: s.score for s in self.latest_scores}
        for sym, pos in self.positions.items():
            pos.current_score = score_map.get(sym, 0.0)

        # 편출 대상: 후보에 없거나 점수가 크게 하락한 종목
        to_remove = []
        for sym, pos in self.positions.items():
            if sym not in candidate_symbols:
                # 후보에서 탈락
                to_remove.append(pos)
                logger.info(f"  편출 대상: {pos.name} (현재점수: {pos.current_score:.1f})")
            else:
                # 현재 보유 중인데 후보에 있는 새 종목이 훨씬 점수가 높으면 교체
                # (편입 당시 점수 - 현재 점수) 차이가 너무 크면 교체
                new_candidate_score = score_map.get(sym, 0.0)
                # 후보 중에 현재 보유 종목보다 점수가 훨씬 높은 게 있으면 교체 고려
                for c in candidates:
                    if c.symbol not in current_symbols:
                        if c.score - new_candidate_score >= self.rotation_threshold:
                            to_remove.append(pos)
                            logger.info(
                                f"  교체 대상: {pos.name} ({new_candidate_score:.1f}점) "
                                f"→ {c.name} ({c.score:.1f}점)으로 교체"
                            )
                            break

        remove_syms = {p.symbol for p in to_remove}

        # 편입 대상: 후보인데 현재 보유 안 한 것
        to_add = [
            c for c in candidates
            if c.symbol not in current_symbols or c.symbol in remove_syms
        ]

        # 유지 대상
        to_hold = [
            pos for sym, pos in self.positions.items()
            if sym not in remove_syms
        ]

        return {
            "add":    to_add[:max(0, self.max_positions - len(to_hold))],
            "remove": to_remove,
            "hold":   to_hold,
        }

    def calculate_allocation(self, actions: dict) -> Dict[str, float]:
        """
        각 종목별 할당 자본금 계산
        상위 종목에 더 높은 비중 배정 (점수 비례)
        """
        to_hold = actions["hold"]
        to_add  = actions["add"]
        all_active = to_hold + [
            Position(
                symbol=s.symbol, name=s.name,
                market_type=s.market_type,
                entry_price=s.current_price,
                entry_score=s.score,
                current_score=s.score,
            )
            for s in to_add
        ]

        if not all_active:
            return {}

        # 점수 기반 비중 계산
        total_score = sum(
            p.current_score if p.current_score > 0 else p.entry_score
            for p in all_active
        )
        if total_score == 0:
            weight_per = 1.0 / len(all_active)
            weights = {p.symbol: weight_per for p in all_active}
        else:
            weights = {}
            for p in all_active:
                score = p.current_score if p.current_score > 0 else p.entry_score
                weights[p.symbol] = score / total_score

        # 최대 비중 제한 적용 (초과분은 나머지에 분배)
        capped = {}
        overflow = 0.0
        for sym, w in weights.items():
            if w > self.max_single_weight:
                overflow += w - self.max_single_weight
                capped[sym] = self.max_single_weight
            else:
                capped[sym] = w

        # overflow 분배
        if overflow > 0:
            non_capped = [s for s, w in capped.items() if w < self.max_single_weight]
            if non_capped:
                add_per = overflow / len(non_capped)
                for sym in non_capped:
                    capped[sym] = min(self.max_single_weight, capped[sym] + add_per)

        # 실제 금액으로 변환
        allocation = {}
        available = self.total_capital * 0.90  # 10% 현금 보유
        for sym, w in capped.items():
            allocation[sym] = available * w

        return allocation

    def print_portfolio(self):
        """현재 포트폴리오 출력"""
        print("\n" + "─" * 55)
        print("  [동적 포트폴리오 현황]")
        print(f"  총 자본금: {self.total_capital:,.0f}원")
        print(f"  보유 종목: {len(self.positions)}개 / 최대 {self.max_positions}개")
        print("─" * 55)
        if not self.positions:
            print("  (보유 포지션 없음)")
        for i, (sym, pos) in enumerate(self.positions.items(), 1):
            score_arrow = "↑" if pos.current_score >= pos.entry_score else "↓"
            print(
                f"  {i}. {pos.name:<12} | "
                f"점수: {pos.entry_score:.0f}→{pos.current_score:.0f}{score_arrow} | "
                f"할당: {pos.allocated_capital:,.0f}원"
            )
        print("─" * 55)

    def print_rebalance_plan(self, actions: dict, allocation: dict):
        """리밸런싱 계획 출력"""
        print("\n" + "═" * 55)
        print("  [AI 포트폴리오 리밸런싱 계획]")
        print("═" * 55)

        if actions["remove"]:
            print(f"  ▼ 편출 ({len(actions['remove'])}개):")
            for pos in actions["remove"]:
                print(f"    - {pos.name} ({pos.symbol}) | 현재점수: {pos.current_score:.1f}")

        if actions["add"]:
            print(f"  ▲ 편입 ({len(actions['add'])}개):")
            for s in actions["add"]:
                capital = allocation.get(s.symbol, 0)
                print(f"    + {s.name} ({s.symbol}) | 점수: {s.score:.1f} | "
                      f"할당: {capital:,.0f}원")

        if actions["hold"]:
            print(f"  ● 유지 ({len(actions['hold'])}개):")
            for pos in actions["hold"]:
                capital = allocation.get(pos.symbol, pos.allocated_capital)
                print(f"    ○ {pos.name} ({pos.symbol}) | "
                      f"점수: {pos.current_score:.1f} | 할당: {capital:,.0f}원")

        print("═" * 55)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from data.screener import AIScreener

    screener = AIScreener()
    scores = screener.scan_crypto(top_n=10)

    portfolio = DynamicPortfolio(
        total_capital=100_000,
        max_positions=3,
    )
    portfolio.update_scores(scores)
    actions = portfolio.get_rebalance_actions()
    allocation = portfolio.calculate_allocation(actions)
    portfolio.print_rebalance_plan(actions, allocation)
