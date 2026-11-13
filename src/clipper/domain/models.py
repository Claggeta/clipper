import math
import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

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


class QueuedSource(BaseModel):
    """One source for `drafts`: a URL or file, with its own `--about` text.

    `line` is its line in a `--list` file, or None for one given on the
    command line. See docs/decisions/0022 (list amendment).
    """

    model_config = ConfigDict(frozen=True)

    source: str = Field(min_length=1)
    about: str | None = None
    line: int | None = Field(default=None, ge=1)


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


VerticalStyleName = Literal["fit", "face"]

# Where an on-screen text sits (ADR 0030): below the area YouTube's own
# interface covers, or the middle. Never the bottom, the captions' place.
TextPlace = Literal["top", "middle"]
# An on-screen text's look (ADR 0030, styling amendment, V7 9g).
TextColour = Literal["white", "yellow", "green", "cyan", "pink", "orange"]
TextLook = Literal["box", "outline"]
TextSize = Literal["small", "medium", "large"]
TEXT_LIMIT = 120


class OnScreenText(BaseModel):
    """A line of text burned over the clip (ADR 0030).

    `start_seconds` and `end_seconds` are seconds of the finished clip,
    after any cuts, not of the source video. Its look (styling amendment):
    a colour, a half-dark `box` or a black `outline`, a size, and a fade in;
    `x` and `y`, fractions of the frame for the text's centre, place it
    freely instead of at `place`. The defaults are the original look.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    start_seconds: float = Field(ge=0)
    end_seconds: float
    place: TextPlace = "top"
    colour: TextColour = "white"
    look: TextLook = "box"
    size: TextSize = "large"
    fade: bool = False
    x: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    y: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @field_validator("text")
    @classmethod
    def _one_line(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("a text is empty")
        if "\n" in text or "\r" in text:
            raise ValueError("a text must be one line")
        if len(text) > TEXT_LIMIT:
            raise ValueError(f"a text must be at most {TEXT_LIMIT} characters")
        return text

    @model_validator(mode="after")
    def _ends_after_start(self) -> "OnScreenText":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("a text must end after it starts")
        if (self.x is None) != (self.y is None):
            raise ValueError("a text's position needs both x and y")
        return self


# A caption word's fixed text, at most this long (ADR 0017, V7 9f).
WORD_FIX_LIMIT = 40
# Fixes or key words per clip, at most; a long clip has about 250 words.
MAX_CAPTION_EDITS = 400
# The colours a clip's active and key words may take, by name.
Highlight = Literal["yellow", "green", "cyan", "pink", "orange"]


class WordFix(BaseModel):
    """A caption word's text replaced, by the word's start in the source.

    Empty text hides the word. See docs/decisions/0017 (caption editing
    amendment).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    text: str

    @field_validator("text")
    @classmethod
    def _one_short_line(cls, value: str) -> str:
        text = " ".join(value.split())
        if len(text) > WORD_FIX_LIMIT:
            raise ValueError(f"a word fix must be at most {WORD_FIX_LIMIT} characters")
        return text


class CaptionEdits(BaseModel):
    """How a clip's word captions differ from the transcript (ADR 0017,
    caption editing amendment): words with fixed text (`fixes`), words shown
    in the highlight colour throughout their chunk (`keys`, by start time),
    and the colour the active and key words take."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fixes: tuple[WordFix, ...] = Field(default=(), max_length=MAX_CAPTION_EDITS)
    keys: tuple[float, ...] = Field(default=(), max_length=MAX_CAPTION_EDITS)
    highlight: Highlight = "yellow"

    @model_validator(mode="after")
    def _each_word_once(self) -> "CaptionEdits":
        starts = [round(fix.start_seconds, 3) for fix in self.fixes]
        if len(set(starts)) != len(starts):
            raise ValueError("a word is fixed twice")
        keys = [round(key, 3) for key in self.keys]
        if len(set(keys)) != len(keys):
            raise ValueError("a key word is given twice")
        if any(key < 0 or not math.isfinite(key) for key in self.keys):
            raise ValueError("a key word's time must be a number of seconds")
        return self

    @property
    def empty(self) -> bool:
        """True when the captions are the transcript's, in yellow."""
        return not self.fixes and not self.keys and self.highlight == "yellow"


