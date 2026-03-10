"""
한국투자증권 KIS Developers API 모듈
국내주식 + 미국주식 시세 조회 및 매수/매도 주문 처리
"""
import json
import time
import logging
import requests
from datetime import datetime

from config.settings import (
    KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO,
    KIS_IS_PAPER_TRADING, KIS_BASE_URL
)

logger = logging.getLogger(__name__)


class KISAPI:
    """한국투자증권 API 클래스"""

    def __init__(self):
        self.app_key = KIS_APP_KEY
        self.app_secret = KIS_APP_SECRET
        self.account_no = KIS_ACCOUNT_NO
        self.base_url = KIS_BASE_URL
        self.is_paper = KIS_IS_PAPER_TRADING

        # 액세스 토큰 (OAuth)
        self.access_token = None
        self.token_expired_at = None

        mode = "모의투자" if self.is_paper else "실전투자"
        logger.info(f"KIS API 초기화 완료 [{mode}]")

    # ─────────────────────────────────────────
    # 인증 토큰 관리
    # ─────────────────────────────────────────
    def get_access_token(self):
        """OAuth 액세스 토큰 발급"""
        url = f"{self.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        try:
            res = requests.post(url, json=body)
            res.raise_for_status()
            data = res.json()
            self.access_token = data.get("access_token")
            self.token_expired_at = data.get("access_token_token_expired")
            logger.info(f"KIS 토큰 발급 성공 (만료: {self.token_expired_at})")
            return self.access_token
        except Exception as e:
            logger.error(f"KIS 토큰 발급 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"응답: {e.response.text}")
            return None

    def _get_headers(self, tr_id: str, extra: dict = None):
        """공통 요청 헤더 생성"""
        if not self.access_token:
            self.get_access_token()

        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
        }
        if extra:
            headers.update(extra)
        return headers

    # ─────────────────────────────────────────
    # 국내주식 시세 조회
    # ─────────────────────────────────────────
    def get_kr_stock_price(self, stock_code: str):
        """국내주식 현재가 조회"""
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = self._get_headers("FHKST01010100")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }
        try:
            res = requests.get(url, headers=headers, params=params)
            res.raise_for_status()
            data = res.json()
            if data.get("rt_cd") == "0":
                output = data.get("output", {})
                return {
                    "code": stock_code,
                    "name": output.get("hts_kor_isnm", ""),
                    "price": int(output.get("stck_prpr", 0)),        # 현재가
                    "change": int(output.get("prdy_vrss", 0)),        # 전일대비
                    "change_rate": float(output.get("prdy_ctrt", 0)), # 등락률
                    "volume": int(output.get("acml_vol", 0)),          # 거래량
                    "high": int(output.get("stck_hgpr", 0)),           # 고가
                    "low": int(output.get("stck_lwpr", 0)),             # 저가
                }
            else:
                logger.error(f"국내주식 시세 조회 실패: {data.get('msg1')}")
                return None
        except Exception as e:
            logger.error(f"국내주식 시세 조회 오류 ({stock_code}): {e}")
            return None

    # ─────────────────────────────────────────
    # 국내주식 잔고 조회
    # ─────────────────────────────────────────
    def get_kr_balance(self):
        """국내주식 잔고 조회"""
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"

        # 모의투자/실전투자 TR_ID 분기
        tr_id = "VTTC8434R" if self.is_paper else "TTTC8434R"
        headers = self._get_headers(tr_id)

        account = self.account_no.split("-")
        params = {
            "CANO": account[0],                # 계좌번호 앞 8자리
            "ACNT_PRDT_CD": account[1] if len(account) > 1 else "01",  # 계좌상품코드
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "N",
            "INQR_DVSN": "01",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        try:
            res = requests.get(url, headers=headers, params=params)
            res.raise_for_status()
            data = res.json()
            if data.get("rt_cd") == "0":
                output2 = data.get("output2", [{}])
                summary = output2[0] if output2 else {}
                return {
                    "holdings": data.get("output1", []),           # 보유 종목
                    "total_eval": int(summary.get("tot_evlu_amt", 0)),     # 총평가금액
                    "cash": int(summary.get("dnca_tot_amt", 0)),           # 예수금
                    "profit_loss": int(summary.get("evlu_pfls_smtl_amt", 0)),  # 평가손익
                }
            else:
                logger.error(f"잔고 조회 실패: {data.get('msg1')}")
                return None
        except Exception as e:
            logger.error(f"잔고 조회 오류: {e}")
            return None

    # ─────────────────────────────────────────
    # 미국주식 시세 조회
    # ─────────────────────────────────────────
    def get_us_stock_price(self, symbol: str, exchange: str = "NAS"):
        """미국주식 현재가 조회
        exchange: NAS(나스닥), NYS(뉴욕), AMS(아멕스)
        """
        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/price"
        headers = self._get_headers("HHDFS00000300")
        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": symbol,
        }
        try:
            res = requests.get(url, headers=headers, params=params)
            res.raise_for_status()
            data = res.json()
            if data.get("rt_cd") == "0":
                output = data.get("output", {})
                return {
                    "symbol": symbol,
                    "price": float(output.get("last", 0)),       # 현재가 (USD)
                    "change": float(output.get("diff", 0)),       # 전일대비
                    "change_rate": float(output.get("rate", 0)),  # 등락률
                    "volume": int(output.get("tvol", 0)),          # 거래량
                    "high": float(output.get("high", 0)),
                    "low": float(output.get("low", 0)),
                }
            else:
                logger.error(f"미국주식 시세 조회 실패: {data.get('msg1')}")
                return None
        except Exception as e:
            logger.error(f"미국주식 시세 조회 오류 ({symbol}): {e}")
            return None

    # ─────────────────────────────────────────
    # 국내주식 매수/매도 주문
    # ─────────────────────────────────────────
    def buy_kr_stock(self, stock_code: str, qty: int, price: int = 0, order_type: str = "01"):
        """국내주식 매수 주문
        order_type: "00"=지정가, "01"=시장가
        """
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        tr_id = "VTTC0802U" if self.is_paper else "TTTC0802U"
        headers = self._get_headers(tr_id)

        account = self.account_no.split("-")
        body = {
            "CANO": account[0],
            "ACNT_PRDT_CD": account[1] if len(account) > 1 else "01",
            "PDNO": stock_code,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),  # 시장가일 때 "0"
        }
        try:
            res = requests.post(url, headers=headers, json=body)
            res.raise_for_status()
            data = res.json()
            if data.get("rt_cd") == "0":
                logger.info(f"매수 주문 성공: {stock_code} {qty}주")
                return data.get("output")
            else:
                logger.error(f"매수 주문 실패: {data.get('msg1')}")
                return None
        except Exception as e:
            logger.error(f"매수 주문 오류: {e}")
            return None

    def sell_kr_stock(self, stock_code: str, qty: int, price: int = 0, order_type: str = "01"):
        """국내주식 매도 주문"""
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        tr_id = "VTTC0801U" if self.is_paper else "TTTC0801U"
        headers = self._get_headers(tr_id)

        account = self.account_no.split("-")
        body = {
            "CANO": account[0],
            "ACNT_PRDT_CD": account[1] if len(account) > 1 else "01",
            "PDNO": stock_code,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        try:
            res = requests.post(url, headers=headers, json=body)
            res.raise_for_status()
            data = res.json()
            if data.get("rt_cd") == "0":
                logger.info(f"매도 주문 성공: {stock_code} {qty}주")
                return data.get("output")
            else:
                logger.error(f"매도 주문 실패: {data.get('msg1')}")
                return None
        except Exception as e:
            logger.error(f"매도 주문 오류: {e}")
            return None


def test_connection():
    """KIS API 연결 테스트"""
    print("\n" + "=" * 50)
    mode = "모의투자" if KIS_IS_PAPER_TRADING else "실전투자"
    print(f"  KIS API 연결 테스트 [{mode}]")
    print("=" * 50)

    api = KISAPI()

    # 1. 액세스 토큰 발급
    print("\n[1] 액세스 토큰 발급...")
    token = api.get_access_token()
    if token:
        print(f"    토큰 발급 성공! ({token[:20]}...)")
    else:
        print("    ❌ 토큰 발급 실패 - API 키를 확인하세요")
        return

    # 2. 국내주식 시세 조회 (삼성전자)
    print("\n[2] 국내주식 시세 조회 (삼성전자 005930)...")
    samsung = api.get_kr_stock_price("005930")
    if samsung:
        print(f"    {samsung['name']}: {samsung['price']:,}원 ({samsung['change_rate']:+.2f}%)")
    else:
        print("    ❌ 시세 조회 실패")

    # 3. 국내주식 잔고 조회
    print("\n[3] 모의투자 잔고 조회...")
    balance = api.get_kr_balance()
    if balance:
        print(f"    예수금       : {balance['cash']:>15,} 원")
        print(f"    총평가금액   : {balance['total_eval']:>15,} 원")
        print(f"    평가손익     : {balance['profit_loss']:>+15,} 원")
        holdings = balance.get("holdings", [])
        if holdings:
            print(f"    보유 종목 수 : {len(holdings)}개")
    else:
        print("    ❌ 잔고 조회 실패")

    # 4. 미국주식 시세 조회 (애플) - 장 마감 시 0으로 나올 수 있음
    print("\n[4] 미국주식 시세 조회 (AAPL)...")
    aapl = api.get_us_stock_price("AAPL")
    if aapl:
        print(f"    AAPL: ${aapl['price']:.2f} ({aapl['change_rate']:+.2f}%)")
    else:
        print("    ❌ 미국주식 시세 조회 실패 (장 마감 시간일 수 있음)")

    print("\n" + "=" * 50)
    print("  연결 테스트 완료!")
    print("=" * 50)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    test_connection()
