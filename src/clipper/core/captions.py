from collections.abc import Iterator
from typing import NamedTuple

from clipper.core.cuts import Cut, TimeMap
from clipper.domain.models import (
    CaptionEdits,
    ClipCandidate,
    OnScreenText,
    Segment,
    Transcript,
    Word,
)

# libass reads `{...}` as override tags and `\n`, `\N` and `\h` as a line break
# or a space, so spoken text containing them could be dropped or restyled
# (ADR 0010). `<` and `>` were markup in ffmpeg's SRT reader; they are still
# removed, as ADR 0017 keeps 0010's rules. Only the builder's own tags use
# braces.
_MARKUP = str.maketrans("", "", "{}<>\\")

# ADR 0017, item 4 amendment. A chunk is at most this many words, and ends
# early at the end of a segment or after a word that ends a sentence.
_WORDS_PER_CHUNK = 3
_SENTENCE_END = (".", "?", "!")

# Style values chosen by eye from real frames, not tuned (ADR 0017). Colours
# are ASS's &HAABBGGRR. The active word is drawn in _HIGHLIGHT, then `\r`
# resets to the style.
_FONT = "Arial"
_WHITE = "&H00FFFFFF"
_BLACK = "&H00000000"
_RESET = "{\\r}"
# The active word's colour, and key words' (ADR 0017, caption editing
# amendment), as RGB; yellow is the original highlight.
HIGHLIGHTS = {
    "yellow": "#FFFF00",
    "green": "#00E676",
    "cyan": "#00E5FF",
    "pink": "#FF4FD8",
    "orange": "#FF9100",
}


def _colour_tag(name: str) -> str:
    """`{\\c&HBBGGRR&}`, ASS's colour override, for a highlight name."""
    rgb = HIGHLIGHTS[name].removeprefix("#")
    return f"{{\\c&H00{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}&}}"


_HIGHLIGHT = _colour_tag("yellow")


class _Layout(NamedTuple):
    width: int
    height: int
    font_size: int
    outline: int
    margin_h: int
    margin_v: int


# The vertical frame is ADR 0016's 1080x1920. Other clips keep the source
# frame, whose size the builder does not know: libass scales a 16:9 canvas to
# it exactly, and other shapes are checked by manual step C8.
_VERTICAL = _Layout(1080, 1920, font_size=80, outline=6, margin_h=60, margin_v=420)
_SOURCE = _Layout(1920, 1080, font_size=72, outline=5, margin_h=80, margin_v=80)


class _TextLayout(NamedTuple):
    """On-screen text (ADR 0030): its size, the box's padding, and where
    each place puts it, as an anchor point on the canvas."""

    font_size: int
    padding: int
    top: tuple[int, int]
    middle: tuple[int, int]


# Checked by eye on a rendered frame: below the area Shorts' own buttons
# and title cover at the top, and the frame's centre. The box is libass's
# one box per event, half transparent (ADR 0030, one box amendment).
_VERTICAL_TEXT = _TextLayout(84, 18, top=(540, 300), middle=(540, 960))
_SOURCE_TEXT = _TextLayout(64, 14, top=(960, 80), middle=(960, 540))
_HALF_BLACK = "&H80000000"
_CLEAR = "&HFF000000"


class CaptionWord(NamedTuple):
    """One word of a caption chunk, in source seconds (ADR 0031); `key`
    words stay in the highlight colour (ADR 0017)."""

    start: float
    end: float
    text: str
    key: bool = False


class Captions(NamedTuple):
    """ASS text for one clip, whether any cue lacked word times, and whether
    any speech is in it (beside on-screen texts)."""

    text: str
    from_segments: bool
    speech: bool = False


def _to_ms(seconds: float) -> int:
    return round(seconds * 1000)


def _centis(ms: int) -> int:
    """Milliseconds to the nearest centisecond, half up."""
    return (ms + 5) // 10


