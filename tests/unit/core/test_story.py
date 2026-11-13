import pytest

from clipper.core.story import (
    beat_span,
    chunk_transcript,
    clean_card,
    fit_beats,
    notes_listing,
    parse_time,
    plan_story,
)
from clipper.domain.errors import StoryError
from clipper.domain.models import (
    BeatChoice,
    Segment,
    StoryBeat,
    StoryNote,
    Transcript,
    Word,
)

# Constructed transcripts and notes, not a real stream.


def _seg(start: float, end: float, text: str = "words") -> Segment:
    return Segment(start_seconds=start, end_seconds=end, text=text)


def test_parse_time_reads_minutes_hours_and_brackets() -> None:
    assert parse_time("[3:10]") == 190
    assert parse_time("1:02:03") == 3723
    assert parse_time("75:00") == 4500
    assert parse_time("soon") is None


def test_chunks_start_at_least_three_minutes_apart() -> None:
    segments = tuple(_seg(t, t + 5) for t in range(0, 600, 60))
    chunks = chunk_transcript(Transcript(segments=segments))
    assert [c[0].start_seconds for c in chunks] == [0, 180, 360, 540]


def test_clean_card_drops_the_full_stop_and_spaces() -> None:
    assert clean_card("  Cut grass   with clippers. ") == "Cut grass with clippers"


def test_a_beat_starts_at_the_thought_and_ends_at_a_sentence() -> None:
    segments = [
        _seg(0.0, 4.0, "So we start."),
        _seg(10.0, 14.0, "Then this happened"),
        _seg(14.3, 20.0, "and it ran on"),
        _seg(20.2, 27.5, "until here."),
        _seg(27.7, 33.0, "More talk."),
    ]
    # The key line at 15 s runs on from 10 s, a pause after 4 s; it is long
    # enough by 27.5 s, which ends a sentence.
    assert beat_span(segments, 15.0) == (10.0, 27.5)


def test_a_short_beat_takes_a_long_next_segments_words_to_a_break() -> None:
    long = Segment(
        start_seconds=6.0,
        end_seconds=40.0,
        text="one two three four",
        words=(
            Word(start_seconds=6.0, end_seconds=9.0, text="one"),
            Word(start_seconds=9.1, end_seconds=15.5, text="two"),
            Word(start_seconds=16.0, end_seconds=20.0, text="three"),
            Word(start_seconds=20.1, end_seconds=40.0, text="four"),
        ),
    )
    segments = [_seg(0.0, 5.0, "Oh no, bees."), long]
    assert beat_span(segments, 2.0) == (0.0, 15.5)


def test_beats_fit_the_limit_by_dropping_the_weakest_middle_ones() -> None:
    def beat(start: float) -> StoryBeat:
        return StoryBeat(start_seconds=start, end_seconds=start + 20, card="c")

    beats = [(beat(0), 0.2), (beat(100), 0.9), (beat(200), 0.3), (beat(300), 0.1)]
    # 80 s over a 50 s limit: the 0.3 one goes, then the 0.9 one; first and
    # last stay.
    assert [b.start_seconds for b in fit_beats(beats, limit=50)] == [0, 300]
    overlapping = [(beat(0), 0.5), (beat(10), 0.5), (beat(50), 0.5)]
    assert [b.start_seconds for b in fit_beats(overlapping)] == [0, 50]


class FakeWriter:
    def __init__(self, parts: list[int]) -> None:
        self.parts = parts
        self.listing = ""

    def note(self, text: str) -> StoryNote:
        first = text.split("]")[0]
        return StoryNote(
            summary=f"About {first}]",
            kind="progress",
            importance=0.5,
            key_time=first + "]",
            key_line="line",
        )

    def beats(self, listing: str) -> list[BeatChoice]:
        self.listing = listing
        return [BeatChoice(part=p, card=f"Card {p}.", why="Why.") for p in self.parts]


def _stream() -> Transcript:
    return Transcript(
        segments=tuple(_seg(t, t + 18.0, f"Line {t}.") for t in range(0, 900, 20))
    )


def test_a_story_is_planned_from_notes_with_progress() -> None:
    steps: list[str] = []
    writer = FakeWriter([1, 3, 99])
    beats = plan_story(_stream(), writer, steps.append)
    assert steps[0] == "Reading the stream: note 1 of 5"
    assert steps[-1] == "Choosing the story"
    assert "Part 3 [6:00-8:58] progress, importance 0.5" in writer.listing
    # Part 99 does not exist and is skipped; each beat is from its key line.
    assert [(b.start_seconds, b.card) for b in beats] == [
        (0.0, "Card 1"),
        (360.0, "Card 3"),
    ]
    assert all(15 <= b.end_seconds - b.start_seconds <= 25 for b in beats)


def test_a_story_needs_speech_and_a_picked_part() -> None:
    with pytest.raises(StoryError, match="no speech"):
        plan_story(Transcript(segments=()), FakeWriter([1]))
    with pytest.raises(StoryError, match="picked no parts"):
        plan_story(_stream(), FakeWriter([42]))


def test_the_notes_listing_names_each_part() -> None:
    from clipper.core.story import ChunkNote

    note = StoryNote(
        summary="Buys scissors.",
        kind="milestone",
        importance=0.9,
        key_time="3:10",
        key_line="scissor time",
    )
    listing = notes_listing([ChunkNote(2, 180.0, 359.0, note)])
    assert listing == (
        "Part 2 [3:00-5:59] milestone, importance 0.9: Buys scissors. "
        "(key line 3:10: scissor time)"
    )
