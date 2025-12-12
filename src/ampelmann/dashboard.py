"""Dashboard JSON generation for Ampelmann."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ampelmann.db import Database
from ampelmann.models import Check, Config


def generate_status_json(
    checks: list[Check],
    db: Database,
) -> dict[str, Any]:
    """Generate current status JSON.

    Args:
        checks: List of all checks.
        db: Database instance.

    Returns:
        Status data dictionary.
    """
    status_data: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "checks": {},
        "summary": {
            "total": len(checks),
            "ok": 0,
            "alert": 0,
            "error": 0,
            "disabled": 0,
        },
    }

    for check in checks:
        if not check.enabled:
            status_data["summary"]["disabled"] += 1
            status_data["checks"][check.name] = {
                "status": "disabled",
                "description": check.description,
            }
            continue

        latest = db.get_latest_run(check.name)
        if latest:
            status = latest.status.value
            status_data["summary"][status] += 1
            status_data["checks"][check.name] = {
                "status": status,
                "description": check.description,
                "last_run": latest.run_at.isoformat(),
                "alert_message": latest.alert_message,
            }
        else:
            status_data["checks"][check.name] = {
                "status": "pending",
                "description": check.description,
            }

    return status_data


def generate_history_json(
    db: Database,
    hours: int = 48,
) -> dict[str, Any]:
    """Generate recent history JSON.

    Args:
        db: Database instance.
        hours: Number of hours of history to include.

    Returns:
        History data dictionary.
    """
    since = datetime.now() - timedelta(hours=hours)
    runs = db.get_runs(since=since, limit=1000)

    history_data: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "period_hours": hours,
        "runs": [],
    }

    for run in runs:
        history_data["runs"].append({
            "check_name": run.check_name,
            "run_at": run.run_at.isoformat(),
            "status": run.status.value,
            "alert_message": run.alert_message,
            "command_duration_ms": run.command_duration_ms,
            "llm_duration_ms": run.llm_duration_ms,
        })

    return history_data


def generate_stats_json(
    db: Database,
    days: int = 7,
) -> dict[str, Any]:
    """Generate statistics JSON.

    Args:
        db: Database instance.
        days: Number of days to include.

    Returns:
        Statistics data dictionary.
    """
    stats = db.get_stats(days=days)

    total = sum(stats.values())
    stats_data = {
        "generated_at": datetime.now().isoformat(),
        "period_days": days,
        "total_runs": total,
        "by_status": stats,
        "success_rate": round(stats["ok"] / total * 100, 1) if total > 0 else 0,
    }

    return stats_data


def generate_check_json(
    check: Check,
    db: Database,
    history_count: int = 50,
) -> dict[str, Any]:
    """Generate detailed JSON for a single check.

    Args:
        check: The check definition.
        db: Database instance.
        history_count: Number of historical runs to include.

    Returns:
        Check detail data dictionary.
    """
    runs = db.get_runs(check_name=check.name, limit=history_count)
    stats = db.get_stats(check_name=check.name, days=7)

    check_data: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "name": check.name,
        "description": check.description,
        "enabled": check.enabled,
        "schedule": check.schedule,
        "command": check.command,
        "stats": {
            "total_runs": sum(stats.values()),
            "by_status": stats,
        },
        "history": [],
    }

    for run in runs:
        check_data["history"].append({
            "run_at": run.run_at.isoformat(),
            "status": run.status.value,
            "command_exit_code": run.command_exit_code,
            "command_duration_ms": run.command_duration_ms,
            "llm_model": run.llm_model,
            "llm_response": run.llm_response,
            "llm_duration_ms": run.llm_duration_ms,
            "alert_message": run.alert_message,
        })

    return check_data


def write_dashboard(
    config: Config,
    checks: list[Check],
    db: Database,
) -> None:
    """Generate all dashboard JSON files.

    Args:
        config: Application config.
        checks: List of all checks.
        db: Database instance.
    """
    output_dir = config.dashboard.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Data directory structure (matches index.html fetch paths)
    data_dir = output_dir / "data"
    data_dir.mkdir(exist_ok=True)
    checks_dir = data_dir / "checks"
    checks_dir.mkdir(exist_ok=True)

    # Status JSON
    status_data = generate_status_json(checks, db)
    _write_json(data_dir / "status.json", status_data)

    # History JSON
    history_data = generate_history_json(db, config.dashboard.history_hours)
    _write_json(data_dir / "history.json", history_data)

    # Stats JSON
    stats_data = generate_stats_json(db, config.dashboard.stats_days)
    _write_json(data_dir / "stats.json", stats_data)

    # Per-check JSON
    for check in checks:
        check_data = generate_check_json(check, db, config.dashboard.check_history_count)
        _write_json(checks_dir / f"{check.name}.json", check_data)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON data to a file.

    Args:
        path: Output file path.
        data: Data to write.
    """
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
