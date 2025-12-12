"""Pytest fixtures for Ampelmann tests."""

import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_dir():
    """Provide a temporary directory."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def sample_config_dict():
    """Provide a sample configuration as a dictionary."""
    return {
        "ollama": {
            "host": "http://localhost:11434",
            "model": "qwen2.5:7b",
            "timeout": 120,
        },
        "ntfy": {
            "url": "https://ntfy.example.com",
            "topic": "test",
        },
        "database": {
            "path": "/tmp/test.db",
        },
        "logging": {
            "level": "DEBUG",
            "path": "/tmp/test.log",
        },
        "dashboard": {
            "output_dir": "/tmp/dashboard",
            "history_hours": 48,
            "stats_days": 7,
            "check_history_count": 50,
        },
        "defaults": {
            "alert_on_check_error": True,
            "alert_on_llm_error": True,
            "retain_days": 90,
        },
    }


@pytest.fixture
def sample_check_dict():
    """Provide a sample check definition as a dictionary."""
    return {
        "name": "test-check",
        "description": "A test check",
        "enabled": True,
        "command": "echo 'OK'",
        "schedule": "* * * * *",
        "timeout": 30,
        "llm": {
            "model": "qwen2.5:7b",
            "timeout": 60,
            "prompt": "Respond with OK or an alert message.",
        },
        "notify": {
            "priority": "default",
            "tags": ["test"],
        },
    }


@pytest.fixture
def sample_config_toml(temp_dir, sample_config_dict):
    """Write sample config to a TOML file and return path."""

    # We need to write TOML, but tomllib is read-only.
    # We'll manually format it for tests.
    config_path = temp_dir / "config.toml"
    content = """
[ollama]
host = "http://localhost:11434"
model = "qwen2.5:7b"
timeout = 120

[ntfy]
url = "https://ntfy.example.com"
topic = "test"

[database]
path = "/tmp/test.db"

[logging]
level = "DEBUG"
path = "/tmp/test.log"

[dashboard]
output_dir = "/tmp/dashboard"
history_hours = 48
stats_days = 7
check_history_count = 50

[defaults]
alert_on_check_error = true
alert_on_llm_error = true
retain_days = 90
"""
    config_path.write_text(content)
    return config_path


@pytest.fixture
def sample_check_toml(temp_dir, sample_check_dict):
    """Write sample check to a TOML file and return path."""
    check_path = temp_dir / "test-check.toml"
    content = """
name = "test-check"
description = "A test check"
enabled = true
command = "echo 'OK'"
schedule = "* * * * *"
timeout = 30

[llm]
model = "qwen2.5:7b"
timeout = 60
prompt = "Respond with OK or an alert message."

[notify]
priority = "default"
tags = ["test"]
"""
    check_path.write_text(content)
    return check_path


@pytest.fixture
def test_db(temp_dir):
    """Provide an initialized test database."""
    from ampelmann.db import Database

    db_path = temp_dir / "test.db"
    db = Database(db_path)
    db.init_schema()
    return db
