"""
AI 스크리너 - 전체 코인/주식 자동 스캔 및 점수화
매일 전체 유니버스를 스캔해서 투자 가치 있는 종목을 자동 발굴

점수 체계 (총 100점):
  모멘텀    30점 - 최근 수익률, 추세
  기술지표  30점 - RSI, MACD, 볼린저밴드
  거래량    20점 - 거래량 급증, 관심도
  안정성    20점 - 변동성, MDD

참고: 마켓 위저드, 모멘텀 투자의 기술 (Gary Antonacci)
"""
import time
import logging
import requests
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

import pyupbit
from data.collector import DataCollector
from data.indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


@dataclass
class AssetScore:
    """종목 점수 데이터"""
    symbol: str                    # 종목코드 / 마켓코드
    name: str                      # 종목명
    market_type: str               # 'CRYPTO' / 'KR' / 'US'
    current_price: float           # 현재가
    score: float                   # 종합 점수 (0~100)
    momentum_score: float          # 모멘텀 점수
    technical_score: float         # 기술지표 점수
    volume_score: float            # 거래량 점수
    stability_score: float         # 안정성 점수
    rsi: float                     # RSI
    bb_pct: float                  # 볼린저밴드 위치
    change_1d: float               # 1일 수익률
    change_7d: float               # 7일 수익률
    change_30d: float              # 30일 수익률
    volume_ratio: float            # 거래량 비율 (현재/평균)
    reason: str = ""               # 선발 이유

    def __str__(self):
        return (
            f"{self.symbol:<12} {self.name:<12} "
            f"점수:{self.score:>5.1f} | "
            f"RSI:{self.rsi:>5.1f} | "
            f"1일:{self.change_1d:>+6.2%} | "
            f"7일:{self.change_7d:>+6.2%} | "
            f"거래량:{self.volume_ratio:>4.1f}x"
        )


