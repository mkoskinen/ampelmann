"""Tests for ampelmann.scheduler."""

from datetime import datetime, timedelta

import pytest

from ampelmann.models import Check
from ampelmann.scheduler import (
    get_due_checks,
    is_check_due,
    next_run_time,
    parse_schedule,
    prev_run_time,
)


class TestIsCheckDue:
    def test_check_due_never_run(self, sample_check_dict):
        """Check that never ran should be due."""
        check = Check.from_dict(sample_check_dict)
        assert is_check_due(check, last_run=None)

    def test_check_due_after_interval(self, sample_check_dict):
        """Check should be due after schedule passes."""
        sample_check_dict["schedule"] = "0 * * * *"  # Hourly
        check = Check.from_dict(sample_check_dict)
        last_run = datetime.now() - timedelta(hours=2)
        assert is_check_due(check, last_run=last_run)

    def test_check_not_due_recently_run(self, sample_check_dict):
        """Check should not be due if recently run (after most recent scheduled time)."""
        sample_check_dict["schedule"] = "0 * * * *"  # Hourly
        check = Check.from_dict(sample_check_dict)
        # Get the most recent scheduled time, then set last_run to 5 min after that
        # This ensures we've "already run" for this schedule window
        prev_scheduled = prev_run_time(check)
        last_run = prev_scheduled + timedelta(minutes=5)
        assert not is_check_due(check, last_run=last_run)

    def test_check_disabled(self, sample_check_dict):
        """Disabled checks are never due."""
        sample_check_dict["enabled"] = False
        check = Check.from_dict(sample_check_dict)
        assert not is_check_due(check, last_run=None)

    def test_invalid_schedule(self, sample_check_dict):
        """Invalid cron should raise."""
        sample_check_dict["schedule"] = "invalid"
        check = Check.from_dict(sample_check_dict)
        with pytest.raises(ValueError, match="Invalid cron"):
            is_check_due(check, last_run=None)


class TestNextRunTime:
    def test_next_run_time_hourly(self, sample_check_dict):
        """Calculate next run for hourly schedule."""
        sample_check_dict["schedule"] = "0 * * * *"
        check = Check.from_dict(sample_check_dict)
        next_time = next_run_time(check)
        assert next_time.minute == 0

    def test_next_run_time_daily(self, sample_check_dict):
        """Calculate next run for daily schedule."""
        sample_check_dict["schedule"] = "0 6 * * *"
        check = Check.from_dict(sample_check_dict)
        next_time = next_run_time(check)
        assert next_time.hour == 6
        assert next_time.minute == 0

    def test_next_run_time_with_base(self, sample_check_dict):
        """Calculate next run from specific time."""
        sample_check_dict["schedule"] = "0 * * * *"
        check = Check.from_dict(sample_check_dict)
        base = datetime(2024, 1, 15, 10, 30)
        next_time = next_run_time(check, after=base)
        assert next_time == datetime(2024, 1, 15, 11, 0)


class TestPrevRunTime:
    def test_prev_run_time(self, sample_check_dict):
        """Calculate previous run time."""
        sample_check_dict["schedule"] = "0 * * * *"
        check = Check.from_dict(sample_check_dict)
        base = datetime(2024, 1, 15, 10, 30)
        prev_time = prev_run_time(check, before=base)
        assert prev_time == datetime(2024, 1, 15, 10, 0)


class TestGetDueChecks:
    def test_get_due_checks(self, sample_check_dict):
        """Filter to only due checks."""
        check1_dict = sample_check_dict.copy()
        check1_dict["name"] = "check1"
        check1_dict["schedule"] = "0 * * * *"

        check2_dict = sample_check_dict.copy()
        check2_dict["name"] = "check2"
        check2_dict["schedule"] = "0 * * * *"

        check1 = Check.from_dict(check1_dict)
        check2 = Check.from_dict(check2_dict)
        checks = [check1, check2]

        # check1: ran 5 min after last scheduled time (not due)
        # check2: never ran (due)
        prev_scheduled = prev_run_time(check1)
        last_runs = {
            "check1": prev_scheduled + timedelta(minutes=5),
            "check2": None,
        }

        due = get_due_checks(checks, last_runs)
        assert len(due) == 1
        assert due[0].name == "check2"

    def test_get_due_checks_skips_invalid(self, sample_check_dict):
        """Skip checks with invalid schedules."""
        check1_dict = sample_check_dict.copy()
        check1_dict["name"] = "valid"
        check1_dict["schedule"] = "* * * * *"

        check2_dict = sample_check_dict.copy()
        check2_dict["name"] = "invalid"
        check2_dict["schedule"] = "not-a-cron"

        checks = [Check.from_dict(check1_dict), Check.from_dict(check2_dict)]
        due = get_due_checks(checks, {})

        assert len(due) == 1
        assert due[0].name == "valid"


class TestParseSchedule:
    def test_every_minute(self):
        """Parse every minute schedule."""
        assert parse_schedule("* * * * *") == "every minute"

    def test_every_n_minutes(self):
        """Parse every N minutes schedule."""
        assert parse_schedule("*/5 * * * *") == "every 5 minutes"
        assert parse_schedule("*/15 * * * *") == "every 15 minutes"

    def test_hourly(self):
        """Parse hourly schedule."""
        assert parse_schedule("0 * * * *") == "hourly"
        assert parse_schedule("30 * * * *") == "hourly at :30"

    def test_daily(self):
        """Parse daily schedule."""
        assert parse_schedule("0 6 * * *") == "daily at 06:00"
        assert parse_schedule("30 14 * * *") == "daily at 14:30"

    def test_weekly(self):
        """Parse weekly schedule."""
        assert parse_schedule("0 6 * * 1") == "weekly on Monday at 06:00"
        assert parse_schedule("0 6 * * 0") == "weekly on Sunday at 06:00"

    def test_invalid(self):
        """Raise on invalid schedule."""
        with pytest.raises(ValueError):
            parse_schedule("not a cron")