def _timestamp(ms: int) -> str:
    """ASS time, H:MM:SS.cc."""
    hours, rest = divmod(_centis(ms), 360_000)
    minutes, rest = divmod(rest, 6_000)
    secs, centis = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _clean(text: str) -> str:
    return " ".join(text.translate(_MARKUP).split())


def _text_style(layout: _Layout, text: _TextLayout) -> str:
    """The on-screen text's style: bold white on one half-dark box.

    BorderStyle 3 draws a box per line, padded by the outline, so wrapped
    lines' boxes overlapped and printed darker. BorderStyle 4 (libass
    0.17 or newer) draws one box behind the whole event in the back
    colour, still padded by the outline, whose own colour is clear.
    """
    return ",".join(
        str(v)
        for v in (
            "Text",
            _FONT,
            text.font_size,
            _WHITE,
            _WHITE,
            _CLEAR,  # no outline round the letters
            _HALF_BLACK,  # the box
            -1,  # bold
            0,
            0,
            0,
            100,
            100,
            0,
            0,
            4,  # one box per event
            text.padding,
            0,
            8,  # top centre; each event sets its own point
            layout.margin_h,
            layout.margin_h,
            0,
            1,
        )
    )


def _header(layout: _Layout, text: _TextLayout | None = None) -> str:
    style = ",".join(
        str(v)
        for v in (
            "Default",
            _FONT,
            layout.font_size,
            _WHITE,
            _WHITE,
            _BLACK,
            _BLACK,
            -1,  # bold
            0,
            0,
            0,
            100,
            100,
            0,
            0,
            1,  # outline and shadow, not an opaque box
            layout.outline,
            0,  # no shadow
            2,  # bottom centre
            layout.margin_h,
            layout.margin_h,
            layout.margin_v,
            1,
        )
    )
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {layout.width}\n"
        f"PlayResY: {layout.height}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: {style}\n"
        + (f"Style: {_text_style(layout, text)}\n" if text else "")
        + "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )


def _event(start: int, end: int, text: str) -> str | None:
    """A Dialogue line, or None when it has no length in centiseconds."""
    if _centis(end) <= _centis(start):
        return None
    return f"Dialogue: 0,{_timestamp(start)},{_timestamp(end)},Default,,0,0,0,,{text}\n"


class _Timed(NamedTuple):
    start: int
    end: int
    text: str
    key: bool = False


def _kept_words(
    words: tuple[Word, ...],
    clip_start: int,
    clip_end: int,
    cuts: TimeMap | None = None,
    edits: CaptionEdits | None = None,
) -> Iterator[_Timed]:
    """The words captions show, in source milliseconds: those that start
    inside the clip and not inside a cut, their ends clamped to the clip.

    With edits, a fixed word takes its new text (none hides it) and a key
    word is marked; both are found by the word's start, to the millisecond.
    """
    fixes = {_to_ms(f.start_seconds): f.text for f in edits.fixes} if edits else {}
    keys = {_to_ms(k) for k in edits.keys} if edits else set()
    for word in words:
        start = _to_ms(word.start_seconds)
        text = _clean(fixes.get(start, word.text))
        if not (clip_start <= start < clip_end) or not text:
            continue
        if cuts is not None and cuts.output(start) is None:
            continue
        end = min(_to_ms(word.end_seconds), clip_end)
        yield _Timed(start, end, text, start in keys)


def _words_in_clip(
    words: tuple[Word, ...],
    clip_start: int,
    clip_end: int,
    cuts: TimeMap | None = None,
    edits: CaptionEdits | None = None,
) -> list[_Timed]:
    """Words that start inside the clip, clamped to it and shifted to zero.

    With cuts (in milliseconds), a word that starts inside a cut is dropped
    and the rest move to their times in the cut clip (ADR 0029).
    """
    kept: list[_Timed] = []
    for word in _kept_words(words, clip_start, clip_end, cuts, edits):
        if cuts is None:
            kept.append(
                word._replace(start=word.start - clip_start, end=word.end - clip_start)
            )
            continue
        moved = cuts.output(word.start)
        assert moved is not None  # _kept_words drops words inside a cut
        kept.append(
            word._replace(start=round(moved), end=round(cuts.clamped(word.end)))
        )
    return kept


