import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TIMESTAMP = re.compile(r"(?:([0-9]+):)?([0-9]+):([0-9]{2}(?:\.[0-9]+)?)")
_TIMESTAMP_HINT = 'must be a "M:SS" or "H:MM:SS" string, e.g. "0:50" or "1:02:03"'


class Word(BaseModel):
    """One transcribed word with its start and end in seconds.

    A word may have zero length: Whisper can time a word as an instant, and
    its text is still worth showing in captions.
    """

    model_config = ConfigDict(frozen=True)

    start_seconds: float = Field(ge=0)
    end_seconds: float
    text: str

    @model_validator(mode="after")
    def _end_not_before_start(self) -> "Word":
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must not be before start_seconds")
        return self


class Segment(BaseModel):
    """A span of transcribed speech with start and end times in seconds.

    `words` is empty for transcripts made without word timestamps, including
    older saved JSON (see docs/decisions/0017).
    """

    model_config = ConfigDict(frozen=True)

    start_seconds: float = Field(ge=0)
    end_seconds: float
    text: str
    words: tuple[Word, ...] = ()

    @model_validator(mode="after")
    def _end_after_start(self) -> "Segment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self

    @model_validator(mode="after")
    def _words_inside_and_ordered(self) -> "Segment":
        message = "words must be in order, not overlap, and lie inside the segment"
        previous_end = self.start_seconds
        for word in self.words:
            if word.start_seconds < previous_end:
                raise ValueError(message)
            previous_end = word.end_seconds
        if previous_end > self.end_seconds:
            raise ValueError(message)
        return self

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


class Transcript(BaseModel):
    """An ordered, non-overlapping sequence of transcribed segments."""

    model_config = ConfigDict(frozen=True)

    segments: tuple[Segment, ...]

    @model_validator(mode="after")
    def _segments_ordered(self) -> "Transcript":
        for previous, current in zip(self.segments, self.segments[1:], strict=False):
            if current.start_seconds < previous.end_seconds:
                raise ValueError("segments must be ordered and non-overlapping")
        return self

    @property
    def full_text(self) -> str:
        return " ".join(s.text for s in self.segments)

    @property
    def duration_seconds(self) -> float:
        return self.segments[-1].end_seconds if self.segments else 0.0


class ClipCandidate(BaseModel):
    """A time range the detector considers clip-worthy, with its score."""

    model_config = ConfigDict(frozen=True)

    start_seconds: float = Field(ge=0)
    end_seconds: float
    score: float = Field(ge=0, le=1)
    reasons: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _end_after_start(self) -> "ClipCandidate":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


class AnalysisResult(BaseModel):
    """The output of analyzing one video.

    `detector` names the detector that chose the candidates. It is None in
    results saved before it was recorded (ADR 0013).
    """

    model_config = ConfigDict(frozen=True)

    source: str
    transcript: Transcript
    candidates: tuple[ClipCandidate, ...]
    detector: str | None = None


class MediaInfo(BaseModel):
    """Facts about a media file, as reported by a probe."""

    model_config = ConfigDict(frozen=True)

    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    has_video: bool


class RenderedClip(BaseModel):
    """A clip candidate and the video file it was rendered to."""

    model_config = ConfigDict(frozen=True)

    candidate: ClipCandidate
    path: Path


class ManifestClip(BaseModel):
    """One selected clip in a render manifest. See docs/decisions/0019.

    `file` is the clip's name inside the manifest's folder, set when it was
    rendered; `error` is the failure message otherwise. `captions` is what was
    burned in: word-by-word, whole segments (a transcript without word times),
    or nothing.
    """

    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1)
    start_seconds: float
    end_seconds: float
    score: float
    reasons: tuple[str, ...]
    file: str | None = None
    captions: Literal["words", "segments"] | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _file_or_error(self) -> "ManifestClip":
        if (self.file is None) == (self.error is None):
            raise ValueError("exactly one of file and error must be set")
        return self


