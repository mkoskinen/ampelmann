"""Tests for CLI commands."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from ampelmann.cli import main


@pytest.fixture
def runner() -> CliRunner:
    """Create a CLI runner."""
    return CliRunner()


@pytest.fixture
def temp_config(tmp_path: Path) -> Path:
    """Create a temporary config file."""
    config_path = tmp_path / "config.toml"
    checks_dir = tmp_path / "checks.d"
    checks_dir.mkdir()
    db_path = tmp_path / "ampelmann.db"
    log_path = tmp_path / "ampelmann.log"

    # Use forward slashes and escape backslashes for TOML
    db_path_str = str(db_path).replace("\\", "/")
    checks_dir_str = str(checks_dir).replace("\\", "/")
    log_path_str = str(log_path).replace("\\", "/")

    config_path.write_text(f'''# Top-level config
checks_dir = "{checks_dir_str}"

[database]
path = "{db_path_str}"

[ollama]
host = "http://localhost:11434"
model = "test-model"

[ntfy]
url = "https://ntfy.sh"
topic = "test"

[logging]
level = "WARNING"
path = "{log_path_str}"
''')
    return config_path


@pytest.fixture
def temp_check(temp_config: Path) -> Path:
    """Create a temporary check file."""
    checks_dir = temp_config.parent / "checks.d"
    check_path = checks_dir / "test-check.toml"
    check_path.write_text("""
name = "test-check"
description = "A test check"
enabled = true
schedule = "* * * * *"
timeout = 10
use_llm = false
command = "echo 'hello world'"

