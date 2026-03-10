"""
업비트 Open API 모듈
암호화폐 잔고 조회, 시세 조회, 매수/매도 주문 처리
"""
import jwt
import uuid
import hashlib
import logging
from urllib.parse import urlencode, unquote
import requests
import pyupbit

from config.settings import UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY

logger = logging.getLogger(__name__)


class UpbitAPI:
    """업비트 API 클래스"""

    BASE_URL = "https://api.upbit.com/v1"

    def __init__(self):
        self.access_key = UPBIT_ACCESS_KEY
        self.secret_key = UPBIT_SECRET_KEY

        # pyupbit 객체 (편의 메서드용)
        if self.access_key and self.secret_key:
            self.upbit = pyupbit.Upbit(self.access_key, self.secret_key)
        else:
            self.upbit = None
            logger.warning("업비트 API 키가 설정되지 않았습니다")

    def _get_auth_header(self, query_params=None):
        """JWT 인증 헤더 생성"""
        payload = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
        }

        # 쿼리 파라미터가 있으면 해시 추가
        if query_params:
            query_string = unquote(urlencode(query_params, doseq=True)).encode("utf-8")
            m = hashlib.sha512()
            m.update(query_string)
            query_hash = m.hexdigest()
            payload["query_hash"] = query_hash
            payload["query_hash_alg"] = "SHA512"

        jwt_token = jwt.encode(payload, self.secret_key, algorithm="HS256")
        return {"Authorization": f"Bearer {jwt_token}"}

    # ─────────────────────────────────────────
    # 잔고 조회
    # ─────────────────────────────────────────
    def get_balances(self):
        """전체 잔고 조회"""
        try:
            headers = self._get_auth_header()
            res = requests.get(f"{self.BASE_URL}/accounts", headers=headers)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            logger.error(f"잔고 조회 실패: {e}")
            return []

    def get_krw_balance(self):
        """원화(KRW) 잔고 조회"""
        balances = self.get_balances()
        for b in balances:
            if b.get("currency") == "KRW":
                return float(b.get("balance", 0))
        return 0.0

    def get_coin_balance(self, ticker: str):
        """특정 코인 잔고 조회 (예: 'BTC')"""
        balances = self.get_balances()
        for b in balances:
            if b.get("currency") == ticker:
                return float(b.get("balance", 0))
        return 0.0

    # ─────────────────────────────────────────
    # 시세 조회 (인증 불필요 - 공개 API)
    # ─────────────────────────────────────────
    def get_current_price(self, market: str):
        """현재가 조회 (예: 'KRW-BTC')"""
        try:
            price = pyupbit.get_current_price(market)
            return price
        except Exception as e:
            logger.error(f"현재가 조회 실패 ({market}): {e}")
            return None

    def get_ohlcv(self, market: str, interval: str = "day", count: int = 200):
        """캔들 데이터 조회
        interval: 'day', 'minute1', 'minute3', 'minute5', 'minute15', 'minute60', 'week', 'month'
        """
        try:
            df = pyupbit.get_ohlcv(market, interval=interval, count=count)
            return df
        except Exception as e:
            logger.error(f"OHLCV 조회 실패 ({market}): {e}")
            return None

    def get_orderbook(self, market: str):
        """호가 조회"""
        try:
            res = requests.get(
                f"{self.BASE_URL}/orderbook",
                params={"markets": market}
            )
            res.raise_for_status()
            return res.json()
        except Exception as e:
            logger.error(f"호가 조회 실패 ({market}): {e}")
            return None

    # ─────────────────────────────────────────
    # 주문
    # ─────────────────────────────────────────
    def buy_market_order(self, market: str, price: float):
        """시장가 매수 (price: 매수할 금액, 원화 기준)"""
        try:
            result = self.upbit.buy_market_order(market, price)
            logger.info(f"시장가 매수 완료: {market} {price:,.0f}원")
            return result
        except Exception as e:
            logger.error(f"시장가 매수 실패 ({market}): {e}")
            return None

    def sell_market_order(self, market: str, volume: float):
        """시장가 매도 (volume: 매도할 코인 수량)"""
        try:
            result = self.upbit.sell_market_order(market, volume)
            logger.info(f"시장가 매도 완료: {market} {volume} 개")
            return result
        except Exception as e:
            logger.error(f"시장가 매도 실패 ({market}): {e}")
            return None

    def buy_limit_order(self, market: str, price: float, volume: float):
        """지정가 매수"""
        try:
            result = self.upbit.buy_limit_order(market, price, volume)
            logger.info(f"지정가 매수 주문: {market} {price:,.0f}원 x {volume}")
            return result
        except Exception as e:
            logger.error(f"지정가 매수 실패 ({market}): {e}")
            return None

    def sell_limit_order(self, market: str, price: float, volume: float):
        """지정가 매도"""
        try:
            result = self.upbit.sell_limit_order(market, price, volume)
            logger.info(f"지정가 매도 주문: {market} {price:,.0f}원 x {volume}")
            return result
        except Exception as e:
            logger.error(f"지정가 매도 실패 ({market}): {e}")
            return None

    def cancel_order(self, uuid_str: str):
        """주문 취소"""
        try:
            params = {"uuid": uuid_str}
            headers = self._get_auth_header(params)
            res = requests.delete(f"{self.BASE_URL}/order", params=params, headers=headers)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            logger.error(f"주문 취소 실패: {e}")
            return None

    def get_orders(self, state: str = "wait"):
        """주문 목록 조회 (state: 'wait', 'done', 'cancel')"""
        try:
            params = {"state": state}
            headers = self._get_auth_header(params)
            res = requests.get(f"{self.BASE_URL}/orders", params=params, headers=headers)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            logger.error(f"주문 목록 조회 실패: {e}")
            return []


def test_connection():
    """업비트 API 연결 테스트"""
    print("\n" + "=" * 50)
    print("  업비트 API 연결 테스트")
    print("=" * 50)

    api = UpbitAPI()

    # 1. 잔고 조회 테스트
    print("\n[1] 원화 잔고 조회...")
    krw = api.get_krw_balance()
    print(f"    KRW 잔고: {krw:,.0f} 원")

    # 2. 전체 보유 코인 조회
    print("\n[2] 전체 보유 자산...")
    balances = api.get_balances()
    if balances:
        for b in balances:
            currency = b.get("currency")
            balance = float(b.get("balance", 0))
            if balance > 0:
                print(f"    {currency}: {balance}")
    else:
        print("    보유 자산 없음 또는 조회 실패")

    # 3. 비트코인 현재가 조회 (인증 불필요)
    print("\n[3] 비트코인(BTC) 현재가...")
    btc_price = api.get_current_price("KRW-BTC")
    if btc_price:
        print(f"    BTC: {btc_price:,.0f} 원")

    # 4. 이더리움 현재가
    print("\n[4] 이더리움(ETH) 현재가...")
    eth_price = api.get_current_price("KRW-ETH")
    if eth_price:
        print(f"    ETH: {eth_price:,.0f} 원")

    print("\n" + "=" * 50)
    print("  연결 테스트 완료!")
    print("=" * 50)


if __name__ == "__main__":
    test_connection()
