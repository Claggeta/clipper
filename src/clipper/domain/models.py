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


class FaceBox(BaseModel):
    """One face found in a frame, as fractions of the frame's width and height.

    `x` and `y` are the top-left corner and may fall just outside 0 to 1 for a
    face at the edge. `score` is the detector's confidence. See
    docs/decisions/0025.
    """

    model_config = ConfigDict(frozen=True)

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    w: float = Field(gt=0, allow_inf_nan=False)
    h: float = Field(gt=0, allow_inf_nan=False)
    score: float = Field(ge=0, le=1)


class FaceSample(BaseModel):
    """The faces found in one frame, `seconds` into the video."""

    model_config = ConfigDict(frozen=True)

    seconds: float = Field(ge=0)
    faces: tuple[FaceBox, ...] = ()


class FaceTrack(BaseModel):
    """Faces sampled across one span of a video, in time order.

    `source` is the video's file name. See docs/decisions/0025.
    """

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float
    samples_per_second: float = Field(gt=0)
    samples: tuple[FaceSample, ...]

    @model_validator(mode="after")
    def _end_after_start(self) -> "FaceTrack":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class MediaInfo(BaseModel):
    """Facts about a media file, as reported by a probe.

    `start_seconds` is the file's first timestamp, usually 0. A browser's
    player counts from it while ffmpeg counts from the file's start, so the
    review page needs it to be 0 (ADR 0024, start offset amendment).
    `width` and `height` are the first video stream's, in pixels, or None
    when the probe cannot tell; the face crop needs them (ADR 0025).
    """

    model_config = ConfigDict(frozen=True)

    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    has_video: bool
    start_seconds: float = Field(default=0.0, allow_inf_nan=False)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)


class CropPath(BaseModel):
    """A full-height crop that moves across the frame, for the face style.

    `width` and `height` are the window's size in source pixels. Each point is
    a time in seconds from the clip's start and the window's left edge in
    source pixels from then on; the first is at 0. See docs/decisions/0025.
    """

    model_config = ConfigDict(frozen=True)

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    points: tuple[tuple[float, int], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _starts_at_zero(self) -> "CropPath":
        if self.points[0][0] != 0:
            raise ValueError("the first point must be at 0 seconds")
        return self


class RenderedClip(BaseModel):
    """A clip candidate and the video file it was rendered to."""

    model_config = ConfigDict(frozen=True)

    candidate: ClipCandidate
    path: Path


class VideoSource(BaseModel):
    """One video named by a URL that `fetch` accepts. See docs/decisions/0020.

    `video_id` is the file stem: YouTube's 11-character ID, or a Twitch VOD's
    number after a `v`.
    """

    model_config = ConfigDict(frozen=True)

    platform: Literal["youtube", "twitch"]
    video_id: str = Field(min_length=1)


class VideoInfo(BaseModel):
    """A fetched video's metadata, saved beside it. See docs/decisions/0020.

    `channel` is yt-dlp's uploader: a Twitch login or a YouTube channel name.
    `upload_date` is yt-dlp's `YYYYMMDD`. Twitch VODs have no description.
    """

    model_config = ConfigDict(frozen=True)

    platform: Literal["youtube", "twitch"]
    video_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    upload_date: str | None = None
    duration_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    description: str = ""


class ClipLink(BaseModel):
    """One line of a links file: a Twitch clip and its VOD link's `t`.

    `end_seconds` is `t`, which marks the clip's end in the VOD to within 4 s
    (docs/decisions/0012, Twitch clips amendment). `clip_id` is the clip's
    slug, the same for every URL form of one clip.
    """

    model_config = ConfigDict(frozen=True)

    line: int = Field(ge=1)
    clip_id: str = Field(min_length=1)
    vod: VideoSource
    end_seconds: float = Field(gt=0)


class ClipInfo(BaseModel):
    """A Twitch clip's title and length, read from its metadata."""

    model_config = ConfigDict(frozen=True)

    title: str
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)


