"""
파라미터 버전별 성과 비교 분석
v1 (RSI 55, BB 0.50) : 3/6 19:15 ~ 3/8 21:15
v2 (RSI 45, BB 0.35) : 3/8 21:16 ~ 3/9 00:42
v3 (RSI 35, BB 0.20) : 3/9 00:43 ~
"""
import sys
import re
import sqlite3
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

DB_FILE = Path("db/trading.db")

# 버전 전환 시각 (KST)
VERSION_BOUNDARIES = [
    ("v1 (RSI55·공격)", "2026-03-06 00:00:00", "2026-03-08 21:15:00"),
    ("v2 (RSI45·균형)", "2026-03-08 21:16:00", "2026-03-09 00:42:00"),
    ("v3 (RSI35·최적)", "2026-03-09 00:43:00", "2099-12-31 00:00:00"),
]


def parse_pnl(note: str, amount: float) -> float:
    m = re.search(r"pnl:([+\-\d,.]+)", note or "")
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return float(amount or 0)


def analyze_version(rows_sell: list, rows_buy: list, label: str):
    n_sell = len(rows_sell)
    n_buy  = len(rows_buy)

    if n_sell == 0:
        return {
            "label": label, "buys": n_buy, "sells": 0,
            "wins": 0, "losses": 0, "win_rate": 0,
            "total_pnl": 0, "avg_pnl": 0,
            "avg_win": 0, "avg_loss": 0, "risk_reward": 0,
            "pnl_list": []
        }

    pnl_list = [parse_pnl(r["note"], r["amount"]) for r in rows_sell]
    wins   = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]

    total_pnl  = sum(pnl_list)
    avg_pnl    = total_pnl / n_sell
    avg_win    = sum(wins)  / len(wins)   if wins   else 0
    avg_loss   = sum(losses) / len(losses) if losses else 0
    win_rate   = len(wins) / n_sell
    risk_reward = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    return {
        "label":       label,
        "buys":        n_buy,
        "sells":       n_sell,
        "wins":        len(wins),
        "losses":      len(losses),
        "win_rate":    win_rate,
        "total_pnl":   total_pnl,
        "avg_pnl":     avg_pnl,
        "avg_win":     avg_win,
        "avg_loss":    avg_loss,
        "risk_reward": risk_reward,
        "pnl_list":    pnl_list,
    }


