from clipper.core.captions import Captions, build_ass, clip_text
from clipper.domain.models import ClipCandidate, Segment, Transcript, Word

# All transcripts here are constructed, not Whisper output.

_ON = "{\\c&H0000FFFF&}"
_OFF = "{\\r}"


def _c(start: float, end: float) -> ClipCandidate:
    # Constructed candidate; the reason text is arbitrary.
    return ClipCandidate(
        start_seconds=start, end_seconds=end, score=0.5, reasons=("test",)
    )


def _seg(start: float, end: float, text: str) -> Segment:
    """A segment without word times, as in v0.3 JSON."""
    return Segment(start_seconds=start, end_seconds=end, text=text)


def _wseg(*words: tuple[float, float, str]) -> Segment:
    """A segment spanning its words, with their text joined by spaces."""
    return Segment(
        start_seconds=words[0][0],
        end_seconds=words[-1][1],
        text=" ".join(t for _, _, t in words),
        words=tuple(Word(start_seconds=s, end_seconds=e, text=t) for s, e, t in words),
    )


def _t(*segments: Segment) -> Transcript:
    return Transcript(segments=segments)


def _events(captions: Captions) -> list[str]:
    """Each Dialogue line as 'start,end,text'."""
    return [
        ",".join(line.split(",", 9)[i] for i in (1, 2, 9))
        for line in captions.text.splitlines()
        if line.startswith("Dialogue: ")
    ]


def _hl(words: list[str], active: int) -> str:
    return " ".join(
        f"{_ON}{w}{_OFF}" if i == active else w for i, w in enumerate(words)
    )


def test_each_word_is_an_event_showing_its_chunk_highlighted() -> None:
    seg = _wseg((10.0, 10.4, "one"), (10.5, 10.9, "two"), (11.0, 11.6, "three"))
    captions = build_ass(_t(seg), _c(10.0, 20.0), vertical=True)
    words = ["one", "two", "three"]
    assert _events(captions) == [
        f"0:00:00.00,0:00:00.50,{_hl(words, 0)}",
        f"0:00:00.50,0:00:01.00,{_hl(words, 1)}",
        f"0:00:01.00,0:00:01.60,{_hl(words, 2)}",
    ]
    assert captions.from_segments is False


def test_chunks_hold_at_most_three_words() -> None:
    seg = _wseg(*((float(i), i + 0.5, f"w{i}") for i in range(5)))
    events = _events(build_ass(_t(seg), _c(0.0, 10.0), vertical=True))
    assert len(events) == 5
    assert events[2].endswith(_hl(["w0", "w1", "w2"], 2))
    assert events[3] == f"0:00:03.00,0:00:04.00,{_hl(['w3', 'w4'], 0)}"
    # The last word of a chunk ends with the word, not at the next chunk.
    assert events[2].startswith("0:00:02.00,0:00:02.50,")


def test_a_chunk_ends_after_a_sentence() -> None:
    seg = _wseg((0.0, 0.5, "Done."), (1.0, 1.5, "Next"), (2.0, 2.5, "one?"))
    events = _events(build_ass(_t(seg), _c(0.0, 10.0), vertical=False))
    assert events[0] == f"0:00:00.00,0:00:00.50,{_hl(['Done.'], 0)}"
    assert events[1].endswith(_hl(["Next", "one?"], 0))


def test_a_chunk_never_spans_two_segments() -> None:
    first = _wseg((0.0, 0.5, "a"), (0.5, 1.0, "b"))
    second = _wseg((1.0, 1.5, "c"), (1.5, 2.0, "d"))
    events = _events(build_ass(_t(first, second), _c(0.0, 10.0), vertical=True))
    assert events[1].endswith(_hl(["a", "b"], 1))
    assert events[2].endswith(_hl(["c", "d"], 0))


