"""
이해하기 쉬운 HTML 성과 리포트 생성기
날짜별 거래 내역 + 버전별 수익률 비교
"""
import sys
import re
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, ".")

DB_FILE    = Path("db/trading.db")
STATE_FILE = Path("db/virtual_state.json")
OUT_FILE   = Path("DRY_RUN_REPORT.html")

INITIAL_CAPITAL = 100_000

# 버전 경계
VERSIONS = [
    {
        "id":    "v1",
        "label": "v1 — 공격 모드",
        "desc":  "RSI 55 / BB 0.50 / 손절 3%",
        "start": "2026-03-06 00:00:00",
        "end":   "2026-03-08 21:15:59",
        "color": "#ef4444",   # 빨강
    },
    {
        "id":    "v2",
        "label": "v2 — 균형 모드",
        "desc":  "RSI 45 / BB 0.35 / 손절 5%",
        "start": "2026-03-08 21:16:00",
        "end":   "2026-03-09 00:42:59",
        "color": "#f59e0b",   # 노랑
    },
    {
        "id":    "v3",
        "label": "v3 — 최적 모드",
        "desc":  "RSI 35 / BB 0.20 / 손절 5% (백테스트 기반)",
        "start": "2026-03-09 00:43:00",
        "end":   "2099-12-31 00:00:00",
        "color": "#22c55e",   # 초록
    },
]


def parse_pnl(note: str, amount: float) -> float:
    m = re.search(r"pnl:([+\-\d,.]+)", note or "")
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return float(amount or 0)


def get_version(dt_str: str) -> dict:
    for v in VERSIONS:
        if v["start"] <= dt_str <= v["end"]:
            return v
    return VERSIONS[-1]