def _chunks(words: list[_Timed]) -> Iterator[list[_Timed]]:
    chunk: list[_Timed] = []
    for word in words:
        chunk.append(word)
        if len(chunk) == _WORDS_PER_CHUNK or word.text.endswith(_SENTENCE_END):
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _word_events(chunk: list[_Timed], highlight: str = _HIGHLIGHT) -> Iterator[str]:
    """One event per word, showing the chunk with that word highlighted.

    A word's event lasts until the next word in the chunk starts, so the chunk
    does not flicker between words. The last word's ends with the word. Key
    words are in the highlight colour throughout.
    """
    for i, word in enumerate(chunk):
        end = chunk[i + 1].start if i + 1 < len(chunk) else word.end
        text = " ".join(
            f"{highlight}{w.text}{_RESET}" if j == i or w.key else w.text
            for j, w in enumerate(chunk)
        )
        event = _event(word.start, end, text)
        if event:
            yield event


def _segment_event(
    segment: Segment, clip_start: int, clip_end: int, cuts: TimeMap | None = None
) -> str | None:
    """The whole segment as one cue, for a segment without word times; with
    cuts, its ends move to the cut clip and a cut inside it is shown through."""
    start = max(_to_ms(segment.start_seconds), clip_start)
    end = min(_to_ms(segment.end_seconds), clip_end)
    text = _clean(segment.text)
    if end <= start or not text:
        return None
    if cuts is None:
        return _event(start - clip_start, end - clip_start, text)
    return _event(round(cuts.clamped(start)), round(cuts.clamped(end)), text)


def caption_chunks(
    transcript: Transcript,
    candidate: ClipCandidate,
    cuts: tuple[Cut, ...] = (),
    edits: CaptionEdits | None = None,
) -> list[list[CaptionWord]]:
    """The word chunks `build_ass` shows for the clip, in source seconds,
    for the review page's live preview (ADR 0031). The same words, fixes
    and chunks; segments without word times give none."""
    clip_start = _to_ms(candidate.start_seconds)
    clip_end = _to_ms(candidate.end_seconds)
    time_map = (
        TimeMap(clip_start, clip_end, [(_to_ms(a), _to_ms(b)) for a, b in cuts])
        if cuts
        else None
    )
    chunks: list[list[CaptionWord]] = []
    for segment in transcript.segments:
        words = list(_kept_words(segment.words, clip_start, clip_end, time_map, edits))
        for chunk in _chunks(words):
            chunks.append(
                [
                    CaptionWord(w.start / 1000, w.end / 1000, w.text, w.key)
                    for w in chunk
                ]
            )
    return chunks


def preview_layout(vertical: bool) -> dict[str, object]:
    """The caption and on-screen text sizes and places on the canvas, for
    the live preview to draw as the burn-in does (ADR 0031)."""
    layout = _VERTICAL if vertical else _SOURCE
    text = _VERTICAL_TEXT if vertical else _SOURCE_TEXT
    return {
        "width": layout.width,
        "height": layout.height,
        "caption_size": layout.font_size,
        "caption_outline": layout.outline,
        "caption_margin_h": layout.margin_h,
        "caption_margin_v": layout.margin_v,
        "text_size": text.font_size,
        "text_padding": text.padding,
        "text_top": list(text.top),
        "text_middle": list(text.middle),
        "highlights": HIGHLIGHTS,
    }