class AIScreener:
    """AI 스크리너 - 전체 유니버스 자동 스캔"""

    def __init__(self):
        self.collector = DataCollector()

    # ─────────────────────────────────────────
    # 암호화폐 스크리닝
    # ─────────────────────────────────────────
    def scan_crypto(self, top_n: int = 5,
                    min_volume_krw: float = 5_000_000_000) -> List[AssetScore]:
        """업비트 KRW 전체 코인 스캔
        Args:
            top_n: 상위 N개 반환
            min_volume_krw: 최소 거래대금 (기본 50억원 - 유동성 필터)
        """
        logger.info("암호화폐 전체 스캔 시작...")

        # 전체 KRW 마켓 목록 조회
        try:
            res = requests.get(
                "https://api.upbit.com/v1/market/all?isDetails=false",
                timeout=10
            )
            all_markets = [
                m["market"] for m in res.json()
                if m["market"].startswith("KRW-")
            ]
        except Exception as e:
            logger.error(f"마켓 목록 조회 실패: {e}")
            return []

        logger.info(f"  총 {len(all_markets)}개 KRW 코인 발견")

        # 1차 필터: 24시간 거래대금 기준
        try:
            # 업비트 티커 한번에 조회 (최대 100개씩)
            chunk_size = 100
            tickers_all = []
            for i in range(0, len(all_markets), chunk_size):
                chunk = ",".join(all_markets[i:i+chunk_size])
                r = requests.get(
                    f"https://api.upbit.com/v1/ticker?markets={chunk}",
                    timeout=10
                )
                tickers_all.extend(r.json())
                time.sleep(0.1)

            # 거래대금 필터
            filtered = [
                t for t in tickers_all
                if float(t.get("acc_trade_price_24h", 0)) >= min_volume_krw
            ]
            logger.info(f"  거래대금 {min_volume_krw/1e8:.0f}억원 이상: {len(filtered)}개")
        except Exception as e:
            logger.error(f"티커 조회 실패: {e}")
            return []

        # 2차: 각 종목 기술적 분석
        scores = []
        for ticker in filtered:
            market = ticker["market"]
            try:
                score = self._score_crypto(ticker)
                if score:
                    scores.append(score)
                time.sleep(0.05)  # API 제한 방지
            except Exception as e:
                logger.warning(f"  {market} 스코어링 실패: {e}")

        # 점수 내림차순 정렬 후 상위 N개 반환
        scores.sort(key=lambda x: x.score, reverse=True)
        top = scores[:top_n]

        logger.info(f"  스크리닝 완료: {len(scores)}개 → 상위 {len(top)}개 선발")
        return top

    def _score_crypto(self, ticker: dict) -> Optional[AssetScore]:
        """개별 코인 점수 계산"""
        market = ticker["market"]

        # 1시간봉 200개 = 약 8일치 (더 빠른 패턴 포착)
        df = self.collector.get_crypto_ohlcv(market, interval="minute60", count=200)
        if df is None or len(df) < 20:
            return None

        # 지표 계산 (ma120은 60일 데이터로 계산 불가 - subset으로 필수 컬럼만 dropna)
        df = TechnicalIndicators.add_rsi(df.copy())
        df = TechnicalIndicators.add_bollinger_bands(df)
        df = TechnicalIndicators.add_macd(df)
        df = TechnicalIndicators.add_moving_averages(df)
        required_cols = [c for c in ["rsi14", "bb_pct", "macd_hist", "ma5", "ma20"]
                         if c in df.columns]
        df = df.dropna(subset=required_cols)
        if len(df) < 10:
            return None

        latest = df.iloc[-1]
        price   = float(ticker.get("trade_price", 0))
        if price <= 0:
            return None

        # ── 수익률 계산 ──
        change_1d  = float(ticker.get("signed_change_rate", 0))
        prices     = df["close"].values
        change_7d  = (prices[-1] / prices[min(-7, -len(prices))] - 1) if len(prices) >= 7 else 0
        change_30d = (prices[-1] / prices[min(-30, -len(prices))] - 1) if len(prices) >= 30 else 0

        # ── 거래량 비율 ──
        vol_now = df["volume"].iloc[-1]
        vol_avg = df["volume"].iloc[-20:].mean()
        vol_ratio = vol_now / vol_avg if vol_avg > 0 else 1

        # ── RSI / BB ──
        rsi    = float(latest.get("rsi14", 50))
        bb_pct = float(latest.get("bb_pct", 0.5))
        macd_h = float(latest.get("macd_hist", 0))
        macd_h_prev = float(df.iloc[-2].get("macd_hist", 0))

        # ── 점수 계산 ──

        # 1. 모멘텀 점수 (30점)
        # 7일 수익률 기반, 너무 과열된 건 감점
        momentum_score = 0.0
        if change_7d > 0.20:   momentum_score = 25  # 20% 이상 급등 → 과열 주의
        elif change_7d > 0.10: momentum_score = 30  # 10~20% → 최적
        elif change_7d > 0.05: momentum_score = 25
        elif change_7d > 0.00: momentum_score = 15
        elif change_7d > -0.05: momentum_score = 10
        else:                   momentum_score = 0

        # 30일 추세 보정
        if change_30d > 0.10:  momentum_score = min(30, momentum_score + 5)
        elif change_30d < -0.20: momentum_score = max(0, momentum_score - 10)

        # 2. 기술지표 점수 (30점)
        technical_score = 0.0

        # RSI: 30~50 구간이 매수 적기 (과매도 회복 초기)
        if 30 <= rsi <= 50:    technical_score += 15
        elif 50 < rsi <= 60:   technical_score += 10
        elif rsi < 30:         technical_score += 8   # 과매도 (반등 기대)
        elif rsi > 70:         technical_score += 2   # 과매수 (위험)

        # 볼린저밴드: 하단~중간 구간 선호
        if bb_pct < 0.30:      technical_score += 10  # 하단 → 반등 기대
        elif bb_pct < 0.50:    technical_score += 8
        elif bb_pct < 0.70:    technical_score += 5
        else:                   technical_score += 2   # 상단 → 위험

        # MACD 방향
        if macd_h > macd_h_prev:  technical_score += 5  # 상승 반전

        # 3. 거래량 점수 (20점)
        volume_score = 0.0
        if vol_ratio >= 3.0:   volume_score = 20  # 평균 3배 이상 급증
        elif vol_ratio >= 2.0: volume_score = 15
        elif vol_ratio >= 1.5: volume_score = 10
        elif vol_ratio >= 1.0: volume_score = 5
        else:                   volume_score = 0

        # 4. 안정성 점수 (20점) - 변동성이 낮을수록 좋음
        volatility = float(df["close"].pct_change().std())
        if volatility < 0.02:   stability_score = 20
        elif volatility < 0.03: stability_score = 15
        elif volatility < 0.05: stability_score = 10
        elif volatility < 0.08: stability_score = 5
        else:                    stability_score = 0  # 너무 변동성 큼

        total_score = (
            momentum_score +
            technical_score +
            volume_score +
            stability_score
        )

        # 이유 생성
        reasons = []
        if change_7d > 0.05:    reasons.append(f"7일+{change_7d:.1%}")
        if vol_ratio >= 2.0:    reasons.append(f"거래량{vol_ratio:.1f}x")
        if rsi < 40:            reasons.append(f"RSI과매도({rsi:.0f})")
        if bb_pct < 0.30:       reasons.append("BB하단")
        if macd_h > macd_h_prev: reasons.append("MACD반등")

        coin_name = market.replace("KRW-", "")

        return AssetScore(
            symbol=market,
            name=coin_name,
            market_type="CRYPTO",
            current_price=price,
            score=total_score,
            momentum_score=momentum_score,
            technical_score=technical_score,
            volume_score=volume_score,
            stability_score=stability_score,
            rsi=rsi,
            bb_pct=bb_pct,
            change_1d=change_1d,
            change_7d=change_7d,
            change_30d=change_30d,
            volume_ratio=vol_ratio,
            reason=", ".join(reasons) if reasons else "기술적 중립",
        )

    # ─────────────────────────────────────────
    # 국내주식 스크리닝
    # ─────────────────────────────────────────
    def scan_kr_stocks(self, top_n: int = 5,
                       market: str = "KOSPI") -> List[AssetScore]:
        """국내주식 전체 스캔
        Args:
            top_n: 상위 N개 반환
            market: 'KOSPI' 또는 'KOSDAQ'
        """
        logger.info(f"국내주식({market}) 전체 스캔 시작...")

        try:
            from pykrx import stock as pykrx_stock
            from datetime import timedelta

            # 최근 영업일 탐색 (최대 5일 전까지 시도)
            query_date = None
            tickers = []
            for days_back in range(0, 6):
                candidate = datetime.now() - timedelta(days=days_back)
                if candidate.weekday() >= 5:  # 주말 건너뜀
                    continue
                d = candidate.strftime("%Y%m%d")
                try:
                    t = pykrx_stock.get_market_ticker_list(d, market=market)
                    if t and len(t) > 0:
                        query_date = d
                        tickers = t
                        break
                except Exception:
                    continue

            if not query_date or not tickers:
                logger.warning("  pykrx 종목 목록 조회 실패 → 대형주 고정 목록 사용")
                # pykrx 실패 시 시총 상위 종목 하드코딩 (KOSPI 대형주)
                tickers = [
                    "005930", "000660", "005380", "035420", "000270",
                    "105560", "055550", "086790", "032830", "003550",
                    "207940", "006400", "051910", "028260", "066570",
                    "003490", "096770", "034730", "017670", "030200",
                    "011200", "018260", "010950", "009150", "010130",
                    "326030", "035720", "259960", "015760", "011070",
                ]
                query_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

            logger.info(f"  총 {len(tickers)}개 {market} 종목 (기준일: {query_date})")
        except Exception as e:
            logger.error(f"종목 목록 조회 실패: {e}")
            return []

        # 시가총액 상위 200개만 분석 (속도 최적화)
        try:
            cap_df = pykrx_stock.get_market_cap_by_ticker(query_date, market=market)
            cap_df = cap_df.sort_values("시가총액", ascending=False)
            top_tickers = cap_df.index[:200].tolist()
            logger.info(f"  시가총액 상위 200개 분석")
        except Exception:
            top_tickers = tickers[:200]

        scores = []
        for i, code in enumerate(top_tickers):
            try:
                score = self._score_kr_stock(code)
                if score:
                    scores.append(score)
                if (i + 1) % 50 == 0:
                    logger.info(f"  진행: {i+1}/{len(top_tickers)}")
                time.sleep(0.05)
            except Exception as e:
                logger.debug(f"  {code} 스코어링 실패: {e}")

        scores.sort(key=lambda x: x.score, reverse=True)
        top = scores[:top_n]
        logger.info(f"  스캔 완료: {len(scores)}개 → 상위 {len(top)}개 선발")
        return top

    def _score_kr_stock(self, code: str) -> Optional[AssetScore]:
        """개별 국내주식 점수 계산"""
        df = self.collector.get_kr_ohlcv(code, days=90)
        if df is None or len(df) < 20:
            return None

        df = TechnicalIndicators.add_rsi(df.copy())
        df = TechnicalIndicators.add_bollinger_bands(df)
        df = TechnicalIndicators.add_macd(df)
        df = TechnicalIndicators.add_moving_averages(df)
        required_cols = [c for c in ["rsi14", "bb_pct", "macd_hist", "ma5", "ma20"]
                         if c in df.columns]
        df = df.dropna(subset=required_cols)
        if len(df) < 10:
            return None

        latest   = df.iloc[-1]
        price    = float(latest["close"])
        prices   = df["close"].values

        change_1d  = float(df["close"].pct_change().iloc[-1])
        change_7d  = (prices[-1] / prices[min(-7, -len(prices))] - 1) if len(prices) >= 7 else 0
        change_30d = (prices[-1] / prices[min(-30, -len(prices))] - 1) if len(prices) >= 30 else 0

        vol_now   = float(df["volume"].iloc[-1])
        vol_avg   = float(df["volume"].iloc[-20:].mean())
        vol_ratio = vol_now / vol_avg if vol_avg > 0 else 1

        rsi    = float(latest.get("rsi14", 50))
        bb_pct = float(latest.get("bb_pct", 0.5))
        macd_h = float(latest.get("macd_hist", 0))
        macd_h_prev = float(df.iloc[-2].get("macd_hist", 0))

        # 이동평균 정배열 확인 (상승 추세)
        ma5   = float(latest.get("ma5", 0))
        ma20  = float(latest.get("ma20", 0))
        ma_aligned = (price > ma5 > ma20)  # 정배열

        # 점수 계산 (암호화폐와 동일한 체계)
        momentum_score = 0.0
        if change_7d > 0.10:   momentum_score = 30
        elif change_7d > 0.05: momentum_score = 25
        elif change_7d > 0.02: momentum_score = 20
        elif change_7d > 0.00: momentum_score = 15
        else:                   momentum_score = 5

        if ma_aligned:  momentum_score = min(30, momentum_score + 5)

        technical_score = 0.0
        if 30 <= rsi <= 50:    technical_score += 15
        elif 50 < rsi <= 60:   technical_score += 10
        elif rsi < 30:         technical_score += 8
        elif rsi > 70:         technical_score += 2

        if bb_pct < 0.30:      technical_score += 10
        elif bb_pct < 0.50:    technical_score += 8
        elif bb_pct < 0.70:    technical_score += 5
        else:                   technical_score += 2

        if macd_h > macd_h_prev:  technical_score += 5

        volume_score = 0.0
        if vol_ratio >= 3.0:   volume_score = 20
        elif vol_ratio >= 2.0: volume_score = 15
        elif vol_ratio >= 1.5: volume_score = 10
        elif vol_ratio >= 1.0: volume_score = 5

        volatility = float(df["close"].pct_change().std())
        if volatility < 0.01:   stability_score = 20
        elif volatility < 0.02: stability_score = 15
        elif volatility < 0.03: stability_score = 10
        elif volatility < 0.05: stability_score = 5
        else:                    stability_score = 0

        total_score = momentum_score + technical_score + volume_score + stability_score

        try:
            from pykrx import stock as ps
            name = ps.get_market_ticker_name(code)
        except Exception:
            name = code

        reasons = []
        if change_7d > 0.03:    reasons.append(f"7일+{change_7d:.1%}")
        if vol_ratio >= 2.0:    reasons.append(f"거래량{vol_ratio:.1f}x")
        if rsi < 40:            reasons.append(f"RSI과매도({rsi:.0f})")
        if ma_aligned:          reasons.append("이평정배열")
        if macd_h > macd_h_prev: reasons.append("MACD반등")

        return AssetScore(
            symbol=code,
            name=name,
            market_type="KR",
            current_price=price,
            score=total_score,
            momentum_score=momentum_score,
            technical_score=technical_score,
            volume_score=volume_score,
            stability_score=stability_score,
            rsi=rsi,
            bb_pct=bb_pct,
            change_1d=change_1d,
            change_7d=change_7d,
            change_30d=change_30d,
            volume_ratio=vol_ratio,
            reason=", ".join(reasons) if reasons else "기술적 중립",
        )

    # ─────────────────────────────────────────
    # 전체 스캔 & 결과 출력
    # ─────────────────────────────────────────
    def run_full_scan(self, crypto_top: int = 5,
                      kr_top: int = 5) -> dict:
        """암호화폐 + 국내주식 전체 스캔"""
        print("\n" + "=" * 65)
        print("  AI 스크리너 - 전체 시장 스캔")
        print(f"  실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 65)

        results = {"crypto": [], "kr": []}

        # 암호화폐 스캔
        print("\n[암호화폐] 업비트 KRW 전체 코인 스캔 중...")
        results["crypto"] = self.scan_crypto(top_n=crypto_top)

        # 국내주식 스캔
        print("\n[국내주식] KOSPI 시가총액 상위 200개 스캔 중...")
        results["kr"] = self.scan_kr_stocks(top_n=kr_top, market="KOSPI")

        # 결과 출력
        self._print_results(results)
        return results

    def _print_results(self, results: dict):
        """스캔 결과 출력"""
        print("\n" + "=" * 65)
        print("  스캔 결과 - AI 추천 종목")
        print("=" * 65)

        # 암호화폐
        if results["crypto"]:
            print(f"\n  [암호화폐 TOP {len(results['crypto'])}]")
            print(f"  {'순위':<4} {'종목':<10} {'점수':>5} {'RSI':>5} "
                  f"{'1일':>7} {'7일':>7} {'거래량':>5}  선발이유")
            print("  " + "-" * 60)
            for i, s in enumerate(results["crypto"], 1):
                print(
                    f"  {i:<4} {s.name:<10} {s.score:>5.1f} {s.rsi:>5.1f} "
                    f"{s.change_1d:>+6.2%} {s.change_7d:>+6.2%} "
                    f"{s.volume_ratio:>4.1f}x  {s.reason}"
                )

        # 국내주식
        if results["kr"]:
            print(f"\n  [국내주식 TOP {len(results['kr'])}]")
            print(f"  {'순위':<4} {'코드':<8} {'종목명':<10} {'점수':>5} {'RSI':>5} "
                  f"{'1일':>7} {'7일':>7} {'거래량':>5}  선발이유")
            print("  " + "-" * 65)
            for i, s in enumerate(results["kr"], 1):
                print(
                    f"  {i:<4} {s.symbol:<8} {s.name:<10} {s.score:>5.1f} "
                    f"{s.rsi:>5.1f} "
                    f"{s.change_1d:>+6.2%} {s.change_7d:>+6.2%} "
                    f"{s.volume_ratio:>4.1f}x  {s.reason}"
                )

        print("\n" + "=" * 65)
        all_assets = results["crypto"] + results["kr"]
        if all_assets:
            best = max(all_assets, key=lambda x: x.score)
            print(f"  ★ 최고 추천: {best.name} ({best.symbol}) | 점수: {best.score:.1f}")
        print("=" * 65)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    screener = AIScreener()
    screener.run_full_scan(crypto_top=5, kr_top=5)
