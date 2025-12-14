"""Logging configuration for Ampelmann."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from ampelmann.models import LoggingConfig


def setup_logging(config: LoggingConfig) -> None:
    """Configure logging for the application.

    Args:
        config: Logging configuration.
    """
    # Get the root logger for ampelmann
    logger = logging.getLogger("ampelmann")
    logger.setLevel(getattr(logging, config.level.upper(), logging.INFO))

    # Clear existing handlers
    logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler if path is configured
    if config.path:
        try:
            # Ensure directory exists
            config.path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = RotatingFileHandler(
                config.path,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as e:
            # Fall back to stderr if file logging fails
            sys.stderr.write(f"Warning: Could not set up file logging: {e}\n")

    # Also log errors to stderr for visibility
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a module.

    Args:
        name: Module name (usually __name__).

    Returns:
        Logger instance.
    """
    return logging.getLogger(name)
