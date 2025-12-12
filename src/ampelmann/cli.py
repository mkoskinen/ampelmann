"""Command-line interface for Ampelmann."""

from datetime import datetime, timedelta
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from ampelmann import __version__
from ampelmann.config import ConfigError, load_checks, load_config, validate_check
from ampelmann.dashboard import write_dashboard
from ampelmann.db import Database
from ampelmann.llm import OllamaClient, analyze_error, analyze_output
from ampelmann.models import CheckState, CheckStatus, Config
from ampelmann.notify import NtfyClient, send_alert
from ampelmann.runner import run_check, truncate_output
from ampelmann.scheduler import get_due_checks, parse_schedule

console = Console()

STATUS_COLORS = {
    CheckStatus.OK: "green",
    CheckStatus.ALERT: "yellow",
    CheckStatus.ERROR: "red",
}


def get_config(config_path: str | None) -> Config:
    """Load configuration."""
    try:
        path = Path(config_path) if config_path else None
        return load_config(path)
    except ConfigError as e:
        console.print(f"[red]Configuration error:[/red] {e}")
        raise SystemExit(1) from None


def get_db(config: Config) -> Database:
    """Get database instance."""
    db = Database(config.database.path)
    db.init_schema()
    return db


@click.group()
@click.version_option(version=__version__)
@click.option("-c", "--config", "config_path", help="Path to config file")
@click.pass_context
def main(ctx: click.Context, config_path: str | None) -> None:
    """Ampelmann - LLM-powered system alert filter."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


@main.command()
@click.option("--all", "run_all", is_flag=True, help="Run all checks regardless of schedule")
@click.option("--force", is_flag=True, help="Force run even if recently run")
@click.option("--dry-run", is_flag=True, help="Show what would run without executing")
@click.option("--no-notify", is_flag=True, help="Skip sending notifications")
@click.argument("check_name", required=False)
@click.pass_context
def run(
    ctx: click.Context,
    run_all: bool,
    force: bool,
    dry_run: bool,
    no_notify: bool,
    check_name: str | None,
) -> None:
    """Run checks."""
    config = get_config(ctx.obj.get("config_path"))
    db = get_db(config)

    # Load all checks (includes matrix expansion)
    try:
        checks = load_checks(config.checks_dir)
    except ConfigError as e:
        console.print(f"[red]Error loading checks:[/red] {e}")
        raise SystemExit(1) from None

    if not checks:
        console.print("[yellow]No checks found[/yellow]")
        return

    # Filter to specific check if requested
    if check_name:
        checks = [c for c in checks if c.name == check_name]
        if not checks:
            console.print(f"[red]Check not found:[/red] {check_name}")
            raise SystemExit(1) from None

    # Determine which checks to run
    if run_all or force or check_name:
        due_checks = [c for c in checks if c.enabled]
    else:
        # Get last run times from database
        last_runs: dict[str, datetime | None] = {}
        for check in checks:
            state = db.get_state(check.name)
            last_runs[check.name] = state.last_run_at if state else None
        due_checks = get_due_checks(checks, last_runs)

    if not due_checks:
        console.print("[green]No checks due[/green]")
        return

    if dry_run:
        console.print("[yellow]Dry run - would execute:[/yellow]")
        for check in due_checks:
            console.print(f"  - {check.name}")
        return

    # Initialize clients
    ollama = OllamaClient(host=config.ollama.host, timeout=config.ollama.timeout)
    ntfy = NtfyClient(url=config.ntfy.url, topic=config.ntfy.topic, token=config.ntfy.token)

    # Run checks
    for check in due_checks:
        console.print(f"[blue]Running:[/blue] {check.name}")

        # Execute command
        check_run = run_check(check)
        console.print(f"  Command: exit={check_run.command_exit_code} ({check_run.command_duration_ms}ms)")

        # Analyze with LLM
        output = truncate_output(check_run.command_output)
        check_run.command_output = output  # Store truncated version

        # Fetch history for LLM context (check-specific or global default)
        history = None
        history_count = check.llm.history_context if check.llm.history_context is not None else config.defaults.default_history_context
        if history_count > 0:
            history = db.get_runs(check_name=check.name, limit=history_count)

        if check_run.command_exit_code == 0:
            # Success: analyze output normally
            check_run = analyze_output(ollama, check, check_run, config, history)
            console.print(f"  LLM: {check_run.llm_model} ({check_run.llm_duration_ms}ms)")
        elif config.defaults.analyze_errors:
            # Error: ask LLM to explain the failure
            check_run = analyze_error(ollama, check, check_run, config, history)
            console.print(f"  LLM (error): {check_run.llm_model} ({check_run.llm_duration_ms}ms)")
        else:
            # Error analysis disabled - just set basic error status
            check_run.status = CheckStatus.ERROR
            check_run.alert_message = f"Command failed with exit code {check_run.command_exit_code}"

        # Show result
        status_color = STATUS_COLORS[check_run.status]
        console.print(f"  Status: [{status_color}]{check_run.status.value}[/{status_color}]")

        # Send notification if needed
        if check_run.status in (CheckStatus.ALERT, CheckStatus.ERROR) and not no_notify:
            sent = send_alert(ntfy, check_run, tags=check.notify.tags, priority=check.notify.priority)
            check_run.alert_sent = sent
            if sent:
                console.print("  [yellow]Alert sent[/yellow]")
            else:
                console.print("  [red]Alert failed[/red]")

        # Save to database
        db.save_run(check_run)
        db.update_state(CheckState(
            check_name=check.name,
            last_run_at=check_run.run_at,
            last_status=check_run.status,
        ))

    console.print(f"[green]Completed {len(due_checks)} check(s)[/green]")

    # Auto-update dashboard if configured
    if config.dashboard.auto_update:
        write_dashboard(config, load_checks(config.checks_dir), db)
        console.print("[dim]Dashboard updated[/dim]")


@main.command("list")
@click.pass_context
def list_checks(ctx: click.Context) -> None:
    """List all checks."""
    config = get_config(ctx.obj.get("config_path"))
    db = get_db(config)

    try:
        checks = load_checks(config.checks_dir)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1) from None

    if not checks:
        console.print("[yellow]No checks found[/yellow]")
        return

    table = Table(title="Checks")
    table.add_column("Name", style="cyan")
    table.add_column("Enabled")
    table.add_column("Schedule")
    table.add_column("Last Status")
    table.add_column("Last Run")

    for check in checks:
        enabled = "[green]yes[/green]" if check.enabled else "[dim]no[/dim]"
        schedule = parse_schedule(check.schedule)

        state = db.get_state(check.name)
        if state and state.last_status:
            status_color = STATUS_COLORS[state.last_status]
            last_status = f"[{status_color}]{state.last_status.value}[/{status_color}]"
            last_run = state.last_run_at.strftime("%Y-%m-%d %H:%M") if state.last_run_at else "-"
        else:
            last_status = "[dim]-[/dim]"
            last_run = "[dim]never[/dim]"

        table.add_row(check.name, enabled, schedule, last_status, last_run)

    console.print(table)


def _format_duration(ms: int | None) -> str:
    """Format milliseconds as human-readable duration."""
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    return f"{minutes}m{seconds % 60}s"


def _format_time_ago(dt: datetime | None) -> str:
    """Format datetime as relative time (e.g., '2h ago')."""
    if dt is None:
        return "never"
    now = datetime.now()
    delta = now - dt

    if delta < timedelta(minutes=1):
        return "just now"
    if delta < timedelta(hours=1):
        mins = int(delta.total_seconds() // 60)
        return f"{mins}m ago"
    if delta < timedelta(days=1):
        hours = int(delta.total_seconds() // 3600)
        return f"{hours}h ago"
    days = delta.days
    return f"{days}d ago"


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show compact traffic-light status of all checks."""
    config = get_config(ctx.obj.get("config_path"))
    db = get_db(config)

    try:
        checks = load_checks(config.checks_dir)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1) from None

    if not checks:
        console.print("[yellow]No checks found[/yellow]")
        return

    # Header
    console.print(f"[bold]AMPELMANN[/bold] [dim]v{__version__}[/dim]")
    console.print("[dim]" + "─" * 48 + "[/dim]")

    # Get states for all checks
    for check in sorted(checks, key=lambda c: c.name):
        if not check.enabled:
            continue

        state = db.get_state(check.name)
        last_run = db.get_runs(check_name=check.name, limit=1)

        # Determine status and color
        if state and state.last_status:
            if state.last_status == CheckStatus.OK:
                indicator = "[green]●[/green]"
                status_raw = "ok"
                status_color = "green"
            elif state.last_status == CheckStatus.ALERT:
                indicator = "[yellow]◉[/yellow]"
                status_raw = "ALERT"
                status_color = "yellow"
            else:  # ERROR
                indicator = "[red]◉[/red]"
                status_raw = "ERROR"
                status_color = "red"
        else:
            indicator = "[dim]○[/dim]"
            status_raw = "-"
            status_color = "dim"

        # Time ago
        time_ago = _format_time_ago(state.last_run_at if state else None)

        # Duration (command + LLM)
        if last_run:
            total_ms = (last_run[0].command_duration_ms or 0) + (last_run[0].llm_duration_ms or 0)
            duration = _format_duration(total_ms)
        else:
            duration = "-"

        # Format line - pad before adding color
        name_col = check.name[:18].ljust(18)
        status_col = f"[{status_color}]{status_raw:<5}[/{status_color}]"
        time_col = f"{time_ago:>10}"

        console.print(f" {indicator} {name_col} {status_col} {time_col}  {duration:>6}")

    console.print("[dim]" + "─" * 48 + "[/dim]")


