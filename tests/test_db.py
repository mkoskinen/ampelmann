"""Tests for ampelmann.db."""

from datetime import datetime, timedelta

from ampelmann.db import Database
from ampelmann.models import CheckRun, CheckState, CheckStatus


class TestDatabase:
    def test_init_creates_parent_dir(self, temp_dir):
        """Database creates parent directory if needed."""
        db_path = temp_dir / "subdir" / "test.db"
        db = Database(db_path)
        db.init_schema()
        assert db_path.exists()

    def test_init_schema(self, test_db):
        """Schema is created successfully."""
        # test_db fixture already calls init_schema
        with test_db.connection() as conn:
            # Check tables exist
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = {t["name"] for t in tables}
            assert "check_runs" in table_names
            assert "check_state" in table_names


class TestCheckRuns:
    def test_save_and_get_run(self, test_db):
        """Save and retrieve a check run."""
        run = CheckRun(
            check_name="test-check",
            run_at=datetime.now(),
            command_output="OK",
            command_exit_code=0,
            command_duration_ms=100,
            status=CheckStatus.OK,
            llm_model="phi4:14b",
            llm_response="OK",
            llm_duration_ms=500,
        )
        run_id = test_db.save_run(run)
        assert run_id > 0

        runs = test_db.get_runs(check_name="test-check")
        assert len(runs) == 1
        assert runs[0].check_name == "test-check"
        assert runs[0].status == CheckStatus.OK
        assert runs[0].id == run_id

    def test_get_runs_filter_by_status(self, test_db):
        """Filter runs by status."""
        now = datetime.now()

        # Create OK run
        test_db.save_run(
            CheckRun(
                check_name="test",
                run_at=now,
                command_output="",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.OK,
            )
        )

        # Create alert run
        test_db.save_run(
            CheckRun(
                check_name="test",
                run_at=now,
                command_output="",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.ALERT,
            )
        )

        ok_runs = test_db.get_runs(status=CheckStatus.OK)
        assert len(ok_runs) == 1
        assert ok_runs[0].status == CheckStatus.OK

        alert_runs = test_db.get_runs(status=CheckStatus.ALERT)
        assert len(alert_runs) == 1
        assert alert_runs[0].status == CheckStatus.ALERT

    def test_get_runs_filter_by_time(self, test_db):
        """Filter runs by time."""
        old_time = datetime.now() - timedelta(days=2)
        new_time = datetime.now()

        test_db.save_run(
            CheckRun(
                check_name="test",
                run_at=old_time,
                command_output="",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.OK,
            )
        )

        test_db.save_run(
            CheckRun(
                check_name="test",
                run_at=new_time,
                command_output="",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.OK,
            )
        )

        since = datetime.now() - timedelta(days=1)
        recent_runs = test_db.get_runs(since=since)
        assert len(recent_runs) == 1

    def test_get_latest_run(self, test_db):
        """Get most recent run for a check."""
        now = datetime.now()

        test_db.save_run(
            CheckRun(
                check_name="test",
                run_at=now - timedelta(hours=1),
                command_output="old",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.OK,
            )
        )

        test_db.save_run(
            CheckRun(
                check_name="test",
                run_at=now,
                command_output="new",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.OK,
            )
        )

        latest = test_db.get_latest_run("test")
        assert latest is not None
        assert latest.command_output == "new"

    def test_get_latest_run_none(self, test_db):
        """Return None when no runs exist."""
        latest = test_db.get_latest_run("nonexistent")
        assert latest is None


class TestCheckState:
    def test_update_and_get_state(self, test_db):
        """Save and retrieve check state."""
        state = CheckState(
            check_name="test",
            last_run_at=datetime.now(),
            last_status=CheckStatus.OK,
            config_hash="abc123",
        )
        test_db.update_state(state)

        retrieved = test_db.get_state("test")
        assert retrieved is not None
        assert retrieved.check_name == "test"
        assert retrieved.last_status == CheckStatus.OK
        assert retrieved.config_hash == "abc123"

    def test_update_state_overwrites(self, test_db):
        """Updating state replaces existing."""
        state1 = CheckState(check_name="test", last_status=CheckStatus.OK)
        test_db.update_state(state1)

        state2 = CheckState(check_name="test", last_status=CheckStatus.ALERT)
        test_db.update_state(state2)

        retrieved = test_db.get_state("test")
        assert retrieved is not None
        assert retrieved.last_status == CheckStatus.ALERT

    def test_get_state_none(self, test_db):
        """Return None for nonexistent state."""
        state = test_db.get_state("nonexistent")
        assert state is None


class TestCleanup:
    def test_cleanup_old_runs(self, test_db):
        """Delete runs older than retention period."""
        old_time = datetime.now() - timedelta(days=100)
        new_time = datetime.now()

        test_db.save_run(
            CheckRun(
                check_name="test",
                run_at=old_time,
                command_output="old",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.OK,
            )
        )

        test_db.save_run(
            CheckRun(
                check_name="test",
                run_at=new_time,
                command_output="new",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.OK,
            )
        )

        deleted = test_db.cleanup_old_runs(retain_days=90)
        assert deleted == 1

        runs = test_db.get_runs()
        assert len(runs) == 1
        assert runs[0].command_output == "new"


class TestStats:
    def test_get_stats(self, test_db):
        """Get statistics by status."""
        now = datetime.now()

        # 2 OK, 1 alert
        for _ in range(2):
            test_db.save_run(
                CheckRun(
                    check_name="test",
                    run_at=now,
                    command_output="",
                    command_exit_code=0,
                    command_duration_ms=100,
                    status=CheckStatus.OK,
                )
            )

        test_db.save_run(
            CheckRun(
                check_name="test",
                run_at=now,
                command_output="",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.ALERT,
            )
        )

        stats = test_db.get_stats()
        assert stats["ok"] == 2
        assert stats["alert"] == 1
        assert stats["error"] == 0

    def test_get_stats_by_check(self, test_db):
        """Get statistics filtered by check name."""
        now = datetime.now()

        test_db.save_run(
            CheckRun(
                check_name="check1",
                run_at=now,
                command_output="",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.OK,
            )
        )

        test_db.save_run(
            CheckRun(
                check_name="check2",
                run_at=now,
                command_output="",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.ALERT,
            )
        )

        stats = test_db.get_stats(check_name="check1")
        assert stats["ok"] == 1
        assert stats["alert"] == 0
