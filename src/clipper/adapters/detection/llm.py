import logging
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from clipper.adapters.detection.windows import (
    STEP_SECONDS,
    WINDOW_SECONDS,
    select_top,
    sliding_windows,
)
from clipper.domain.errors import DetectionError
from clipper.domain.models import ClipCandidate, Segment, Transcript

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You judge excerpts from the transcript of a long-form video, such as a "
    "stream, a podcast or a talk, for a short-form clip channel. Each message "
    "is one excerpt of about 45 seconds of speech. It was transcribed "
    "automatically, so punctuation may be missing. Rate how well it would work "
    "as a standalone short clip: a hook in the first seconds, a complete point "
    "or story, a strong reaction, humour, or a surprising claim. Filler, "
    "logistics and talk that needs earlier context score low. Reply with JSON "
    'only: "score", a number from 0 to 1, and "reason", one short sentence on '
    "why."
)
# Log progress this often; a long video has over a thousand windows.
_PROGRESS_EVERY = 50
# Enough of a bad reply to see what went wrong.
_REPLY_LIMIT = 200


class TextGenerator(Protocol):
    """Returns a model's raw reply to one prompt, constrained to a JSON schema."""

    def generate(self, system: str, prompt: str, schema: dict[str, object]) -> str: ...


class WindowScore(BaseModel):
    """The model's verdict on one window."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    score: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)


_SCHEMA = WindowScore.model_json_schema()


class LlmClipDetector:
    """Scores every sliding window with a local LLM. See docs/decisions/0014.

    Re-ranks the heuristic's windows; it does not choose boundaries, and its
    ends are not extended (ADR 0023, item 5 amendment). Any failure
    raises `DetectionError`; it never falls back to another detector.
    """

    def __init__(
        self,
        generator: TextGenerator,
        top_n: int = 5,
        window_seconds: float = WINDOW_SECONDS,
        step_seconds: float = STEP_SECONDS,
    ) -> None:
        self._generator = generator
        self._top_n = top_n
        self._window = window_seconds
        self._step = step_seconds

    def detect(self, transcript: Transcript) -> list[ClipCandidate]:
        windows = sliding_windows(transcript, self._window, self._step)
        scored: list[ClipCandidate] = []
        for number, window in enumerate(windows, start=1):
            scored.append(self._score(window))
            if number % _PROGRESS_EVERY == 0 or number == len(windows):
                logger.info("Scored %d of %d windows", number, len(windows))
        return select_top(scored, self._top_n)

    def _score(self, window: tuple[Segment, ...]) -> ClipCandidate:
        start, end = window[0].start_seconds, window[-1].end_seconds
        text = " ".join(s.text.strip() for s in window)
        reply = self._generator.generate(SYSTEM_PROMPT, text, _SCHEMA)
        try:
            verdict = WindowScore.model_validate_json(reply)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first["loc"]) or "<root>"
            raise DetectionError(
                f"Model reply for window {_span(start, end)} is not a valid score: "
                f"{location}: {first['msg']} (got {reply[:_REPLY_LIMIT]!r})"
            ) from exc
        return ClipCandidate(
            start_seconds=start,
            end_seconds=end,
            score=verdict.score,
            reasons=(verdict.reason,),
        )


def _span(start: float, end: float) -> str:
    return f"{_hms(start)}-{_hms(end)}"


def _hms(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}"