def load_data():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    trades    = [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY datetime").fetchall()]
    positions = [dict(r) for r in conn.execute("SELECT * FROM positions ORDER BY entry_date").fetchall()]
    conn.close()
    return trades, positions


def compute_version_stats(trades):
    stats = {v["id"]: {"buys": [], "sells": [], "pnls": [], "ver": v} for v in VERSIONS}

    for t in trades:
        v   = get_version(t["datetime"])
        vid = v["id"]
        if "SELL" in t["side"]:
            pnl = parse_pnl(t["note"], t["amount"])
            stats[vid]["sells"].append(t)
            stats[vid]["pnls"].append(pnl)
        elif "BUY" in t["side"]:
            stats[vid]["buys"].append(t)

    results = []
    cumulative = 0.0
    for v in VERSIONS:
        vid   = v["id"]
        d     = stats[vid]
        pnls  = d["pnls"]
        n     = len(pnls)
        wins  = [p for p in pnls if p > 0]
        loss  = [p for p in pnls if p <= 0]
        total = sum(pnls)
        cumulative += total
        results.append({
            "ver":       v,
            "n_buy":     len(d["buys"]),
            "n_sell":    n,
            "wins":      len(wins),
            "losses":    len(loss),
            "win_rate":  len(wins)/n if n else None,
            "total_pnl": total,
            "avg_pnl":   total/n if n else None,
            "avg_win":   sum(wins)/len(wins) if wins else 0,
            "avg_loss":  sum(loss)/len(loss) if loss else 0,
            "rr":        abs(sum(wins)/len(wins) / (sum(loss)/len(loss))) if wins and loss else None,
            "cumulative": cumulative,
            "pnls":      pnls,
        })
    return results


def group_by_date(trades):
    by_date = defaultdict(list)
    for t in trades:
        date = t["datetime"][:10]
        by_date[date].append(t)
    return dict(sorted(by_date.items()))


def pnl_bar(pnl: float, scale: float = 50) -> str:
    """간단한 수평 막대 HTML"""
    if pnl >= 0:
        w = min(int(abs(pnl) / scale * 100), 100)
        return f'<div class="bar-pos" style="width:{w}%"></div>'
    else:
        w = min(int(abs(pnl) / scale * 100), 100)
        return f'<div class="bar-neg" style="width:{w}%"></div>'


def generate_html(trades, positions, ver_stats):
    # 가상잔고
    vkrw = INITIAL_CAPITAL
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        vkrw = float(s["virtual_krw"])
        updated = s.get("updated_at", "")
    except Exception:
        updated = ""

    total_pnl  = vkrw - INITIAL_CAPITAL
    pct        = total_pnl / INITIAL_CAPITAL * 100
    pct_color  = "#22c55e" if total_pnl >= 0 else "#ef4444"

    by_date = group_by_date(trades)

    # 날짜별 누적 손익 계산 (차트용)
    cum = 0.0
    chart_labels = []
    chart_data   = []
    for date, day_trades in by_date.items():
        for t in day_trades:
            if "SELL" in t["side"]:
                cum += parse_pnl(t["note"], t["amount"])
        chart_labels.append(date[5:])  # MM-DD
        chart_data.append(round(cum, 2))

    # ── HTML 생성 ──
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 자동매매 Dry Run 리포트</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }}
  h1   {{ font-size: 1.6rem; font-weight: 700; margin-bottom: 4px; }}
  h2   {{ font-size: 1.1rem; font-weight: 600; margin: 24px 0 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; }}
  .subtitle {{ color: #64748b; font-size: .85rem; margin-bottom: 28px; }}
  
  /* 카드 */
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 32px; }}
  .card  {{ background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }}
  .card-label {{ font-size: .75rem; color: #64748b; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px; }}
  .card-value {{ font-size: 1.8rem; font-weight: 700; }}
  .card-sub   {{ font-size: .8rem; color: #64748b; margin-top: 4px; }}
  .green {{ color: #22c55e; }}
  .red   {{ color: #ef4444; }}
  .yellow{{ color: #f59e0b; }}
  
  /* 버전 비교 */
  .versions {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-bottom: 32px; }}
  .ver-card {{ background: #1e293b; border-radius: 12px; padding: 20px; border-left: 4px solid; }}
  .ver-title {{ font-size: 1rem; font-weight: 700; margin-bottom: 4px; }}
  .ver-desc  {{ font-size: .8rem; color: #64748b; margin-bottom: 14px; }}
  .ver-row   {{ display: flex; justify-content: space-between; font-size: .85rem; padding: 4px 0; border-bottom: 1px solid #334155; }}
  .ver-row:last-child {{ border-bottom: none; }}
  .ver-key   {{ color: #94a3b8; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: .7rem; font-weight: 700; margin-left: 6px; }}
  .badge-green  {{ background: #14532d; color: #22c55e; }}
  .badge-red    {{ background: #450a0a; color: #ef4444; }}
  .badge-yellow {{ background: #451a03; color: #f59e0b; }}
  .badge-gray   {{ background: #1e293b; color: #94a3b8; border: 1px solid #334155; }}

  /* 차트 (CSS 막대) */
  .mini-chart {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 32px; border: 1px solid #334155; }}
  .chart-bars {{ display: flex; align-items: flex-end; gap: 6px; height: 100px; margin-top: 12px; }}
  .bar-wrap   {{ flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px; }}
  .bar        {{ width: 100%; border-radius: 4px 4px 0 0; min-height: 2px; transition: .3s; }}
  .bar-label  {{ font-size: .65rem; color: #64748b; white-space: nowrap; }}
  .bar-val    {{ font-size: .65rem; font-weight: 600; }}

  /* 날짜별 거래 테이블 */
  .day-section {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 16px; border: 1px solid #334155; }}
  .day-header  {{ display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }}
  .day-date    {{ font-size: 1rem; font-weight: 700; }}
  .day-summary {{ font-size: .8rem; color: #64748b; }}
  table   {{ width: 100%; border-collapse: collapse; font-size: .82rem; }}
  th      {{ text-align: left; color: #64748b; padding: 6px 8px; border-bottom: 1px solid #334155; font-weight: 500; }}
  td      {{ padding: 7px 8px; border-bottom: 1px solid #1e293b; }}
  tr:last-child td {{ border-bottom: none; }}
  .side-buy  {{ color: #60a5fa; font-weight: 600; }}
  .side-sell {{ color: #f87171; font-weight: 600; }}
  .side-win  {{ color: #22c55e; font-weight: 600; }}
  .pnl-pos   {{ color: #22c55e; font-weight: 700; }}
  .pnl-neg   {{ color: #ef4444; font-weight: 700; }}
  .bar-pos {{ height: 8px; background: #22c55e; border-radius: 4px; }}
  .bar-neg {{ height: 8px; background: #ef4444; border-radius: 4px; }}
  .bar-wrap-h {{ width: 80px; background: #334155; border-radius: 4px; overflow: hidden; }}
  
  /* 오픈 포지션 */
  .open-pos {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 32px; border: 1px solid #334155; }}
  
  footer {{ text-align: center; color: #475569; font-size: .8rem; margin-top: 32px; }}
</style>
</head>
<body>

<h1>🤖 AI 자동매매 Dry Run 리포트</h1>
<p class="subtitle">생성일: {datetime.now().strftime('%Y년 %m월 %d일 %H:%M')} &nbsp;|&nbsp; 초기자본: {INITIAL_CAPITAL:,}원 &nbsp;|&nbsp; 모의거래 (실제 돈 아님)</p>

<!-- ── 핵심 요약 카드 ── -->
<h2>📊 전체 요약</h2>
<div class="cards">
  <div class="card">
    <div class="card-label">가상 잔고</div>
    <div class="card-value" style="color:{pct_color}">{vkrw:,.0f}원</div>
    <div class="card-sub">초기 {INITIAL_CAPITAL:,}원</div>
  </div>
  <div class="card">
    <div class="card-label">누적 손익</div>
    <div class="card-value" style="color:{pct_color}">{'+' if total_pnl>=0 else ''}{total_pnl:,.0f}원</div>
    <div class="card-sub">수익률 {'+' if pct>=0 else ''}{pct:.2f}%</div>
  </div>
  <div class="card">
    <div class="card-label">총 거래</div>
    <div class="card-value">{len([t for t in trades if 'SELL' in t['side']])}<span style="font-size:1rem;color:#64748b">건</span></div>
    <div class="card-sub">매수 {len([t for t in trades if 'BUY' in t['side']])}건 포함</div>
  </div>
  <div class="card">
    <div class="card-label">운영 기간</div>
    <div class="card-value" style="font-size:1.2rem">{min(t['datetime'][:10] for t in trades) if trades else '-'}</div>
    <div class="card-sub">~ {max(t['datetime'][:10] for t in trades) if trades else '-'}</div>
  </div>
  <div class="card">
    <div class="card-label">현재 버전</div>
    <div class="card-value" style="font-size:1.2rem;color:#22c55e">v3</div>
    <div class="card-sub">RSI 35 / BB 0.20</div>
  </div>
</div>
"""

    # ── 버전별 비교 ──
    html += '<h2>🔄 파라미터 버전별 성과</h2>\n<div class="versions">\n'
    for s in ver_stats:
        v     = s["ver"]
        n     = s["n_sell"]
        badge = ""
        if n == 0:
            badge = '<span class="badge badge-gray">데이터 수집 중</span>'
        elif s["win_rate"] is not None:
            if s["win_rate"] >= 0.4:
                badge = '<span class="badge badge-green">양호</span>'
            elif s["win_rate"] >= 0.2:
                badge = '<span class="badge badge-yellow">개선 중</span>'
            else:
                badge = '<span class="badge badge-red">부진</span>'

        html += f'<div class="ver-card" style="border-color:{v["color"]}">\n'
        html += f'  <div class="ver-title" style="color:{v["color"]}">{v["label"]}{badge}</div>\n'
        html += f'  <div class="ver-desc">{v["desc"]}</div>\n'

        if n == 0:
            html += f'  <div class="ver-row"><span class="ver-key">매수</span><span>{s["n_buy"]}건 보유 중</span></div>\n'
            html += f'  <div class="ver-row"><span class="ver-key">매도</span><span>아직 없음</span></div>\n'
        else:
            wr_color = "#22c55e" if (s["win_rate"] or 0) >= 0.4 else "#ef4444"
            pnl_color = "#22c55e" if s["total_pnl"] >= 0 else "#ef4444"
            rr_color = "#22c55e" if (s["rr"] or 0) >= 2.0 else "#f59e0b"
            html += f'  <div class="ver-row"><span class="ver-key">매수/매도</span><span>{s["n_buy"]}건 / {n}건</span></div>\n'
            html += f'  <div class="ver-row"><span class="ver-key">승률</span><span style="color:{wr_color};font-weight:700">{(s["win_rate"] or 0)*100:.1f}% ({s["wins"]}승 {s["losses"]}패)</span></div>\n'
            html += f'  <div class="ver-row"><span class="ver-key">총 손익</span><span style="color:{pnl_color};font-weight:700">{"+"+f"{s['total_pnl']:,.0f}" if s["total_pnl"]>=0 else f"{s['total_pnl']:,.0f}"}원</span></div>\n'
            html += f'  <div class="ver-row"><span class="ver-key">평균 손익</span><span style="color:{pnl_color}">{"+"+f"{s['avg_pnl']:,.0f}" if (s["avg_pnl"] or 0)>=0 else f"{s['avg_pnl']:,.0f}"}원/건</span></div>\n'
            if s["rr"] is not None:
                html += f'  <div class="ver-row"><span class="ver-key">손익비</span><span style="color:{rr_color}">{s["rr"]:.2f} <small style="color:#64748b">(목표 ≥2.0)</small></span></div>\n'
        html += '</div>\n'

    html += '</div>\n'

    # ── 누적 손익 흐름 (CSS 막대 차트) ──
    if chart_data:
        max_abs = max(abs(v) for v in chart_data) or 1
        html += '<h2>📈 날짜별 누적 손익 흐름</h2>\n'
        html += '<div class="mini-chart">\n'
        html += '  <div style="font-size:.8rem;color:#64748b">날짜별 누적 손익 (원)</div>\n'
        html += '  <div class="chart-bars">\n'
        for i, (lbl, val) in enumerate(zip(chart_labels, chart_data)):
            bar_h = max(int(abs(val) / max_abs * 90), 2)
            clr   = "#22c55e" if val >= 0 else "#ef4444"
            val_str = f"{'+' if val>=0 else ''}{val:,.0f}"
            html += f'    <div class="bar-wrap">\n'
            html += f'      <div class="bar-val" style="color:{clr};font-size:.6rem">{val_str}</div>\n'
            html += f'      <div class="bar" style="height:{bar_h}px;background:{clr}"></div>\n'
            html += f'      <div class="bar-label">{lbl}</div>\n'
            html += f'    </div>\n'
        html += '  </div>\n</div>\n'

    # ── 날짜별 거래 내역 ──
    html += '<h2>📅 날짜별 거래 내역</h2>\n'
    day_cum = 0.0
    for date, day_trades in by_date.items():
        day_pnl = sum(parse_pnl(t["note"], t["amount"]) for t in day_trades if "SELL" in t["side"])
        day_cum += day_pnl
        day_sells = [t for t in day_trades if "SELL" in t["side"]]
        day_wins  = [t for t in day_sells if parse_pnl(t["note"], t["amount"]) > 0]
        ver       = get_version(day_trades[0]["datetime"])

        pnl_color = "#22c55e" if day_pnl >= 0 else "#ef4444"
        html += f'<div class="day-section">\n'
        html += f'  <div class="day-header">\n'
        html += f'    <span class="day-date">{date}</span>\n'
        html += f'    <span class="badge" style="background:{ver["color"]}22;color:{ver["color"]};border:1px solid {ver["color"]}44">{ver["id"]}</span>\n'
        if day_sells:
            html += f'    <span class="day-summary">매도 {len(day_sells)}건 | 승 {len(day_wins)}/{len(day_sells)} | 당일손익 <strong style="color:{pnl_color}">{"+"+f"{day_pnl:,.0f}" if day_pnl>=0 else f"{day_pnl:,.0f}"}원</strong> | 누적 <strong style="color:{"#22c55e" if day_cum>=0 else "#ef4444"}">{"+"+f"{day_cum:,.0f}" if day_cum>=0 else f"{day_cum:,.0f}"}원</strong></span>\n'
        else:
            html += f'    <span class="day-summary">매수만 있음 (매도 없음)</span>\n'
        html += f'  </div>\n'
        html += f'  <table>\n'
        html += f'    <tr><th>시간</th><th>구분</th><th>종목</th><th>가격</th><th>손익</th><th>메모</th></tr>\n'
        for t in day_trades:
            time_str = t["datetime"][11:16]
            side     = t["side"]
            symbol   = t["symbol"]
            price    = t["price"] or 0
            note     = (t["note"] or "")[:30]

            if "SELL" in side:
                pnl = parse_pnl(t["note"], t["amount"])
                pnl_cls = "pnl-pos" if pnl >= 0 else "pnl-neg"
                pnl_str = f'{"+"+f"{pnl:,.0f}" if pnl>=0 else f"{pnl:,.0f}"}원'
                side_cls = "side-win" if pnl > 0 else "side-sell"
                html += f'    <tr><td>{time_str}</td><td class="{side_cls}">{side}</td><td><strong>{symbol}</strong></td><td>{price:,.0f}</td><td class="{pnl_cls}">{pnl_str}</td><td style="color:#64748b;font-size:.75rem">{note}</td></tr>\n'
            else:
                html += f'    <tr><td>{time_str}</td><td class="side-buy">{side}</td><td><strong>{symbol}</strong></td><td>{price:,.2f}</td><td>-</td><td style="color:#64748b;font-size:.75rem">{note}</td></tr>\n'
        html += f'  </table>\n</div>\n'

    # ── 오픈 포지션 ──
    if positions:
        html += '<h2>🔓 현재 오픈 포지션</h2>\n'
        html += '<div class="open-pos">\n'
        html += '  <table>\n'
        html += '    <tr><th>시장</th><th>종목</th><th>진입가</th><th>수량</th><th>손절가</th><th>익절가</th><th>진입일시</th></tr>\n'
        for p in positions:
            html += f'    <tr><td>{p["market"]}</td><td><strong>{p["symbol"]}</strong></td>'
            html += f'<td>{float(p["entry_price"]):,.2f}</td>'
            html += f'<td>{float(p["quantity"]):.4f}</td>'
            html += f'<td style="color:#ef4444">{float(p["stop_loss"] or 0):,.2f}</td>'
            html += f'<td style="color:#22c55e">{float(p["take_profit"] or 0):,.2f}</td>'
            html += f'<td style="color:#64748b;font-size:.75rem">{p["entry_date"]}</td></tr>\n'
        html += '  </table>\n</div>\n'

    # ── 개선 포인트 ──
    html += '<h2>💡 버전별 문제 & 개선 내용</h2>\n'
    improvements = [
        ("v1 → v2", "RSI 55→45", "같은 종목 반복 매수 (-111, -28, -56원 반복) → 손실 규모 감소"),
        ("v2 → v3", "RSI 45→35, BB 0.50→0.20", "백테스트 기반 최적화. POKT RSI25 → +64.69%, MON RSI35 → +53.39% 기대"),
        ("v2 → v3", "쿨다운 60분 추가", "매도 후 같은 종목 즉시 재매수 차단 (SENT/MON 반복 방지)"),
        ("v3 신규",  "SMCI 버그 수정", "포지션 DB 저장 오류로 매 5분 반복 매수 → 쿨다운 & 에러 로깅 추가"),
    ]
    html += '<div class="day-section">\n<table>\n'
    html += '<tr><th>버전</th><th>변경 내용</th><th>기대 효과</th></tr>\n'
    for ver, change, effect in improvements:
        html += f'<tr><td><strong>{ver}</strong></td><td style="color:#f59e0b">{change}</td><td style="color:#94a3b8">{effect}</td></tr>\n'
    html += '</table>\n</div>\n'

    html += f"""
<footer>
  AI 자동매매 Dry Run 리포트 | 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 실제 거래 아님
</footer>
</body>
</html>
"""
    return html


def main():
    trades, positions = load_data()
    ver_stats = compute_version_stats(trades)
    html = generate_html(trades, positions, ver_stats)
    OUT_FILE.write_text(html, encoding="utf-8")
    print(f"리포트 저장 완료: {OUT_FILE.absolute()}")
    print(f"브라우저에서 열기: {OUT_FILE.absolute()}")


if __name__ == "__main__":
    main()
