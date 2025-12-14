"""Tests for ampelmann.llm."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from ampelmann.llm import LLMError, OllamaClient, analyze_output, build_prompt, format_history
from ampelmann.models import Check, CheckRun, CheckStatus, Config


class TestOllamaClient:
    def test_init_default(self):
        """Initialize with defaults."""
        client = OllamaClient()
        assert client.host == "http://localhost:11434"
        assert client.timeout == 600

    def test_init_custom(self):
        """Initialize with custom values."""
        client = OllamaClient(host="http://example.com:1234", timeout=60)
        assert client.host == "http://example.com:1234"
        assert client.timeout == 60

    def test_host_trailing_slash_removed(self):
        """Trailing slash is removed from host."""
        client = OllamaClient(host="http://example.com/")
        assert client.host == "http://example.com"

    @patch("ampelmann.llm.httpx.Client")
    def test_generate_success(self, mock_client_class):
        """Successful generation."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "  OK  "}
        mock_client.post.return_value = mock_response

        client = OllamaClient()
        result = client.generate("test-model", "test prompt")

        assert result == "OK"
        mock_client.post.assert_called_once()

    @patch("ampelmann.llm.httpx.Client")
    def test_generate_timeout(self, mock_client_class):
        """Handle timeout."""
        import httpx

        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.TimeoutException("timeout")

        client = OllamaClient()
        with pytest.raises(LLMError, match="timed out"):
            client.generate("test-model", "test prompt")

    @patch("ampelmann.llm.httpx.Client")
    def test_is_available_true(self, mock_client_class):
        """Check availability when responding."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.get.return_value = mock_response

        client = OllamaClient()
        assert client.is_available() is True

    @patch("ampelmann.llm.httpx.Client")
    def test_is_available_false(self, mock_client_class):
        """Check availability when not responding."""
        import httpx

        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("connection refused")

        client = OllamaClient()
        assert client.is_available() is False


class TestBuildPrompt:
    def test_build_prompt(self, sample_check_dict):
        """Build full prompt with output."""
        check = Check.from_dict(sample_check_dict)
        output = "Command output here"

        prompt = build_prompt(check, output)

        assert check.llm.prompt in prompt
        assert "Command output here" in prompt
        assert "--- Current Output ---" in prompt

    def test_build_prompt_with_history(self, sample_check_dict):
        """Build prompt includes history when provided."""
        check = Check.from_dict(sample_check_dict)
        output = "Current output"

        history = [
            CheckRun(
                check_name="test-check",
                run_at=datetime(2024, 1, 15, 10, 30),
                command_output="Previous output 1",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.OK,
            ),
        ]

        prompt = build_prompt(check, output, history)

        assert "--- Previous Runs" in prompt
        assert "2024-01-15 10:30" in prompt
        assert "Previous output 1" in prompt
        assert "--- Current Output ---" in prompt
        assert "Current output" in prompt


class TestFormatHistory:
    def test_empty_history(self):
        """Empty history returns empty string."""
        assert format_history([]) == ""

    def test_format_single_run(self):
        """Format a single historical run."""
        history = [
            CheckRun(
                check_name="test",
                run_at=datetime(2024, 1, 15, 10, 30),
                command_output="Output here",
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.OK,
            ),
        ]

        result = format_history(history)

        assert "2024-01-15 10:30" in result
        assert "Output here" in result
        # Status is not included to avoid feedback loops

    def test_truncate_long_output(self):
        """Long outputs are truncated."""
        history = [
            CheckRun(
                check_name="test",
                run_at=datetime(2024, 1, 15, 10, 30),
                command_output="x" * 1200,
                command_exit_code=0,
                command_duration_ms=100,
                status=CheckStatus.OK,
            ),
        ]

        result = format_history(history)

        assert "... (truncated)" in result
        # Should have 1000 chars + truncation message
        assert result.count("x") == 1000


class TestAnalyzeOutput:
    @patch("ampelmann.llm.httpx.Client")
    def test_analyze_ok(self, mock_client_class, sample_check_dict):
        """Analyze output that results in OK."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "OK"}
        mock_client.post.return_value = mock_response

        check = Check.from_dict(sample_check_dict)
        config = Config()
        run = CheckRun(
            check_name="test",
            run_at=datetime.now(),
            command_output="all good",
            command_exit_code=0,
            command_duration_ms=100,
            status=CheckStatus.OK,
        )

        client = OllamaClient()
        result = analyze_output(client, check, run, config)

        assert result.status == CheckStatus.OK
        assert result.llm_response == "OK"
        assert result.llm_model is not None
        assert result.llm_duration_ms is not None

    @patch("ampelmann.llm.httpx.Client")
    def test_analyze_alert(self, mock_client_class, sample_check_dict):
        """Analyze output that results in alert."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "ALERT: Disk is failing"}
        mock_client.post.return_value = mock_response

        check = Check.from_dict(sample_check_dict)
        config = Config()
        run = CheckRun(
            check_name="test",
            run_at=datetime.now(),
            command_output="error output",
            command_exit_code=0,
            command_duration_ms=100,
            status=CheckStatus.OK,
        )

        client = OllamaClient()
        result = analyze_output(client, check, run, config)

        assert result.status == CheckStatus.ALERT
        assert result.alert_message == "ALERT: Disk is failing"

    @patch("ampelmann.llm.httpx.Client")
    def test_analyze_llm_error(self, mock_client_class, sample_check_dict):
        """Handle LLM error."""
        import httpx

        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.TimeoutException("timeout")

        check = Check.from_dict(sample_check_dict)
        config = Config()
        run = CheckRun(
            check_name="test",
            run_at=datetime.now(),
            command_output="output",
            command_exit_code=0,
            command_duration_ms=100,
            status=CheckStatus.OK,
        )

        client = OllamaClient()
        result = analyze_output(client, check, run, config)

        assert result.status == CheckStatus.ERROR
        assert "LLM" in result.llm_response