def test_words_starting_inside_the_clip_are_kept_and_clamped() -> None:
    first = _wseg((9.5, 10.2, "before"), (10.2, 10.6, "in"))
    second = _wseg((19.8, 20.5, "late"))
    events = _events(build_ass(_t(first, second), _c(10.0, 20.0), vertical=True))
    # "before" starts before the clip and is dropped; "late" ends at the edge.
    assert events == [
        f"0:00:00.20,0:00:00.60,{_hl(['in'], 0)}",
        f"0:00:09.80,0:00:10.00,{_hl(['late'], 0)}",
    ]


def test_a_word_is_held_until_the_next_word_in_its_chunk() -> None:
    seg = _wseg((1.0, 1.2, "wait"), (4.0, 4.5, "what"))
    events = _events(build_ass(_t(seg), _c(0.0, 5.0), vertical=True))
    assert events[0] == f"0:00:01.00,0:00:04.00,{_hl(['wait', 'what'], 0)}"


def test_a_word_starting_at_the_clip_end_is_dropped() -> None:
    seg = _wseg((20.0, 20.5, "after"))
    assert build_ass(_t(seg), _c(10.0, 20.0), vertical=True) == ("", False)


def test_a_zero_length_word_shows_in_its_chunk_but_is_never_highlighted() -> None:
    seg = _wseg((1.0, 1.0, "a"), (1.0, 1.5, "b"))
    events = _events(build_ass(_t(seg), _c(0.0, 5.0), vertical=True))
    assert events == [f"0:00:01.00,0:00:01.50,{_hl(['a', 'b'], 1)}"]


def test_times_round_to_centiseconds() -> None:
    # 4 ms rounds down and 5 ms rounds up, so "x" has no length and is
    # dropped. 33.1 - 32.43 is not exactly 0.67 in floats.
    seg = _wseg((32.431, 32.434, "x"), (32.434, 32.435, "y"), (32.435, 33.1, "z"))
    events = _events(build_ass(_t(seg), _c(32.43, 40.0), vertical=True))
    words = ["x", "y", "z"]
    assert events == [
        f"0:00:00.00,0:00:00.01,{_hl(words, 1)}",
        f"0:00:00.01,0:00:00.67,{_hl(words, 2)}",
    ]


def test_timestamp_past_one_hour_and_ten_hours() -> None:
    transcript = _t(_wseg((3700.0, 3701.5, "late")), _wseg((36000.0, 36001.0, "x")))
    events = _events(build_ass(transcript, _c(0.0, 40000.0), vertical=True))
    assert events[0].startswith("1:01:40.00,1:01:41.50,")
    assert events[1].startswith("10:00:00.00,10:00:01.00,")


def test_markup_characters_are_removed_from_words() -> None:
    seg = _wseg((0.0, 0.5, "{a}"), (0.5, 1.0, "<b>"), (1.0, 1.5, "C:\\new"))
    events = _events(build_ass(_t(seg), _c(0.0, 5.0), vertical=True))
    assert events[0].endswith(_hl(["a", "b", "C:new"], 0))


def test_a_word_that_is_only_markup_is_dropped() -> None:
    seg = _wseg((0.0, 0.5, "{}"), (0.5, 1.0, "kept"))
    events = _events(build_ass(_t(seg), _c(0.0, 5.0), vertical=True))
    assert events == [f"0:00:00.50,0:00:01.00,{_hl(['kept'], 0)}"]


def test_a_segment_without_words_is_one_whole_cue() -> None:
    captions = build_ass(
        _t(_seg(8.0, 12.0, "so this is it")), _c(10.0, 20.0), vertical=True
    )
    assert _events(captions) == ["0:00:00.00,0:00:02.00,so this is it"]
    assert captions.from_segments is True


def test_segment_cues_keep_0010s_rules() -> None:
    transcript = _t(
        _seg(5.0, 10.0, "touches the start"),
        _seg(11.0, 12.0, "  a {\\an8}b\n<i>c</i>  "),
        _seg(13.0, 14.0, " \n "),
    )
    captions = build_ass(transcript, _c(10.0, 20.0), vertical=False)
    assert _events(captions) == ["0:00:01.00,0:00:02.00,a an8b ic/i"]


