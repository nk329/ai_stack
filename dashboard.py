"""
AI 자동매매 웹 대시보드
- Flask 기반 / 로그인 보호
- 누적 손익 차트, 거래 내역, 오픈 포지션
- 새로고침 버튼으로 최신 데이터 반영
"""
import os
import re
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import (Flask, render_template_string, redirect,
                   url_for, request, session, jsonify)
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("DASHBOARD_SECRET_KEY", "ai_stock_secret")

DB_FILE         = Path("db/trading.db")
STATE_FILE      = Path("db/virtual_state.json")
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", 100000))
DASH_USER       = os.getenv("DASHBOARD_USER", "admin")
DASH_PASS       = os.getenv("DASHBOARD_PASSWORD", "password")

# ─────────────────────────────────────────
# 로그인 필요 데코레이터
# ─────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────
# 데이터 조회 함수
# ─────────────────────────────────────────
def get_trades():
    if not DB_FILE.exists():
        return []
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM trades ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_positions():
    if not DB_FILE.exists():
        return []
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM positions ORDER BY entry_date DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def parse_pnl(note: str, amount: float) -> float:
    m = re.search(r"pnl:([+\-\d,.]+)", note or "")
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return 0.0


def get_usd_krw() -> float:
    """실시간 USD/KRW 환율 (yfinance, 실패 시 1450 기본값)"""
    try:
        import yfinance as yf
        rate = yf.Ticker("KRW=X").fast_info["last_price"]
        if rate and 900 < rate < 2000:
            return float(rate)
    except Exception:
        pass
    return 1450.0


def get_invested(positions) -> float:
    """오픈 포지션의 총 투자금액 계산 (미국주식은 실시간 환율로 원화 환산)"""
    usd_krw = get_usd_krw()
    total = 0.0
    for p in positions:
        try:
            value = float(p["entry_price"]) * float(p["quantity"])
            if p.get("market") == "US":
                value *= usd_krw   # 실시간 환율 적용
            total += value
        except Exception:
            pass
    return total


def get_summary(trades, positions=None):
    sells    = [t for t in trades if "SELL" in t["side"]]
    buys     = [t for t in trades if "BUY"  in t["side"]]
    pnl_list = [parse_pnl(t["note"], t["amount"]) for t in sells]

    total_pnl = sum(pnl_list)                          # 실현 손익만
    wins      = [p for p in pnl_list if p > 0]
    losses    = [p for p in pnl_list if p < 0]
    win_rate  = len(wins) / len(sells) * 100 if sells else 0

    # 가용 잔고: virtual_state.json 우선 (scheduler가 실시간 업데이트)
    available_krw = INITIAL_CAPITAL + total_pnl
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            available_krw = float(data.get("virtual_krw", available_krw))
    except Exception:
        pass

    # 투자 중 금액: 오픈 포지션 진입가 × 수량
    invested = get_invested(positions or [])

    # 총 자산 = 가용 잔고 + 투자 중 금액
    total_asset = available_krw + invested

    # 수익률 = 실현손익 / 초기자본 (투자 중 금액은 손실 아님)
    pct = total_pnl / INITIAL_CAPITAL * 100

    # 날짜별 누적 실현 손익 (차트용)
    daily = {}
    for t in sorted(trades, key=lambda x: x["datetime"]):
        if "SELL" in t["side"]:
            date = t["datetime"][:10]
            daily[date] = daily.get(date, 0) + parse_pnl(t["note"], t["amount"])

    cum = 0.0
    chart_dates, chart_values = [], []
    for date in sorted(daily):
        cum += daily[date]
        chart_dates.append(date[5:])   # MM-DD
        chart_values.append(round(cum, 2))

    return {
        "vkrw":          available_krw,   # 가용 잔고 (현금)
        "invested":      invested,         # 투자 중 금액
        "total_asset":   total_asset,      # 총 자산
        "total_pnl":     total_pnl,        # 실현 손익
        "pct":           pct,              # 실현 수익률
        "n_buy":         len(buys),
        "n_sell":        len(sells),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      win_rate,
        "avg_win":       sum(wins)   / len(wins)   if wins   else 0,
        "avg_loss":      sum(losses) / len(losses) if losses else 0,
        "chart_dates":   chart_dates,
        "chart_values":  chart_values,
    }


