import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TIMESTAMP = re.compile(r"(?:([0-9]+):)?([0-9]+):([0-9]{2}(?:\.[0-9]+)?)")
_TIMESTAMP_HINT = 'must be a "M:SS" or "H:MM:SS" string, e.g. "0:50" or "1:02:03"'


class Segment(BaseModel):
    """A span of transcribed speech with start and end times in seconds."""

    model_config = ConfigDict(frozen=True)

    start_seconds: float = Field(ge=0)
    end_seconds: float
    text: str

    @model_validator(mode="after")
    def _end_after_start(self) -> "Segment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
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
    """The output of analyzing one video."""

    model_config = ConfigDict(frozen=True)

    source: str
    transcript: Transcript
    candidates: tuple[ClipCandidate, ...]


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
