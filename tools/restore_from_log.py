"""
로그 파일에서 DRY-BUY / DRY-SELL 기록을 파싱하여 DB에 복원하는 스크립트.
컴퓨터가 꺼져서 DB에 기록이 없는 경우 사용.
"""
import sys
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, ".")

LOG_FILE = Path("logs/trading.log")
DB_FILE  = Path("db/trading.db")

# ───────────────────────────────────────────────────────────────
# 로그 형식 두 가지:
#   1) 2026-03-06 19:22:41 [INFO] module - [DRY-BUY] ...
#   2) 22:38:33   [DRY-BUY] ...  (날짜 없이 시간만)
# ───────────────────────────────────────────────────────────────

# 날짜 있는 형식
FULL_BUY_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2}).*?"
    r"\[DRY-BUY\]\s+(\S+)\s*\|\s*가격:\s*([\d,]+)\s*\|\s*금액:\s*([\d,]+)원"
)
FULL_SELL_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2}).*?"
    r"\[DRY-SELL\]\s+(\S+)\s*\|\s*가격:\s*([\d,]+)\s*\|\s*손익:\s*([+\-\d,]+)원"
)

# 시간만 있는 형식
TIME_BUY_PATTERN = re.compile(
    r"^(\d{2}:\d{2}:\d{2})\s+\[DRY-BUY\]\s+(\S+)\s*\|\s*가격:\s*([\d,]+)\s*\|\s*금액:\s*([\d,]+)원"
)
TIME_SELL_PATTERN = re.compile(
    r"^(\d{2}:\d{2}:\d{2})\s+\[DRY-SELL\]\s+(\S+)\s*\|\s*가격:\s*([\d,]+)\s*\|\s*손익:\s*([+\-\d,]+)원"
)


def parse_num(s: str) -> float:
    return float(s.replace(",", "").replace("+", ""))