class ClipTitle(BaseModel):
    """A suggested title and hook for one clip. See docs/decisions/0018.

    Both are stripped, must not be empty, and must be one line.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    title: str = Field(min_length=1)
    hook: str = Field(min_length=1)

    @field_validator("title", "hook")
    @classmethod
    def _one_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("must be one line")
        return value


class ManifestClip(BaseModel):
    """One selected clip in a render manifest. See docs/decisions/0019.

    `file` is the clip's name inside the manifest's folder, set when it was
    rendered; `error` is the failure message otherwise. `captions` is what was
    burned in: word-by-word, whole segments (a transcript without word times),
    or nothing. `render --titles` sets `title` and `hook` together on a
    rendered clip with speech in range (ADR 0018); the review page may then
    change or clear the title alone.

    `reviewed` is what the review page did with the clip (ADR 0024, item 3c
    amendment). A trashed clip's file was deleted, so it has no `file`, and
    may keep the `error` it had.

    `start_seconds` and `end_seconds` are what the file covers. The review
    page's re-render (item 3d amendment) moves them, and keeps the draft's
    span in `draft_start_seconds` and `draft_end_seconds`, set together;
    `draft_span` is that span, or the clip's own when never re-rendered.

    `model_title` is the title `render --titles` wrote, kept by the page's
    first write to the clip (None if there was none); `title` may then be
    the maintainer's.

    `added` marks a clip the maintainer added in the review page (item 3e
    amendment), ranked after the drafts, with score 0 and the reason
    "added in review". Its draft span is its last saved span.
    """

    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1)
    start_seconds: float
    end_seconds: float
    score: float
    reasons: tuple[str, ...]
    file: str | None = None
    captions: Literal["words", "segments"] | None = None
    title: str | None = None
    hook: str | None = None
    error: str | None = None
    reviewed: Literal["saved", "trashed"] | None = None
    draft_start_seconds: float | None = None
    draft_end_seconds: float | None = None
    model_title: str | None = None
    added: bool = False

    @model_validator(mode="after")
    def _file_or_error(self) -> "ManifestClip":
        if self.reviewed == "trashed":
            if self.file is not None:
                raise ValueError("a trashed clip has no file")
        elif (self.file is None) == (self.error is None):
            raise ValueError("exactly one of file and error must be set")
        return self

    @model_validator(mode="after")
    def _draft_span_together(self) -> "ManifestClip":
        if (self.draft_start_seconds is None) != (self.draft_end_seconds is None):
            raise ValueError(
                "draft_start_seconds and draft_end_seconds must be set together"
            )
        return self

    @property
    def draft_span(self) -> tuple[float, float]:
        """The span the detector proposed, before any re-render."""
        if self.draft_start_seconds is None or self.draft_end_seconds is None:
            return self.start_seconds, self.end_seconds
        return self.draft_start_seconds, self.draft_end_seconds


class RenderManifest(BaseModel):
    """What one `render` run wrote to a video's folder. See docs/decisions/0019.

    `vertical`, `captions` and `titles` are the flags the run was given.
    `analysis` is the result JSON path as given on the command line.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    analysis: str
    detector: str | None
    vertical: bool
    captions: bool
    titles: bool
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


def format_timestamp(seconds: float) -> str:
    """Write seconds as "H:MM:SS", with milliseconds only when they are not zero.

    The inverse of the labels file's timestamp parsing, to the millisecond.
    """
    millis = round(seconds * 1000)
    whole, fraction = divmod(millis, 1000)
    minutes, secs = divmod(whole, 60)
    hours, minutes = divmod(minutes, 60)
    text = f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{text}.{fraction:03d}".rstrip("0") if fraction else text


