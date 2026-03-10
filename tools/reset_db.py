"""DB 상태 확인 및 초기화 도구"""
import sys
sys.path.insert(0, ".")
import sqlite3

conn = sqlite3.connect("db/trading.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 테이블 구조 확인
print("=== 테이블 목록 ===")
tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    print(f"\n[{t['name']}]")
    cols = cur.execute(f"PRAGMA table_info({t['name']})").fetchall()
    for c in cols:
        print(f"  {c['name']} ({c['type']})")

print("\n=== 현재 오픈 포지션 ===")
rows = cur.execute("SELECT * FROM positions").fetchall()
if rows:
    for r in rows:
        print(dict(r))
else:
    print("없음")

print("\n=== 최근 거래 10건 ===")
try:
    rows = cur.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 10").fetchall()
    for r in rows:
        print(dict(r))
except Exception as e:
    print(f"조회 실패: {e}")

# 오픈 포지션 초기화
print("\n=== 오픈 포지션 초기화 ===")
deleted = cur.execute("DELETE FROM positions").rowcount
conn.commit()
print(f"  {deleted}개 포지션 삭제 완료")
print("  (Dry Run 가상 거래 기록이므로 실제 손익 없음)")

conn.close()
print("\n완료! 이제 DRY-BUY가 정상 작동합니다.")
