import json

import pytest

from clipper.adapters.titles.ollama import SYSTEM_PROMPT, OllamaTitleWriter
from clipper.core.interfaces import TitleWriter
from clipper.domain.errors import ClipperError, DetectionError, TitleError
from clipper.domain.models import ClipTitle

# All replies here are constructed, not produced by a real model.


def _reply(title: object = "A title", hook: object = "A hook.") -> str:
    return json.dumps({"title": title, "hook": hook})


class FakeGenerator:
    def __init__(self, reply: str = _reply()) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self._reply = reply

    def generate(self, system: str, prompt: str, schema: dict[str, object]) -> str:
        self.calls.append((system, prompt, schema))
        return self._reply


def test_satisfies_protocol() -> None:
    assert isinstance(OllamaTitleWriter(FakeGenerator()), TitleWriter)


def test_sends_the_prompt_the_clip_text_and_the_title_schema() -> None:
    generator = FakeGenerator()
    title = OllamaTitleWriter(generator).write("what was said")
    assert title == ClipTitle(title="A title", hook="A hook.")
    assert generator.calls == [
        (SYSTEM_PROMPT, "what was said", ClipTitle.model_json_schema())
    ]


def test_title_and_hook_are_stripped() -> None:
    writer = OllamaTitleWriter(FakeGenerator(_reply("  padded ", " hook  ")))
    assert writer.write("text") == ClipTitle(title="padded", hook="hook")


@pytest.mark.parametrize(
    ("reply", "detail"),
    [
        ("not json", "<root>: Invalid JSON"),
        ("", "<root>: Invalid JSON"),
        (_reply(title="  "), "title: String should have at least 1 character"),
        (_reply(hook=""), "hook: String should have at least 1 character"),
        (_reply(title="two\nlines"), "title: Value error, must be one line"),
        (json.dumps({"title": "A title"}), "hook: Field required"),
        (_reply(hook=3), "hook: Input should be a valid string"),
    ],
)
def test_invalid_reply_raises_title_error(reply: str, detail: str) -> None:
    with pytest.raises(TitleError) as info:
        OllamaTitleWriter(FakeGenerator(reply)).write("text")
    message = str(info.value)
    assert message.startswith("Model reply is not a valid title: ")
    assert detail in message
    assert repr(reply[:200]) in message
    assert isinstance(info.value, ClipperError)


def test_client_errors_become_title_errors_with_the_same_message() -> None:
    class Failing:
        def generate(self, system: str, prompt: str, schema: dict[str, object]) -> str:
            raise DetectionError("Cannot reach Ollama at [url]")

    with pytest.raises(TitleError, match=r"^Cannot reach Ollama at \[url\]$"):
        OllamaTitleWriter(Failing()).write("text")
