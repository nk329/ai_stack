"""
AI 자동매매 스케줄러 - 실시간 버전
핵심 원칙:
  - 신호 체크: 3분마다 (코인 24h / 주식 장중 / 미국주식 미장)
  - 전체 스캔: 1시간마다 (종목 재선발)
  - 빠른 체크는 이미 선발된 종목만 → 30초 이내 완료
"""
import sys
import time
import json
import logging
import schedule
from datetime import datetime
from pathlib import Path
import pytz
import yfinance as yf

sys.path.insert(0, ".")

from config.settings import INITIAL_CAPITAL, TIMEZONE
from data.collector import DataCollector
from data.screener import AIScreener
from strategy.rsi_bb import RSIBollingerStrategy
from strategy.pattern_strategy import PatternStrategy
from strategy.portfolio import DynamicPortfolio
from strategy.base import Signal, Market
from risk.manager import RiskManager
from db.database import TradingDB
from notification.telegram import TelegramNotifier
from api.upbit_api import UpbitAPI
from api.kis_api import KISAPI

logger = logging.getLogger(__name__)
KST = pytz.timezone(TIMEZONE)

# ─────────────────────────────────────────
# 실시간 환율 조회 (USD/KRW)
# ─────────────────────────────────────────
_usd_krw_cache = {"rate": 1450.0, "updated": None}

def get_usd_krw() -> float:
    """실시간 USD/KRW 환율 조회 (1시간 캐시)"""
    now = datetime.now()
    cached = _usd_krw_cache
    # 캐시가 없거나 1시간 이상 지났으면 갱신
    if cached["updated"] is None or (now - cached["updated"]).seconds > 3600:
        try:
            ticker = yf.Ticker("KRW=X")
            rate = ticker.fast_info["last_price"]
            if rate and 900 < rate < 2000:  # 정상 범위 체크
                cached["rate"] = float(rate)
                cached["updated"] = now
                logger.info(f"[환율] USD/KRW 갱신: {rate:,.1f}원")
        except Exception as e:
            logger.warning(f"[환율] 조회 실패, 이전 값 사용 ({cached['rate']:,.0f}원): {e}")
    return cached["rate"]

# ─────────────────────────────────────────
# 거래 수수료 상수
# ─────────────────────────────────────────
FEE_CRYPTO_BUY  = 0.0005   # 업비트 매수 수수료 0.05%
FEE_CRYPTO_SELL = 0.0005   # 업비트 매도 수수료 0.05%
FEE_KR_BUY      = 0.00015  # 국내주식 매수 0.015%
FEE_KR_SELL     = 0.00195  # 국내주식 매도 0.015% + 거래세 0.18%
FEE_US_BUY      = 0.001    # 미국주식 매수 0.1%
FEE_US_SELL     = 0.001    # 미국주식 매도 0.1%

# ─────────────────────────────────────────
# 미국 주요 종목 (스크리너 미구현 전 기본 후보)
# ─────────────────────────────────────────
US_CANDIDATES = [
    {"symbol": "AAPL",  "name": "애플"},
    {"symbol": "MSFT",  "name": "마이크로소프트"},
    {"symbol": "NVDA",  "name": "엔비디아"},
    {"symbol": "AMZN",  "name": "아마존"},
    {"symbol": "GOOGL", "name": "구글"},
    {"symbol": "META",  "name": "메타"},
    {"symbol": "TSLA",  "name": "테슬라"},
    {"symbol": "AMD",   "name": "AMD"},
    {"symbol": "SMCI",  "name": "슈퍼마이크로"},
    {"symbol": "PLTR",  "name": "팔란티어"},
]


def is_kr_market_open() -> bool:
    """국내 주식 장중 여부 (09:00~15:30 평일)"""
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return 900 <= t <= 1530


def is_us_market_open() -> bool:
    """미국 주식 장중 여부 (한국 기준 23:30~06:00 평일)"""
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    # KST 23:30 ~ 다음날 06:00 (서머타임 미적용 기준)
    return t >= 2330 or t <= 600


