"""Tests for ampelmann.dashboard."""

from datetime import datetime, timedelta

from ampelmann.dashboard import (
    generate_check_json,
    generate_history_json,
    generate_stats_json,
    generate_status_json,
    write_dashboard,
)
from ampelmann.models import Check, CheckRun, CheckStatus, Config


class TestGenerateStatusJson:
    def test_status_json_empty(self, test_db):
        """Generate status with no checks."""
        result = generate_status_json([], test_db)

        assert "generated_at" in result
        assert result["checks"] == {}
        assert result["summary"]["total"] == 0

    def test_status_json_with_checks(self, test_db, sample_check_dict):
        """Generate status with checks."""
        check = Check.from_dict(sample_check_dict)

        # Add a run to the database
        run = CheckRun(
            check_name=check.name,
            run_at=datetime.now(),
            command_output="OK",
            command_exit_code=0,
            command_duration_ms=100,
            status=CheckStatus.OK,
        )
        test_db.save_run(run)

        result = generate_status_json([check], test_db)

        assert result["summary"]["total"] == 1
        assert result["summary"]["ok"] == 1
        assert check.name in result["checks"]
        assert result["checks"][check.name]["status"] == "ok"

    def test_status_json_disabled_check(self, test_db, sample_check_dict):
        """Generate status with disabled check."""
        sample_check_dict["enabled"] = False
        check = Check.from_dict(sample_check_dict)

        result = generate_status_json([check], test_db)

        assert result["summary"]["disabled"] == 1
        assert result["checks"][check.name]["status"] == "disabled"


class TestGenerateHistoryJson:
    def test_history_json_empty(self, test_db):
        """Generate history with no runs."""
        result = generate_history_json(test_db, hours=48)

        assert "generated_at" in result
        assert result["period_hours"] == 48
        assert result["runs"] == []

    def test_history_json_with_runs(self, test_db):
        """Generate history with runs."""
        run = CheckRun(
            check_name="test",
            run_at=datetime.now(),
            command_output="OK",
            command_exit_code=0,
            command_duration_ms=100,
            status=CheckStatus.OK,
        )
        test_db.save_run(run)

        result = generate_history_json(test_db, hours=48)

        assert len(result["runs"]) == 1
        assert result["runs"][0]["check_name"] == "test"
        assert result["runs"][0]["status"] == "ok"

    def test_history_json_filters_old(self, test_db):
        """History filters out old runs."""
        old_run = CheckRun(
            check_name="test",
            run_at=datetime.now() - timedelta(hours=100),
            command_output="old",
            command_exit_code=0,
            command_duration_ms=100,
            status=CheckStatus.OK,
        )
        test_db.save_run(old_run)

        result = generate_history_json(test_db, hours=48)

        assert len(result["runs"]) == 0


class TestGenerateStatsJson:
    def test_stats_json_empty(self, test_db):
        """Generate stats with no runs."""
        result = generate_stats_json(test_db, days=7)

        assert result["total_runs"] == 0
        assert result["success_rate"] == 0
        assert result["by_status"]["ok"] == 0

    def test_stats_json_with_runs(self, test_db):
        """Generate stats with runs."""
        # Add 3 OK, 1 alert
        for _ in range(3):
            test_db.save_run(
                CheckRun(
                    check_name="test",
                    run_at=datetime.now(),
                    command_output="",
                    command_exit_code=0,
                    command_duration_ms=100,
                    status=CheckStatus.OK,
                )
            )
        test_db.save_run(
            CheckRun(
                check_name="test",
                run_at=datetime.now(),
                command_output="",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.ALERT,
            )
        )

        result = generate_stats_json(test_db, days=7)

        assert result["total_runs"] == 4
        assert result["by_status"]["ok"] == 3
        assert result["by_status"]["alert"] == 1
        assert result["success_rate"] == 75.0


class TestGenerateCheckJson:
    def test_check_json(self, test_db, sample_check_dict):
        """Generate JSON for single check."""
        check = Check.from_dict(sample_check_dict)

        # Add some runs
        for i in range(3):
            test_db.save_run(
                CheckRun(
                    check_name=check.name,
                    run_at=datetime.now() - timedelta(hours=i),
                    command_output=f"output {i}",
                    command_exit_code=0,
                    command_duration_ms=100,
                    status=CheckStatus.OK,
                    llm_model="phi4:14b",
                    llm_response="OK",
                    llm_duration_ms=500,
                )
            )

        result = generate_check_json(check, test_db, history_count=50)

        assert result["name"] == check.name
        assert result["description"] == check.description
        assert result["schedule"] == check.schedule
        assert len(result["history"]) == 3
        assert result["stats"]["total_runs"] == 3


class TestWriteDashboard:
    def test_write_dashboard(self, temp_dir, test_db, sample_check_dict):
        """Write all dashboard files."""
        check = Check.from_dict(sample_check_dict)

        config = Config()
        config.dashboard.output_dir = temp_dir / "dashboard"

        # Add a run
        test_db.save_run(
            CheckRun(
                check_name=check.name,
                run_at=datetime.now(),
                command_output="OK",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.OK,
            )
        )

        write_dashboard(config, [check], test_db)

        # Check files were created (in data/ subdirectory)
        assert (temp_dir / "dashboard" / "data" / "status.json").exists()
        assert (temp_dir / "dashboard" / "data" / "history.json").exists()
        assert (temp_dir / "dashboard" / "data" / "stats.json").exists()
        assert (temp_dir / "dashboard" / "data" / "checks" / f"{check.name}.json").exists()