def main():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    all_trades = conn.execute("SELECT * FROM trades ORDER BY datetime").fetchall()
    all_trades = [dict(r) for r in all_trades]
    conn.close()

    # 버전별 분류
    version_data = {}
    for label, start, end in VERSION_BOUNDARIES:
        sells = [t for t in all_trades
                 if "SELL" in t["side"] and start <= t["datetime"] <= end]
        buys  = [t for t in all_trades
                 if "BUY"  in t["side"] and start <= t["datetime"] <= end]
        version_data[label] = analyze_version(sells, buys, label)

    # ── 버전별 상세 거래 내역 ──
    for label, start, end in VERSION_BOUNDARIES:
        trades_in = [t for t in all_trades if start <= t["datetime"] <= end]
        if not trades_in:
            continue
        print(f"\n{'='*65}")
        print(f"  {label}")
        print(f"{'='*65}")
        print(f"  {'날짜시간':<20} {'구분':<10} {'종목':<14} {'손익':>10}")
        print(f"  {'-'*20} {'-'*10} {'-'*14} {'-'*10}")
        for t in trades_in:
            side = t["side"]
            if "SELL" in side:
                pnl  = parse_pnl(t["note"], t["amount"])
                sign = "+" if pnl >= 0 else ""
                print(f"  {t['datetime']:<20} {side:<10} {t['symbol']:<14} {sign}{pnl:>9,.0f}원")
            else:
                print(f"  {t['datetime']:<20} {side:<10} {t['symbol']:<14} {'매수':>10}")

    # ── 버전 비교표 ──
    print(f"\n\n{'='*75}")
    print(f"  버전별 성과 비교")
    print(f"{'='*75}")
    print(f"  {'버전':<18} {'매수':>5} {'매도':>5} {'승률':>8} {'총손익':>10} {'평균손익':>9} {'손익비':>7} {'개선여부'}")
    print(f"  {'-'*18} {'-'*5} {'-'*5} {'-'*8} {'-'*10} {'-'*9} {'-'*7} {'-'*8}")

    prev_pnl = None
    for label, _, _ in VERSION_BOUNDARIES:
        d = version_data[label]
        if d["sells"] == 0:
            print(f"  {label:<18} {d['buys']:>5} {'0':>5} {'데이터없음':>8}")
            continue

        rr_str = f"{d['risk_reward']:.2f}" if d['risk_reward'] > 0 else "-"
        if prev_pnl is not None:
            diff   = d["avg_pnl"] - prev_pnl
            flag   = "▲ 개선" if diff > 0 else "▼ 악화" if diff < 0 else "= 동일"
        else:
            flag = "-"
        prev_pnl = d["avg_pnl"]

        print(
            f"  {label:<18} {d['buys']:>5} {d['sells']:>5}"
            f" {d['win_rate']:>8.1%} {d['total_pnl']:>+10,.0f}원"
            f" {d['avg_pnl']:>+9,.0f}원 {rr_str:>7} {flag}"
        )

    # ── 핵심 지표 비교 ──
    print(f"\n{'='*75}")
    print(f"  핵심 지표 상세")
    print(f"{'='*75}")
    for label, _, _ in VERSION_BOUNDARIES:
        d = version_data[label]
        if d["sells"] == 0:
            print(f"\n  [{label}]  매도 데이터 없음 (매수 {d['buys']}건 오픈 중)")
            continue
        print(f"\n  [{label}]")
        print(f"    매수 {d['buys']}건 / 매도 {d['sells']}건")
        print(f"    승률  : {d['win_rate']:.1%}  ({d['wins']}승 {d['losses']}패)")
        print(f"    총손익: {d['total_pnl']:+,.0f}원")
        print(f"    평균  : {d['avg_pnl']:+,.0f}원/건")
        print(f"    평균이익: {d['avg_win']:+,.0f}원  |  평균손실: {d['avg_loss']:+,.0f}원")
        print(f"    손익비: {d['risk_reward']:.2f}  (목표: ≥ 2.0)")
        if d["pnl_list"]:
            best  = max(d["pnl_list"])
            worst = min(d["pnl_list"])
            print(f"    최대이익: {best:+,.0f}원  |  최대손실: {worst:+,.0f}원")

    # ── 요약 판정 ──
    print(f"\n{'='*75}")
    print(f"  종합 판정")
    print(f"{'='*75}")

    v1 = version_data.get("v1 (RSI55·공격)")
    v2 = version_data.get("v2 (RSI45·균형)")
    v3 = version_data.get("v3 (RSI35·최적)")

    if v1 and v1["sells"] > 0 and v2 and v2["sells"] > 0:
        if v2["win_rate"] > v1["win_rate"]:
            print(f"  ✓ v1→v2: 승률 {v1['win_rate']:.1%} → {v2['win_rate']:.1%}  개선")
        else:
            print(f"  ✗ v1→v2: 승률 {v1['win_rate']:.1%} → {v2['win_rate']:.1%}  개선 필요")

    if v3 and v3["sells"] == 0:
        print(f"  ℹ v3 (RSI35 최적): 아직 매도 데이터 없음 — 계속 수집 중")
        print(f"    현재 매수 {v3['buys']}건 보유 중, 익절/손절 시 비교 가능")

    print(f"\n  가상잔고: ", end="")
    try:
        import json
        state = json.loads(Path("db/virtual_state.json").read_text(encoding="utf-8"))
        vkrw = float(state["virtual_krw"])
        print(f"{vkrw:,.0f}원  (초기 100,000원 대비 {(vkrw-100000)/100000*100:+.2f}%)")
    except Exception:
        print("확인 불가")


if __name__ == "__main__":
    main()