# Punch-in zooms (ADR 0016, zoom amendment, V7 9h): at most this much, as
# 1080p source upscaled further goes soft.
ZOOM_MIN = 1.1
ZOOM_MAX = 2.0
# Zooms or hand crops per clip, at most.
MAX_ZOOMS = 10
MAX_HAND_CROPS = 10


class Zoom(BaseModel):
    """A punch-in over a stretch of the clip, in source seconds like cuts
    (ADR 0016, zoom amendment): the frame scaled by `scale` around a centre
    `x`, `y` (fractions of the frame), at once or `smooth`ly eased."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(allow_inf_nan=False)
    scale: float = Field(default=1.3, ge=ZOOM_MIN, le=ZOOM_MAX)
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)
    smooth: bool = False

    @model_validator(mode="after")
    def _ends_after_start(self) -> "Zoom":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("a zoom must end after it starts")
        return self


class HandCrop(BaseModel):
    """A full-height 9:16 window placed by hand over a stretch of the clip,
    in source seconds, centred at `x` (a fraction of the source width), in
    place of the fit or the face track there (ADR 0025, hand crop
    amendment)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(allow_inf_nan=False)
    x: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def _ends_after_start(self) -> "HandCrop":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("a hand crop must end after it starts")
        return self


class HandCropPlan(BaseModel):
    """What the renderer needs for hand crops in the fit style: the windows
    as a crop path in the cut clip's seconds, and the stretches they show
    in (ADR 0025, hand crop amendment)."""

    model_config = ConfigDict(frozen=True)

    path: CropPath
    ranges: tuple[tuple[float, float], ...] = Field(min_length=1)


# Clip audio (ADR 0007, audio amendment, V7 9i): the clip's own volume, a
# music bed and sound effects, from files the maintainer adds.
GAIN_LIMIT_DB = 12.0
MAX_EFFECTS = 10
AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")


def _audio_file(name: str) -> str:
    if Path(name).name != name or not name.strip():
        raise ValueError("an audio file must be a plain file name")
    if Path(name).suffix.lower() not in AUDIO_SUFFIXES:
        raise ValueError(f"an audio file must be one of {', '.join(AUDIO_SUFFIXES)}")
    return name


class MusicBed(BaseModel):
    """A music file under the clip, looped and faded out at the end, at
    `level_db`, lowered under speech when `duck`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    level_db: float = Field(default=-18.0, ge=-30, le=0)
    duck: bool = True

    _file = field_validator("file")(_audio_file)


class SoundEffect(BaseModel):
    """A sound effect file at a moment of the source, at `level_db`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    at_seconds: float = Field(ge=0, allow_inf_nan=False)
    level_db: float = Field(default=0.0, ge=-20, le=6)

    _file = field_validator("file")(_audio_file)


class ClipAudio(BaseModel):
    """A clip's audio edits (ADR 0007, audio amendment): its own volume in
    dB, a music bed, and sound effects in source seconds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gain_db: float = Field(default=0.0, ge=-GAIN_LIMIT_DB, le=GAIN_LIMIT_DB)
    music: MusicBed | None = None
    effects: tuple[SoundEffect, ...] = Field(default=(), max_length=MAX_EFFECTS)

    @property
    def empty(self) -> bool:
        """True when the clip's audio is as cut, alone."""
        return self.gain_db == 0 and self.music is None and not self.effects


class MixEffect(BaseModel):
    """A sound effect for the renderer: its file and its time in the cut clip."""

    model_config = ConfigDict(frozen=True)

    path: Path
    at_seconds: float = Field(ge=0)
    level_db: float


class AudioMix(BaseModel):
    """What the renderer mixes (ADR 0007, audio amendment): files resolved,
    effect times in the cut clip's seconds."""

    model_config = ConfigDict(frozen=True)

    gain_db: float = 0.0
    music: Path | None = None
    music_level_db: float = -18.0
    duck: bool = True
    effects: tuple[MixEffect, ...] = ()