class RenderManifest(BaseModel):
    """What one `render` run wrote to a video's folder. See docs/decisions/0019.

    `vertical` and `captions` are the flags the run was given. `analysis` is
    the result JSON path as given on the command line.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    analysis: str
    detector: str | None
    vertical: bool
    captions: bool
    clips: tuple[ManifestClip, ...]


def _parse_timestamp(value: object) -> float:
    """Convert "M:SS" or "H:MM:SS", with optional fractional seconds, to seconds.

    Plain numbers are rejected: a hand-written 0.50 meant 0:50, not half a second.
    """
    if not isinstance(value, str):
        raise ValueError(_TIMESTAMP_HINT)
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        raise ValueError(_TIMESTAMP_HINT)
    hours, minutes, seconds = match.groups()
    if float(seconds) >= 60:
        raise ValueError(_TIMESTAMP_HINT)
    if hours is not None and (len(minutes) != 2 or int(minutes) >= 60):
        raise ValueError(_TIMESTAMP_HINT)
    return int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds)


class Label(BaseModel):
    """A hand-labeled moment worth cutting. See docs/decisions/0012.

    Written as `start`/`end` timestamp strings, held as seconds.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_seconds: float = Field(validation_alias="start")
    end_seconds: float = Field(validation_alias="end")
    note: str | None = None

    @field_validator("start_seconds", "end_seconds", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> float:
        return _parse_timestamp(value)

    @model_validator(mode="after")
    def _end_after_start(self) -> "Label":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end must be after start")
        return self

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


class LabeledVideo(BaseModel):
    """One video's hand-written labels, ordered and non-overlapping."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    labels: tuple[Label, ...]

    @model_validator(mode="after")
    def _labels_ordered(self) -> "LabeledVideo":
        for index in range(1, len(self.labels)):
            if self.labels[index].start_seconds < self.labels[index - 1].end_seconds:
                raise ValueError(
                    f"labels must be ordered and non-overlapping: labels.{index} "
                    f"starts before labels.{index - 1} ends"
                )
        return self


class Evaluation(BaseModel):
    """A detector's candidates compared with one video's labels. See ADR 0012.

    `hits` holds `(label index, candidate index)` pairs, one per hitting pair.
    `matches` is the subset in which each label and each candidate appears at
    most once: precision counts matched candidates, so a second candidate on
    an already matched label is a repeat, not a hit. Recall counts labels with
    any hit. Recall and precision are None when there is nothing to divide by.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    labels: tuple[Label, ...]
    candidates: tuple[ClipCandidate, ...]
    hits: tuple[tuple[int, int], ...]
    matches: tuple[tuple[int, int], ...]

    @model_validator(mode="after")
    def _hits_in_range(self) -> "Evaluation":
        for label_index, candidate_index in self.hits:
            if not 0 <= label_index < len(self.labels):
                raise ValueError(f"hit names label {label_index}, which does not exist")
            if not 0 <= candidate_index < len(self.candidates):
                raise ValueError(
                    f"hit names candidate {candidate_index}, which does not exist"
                )
        return self

    @model_validator(mode="after")
    def _matches_are_one_to_one_hits(self) -> "Evaluation":
        for match in self.matches:
            if match not in self.hits:
                raise ValueError(f"match {match} is not a hit")
        labels = [label_index for label_index, _ in self.matches]
        candidates = [candidate_index for _, candidate_index in self.matches]
        if len(set(labels)) != len(labels):
            raise ValueError("a label is matched more than once")
        if len(set(candidates)) != len(candidates):
            raise ValueError("a candidate is matched more than once")
        return self

    @property
    def labels_hit(self) -> int:
        return len({label_index for label_index, _ in self.hits})

    @property
    def candidates_hit(self) -> int:
        return len({candidate_index for _, candidate_index in self.hits})

    @property
    def candidates_matched(self) -> int:
        return len(self.matches)

    @property
    def repeats(self) -> int:
        """Candidates that hit only labels another candidate matched."""
        return self.candidates_hit - self.candidates_matched

    @property
    def recall(self) -> float | None:
        return self.labels_hit / len(self.labels) if self.labels else None

    @property
    def precision(self) -> float | None:
        if not self.candidates:
            return None
        return self.candidates_matched / len(self.candidates)