def test_mixed_segments_set_from_segments() -> None:
    transcript = _t(_wseg((1.0, 1.5, "word")), _seg(2.0, 3.0, "no words"))
    captions = build_ass(transcript, _c(0.0, 5.0), vertical=True)
    assert _events(captions) == [
        f"0:00:01.00,0:00:01.50,{_hl(['word'], 0)}",
        "0:00:02.00,0:00:03.00,no words",
    ]
    assert captions.from_segments is True


def test_from_segments_is_false_when_the_segment_cue_is_outside_the_clip() -> None:
    transcript = _t(_wseg((1.0, 1.5, "word")), _seg(8.0, 9.0, "outside"))
    assert build_ass(transcript, _c(0.0, 5.0), vertical=True).from_segments is False


def test_no_speech_in_range_returns_empty_text() -> None:
    assert build_ass(_t(_seg(0.0, 1.0, "early")), _c(10.0, 20.0), vertical=True) == (
        "",
        False,
    )
    assert build_ass(Transcript(segments=()), _c(0.0, 5.0), vertical=False) == (
        "",
        False,
    )


def test_vertical_layout_is_1080x1920() -> None:
    text = build_ass(_t(_seg(0.0, 1.0, "x")), _c(0.0, 5.0), vertical=True).text
    assert "PlayResX: 1080\nPlayResY: 1920\n" in text


def test_source_layout_is_a_16_by_9_canvas() -> None:
    text = build_ass(_t(_seg(0.0, 1.0, "x")), _c(0.0, 5.0), vertical=False).text
    assert "PlayResX: 1920\nPlayResY: 1080\n" in text


def test_header_has_one_bold_bottom_centre_style_and_events_follow_it() -> None:
    text = build_ass(_t(_seg(0.0, 1.0, "x")), _c(0.0, 5.0), vertical=True).text
    lines = text.splitlines()
    assert lines[0] == "[Script Info]"
    assert "ScaledBorderAndShadow: yes" in lines
    styles = [line for line in lines if line.startswith("Style: ")]
    assert len(styles) == 1
    fields = styles[0].removeprefix("Style: ").split(",")
    assert fields[0] == "Default"
    assert fields[7] == "-1"  # bold
    assert fields[18] == "2"  # bottom centre
    assert lines.index("[Events]") < lines.index(
        "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,x"
    )
    assert text.endswith("\n")
    assert "\r" not in text


def test_clip_text_joins_the_words_that_start_inside_the_clip() -> None:
    transcript = _t(
        _wseg((0.0, 0.5, "before"), (0.9, 1.2, "edge")),
        _wseg((2.0, 2.5, "one"), (2.6, 3.0, "two")),
        _wseg((4.0, 4.5, "three"), (5.0, 5.5, "after")),
    )
    assert clip_text(transcript, _c(1.0, 5.0)) == "one two three"


def test_clip_text_uses_the_whole_text_of_a_segment_without_words() -> None:
    transcript = _t(
        _seg(0.0, 2.0, "  starts  before "),
        _wseg((3.0, 3.5, "word")),
        _seg(9.0, 12.0, "ends after"),
        _seg(12.0, 13.0, "outside"),
    )
    assert clip_text(transcript, _c(1.0, 10.0)) == "starts before word ends after"


def test_clip_text_removes_caption_markup() -> None:
    transcript = _t(_seg(0.0, 2.0, "a {b} <i>c</i> d\\N"))
    assert clip_text(transcript, _c(0.0, 2.0)) == "a b ic/i dN"


def test_clip_text_is_empty_with_no_speech_in_range() -> None:
    transcript = _t(_seg(0.0, 1.0, "early"), _wseg((5.0, 5.5, "late")))
    assert clip_text(transcript, _c(1.0, 5.0)) == ""
