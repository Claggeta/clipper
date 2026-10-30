import logging
import shutil
import subprocess

from clipper.domain.errors import ClipperError

_MAX_ERROR_LINES = 10


def run_tool(
    executable: str,
    args: list[str],
    *,
    error_type: type[ClipperError],
    log: logging.Logger,
) -> subprocess.CompletedProcess[str]:
    """Run an ffmpeg-family tool and return its result.

    Raises ``error_type`` if the executable is not on PATH. A non-zero exit does
    not raise: the full stderr is logged at INFO and the caller classifies it.
    See docs/decisions/0004.
    """
    if shutil.which(executable) is None:
        raise error_type(f"'{executable}' not found on PATH. Install ffmpeg first.")
    result = subprocess.run(
        [executable, *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        log.info("%s stderr:\n%s", executable, result.stderr)
    return result


def format_failure(executable: str, result: subprocess.CompletedProcess[str]) -> str:
    """``<tool> failed (exit N): <last non-empty stderr lines>``, capped by lines."""
    lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
    detail = "\n".join(lines[-_MAX_ERROR_LINES:])
    return f"{executable} failed (exit {result.returncode}): {detail}"
