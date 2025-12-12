"""Configuration loading for Ampelmann."""

import itertools
import re
import tomllib
from pathlib import Path
from typing import Any

from ampelmann.models import Check, Config

DEFAULT_CONFIG_PATHS = [
    Path("/etc/ampelmann/config.toml"),
    Path.home() / ".config/ampelmann/config.toml",
    Path("config.toml"),
]


class ConfigError(Exception):
    """Configuration error."""


def load_config(path: Path | None = None) -> Config:
    """Load the main configuration file.

    Args:
        path: Explicit path to config file. If None, searches default locations.

    Returns:
        Loaded Config object.

    Raises:
        ConfigError: If no config file found or parsing fails.
    """
    config_path: Path
    if path is not None:
        config_path = path
        if not config_path.exists():
            raise ConfigError(f"Config file not found: {config_path}")
    else:
        found_path: Path | None = None
        for candidate in DEFAULT_CONFIG_PATHS:
            if candidate.exists():
                found_path = candidate
                break
        if found_path is None:
            # Return defaults if no config file exists
            return Config()
        config_path = found_path

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {config_path}: {e}") from e

    return Config.from_dict(data)


def load_check(path: Path) -> Check:
    """Load a single check definition from a TOML file.

    Args:
        path: Path to the check TOML file.

    Returns:
        Loaded Check object.

    Raises:
        ConfigError: If file not found or parsing fails.
    """
    if not path.exists():
        raise ConfigError(f"Check file not found: {path}")

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {path}: {e}") from e

    required = ["name", "command", "schedule"]
    missing = [k for k in required if k not in data]
    if missing:
        raise ConfigError(f"Missing required fields in {path}: {', '.join(missing)}")

    return Check.from_dict(data, source_path=path)


def load_checks_from_file(path: Path) -> list[Check]:
    """Load check(s) from a TOML file, expanding matrix if present.

    Args:
        path: Path to the check TOML file.

    Returns:
        List of Check objects (1 for normal, N for matrix checks).

    Raises:
        ConfigError: If file not found or parsing fails.
    """
    if not path.exists():
        raise ConfigError(f"Check file not found: {path}")

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {path}: {e}") from e

    required = ["name", "command", "schedule"]
    missing = [k for k in required if k not in data]
    if missing:
        raise ConfigError(f"Missing required fields in {path}: {', '.join(missing)}")

    # Check for matrix expansion
    if "matrix" in data:
        return _expand_matrix(data, path)

    return [Check.from_dict(data, source_path=path)]


def _expand_matrix(data: dict[str, Any], source_path: Path) -> list[Check]:
    """Expand a check definition with matrix into multiple checks.

    Args:
        data: Parsed TOML data with matrix section.
        source_path: Original file path.

    Returns:
        List of expanded Check objects.
    """
    matrix = data.pop("matrix")

    if not isinstance(matrix, dict):
        raise ConfigError(f"matrix must be a table in {source_path}")

    if not matrix:
        raise ConfigError(f"matrix cannot be empty in {source_path}")

    # Validate matrix values are lists
    for key, values in matrix.items():
        if not isinstance(values, list):
            raise ConfigError(f"matrix.{key} must be a list in {source_path}")
        if not values:
            raise ConfigError(f"matrix.{key} cannot be empty in {source_path}")

    # Generate all combinations
    keys = list(matrix.keys())
    value_lists = [matrix[k] for k in keys]
    combinations = list(itertools.product(*value_lists))

    checks: list[Check] = []
    for combo in combinations:
        # Create variable mapping
        variables = dict(zip(keys, combo, strict=True))

        # Deep copy and substitute
        expanded_data = _substitute_variables(data, variables)

        checks.append(Check.from_dict(expanded_data, source_path=source_path))

    return checks


def _substitute_variables(data: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
    """Recursively substitute ${var} in string values.

    Args:
        data: Dictionary to process.
        variables: Variable name to value mapping.

    Returns:
        New dictionary with substitutions applied.
    """
    result: dict[str, Any] = {}

    for key, value in data.items():
        if isinstance(value, str):
            result[key] = _substitute_string(value, variables)
        elif isinstance(value, dict):
            result[key] = _substitute_variables(value, variables)
        elif isinstance(value, list):
            result[key] = [
                _substitute_string(v, variables) if isinstance(v, str) else v
                for v in value
            ]
        else:
            result[key] = value

    return result


def _substitute_string(text: str, variables: dict[str, Any]) -> str:
    """Substitute ${var} patterns in a string.

    Args:
        text: String with potential ${var} patterns.
        variables: Variable name to value mapping.

    Returns:
        String with substitutions applied.
    """
    def replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name in variables:
            return str(variables[var_name])
        return match.group(0)  # Keep original if not found

    return re.sub(r"\$\{(\w+)\}", replace, text)


def load_checks(checks_dir: Path) -> list[Check]:
    """Load all check definitions from a directory.

    Args:
        checks_dir: Directory containing check TOML files.

    Returns:
        List of loaded Check objects.

    Raises:
        ConfigError: If directory doesn't exist.
    """
    if not checks_dir.exists():
        raise ConfigError(f"Checks directory not found: {checks_dir}")

    if not checks_dir.is_dir():
        raise ConfigError(f"Not a directory: {checks_dir}")

    checks: list[Check] = []
    for path in sorted(checks_dir.glob("*.toml")):
        expanded = load_checks_from_file(path)
        checks.extend(expanded)

    return checks


def validate_check(check: Check) -> list[str]:
    """Validate a check definition.

    Args:
        check: Check to validate.

    Returns:
        List of validation error messages (empty if valid).
    """
    errors: list[str] = []

    if not check.name:
        errors.append("name is required")

    if not check.command:
        errors.append("command is required")

    if not check.schedule:
        errors.append("schedule is required")

    if check.timeout < 1:
        errors.append("timeout must be positive")

    if not check.llm.prompt:
        errors.append("llm.prompt is required")

    # Validate cron schedule
    try:
        from croniter import croniter  # type: ignore[import-untyped]
        from croniter.croniter import CroniterBadCronError  # type: ignore[import-untyped]

        croniter(check.schedule)
    except (ValueError, KeyError, CroniterBadCronError) as e:
        errors.append(f"invalid schedule '{check.schedule}': {e}")

    return errors