[notify]
priority = "default"
""")
    return check_path


class TestListCommand:
    """Tests for the list command."""

    def test_list_no_checks(self, runner: CliRunner, temp_config: Path) -> None:
        """Test list with no checks."""
        result = runner.invoke(main, ["-c", str(temp_config), "list"])
        assert result.exit_code == 0
        assert "No checks found" in result.output

    def test_list_with_checks(self, runner: CliRunner, temp_config: Path, temp_check: Path) -> None:
        """Test list with checks."""
        result = runner.invoke(main, ["-c", str(temp_config), "list"])
        assert result.exit_code == 0
        assert "test-check" in result.output


class TestValidateCommand:
    """Tests for the validate command."""

    def test_validate_no_checks(self, runner: CliRunner, temp_config: Path) -> None:
        """Test validate with no checks."""
        result = runner.invoke(main, ["-c", str(temp_config), "validate"])
        assert result.exit_code == 0
        assert "No checks found" in result.output

    def test_validate_valid_check(
        self, runner: CliRunner, temp_config: Path, temp_check: Path
    ) -> None:
        """Test validate with valid check."""
        result = runner.invoke(main, ["-c", str(temp_config), "validate"])
        assert result.exit_code == 0
        assert "test-check" in result.output
        assert "OK" in result.output


class TestStatusCommand:
    """Tests for the status command."""

    def test_status_no_checks(self, runner: CliRunner, temp_config: Path) -> None:
        """Test status with no checks."""
        result = runner.invoke(main, ["-c", str(temp_config), "status"])
        assert result.exit_code == 0
        assert "No checks found" in result.output

    def test_status_with_check(
        self, runner: CliRunner, temp_config: Path, temp_check: Path
    ) -> None:
        """Test status with check."""
        result = runner.invoke(main, ["-c", str(temp_config), "status"])
        assert result.exit_code == 0
        assert "AMPELMANN" in result.output


class TestRunCommand:
    """Tests for the run command."""

    def test_run_dry_run(
        self, runner: CliRunner, temp_config: Path, temp_check: Path
    ) -> None:
        """Test run with dry-run flag."""
        result = runner.invoke(
            main, ["-c", str(temp_config), "run", "--dry-run", "--all"]
        )
        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "test-check" in result.output

    def test_run_no_llm_check(
        self, runner: CliRunner, temp_config: Path, temp_check: Path
    ) -> None:
        """Test running a check without LLM."""
        result = runner.invoke(
            main, ["-c", str(temp_config), "run", "--no-notify", "test-check"]
        )
        assert result.exit_code == 0
        assert "test-check" in result.output
        assert "ok" in result.output.lower()

    def test_run_nonexistent_check(
        self, runner: CliRunner, temp_config: Path, temp_check: Path
    ) -> None:
        """Test running nonexistent check."""
        result = runner.invoke(main, ["-c", str(temp_config), "run", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestEnableDisableCommands:
    """Tests for enable/disable commands."""

    def test_disable_check(
        self, runner: CliRunner, temp_config: Path, temp_check: Path
    ) -> None:
        """Test disabling a check."""
        result = runner.invoke(main, ["-c", str(temp_config), "disable", "test-check"])
        assert result.exit_code == 0
        assert "Disabled" in result.output

        # Verify file was updated
        content = temp_check.read_text()
        assert "enabled = false" in content

    def test_enable_check(
        self, runner: CliRunner, temp_config: Path, temp_check: Path
    ) -> None:
        """Test enabling a disabled check."""
        # First disable it
        temp_check.write_text(temp_check.read_text().replace("enabled = true", "enabled = false"))

        result = runner.invoke(main, ["-c", str(temp_config), "enable", "test-check"])
        assert result.exit_code == 0
        assert "Enabled" in result.output

        # Verify file was updated
        content = temp_check.read_text()
        assert "enabled = true" in content

    def test_enable_already_enabled(
        self, runner: CliRunner, temp_config: Path, temp_check: Path
    ) -> None:
        """Test enabling an already enabled check."""
        result = runner.invoke(main, ["-c", str(temp_config), "enable", "test-check"])
        assert result.exit_code == 0
        assert "Already enabled" in result.output

    def test_disable_nonexistent(self, runner: CliRunner, temp_config: Path) -> None:
        """Test disabling nonexistent check."""
        result = runner.invoke(main, ["-c", str(temp_config), "disable", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestHistoryCommand:
    """Tests for the history command."""

    def test_history_empty(self, runner: CliRunner, temp_config: Path) -> None:
        """Test history with no runs."""
        result = runner.invoke(main, ["-c", str(temp_config), "history"])
        assert result.exit_code == 0
        assert "No history found" in result.output


class TestCleanupCommand:
    """Tests for the cleanup command."""

    def test_cleanup(self, runner: CliRunner, temp_config: Path) -> None:
        """Test cleanup command."""
        result = runner.invoke(main, ["-c", str(temp_config), "cleanup", "--days", "30"])
        assert result.exit_code == 0
        assert "Deleted" in result.output


class TestDashboardCommand:
    """Tests for the dashboard command."""

    def test_dashboard(self, runner: CliRunner, temp_config: Path, tmp_path: Path) -> None:
        """Test dashboard generation."""
        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()

        # Update config to use this dashboard dir
        config_content = temp_config.read_text()
        config_content += f'\n[dashboard]\noutput_dir = "{dashboard_dir}"\n'
        temp_config.write_text(config_content)

        result = runner.invoke(main, ["-c", str(temp_config), "dashboard"])
        assert result.exit_code == 0
        assert "Dashboard written" in result.output


class TestShowCommand:
    """Tests for the show command."""

    def test_show_check(
        self, runner: CliRunner, temp_config: Path, temp_check: Path
    ) -> None:
        """Test showing check details."""
        result = runner.invoke(main, ["-c", str(temp_config), "show", "test-check"])
        assert result.exit_code == 0
        assert "test-check" in result.output
        assert "A test check" in result.output

    def test_show_nonexistent(self, runner: CliRunner, temp_config: Path) -> None:
        """Test showing nonexistent check."""
        result = runner.invoke(main, ["-c", str(temp_config), "show", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()
