"""
로컬 DB + VM DB 병합 스크립트
- 중복 제거: datetime + market + symbol + side 기준
- positions는 VM 기준으로 덮어쓰기 (최신 상태 우선)
- 병합 결과: db/trading_merged.db
"""
import sqlite3
import shutil
from pathlib import Path
from datetime import datetime

LOCAL_DB  = Path("db/trading.db")
VM_DB     = Path("db/trading_vm.db")
MERGED_DB = Path("db/trading_merged.db")


def get_schema(conn):
    """테이블 생성 SQL 가져오기"""
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
    ).fetchall()
    return [r[0] for r in rows]


def merge():
    if not LOCAL_DB.exists():
        print(f"❌ 로컬 DB 없음: {LOCAL_DB}")
        return
    if not VM_DB.exists():
        print(f"❌ VM DB 없음: {VM_DB}")
        print("   PowerShell에서 먼저 실행하세요:")
        print("   scp namkyu@172.16.1.15:~/ai_stack/db/trading.db d:\\ai_stock\\db\\trading_vm.db")
        return

    # 로컬 DB를 기준으로 복사 후 VM 데이터 추가
    shutil.copy2(LOCAL_DB, MERGED_DB)
    print(f"✅ 병합 DB 생성: {MERGED_DB}")

    merged = sqlite3.connect(MERGED_DB)
    vm     = sqlite3.connect(VM_DB)

    # ─────────────────────────────────────────
    # trades 병합 (중복 제거)
    # ─────────────────────────────────────────
    print("\n[trades 병합]")

    # 기존 로컬 키 셋
    existing = set()
    for row in merged.execute("SELECT datetime, market, symbol, side FROM trades"):
        existing.add(tuple(row))

    vm_trades = vm.execute(
        "SELECT datetime, market, symbol, side, price, quantity, amount, strategy, note FROM trades"
    ).fetchall()

    added = 0
    skipped = 0
    for t in vm_trades:
        key = (t[0], t[1], t[2], t[3])  # datetime, market, symbol, side
        if key in existing:
            skipped += 1
            continue
        merged.execute("""
            INSERT INTO trades (datetime, market, symbol, side, price, quantity, amount, strategy, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, t)
        existing.add(key)
        added += 1
        print(f"  ➕ {t[0]} | {t[1]} | {t[2]} | {t[3]}")

    print(f"  추가: {added}건 / 중복 스킵: {skipped}건")

    # ─────────────────────────────────────────
    # positions 병합 (VM 것을 추가, 없으면 insert)
    # ─────────────────────────────────────────
    print("\n[positions 병합]")
    vm_positions = vm.execute(
        "SELECT market, symbol, entry_price, quantity, stop_loss, take_profit, strategy, entry_date FROM positions"
    ).fetchall()

    pos_added = 0
    pos_updated = 0
    for p in vm_positions:
        market, symbol = p[0], p[1]
        existing_pos = merged.execute(
            "SELECT id FROM positions WHERE market=? AND symbol=?", (market, symbol)
        ).fetchone()

        if existing_pos:
            merged.execute("""
                UPDATE positions SET entry_price=?, quantity=?, stop_loss=?, take_profit=?,
                strategy=?, entry_date=? WHERE market=? AND symbol=?
            """, (p[2], p[3], p[4], p[5], p[6], p[7], market, symbol))
            pos_updated += 1
            print(f"  🔄 {market} {symbol} (업데이트)")
        else:
            merged.execute("""
                INSERT INTO positions (market, symbol, entry_price, quantity, stop_loss, take_profit, strategy, entry_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, p)
            pos_added += 1
            print(f"  ➕ {market} {symbol} (추가)")

    print(f"  추가: {pos_added}개 / 업데이트: {pos_updated}개")

    merged.commit()
    merged.close()
    vm.close()

    # ─────────────────────────────────────────
    # 최종 통계
    # ─────────────────────────────────────────
    result = sqlite3.connect(MERGED_DB)
    total_trades    = result.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    total_positions = result.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    result.close()

    print(f"\n{'='*50}")
    print(f"✅ 병합 완료!")
    print(f"   총 거래 내역: {total_trades}건")
    print(f"   총 포지션   : {total_positions}개")
    print(f"   저장 위치   : {MERGED_DB}")
    print(f"\n다음 단계:")
    print(f"  1. 병합 DB를 메인 DB로 교체:")
    print(f"     copy db\\trading_merged.db db\\trading.db")
    print(f"  2. VM으로 업로드:")
    print(f"     scp d:\\ai_stock\\db\\trading_merged.db namkyu@172.16.1.15:~/ai_stack/db/trading.db")


if __name__ == "__main__":
    merge()