class AutoTrader:
    """AI 자동매매 - 실시간 모니터링 버전"""

    def __init__(self, dry_run: bool = True,
                 max_crypto: int = 10,  # 코인 최대 10개
                 max_kr: int = 5,
                 max_us: int = 3,
                 signal_interval: int = 3):   # 신호 체크 주기 (분)
        self.dry_run         = dry_run
        self.max_crypto      = max_crypto
        self.max_kr          = max_kr
        self.max_us          = max_us
        self.signal_interval = signal_interval

        self.collector = DataCollector()
        self.screener  = AIScreener()
        self.risk      = RiskManager(INITIAL_CAPITAL)
        self.db        = TradingDB()
        self.notifier  = TelegramNotifier()
        self.upbit     = UpbitAPI()
        self.kis       = KISAPI()

        # 포트폴리오 관리자
        # total_capital을 크게 잡고 포지션당 실제 투자금은 virtual_krw로 동적 결정
        self.per_position_krw = 5000   # 포지션당 투자금 (원) - 가용 잔고에서 이 금액씩 투자
        large_cap = INITIAL_CAPITAL * 10  # 할당 계산용 (실제 투자금은 per_position_krw 기준)
        self.crypto_portfolio = DynamicPortfolio(
            total_capital=large_cap, max_positions=max_crypto, min_score=25.0)
        self.kr_portfolio = DynamicPortfolio(
            total_capital=large_cap, max_positions=max_kr, min_score=25.0)
        self.us_portfolio = DynamicPortfolio(
            total_capital=large_cap, max_positions=max_us, min_score=25.0)

        # 현재 선발된 타겟 목록
        self.crypto_targets: list = []
        self.kr_targets: list     = []
        self.us_targets: list     = []

        # 쿨다운: 매도 후 동일 종목 재매수 방지 (symbol → 마지막 매도 시각)
        self._cooldown: dict = {}   # {"KRW-MON": datetime}
        self._cooldown_minutes = 20  # 매도 후 20분 쿨다운 (공격적 모드)

        # 트레일링 스탑: 보유 중 최고가 추적 {market: max_price}
        self._max_price: dict = {}
        self._trailing_pct   = 0.04   # 최고가 대비 -4% 손절

        # 시간 기반 탈출: 보유 X일 이상 & 수익 없으면 매도
        self._time_exit_days = 5      # 5일 이상 보유 & 수익률 0% 미만 → 매도

        # Dry Run 가상 잔고 (재시작 후에도 유지 - JSON 파일로 영속 저장)
        self._vkrw_file = Path("db/virtual_state.json")
        self.virtual_krw: float = self._load_virtual_krw()

        mode = "Dry Run (모의실행)" if dry_run else "실전 매매"
        logger.info("=" * 60)
        logger.info(f"  AI 자동매매 시작 [{mode}]")
        logger.info(f"  초기 자본금 : {INITIAL_CAPITAL:,.0f}원")
        logger.info(f"  신호 체크   : 매 {signal_interval}분")
        logger.info(f"  대상 시장   : 코인 전용 (24h 상시 매매)")
        logger.info("=" * 60)

    # ─────────────────────────────────────────
    # 가상 잔고 영속 저장 (Dry Run 재시작 대응)
    # ─────────────────────────────────────────
    def _load_virtual_krw(self) -> float:
        """JSON 파일에서 가상 잔고 로드, 없으면 초기 자본금 반환"""
        try:
            if self._vkrw_file.exists():
                data = json.loads(self._vkrw_file.read_text(encoding="utf-8"))
                krw = float(data.get("virtual_krw", INITIAL_CAPITAL))
                logger.info(f"[가상잔고] 이전 세션 복원: {krw:,.0f}원")
                return krw
        except Exception as e:
            logger.warning(f"[가상잔고] 파일 로드 실패, 초기값 사용: {e}")
        logger.info(f"[가상잔고] 초기 자본금으로 시작: {INITIAL_CAPITAL:,.0f}원")
        return float(INITIAL_CAPITAL)

    def _save_virtual_krw(self):
        """가상 잔고를 JSON 파일에 저장"""
        try:
            self._vkrw_file.parent.mkdir(exist_ok=True)
            data = {
                "virtual_krw": self.virtual_krw,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self._vkrw_file.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        except Exception as e:
            logger.warning(f"[가상잔고] 파일 저장 실패: {e}")

    # ─────────────────────────────────────────
    # 전체 시장 스캔 (1시간마다)
    # ─────────────────────────────────────────
    def run_market_scan(self):
        """전체 시장 스캔 - 종목 재선발"""
        now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        logger.info(f"[{now}] ════ AI 전체 시장 스캔 ════")

        # 코인 스캔 (항상)
        self._scan_crypto()

        # 주식 스캔 비활성화 (코인 전략 집중)
        # kr_time = datetime.now(KST)
        # if 8 <= kr_time.hour < 16:
        #     self._scan_kr_stocks()
        # elif kr_time.hour >= 21 or kr_time.hour < 7:
        #     self._scan_us_stocks()

        logger.info(f"[{now}] ════ 스캔 완료 ════")

    def _scan_crypto(self):
        logger.info("  [코인] 전체 KRW 코인 스캔...")
        scores = self.screener.scan_crypto(top_n=self.max_crypto + 3)
        self.crypto_portfolio.update_scores(scores)
        actions    = self.crypto_portfolio.get_rebalance_actions()
        allocation = self.crypto_portfolio.calculate_allocation(actions)
        self.crypto_portfolio.print_rebalance_plan(actions, allocation)

        self.crypto_targets = self._build_crypto_targets(actions, allocation, scores)
        logger.info(f"  [코인] 타겟 {len(self.crypto_targets)}개 확정")
        for t in self.crypto_targets:
            logger.info(f"    → {t['name']} | 점수:{t['score']:.1f} | 할당:{t['capital']:,.0f}원")

    def _scan_kr_stocks(self):
        logger.info("  [국내주식] KOSPI 스캔...")
        scores = self.screener.scan_kr_stocks(top_n=self.max_kr + 3)
        self.kr_portfolio.update_scores(scores)
        actions    = self.kr_portfolio.get_rebalance_actions()
        allocation = self.kr_portfolio.calculate_allocation(actions)
        self.kr_portfolio.print_rebalance_plan(actions, allocation)

        # 공격적 파라미터: 매수 조건 완화, 빠른 익절
        rsi_buy, rsi_sell = (60, 75) if self.dry_run else (35, 65)
        bb_buy,  bb_sell  = (0.60, 0.85) if self.dry_run else (0.20, 0.80)
        self.kr_targets = [
            {
                "code":  s.symbol,
                "name":  s.name,
                "strategy": RSIBollingerStrategy(
                    rsi_oversold=rsi_buy, rsi_overbought=rsi_sell,
                    bb_buy_pct=bb_buy, bb_sell_pct=bb_sell,
                ),
                "stop_loss":   0.05,   # 손절 5%
                "take_profit": 0.07,   # 익절 7% (빠른 익절)
                "score":   s.score,
                "capital": allocation.get(s.symbol, 0),
            }
            for s in (actions["add"] + [
                a for a in actions["hold"]
                if any(t.symbol == a.symbol for t in scores)
            ])
        ]
        logger.info(f"  [국내주식] 타겟 {len(self.kr_targets)}개 확정")

    def _scan_us_stocks(self):
        """미국주식 스캔 (yfinance 기반 상위 10개 중 선발)"""
        logger.info("  [미국주식] 나스닥 주요 종목 스캔...")
        scores = []
        for c in US_CANDIDATES:
            try:
                score = self._score_us_stock(c["symbol"], c["name"])
                if score:
                    scores.append(score)
                time.sleep(0.2)
            except Exception as e:
                logger.debug(f"  {c['symbol']} 스코어링 실패: {e}")

        scores.sort(key=lambda x: x.score, reverse=True)
        self.us_portfolio.update_scores(scores)
        actions    = self.us_portfolio.get_rebalance_actions()
        allocation = self.us_portfolio.calculate_allocation(actions)
        self.us_portfolio.print_rebalance_plan(actions, allocation)

        # 공격적 파라미터: 매수 조건 완화, 빠른 익절
        rsi_buy, rsi_sell = (60, 75) if self.dry_run else (35, 65)
        bb_buy,  bb_sell  = (0.60, 0.85) if self.dry_run else (0.20, 0.80)
        self.us_targets = [
            {
                "symbol":  s.symbol,
                "name":    s.name,
                "strategy": RSIBollingerStrategy(
                    rsi_oversold=rsi_buy, rsi_overbought=rsi_sell,
                    bb_buy_pct=bb_buy, bb_sell_pct=bb_sell,
                ),
                "stop_loss":   0.05,   # 손절 5%
                "take_profit": 0.07,   # 익절 7% (빠른 익절)
                "score":   s.score,
                "capital": allocation.get(s.symbol, 0),
            }
            for s in (actions["add"] + [
                a for a in actions["hold"]
                if any(t.symbol == a.symbol for t in scores)
            ])
        ]
        logger.info(f"  [미국주식] 타겟 {len(self.us_targets)}개 확정")

    def _score_us_stock(self, symbol: str, name: str):
        """미국주식 개별 점수 계산"""
        from data.screener import AssetScore
        from data.indicators import TechnicalIndicators

        df = self.collector.get_us_ohlcv(symbol, days=90)
        if df is None or len(df) < 20:
            return None

        df = TechnicalIndicators.add_rsi(df.copy())
        df = TechnicalIndicators.add_bollinger_bands(df)
        df = TechnicalIndicators.add_macd(df)
        df = TechnicalIndicators.add_moving_averages(df)
        required = [c for c in ["rsi14", "bb_pct", "macd_hist"] if c in df.columns]
        df = df.dropna(subset=required)
        if len(df) < 10:
            return None

        latest = df.iloc[-1]
        prices  = df["close"].values
        change_1d  = float(df["close"].pct_change().iloc[-1])
        change_7d  = (prices[-1] / prices[min(-7, -len(prices))] - 1)
        change_30d = (prices[-1] / prices[min(-30, -len(prices))] - 1)

        vol_ratio = (df["volume"].iloc[-1] / df["volume"].iloc[-20:].mean()
                     if df["volume"].iloc[-20:].mean() > 0 else 1)

        rsi    = float(latest.get("rsi14", 50))
        bb_pct = float(latest.get("bb_pct", 0.5))
        macd_h = float(latest.get("macd_hist", 0))
        macd_h_prev = float(df.iloc[-2].get("macd_hist", 0))

        # 점수 계산 (kr_stock 동일 체계)
        momentum_score = (30 if change_7d > 0.10 else
                          25 if change_7d > 0.05 else
                          20 if change_7d > 0.02 else
                          15 if change_7d > 0    else 5)

        technical_score = 0.0
        if 30 <= rsi <= 50:    technical_score += 15
        elif 50 < rsi <= 60:   technical_score += 10
        elif rsi < 30:         technical_score += 8
        elif rsi > 70:         technical_score += 2
        if bb_pct < 0.30:      technical_score += 10
        elif bb_pct < 0.50:    technical_score += 8
        elif bb_pct < 0.70:    technical_score += 5
        else:                   technical_score += 2
        if macd_h > macd_h_prev: technical_score += 5

        volume_score = (20 if vol_ratio >= 3 else 15 if vol_ratio >= 2
                        else 10 if vol_ratio >= 1.5 else 5)

        volatility = float(df["close"].pct_change().std())
        stability_score = (20 if volatility < 0.01 else 15 if volatility < 0.02
                           else 10 if volatility < 0.03 else 5 if volatility < 0.05 else 0)

        total = momentum_score + technical_score + volume_score + stability_score

        return AssetScore(
            symbol=symbol, name=name, market_type="US",
            current_price=float(latest["close"]),
            score=total,
            momentum_score=momentum_score, technical_score=technical_score,
            volume_score=volume_score, stability_score=stability_score,
            rsi=rsi, bb_pct=bb_pct,
            change_1d=change_1d, change_7d=change_7d, change_30d=change_30d,
            volume_ratio=vol_ratio,
            reason=f"RSI:{rsi:.0f} BB:{bb_pct:.2f} 7일:{change_7d:+.1%}",
        )

    def _build_crypto_targets(self, actions, allocation, scores) -> list:
        # 차트 패턴 전략 (이미지 분석 기반)
        # 매수: 쌍바닥+핀버+MA추세+거래량+피보나치 점수제 (55점 이상)
        # 매도: 쌍봉+흑삼병+급락캔들+MA하향 즉시 매도
        pattern_strat = PatternStrategy(buy_score_threshold=55.0)

        return [
            {
                "market":   s.symbol,
                "name":     s.name,
                "strategy": pattern_strat,
                "stop_loss":   0.05,   # 고정 손절 5% (트레일링 스탑과 병행)
                "take_profit": 0.08,   # 익절 8%
                "score":   s.score,
                "capital": allocation.get(s.symbol, 0),
            }
            for s in actions["add"] + [
                a for a in actions["hold"]
                if any(t.symbol == a.symbol for t in scores)
            ]
        ]

    # ─────────────────────────────────────────
    # 실시간 신호 체크 (3분마다 - 빠름)
    # ─────────────────────────────────────────
    def run_realtime_signals(self):
        """5분마다 실행 - 선발된 종목 신호만 체크"""
        now = datetime.now(KST).strftime("%H:%M")

        # 코인은 24시간 항상
        if self.crypto_targets:
            for cfg in self.crypto_targets:
                try:
                    self._process_crypto(cfg)
                except Exception as e:
                    logger.error(f"[{cfg['market']}] 오류: {e}")
        else:
            # 타겟 없으면 스캔 먼저
            logger.info(f"[{now}] 코인 타겟 없음 → 스캔 실행")
            self._scan_crypto()

        # 국내주식 / 미국주식 비활성화 (코인 전략 집중)
        # if is_kr_market_open(): ...
        # if is_us_market_open(): ...

    # ─────────────────────────────────────────
    # 개별 종목 매매 처리
    # ─────────────────────────────────────────
    def _process_crypto(self, cfg: dict):
        market   = cfg["market"]
        strategy = cfg["strategy"]
        sl, tp   = cfg["stop_loss"], cfg["take_profit"]

        df = self.collector.get_crypto_ohlcv(market, interval="day", count=100)
        if df is None or df.empty or len(df) < 30:
            return

        signal        = strategy.generate_signal(df, market, Market.CRYPTO)
        current_price = self.upbit.get_current_price(market)
        if not current_price:
            return

        position = self.db.get_position("CRYPTO", market)

        if signal.signal == Signal.BUY and not position:
            # 쿨다운 체크: 최근 매도 종목은 재매수 대기
            cooldown_until = self._cooldown.get(market)
            if cooldown_until and datetime.now(KST) < cooldown_until:
                remain = int((cooldown_until - datetime.now(KST)).total_seconds() / 60)
                logger.info(f"  [{market}] 쿨다운 중 ({remain}분 후 재매수 가능)")
                return

            # Dry Run: 가상 잔고 사용 / 실전: 실제 업비트 잔고
            if self.dry_run:
                krw = self.virtual_krw
            else:
                krw = self.upbit.get_krw_balance()
            # 포지션당 고정 투자금 사용 (가용 잔고 내에서)
            invest = min(self.per_position_krw, krw * 0.95)
            if invest < 5000:
                logger.info(f"  [{market}] 잔고 부족 (가용:{krw:,.0f}원, 필요:5,000원)")
                return

            stop_price = current_price * (1 - sl)
            take_price = current_price * (1 + tp)

            if self.dry_run:
                fee   = invest * FEE_CRYPTO_BUY          # 매수 수수료
                total = invest + fee                      # 실제 차감액 = 투자금 + 수수료
                self.virtual_krw -= total
                qty   = invest / current_price           # 수수료 제외한 실제 매수 수량
                logger.info(
                    f"  [DRY-BUY]  {market} | "
                    f"가격:{current_price:>14,.0f} | 금액:{invest:>8,.0f}원 | "
                    f"수수료:{fee:,.0f}원 | "
                    f"손절:{stop_price:>14,.0f} | 익절:{take_price:>14,.0f} | "
                    f"패턴점수:{cfg['score']:.0f}pt (가상잔고:{self.virtual_krw:,.0f}원)"
                )
                # Dry Run: DB에 포지션 + 거래 기록 모두 저장
                self.db.open_position("CRYPTO", market, current_price, qty,
                                      stop_price, take_price, "PatternStrategy")
                self.db.record_trade("CRYPTO", market, "DRY-BUY", current_price, qty,
                                     strategy="PatternStrategy",
                                     note=f"패턴:{cfg['score']:.0f}pt fee:{fee:,.0f} 가상잔고:{self.virtual_krw:,.0f}")
                self._max_price[market] = current_price  # 최고가 초기화
                self._save_virtual_krw()
            else:
                result = self.upbit.buy_market_order(market, invest)
                if result:
                    qty = invest / current_price
                    self.db.open_position("CRYPTO", market, current_price, qty,
                                          stop_price, take_price, strategy.name)
                    self.db.record_trade("CRYPTO", market, "BUY", current_price, qty,
                                         strategy=strategy.name,
                                         note=f"AI:{cfg['score']:.0f}pt")
                    logger.info(f"  [BUY]  {market} {invest:,.0f}원 @ {current_price:,.0f}")

        elif position:
            entry  = float(position["entry_price"])
            qty    = float(position["quantity"])
            stop_p = float(position.get("stop_loss") or entry * (1 - sl))
            take_p = float(position.get("take_profit") or entry * (1 + tp))
            ret    = (current_price - entry) / entry

            # 트레일링 스탑: 최고가 갱신 및 추적
            prev_max = self._max_price.get(market, entry)
            if current_price > prev_max:
                self._max_price[market] = current_price
                prev_max = current_price
            trailing_stop = prev_max * (1 - self._trailing_pct)

            # 시간 기반 탈출: 보유일 계산
            days_held = 0
            try:
                from datetime import timedelta as td
                entry_date = datetime.strptime(
                    position.get("entry_date", ""), "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=KST)
                days_held = (datetime.now(KST) - entry_date).days
            except Exception:
                pass

            sell_reason = ""
            if current_price <= trailing_stop and ret > -sl:
                sell_reason = f"트레일링스탑 ({ret:+.2%}, 최고가대비-{self._trailing_pct:.0%})"
            elif current_price <= stop_p:
                sell_reason = f"고정손절 ({ret:+.2%})"
            elif current_price >= take_p:
                sell_reason = f"익절 ({ret:+.2%})"
            elif signal.signal == Signal.SELL:
                sell_reason = f"패턴매도 ({ret:+.2%})"
            elif days_held >= self._time_exit_days and ret < 0:
                sell_reason = f"시간탈출 ({days_held}일보유, {ret:+.2%})"

            if sell_reason:
                gross_pnl = (current_price - entry) * qty  # 수수료 전 손익
                sell_fee  = current_price * qty * FEE_CRYPTO_SELL  # 매도 수수료
                pnl       = gross_pnl - sell_fee            # 수수료 반영 실현 손익
                if self.dry_run:
                    self.virtual_krw += entry * qty + gross_pnl - sell_fee  # 수수료 차감 후 복구
                    logger.info(
                        f"  [DRY-SELL] {market} | "
                        f"가격:{current_price:>14,.0f} | "
                        f"수수료:{sell_fee:,.0f}원 | 손익:{pnl:>+10,.0f}원 | {sell_reason} "
                        f"(가상잔고:{self.virtual_krw:,.0f}원)"
                    )
                    self.db.close_position("CRYPTO", market)
                    self.db.record_trade("CRYPTO", market, "DRY-SELL", current_price, qty,
                                         strategy="PatternStrategy",
                                         note=f"{sell_reason} fee:{sell_fee:,.0f} pnl:{pnl:+,.0f}")
                    self.risk.record_trade_result(pnl)
                    self._save_virtual_krw()
                    self._max_price.pop(market, None)  # 최고가 초기화
                    from datetime import timedelta
                    self._cooldown[market] = datetime.now(KST) + timedelta(minutes=self._cooldown_minutes)
                    logger.info(f"  [{market}] 쿨다운 시작 ({self._cooldown_minutes}분)")
                else:
                    coin = market.split("-")[1]
                    bal  = self.upbit.get_coin_balance(coin)
                    if bal > 0:
                        self.upbit.sell_market_order(market, bal)
                        self.db.close_position("CRYPTO", market)
                        self.db.record_trade("CRYPTO", market, "SELL",
                                              current_price, bal,
                                              strategy=strategy.name, note=sell_reason)
                        self.risk.record_trade_result(pnl)
                        logger.info(f"  [SELL] {market} {sell_reason} | 손익:{pnl:+,.0f}원")
                        from datetime import timedelta
                        self._cooldown[market] = datetime.now(KST) + timedelta(minutes=self._cooldown_minutes)
                        self._max_price.pop(market, None)
            else:
                logger.info(
                    f"  [HOLD] {market} | "
                    f"현재:{current_price:>14,.0f} | 수익률:{ret:>+7.2%} | "
                    f"트레일:{trailing_stop:>14,.0f} | {days_held}일보유"
                )

    def _process_kr_stock(self, cfg: dict):
        code, name = cfg["code"], cfg["name"]
        strategy   = cfg["strategy"]
        sl, tp     = cfg["stop_loss"], cfg["take_profit"]

        df = self.collector.get_kr_ohlcv(code, days=150)
        if df is None or df.empty or len(df) < 30:
            return

        signal     = strategy.generate_signal(df, code, Market.KR)
        price_info = self.kis.get_kr_stock_price(code)
        if not price_info:
            return

        current_price = price_info["price"]
        position = self.db.get_position("KR", code)

        if signal.signal == Signal.BUY and not position:
            invest = min(self.per_position_krw, self.virtual_krw * 0.95)
            qty    = int(invest / current_price)
            if qty < 1:
                logger.info(f"  [{code}] 1주 매수 불가 (주가:{current_price:,}원 > 예산:{invest:,.0f}원)")
                return
            stop_price = int(current_price * (1 - sl))
            take_price = int(current_price * (1 + tp))

            if self.dry_run:
                invest    = qty * current_price
                fee       = invest * FEE_KR_BUY           # 매수 수수료 0.015%
                total     = invest + fee
                self.virtual_krw -= total
                logger.info(
                    f"  [DRY-BUY]  {name}({code}) | "
                    f"가격:{current_price:>8,}원 | {qty}주 | "
                    f"수수료:{fee:,.0f}원 | "
                    f"손절:{stop_price:,} | 익절:{take_price:,} | "
                    f"AI:{cfg['score']:.0f}pt (가상잔고:{self.virtual_krw:,.0f}원)"
                )
                self.db.open_position("KR", code, current_price, qty,
                                      stop_price, take_price, "DryRun")
                self.db.record_trade("KR", code, "DRY-BUY", current_price, qty,
                                     strategy="DryRun",
                                     note=f"AI:{cfg['score']:.0f}pt fee:{fee:,.0f} {name}")
                self._save_virtual_krw()

        elif position:
            entry  = float(position["entry_price"])
            qty    = float(position["quantity"])
            stop_p = float(position.get("stop_loss") or entry * (1 - sl))
            take_p = float(position.get("take_profit") or entry * (1 + tp))
            ret    = (current_price - entry) / entry

            sell_reason = ""
            if current_price <= stop_p:
                sell_reason = f"손절 ({ret:+.2%})"
            elif current_price >= take_p:
                sell_reason = f"익절 ({ret:+.2%})"
            elif signal.signal == Signal.SELL:
                sell_reason = f"전략매도 ({ret:+.2%})"

            if sell_reason:
                gross_pnl = (current_price - entry) * qty
                sell_fee  = current_price * qty * FEE_KR_SELL  # 매도 수수료 + 거래세 0.195%
                pnl       = gross_pnl - sell_fee
                if self.dry_run:
                    self.virtual_krw += entry * qty + gross_pnl - sell_fee
                    logger.info(
                        f"  [DRY-SELL] {name}({code}) | "
                        f"가격:{current_price:>8,}원 | "
                        f"수수료:{sell_fee:,.0f}원 | 손익:{pnl:>+10,.0f}원 | {sell_reason} "
                        f"(가상잔고:{self.virtual_krw:,.0f}원)"
                    )
                    self.db.close_position("KR", code)
                    self.db.record_trade("KR", code, "DRY-SELL", current_price, qty,
                                         strategy="DryRun",
                                         note=f"{sell_reason} fee:{sell_fee:,.0f} pnl:{pnl:+,.0f} {name}")
                    self.risk.record_trade_result(pnl)
                    self._save_virtual_krw()
                else:
                    self.kis.sell_kr_stock(code, int(qty))
                    self.db.close_position("KR", code)
                    self.db.record_trade("KR", code, "SELL", current_price, qty,
                                          strategy=strategy.name, note=sell_reason)
                    self.risk.record_trade_result(pnl)
                    logger.info(f"  [SELL] {name} {sell_reason} | 손익:{pnl:+,.0f}원")
            else:
                logger.info(
                    f"  [HOLD] {name}({code}) | "
                    f"현재:{current_price:>8,}원 | 수익률:{ret:>+7.2%}"
                )

    def _process_us_stock(self, cfg: dict):
        symbol, name = cfg["symbol"], cfg["name"]
        strategy     = cfg["strategy"]
        sl, tp       = cfg["stop_loss"], cfg["take_profit"]

        df = self.collector.get_us_ohlcv(symbol, days=150)
        if df is None or df.empty or len(df) < 30:
            return

        signal = strategy.generate_signal(df, symbol, Market.KR)  # KR과 동일 로직

        # yfinance로 현재가 조회
        try:
            ticker = yf.Ticker(symbol)
            current_price = ticker.fast_info["last_price"]
        except Exception:
            return

        position = self.db.get_position("US", symbol)

        if signal.signal == Signal.BUY and not position:
            # per_position_krw 원화 예산을 실시간 환율로 달러 환산 후 수량 계산
            usd_krw    = get_usd_krw()
            budget_krw = min(self.per_position_krw, self.virtual_krw * 0.95)
            budget_usd = budget_krw / usd_krw              # 원화 예산 → 달러 환산
            qty        = int(budget_usd / current_price)   # 살 수 있는 주수
            if qty < 1:
                logger.info(
                    f"  [{symbol}] 예산 부족으로 매수 불가 "
                    f"(예산:{budget_krw:,.0f}원=${budget_usd:.1f}, 주가:${current_price:.2f})"
                )
                return
            stop_price = current_price * (1 - sl)
            take_price = current_price * (1 + tp)

            if self.dry_run:
                # 쿨다운 체크 (US주식도 적용)
                cooldown_until = self._cooldown.get(symbol)
                if cooldown_until and datetime.now(KST) < cooldown_until:
                    remain = int((cooldown_until - datetime.now(KST)).total_seconds() / 60)
                    logger.info(f"  [{symbol}] 쿨다운 중 ({remain}분 후 재매수 가능)")
                    return

                invest_usd = qty * current_price
                fee_usd    = invest_usd * FEE_US_BUY           # 매수 수수료 0.1%
                total_usd  = invest_usd + fee_usd
                total_krw  = total_usd * usd_krw               # 원화 환산 (수수료 포함)

                # 가용 잔고 확인
                if total_krw > self.virtual_krw:
                    logger.info(f"  [{symbol}] 잔고 부족 (필요:{total_krw:,.0f}원, 가용:{self.virtual_krw:,.0f}원)")
                    return

                self.virtual_krw -= total_krw
                logger.info(
                    f"  [DRY-BUY]  {name}({symbol}) | "
                    f"${current_price:>8,.2f} ({usd_krw:,.0f}원/달러) | {qty}주 | "
                    f"수수료:${fee_usd:.2f} | 원화:{total_krw:,.0f}원 | "
                    f"손절:${stop_price:,.2f} | 익절:${take_price:,.2f} | "
                    f"AI:{cfg['score']:.0f}pt (가상잔고:{self.virtual_krw:,.0f}원)"
                )
                try:
                    self.db.open_position("US", symbol, current_price, qty,
                                          stop_price, take_price, "DryRun")
                    self.db.record_trade("US", symbol, "DRY-BUY", current_price, qty,
                                         strategy="DryRun",
                                         note=f"AI:{cfg['score']:.0f}pt {name} rate:{usd_krw:.0f} krw:{total_krw:,.0f}")
                    self._save_virtual_krw()
                    # 쿨다운 등록 (매수 후에도 재매수 방지)
                    from datetime import timedelta
                    self._cooldown[symbol] = datetime.now(KST) + timedelta(minutes=self._cooldown_minutes)
                except Exception as e:
                    logger.error(f"  [{symbol}] DB 저장 오류: {e}")
            else:
                # KIS API로 미국주식 매수 (추후 구현)
                logger.info(f"  [BUY]  {name} {qty}주 @ ${current_price:,.2f}")

        elif position:
            entry  = float(position["entry_price"])
            qty    = float(position["quantity"])
            stop_p = float(position.get("stop_loss") or entry * (1 - sl))
            take_p = float(position.get("take_profit") or entry * (1 + tp))
            ret    = (current_price - entry) / entry

            sell_reason = ""
            if current_price <= stop_p:
                sell_reason = f"손절 ({ret:+.2%})"
            elif current_price >= take_p:
                sell_reason = f"익절 ({ret:+.2%})"
            elif signal.signal == Signal.SELL:
                sell_reason = f"전략매도 ({ret:+.2%})"

            if sell_reason:
                usd_krw   = get_usd_krw()
                gross_pnl_usd = (current_price - entry) * qty
                sell_fee_usd  = current_price * qty * FEE_US_SELL   # 매도 수수료 0.1%
                pnl_usd       = gross_pnl_usd - sell_fee_usd
                pnl_krw       = pnl_usd * usd_krw                   # 원화 환산 손익
                if self.dry_run:
                    # 매도 수익 원화로 잔고 복구
                    sell_amount_krw = (entry * qty + gross_pnl_usd - sell_fee_usd) * usd_krw
                    self.virtual_krw += sell_amount_krw
                    logger.info(
                        f"  [DRY-SELL] {name}({symbol}) | "
                        f"${current_price:>8,.2f} ({usd_krw:,.0f}원/달러) | "
                        f"수수료:${sell_fee_usd:.2f} | 손익:{pnl_krw:>+,.0f}원 | {sell_reason} "
                        f"(가상잔고:{self.virtual_krw:,.0f}원)"
                    )
                    self.db.close_position("US", symbol)
                    self.db.record_trade("US", symbol, "DRY-SELL", current_price, qty,
                                         strategy="DryRun",
                                         note=f"{sell_reason} rate:{usd_krw:.0f} fee:{sell_fee_usd:.2f} pnl:{pnl_krw:+,.0f}")
                    self.risk.record_trade_result(pnl_krw)
                    self._save_virtual_krw()
                    from datetime import timedelta
                    self._cooldown[symbol] = datetime.now(KST) + timedelta(minutes=self._cooldown_minutes)
            else:
                logger.info(
                    f"  [HOLD] {name}({symbol}) | "
                    f"${current_price:>8,.2f} | 수익률:{ret:>+7.2%}"
                )

    # ─────────────────────────────────────────
    # 일일 리포트
    # ─────────────────────────────────────────
    def send_daily_report(self):
        now = datetime.now(KST).strftime("%Y-%m-%d")
        logger.info(f"[{now}] ── 일일 리포트 ──")
        self.risk.print_status()
        self.db.print_summary()
        self.crypto_portfolio.print_portfolio()

    # ─────────────────────────────────────────
    # 스케줄 등록 및 실행
    # ─────────────────────────────────────────
    def start(self):
        """스케줄러 시작"""

        # ── 핵심: 3분마다 실시간 신호 체크 ──
        schedule.every(self.signal_interval).minutes.do(self.run_realtime_signals)

        # ── 전체 스캔: 1시간마다 ──
        schedule.every(60).minutes.do(self.run_market_scan)

        # ── 일일 리포트 ──
        schedule.every().day.at("08:00").do(self.send_daily_report)

        mode = "Dry Run" if self.dry_run else "실전 매매"
        logger.info(f"스케줄 등록 완료 [{mode}]")
        logger.info(f"  실시간 신호 체크 : 매 {self.signal_interval}분")
        logger.info(f"  전체 시장 스캔   : 매 60분")
        logger.info(f"  일일 리포트      : 08:00")
        logger.info(f"  Ctrl+C 로 중단")
        logger.info("─" * 60)

        # 시작 즉시 스캔 + 신호 체크
        logger.info("시작 즉시 전체 스캔 실행...")
        self.run_market_scan()
        self.run_realtime_signals()

        while True:
            try:
                schedule.run_pending()
                time.sleep(10)
            except KeyboardInterrupt:
                logger.info("사용자 중단 (Ctrl+C)")
                break
            except Exception as e:
                logger.error(f"루프 오류: {e}")
                time.sleep(30)


def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description="AI 자동매매 - 실시간 버전")
    parser.add_argument("--live",     action="store_true", help="실전 매매 모드")
    parser.add_argument("--interval", type=int, default=3, help="신호 체크 주기(분), 기본 3")
    parser.add_argument("--max-crypto", type=int, default=3)
    parser.add_argument("--max-kr",     type=int, default=3)
    parser.add_argument("--max-us",     type=int, default=3)
    args = parser.parse_args()

    if args.live:
        print("\n" + "=" * 60)
        print("  ⚠️  실전 매매 모드 - 실제 돈으로 거래됩니다!")
        print("=" * 60)
        if input("  계속하려면 'yes' 입력: ").strip().lower() != "yes":
            print("  취소됨")
            return

    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler("logs/trading.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ]
    )

    trader = AutoTrader(
        dry_run=not args.live,
        max_crypto=args.max_crypto,
        max_kr=args.max_kr,
        max_us=args.max_us,
        signal_interval=args.interval,
    )
    trader.start()


if __name__ == "__main__":
    main()