@main.command()
@click.argument("name")
@click.pass_context
def show(ctx: click.Context, name: str) -> None:
    """Show check details."""
    config = get_config(ctx.obj.get("config_path"))
    db = get_db(config)

    try:
        checks = load_checks(config.checks_dir)
        check = next((c for c in checks if c.name == name), None)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1) from None

    if not check:
        console.print(f"[red]Check not found:[/red] {name}")
        raise SystemExit(1) from None

    console.print(f"[bold]{check.name}[/bold]")
    console.print(f"Description: {check.description or '-'}")
    console.print(f"Enabled: {'yes' if check.enabled else 'no'}")
    console.print(f"Schedule: {parse_schedule(check.schedule)} ({check.schedule})")
    console.print(f"Command: {check.command}")
    console.print(f"Timeout: {check.timeout}s")
    console.print(f"LLM Model: {check.llm.model or 'default'}")
    console.print()
    console.print("[bold]Prompt:[/bold]")
    console.print(check.llm.prompt)

    # Show recent history
    runs = db.get_runs(check_name=name, limit=5)
    if runs:
        console.print()
        console.print("[bold]Recent runs:[/bold]")
        for run in runs:
            status_color = STATUS_COLORS[run.status]
            console.print(
                f"  {run.run_at.strftime('%Y-%m-%d %H:%M')} "
                f"[{status_color}]{run.status.value}[/{status_color}]"
            )


