"""SQLite database operations for Ampelmann."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from ampelmann.models import CheckRun, CheckState, CheckStatus


# Register datetime adapter/converter (required for Python 3.12+)
def _adapt_datetime(val: datetime) -> str:
    """Convert datetime to ISO format string for storage."""
    return val.isoformat()


def _convert_datetime(val: bytes) -> datetime:
    """Convert ISO format string back to datetime."""
    return datetime.fromisoformat(val.decode())


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_converter("timestamp", _convert_datetime)

SCHEMA = """
CREATE TABLE IF NOT EXISTS check_runs (
    id INTEGER PRIMARY KEY,
    check_name TEXT NOT NULL,
    run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    command_output TEXT,
    command_exit_code INTEGER,
    command_duration_ms INTEGER,
    llm_model TEXT,
    llm_response TEXT,
    llm_duration_ms INTEGER,
    status TEXT CHECK(status IN ('ok', 'alert', 'error')),
    alert_sent BOOLEAN DEFAULT 0,
    alert_message TEXT
);

CREATE TABLE IF NOT EXISTS check_state (
    check_name TEXT PRIMARY KEY,
    last_run_at TIMESTAMP,
    last_status TEXT,
    config_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_check_runs_name ON check_runs(check_name);
CREATE INDEX IF NOT EXISTS idx_check_runs_run_at ON check_runs(run_at);
CREATE INDEX IF NOT EXISTS idx_check_runs_status ON check_runs(status);
"""


class Database:
    """SQLite database wrapper for Ampelmann."""

    def __init__(self, path: Path) -> None:
        """Initialize database connection.

        Args:
            path: Path to SQLite database file.
        """
        self.path = path
        self._ensure_parent_dir()

    def _ensure_parent_dir(self) -> None:
        """Ensure the parent directory exists."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Context manager for database connections."""
        conn = sqlite3.connect(
            self.path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Initialize database schema."""
        with self.connection() as conn:
            conn.executescript(SCHEMA)

    def save_run(self, run: CheckRun) -> int:
        """Save a check run to the database.

        Args:
            run: CheckRun to save.

        Returns:
            ID of the inserted row.
        """
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO check_runs (
                    check_name, run_at, command_output, command_exit_code,
                    command_duration_ms, llm_model, llm_response, llm_duration_ms,
                    status, alert_sent, alert_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.check_name,
                    run.run_at,
                    run.command_output,
                    run.command_exit_code,
                    run.command_duration_ms,
                    run.llm_model,
                    run.llm_response,
                    run.llm_duration_ms,
                    run.status.value,
                    run.alert_sent,
                    run.alert_message,
                ),
            )
            return cursor.lastrowid or 0

    def get_runs(
        self,
        check_name: str | None = None,
        status: CheckStatus | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[CheckRun]:
        """Get check runs with optional filtering.

        Args:
            check_name: Filter by check name.
            status: Filter by status.
            since: Filter runs after this time.
            limit: Maximum number of results.

        Returns:
            List of CheckRun objects.
        """
        query = "SELECT * FROM check_runs WHERE 1=1"
        params: list[str | datetime] = []

        if check_name:
            query += " AND check_name = ?"
            params.append(check_name)

        if status:
            query += " AND status = ?"
            params.append(status.value)

        if since:
            query += " AND run_at >= ?"
            params.append(since)

        query += " ORDER BY run_at DESC LIMIT ?"
        params.append(str(limit))

        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()

        return [self._row_to_run(row) for row in rows]

    def get_latest_run(self, check_name: str) -> CheckRun | None:
        """Get the most recent run for a check.

        Args:
            check_name: Name of the check.

        Returns:
            Most recent CheckRun or None.
        """
        runs = self.get_runs(check_name=check_name, limit=1)
        return runs[0] if runs else None

    def get_state(self, check_name: str) -> CheckState | None:
        """Get the current state for a check.

        Args:
            check_name: Name of the check.

        Returns:
            CheckState or None if not found.
        """
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM check_state WHERE check_name = ?",
                (check_name,),
            ).fetchone()

        if not row:
            return None

        return CheckState(
            check_name=row["check_name"],
            last_run_at=row["last_run_at"],
            last_status=CheckStatus(row["last_status"]) if row["last_status"] else None,
            config_hash=row["config_hash"],
        )

    def update_state(self, state: CheckState) -> None:
        """Update or insert check state.

        Args:
            state: CheckState to save.
        """
        with self.connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO check_state
                    (check_name, last_run_at, last_status, config_hash)
                VALUES (?, ?, ?, ?)
                """,
                (
                    state.check_name,
                    state.last_run_at,
                    state.last_status.value if state.last_status else None,
                    state.config_hash,
                ),
            )

    def cleanup_old_runs(self, retain_days: int) -> int:
        """Delete runs older than the retention period.

        Args:
            retain_days: Number of days to retain.

        Returns:
            Number of deleted rows.
        """
        cutoff = datetime.now() - timedelta(days=retain_days)
        with self.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM check_runs WHERE run_at < ?",
                (cutoff,),
            )
            return cursor.rowcount

    def get_stats(
        self,
        check_name: str | None = None,
        days: int = 7,
    ) -> dict[str, int]:
        """Get statistics for check runs.

        Args:
            check_name: Filter by check name (None for all).
            days: Number of days to include.

        Returns:
            Dictionary with counts by status.
        """
        since = datetime.now() - timedelta(days=days)

        query = """
            SELECT status, COUNT(*) as count
            FROM check_runs
            WHERE run_at >= ?
        """
        params: list[str | datetime] = [since]

        if check_name:
            query += " AND check_name = ?"
            params.append(check_name)

        query += " GROUP BY status"

        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()

        stats = {"ok": 0, "alert": 0, "error": 0}
        for row in rows:
            stats[row["status"]] = row["count"]

        return stats

    def _row_to_run(self, row: sqlite3.Row) -> CheckRun:
        """Convert a database row to a CheckRun."""
        return CheckRun(
            id=row["id"],
            check_name=row["check_name"],
            run_at=row["run_at"],
            command_output=row["command_output"],
            command_exit_code=row["command_exit_code"],
            command_duration_ms=row["command_duration_ms"],
            llm_model=row["llm_model"],
            llm_response=row["llm_response"],
            llm_duration_ms=row["llm_duration_ms"],
            status=CheckStatus(row["status"]),
            alert_sent=bool(row["alert_sent"]),
            alert_message=row["alert_message"],
        )
