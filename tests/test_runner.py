"""Tests for ampelmann.runner."""


from ampelmann.models import Check, CheckStatus
from ampelmann.runner import run_check, run_command, truncate_output


class TestRunCommand:
    def test_run_simple_command(self):
        """Run a simple echo command."""
        output, exit_code, duration = run_command("echo hello")
        assert output == "hello"
        assert exit_code == 0
        assert duration > 0

    def test_run_command_captures_stderr(self):
        """Capture stderr in output."""
        output, exit_code, duration = run_command("echo error >&2")
        assert "error" in output
        assert exit_code == 0

    def test_run_command_exit_code(self):
        """Capture non-zero exit code."""
        output, exit_code, duration = run_command("exit 42")
        assert exit_code == 42

    def test_run_command_timeout(self):
        """Handle command timeout."""
        output, exit_code, duration = run_command("sleep 10", timeout=1)
        assert exit_code == -1
        assert "timed out" in output.lower()

    def test_run_command_combined_output(self):
        """Combine stdout and stderr."""
        output, exit_code, duration = run_command(
            "echo stdout; echo stderr >&2"
        )
        assert "stdout" in output
        assert "stderr" in output


class TestRunCheck:
    def test_run_check_success(self, sample_check_dict):
        """Run a successful check."""
        sample_check_dict["command"] = "echo OK"
        check = Check.from_dict(sample_check_dict)

        result = run_check(check)

        assert result.check_name == "test-check"
        assert result.command_output == "OK"
        assert result.command_exit_code == 0
        assert result.command_duration_ms > 0
        assert result.status == CheckStatus.OK

    def test_run_check_failure(self, sample_check_dict):
        """Run a failing check."""
        sample_check_dict["command"] = "exit 1"
        check = Check.from_dict(sample_check_dict)

        result = run_check(check)

        assert result.command_exit_code == 1
        assert result.status == CheckStatus.ERROR

    def test_run_check_timeout(self, sample_check_dict):
        """Handle check timeout."""
        sample_check_dict["command"] = "sleep 10"
        sample_check_dict["timeout"] = 1
        check = Check.from_dict(sample_check_dict)

        result = run_check(check)

        assert result.command_exit_code == -1
        assert result.status == CheckStatus.ERROR


class TestTruncateOutput:
    def test_no_truncation_needed(self):
        """Short output is not truncated."""
        output = "short output"
        result = truncate_output(output, max_chars=100)
        assert result == output

    def test_truncation(self):
        """Long output is truncated."""
        output = "x" * 1000
        result = truncate_output(output, max_chars=100)
        assert len(result) < len(output)
        assert "truncated" in result

    def test_truncation_preserves_ends(self):
        """Truncation keeps start and end."""
        output = "START" + ("x" * 1000) + "END"
        result = truncate_output(output, max_chars=100)
        assert result.startswith("START")
        assert result.endswith("END")
