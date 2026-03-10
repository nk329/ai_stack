"""
시장 데이터 수집 모듈
국내주식, 미국주식, 암호화폐 OHLCV 데이터 수집
"""
import logging
import pandas as pd
import yfinance as yf
import pyupbit
from datetime import datetime, timedelta
from pykrx import stock as pykrx_stock

logger = logging.getLogger(__name__)


class DataCollector:
    """시장 데이터 수집 클래스"""

    # ─────────────────────────────────────────
    # 국내주식 데이터
    # ─────────────────────────────────────────
    def get_kr_ohlcv(self, stock_code: str, days: int = 200) -> pd.DataFrame:
        """국내주식 OHLCV 데이터 조회 (pykrx)
        Args:
            stock_code: 종목코드 (예: '005930')
            days: 조회 기간 (일)
        """
        try:
            end = datetime.today().strftime("%Y%m%d")
            start = (datetime.today() - timedelta(days=days)).strftime("%Y%m%d")
            df = pykrx_stock.get_market_ohlcv_by_date(start, end, stock_code)
            df.index = pd.to_datetime(df.index)
            # pykrx 버전에 따라 컬럼 수가 다름 (6개 또는 7개)
            col_map = {
                6: ["open", "high", "low", "close", "volume", "change"],
                7: ["open", "high", "low", "close", "volume", "value", "change"],
            }
            cols = col_map.get(len(df.columns))
            if cols:
                df.columns = cols
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            logger.info(f"국내주식 데이터 수집: {stock_code} ({len(df)}일)")
            return df
        except Exception as e:
            logger.error(f"국내주식 데이터 수집 실패 ({stock_code}): {e}")
            return pd.DataFrame()

    def get_kr_stock_name(self, stock_code: str) -> str:
        """종목코드로 종목명 조회"""
        try:
            return pykrx_stock.get_market_ticker_name(stock_code)
        except Exception:
            return stock_code

    def get_kr_market_tickers(self, market: str = "KOSPI") -> list:
        """상장 종목 코드 전체 조회
        market: 'KOSPI', 'KOSDAQ', 'KONEX'
        """
        try:
            today = datetime.today().strftime("%Y%m%d")
            tickers = pykrx_stock.get_market_ticker_list(today, market=market)
            return tickers
        except Exception as e:
            logger.error(f"종목 목록 조회 실패: {e}")
            return []

    # ─────────────────────────────────────────
    # 미국주식 데이터
    # ─────────────────────────────────────────
    def get_us_ohlcv(self, symbol: str, days: int = 200) -> pd.DataFrame:
        """미국주식 OHLCV 데이터 조회 (yfinance)
        Args:
            symbol: 티커 심볼 (예: 'AAPL')
            days: 조회 기간 (일)
        """
        try:
            end = datetime.today()
            start = end - timedelta(days=days)
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            logger.info(f"미국주식 데이터 수집: {symbol} ({len(df)}일)")
            return df
        except Exception as e:
            logger.error(f"미국주식 데이터 수집 실패 ({symbol}): {e}")
            return pd.DataFrame()

    def get_us_stock_info(self, symbol: str) -> dict:
        """미국주식 기본 정보 조회"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            return {
                "symbol": symbol,
                "name": info.get("longName", symbol),
                "sector": info.get("sector", ""),
                "market_cap": info.get("marketCap", 0),
                "pe_ratio": info.get("trailingPE", 0),
            }
        except Exception as e:
            logger.error(f"미국주식 정보 조회 실패 ({symbol}): {e}")
            return {}

    # ─────────────────────────────────────────
    # 암호화폐 데이터
    # ─────────────────────────────────────────
    def get_crypto_ohlcv(self, market: str, interval: str = "day", count: int = 200) -> pd.DataFrame:
        """암호화폐 OHLCV 데이터 조회 (업비트)
        Args:
            market: 마켓 코드 (예: 'KRW-BTC')
            interval: 'day', 'minute1', 'minute3', 'minute5', 'minute15', 'minute60', 'week'
            count: 데이터 개수
        """
        try:
            df = pyupbit.get_ohlcv(market, interval=interval, count=count)
            if df is None or df.empty:
                return pd.DataFrame()
            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            logger.info(f"암호화폐 데이터 수집: {market} ({len(df)}개)")
            return df
        except Exception as e:
            logger.error(f"암호화폐 데이터 수집 실패 ({market}): {e}")
            return pd.DataFrame()

    def get_crypto_current_price(self, market: str) -> float:
        """암호화폐 현재가 조회"""
        try:
            return pyupbit.get_current_price(market)
        except Exception as e:
            logger.error(f"암호화폐 현재가 조회 실패 ({market}): {e}")
            return 0.0

    def get_all_krw_markets(self) -> list:
        """업비트 KRW 마켓 전체 목록 조회"""
        try:
            import requests
            res = requests.get("https://api.upbit.com/v1/market/all?isDetails=false")
            markets = res.json()
            krw_markets = [m["market"] for m in markets if m["market"].startswith("KRW-")]
            return krw_markets
        except Exception as e:
            logger.error(f"마켓 목록 조회 실패: {e}")
            return []


def test_collector():
    """데이터 수집 테스트"""
    print("\n" + "=" * 50)
    print("  데이터 수집 모듈 테스트")
    print("=" * 50)

    collector = DataCollector()

    # 국내주식 테스트
    print("\n[1] 삼성전자 국내주식 데이터 (최근 10일)...")
    df_kr = collector.get_kr_ohlcv("005930", days=20)
    if not df_kr.empty:
        print(df_kr.tail(3).to_string())
    else:
        print("    ❌ 데이터 없음")

    # 미국주식 테스트
    print("\n[2] AAPL 미국주식 데이터 (최근 3일)...")
    df_us = collector.get_us_ohlcv("AAPL", days=10)
    if not df_us.empty:
        print(df_us.tail(3).to_string())
    else:
        print("    ❌ 데이터 없음")

    # 암호화폐 테스트
    print("\n[3] BTC 암호화폐 데이터 (최근 3일)...")
    df_btc = collector.get_crypto_ohlcv("KRW-BTC", count=5)
    if not df_btc.empty:
        print(df_btc.tail(3).to_string())
    else:
        print("    ❌ 데이터 없음")

    print("\n✅ 데이터 수집 테스트 완료!")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    test_collector()
