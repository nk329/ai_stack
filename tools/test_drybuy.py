"""DRY-BUY 로직 직접 테스트 - 가상 BUY 신호를 강제로 주입해서 확인"""
import sys
sys.path.insert(0, ".")

from config.settings import INITIAL_CAPITAL
from db.database import TradingDB
from api.upbit_api import UpbitAPI

upbit = UpbitAPI()
db    = TradingDB()

virtual_krw = INITIAL_CAPITAL  # 100,000원

market        = "KRW-POKT"
current_price = upbit.get_current_price(market)
sl, tp        = 0.05, 0.15
budget        = 9000  # 할당 자본금
invest        = min(budget, virtual_krw * 0.95)

print(f"\n=== DRY-BUY 테스트 ===")
print(f"종목      : {market}")
print(f"현재가    : {current_price:,.2f}원")
print(f"가상잔고  : {virtual_krw:,.0f}원")
print(f"투자금    : {invest:,.0f}원")
print(f"5000원 체크: {'통과' if invest >= 5000 else '실패 (5000원 미만)'}")

position = db.get_position("CRYPTO", market)
print(f"기존 포지션: {'있음' if position else '없음 (매수 가능)'}")

if invest >= 5000 and not position:
    stop_price = current_price * (1 - sl)
    take_price = current_price * (1 + tp)
    qty        = invest / current_price
    virtual_krw -= invest

    print(f"\n✅ DRY-BUY 실행!")
    print(f"  가격   : {current_price:,.2f}원")
    print(f"  수량   : {qty:.4f}")
    print(f"  손절   : {stop_price:,.2f}원 (-{sl:.0%})")
    print(f"  익절   : {take_price:,.2f}원 (+{tp:.0%})")
    print(f"  잔여   : {virtual_krw:,.0f}원")

    # DB에 포지션 기록
    db.open_position("CRYPTO", market, current_price, qty,
                     stop_price, take_price, "TestDryRun")
    print(f"\nDB 저장 완료 - 포지션 확인:")
    pos = db.get_position("CRYPTO", market)
    print(f"  {pos}")
else:
    print(f"\n❌ DRY-BUY 미실행 (잔고부족 또는 포지션 있음)")
