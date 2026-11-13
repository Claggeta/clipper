"""A VOD's story in beats (ADR 0033): a note per chunk of the transcript,
the beats from the notes, and spans around each beat's key line."""

from collections.abc import Callable, Sequence

from clipper.core.interfaces import StoryWriter
from clipper.domain.errors import StoryError
from clipper.domain.models import Segment, StoryBeat, StoryNote, Transcript

# Set by the spike on two incremental-game VODs (ADR 0033).
CHUNK_SECONDS = 180.0
MIN_BEAT = 15.0
MAX_BEAT = 25.0
# A gap in speech this long ends a beat once it is long enough.
PAUSE = 0.8
# A beat starts at most this far before its key line's segment.
THOUGHT_BACK = 6.0
# A long next segment's words end at a gap this long.
WORD_GAP = 0.3
# The whole fits a Short with room to spare.
STORY_LIMIT = 170.0
# A key line timed this far outside its chunk is taken as the chunk's start.
KEY_SLACK = 5.0
_SENTENCE_END = (".", "?", "!")


class ChunkNote:
    """A note with its chunk's place: number, start and end, and the key
    line's time in seconds."""

    def __init__(self, part: int, start: float, end: float, note: StoryNote) -> None:
        self.part = part
        self.start = start
        self.end = end
        self.note = note
        key = parse_time(note.key_time)
        inside = key is not None and start - KEY_SLACK <= key <= end + KEY_SLACK
        self.key = key if key is not None and inside else start


def mmss(seconds: float) -> str:
    """`m:ss`, minutes not wrapped into hours, as the prompts show times."""
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def parse_time(text: str) -> float | None:
    """Seconds from `m:ss`, `h:mm:ss` or `[m:ss]`, or None."""
    parts = text.strip().strip("[]").split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def chunk_transcript(transcript: Transcript) -> list[tuple[Segment, ...]]:
    """The segments in chunks starting at least `CHUNK_SECONDS` apart."""
    chunks: list[tuple[Segment, ...]] = []
    current: list[Segment] = []
    start = 0.0
    for segment in transcript.segments:
        if current and segment.start_seconds - start >= CHUNK_SECONDS:
            chunks.append(tuple(current))
            current = []
        if not current:
            start = segment.start_seconds
        current.append(segment)
    if current:
        chunks.append(tuple(current))
    return chunks


def chunk_text(chunk: Sequence[Segment]) -> str:
    """A chunk as the note prompt shows it: each line with its time."""
    return "\n".join(f"[{mmss(s.start_seconds)}] {s.text.strip()}" for s in chunk)


def notes_listing(notes: Sequence[ChunkNote]) -> str:
    """The notes as the beats prompt shows them, in order."""
    return "\n".join(
        f"Part {n.part} [{mmss(n.start)}-{mmss(n.end)}] {n.note.kind}, importance "
        f"{n.note.importance:g}: {n.note.summary} (key line {n.note.key_time}: "
        f"{n.note.key_line})"
        for n in notes
    )


def clean_card(card: str) -> str:
    """A card for the screen: one line, no trailing full stop, cut to fit."""
    text = " ".join(card.split()).rstrip(".")
    return text[:60]


def beat_span(segments: Sequence[Segment], key: float) -> tuple[float, float]:
    """A beat around `key`: back to the start of the thought (segments that
    run straight into it, up to `THOUGHT_BACK` seconds before), on to
    `MIN_BEAT` and further to a pause or a sentence end, at most `MAX_BEAT`;
    a long next segment's words up to a break when still short."""
    if not segments:
        raise StoryError("No speech to cut a beat from")
    index = min(
        range(len(segments)),
        key=lambda i: (
            0.0
            if segments[i].start_seconds <= key <= segments[i].end_seconds
            else min(
                abs(segments[i].start_seconds - key), abs(segments[i].end_seconds - key)
            )
        ),
    )
    first = index
    while (
        first > 0
        and segments[first].start_seconds - segments[first - 1].end_seconds < PAUSE
        and segments[index].start_seconds - segments[first - 1].start_seconds
        <= THOUGHT_BACK
    ):
        first -= 1
    start = segments[first].start_seconds
    last = index
    end = segments[last].end_seconds
    while last + 1 < len(segments):
        following = segments[last + 1]
        if following.end_seconds - start > MAX_BEAT:
            break
        long_enough = end - start >= MIN_BEAT
        paused = following.start_seconds - end >= PAUSE
        sentence = segments[last].text.strip().endswith(_SENTENCE_END)
        if long_enough and (paused or sentence):
            break
        last += 1
        end = following.end_seconds
    if end - start < MIN_BEAT and last + 1 < len(segments):
        words = segments[last + 1].words
        for i, word in enumerate(words):
            if word.end_seconds - start > MAX_BEAT:
                break
            end = word.end_seconds
            gap = words[i + 1].start_seconds - end if i + 1 < len(words) else 1.0
            at_break = gap >= WORD_GAP or word.text.strip().endswith(
                (*_SENTENCE_END, ",")
            )
            if end - start >= MIN_BEAT and at_break:
                break
    return round(start, 2), round(min(end, start + MAX_BEAT), 2)


def fit_beats(
    beats: list[tuple[StoryBeat, float]], limit: float = STORY_LIMIT
) -> list[StoryBeat]:
    """Beats in time order, overlapping ones merged into the first, the
    least important middle ones dropped until the whole fits `limit`.
    Each beat comes with its note's importance."""
    ordered = sorted(beats, key=lambda pair: pair[0].start_seconds)
    merged: list[tuple[StoryBeat, float]] = []
    for beat, importance in ordered:
        if merged and beat.start_seconds < merged[-1][0].end_seconds:
            continue
        merged.append((beat, importance))
    while (
        sum(b.end_seconds - b.start_seconds for b, _ in merged) > limit
        and len(merged) > 2
    ):
        weakest = min(merged[1:-1], key=lambda pair: pair[1])
        merged.remove(weakest)
    return [beat for beat, _ in merged]


def plan_story(
    transcript: Transcript,
    writer: StoryWriter,
    progress: Callable[[str], None] | None = None,
) -> list[StoryBeat]:
    """The VOD's story as beats (ADR 0033): a note per chunk, the beats from
    the notes, a span around each beat's key line, fitted to a Short.
    `progress` is told each step, for the page. Raises `StoryError` when
    there is nothing to plan or the beats name no part."""
    chunks = chunk_transcript(transcript)
    if not chunks:
        raise StoryError("The transcript has no speech to summarize")
    notes: list[ChunkNote] = []
    for number, chunk in enumerate(chunks, start=1):
        if progress is not None:
            progress(f"Reading the stream: note {number} of {len(chunks)}")
        notes.append(
            ChunkNote(
                number,
                chunk[0].start_seconds,
                chunk[-1].end_seconds,
                writer.note(chunk_text(chunk)),
            )
        )
    if progress is not None:
        progress("Choosing the story")
    by_part = {note.part: note for note in notes}
    picked: list[tuple[StoryBeat, float]] = []
    for choice in writer.beats(notes_listing(notes)):
        note = by_part.get(choice.part)
        if note is None:
            continue
        nearby = [
            s
            for s in transcript.segments
            if note.start - 30 <= s.start_seconds < note.end + 40
        ]
        start, end = beat_span(nearby, note.key)
        picked.append(
            (
                StoryBeat(
                    start_seconds=start,
                    end_seconds=end,
                    card=clean_card(choice.card),
                    why=choice.why.strip(),
                ),
                note.note.importance,
            )
        )
    if not picked:
        raise StoryError("The model picked no parts of the stream as beats")
    return fit_beats(picked)
