from clipper.core.captions import build_srt
from clipper.domain.models import ClipCandidate, Segment, Transcript


def _c(start: float, end: float) -> ClipCandidate:
    # Constructed candidate; the reason text is arbitrary.
    return ClipCandidate(
        start_seconds=start, end_seconds=end, score=0.5, reasons=("test",)
    )


def _t(*spans: tuple[float, float, str]) -> Transcript:
    # Constructed transcript, not Whisper output.
    return Transcript(
        segments=tuple(
            Segment(start_seconds=s, end_seconds=e, text=text) for s, e, text in spans
        )
    )


def test_segment_inside_is_shifted_to_clip_start() -> None:
    srt = build_srt(_t((12.0, 14.5, "hello there")), _c(10.0, 20.0))
    assert srt == "1\n00:00:02,000 --> 00:00:04,500\nhello there\n"


def test_segment_straddling_start_is_clamped_and_keeps_full_text() -> None:
    srt = build_srt(_t((8.0, 12.0, "so this is it")), _c(10.0, 20.0))
    assert srt == "1\n00:00:00,000 --> 00:00:02,000\nso this is it\n"


def test_segment_straddling_end_is_clamped() -> None:
    srt = build_srt(_t((18.0, 23.0, "and then")), _c(10.0, 20.0))
    assert srt == "1\n00:00:08,000 --> 00:00:10,000\nand then\n"


def test_segment_spanning_whole_clip_is_clamped_both_sides() -> None:
    srt = build_srt(_t((5.0, 30.0, "long")), _c(10.0, 20.0))
    assert srt == "1\n00:00:00,000 --> 00:00:10,000\nlong\n"


def test_segments_touching_the_edges_are_excluded() -> None:
    transcript = _t((5.0, 10.0, "before"), (20.0, 25.0, "after"))
    assert build_srt(transcript, _c(10.0, 20.0)) == ""


def test_no_overlap_returns_empty_string() -> None:
    assert build_srt(_t((0.0, 1.0, "early")), _c(10.0, 20.0)) == ""


def test_empty_transcript_returns_empty_string() -> None:
    assert build_srt(Transcript(segments=()), _c(0.0, 5.0)) == ""


def test_cues_are_numbered_and_separated_by_a_blank_line() -> None:
    transcript = _t((10.0, 11.0, "one"), (12.0, 13.0, "two"))
    assert build_srt(transcript, _c(10.0, 20.0)) == (
        "1\n00:00:00,000 --> 00:00:01,000\none\n"
        "\n"
        "2\n00:00:02,000 --> 00:00:03,000\ntwo\n"
    )


def test_numbering_skips_dropped_segments() -> None:
    transcript = _t((10.0, 11.0, "   "), (12.0, 13.0, "kept"))
    assert build_srt(transcript, _c(10.0, 20.0)).startswith("1\n")


def test_times_round_to_milliseconds_without_float_noise() -> None:
    # 32.43 - 32.43 is 0 and 33.1 - 32.43 is not exactly 0.67 in floats.
    srt = build_srt(_t((32.43, 33.1, "x")), _c(32.43, 40.0))
    assert srt == "1\n00:00:00,000 --> 00:00:00,670\nx\n"


def test_cue_with_no_length_after_rounding_is_dropped() -> None:
    # Overlaps the clip by 0.0004 s, which rounds to zero milliseconds.
    assert build_srt(_t((9.0, 10.0004, "blip")), _c(10.0, 20.0)) == ""


def test_whitespace_and_newlines_collapse_to_single_spaces() -> None:
    srt = build_srt(_t((0.0, 1.0, "  one\n\ntwo \t three  ")), _c(0.0, 5.0))
    assert srt == "1\n00:00:00,000 --> 00:00:01,000\none two three\n"


def test_empty_text_produces_no_cue() -> None:
    assert build_srt(_t((0.0, 1.0, " \n ")), _c(0.0, 5.0)) == ""


def test_timestamp_past_one_hour() -> None:
    srt = build_srt(_t((3700.0, 3701.5, "late")), _c(100.0, 4000.0))
    assert srt == "1\n01:00:00,000 --> 01:00:01,500\nlate\n"


def test_timestamp_hours_widen_past_99() -> None:
    srt = build_srt(_t((360000.0, 360001.0, "x")), _c(0.0, 400000.0))
    assert srt.splitlines()[1] == "100:00:00,000 --> 100:00:01,000"


def test_markup_characters_are_removed() -> None:
    srt = build_srt(_t((0.0, 1.0, "a {\\an8}b <i>c</i> d --> e")), _c(0.0, 5.0))
    assert srt == "1\n00:00:00,000 --> 00:00:01,000\na an8b ic/i d -- e\n"


def test_cue_that_is_only_markup_characters_is_dropped() -> None:
    assert build_srt(_t((0.0, 1.0, "<>{}")), _c(0.0, 5.0)) == ""


def test_backslash_is_removed_so_libass_cannot_read_a_line_break() -> None:
    srt = build_srt(_t((0.0, 1.0, "C:\\new\\table \\N \\h x")), _c(0.0, 5.0))
    assert srt == "1\n00:00:00,000 --> 00:00:01,000\nC:newtable N h x\n"


def test_cue_that_is_only_backslashes_is_dropped() -> None:
    assert build_srt(_t((0.0, 1.0, "\\ \\\\")), _c(0.0, 5.0)) == ""
