"""Tests for ampelmann.models."""

from ampelmann.models import (
    Check,
    CheckStatus,
    Config,
    NotifyPriority,
)


class TestCheckStatus:
    def test_status_values(self):
        """Check status enum has expected values."""
        assert CheckStatus.OK.value == "ok"
        assert CheckStatus.ALERT.value == "alert"
        assert CheckStatus.ERROR.value == "error"


class TestNotifyPriority:
    def test_priority_values(self):
        """Notify priority enum has expected values."""
        assert NotifyPriority.MIN.value == "min"
        assert NotifyPriority.LOW.value == "low"
        assert NotifyPriority.DEFAULT.value == "default"
        assert NotifyPriority.HIGH.value == "high"
        assert NotifyPriority.URGENT.value == "urgent"


class TestCheck:
    def test_from_dict_minimal(self):
        """Create check with minimal required fields."""
        data = {
            "name": "test",
            "command": "echo hello",
            "schedule": "* * * * *",
        }
        check = Check.from_dict(data)
        assert check.name == "test"
        assert check.command == "echo hello"
        assert check.schedule == "* * * * *"
        assert check.enabled is True
        assert check.timeout == 30

    def test_from_dict_full(self, sample_check_dict):
        """Create check with all fields."""
        check = Check.from_dict(sample_check_dict)
        assert check.name == "test-check"
        assert check.description == "A test check"
        assert check.command == "echo 'OK'"
        assert check.schedule == "* * * * *"
        assert check.timeout == 30
        assert check.enabled is True
        assert check.llm.model == "qwen2.5:7b"
        assert check.llm.timeout == 60
        assert check.notify.priority == NotifyPriority.DEFAULT
        assert check.notify.tags == ["test"]

    def test_from_dict_disabled(self):
        """Create disabled check."""
        data = {
            "name": "test",
            "command": "echo hello",
            "schedule": "* * * * *",
            "enabled": False,
        }
        check = Check.from_dict(data)
        assert check.enabled is False


class TestConfig:
    def test_default_config(self):
        """Config with defaults."""
        config = Config()
        assert config.ollama.host == "http://localhost:11434"
        assert config.ollama.model == "qwen2.5:7b"
        assert config.ntfy.url == "https://ntfy.sh"

    def test_from_dict(self, sample_config_dict):
        """Create config from dictionary."""
        config = Config.from_dict(sample_config_dict)
        assert config.ollama.host == "http://localhost:11434"
        assert config.ollama.model == "qwen2.5:7b"
        assert config.ntfy.url == "https://ntfy.example.com"
        assert config.ntfy.topic == "test"
        assert config.defaults.retain_days == 90
