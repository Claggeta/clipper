import json
import urllib.error
import urllib.request

from clipper.domain.errors import DetectionError

# Per request. The first request also loads the model into memory.
TIMEOUT_SECONDS = 120.0
# Requested with temperature 0 for repeatability on one setup (ADR 0015).
SEED = 0
# Enough of an unexpected body to recognise it, not a whole HTML page.
_BODY_LIMIT = 300


class OllamaClient:
    """Sends one prompt at a time to a local Ollama server. See docs/decisions/0014.

    Uses the standard library only, so no HTTP client dependency is added.
    """

    def __init__(
        self, url: str, model: str, timeout_seconds: float = TIMEOUT_SECONDS
    ) -> None:
        self._url = url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds

    def generate(self, system: str, prompt: str, schema: dict[str, object]) -> str:
        """Return the model's reply, constrained to `schema`, as raw text."""
        payload = {
            "model": self._model,
            "system": system,
            "prompt": prompt,
            "format": schema,
            "stream": False,
            # A thinking model otherwise reasons before the JSON, and can run out
            # of context before writing it (ADR 0014, 7c amendment).
            "think": False,
            "options": {"temperature": 0, "seed": SEED},
        }
        request = urllib.request.Request(
            f"{self._url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body: bytes = response.read()
        except urllib.error.HTTPError as exc:
            raise DetectionError(self._http_failure(exc)) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise DetectionError(self._timed_out()) from exc
            raise DetectionError(
                f"Cannot reach Ollama at {self._url}: {exc.reason}. Is it running?"
            ) from exc
        except TimeoutError as exc:
            raise DetectionError(self._timed_out()) from exc
        except OSError as exc:
            raise DetectionError(
                f"Request to Ollama at {self._url} failed: {exc}"
            ) from exc
        return self._reply(body)

    def _timed_out(self) -> str:
        return (
            f"Ollama at {self._url} did not answer within {self._timeout:g} s "
            f"(model {self._model!r})"
        )

    def _http_failure(self, exc: urllib.error.HTTPError) -> str:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        detail = (
            str(data["error"])
            if isinstance(data, dict) and "error" in data
            else raw.strip()[:_BODY_LIMIT]
        )
        message = (
            f"Ollama at {self._url} returned HTTP {exc.code} for model "
            f"{self._model!r}: {detail or exc.reason}"
        )
        if exc.code == 404:
            message += f". Is the model pulled? (`ollama pull {self._model}`)"
        return message

    def _reply(self, body: bytes) -> str:
        text = body.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        reply = data.get("response") if isinstance(data, dict) else None
        if not isinstance(reply, str):
            raise DetectionError(
                f"Unexpected response from Ollama at {self._url}: "
                f"{text.strip()[:_BODY_LIMIT]!r}"
            )
        return reply
