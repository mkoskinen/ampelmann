"""Ollama LLM client for Ampelmann."""

import httpx

from ampelmann.models import Check, CheckRun, CheckStatus, Config


class LLMError(Exception):
    """Error communicating with LLM."""


class OllamaClient:
    """Client for Ollama API."""

    def __init__(self, host: str = "http://localhost:11434", timeout: int = 120) -> None:
        """Initialize Ollama client.

        Args:
            host: Ollama API host URL.
            timeout: Request timeout in seconds.
        """
        self.host = host.rstrip("/")
        self.timeout = timeout

    def generate(self, model: str, prompt: str, timeout: int | None = None) -> str:
        """Generate a response from the LLM.

        Args:
            model: Model name to use.
            prompt: The prompt to send.
            timeout: Request timeout (overrides default).

        Returns:
            Generated response text.

        Raises:
            LLMError: If the request fails.
        """
        url = f"{self.host}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }

        try:
            with httpx.Client(timeout=timeout or self.timeout) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                result: str = data.get("response", "")
                return result.strip()

        except httpx.TimeoutException as e:
            raise LLMError(f"LLM request timed out after {timeout or self.timeout}s") from e
        except httpx.HTTPStatusError as e:
            raise LLMError(f"LLM request failed: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise LLMError(f"LLM connection error: {e}") from e
        except Exception as e:
            raise LLMError(f"LLM error: {e}") from e

    def is_available(self) -> bool:
        """Check if Ollama is available.

        Returns:
            True if Ollama is responding.
        """
        try:
            with httpx.Client(timeout=5) as client:
                response = client.get(f"{self.host}/api/tags")
                return response.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """List available models.

        Returns:
            List of model names.

        Raises:
            LLMError: If the request fails.
        """
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(f"{self.host}/api/tags")
                response.raise_for_status()
                data = response.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            raise LLMError(f"Failed to list models: {e}") from e


def format_history(history: list[CheckRun]) -> str:
    """Format historical runs for LLM context.

    Args:
        history: List of previous CheckRun objects (newest first).

    Returns:
        Formatted history string.
    """
    if not history:
        return ""

    lines = ["--- Previous Runs (newest first) ---"]
    for run in history:
        timestamp = run.run_at.strftime("%Y-%m-%d %H:%M")
        status = run.status.value.upper()
        lines.append(f"\n[{timestamp}] Status: {status}")
        if run.command_output:
            # Truncate long outputs
            output = run.command_output[:500]
            if len(run.command_output) > 500:
                output += "\n... (truncated)"
            lines.append(output)
        if run.alert_message:
            lines.append(f"Alert: {run.alert_message}")
    lines.append("--- End Previous Runs ---\n")
    return "\n".join(lines)


def build_prompt(check: Check, command_output: str, history: list[CheckRun] | None = None) -> str:
    """Build the full prompt for LLM analysis.

    Args:
        check: The check definition.
        command_output: Output from the command.
        history: Optional list of previous runs for context.

    Returns:
        Complete prompt string.
    """
    history_section = format_history(history) if history else ""

    return f"""{check.llm.prompt}
{history_section}
--- Current Output ---
{command_output}
--- End Output ---"""


TRIAGE_PROMPT = """Quickly assess this system check output. Respond with ONLY one word:
- "OK" if everything looks normal
- "ALERT" if there's any issue that needs attention

{check_prompt}
{history_section}
--- Output ---
{output}
--- End Output ---"""


def build_triage_prompt(
    check: Check, command_output: str, history: list[CheckRun] | None = None
) -> str:
    """Build a simple triage prompt for fast OK/ALERT decision."""
    history_section = format_history(history) if history else ""
    return TRIAGE_PROMPT.format(
        check_prompt=check.llm.prompt,
        output=command_output,
        history_section=history_section,
    )


ERROR_ANALYSIS_PROMPT = """A system monitoring command failed. Analyze the error and explain briefly what went wrong and how to fix it.

Command: {command}
Exit code: {exit_code}
{history_section}
--- Error Output ---
{output}
--- End Output ---

Respond with a brief (1-2 sentence) explanation of the error and suggested fix."""


def build_error_prompt(
    check: Check, exit_code: int, output: str, history: list[CheckRun] | None = None
) -> str:
    """Build prompt for analyzing a command error.

    Args:
        check: The check definition.
        exit_code: Command exit code.
        output: Command output (stdout/stderr).
        history: Optional previous runs for context.

    Returns:
        Complete prompt string.
    """
    history_section = format_history(history) if history else ""
    return ERROR_ANALYSIS_PROMPT.format(
        command=check.command,
        exit_code=exit_code,
        output=output or "(no output)",
        history_section=history_section,
    )


def analyze_output(
    client: OllamaClient,
    check: Check,
    run: CheckRun,
    config: Config,
    history: list[CheckRun] | None = None,
) -> CheckRun:
    """Analyze check output with LLM and update the run.

    Supports two-stage analysis:
    1. Triage: Fast model for OK/ALERT decision (if triage_model configured)
    2. Analysis: Detailed model for explaining issues (if not skip_analysis)

    Args:
        client: Ollama client.
        check: The check definition.
        run: CheckRun with command output.
        config: Application config.
        history: Optional previous runs for context.

    Returns:
        Updated CheckRun with LLM analysis.
    """
    import time

    timeout = check.llm.timeout or config.ollama.timeout
    default_model = config.ollama.model

    # Two-stage analysis if triage_model is configured
    if check.llm.triage_model:
        return _two_stage_analysis(client, check, run, config, history, timeout, default_model)

    # Single-stage analysis (legacy behavior)
    model = check.llm.model or default_model
    prompt = build_prompt(check, run.command_output, history)

    start = time.monotonic()
    try:
        response = client.generate(model, prompt, timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)

        run.llm_model = model
        run.llm_response = response
        run.llm_duration_ms = duration_ms

        response_lower = response.lower().strip()
        if response_lower == "ok" or response_lower.startswith("ok."):
            run.status = CheckStatus.OK
        else:
            run.status = CheckStatus.ALERT
            run.alert_message = response

    except LLMError as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        run.llm_model = model
        run.llm_response = f"LLM Error: {e}"
        run.llm_duration_ms = duration_ms

        if config.defaults.alert_on_llm_error:
            run.status = CheckStatus.ERROR
            run.alert_message = f"LLM analysis failed: {e}"

    return run


def _two_stage_analysis(
    client: OllamaClient,
    check: Check,
    run: CheckRun,
    config: Config,
    history: list[CheckRun] | None,
    timeout: int,
    default_model: str,
) -> CheckRun:
    """Two-stage analysis: triage then detailed analysis if needed."""
    import time

    # triage_model is guaranteed to be set (caller checks this)
    triage_model: str = check.llm.triage_model  # type: ignore[assignment]
    analysis_model = check.llm.analysis_model or check.llm.model or default_model

    # Stage 1: Triage with fast model
    triage_prompt = build_triage_prompt(check, run.command_output, history)
    start = time.monotonic()

    try:
        triage_response = client.generate(triage_model, triage_prompt, timeout=timeout)
        triage_duration = int((time.monotonic() - start) * 1000)

        triage_lower = triage_response.lower().strip()
        is_ok = triage_lower == "ok" or triage_lower.startswith("ok")

        if is_ok:
            # All good - no further analysis needed
            run.llm_model = triage_model
            run.llm_response = triage_response
            run.llm_duration_ms = triage_duration
            run.status = CheckStatus.OK
            return run

        # Stage 2: Detailed analysis (unless skip_analysis)
        if check.llm.skip_analysis:
            run.llm_model = triage_model
            run.llm_response = triage_response
            run.llm_duration_ms = triage_duration
            run.status = CheckStatus.ALERT
            run.alert_message = "Issue detected (detailed analysis skipped)"
            return run

        # Get detailed analysis from analysis model
        analysis_prompt = build_prompt(check, run.command_output, history)
        analysis_start = time.monotonic()
        analysis_response = client.generate(analysis_model, analysis_prompt, timeout=timeout)
        analysis_duration = int((time.monotonic() - analysis_start) * 1000)

        run.llm_model = f"{triage_model}+{analysis_model}"
        run.llm_response = analysis_response
        run.llm_duration_ms = triage_duration + analysis_duration
        run.status = CheckStatus.ALERT
        run.alert_message = analysis_response

    except LLMError as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        run.llm_model = triage_model
        run.llm_response = f"LLM Error: {e}"
        run.llm_duration_ms = duration_ms

        if config.defaults.alert_on_llm_error:
            run.status = CheckStatus.ERROR
            run.alert_message = f"LLM analysis failed: {e}"

    return run


def analyze_error(
    client: OllamaClient,
    check: Check,
    run: CheckRun,
    config: Config,
    history: list[CheckRun] | None = None,
) -> CheckRun:
    """Analyze a command error with LLM and update the run.

    Args:
        client: Ollama client.
        check: The check definition.
        run: CheckRun with failed command.
        config: Application config.
        history: Optional previous runs for context.

    Returns:
        Updated CheckRun with error analysis.
    """
    import time

    # Use error_model if configured, otherwise fall back to check/default model
    model = config.defaults.error_model or check.llm.model or config.ollama.model
    timeout = check.llm.timeout or config.ollama.timeout

    prompt = build_error_prompt(check, run.command_exit_code, run.command_output, history)

    start = time.monotonic()
    try:
        response = client.generate(model, prompt, timeout=timeout)
        duration_ms = int((time.monotonic() - start) * 1000)

        run.llm_model = model
        run.llm_response = response
        run.llm_duration_ms = duration_ms
        run.status = CheckStatus.ERROR
        run.alert_message = response

    except LLMError as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        run.llm_model = model
        run.llm_response = f"LLM Error: {e}"
        run.llm_duration_ms = duration_ms
        run.status = CheckStatus.ERROR
        run.alert_message = f"Command failed (exit {run.command_exit_code})"

    return run