class ClipVariant(BaseModel):
    """One rendered style of a clip: the style and its file name in the folder.

    See docs/decisions/0025 (several styles amendment).
    """

    model_config = ConfigDict(frozen=True)

    style: VerticalStyleName
    file: str = Field(min_length=1)


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

    `variants` lists each vertical style rendered for the clip, in the run's
    order (ADR 0025); `file` is the first of them. Empty for a landscape run,
    an older manifest, or after the review page re-rendered or trashed it.

    `cuts` are the source-time ranges left out of the file (ADR 0029), in
    order and inside `start_seconds`..`end_seconds`; empty when uncut.
    `texts` are the lines burned over it (ADR 0030), and `thumbnail` the
    name of its thumbnail image in the folder, if one was made.
    `caption_edits` are its word fixes, key words and colour (ADR 0017,
    caption editing amendment), None when the captions are as transcribed.
    `zooms` and `hand_crops` are its punch-ins and hand-placed windows
    (ADR 0016 and 0025 amendments), in source seconds. `audio` is its
    volume, music and sound effects (ADR 0007, audio amendment), None when
    as cut.
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
    variants: tuple[ClipVariant, ...] = ()
    cuts: tuple[tuple[float, float], ...] = ()
    texts: tuple[OnScreenText, ...] = ()
    thumbnail: str | None = None
    caption_edits: CaptionEdits | None = None
    zooms: tuple[Zoom, ...] = ()
    hand_crops: tuple[HandCrop, ...] = ()
    audio: ClipAudio | None = None

    @model_validator(mode="after")
    def _variants_have_the_file(self) -> "ManifestClip":
        if self.variants and self.file != self.variants[0].file:
            raise ValueError("file must be the first variant's file")
        return self

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


# A VOD's clips combined (ADR 0032, V7 9k): YouTube Shorts run up to
# about this long, so past it the page warns; a label card is one line.
SHORTS_LIMIT_SECONDS = 180.0
CARD_LIMIT = 60


