"""Tests for ampelmann.config."""


import pytest

from ampelmann.config import (
    ConfigError,
    load_check,
    load_checks,
    load_checks_from_file,
    load_config,
    validate_check,
)


class TestLoadConfig:
    def test_load_config_from_file(self, sample_config_toml):
        """Load config from explicit path."""
        config = load_config(sample_config_toml)
        assert config.ollama.host == "http://localhost:11434"
        assert config.ntfy.topic == "test"

    def test_load_config_missing_file(self, temp_dir):
        """Raise error for missing config file."""
        with pytest.raises(ConfigError, match="not found"):
            load_config(temp_dir / "nonexistent.toml")

    def test_load_config_invalid_toml(self, temp_dir):
        """Raise error for invalid TOML."""
        bad_config = temp_dir / "bad.toml"
        bad_config.write_text("this is not valid toml [[[")
        with pytest.raises(ConfigError, match="Invalid TOML"):
            load_config(bad_config)

    def test_load_config_defaults(self, temp_dir, monkeypatch):
        """Return defaults when no config file exists."""
        # Patch default paths to nonexistent locations
        monkeypatch.setattr(
            "ampelmann.config.DEFAULT_CONFIG_PATHS",
            [temp_dir / "nope.toml"],
        )
        config = load_config()
        assert config.ollama.model == "qwen2.5:7b"


class TestLoadCheck:
    def test_load_check(self, sample_check_toml):
        """Load check from TOML file."""
        check = load_check(sample_check_toml)
        assert check.name == "test-check"
        assert check.command == "echo 'OK'"
        assert check.llm.prompt == "Respond with OK or an alert message."
        assert check.source_path == sample_check_toml

    def test_load_check_missing_file(self, temp_dir):
        """Raise error for missing check file."""
        with pytest.raises(ConfigError, match="not found"):
            load_check(temp_dir / "nonexistent.toml")

    def test_load_check_missing_required(self, temp_dir):
        """Raise error for missing required fields."""
        bad_check = temp_dir / "bad.toml"
        bad_check.write_text('name = "test"\n')
        with pytest.raises(ConfigError, match="Missing required fields"):
            load_check(bad_check)


class TestLoadChecks:
    def test_load_checks_from_dir(self, temp_dir):
        """Load multiple checks from directory."""
        checks_dir = temp_dir / "checks.d"
        checks_dir.mkdir()

        (checks_dir / "check1.toml").write_text("""
name = "check1"
command = "echo 1"
schedule = "0 * * * *"
[llm]
prompt = "test"
""")
        (checks_dir / "check2.toml").write_text("""
name = "check2"
command = "echo 2"
schedule = "0 * * * *"
[llm]
prompt = "test"
""")

        checks = load_checks(checks_dir)
        assert len(checks) == 2
        names = {c.name for c in checks}
        assert names == {"check1", "check2"}

    def test_load_checks_missing_dir(self, temp_dir):
        """Raise error for missing directory."""
        with pytest.raises(ConfigError, match="not found"):
            load_checks(temp_dir / "nonexistent")

    def test_load_checks_not_a_dir(self, temp_dir):
        """Raise error if path is not a directory."""
        file_path = temp_dir / "file.txt"
        file_path.touch()
        with pytest.raises(ConfigError, match="Not a directory"):
            load_checks(file_path)


class TestValidateCheck:
    def test_validate_valid_check(self, sample_check_dict):
        """Valid check has no errors."""
        from ampelmann.models import Check

        check = Check.from_dict(sample_check_dict)
        errors = validate_check(check)
        assert errors == []

    def test_validate_missing_prompt(self, sample_check_dict):
        """Missing prompt is an error."""
        from ampelmann.models import Check

        sample_check_dict["llm"]["prompt"] = ""
        check = Check.from_dict(sample_check_dict)
        errors = validate_check(check)
        assert any("prompt" in e for e in errors)

    def test_validate_invalid_schedule(self, sample_check_dict):
        """Invalid cron schedule is an error."""
        from ampelmann.models import Check

        sample_check_dict["schedule"] = "not a cron"
        check = Check.from_dict(sample_check_dict)
        errors = validate_check(check)
        assert any("schedule" in e for e in errors)

    def test_validate_negative_timeout(self, sample_check_dict):
        """Negative timeout is an error."""
        from ampelmann.models import Check

        sample_check_dict["timeout"] = -1
        check = Check.from_dict(sample_check_dict)
        errors = validate_check(check)
        assert any("timeout" in e for e in errors)


