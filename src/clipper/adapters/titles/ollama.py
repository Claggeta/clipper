from pydantic import ValidationError

from clipper.adapters.detection.llm import TextGenerator
from clipper.domain.errors import DetectionError, TitleError
from clipper.domain.models import ClipTitle

# Limits are in words: the model keeps to them, not to character counts (ADR
# 0018, title prompt amendment).
SYSTEM_PROMPT = (
    "You write post text for short clips cut from long-form videos, such as "
    "streams, podcasts and talks. Each message is the transcript of one clip, "
    "transcribed automatically, so punctuation may be missing. Write a "
    '"title" of at most 8 words, and a "hook": one sentence of at most 15 '
    "words that makes a viewer want to watch, to open the post's caption. Use "
    "only what the transcript says and keep the speaker's meaning: do not "
    "invent names or facts, or turn a claim into its opposite. No hashtags and "
    "no emoji. No swear words of any kind, not even mild ones such as "
    '"ass", "damn" or "hell": if the speaker swears, say it another way or '
    "leave the word out. Write in the language of the transcript. Reply with "
    'JSON only: "title" and "hook", each one line.'
)
# Enough of a bad reply to see what went wrong.
_REPLY_LIMIT = 200

_SCHEMA = ClipTitle.model_json_schema()


class OllamaTitleWriter:
    """Writes a title and hook per clip with a local LLM. See docs/decisions/0018.

    One request per clip, through the detector's `OllamaClient` (temperature 0,
    seed 0, `think: false`). Any failure raises `TitleError`; there is no
    fallback text.
    """

    def __init__(self, generator: TextGenerator) -> None:
        self._generator = generator

    def write(self, text: str) -> ClipTitle:
        try:
            reply = self._generator.generate(SYSTEM_PROMPT, text, _SCHEMA)
        except DetectionError as exc:
            # The client is shared with the detector; its messages name Ollama,
            # not detection, so they are kept as they are.
            raise TitleError(str(exc)) from exc
        try:
            return ClipTitle.model_validate_json(reply)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first["loc"]) or "<root>"
            raise TitleError(
                f"Model reply is not a valid title: {location}: {first['msg']} "
                f"(got {reply[:_REPLY_LIMIT]!r})"
            ) from exc
