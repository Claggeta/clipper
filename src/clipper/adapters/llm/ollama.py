import json
import urllib.error
import urllib.request
from typing import Protocol

from clipper.domain.errors import LlmError

# Per request. The first request also loads the model into memory.
TIMEOUT_SECONDS = 120.0
# Requested with temperature 0 for repeatability on one setup (ADR 0015).
SEED = 0
# A long prompt's size before sending, as a rough count of characters per
# token; refused past this share of the context it asks for (ADR 0033).
CHARS_PER_TOKEN = 3.0
CONTEXT_FILL = 0.9
# A reply whose prompt read this close to the window filled it.
CONTEXT_MARGIN = 32
# Enough of an unexpected body to recognise it, not a whole HTML page.
_BODY_LIMIT = 300


class TextGenerator(Protocol):
    """Returns a model's raw reply to one prompt, constrained to a JSON schema."""

    def generate(self, system: str, prompt: str, schema: dict[str, object]) -> str: ...


class OllamaClient:
    """Sends one prompt at a time to a local Ollama server. See docs/decisions/0014.

    Shared by the LLM detector and the title writer, each of which turns
    its `LlmError` into its own error with the message kept. Uses the
    standard library only, so no HTTP client dependency is added.
    """

    def __init__(
        self, url: str, model: str, timeout_seconds: float = TIMEOUT_SECONDS
    ) -> None:
        self._url = url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds

    def generate(
        self,
        system: str,
        prompt: str,
        schema: dict[str, object],
        num_ctx: int | None = None,
    ) -> str:
        """Return the model's reply, constrained to `schema`, as raw text.

        `num_ctx` asks for a context window of that many tokens, for a long
        prompt (ADR 0033); Ollama's default silently drops the start of a
        prompt longer than its window. With it, a prompt estimated to fill
        most of the window is refused before sending, and a reply whose
        prompt filled the window is an error, as its start was likely
        dropped. Without it, Ollama's defaults stand, as before.
        """
        options: dict[str, object] = {"temperature": 0, "seed": SEED}
        if num_ctx is not None:
            estimate = (len(system) + len(prompt)) / CHARS_PER_TOKEN
            if estimate > num_ctx * CONTEXT_FILL:
                raise LlmError(
                    f"The prompt is about {estimate:.0f} tokens, too long for a "
                    f"{num_ctx}-token context"
                )
            options["num_ctx"] = num_ctx
        payload = {
            "model": self._model,
            "system": system,
            "prompt": prompt,
            "format": schema,
            "stream": False,
            # A thinking model otherwise reasons before the JSON, and can run out
            # of context before writing it (ADR 0014, 7c amendment).
            "think": False,
            "options": options,
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
            raise LlmError(self._http_failure(exc)) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise LlmError(self._timed_out()) from exc
            raise LlmError(
                f"Cannot reach Ollama at {self._url}: {exc.reason}. Is it running?"
            ) from exc
        except TimeoutError as exc:
            raise LlmError(self._timed_out()) from exc
        except OSError as exc:
            raise LlmError(f"Request to Ollama at {self._url} failed: {exc}") from exc
        return self._reply(body, num_ctx)

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

    def _reply(self, body: bytes, num_ctx: int | None = None) -> str:
        text = body.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        reply = data.get("response") if isinstance(data, dict) else None
        if not isinstance(reply, str):
            raise LlmError(
                f"Unexpected response from Ollama at {self._url}: "
                f"{text.strip()[:_BODY_LIMIT]!r}"
            )
        read = data.get("prompt_eval_count") if isinstance(data, dict) else None
        if (
            num_ctx is not None
            and isinstance(read, int)
            and read >= num_ctx - CONTEXT_MARGIN
        ):
            raise LlmError(
                f"Ollama read {read} prompt tokens, filling its {num_ctx}-token "
                "context, so the prompt's start was likely dropped"
            )
        return reply
