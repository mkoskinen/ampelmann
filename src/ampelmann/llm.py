"""Ollama LLM client for Ampelmann."""

import logging

import httpx

from ampelmann.models import Check, CheckRun, CheckStatus, Config
from ampelmann.retry import retry_on_error

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Error communicating with LLM."""


class OllamaClient:
    """Client for Ollama API."""

    def __init__(
        self,
        host: str = "http://localhost:11434",
        timeout: int = 600,
        max_retries: int = 3,
    ) -> None:
        """Initialize Ollama client.

        Args:
            host: Ollama API host URL.
            timeout: Request timeout in seconds.
            max_retries: Maximum retry attempts for transient failures.
        """
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def generate(self, model: str, prompt: str, timeout: int | None = None) -> str:
        """Generate a response from the LLM.

        Args:
            model: Model name to use.
            prompt: The prompt to send.
            timeout: Request timeout (overrides default).

        Returns:
            Generated response text.

        Raises:
            LLMError: If the request fails after retries.
        """
        url = f"{self.host}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        effective_timeout = timeout or self.timeout

        def _do_request() -> str:
            try:
                with httpx.Client(timeout=effective_timeout) as client:
                    response = client.post(url, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    result: str = data.get("response", "")
                    return result.strip()

            except httpx.TimeoutException as e:
                raise LLMError(f"LLM request timed out after {effective_timeout}s") from e
            except httpx.HTTPStatusError as e:
                # Don't retry client errors (4xx)
                if 400 <= e.response.status_code < 500:
                    raise LLMError(f"LLM request failed: {e.response.status_code}") from e
                raise LLMError(f"LLM server error: {e.response.status_code}") from e
            except httpx.RequestError as e:
                raise LLMError(f"LLM connection error: {e}") from e

        # Retry on connection errors, not on timeouts (they're already slow)
        return retry_on_error(
            _do_request,
            max_attempts=self.max_retries,
            delay=1.0,
            exceptions=(LLMError,),
        )

    def is_available(self) -> bool:
        """Check if Ollama is available.

        Returns:
            True if Ollama is responding.
        """
        try:
            with httpx.Client(timeout=5) as client:
                response = client.get(f"{self.host}/api/tags")
                return response.status_code == 200
        except httpx.RequestError as e:
            logger.debug("Ollama not available: %s", e)
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
        except httpx.HTTPStatusError as e:
            raise LLMError(f"Failed to list models: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
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
        lines.append(f"\n[{timestamp}]")
        if run.command_output:
            # Truncate long outputs - only include raw command output, not LLM responses
            # to avoid feedback loops from hallucinated issues
            output = run.command_output[:1000]
            if len(run.command_output) > 1000:
                output += "\n... (truncated)"
            lines.append(output)
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


TRIAGE_PROMPT = """Assess this system check output. Your response must be exactly one word: OK or ALERT

{check_prompt}
{history_section}
--- Current Output ---
{output}
--- End Output ---

Respond with exactly one word: OK or ALERT"""


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


def _parse_llm_status(response: str) -> tuple[CheckStatus, str | None]:
    """Parse status from LLM response.

    Looks for explicit STATUS: markers first, then falls back to heuristics.

    Args:
        response: Raw LLM response text.

    Returns:
        Tuple of (status, alert_message). alert_message is None for OK status.
    """
    response_stripped = response.strip()
    response_lower = response_stripped.lower()

    # Look for explicit STATUS: marker (preferred format)
    for line in response_stripped.split("\n"):
        line_stripped = line.strip()
        line_lower = line_stripped.lower()

        if line_lower.startswith("status:"):
            status_part = line_lower[7:].strip()
            if status_part.startswith("ok"):
                return CheckStatus.OK, None
            elif status_part.startswith("warning") or status_part.startswith("alert"):
                # Return rest of response as alert message
                return CheckStatus.ALERT, response_stripped
            elif status_part.startswith("critical") or status_part.startswith("error"):
                return CheckStatus.ALERT, response_stripped

    # Fallback: check if response is just "OK" or starts with "OK"
    first_word = response_lower.split()[0] if response_lower else ""
    if first_word == "ok" or response_lower.startswith("ok.") or response_lower.startswith("ok\n"):
        return CheckStatus.OK, None

    # Fallback: check for "no issues", "all good", etc.
    ok_phrases = ["no issues", "no problems", "all good", "all systems normal", "everything is fine"]
    if any(phrase in response_lower for phrase in ok_phrases) and "alert" not in first_word:
        return CheckStatus.OK, None

    # Default to ALERT if we can't determine status
    return CheckStatus.ALERT, response_stripped


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

        # Parse status from response - look for explicit STATUS: marker first
        run.status, run.alert_message = _parse_llm_status(response)

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

        # Use shared status parsing for triage
        triage_status, _ = _parse_llm_status(triage_response)
        is_ok = triage_status == CheckStatus.OK

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
