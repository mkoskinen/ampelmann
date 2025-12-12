"""Scheduling logic for Ampelmann checks."""

from datetime import datetime

from croniter import croniter  # type: ignore[import-untyped]
from croniter.croniter import CroniterBadCronError  # type: ignore[import-untyped]

from ampelmann.models import Check


def is_check_due(check: Check, last_run: datetime | None) -> bool:
    """Determine if a check is due to run.

    Args:
        check: The check definition.
        last_run: When the check last ran (None if never).

    Returns:
        True if the check should run now.

    Raises:
        ValueError: If the cron schedule is invalid.
    """
    if not check.enabled:
        return False

    try:
        if last_run is None:
            # Validate schedule even when never run
            croniter(check.schedule)
            return True

        cron = croniter(check.schedule, last_run)
        next_time: datetime = cron.get_next(datetime)
        return datetime.now() >= next_time
    except (ValueError, KeyError, CroniterBadCronError) as e:
        raise ValueError(f"Invalid cron schedule '{check.schedule}': {e}") from e


def next_run_time(check: Check, after: datetime | None = None) -> datetime:
    """Calculate the next scheduled run time for a check.

    Args:
        check: The check definition.
        after: Calculate next run after this time (default: now).

    Returns:
        The next scheduled run time.

    Raises:
        ValueError: If the cron schedule is invalid.
    """
    base_time = after or datetime.now()

    try:
        cron = croniter(check.schedule, base_time)
        result: datetime = cron.get_next(datetime)
        return result
    except (ValueError, KeyError, CroniterBadCronError) as e:
        raise ValueError(f"Invalid cron schedule '{check.schedule}': {e}") from e


def prev_run_time(check: Check, before: datetime | None = None) -> datetime:
    """Calculate the previous scheduled run time for a check.

    Args:
        check: The check definition.
        before: Calculate previous run before this time (default: now).

    Returns:
        The previous scheduled run time.

    Raises:
        ValueError: If the cron schedule is invalid.
    """
    base_time = before or datetime.now()

    try:
        cron = croniter(check.schedule, base_time)
        result: datetime = cron.get_prev(datetime)
        return result
    except (ValueError, KeyError, CroniterBadCronError) as e:
        raise ValueError(f"Invalid cron schedule '{check.schedule}': {e}") from e


def get_due_checks(checks: list[Check], last_runs: dict[str, datetime | None]) -> list[Check]:
    """Filter checks to only those that are due.

    Args:
        checks: List of all checks.
        last_runs: Mapping of check name to last run time.

    Returns:
        List of checks that are due to run.
    """
    due = []
    for check in checks:
        last_run = last_runs.get(check.name)
        try:
            if is_check_due(check, last_run):
                due.append(check)
        except ValueError:
            # Skip checks with invalid schedules
            continue
    return due


def parse_schedule(schedule: str) -> str:
    """Validate and describe a cron schedule.

    Args:
        schedule: Cron expression.

    Returns:
        Human-readable description.

    Raises:
        ValueError: If schedule is invalid.
    """
    try:
        croniter(schedule)
    except (ValueError, KeyError, CroniterBadCronError) as e:
        raise ValueError(f"Invalid cron schedule: {e}") from e

    # Parse common patterns
    parts = schedule.split()
    if len(parts) != 5:
        return schedule

    minute, hour, day, month, weekday = parts

    # Every minute
    if schedule == "* * * * *":
        return "every minute"

    # Every N minutes
    if minute.startswith("*/") and hour == "*" and day == "*" and month == "*" and weekday == "*":
        n = minute[2:]
        return f"every {n} minutes"

    # Hourly
    if hour == "*" and day == "*" and month == "*" and weekday == "*":
        if minute == "0":
            return "hourly"
        return f"hourly at :{minute.zfill(2)}"

    # Daily
    if day == "*" and month == "*" and weekday == "*":
        return f"daily at {hour.zfill(2)}:{minute.zfill(2)}"

    # Weekly
    if day == "*" and month == "*" and weekday != "*":
        days = {
            "0": "Sunday", "1": "Monday", "2": "Tuesday", "3": "Wednesday",
            "4": "Thursday", "5": "Friday", "6": "Saturday", "7": "Sunday",
        }
        day_name = days.get(weekday, weekday)
        return f"weekly on {day_name} at {hour.zfill(2)}:{minute.zfill(2)}"

    return schedule
