"""Check execution for Ampelmann."""

import subprocess
import time
from datetime import datetime

from ampelmann.models import Check, CheckRun, CheckStatus


class RunnerError(Exception):
    """Error during check execution."""


def run_command(command: str, timeout: int = 30) -> tuple[str, int, int]:
    """Execute a shell command and capture output.

    Args:
        command: Shell command to execute.
        timeout: Maximum execution time in seconds.

    Returns:
        Tuple of (output, exit_code, duration_ms).

    Raises:
        RunnerError: If command execution fails unexpectedly.
    """
    start = time.monotonic()

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        # Combine stdout and stderr
        output = result.stdout
        if result.stderr:
            if output:
                output += "\n--- stderr ---\n"
            output += result.stderr

        return output.strip(), result.returncode, duration_ms

    except subprocess.TimeoutExpired as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        output = ""
        if e.stdout:
            output = e.stdout.decode() if isinstance(e.stdout, bytes) else e.stdout
        if e.stderr:
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr
            if output:
                output += "\n--- stderr ---\n"
            output += stderr
        output += f"\n[Command timed out after {timeout}s]"
        return output.strip(), -1, duration_ms

    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        raise RunnerError(f"Command execution failed: {e}") from e


def run_check(check: Check) -> CheckRun:
    """Execute a check and return the result.

    This only runs the command - LLM analysis happens separately.

    Args:
        check: The check to execute.

    Returns:
        CheckRun with command output (no LLM analysis yet).
    """
    run_at = datetime.now()

    # Prepend sudo if check requires elevated privileges
    command = f"sudo {check.command}" if check.sudo else check.command

    try:
        output, exit_code, duration_ms = run_command(
            command,
            timeout=check.timeout,
        )

        # Determine initial status based on exit code (may be changed after LLM analysis)
        status = CheckStatus.OK if exit_code == 0 else CheckStatus.ERROR

        return CheckRun(
            check_name=check.name,
            run_at=run_at,
            command_output=output,
            command_exit_code=exit_code,
            command_duration_ms=duration_ms,
            status=status,
        )

    except RunnerError as e:
        return CheckRun(
            check_name=check.name,
            run_at=run_at,
            command_output=str(e),
            command_exit_code=-1,
            command_duration_ms=0,
            status=CheckStatus.ERROR,
        )


def truncate_output(output: str, max_chars: int = 50000) -> str:
    """Truncate command output if too long for LLM.

    Args:
        output: Command output.
        max_chars: Maximum characters to keep.

    Returns:
        Truncated output with indicator if truncated.
    """
    if len(output) <= max_chars:
        return output

    # Keep first and last portions
    keep = max_chars // 2
    truncated = (
        output[:keep]
        + f"\n\n[... truncated {len(output) - max_chars} characters ...]\n\n"
        + output[-keep:]
    )
    return truncated