def build_ass(
    transcript: Transcript,
    candidate: ClipCandidate,
    *,
    vertical: bool,
    cuts: tuple[Cut, ...] = (),
    texts: tuple[OnScreenText, ...] = (),
    edits: CaptionEdits | None = None,
) -> Captions:
    """Build styled ASS captions for the part of `transcript` inside the clip.

    See docs/decisions/0017. Words that start inside the candidate's span are
    kept, clamped to it, and shifted so the clip starts at zero. They are
    grouped into short chunks, and each word is its own event showing its
    chunk with that word highlighted. A segment without words becomes one cue
    with its whole text, as in ADR 0010, and sets `from_segments`. Times are
    whole milliseconds until they are written as centiseconds; an event with
    no length after that is dropped. `{`, `}`, `<`, `>` and backslash are
    removed from spoken text. `vertical` picks the 1080x1920 layout; otherwise
    the source-frame layout.

    `cuts` are source-time ranges removed from the clip (ADR 0029): words
    that start inside one are dropped, and every time moves to the cut clip.

    `texts` are lines burned over the clip (ADR 0030), in a style of their
    own, each pinned to its place; their times are the finished clip's,
    clamped to its length, and one starting past the end is dropped.
    `speech` says whether any caption came from the transcript.

    `edits` fix or hide words, mark key words and set the highlight colour
    (ADR 0017, caption editing amendment); segments without word times are
    left as they are.

    The text is "" when no event remains. The caller decides what that means.
    """
    clip_start = _to_ms(candidate.start_seconds)
    clip_end = _to_ms(candidate.end_seconds)
    time_map = (
        TimeMap(clip_start, clip_end, [(_to_ms(a), _to_ms(b)) for a, b in cuts])
        if cuts
        else None
    )
    events: list[str] = []
    from_segments = False
    for segment in transcript.segments:
        if not segment.words:
            event = _segment_event(segment, clip_start, clip_end, time_map)
            if event:
                events.append(event)
                from_segments = True
            continue
        words = _words_in_clip(segment.words, clip_start, clip_end, time_map, edits)
        highlight = _colour_tag(edits.highlight) if edits else _HIGHLIGHT
        for chunk in _chunks(words):
            events.extend(_word_events(chunk, highlight))
    speech = bool(events)
    text_layout = _VERTICAL_TEXT if vertical else _SOURCE_TEXT
    length = round(time_map.duration) if time_map else clip_end - clip_start
    shown = [e for t in texts if (e := _text_event(t, text_layout, length))]
    events.extend(shown)
    if not events:
        return Captions("", from_segments=False)
    layout = _VERTICAL if vertical else _SOURCE
    header = _header(layout, text_layout if shown else None)
    return Captions(header + "".join(events), from_segments, speech)


def _text_event(text: OnScreenText, layout: _TextLayout, length: int) -> str | None:
    """One on-screen text as a Dialogue line on the layer above captions,
    pinned to its place so libass does not move it out of another's way."""
    start = _to_ms(text.start_seconds)
    end = min(_to_ms(text.end_seconds), length)
    words = _clean(text.text)
    if start >= length or not words or _centis(end) <= _centis(start):
        return None
    align, (x, y) = (8, layout.top) if text.place == "top" else (5, layout.middle)
    return (
        f"Dialogue: 1,{_timestamp(start)},{_timestamp(end)},Text,,0,0,0,,"
        f"{{\\an{align}\\pos({x},{y})}}{words}\n"
    )


def clip_text(transcript: Transcript, candidate: ClipCandidate) -> str:
    """The words spoken inside the clip, as one line of plain text.

    Selects what `build_ass` shows: words that start inside the candidate's
    span, and the whole text of a segment without words that overlaps it. The
    markup characters captions remove are removed here too. "" when no speech
    is in range. Used for titles (see docs/decisions/0018).
    """
    clip_start = _to_ms(candidate.start_seconds)
    clip_end = _to_ms(candidate.end_seconds)
    parts: list[str] = []
    for segment in transcript.segments:
        if not segment.words:
            if _segment_event(segment, clip_start, clip_end):
                parts.append(_clean(segment.text))
            continue
        parts.extend(
            w.text for w in _words_in_clip(segment.words, clip_start, clip_end)
        )
    return " ".join(parts)