@main.command()
@click.argument("name")
@click.option("--verbose", "-v", is_flag=True, help="Show full output")
@click.pass_context
def test(ctx: click.Context, name: str, verbose: bool) -> None:
    """Test a check without alerting."""
    config = get_config(ctx.obj.get("config_path"))
    db = get_db(config)

    try:
        checks = load_checks(config.checks_dir)
        check = next((c for c in checks if c.name == name), None)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1) from None

    if not check:
        console.print(f"[red]Check not found:[/red] {name}")
        raise SystemExit(1) from None

    console.print(f"[blue]Testing:[/blue] {check.name}")

    # Run command
    console.print("[dim]Executing command...[/dim]")
    check_run = run_check(check)

    console.print(f"Exit code: {check_run.command_exit_code}")
    console.print(f"Duration: {check_run.command_duration_ms}ms")

    if verbose:
        console.print()
        console.print("[bold]Command output:[/bold]")
        console.print(check_run.command_output or "[dim]<empty>[/dim]")

    # Analyze with LLM
    console.print()
    ollama = OllamaClient(host=config.ollama.host, timeout=config.ollama.timeout)
    output = truncate_output(check_run.command_output)
    check_run.command_output = output

    # Fetch history for LLM context (check-specific or global default)
    history = None
    history_count = check.llm.history_context if check.llm.history_context is not None else config.defaults.default_history_context
    if history_count > 0:
        history = db.get_runs(check_name=check.name, limit=history_count)

    if check_run.command_exit_code == 0:
        console.print("[dim]Analyzing with LLM...[/dim]")
        check_run = analyze_output(ollama, check, check_run, config, history)
    elif config.defaults.analyze_errors:
        console.print("[dim]Analyzing error with LLM...[/dim]")
        check_run = analyze_error(ollama, check, check_run, config, history)
    else:
        check_run.status = CheckStatus.ERROR
        check_run.alert_message = f"Command failed with exit code {check_run.command_exit_code}"

    if check_run.llm_model:
        console.print(f"Model: {check_run.llm_model}")
        console.print(f"Duration: {check_run.llm_duration_ms}ms")
        console.print()
        console.print("[bold]LLM response:[/bold]")
        console.print(check_run.llm_response or "[dim]<empty>[/dim]")

    status_color = STATUS_COLORS[check_run.status]
    console.print()
    console.print(f"Result: [{status_color}]{check_run.status.value}[/{status_color}]")
    if check_run.alert_message:
        console.print(f"Alert: {check_run.alert_message}")


