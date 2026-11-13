import io
import json
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from clipper.adapters.llm.ollama import SEED, TIMEOUT_SECONDS, OllamaClient
from clipper.domain.errors import LlmError

# All server responses here are constructed, not captured from a real Ollama
# server. docs/manual-testing.md checks the real ones.

URL = "http://127.0.0.1:11434"
SCHEMA: dict[str, object] = {"type": "object"}


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _patch(monkeypatch: pytest.MonkeyPatch, outcome: bytes | Exception) -> list[Any]:
    seen: list[Any] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
        seen.append((request, timeout))
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        f"{URL}/api/generate", code, "reason", Message(), io.BytesIO(body)
    )


def _generate(url: str = URL) -> str:
    return OllamaClient(url, "some-model:tag").generate("sys", "words", SCHEMA)


def test_posts_one_non_streaming_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(monkeypatch, json.dumps({"response": '{"score": 1}'}).encode())

    assert _generate() == '{"score": 1}'

    [(request, timeout)] = seen
    assert request.full_url == f"{URL}/api/generate"
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert timeout == TIMEOUT_SECONDS
    assert json.loads(request.data) == {
        "model": "some-model:tag",
        "system": "sys",
        "prompt": "words",
        "format": SCHEMA,
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "seed": SEED},
    }


def test_trailing_slash_in_url_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(monkeypatch, json.dumps({"response": ""}).encode())
    _generate(URL + "/")
    assert seen[0][0].full_url == f"{URL}/api/generate"


def test_unreachable_server(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, urllib.error.URLError(ConnectionRefusedError("refused")))
    with pytest.raises(LlmError) as info:
        _generate()
    assert str(info.value) == f"Cannot reach Ollama at {URL}: refused. Is it running?"


@pytest.mark.parametrize(
    "error", [TimeoutError("timed out"), urllib.error.URLError(TimeoutError())]
)
def test_timeout(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    _patch(monkeypatch, error)
    with pytest.raises(LlmError) as info:
        _generate()
    assert str(info.value) == (
        f"Ollama at {URL} did not answer within 120 s (model 'some-model:tag')"
    )


def test_other_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, ConnectionResetError("reset"))
    with pytest.raises(LlmError, match="Request to Ollama at .* failed: reset"):
        _generate()


def test_404_passes_on_the_server_error_and_suggests_a_pull(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _http_error(404, b'{"error": "no such model"}'))
    with pytest.raises(LlmError) as info:
        _generate()
    assert str(info.value) == (
        f"Ollama at {URL} returned HTTP 404 for model 'some-model:tag': "
        "no such model. Is the model pulled? (`ollama pull some-model:tag`)"
    )


def test_other_http_error_with_a_non_json_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _http_error(500, b"  boom  "))
    with pytest.raises(LlmError) as info:
        _generate()
    assert str(info.value) == (
        f"Ollama at {URL} returned HTTP 500 for model 'some-model:tag': boom"
    )


def test_http_error_with_an_empty_body_uses_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, _http_error(500, b""))
    with pytest.raises(LlmError, match="HTTP 500 .*: reason$"):
        _generate()


@pytest.mark.parametrize(
    "body", [b"<html>", b"[]", b'{"done": true}', b'{"response": 3}']
)
def test_unexpected_response_body(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    _patch(monkeypatch, body)
    with pytest.raises(LlmError) as info:
        _generate()
    assert str(info.value).startswith(f"Unexpected response from Ollama at {URL}: ")


# --- A context size for long prompts (ADR 0033) ---


def _long(prompt: str = "words", num_ctx: int = 1000) -> str:
    return OllamaClient(URL, "m").generate("sys", prompt, SCHEMA, num_ctx)


def test_a_context_size_is_sent_as_num_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(
        monkeypatch, json.dumps({"response": "{}", "prompt_eval_count": 40}).encode()
    )
    assert _long() == "{}"
    [(request, _)] = seen
    assert json.loads(request.data)["options"] == {
        "temperature": 0,
        "seed": SEED,
        "num_ctx": 1000,
    }


def test_a_prompt_too_long_for_its_context_is_refused_before_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch(monkeypatch, json.dumps({"response": "{}"}).encode())
    with pytest.raises(LlmError, match="too long for a 1000-token context"):
        _long("x" * 2800)
    assert seen == []


def test_a_reply_whose_prompt_filled_the_context_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(
        monkeypatch, json.dumps({"response": "{}", "prompt_eval_count": 990}).encode()
    )
    with pytest.raises(LlmError, match="read 990 prompt tokens, filling its 1000"):
        _long()


def test_without_a_context_size_the_tokens_read_are_not_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(
        monkeypatch, json.dumps({"response": "{}", "prompt_eval_count": 4096}).encode()
    )
    assert _generate() == "{}"