# ─────────────────────────────────────────
# HTML 템플릿
# ─────────────────────────────────────────
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>AI 자동매매 — 로그인</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #f1f5f9;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .card { background: #fff; border-radius: 16px; padding: 40px 36px;
          box-shadow: 0 4px 24px rgba(0,0,0,.08); width: 360px; }
  h1 { font-size: 1.4rem; font-weight: 700; color: #1e293b; margin-bottom: 6px; text-align: center; }
  .sub { color: #64748b; font-size: .85rem; text-align: center; margin-bottom: 28px; }
  label { display: block; font-size: .82rem; font-weight: 600; color: #475569; margin-bottom: 6px; }
  input { width: 100%; padding: 10px 14px; border: 1.5px solid #e2e8f0; border-radius: 8px;
          font-size: .9rem; outline: none; transition: border .2s; margin-bottom: 16px; }
  input:focus { border-color: #6366f1; }
  button { width: 100%; padding: 12px; background: #6366f1; color: #fff;
           border: none; border-radius: 8px; font-size: .95rem; font-weight: 600;
           cursor: pointer; transition: background .2s; }
  button:hover { background: #4f46e5; }
  .error { color: #ef4444; font-size: .82rem; margin-bottom: 12px; text-align: center; }
</style>
</head>
<body>
<div class="card">
  <h1>🤖 AI 자동매매</h1>
  <p class="sub">대시보드 로그인</p>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  <form method="post">
    <label>아이디</label>
    <input type="text" name="username" placeholder="아이디 입력" required autofocus>
    <label>비밀번호</label>
    <input type="password" name="password" placeholder="비밀번호 입력" required>
    <button type="submit">로그인</button>
  </form>
</div>
</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 자동매매 대시보드</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #f8fafc; color: #1e293b; }

  /* 헤더 */
  header { background: #fff; border-bottom: 1px solid #e2e8f0;
           padding: 16px 32px; display: flex; align-items: center; justify-content: space-between; }
  .logo  { font-size: 1.1rem; font-weight: 700; color: #1e293b; }
  .logo span { color: #6366f1; }
  .header-right { display: flex; align-items: center; gap: 16px; }
  .updated { font-size: .8rem; color: #94a3b8; }
  .btn-refresh { padding: 8px 20px; background: #6366f1; color: #fff; border: none;
                 border-radius: 8px; font-size: .85rem; font-weight: 600; cursor: pointer;
                 transition: background .2s; }
  .btn-refresh:hover { background: #4f46e5; }
  .btn-logout { padding: 8px 16px; background: #f1f5f9; color: #64748b; border: none;
                border-radius: 8px; font-size: .82rem; cursor: pointer; }
  .btn-logout:hover { background: #e2e8f0; }

  /* 레이아웃 */
  main { max-width: 1200px; margin: 0 auto; padding: 28px 24px; }
  h2 { font-size: .8rem; font-weight: 600; color: #94a3b8; text-transform: uppercase;
       letter-spacing: .06em; margin: 28px 0 14px; }

  /* 카드 그리드 */
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; }
  .card  { background: #fff; border-radius: 12px; padding: 20px;
           border: 1px solid #e2e8f0; box-shadow: 0 1px 4px rgba(0,0,0,.04); }
  .card-label { font-size: .72rem; color: #94a3b8; text-transform: uppercase;
                letter-spacing: .05em; margin-bottom: 8px; font-weight: 600; }
  .card-value { font-size: 1.7rem; font-weight: 700; color: #1e293b; }
  .card-sub   { font-size: .78rem; color: #94a3b8; margin-top: 4px; }
  .green { color: #22c55e; }
  .red   { color: #ef4444; }
  .purple{ color: #6366f1; }

  /* 차트 */
  .chart-box { background: #fff; border-radius: 12px; padding: 24px;
               border: 1px solid #e2e8f0; box-shadow: 0 1px 4px rgba(0,0,0,.04); }
  .chart-title { font-size: .9rem; font-weight: 600; color: #1e293b; margin-bottom: 16px; }
  canvas { max-height: 260px; }

  /* 테이블 */
  .table-box { background: #fff; border-radius: 12px; border: 1px solid #e2e8f0;
               box-shadow: 0 1px 4px rgba(0,0,0,.04); overflow: hidden; }
  table { width: 100%; border-collapse: collapse; font-size: .84rem; }
  thead th { background: #f8fafc; padding: 12px 16px; text-align: left;
             color: #64748b; font-weight: 600; font-size: .78rem;
             text-transform: uppercase; letter-spacing: .04em;
             border-bottom: 1px solid #e2e8f0; }
  tbody td { padding: 11px 16px; border-bottom: 1px solid #f1f5f9; color: #334155; }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr:hover td { background: #f8fafc; }

  .badge { display: inline-block; padding: 3px 10px; border-radius: 99px;
           font-size: .72rem; font-weight: 700; }
  .badge-buy  { background: #eff6ff; color: #3b82f6; }
  .badge-sell { background: #fef2f2; color: #ef4444; }
  .badge-win  { background: #f0fdf4; color: #22c55e; }
  .badge-hold { background: #f5f3ff; color: #8b5cf6; }

  .pnl-pos { color: #22c55e; font-weight: 700; }
  .pnl-neg { color: #ef4444; font-weight: 700; }
  .no-data { text-align: center; color: #94a3b8; padding: 32px; font-size: .88rem; }
</style>
</head>
<body>

<!-- 헤더 -->
<header>
  <div class="logo">🤖 <span>AI</span> 자동매매 대시보드</div>
  <div class="header-right">
    <span class="updated">마지막 갱신: {{ now }}</span>
    <button class="btn-refresh" onclick="location.reload()">🔄 새로고침</button>
    <a href="/logout"><button class="btn-logout">로그아웃</button></a>
  </div>
</header>

<main>

  <!-- 요약 카드 -->
  <h2>📊 전체 요약</h2>
  <div class="cards">
    <div class="card">
      <div class="card-label">총 자산</div>
      <div class="card-value purple">{{ "{:,.0f}".format(s.total_asset) }}원</div>
      <div class="card-sub">초기 {{ "{:,.0f}".format(initial) }}원</div>
    </div>
    <div class="card">
      <div class="card-label">가용 잔고 (현금)</div>
      <div class="card-value" style="color:#1e293b">{{ "{:,.0f}".format(s.vkrw) }}원</div>
      <div class="card-sub">투자중 {{ "{:,.0f}".format(s.invested) }}원</div>
    </div>
    <div class="card">
      <div class="card-label">실현 손익</div>
      <div class="card-value {{ 'green' if s.total_pnl >= 0 else 'red' }}">
        {{ "+" if s.total_pnl >= 0 else "" }}{{ "{:,.0f}".format(s.total_pnl) }}원
      </div>
      <div class="card-sub">수익률 {{ "+" if s.pct >= 0 else "" }}{{ "{:.2f}".format(s.pct) }}%</div>
    </div>
    <div class="card">
      <div class="card-label">승률</div>
      <div class="card-value {{ 'green' if s.win_rate >= 50 else 'red' }}">{{ "{:.1f}".format(s.win_rate) }}%</div>
      <div class="card-sub">{{ s.wins }}승 {{ s.losses }}패</div>
    </div>
    <div class="card">
      <div class="card-label">완료 거래</div>
      <div class="card-value purple">{{ s.n_sell }}<span style="font-size:1rem;color:#94a3b8">건</span></div>
      <div class="card-sub">매수 {{ s.n_buy }}건 | 보유 {{ positions|length }}개</div>
    </div>
    <div class="card">
      <div class="card-label">평균 이익</div>
      <div class="card-value green">+{{ "{:,.0f}".format(s.avg_win) }}원</div>
      <div class="card-sub">이익 거래 기준</div>
    </div>
  </div>

  <!-- 누적 손익 차트 -->
  {% if s.chart_dates %}
  <h2>📈 누적 손익 흐름</h2>
  <div class="chart-box">
    <div class="chart-title">날짜별 누적 손익 (원)</div>
    <canvas id="pnlChart"></canvas>
  </div>
  {% endif %}

  <!-- 오픈 포지션 -->
  <h2>🔓 오픈 포지션 ({{ positions|length }}개)</h2>
  <div class="table-box">
    {% if positions %}
    <table>
      <thead>
        <tr><th>시장</th><th>종목</th><th>진입가</th><th>수량</th><th>손절가</th><th>익절가</th><th>진입일시</th></tr>
      </thead>
      <tbody>
        {% for p in positions %}
        <tr>
          <td><span class="badge badge-hold">{{ p.market }}</span></td>
          <td><strong>{{ p.symbol }}</strong></td>
          <td>{{ "{:,.2f}".format(p.entry_price|float) }}</td>
          <td>{{ "{:.4f}".format(p.quantity|float) }}</td>
          <td class="pnl-neg">{{ "{:,.2f}".format(p.stop_loss|float if p.stop_loss else 0) }}</td>
          <td class="pnl-pos">{{ "{:,.2f}".format(p.take_profit|float if p.take_profit else 0) }}</td>
          <td style="color:#94a3b8;font-size:.78rem">{{ p.entry_date }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <p class="no-data">현재 보유 중인 포지션이 없습니다.</p>
    {% endif %}
  </div>

  <!-- 거래 내역 -->
  <h2>📋 거래 내역 (최근 50건)</h2>
  <div class="table-box">
    {% if trades %}
    <table>
      <thead>
        <tr><th>날짜시간</th><th>구분</th><th>시장</th><th>종목</th><th>가격</th><th>손익</th><th>메모</th></tr>
      </thead>
      <tbody>
        {% for t in trades[:50] %}
        {% set pnl = t.note | pnl_from_note %}
        <tr>
          <td style="font-size:.8rem;color:#64748b">{{ t.datetime }}</td>
          <td>
            {% if "SELL" in t.side %}
              <span class="badge {{ 'badge-win' if pnl > 0 else 'badge-sell' }}">{{ t.side }}</span>
            {% else %}
              <span class="badge badge-buy">{{ t.side }}</span>
            {% endif %}
          </td>
          <td>{{ t.market }}</td>
          <td><strong>{{ t.symbol }}</strong></td>
          <td>{{ "{:,.2f}".format(t.price|float) }}</td>
          <td>
            {% if "SELL" in t.side %}
              <span class="{{ 'pnl-pos' if pnl >= 0 else 'pnl-neg' }}">
                {{ "+" if pnl >= 0 else "" }}{{ "{:,.0f}".format(pnl) }}원
              </span>
            {% else %}
              <span style="color:#94a3b8">-</span>
            {% endif %}
          </td>
          <td style="color:#94a3b8;font-size:.78rem">{{ (t.note or '')[:40] }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <p class="no-data">거래 내역이 없습니다.</p>
    {% endif %}
  </div>

</main>

{% if s.chart_dates %}
<script>
const ctx = document.getElementById('pnlChart').getContext('2d');
const labels = {{ s.chart_dates | tojson }};
const values = {{ s.chart_values | tojson }};
const colors = values.map(v => v >= 0 ? 'rgba(34,197,94,0.8)' : 'rgba(239,68,68,0.8)');
const borderColors = values.map(v => v >= 0 ? '#22c55e' : '#ef4444');

new Chart(ctx, {
  type: 'bar',
  data: {
    labels: labels,
    datasets: [{
      label: '누적 손익 (원)',
      data: values,
      backgroundColor: colors,
      borderColor: borderColors,
      borderWidth: 1.5,
      borderRadius: 4,
    }]
  },
  options: {
    responsive: true,
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: ctx => (ctx.raw >= 0 ? '+' : '') + ctx.raw.toLocaleString() + '원'
        }
      }
    },
    scales: {
      y: {
        grid: { color: '#f1f5f9' },
        ticks: {
          callback: val => (val >= 0 ? '+' : '') + val.toLocaleString() + '원'
        }
      },
      x: { grid: { display: false } }
    }
  }
});
</script>
{% endif %}

</body>
</html>
"""


# ─────────────────────────────────────────
# Jinja2 커스텀 필터
# ─────────────────────────────────────────
@app.template_filter("pnl_from_note")
def pnl_from_note(note):
    return parse_pnl(note, 0)


# ─────────────────────────────────────────
# 라우트
# ─────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if (request.form["username"] == DASH_USER and
                request.form["password"] == DASH_PASS):
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "아이디 또는 비밀번호가 올바르지 않습니다."
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    trades    = get_trades()
    positions = get_positions()
    s         = get_summary(trades, positions)   # positions 전달 → 투자금 계산
    now       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return render_template_string(
        DASHBOARD_HTML,
        trades=trades, positions=positions,
        s=s, now=now, initial=INITIAL_CAPITAL
    )


@app.route("/api/summary")
@login_required
def api_summary():
    trades = get_trades()
    s = get_summary(trades)
    return jsonify(s)


# ─────────────────────────────────────────
# 실행
# ─────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  AI 자동매매 대시보드 시작")
    print(f"  접속 주소: http://0.0.0.0:5000")
    print(f"  로그인 ID: {DASH_USER}")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False)
