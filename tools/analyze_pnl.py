"""
Dry Run 성과 분석 도구 (DB 기반)
- 거래 내역 전체 출력
- 수익률 / 승률 / 평균 손익 계산
- 현재 오픈 포지션 미실현 손익
"""
import sys
import re
import sqlite3
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

DB_FILE      = Path("db/trading.db")
STATE_FILE   = Path("db/virtual_state.json")
INITIAL_CAP  = 100_000


def analyze():
    if not DB_FILE.exists():
        print("DB 파일이 없습니다: db/trading.db")
        return

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT * FROM trades ORDER BY id"
    ).fetchall()

    positions = conn.execute(
        "SELECT * FROM positions"
    ).fetchall()

    conn.close()

    if not rows:
        print("DB에 거래 기록이 없습니다.")
        return

    # ─────────────────────────────────────
    # 거래 목록
    # ─────────────────────────────────────
    print("=" * 80)
    print(f"{'#':>4}  {'날짜시간':<20} {'구분':<10} {'종목':<14} {'가격':>14} {'금액/손익':>12}  메모")
    print("-" * 80)

    buys  = []
    sells = []

    for i, r in enumerate(rows, 1):
        side    = r["side"]
        price   = r["price"]   or 0
        amount  = r["amount"]  or 0
        note    = r["note"]    or ""
        symbol  = r["symbol"]

        if "BUY" in side:
            buys.append(r)
            line = f"{i:>4}  {r['datetime']:<20} {side:<10} {symbol:<14} {price:>14,.0f} {amount:>12,.0f}원  {note}"
        else:
            sells.append(r)
            pnl = _extract_pnl(note, amount)
            sign = "+" if pnl >= 0 else ""
            line = f"{i:>4}  {r['datetime']:<20} {side:<10} {symbol:<14} {price:>14,.0f} {sign}{pnl:>11,.0f}원  {note}"

        print(line)

    print("=" * 80)

    # ─────────────────────────────────────
    # 성과 요약
    # ─────────────────────────────────────
    total_pnl = 0.0
    wins = losses = 0
    pnl_list = []

    for r in sells:
        pnl = _extract_pnl(r["note"] or "", r["amount"] or 0)
        total_pnl += pnl
        pnl_list.append(pnl)
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1

    n_sells = len(sells)
    print(f"\n[요약]")
    print(f"  매수 횟수   : {len(buys)}건")
    print(f"  매도 횟수   : {n_sells}건")

    if n_sells > 0:
        wr = wins / n_sells * 100
        avg_pnl = total_pnl / n_sells
        avg_win  = sum(p for p in pnl_list if p > 0) / wins if wins > 0 else 0
        avg_loss = sum(p for p in pnl_list if p < 0) / losses if losses > 0 else 0

        print(f"  승률        : {wr:.1f}%  ({wins}승 {losses}패)")
        print(f"  누적 손익   : {total_pnl:+,.0f}원")
        print(f"  수익률      : {total_pnl / INITIAL_CAP * 100:+.2f}% (초기 10만원 대비)")
        print(f"  평균 손익   : {avg_pnl:+,.0f}원")
        print(f"  평균 이익   : {avg_win:+,.0f}원 (이익 거래 기준)")
        print(f"  평균 손실   : {avg_loss:+,.0f}원 (손실 거래 기준)")
        if avg_loss < 0:
            rr = abs(avg_win / avg_loss)
            print(f"  손익비      : {rr:.2f} (≥2.0 권장)")

    # ─────────────────────────────────────
    # 가상 잔고 현황
    # ─────────────────────────────────────
    print()
    vkrw = INITIAL_CAP + total_pnl
    if STATE_FILE.exists():
        try:
            import json
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            vkrw = float(data.get("virtual_krw", vkrw))
            updated = data.get("updated_at", "알 수 없음")
            print(f"  가상 잔고   : {vkrw:,.0f}원  (마지막 저장: {updated})")
        except Exception:
            print(f"  가상 잔고   : {vkrw:,.0f}원 (추정)")
    else:
        print(f"  가상 잔고   : {vkrw:,.0f}원 (DB pnl 기준 추정)")

    # ─────────────────────────────────────
    # 오픈 포지션 미실현 손익
    # ─────────────────────────────────────
    if positions:
        print(f"\n[오픈 포지션 {len(positions)}개]")
        print(f"  {'종목':<14} {'진입가':>14} {'수량':>12} {'손절가':>14} {'익절가':>14}")
        print(f"  {'-'*14} {'-'*14} {'-'*12} {'-'*14} {'-'*14}")
        for p in positions:
            p_dict = dict(p)
            ep  = float(p_dict.get("entry_price") or 0)
            qty = float(p_dict.get("quantity") or 0)
            sl  = float(p_dict.get("stop_loss") or 0)
            tp  = float(p_dict.get("take_profit") or 0)
            print(f"  {p_dict.get('symbol','?'):<14} {ep:>14,.0f} {qty:>12.6f} {sl:>14,.0f} {tp:>14,.0f}")
    else:
        print("\n  오픈 포지션 없음")

    print()


def _extract_pnl(note: str, fallback: float) -> float:
    """note에서 pnl 값 추출, 실패 시 fallback(amount) 사용"""
    m = re.search(r"pnl:([+\-\d,.]+)", note)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    # amount 자체가 pnl인 경우 (복원 레코드)
    return float(fallback)


if __name__ == "__main__":
    analyze()