@main.command()
@click.pass_context
def validate(ctx: click.Context) -> None:
    """Validate check configurations."""
    config = get_config(ctx.obj.get("config_path"))

    try:
        checks = load_checks(config.checks_dir)
    except ConfigError as e:
        console.print(f"[red]Error loading checks:[/red] {e}")
        raise SystemExit(1) from None

    if not checks:
        console.print("[yellow]No checks found[/yellow]")
        return

    has_errors = False
    for check in checks:
        errors = validate_check(check)
        if errors:
            has_errors = True
            console.print(f"[red]{check.name}:[/red]")
            for error in errors:
                console.print(f"  - {error}")
        else:
            console.print(f"[green]{check.name}:[/green] OK")

    if has_errors:
        raise SystemExit(1) from None


@main.command()
@click.option("--status", type=click.Choice(["ok", "alert", "error"]), help="Filter by status")
@click.option("--limit", default=20, help="Number of entries to show")
@click.pass_context
def history(ctx: click.Context, status: str | None, limit: int) -> None:
    """Show recent check history."""
    config = get_config(ctx.obj.get("config_path"))
    db = get_db(config)

    filter_status = CheckStatus(status) if status else None
    runs = db.get_runs(status=filter_status, limit=limit)

    if not runs:
        console.print("[dim]No history found[/dim]")
        return

    table = Table(title="Recent History")
    table.add_column("Time")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Duration")
    table.add_column("Alert")

    for run in runs:
        status_color = STATUS_COLORS[run.status]
        time_str = run.run_at.strftime("%Y-%m-%d %H:%M")
        status_str = f"[{status_color}]{run.status.value}[/{status_color}]"
        duration = f"{run.command_duration_ms}ms"
        if run.llm_duration_ms:
            duration += f" + {run.llm_duration_ms}ms"
        alert = run.alert_message[:40] + "..." if run.alert_message and len(run.alert_message) > 40 else (run.alert_message or "-")

        table.add_row(time_str, run.check_name, status_str, duration, alert)

    console.print(table)


