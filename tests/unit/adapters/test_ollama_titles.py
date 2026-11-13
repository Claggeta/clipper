import json

import pytest

from clipper.adapters.titles.ollama import SYSTEM_PROMPT, OllamaTitleWriter
from clipper.core.interfaces import TitleWriter
from clipper.domain.errors import ClipperError, LlmError, TitleError
from clipper.domain.models import ClipTitle, VideoInfo

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
            raise LlmError("Cannot reach Ollama at [url]")

    with pytest.raises(TitleError, match=r"^Cannot reach Ollama at \[url\]$"):
        OllamaTitleWriter(Failing()).write("text")


def test_the_video_info_comes_before_the_clip_text() -> None:
    generator = FakeGenerator()
    video = VideoInfo(
        platform="youtube",
        video_id="abcDEF123_-",
        title="A long talk",
        channel="The Show",
        description="Sponsor links",
    )
    OllamaTitleWriter(generator).write("what was said", video)
    prompt = generator.calls[0][1]
    assert prompt == (
        "Channel: The Show\nVideo title: A long talk\n\nTranscript:\nwhat was said"
    )
    assert "Sponsor" not in prompt


def test_the_prompt_says_how_to_use_the_video_info() -> None:
    assert "name them in the title" in SYSTEM_PROMPT
    assert "do not copy the video's title" in SYSTEM_PROMPT


def test_the_about_text_comes_first() -> None:
    generator = FakeGenerator()
    video = VideoInfo(
        platform="twitch", video_id="v1", title="Stream", channel="streamer"
    )
    writer = OllamaTitleWriter(generator)
    writer.write("what was said", video, "Ron (streamer)")
    writer.write("what was said", None, "Ron")
    assert generator.calls[0][1] == (
        "About: Ron (streamer)\nChannel: streamer\nVideo title: Stream\n\n"
        "Transcript:\nwhat was said"
    )
    assert generator.calls[1][1] == "About: Ron\n\nTranscript:\nwhat was said"


def test_the_prompt_prefers_the_about_names() -> None:
    assert "About line, written by the user" in SYSTEM_PROMPT
    assert "use its names over the channel's" in SYSTEM_PROMPT


def test_the_prompt_asks_for_emoji_at_the_end_of_the_title() -> None:
    assert "End the title with one or two emoji" in SYSTEM_PROMPT
    assert "no emoji" not in SYSTEM_PROMPT


def test_a_title_with_emoji_is_kept_as_written() -> None:
    writer = OllamaTitleWriter(FakeGenerator(_reply("Ron at the pump ⛽😱")))
    assert writer.write("text").title == "Ron at the pump ⛽😱"


def test_the_prompt_takes_pronouns_only_from_the_about_line() -> None:
    assert "do not use he, she" in SYSTEM_PROMPT
    assert "whose pronouns the About line states" in SYSTEM_PROMPT