class CombinedPart(BaseModel):
    """One clip in a combined clip, by rank, with an optional label card shown
    at its start ("Hour 3")."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int = Field(ge=1)
    label: str = ""

    @field_validator("label")
    @classmethod
    def _one_short_line(cls, value: str) -> str:
        label = value.strip()
        if "\n" in label or "\r" in label:
            raise ValueError("a label card must be one line")
        if len(label) > CARD_LIMIT:
            raise ValueError(f"a label card must be at most {CARD_LIMIT} characters")
        return label


class Combined(BaseModel):
    """A VOD's kept clips joined into one longer Short (ADR 0032): its file
    in the drafts folder, the clips in VOD order with their cards, the
    music under it, its length, and the title, rating and thumbnail set in
    the review page. Not a moment, so no judged label."""

    model_config = ConfigDict(frozen=True)

    file: str
    parts: tuple[CombinedPart, ...] = Field(min_length=1)
    music: MusicBed | None = None
    duration_seconds: float = Field(gt=0)
    title: str | None = None
    rating: Literal["good", "ok"] | None = None
    thumbnail: str | None = None


class RenderManifest(BaseModel):
    """What one `render` run wrote to a video's folder. See docs/decisions/0019.

    `vertical`, `captions` and `titles` are the flags the run was given.
    `analysis` is the result JSON path as given on the command line.
    `styles` are the vertical styles rendered, in order (ADR 0025); empty for
    a landscape run or an older manifest. `combined` is the clips joined in
    the review page, if made (ADR 0032).
    """

    model_config = ConfigDict(frozen=True)

    source: str
    analysis: str
    detector: str | None
    vertical: bool
    captions: bool
    titles: bool
    clips: tuple[ManifestClip, ...]
    styles: tuple[VerticalStyleName, ...] = ()
    # Written as "summary" before the feature was renamed (ADR 0032).
    combined: Combined | None = Field(
        default=None, validation_alias=AliasChoices("combined", "summary")
    )


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
# How good a keeper is, set in the review page (ADR 0024, rating amendment).
Rating = Literal["good", "ok"]


class Label(Span):
    """A hand-labeled moment worth cutting. See docs/decisions/0012.

    Written as `start`/`end` timestamp strings, held as seconds. In a judged
    file (ADR 0012, V5 amendment) every label has a verdict: `post` is a draft
    as cut, `fix` a draft whose span was moved (`draft` keeps the old span),
    and `added` a moment no draft proposed. `style` is the vertical style the
    maintainer kept for the clip, when there were styles (ADR 0025).
    `rating` says whether a keeper is good enough to post or kept for the
    backlog, and `people` who is on screen, both from the review page (ADR
    0024, rating amendment). `cuts` are the ranges left out of the clip, in
    order and inside its span (ADR 0029), and `texts` the lines burned over
    it (ADR 0030). `caption_edits` are its caption fixes, key words and
    colour (ADR 0017, caption editing amendment). `zooms` and `hand_crops`
    are its punch-ins and hand-placed windows (ADR 0016 and 0025), and
    `audio` its volume, music and sound effects (ADR 0007).
    """

    note: str | None = None
    verdict: Verdict | None = None
    draft: Span | None = None
    style: VerticalStyleName | None = None
    rating: Rating | None = None
    people: tuple[str, ...] = ()
    cuts: tuple[Span, ...] = ()
    texts: tuple[OnScreenText, ...] = ()
    caption_edits: CaptionEdits | None = None
    zooms: tuple[Zoom, ...] = ()
    hand_crops: tuple[HandCrop, ...] = ()
    audio: ClipAudio | None = None

    @field_validator("people")
    @classmethod
    def _people_named_once(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        names = tuple(name.strip() for name in value)
        if any(not name for name in names):
            raise ValueError("a name in people is empty")
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("a name in people is given twice")
        return names

    @model_validator(mode="after")
    def _cuts_inside_in_order(self) -> "Label":
        previous = self.start_seconds
        for cut in self.cuts:
            if (
                not previous < cut.start_seconds
                or not cut.end_seconds < self.end_seconds
            ):
                raise ValueError(
                    "cuts must be in order, apart, and inside the label's span"
                )
            previous = cut.end_seconds
        return self

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
    """One video's labels, ordered by start.

    A file without verdicts has non-overlapping labels and no rejected spans.
    A judged file (ADR 0012, V5 amendment) has a verdict on every label and may
    list rejected spans, ordered by start; they may overlap each other and the
    labels. Its labels may overlap each other too (V7 item 2 amendment): the
    review page saves neighbours that overlap by less than the hit rule, which
    is checked there, not here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    labels: tuple[Label, ...]
    rejected: tuple[RejectedSpan, ...] = ()

    @model_validator(mode="after")
    def _labels_ordered(self) -> "LabeledVideo":
        for index in range(1, len(self.labels)):
            label, previous = self.labels[index], self.labels[index - 1]
            if self.judged:
                if label.start_seconds < previous.start_seconds:
                    raise ValueError(
                        f"labels must be ordered: labels.{index} starts before "
                        f"labels.{index - 1}"
                    )
            elif label.start_seconds < previous.end_seconds:
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


class Campaign(BaseModel):
    """A campaign a clip may be posted for. See docs/decisions/0028.

    A clip qualifies when at least `minimum` of `people` are on screen and,
    if `channels` is set, it comes from one of those channels.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    hashtag: str = Field(pattern=r"^#\S+$")
    minimum: int = Field(ge=1)
    people: tuple[str, ...] = Field(min_length=1)
    channels: tuple[str, ...] = ()


class PostRecord(BaseModel):
    """One clip the maintainer posted. See docs/decisions/0028.

    `clip` is `<drafts folder>/<file>` under the clips dir, with a forward
    slash; `video_id` is the YouTube ID from `url`.
    """

    model_config = ConfigDict(frozen=True)

    clip: str = Field(min_length=1)
    url: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    posted: date


class PostLog(BaseModel):
    """Every post recorded with `clipper posted`, oldest first."""

    model_config = ConfigDict(frozen=True)

    posts: tuple[PostRecord, ...] = ()
