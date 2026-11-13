from typing import Protocol

from pydantic import BaseModel, ValidationError

from clipper.domain.errors import LlmError, StoryError
from clipper.domain.models import BeatChoice, StoryNote

# Set by the spike (ADR 0033).
NOTE_SYSTEM = (
    "You read a part of the transcript of a gaming stream, an incremental game "
    "where the player starts with a tiny, boring task and works toward a huge goal "
    "through upgrades. Each line starts with its time [m:ss]. Say what happens in "
    "this part for the story of the whole stream. Reply with JSON only: "
    '"summary": one sentence; "kind": one of "start", "goal", "progress", '
    '"milestone", "setback", "ending", "other"; "importance": 0 to 1, how much '
    "this part matters to the story of the stream; "
    '"key_time": the [m:ss] of the one line that best shows it; "key_line": that line.'
)
BEATS_SYSTEM = (
    "You plan a short video that tells the story of a whole gaming stream of an "
    "incremental game in order: where it started and the goal, the key upgrades, "
    "milestones and setbacks, and where it ended. You get one note per part of the "
    "stream, in order. Pick 8 to 10 parts as beats, in time order, spread across the "
    "whole stream with no long stretch skipped, always the first part that sets up "
    "the start or goal, at least one setback or struggle if there is one, and a part "
    "near the end showing where it ended. For each beat write a card of at most 6 "
    "words for the screen, in your own words, describing what happens in that part "
    "as its key line shows it, claiming nothing more, with no swear words and no "
    'quoting, like "Day 1: nail clippers" or "Bees end the run". Reply with JSON '
    'only: "beats": a list of {"part": the part number, "card": the card, "why": one '
    "short sentence}."
)
# The beats prompt lists every note: 5,370 tokens for a 2:45 VOD, longer
# than Ollama's default context (ADR 0033).
BEATS_CONTEXT = 16384
_REPLY_LIMIT = 200


class _Beats(BaseModel):
    beats: list[BeatChoice]


_NOTE_SCHEMA = StoryNote.model_json_schema()
_BEATS_SCHEMA = _Beats.model_json_schema()


class LongTextGenerator(Protocol):
    """A model's raw reply to one prompt, constrained to a JSON schema, with
    an optional context size (the shared Ollama client)."""

    def generate(
        self,
        system: str,
        prompt: str,
        schema: dict[str, object],
        num_ctx: int | None = None,
    ) -> str: ...


class OllamaStoryWriter:
    """Notes and beats for a VOD's story from a local LLM. See
    docs/decisions/0033. Any failure raises `StoryError`."""

    def __init__(self, generator: LongTextGenerator) -> None:
        self._generator = generator

    def note(self, text: str) -> StoryNote:
        reply = self._ask(NOTE_SYSTEM, text, _NOTE_SCHEMA, None)
        return self._parse(StoryNote, reply, "note")

    def beats(self, listing: str) -> list[BeatChoice]:
        reply = self._ask(BEATS_SYSTEM, listing, _BEATS_SCHEMA, BEATS_CONTEXT)
        return self._parse(_Beats, reply, "beats").beats

    def _ask(
        self, system: str, prompt: str, schema: dict[str, object], num_ctx: int | None
    ) -> str:
        try:
            return self._generator.generate(system, prompt, schema, num_ctx)
        except LlmError as exc:
            # The client's messages name Ollama, so they are kept as they are.
            raise StoryError(str(exc)) from exc

    def _parse[T: BaseModel](self, model: type[T], reply: str, what: str) -> T:
        try:
            return model.model_validate_json(reply)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first["loc"]) or "<root>"
            raise StoryError(
                f"Model reply is not a valid {what}: {location}: {first['msg']} "
                f"(got {reply[:_REPLY_LIMIT]!r})"
            ) from exc
