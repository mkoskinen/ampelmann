"""Tests for ampelmann.notify."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from ampelmann.models import CheckRun, CheckStatus, NotifyPriority
from ampelmann.notify import (
    NotifyError,
    NtfyClient,
    send_alert,
)


class TestNtfyClient:
    def test_init_default(self):
        """Initialize with defaults."""
        client = NtfyClient()
        assert client.url == "https://ntfy.sh"
        assert client.topic == "ampelmann"

    def test_init_custom(self):
        """Initialize with custom values."""
        client = NtfyClient(url="https://example.com", topic="test")
        assert client.url == "https://example.com"
        assert client.topic == "test"

    @patch("ampelmann.notify.httpx.Client")
    def test_send_simple(self, mock_client_class):
        """Send simple notification."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        client = NtfyClient(url="https://ntfy.example.com", topic="test")
        result = client.send("Hello world")

        assert result is True
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://ntfy.example.com/test"
        assert call_args[1]["content"] == "Hello world"

    @patch("ampelmann.notify.httpx.Client")
    def test_send_with_options(self, mock_client_class):
        """Send notification with all options."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        client = NtfyClient()
        result = client.send(
            message="Test message",
            title="Test Title",
            priority=NotifyPriority.HIGH,
            tags=["warning", "disk"],
        )

        assert result is True
        call_args = mock_client.post.call_args
        headers = call_args[1]["headers"]
        assert headers["Title"] == "Test Title"
        assert headers["Priority"] == "high"
        assert headers["Tags"] == "warning,disk"

    @patch("ampelmann.notify.httpx.Client")
    def test_send_failure(self, mock_client_class):
        """Handle send failure."""
        import httpx

        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock_response
        )
        mock_client.post.return_value = mock_response

        client = NtfyClient()
        with pytest.raises(NotifyError):
            client.send("test")

    @patch("ampelmann.notify.httpx.Client")
    def test_is_available_true(self, mock_client_class):
        """Check availability when responding."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client.get.return_value = mock_response

        client = NtfyClient()
        assert client.is_available() is True

    @patch("ampelmann.notify.httpx.Client")
    def test_is_available_false(self, mock_client_class):
        """Check availability when not responding."""
        import httpx

        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("connection refused")

        client = NtfyClient()
        assert client.is_available() is False


class TestSendAlert:
    @patch("ampelmann.notify.httpx.Client")
    def test_send_alert(self, mock_client_class):
        """Send alert for check run."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        run = CheckRun(
            check_name="test-check",
            run_at=datetime.now(),
            command_output="output",
            command_exit_code=0,
            command_duration_ms=100,
            status=CheckStatus.ALERT,
            alert_message="Disk is failing",
        )

        client = NtfyClient()
        result = send_alert(client, run, tags=["disk"])

        assert result is True

    @patch("ampelmann.notify.httpx.Client")
    def test_send_alert_failure(self, mock_client_class):
        """Handle alert send failure gracefully."""
        import httpx

        mock_client = MagicMock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("network error")

        run = CheckRun(
            check_name="test-check",
            run_at=datetime.now(),
            command_output="output",
            command_exit_code=0,
            command_duration_ms=100,
            status=CheckStatus.ALERT,
        )

        # Use max_retries=1 to speed up test
        client = NtfyClient(max_retries=1)
        result = send_alert(client, run)

        assert result is False  # Fails gracefully


