"""Retry utilities for network operations."""

import logging
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_on_error(
    func: Callable[[], T],
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> T:
    """Retry a function on failure with exponential backoff.

    Args:
        func: Function to call.
        max_attempts: Maximum number of attempts.
        delay: Initial delay between attempts in seconds.
        backoff: Multiplier for delay after each failure.
        exceptions: Exception types to catch and retry.

    Returns:
        Result of the function.

    Raises:
        The last exception if all attempts fail.
    """
    last_exception: Exception | None = None
    current_delay = delay

    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except exceptions as e:
            last_exception = e
            if attempt < max_attempts:
                logger.debug(
                    "Attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt,
                    max_attempts,
                    e,
                    current_delay,
                )
                time.sleep(current_delay)
                current_delay *= backoff
            else:
                logger.debug(
                    "Attempt %d/%d failed: %s. No more retries.",
                    attempt,
                    max_attempts,
                    e,
                )

    # Should never reach here, but satisfy type checker
    if last_exception:
        raise last_exception
    raise RuntimeError("Retry loop exited without result or exception")