def restore_from_log():
    if not LOG_FILE.exists():
        print(f"로그 파일 없음: {LOG_FILE}")
        return

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    # 이미 DB에 있는 symbol+side 쌍 (중복 방지)
    existing_rows = conn.execute("SELECT datetime, market, symbol, side FROM trades").fetchall()
    existing_keys = set(
        (r["datetime"], r["symbol"], r["side"])
        for r in existing_rows
    )

    inserted = 0
    skipped  = 0

    # 날짜 없는 줄에 쓸 추정 날짜 (로그 생성일 기준)
    stat = LOG_FILE.stat()
    log_date = datetime.fromtimestamp(stat.st_mtime).date()
    # 로그 첫 날짜를 찾아서 기준으로 사용
    current_date = None
    prev_hour = None

    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()

            # 날짜 추출 (기준 날짜 갱신)
            date_match = re.match(r"(\d{4}-\d{2}-\d{2})", line)
            if date_match:
                current_date = datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
                # 시간도 함께 추출해서 prev_hour 갱신
                time_match = re.search(r"(\d{2}):\d{2}:\d{2}", line)
                if time_match:
                    prev_hour = int(time_match.group(1))

            # 시간만 있는 경우 날짜 추정
            if current_date is None:
                current_date = log_date

            # 자정 넘어가면 날짜 +1
            time_match_line = re.match(r"(\d{2}):\d{2}:\d{2}", line)
            if time_match_line:
                cur_hour = int(time_match_line.group(1))
                if prev_hour is not None and prev_hour > cur_hour + 6:
                    current_date = current_date + timedelta(days=1)
                prev_hour = cur_hour

            # ── BUY 파싱 ──
            m = FULL_BUY_PATTERN.search(line)
            if not m:
                m2 = TIME_BUY_PATTERN.match(line)
                if m2:
                    time_s, symbol, price_s, amount_s = m2.groups()
                    dt_str = f"{current_date} {time_s}"
                else:
                    dt_str = None
                    m2 = None
            else:
                date_s, time_s, symbol, price_s, amount_s = m.groups()
                dt_str = f"{date_s} {time_s}"
                m2 = m  # 재사용 표시

            if dt_str and (m or m2):
                if not m:  # TIME_BUY_PATTERN 매치
                    m_use = m2
                    symbol    = m_use.group(2)
                    price_s   = m_use.group(3)
                    amount_s  = m_use.group(4)
                else:
                    symbol    = m.group(3)
                    price_s   = m.group(4)
                    amount_s  = m.group(5)

                price  = parse_num(price_s)
                amount = parse_num(amount_s)
                qty    = amount / price if price > 0 else 0
                mkt    = _infer_market(symbol)
                key    = (dt_str, symbol, "DRY-BUY")

                if key in existing_keys:
                    skipped += 1
                else:
                    conn.execute(
                        "INSERT INTO trades (datetime,market,symbol,side,price,quantity,amount,fee,strategy,note) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (dt_str, mkt, symbol, "DRY-BUY", price, qty, amount, 0, "DryRun(복원)", "로그복원")
                    )
                    existing_keys.add(key)
                    inserted += 1
                    print(f"  [복원 BUY ] {dt_str} | {symbol:<14} | {price:,.0f}원 | {amount:,.0f}원")

            # ── SELL 파싱 ──
            m = FULL_SELL_PATTERN.search(line)
            if not m:
                m2 = TIME_SELL_PATTERN.match(line)
                if m2:
                    time_s, symbol, price_s, pnl_s = m2.groups()
                    dt_str = f"{current_date} {time_s}"
                else:
                    dt_str = None
                    m2 = None
            else:
                date_s, time_s, symbol, price_s, pnl_s = m.groups()
                dt_str = f"{date_s} {time_s}"
                m2 = m

            if dt_str and (m or m2):
                if not m:
                    m_use = m2
                    symbol  = m_use.group(2)
                    price_s = m_use.group(3)
                    pnl_s   = m_use.group(4)
                else:
                    symbol  = m.group(3)
                    price_s = m.group(4)
                    pnl_s   = m.group(5)

                price = parse_num(price_s)
                pnl   = parse_num(pnl_s)
                mkt   = _infer_market(symbol)
                key   = (dt_str, symbol, "DRY-SELL")

                if key in existing_keys:
                    skipped += 1
                else:
                    conn.execute(
                        "INSERT INTO trades (datetime,market,symbol,side,price,quantity,amount,fee,strategy,note) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (dt_str, mkt, symbol, "DRY-SELL", price, 0, pnl, 0, "DryRun(복원)", f"pnl:{pnl:+,.0f}")
                    )
                    existing_keys.add(key)
                    inserted += 1
                    sign = "+" if pnl >= 0 else ""
                    print(f"  [복원 SELL] {dt_str} | {symbol:<14} | {price:,.0f}원 | PnL:{sign}{pnl:,.0f}원")

    conn.commit()
    conn.close()
    print(f"\n완료: {inserted}건 복원, {skipped}건 중복 스킵")


def _infer_market(symbol: str) -> str:
    if symbol.startswith("KRW-"):
        return "CRYPTO"
    if re.match(r"^[A-Z]{1,5}$", symbol):
        return "US"
    return "KR"


def show_summary():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    print("\n=== DB 거래 현황 ===")
    rows = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
    sells = [r for r in rows if "SELL" in r["side"]]
    buys  = [r for r in rows if "BUY"  in r["side"]]

    print(f"총 {len(rows)}건  (매수:{len(buys)} / 매도:{len(sells)})")

    total_pnl = 0.0
    wins = losses = 0
    for r in sells:
        note = r["note"] or ""
        m = re.search(r"pnl:([+\-\d,.]+)", note)
        if m:
            try:
                pnl = float(m.group(1).replace(",", ""))
                total_pnl += pnl
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
            except ValueError:
                pass

    print(f"누적 손익  : {total_pnl:+,.0f}원")
    if wins + losses > 0:
        wr = wins / (wins + losses) * 100
        print(f"승률       : {wr:.1f}% ({wins}승 {losses}패)")
    print(f"수익률     : {total_pnl / 100000 * 100:+.2f}% (초기 10만원 대비)")

    conn.close()


if __name__ == "__main__":
    print("=== 로그 → DB 복원 스크립트 ===")
    restore_from_log()
    show_summary()