class TestMatrixExpansion:
    def test_single_variable_matrix(self, temp_dir):
        """Expand matrix with single variable."""
        check_path = temp_dir / "smart.toml"
        check_path.write_text("""
name = "smart-${disk}"
description = "SMART for ${disk}"
command = "smartctl -a /dev/${disk}"
schedule = "0 6 * * *"

[matrix]
disk = ["sda", "sdb"]

[llm]
prompt = "Check ${disk}"
""")
        checks = load_checks_from_file(check_path)

        assert len(checks) == 2
        assert checks[0].name == "smart-sda"
        assert checks[0].command == "smartctl -a /dev/sda"
        assert checks[0].description == "SMART for sda"
        assert checks[0].llm.prompt == "Check sda"
        assert checks[1].name == "smart-sdb"
        assert checks[1].command == "smartctl -a /dev/sdb"

    def test_multiple_variable_matrix(self, temp_dir):
        """Expand matrix with multiple variables (cartesian product)."""
        check_path = temp_dir / "check.toml"
        check_path.write_text("""
name = "check-${host}-${port}"
command = "curl ${host}:${port}"
schedule = "* * * * *"

[matrix]
host = ["localhost", "remote"]
port = ["80", "443"]

[llm]
prompt = "test"
""")
        checks = load_checks_from_file(check_path)

        assert len(checks) == 4
        names = {c.name for c in checks}
        assert names == {
            "check-localhost-80",
            "check-localhost-443",
            "check-remote-80",
            "check-remote-443",
        }

    def test_no_matrix_returns_single(self, temp_dir):
        """Check without matrix returns single check."""
        check_path = temp_dir / "simple.toml"
        check_path.write_text("""
name = "simple"
command = "echo ok"
schedule = "* * * * *"

[llm]
prompt = "test"
""")
        checks = load_checks_from_file(check_path)

        assert len(checks) == 1
        assert checks[0].name == "simple"

    def test_matrix_in_tags(self, temp_dir):
        """Matrix substitution works in notify tags."""
        check_path = temp_dir / "check.toml"
        check_path.write_text("""
name = "check-${disk}"
command = "echo ${disk}"
schedule = "* * * * *"

[matrix]
disk = ["sda"]

[llm]
prompt = "test"

[notify]
tags = ["disk", "${disk}"]
""")
        checks = load_checks_from_file(check_path)

        assert checks[0].notify.tags == ["disk", "sda"]

    def test_empty_matrix_error(self, temp_dir):
        """Empty matrix raises error."""
        check_path = temp_dir / "check.toml"
        check_path.write_text("""
name = "check"
command = "echo"
schedule = "* * * * *"

[matrix]

[llm]
prompt = "test"
""")
        with pytest.raises(ConfigError, match="cannot be empty"):
            load_checks_from_file(check_path)

    def test_empty_matrix_list_error(self, temp_dir):
        """Empty matrix variable list raises error."""
        check_path = temp_dir / "check.toml"
        check_path.write_text("""
name = "check-${disk}"
command = "echo"
schedule = "* * * * *"

[matrix]
disk = []

[llm]
prompt = "test"
""")
        with pytest.raises(ConfigError, match="cannot be empty"):
            load_checks_from_file(check_path)

    def test_load_checks_expands_matrix(self, temp_dir):
        """load_checks expands matrix checks."""
        checks_dir = temp_dir / "checks.d"
        checks_dir.mkdir()

        (checks_dir / "smart.toml").write_text("""
name = "smart-${disk}"
command = "smartctl /dev/${disk}"
schedule = "0 6 * * *"

[matrix]
disk = ["sda", "sdb"]

[llm]
prompt = "test"
""")
        (checks_dir / "simple.toml").write_text("""
name = "simple"
command = "echo ok"
schedule = "* * * * *"

[llm]
prompt = "test"
""")

        checks = load_checks(checks_dir)

        assert len(checks) == 3
        names = {c.name for c in checks}
        assert names == {"smart-sda", "smart-sdb", "simple"}

    def test_unsubstituted_variable_preserved(self, temp_dir):
        """Unknown variables are preserved as-is."""
        check_path = temp_dir / "check.toml"
        check_path.write_text("""
name = "check-${disk}"
command = "echo ${unknown}"
schedule = "* * * * *"

[matrix]
disk = ["sda"]

[llm]
prompt = "test"
""")
        checks = load_checks_from_file(check_path)

        assert checks[0].name == "check-sda"
        assert checks[0].command == "echo ${unknown}"