@main.command()
@click.pass_context
def dashboard(ctx: click.Context) -> None:
    """Regenerate dashboard JSON files."""
    config = get_config(ctx.obj.get("config_path"))
    db = get_db(config)

    try:
        checks = load_checks(config.checks_dir)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1) from None

    write_dashboard(config, checks, db)
    console.print(f"[green]Dashboard written to:[/green] {config.dashboard.output_dir}")


@main.command()
@click.argument("message")
@click.option("--priority", type=click.Choice(["min", "low", "default", "high", "urgent"]), default="default")
@click.option("--tags", help="Comma-separated tags")
@click.pass_context
def alert(ctx: click.Context, message: str, priority: str, tags: str | None) -> None:
    """Send a manual alert notification."""
    config = get_config(ctx.obj.get("config_path"))
    ntfy = NtfyClient(url=config.ntfy.url, topic=config.ntfy.topic, token=config.ntfy.token)

    from ampelmann.models import NotifyPriority

    tag_list = tags.split(",") if tags else None
    prio = NotifyPriority(priority)

    try:
        ntfy.send(message=message, title="Ampelmann", priority=prio, tags=tag_list)
        console.print("[green]Alert sent[/green]")
    except Exception as e:
        console.print(f"[red]Failed to send alert:[/red] {e}")
        raise SystemExit(1) from None


@main.command()
@click.option("--days", default=90, help="Retain data for this many days")
@click.pass_context
def cleanup(ctx: click.Context, days: int) -> None:
    """Remove old data from database."""
    config = get_config(ctx.obj.get("config_path"))
    db = get_db(config)

    deleted = db.cleanup_old_runs(retain_days=days)
    console.print(f"[green]Deleted {deleted} old run(s)[/green]")


@main.command()
@click.argument("name")
@click.pass_context
def enable(ctx: click.Context, name: str) -> None:
    """Enable a check."""
    config = get_config(ctx.obj.get("config_path"))

    # Find check and its source file
    try:
        checks = load_checks(config.checks_dir)
        check = next((c for c in checks if c.name == name), None)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1) from None

    if not check:
        console.print(f"[red]Check not found:[/red] {name}")
        raise SystemExit(1) from None

    check_path = check.source_path
    if not check_path or not check_path.exists():
        console.print(f"[red]Source file not found for:[/red] {name}")
        raise SystemExit(1) from None

    # Read and modify the TOML file
    content = check_path.read_text()
    if "enabled = false" in content:
        content = content.replace("enabled = false", "enabled = true")
        check_path.write_text(content)
        console.print(f"[green]Enabled:[/green] {name}")
    elif "enabled = true" in content:
        console.print(f"[dim]Already enabled:[/dim] {name}")
    else:
        # Add enabled = true after name line
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("name"):
                lines.insert(i + 1, "enabled = true")
                break
        check_path.write_text("\n".join(lines))
        console.print(f"[green]Enabled:[/green] {name}")


@main.command()
@click.argument("name")
@click.pass_context
def disable(ctx: click.Context, name: str) -> None:
    """Disable a check."""
    config = get_config(ctx.obj.get("config_path"))

    # Find check and its source file
    try:
        checks = load_checks(config.checks_dir)
        check = next((c for c in checks if c.name == name), None)
    except ConfigError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1) from None

    if not check:
        console.print(f"[red]Check not found:[/red] {name}")
        raise SystemExit(1) from None

    check_path = check.source_path
    if not check_path or not check_path.exists():
        console.print(f"[red]Source file not found for:[/red] {name}")
        raise SystemExit(1) from None

    # Read and modify the TOML file
    content = check_path.read_text()
    if "enabled = true" in content:
        content = content.replace("enabled = true", "enabled = false")
        check_path.write_text(content)
        console.print(f"[yellow]Disabled:[/yellow] {name}")
    elif "enabled = false" in content:
        console.print(f"[dim]Already disabled:[/dim] {name}")
    else:
        # Add enabled = false after name line
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("name"):
                lines.insert(i + 1, "enabled = false")
                break
        check_path.write_text("\n".join(lines))
        console.print(f"[yellow]Disabled:[/yellow] {name}")


if __name__ == "__main__":
    main()
