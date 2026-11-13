import json

import pytest

from clipper.adapters.llm.story import BEATS_CONTEXT, OllamaStoryWriter
from clipper.core.interfaces import StoryWriter
from clipper.domain.errors import LlmError, StoryError
from clipper.domain.models import BeatChoice

# All replies here are constructed, not produced by a real model.


class FakeGenerator:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str, int | None]] = []

    def generate(
        self,
        system: str,
        prompt: str,
        schema: dict[str, object],
        num_ctx: int | None = None,
    ) -> str:
        self.calls.append((system, prompt, num_ctx))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _note() -> str:
    return json.dumps(
        {
            "summary": "Buys scissors.",
            "kind": "progress",
            "importance": 0.6,
            "key_time": "[3:10]",
            "key_line": "scissor time",
        }
    )


def test_satisfies_protocol() -> None:
    assert isinstance(OllamaStoryWriter(FakeGenerator("{}")), StoryWriter)


def test_a_note_uses_the_default_context() -> None:
    generator = FakeGenerator(_note())
    note = OllamaStoryWriter(generator).note("[3:10] scissor time")
    assert (note.kind, note.importance, note.key_time) == ("progress", 0.6, "[3:10]")
    assert generator.calls[0][1:] == ("[3:10] scissor time", None)


def test_the_beats_ask_for_a_larger_context() -> None:
    reply = json.dumps({"beats": [{"part": 2, "card": "Scissors!", "why": "Upgrade."}]})
    generator = FakeGenerator(reply)
    beats = OllamaStoryWriter(generator).beats("Part 1 ...")
    assert beats == [BeatChoice(part=2, card="Scissors!", why="Upgrade.")]
    assert generator.calls[0][2] == BEATS_CONTEXT


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ("not json", "not a valid note: <root>"),
        (json.dumps({"summary": "x", "kind": "boring"}), "not a valid note"),
    ],
)
def test_a_bad_reply_is_a_story_error(reply: str, message: str) -> None:
    with pytest.raises(StoryError, match=message):
        OllamaStoryWriter(FakeGenerator(reply)).note("text")


def test_client_errors_become_story_errors_with_the_same_message() -> None:
    generator = FakeGenerator(LlmError("Cannot reach Ollama at [url]"))
    with pytest.raises(StoryError, match=r"^Cannot reach Ollama at \[url\]$"):
        OllamaStoryWriter(generator).beats("listing")
