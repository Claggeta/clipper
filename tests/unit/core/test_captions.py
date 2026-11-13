from clipper.core.captions import (
    HIGHLIGHTS,
    Captions,
    CaptionWord,
    build_ass,
    caption_chunks,
    clip_text,
    preview_layout,
)
from clipper.domain.models import (
    CaptionEdits,
    ClipCandidate,
    OnScreenText,
    Segment,
    Transcript,
    Word,
)

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
    assert build_ass(_t(seg), _c(10.0, 20.0), vertical=True) == Captions(
        "", from_segments=False
    )


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
    assert build_ass(
        _t(_seg(0.0, 1.0, "early")), _c(10.0, 20.0), vertical=True
    ) == Captions("", from_segments=False)
    assert build_ass(Transcript(segments=()), _c(0.0, 5.0), vertical=False) == Captions(
        "", from_segments=False
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


def test_cuts_drop_words_inside_them_and_move_the_rest_earlier() -> None:
    seg = _wseg((10.0, 10.4, "one."), (12.0, 12.4, "gone."), (15.0, 15.5, "two."))
    captions = build_ass(_t(seg), _c(10.0, 20.0), vertical=True, cuts=((11.0, 14.0),))
    assert _events(captions) == [
        f"0:00:00.00,0:00:00.40,{_hl(['one.'], 0)}",
        f"0:00:02.00,0:00:02.50,{_hl(['two.'], 0)}",
    ]


def test_a_word_running_into_a_cut_ends_where_the_cut_starts() -> None:
    seg = _wseg((10.0, 11.5, "long."), (15.0, 15.5, "next."))
    captions = build_ass(_t(seg), _c(10.0, 20.0), vertical=True, cuts=((11.0, 14.0),))
    assert _events(captions)[0] == f"0:00:00.00,0:00:01.00,{_hl(['long.'], 0)}"


def test_a_segment_cue_across_a_cut_closes_up_with_it() -> None:
    captions = build_ass(
        _t(_seg(10.0, 18.0, "hello")),
        _c(10.0, 20.0),
        vertical=True,
        cuts=((12.0, 15.0),),
    )
    assert _events(captions) == ["0:00:00.00,0:00:05.00,hello"]


def _text(text: str, start: float, end: float, place: str = "top") -> OnScreenText:
    return OnScreenText.model_validate(
        {"text": text, "start_seconds": start, "end_seconds": end, "place": place}
    )


def test_texts_are_pinned_events_in_their_own_style() -> None:
    seg = _wseg((10.0, 10.4, "one."))
    captions = build_ass(
        _t(seg),
        _c(10.0, 20.0),
        vertical=True,
        texts=(_text("Ron at the pump", 0.0, 3.0), _text("Wait", 2.0, 4.0, "middle")),
    )
    lines = captions.text.splitlines()
    assert sum(line.startswith("Style: Text,Arial,84,") for line in lines) == 1
    assert (
        "Dialogue: 1,0:00:00.00,0:00:03.00,Text,,0,0,0,,{\\an8\\pos(540,300)}"
        "Ron at the pump" in lines
    )
    assert (
        "Dialogue: 1,0:00:02.00,0:00:04.00,Text,,0,0,0,,{\\an5\\pos(540,960)}Wait"
        in lines
    )
    assert captions.speech is True


def test_texts_alone_make_captions_without_speech() -> None:
    captions = build_ass(
        Transcript(segments=()),
        _c(10.0, 20.0),
        vertical=False,
        texts=(_text("{Hook}", 1.0, 2.5),),
    )
    assert captions.speech is False
    assert "Style: Text,Arial,64," in captions.text
    assert captions.text.endswith("{\\an8\\pos(960,80)}Hook\n")


def test_texts_are_clamped_to_the_cut_clip_and_late_ones_dropped() -> None:
    captions = build_ass(
        Transcript(segments=()),
        _c(10.0, 20.0),
        vertical=True,
        cuts=((12.0, 16.0),),
        texts=(_text("long", 4.0, 9.0), _text("late", 6.5, 8.0)),
    )
    # The clip is 6 s after its cut: "long" ends at 6 s, "late" starts past it.
    assert _events(captions) == ["0:00:04.00,0:00:06.00,{\\an8\\pos(540,300)}long"]


def test_texts_share_one_box_so_wrapped_lines_do_not_overlap() -> None:
    captions = build_ass(
        Transcript(segments=()),
        _c(10.0, 20.0),
        vertical=True,
        texts=(_text("Hook", 0.0, 3.0),),
    )
    style = next(
        line for line in captions.text.splitlines() if line.startswith("Style: Text,")
    )
    fields = style.removeprefix("Style: ").split(",")
    # Outline colour clear, back colour the half-dark box, BorderStyle 4
    # (one box per event), the outline as its padding.
    assert fields[5:7] == ["&HFF000000", "&H80000000"]
    assert fields[15:17] == ["4", "18"]


def test_without_texts_the_header_has_no_text_style() -> None:
    seg = _wseg((10.0, 10.4, "one."))
    assert "Style: Text" not in build_ass(_t(seg), _c(10.0, 20.0), vertical=True).text


def test_caption_chunks_are_the_chunks_build_ass_shows_in_source_time() -> None:
    seg = _wseg(
        (9.0, 9.5, "early"),
        (10.0, 10.4, "one"),
        (10.5, 10.9, "two."),
        (11.0, 11.3, "{three}"),
        (11.4, 11.8, "four"),
        (12.0, 12.3, "cut"),
        (12.6, 12.9, "five"),
        (13.0, 13.4, "six"),
        (19.8, 20.6, "last"),
    )
    candidate = _c(10.0, 20.0)
    cuts = ((11.95, 12.45),)
    chunks = caption_chunks(_t(seg), candidate, cuts)
    assert chunks == [
        [CaptionWord(10.0, 10.4, "one"), CaptionWord(10.5, 10.9, "two.")],
        [
            CaptionWord(11.0, 11.3, "three"),
            CaptionWord(11.4, 11.8, "four"),
            CaptionWord(12.6, 12.9, "five"),
        ],
        [CaptionWord(13.0, 13.4, "six"), CaptionWord(19.8, 20.0, "last")],
    ]
    # Each chunk is one run of events in build_ass, in the same words.
    events = _events(build_ass(_t(seg), candidate, vertical=True, cuts=cuts))
    shown = [
        e.split(",", 2)[2].replace("{\\c&H0000FFFF&}", "").replace("{\\r}", "")
        for e in events
    ]
    assert shown == [" ".join(w.text for w in chunk) for chunk in chunks for _ in chunk]


def test_caption_chunks_skip_segments_without_word_times() -> None:
    plain = Segment(start_seconds=10.0, end_seconds=12.0, text="No word times.")
    assert caption_chunks(_t(plain), _c(10.0, 20.0)) == []


def test_preview_layout_names_the_burn_in_sizes() -> None:
    vertical = preview_layout(True)
    assert (vertical["width"], vertical["height"]) == (1080, 1920)
    assert (vertical["caption_size"], vertical["caption_margin_v"]) == (80, 420)
    assert (vertical["text_size"], vertical["text_top"]) == (84, [540, 300])
    assert preview_layout(False)["height"] == 1080


# --- Caption edits (ADR 0017, caption editing amendment) ---


def _edits(**fields: object) -> CaptionEdits:
    return CaptionEdits.model_validate(fields)


def test_without_edits_or_with_empty_ones_the_captions_are_unchanged() -> None:
    seg = _wseg((10.0, 10.4, "one"), (10.5, 10.9, "two."))
    plain = build_ass(_t(seg), _c(10.0, 20.0), vertical=True)
    assert build_ass(_t(seg), _c(10.0, 20.0), vertical=True, edits=_edits()) == plain


def test_a_fixed_word_keeps_its_time_and_a_cleared_one_is_hidden() -> None:
    seg = _wseg((10.0, 10.4, "gonna"), (10.5, 10.9, "uh"), (11.0, 11.4, "win."))
    edits = _edits(
        fixes=[
            {"start_seconds": 10.0, "text": "going to"},
            {"start_seconds": 10.5, "text": ""},
        ]
    )
    events = _events(build_ass(_t(seg), _c(10.0, 20.0), vertical=True, edits=edits))
    assert events == [
        "0:00:00.00,0:00:01.00,{\\c&H0000FFFF&}going to{\\r} win.",
        "0:00:01.00,0:00:01.40,going to {\\c&H0000FFFF&}win.{\\r}",
    ]


def test_a_fix_ending_a_sentence_ends_the_chunk_there() -> None:
    seg = _wseg((10.0, 10.4, "one"), (10.5, 10.9, "two"), (11.0, 11.4, "three"))
    edits = _edits(fixes=[{"start_seconds": 10.0, "text": "One."}])
    chunks = caption_chunks(_t(seg), _c(10.0, 20.0), edits=edits)
    assert [[w.text for w in chunk] for chunk in chunks] == [["One."], ["two", "three"]]


def test_key_words_stay_in_the_chosen_colour_through_their_chunk() -> None:
    seg = _wseg((10.0, 10.4, "a"), (10.5, 10.9, "big"), (11.0, 11.4, "win"))
    edits = _edits(keys=[10.5], highlight="green")
    events = _events(build_ass(_t(seg), _c(10.0, 20.0), vertical=True, edits=edits))
    green = "{\\c&H0076E600&}"
    assert events == [
        f"0:00:00.00,0:00:00.50,{green}a{{\\r}} {green}big{{\\r}} win",
        f"0:00:00.50,0:00:01.00,a {green}big{{\\r}} win",
        f"0:00:01.00,0:00:01.40,a {green}big{{\\r}} {green}win{{\\r}}",
    ]
    [chunk] = caption_chunks(_t(seg), _c(10.0, 20.0), edits=edits)
    assert [w.key for w in chunk] == [False, True, False]


def test_fixes_that_match_no_word_change_nothing() -> None:
    seg = _wseg((10.0, 10.4, "one"))
    edits = _edits(fixes=[{"start_seconds": 10.001, "text": "x"}], keys=[15.0])
    assert _events(build_ass(_t(seg), _c(10.0, 20.0), vertical=True, edits=edits)) == [
        "0:00:00.00,0:00:00.40,{\\c&H0000FFFF&}one{\\r}"
    ]


def test_the_preview_layout_lists_the_highlight_colours() -> None:
    assert preview_layout(True)["highlights"] == HIGHLIGHTS
    assert HIGHLIGHTS["yellow"] == "#FFFF00"


# --- Text styling (ADR 0030, styling amendment) ---


def _styled(**look: object) -> OnScreenText:
    return OnScreenText.model_validate(
        {"text": "Hook", "start_seconds": 0.0, "end_seconds": 3.0, **look}
    )


def _text_lines(captions: Captions) -> list[str]:
    return [line for line in captions.text.splitlines() if ",Text" in line]


def test_the_default_look_adds_no_tags_and_no_outline_style() -> None:
    captions = build_ass(
        Transcript(segments=()), _c(10.0, 20.0), vertical=True, texts=(_styled(),)
    )
    assert _text_lines(captions) == [
        "Dialogue: 1,0:00:00.00,0:00:03.00,Text,,0,0,0,,{\\an8\\pos(540,300)}Hook"
    ]
    assert "TextOutline" not in captions.text


def test_a_styled_text_has_its_colour_size_fade_and_position() -> None:
    text = _styled(colour="pink", size="small", fade=True, x=0.25, y=0.75)
    [line] = _text_lines(
        build_ass(Transcript(segments=()), _c(10.0, 20.0), vertical=True, texts=(text,))
    )
    # Centred on a quarter of 1080 across and three quarters of 1920 down;
    # pink #FF4FD8 as BGR; 84 * 0.65 = 55; a 300 ms fade in.
    assert line.endswith(
        ",Text,,0,0,0,,{\\an5\\pos(270,1440)\\c&H00D84FFF&\\fs55\\fad(300,0)}Hook"
    )


def test_an_outlined_text_uses_its_own_style() -> None:
    captions = build_ass(
        Transcript(segments=()),
        _c(10.0, 20.0),
        vertical=False,
        texts=(_styled(look="outline", place="middle"),),
    )
    styles = [
        line for line in captions.text.splitlines() if line.startswith("Style: Text")
    ]
    # The box style and the outline one: black edge, BorderStyle 1, the
    # captions' outline width.
    assert len(styles) == 2
    fields = styles[1].removeprefix("Style: ").split(",")
    assert fields[0] == "TextOutline"
    assert (fields[5], fields[15], fields[16]) == ("&H00000000", "1", "5")
    assert _text_lines(captions)[0].split(",")[3] == "TextOutline"
