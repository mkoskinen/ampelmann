"""Data models for Ampelmann."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class CheckStatus(Enum):
    """Status of a check run."""

    OK = "ok"
    ALERT = "alert"
    ERROR = "error"


class NotifyPriority(Enum):
    """ntfy notification priority levels."""

    MIN = "min"
    LOW = "low"
    DEFAULT = "default"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class LLMConfig:
    """LLM configuration for a check."""

    prompt: str
    model: str | None = None  # Single model (legacy) or analysis model
    timeout: int | None = None
    history_context: int | None = None  # Override default history context for this check
    triage_model: str | None = None  # Fast model for initial OK/ALERT decision
    analysis_model: str | None = None  # Detailed model for explaining issues
    skip_analysis: bool = False  # Skip detailed analysis, just use triage result


@dataclass
class NotifyConfig:
    """Notification configuration for a check."""

    priority: NotifyPriority = NotifyPriority.DEFAULT
    tags: list[str] = field(default_factory=list)


@dataclass
class Check:
    """A check definition loaded from TOML."""

    name: str
    command: str
    schedule: str
    description: str = ""
    enabled: bool = True
    timeout: int = 30
    sudo: bool = False
    llm: LLMConfig = field(default_factory=lambda: LLMConfig(prompt=""))
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_path: Path | None = None) -> "Check":
        """Create a Check from a dictionary (parsed TOML)."""
        llm_data = data.get("llm", {})
        llm_config = LLMConfig(
            prompt=llm_data.get("prompt", ""),
            model=llm_data.get("model"),
            timeout=llm_data.get("timeout"),
            history_context=llm_data.get("history_context"),
            triage_model=llm_data.get("triage_model"),
            analysis_model=llm_data.get("analysis_model"),
            skip_analysis=llm_data.get("skip_analysis", False),
        )

        notify_data = data.get("notify", {})
        priority_str = notify_data.get("priority", "default")
        notify_config = NotifyConfig(
            priority=NotifyPriority(priority_str),
            tags=notify_data.get("tags", []),
        )

        return cls(
            name=data["name"],
            command=data["command"],
            schedule=data["schedule"],
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
            timeout=data.get("timeout", 30),
            sudo=data.get("sudo", False),
            llm=llm_config,
            notify=notify_config,
            source_path=source_path,
        )


@dataclass
class CheckRun:
    """Result of running a check."""

    check_name: str
    run_at: datetime
    command_output: str
    command_exit_code: int
    command_duration_ms: int
    status: CheckStatus
    llm_model: str | None = None
    llm_response: str | None = None
    llm_duration_ms: int | None = None
    alert_sent: bool = False
    alert_message: str | None = None
    id: int | None = None


@dataclass
class CheckState:
    """Persistent state for a check."""

    check_name: str
    last_run_at: datetime | None = None
    last_status: CheckStatus | None = None
    config_hash: str | None = None


@dataclass
class OllamaConfig:
    """Ollama server configuration."""

    host: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    timeout: int = 120


@dataclass
class NtfyConfig:
    """ntfy notification configuration."""

    url: str = "https://ntfy.sh"
    topic: str = "ampelmann"
    token: str | None = None


@dataclass
class DatabaseConfig:
    """Database configuration."""

    path: Path = field(default_factory=lambda: Path("/var/lib/ampelmann/ampelmann.db"))


@dataclass
class LoggingConfig:
    """Logging configuration."""

    level: str = "INFO"
    path: Path | None = None


@dataclass
class DashboardConfig:
    """Dashboard output configuration."""

    output_dir: Path = field(default_factory=lambda: Path("/srv/site/ampelmann/www/data"))
    history_hours: int = 48
    stats_days: int = 7
    check_history_count: int = 50
    auto_update: bool = False


@dataclass
class DefaultsConfig:
    """Default behavior configuration."""

    alert_on_check_error: bool = True
    alert_on_llm_error: bool = True
    retain_days: int = 90
    analyze_errors: bool = True
    error_model: str | None = None  # Use a different model for error analysis
    default_history_context: int = 3  # Default previous runs to include for LLM context


@dataclass
class PerformanceConfig:
    """Performance threshold configuration."""

    llm_slow_threshold: int = 60
    check_slow_threshold: int = 30


@dataclass
class Config:
    """Main application configuration."""

    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    checks_dir: Path = field(default_factory=lambda: Path("/etc/ampelmann/checks.d"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        """Create Config from a dictionary (parsed TOML)."""
        ollama_data = data.get("ollama", {})
        ollama = OllamaConfig(
            host=ollama_data.get("host", "http://localhost:11434"),
            model=ollama_data.get("model", "qwen2.5:7b"),
            timeout=ollama_data.get("timeout", 120),
        )

        ntfy_data = data.get("ntfy", {})
        ntfy = NtfyConfig(
            url=ntfy_data.get("url", "https://ntfy.sh"),
            topic=ntfy_data.get("topic", "ampelmann"),
            token=ntfy_data.get("token"),
        )

        db_data = data.get("database", {})
        database = DatabaseConfig(
            path=Path(db_data.get("path", "/var/lib/ampelmann/ampelmann.db")),
        )

        log_data = data.get("logging", {})
        logging_cfg = LoggingConfig(
            level=log_data.get("level", "INFO"),
            path=Path(log_data["path"]) if log_data.get("path") else None,
        )

        dash_data = data.get("dashboard", {})
        dashboard = DashboardConfig(
            output_dir=Path(dash_data.get("output_dir", "/srv/site/ampelmann/www/data")),
            history_hours=dash_data.get("history_hours", 48),
            stats_days=dash_data.get("stats_days", 7),
            check_history_count=dash_data.get("check_history_count", 50),
            auto_update=dash_data.get("auto_update", False),
        )

        defaults_data = data.get("defaults", {})
        defaults = DefaultsConfig(
            alert_on_check_error=defaults_data.get("alert_on_check_error", True),
            alert_on_llm_error=defaults_data.get("alert_on_llm_error", True),
            retain_days=defaults_data.get("retain_days", 90),
            analyze_errors=defaults_data.get("analyze_errors", True),
            error_model=defaults_data.get("error_model"),
            default_history_context=defaults_data.get("default_history_context", 3),
        )

        perf_data = data.get("performance", {})
        performance = PerformanceConfig(
            llm_slow_threshold=perf_data.get("llm_slow_threshold", 60),
            check_slow_threshold=perf_data.get("check_slow_threshold", 30),
        )

        checks_dir = Path(data.get("checks_dir", "/etc/ampelmann/checks.d"))

        return cls(
            ollama=ollama,
            ntfy=ntfy,
            database=database,
            logging=logging_cfg,
            dashboard=dashboard,
            defaults=defaults,
            performance=performance,
            checks_dir=checks_dir,
        )
