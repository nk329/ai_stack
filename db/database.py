"""
SQLite 데이터베이스 모듈
거래 기록, 포지션, 성과 통계 저장 및 조회
"""
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = "db/trading.db"


class TradingDB:
    """거래 기록 데이터베이스"""

    def __init__(self, db_path: str = DB_PATH):
        Path(db_path).parent.mkdir(exist_ok=True)
        self.db_path = db_path
        self._init_tables()
        logger.info(f"DB 초기화 완료: {db_path}")

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def _init_tables(self):
        """테이블 초기화"""
        with self._get_conn() as conn:
            conn.executescript("""
                -- 거래 기록 테이블
                CREATE TABLE IF NOT EXISTS trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    datetime    TEXT NOT NULL,
                    market      TEXT NOT NULL,        -- 'KR', 'US', 'CRYPTO'
                    symbol      TEXT NOT NULL,        -- 종목코드/티커/마켓코드
                    side        TEXT NOT NULL,        -- 'BUY' or 'SELL'
                    price       REAL NOT NULL,
                    quantity    REAL NOT NULL,
                    amount      REAL NOT NULL,        -- 거래금액 (price * quantity)
                    fee         REAL DEFAULT 0,       -- 수수료
                    strategy    TEXT DEFAULT '',      -- 전략명
                    note        TEXT DEFAULT ''
                );

                -- 포지션 테이블 (현재 보유 중인 종목)
                CREATE TABLE IF NOT EXISTS positions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    market       TEXT NOT NULL,
                    symbol       TEXT NOT NULL,
                    entry_price  REAL NOT NULL,       -- 평균 매수가
                    quantity     REAL NOT NULL,       -- 보유 수량
                    entry_date   TEXT NOT NULL,
                    stop_loss    REAL,                -- 손절가
                    take_profit  REAL,                -- 익절가
                    strategy     TEXT DEFAULT '',
                    UNIQUE(market, symbol)
                );

                -- 일별 성과 테이블
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date         TEXT PRIMARY KEY,
                    capital      REAL NOT NULL,       -- 당일 종료 자본금
                    daily_pnl    REAL DEFAULT 0,      -- 당일 손익
                    trade_count  INTEGER DEFAULT 0,   -- 거래 횟수
                    win_count    INTEGER DEFAULT 0,   -- 수익 거래 수
                    lose_count   INTEGER DEFAULT 0    -- 손실 거래 수
                );
            """)

    # ─────────────────────────────────────────
    # 거래 기록
    # ─────────────────────────────────────────
    def record_trade(self, market: str, symbol: str, side: str,
                     price: float, quantity: float, fee: float = 0,
                     strategy: str = "", note: str = "") -> int:
        """거래 기록 저장 + 매도 시 daily_stats 자동 업데이트"""
        amount = price * quantity
        now    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today  = datetime.now().strftime("%Y-%m-%d")

        with self._get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO trades
                   (datetime, market, symbol, side, price, quantity, amount, fee, strategy, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, market, symbol, side, price, quantity, amount, fee, strategy, note)
            )
            trade_id = cur.lastrowid
            logger.info(f"거래 기록: [{side}] {symbol} {quantity} @ {price:,.2f} (ID: {trade_id})")

            # 매도 시 note에서 pnl 파싱해 daily_stats 업데이트
            if "SELL" in side.upper():
                import re
                pnl = 0.0
                m = re.search(r"pnl:([+\-\d,.]+)", note or "")
                if m:
                    try:
                        pnl = float(m.group(1).replace(",", ""))
                    except ValueError:
                        pass
                is_win = 1 if pnl > 0 else 0
                conn.execute(
                    """INSERT INTO daily_stats (date, capital, daily_pnl, trade_count, win_count, lose_count)
                       VALUES (?, 0, ?, 1, ?, ?)
                       ON CONFLICT(date) DO UPDATE SET
                         daily_pnl   = daily_pnl + excluded.daily_pnl,
                         trade_count = trade_count + 1,
                         win_count   = win_count + excluded.win_count,
                         lose_count  = lose_count + excluded.lose_count""",
                    (today, pnl, is_win, 1 - is_win)
                )
            return trade_id

    def get_trades(self, symbol: str = None, limit: int = 50) -> list:
        """거래 기록 조회"""
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE symbol=? ORDER BY datetime DESC LIMIT ?",
                    (symbol, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY datetime DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    # ─────────────────────────────────────────
    # 포지션 관리
    # ─────────────────────────────────────────
    def open_position(self, market: str, symbol: str, entry_price: float,
                      quantity: float, stop_loss: float = None,
                      take_profit: float = None, strategy: str = ""):
        """포지션 오픈 (매수 후 기록)
        - 신규 포지션: INSERT
        - 이미 존재하면 수량/손절/익절만 업데이트 (진입가·진입일 보존)
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM positions WHERE market=? AND symbol=?",
                (market, symbol)
            ).fetchone()
            if existing:
                # 추가매수: 기존 진입가·진입일 유지, 수량/손절/익절만 갱신
                conn.execute(
                    """UPDATE positions
                       SET quantity=quantity+?, stop_loss=?, take_profit=?
                       WHERE market=? AND symbol=?""",
                    (quantity, stop_loss, take_profit, market, symbol)
                )
                logger.info(f"포지션 추가: {symbol} +{quantity} (진입가·날짜 유지)")
            else:
                conn.execute(
                    """INSERT INTO positions
                       (market, symbol, entry_price, quantity, entry_date, stop_loss, take_profit, strategy)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (market, symbol, entry_price, quantity, now, stop_loss, take_profit, strategy)
                )
                logger.info(f"포지션 오픈: {symbol} {quantity} @ {entry_price:,.2f}")

    def close_position(self, market: str, symbol: str) -> dict:
        """포지션 클로즈 (매도 후 삭제)"""
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            pos = conn.execute(
                "SELECT * FROM positions WHERE market=? AND symbol=?",
                (market, symbol)
            ).fetchone()
            if pos:
                conn.execute(
                    "DELETE FROM positions WHERE market=? AND symbol=?",
                    (market, symbol)
                )
                logger.info(f"포지션 클로즈: {symbol}")
                return dict(pos)
            return {}

    def get_positions(self) -> list:
        """현재 보유 포지션 전체 조회"""
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM positions").fetchall()
            return [dict(r) for r in rows]

    def get_position(self, market: str, symbol: str) -> dict:
        """특정 종목 포지션 조회"""
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM positions WHERE market=? AND symbol=?",
                (market, symbol)
            ).fetchone()
            return dict(row) if row else {}

    # ─────────────────────────────────────────
    # 성과 통계
    # ─────────────────────────────────────────
    def save_daily_stats(self, capital: float, daily_pnl: float,
                         trade_count: int, win_count: int, lose_count: int):
        """일별 성과 저장"""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO daily_stats
                   (date, capital, daily_pnl, trade_count, win_count, lose_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (today, capital, daily_pnl, trade_count, win_count, lose_count)
            )

    def get_performance_summary(self) -> dict:
        """전체 성과 요약"""
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row

            # 전체 거래 수
            total = conn.execute("SELECT COUNT(*) as cnt FROM trades").fetchone()["cnt"]

            # 완결된 매도 거래에서 수익/손실 계산
            stats = conn.execute("""
                SELECT
                    COUNT(*) as trade_count,
                    SUM(CASE WHEN side='SELL' THEN amount ELSE 0 END) as total_sell
                FROM trades
            """).fetchone()

            # 일별 통계
            daily = conn.execute("""
                SELECT
                    SUM(daily_pnl) as total_pnl,
                    COUNT(*) as trading_days,
                    AVG(daily_pnl) as avg_daily_pnl
                FROM daily_stats
            """).fetchone()

            return {
                "total_trades": total,
                "total_pnl": daily["total_pnl"] or 0,
                "trading_days": daily["trading_days"] or 0,
                "avg_daily_pnl": daily["avg_daily_pnl"] or 0,
            }

    def print_summary(self):
        """성과 요약 출력"""
        summary = self.get_performance_summary()
        positions = self.get_positions()
        recent_trades = self.get_trades(limit=5)

        print("\n── 거래 현황 ────────────────────────")
        print(f"  총 거래 횟수  : {summary['total_trades']}회")
        print(f"  총 손익       : {summary['total_pnl']:>+12,.0f} 원")
        print(f"  평균 일손익   : {summary['avg_daily_pnl']:>+12,.0f} 원")
        print(f"  보유 포지션   : {len(positions)}개")

        if positions:
            print("\n  [보유 포지션]")
            for p in positions:
                print(f"    {p['symbol']:10} | 매수가: {p['entry_price']:>12,.2f} | 수량: {p['quantity']}")

        if recent_trades:
            print("\n  [최근 거래 5건]")
            for t in recent_trades:
                print(f"    {t['datetime'][:16]} | {t['side']:4} | {t['symbol']:10} | {t['amount']:>12,.0f}원")
        print("─────────────────────────────────────")


if __name__ == "__main__":
    print("\nDB 모듈 테스트")
    db = TradingDB()

    # 테스트 거래 기록
    db.record_trade("CRYPTO", "KRW-BTC", "BUY", 103_000_000, 0.001, fee=103, strategy="RSI")
    db.open_position("CRYPTO", "KRW-BTC", 103_000_000, 0.001, stop_loss=99_910_000, take_profit=110_210_000)

    db.print_summary()
    print("✅ DB 테스트 완료!")
