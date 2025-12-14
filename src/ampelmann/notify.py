"""ntfy notification client for Ampelmann."""

import logging

import httpx

from ampelmann.models import CheckRun, NotifyPriority
from ampelmann.retry import retry_on_error

logger = logging.getLogger(__name__)


class NotifyError(Exception):
    """Error sending notification."""


class NtfyClient:
    """Client for ntfy notifications."""

    def __init__(
        self,
        url: str = "https://ntfy.sh",
        topic: str = "ampelmann",
        token: str | None = None,
        max_retries: int = 3,
    ) -> None:
        """Initialize ntfy client.

        Args:
            url: ntfy server URL.
            topic: Default topic for notifications.
            token: Access token for authentication.
            max_retries: Maximum retry attempts for transient failures.
        """
        self.url = url.rstrip("/")
        self.topic = topic
        self.token = token
        self.max_retries = max_retries

    def send(
        self,
        message: str,
        title: str | None = None,
        priority: NotifyPriority = NotifyPriority.DEFAULT,
        tags: list[str] | None = None,
        topic: str | None = None,
    ) -> bool:
        """Send a notification.

        Args:
            message: Notification body.
            title: Notification title.
            priority: Notification priority.
            tags: List of tags/emojis.
            topic: Topic to publish to (overrides default).

        Returns:
            True if notification was sent successfully.

        Raises:
            NotifyError: If the request fails after retries.
        """
        target_topic = topic or self.topic
        url = f"{self.url}/{target_topic}"

        headers: dict[str, str] = {}

        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        if title:
            headers["Title"] = title

        if priority != NotifyPriority.DEFAULT:
            headers["Priority"] = priority.value

        if tags:
            headers["Tags"] = ",".join(tags)

        def _do_send() -> bool:
            try:
                with httpx.Client(timeout=30) as client:
                    response = client.post(url, content=message, headers=headers)
                    response.raise_for_status()
                    return True

            except httpx.HTTPStatusError as e:
                # Don't retry client errors (4xx)
                if 400 <= e.response.status_code < 500:
                    raise NotifyError(f"ntfy request failed: {e.response.status_code}") from e
                raise NotifyError(f"ntfy server error: {e.response.status_code}") from e
            except httpx.RequestError as e:
                raise NotifyError(f"ntfy connection error: {e}") from e

        return retry_on_error(
            _do_send,
            max_attempts=self.max_retries,
            delay=1.0,
            exceptions=(NotifyError,),
        )

    def is_available(self) -> bool:
        """Check if ntfy server is available.

        Returns:
            True if server is responding.
        """
        try:
            with httpx.Client(timeout=5) as client:
                # Just check if the server responds
                response = client.get(self.url)
                return response.status_code < 500
        except httpx.RequestError as e:
            logger.debug("ntfy not available: %s", e)
            return False


def send_alert(
    client: NtfyClient,
    run: CheckRun,
    tags: list[str] | None = None,
    priority: NotifyPriority = NotifyPriority.DEFAULT,
) -> bool:
    """Send an alert notification for a check run.

    Args:
        client: ntfy client.
        run: The check run that triggered the alert.
        tags: Additional tags for the notification.
        priority: Notification priority.

    Returns:
        True if notification was sent.
    """
    title = f"Ampelmann: {run.check_name}"

    message = run.alert_message or run.llm_response or "Alert triggered"

    # Add status emoji to tags
    all_tags = list(tags or [])
    if run.status.value == "alert":
        all_tags.insert(0, "warning")
    elif run.status.value == "error":
        all_tags.insert(0, "x")

    try:
        return client.send(
            message=message,
            title=title,
            priority=priority,
            tags=all_tags if all_tags else None,
        )
    except NotifyError:
        return False