class Span(BaseModel):
    """A time range in a labels file, written as `start`/`end` timestamps."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_seconds: float = Field(validation_alias="start")
    end_seconds: float = Field(validation_alias="end")

    @field_validator("start_seconds", "end_seconds", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> float:
        return _parse_timestamp(value)

    @model_validator(mode="after")
    def _end_after_start(self) -> "Span":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end must be after start")
        return self

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


Verdict = Literal["post", "fix", "added"]


class Label(Span):
    """A hand-labeled moment worth cutting. See docs/decisions/0012.

    Written as `start`/`end` timestamp strings, held as seconds. In a judged
    file (ADR 0012, V5 amendment) every label has a verdict: `post` is a draft
    as cut, `fix` a draft whose span was moved (`draft` keeps the old span),
    and `added` a moment no draft proposed.
    """

    note: str | None = None
    verdict: Verdict | None = None
    draft: Span | None = None

    @model_validator(mode="after")
    def _draft_only_on_fix(self) -> "Label":
        if self.verdict == "fix":
            if self.draft is None:
                raise ValueError('a "fix" label needs the draft\'s span in "draft"')
            if _same_span(self.draft, self):
                raise ValueError('a "fix" label\'s span must differ from its draft')
        elif self.draft is not None:
            raise ValueError('only a "fix" label has a "draft"')
        return self


class RejectedSpan(Span):
    """A draft the maintainer judged and dropped (ADR 0012, V5 amendment)."""

    note: str | None = None


def _same_span(a: Span, b: Span) -> bool:
    return round(a.start_seconds * 1000) == round(b.start_seconds * 1000) and round(
        a.end_seconds * 1000
    ) == round(b.end_seconds * 1000)


class LabeledVideo(BaseModel):
    """One video's labels, ordered and non-overlapping.

    A judged file (ADR 0012, V5 amendment) has a verdict on every label and may
    list rejected spans, ordered by start; they may overlap each other and the
    labels. A file without verdicts has no rejected spans.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    labels: tuple[Label, ...]
    rejected: tuple[RejectedSpan, ...] = ()

    @model_validator(mode="after")
    def _labels_ordered(self) -> "LabeledVideo":
        for index in range(1, len(self.labels)):
            if self.labels[index].start_seconds < self.labels[index - 1].end_seconds:
                raise ValueError(
                    f"labels must be ordered and non-overlapping: labels.{index} "
                    f"starts before labels.{index - 1} ends"
                )
        return self

    @model_validator(mode="after")
    def _judged_or_not(self) -> "LabeledVideo":
        with_verdict = [label.verdict is not None for label in self.labels]
        if any(with_verdict) and not all(with_verdict):
            index = with_verdict.index(False)
            raise ValueError(
                f"labels.{index} has no verdict, but other labels do; a labels "
                "file has a verdict on every label or on none"
            )
        if self.rejected and not all(with_verdict):
            raise ValueError("rejected spans need a verdict on every label")
        for index in range(1, len(self.rejected)):
            if (
                self.rejected[index].start_seconds
                < self.rejected[index - 1].start_seconds
            ):
                raise ValueError(
                    f"rejected spans must be ordered by start: rejected.{index} "
                    f"starts before rejected.{index - 1}"
                )
        return self

    @property
    def judged(self) -> bool:
        """True for a file of judged drafts (ADR 0012, V5 amendment)."""
        return bool(self.rejected) or any(
            label.verdict is not None for label in self.labels
        )


class Evaluation(BaseModel):
    """A detector's candidates compared with one video's labels. See ADR 0012.

    `hits` holds `(label index, candidate index)` pairs, one per hitting pair.
    `matches` is the subset in which each label and each candidate appears at
    most once: precision counts matched candidates, so a second candidate on
    an already matched label is a repeat, not a hit. Recall counts labels with
    any hit. Recall and precision are None when there is nothing to divide by.

    For judged labels, `on_rejected` holds the candidates that hit no label but
    hit a rejected span; the rest of the candidates without a hit are
    unjudged.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    labels: tuple[Label, ...]
    candidates: tuple[ClipCandidate, ...]
    hits: tuple[tuple[int, int], ...]
    matches: tuple[tuple[int, int], ...]
    judged: bool = False
    on_rejected: tuple[int, ...] = ()

    @model_validator(mode="after")
    def _on_rejected_without_hits(self) -> "Evaluation":
        hit = {candidate_index for _, candidate_index in self.hits}
        for candidate_index in self.on_rejected:
            if not 0 <= candidate_index < len(self.candidates):
                raise ValueError(
                    f"on_rejected names candidate {candidate_index}, which does "
                    "not exist"
                )
            if candidate_index in hit:
                raise ValueError(
                    f"candidate {candidate_index} hits a label, so is not on a "
                    "rejected span"
                )
        if self.on_rejected and not self.judged:
            raise ValueError("on_rejected needs judged labels")
        return self

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

    @property
    def candidates_unjudged(self) -> int:
        """Candidates on neither a label nor a rejected span; 0 if not judged."""
        if not self.judged:
            return 0
        return len(self.candidates) - self.candidates_hit - len(self.on_rejected)
