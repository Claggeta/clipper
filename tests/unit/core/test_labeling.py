import pytest

from clipper.core.labeling import (
    clip_labels,
    clip_url,
    parse_clip_url,
    parse_links,
    parse_twitch_offset,
)
from clipper.domain.errors import LabelsError
from clipper.domain.models import ClipInfo, ClipLink, VideoSource

# Constructed clip IDs, VOD numbers and titles; nothing here calls Twitch.
_VOD = VideoSource(platform="twitch", video_id="v1234567890")
_VOD_URL = "https://www.twitch.tv/videos/1234567890"


@pytest.mark.parametrize(
    ("url", "clip_id"),
    [
        (
            "https://www.twitch.tv/somechannel/clip/FunnyClipName-AbC_12",
            "FunnyClipName-AbC_12",
        ),
        ("https://www.twitch.tv/somechannel/clip/FunnyClip-x?range=7d", "FunnyClip-x"),
        ("https://twitch.tv/somechannel/clip/FunnyClip-x/", "FunnyClip-x"),
        ("https://m.twitch.tv/somechannel/clip/FunnyClip-x", "FunnyClip-x"),
        ("https://clips.twitch.tv/FunnyClip-x", "FunnyClip-x"),
    ],
)
def test_parse_clip_url_returns_the_slug(url: str, clip_id: str) -> None:
    assert parse_clip_url(url) == clip_id


@pytest.mark.parametrize(
    "url",
    [
        _VOD_URL,
        "https://www.twitch.tv/somechannel",
        "https://www.twitch.tv/somechannel/clips",
        "https://www.twitch.tv/somechannel/clip/",
        "https://clips.twitch.tv/",
        "https://www.youtube.com/watch?v=abcDEF123_-",
        "clips.twitch.tv/FunnyClip-x",
        "https://clips.twitch.tv/bad%20id",
    ],
)
def test_parse_clip_url_refuses_other_urls(url: str) -> None:
    with pytest.raises(LabelsError, match="Not a Twitch clip URL"):
        parse_clip_url(url)


def test_clip_url_is_the_plain_clips_url() -> None:
    assert clip_url("FunnyClip-x") == "https://clips.twitch.tv/FunnyClip-x"


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("4h36m45s", 16605),
        ("2h1m48s", 7308),
        ("5h6m0s", 18360),
        ("36m5s", 2165),
        ("45s", 45),
        ("1h", 3600),
        ("0h0m1s", 1),
    ],
)
def test_parse_twitch_offset(value: str, seconds: int) -> None:
    assert parse_twitch_offset(value) == seconds


@pytest.mark.parametrize("value", ["", "123", "1:02:03", "5s4m", "1h 2m", "-5s"])
def test_parse_twitch_offset_refuses_other_forms(value: str) -> None:
    with pytest.raises(LabelsError, match="is not a time like 4h36m45s"):
        parse_twitch_offset(value)


def test_parse_links_reads_lines_skipping_comments_and_blanks() -> None:
    text = (
        "# clips from one stream\n"
        "\n"
        f"https://www.twitch.tv/ch/clip/First-a?range=7d {_VOD_URL}?t=1h2m3s\n"
        f"  https://clips.twitch.tv/Second-b   {_VOD_URL}?t=45s  # a note\n"
    )
    assert parse_links(text, "links.txt") == [
        ClipLink(line=3, clip_id="First-a", vod=_VOD, end_seconds=3723),
        ClipLink(line=4, clip_id="Second-b", vod=_VOD, end_seconds=45),
    ]


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("https://clips.twitch.tv/A-a", "expected a clip URL and a VOD URL, got 1"),
        (
            f"https://clips.twitch.tv/A-a {_VOD_URL}?t=5s extra",
            "expected a clip URL and a VOD URL, got 3",
        ),
        (f"https://clips.twitch.tv/A-a {_VOD_URL}", "has no t="),
        (f"https://clips.twitch.tv/A-a {_VOD_URL}?t=0s", "at the very start"),
        (f"https://clips.twitch.tv/A-a {_VOD_URL}?t=90", "not a time like"),
        (f"{_VOD_URL}?t=5s https://clips.twitch.tv/A-a", "Only Twitch VOD URLs"),
        (
            "https://clips.twitch.tv/A-a https://www.youtube.com/watch?v=abcDEF123_-",
            "Not a Twitch VOD URL",
        ),
        (f"https://www.twitch.tv/ch {_VOD_URL}?t=5s", "Not a Twitch clip URL"),
    ],
)
def test_parse_links_names_the_file_and_line(line: str, message: str) -> None:
    text = f"# header\n{line}\n"
    with pytest.raises(LabelsError, match=f"^links.txt line 2: .*{message}"):
        parse_links(text, "links.txt")


def test_parse_links_refuses_a_second_vod() -> None:
    text = (
        f"https://clips.twitch.tv/A-a {_VOD_URL}?t=5s\n"
        "https://clips.twitch.tv/B-b https://www.twitch.tv/videos/999?t=5s\n"
    )
    with pytest.raises(
        LabelsError,
        match="line 2: VOD v999 differs from v1234567890 on line 1",
    ):
        parse_links(text, "links.txt")


def test_parse_links_refuses_a_repeated_clip_in_any_url_form() -> None:
    text = (
        f"https://www.twitch.tv/ch/clip/A-a?range=7d {_VOD_URL}?t=5s\n"
        f"https://clips.twitch.tv/A-a {_VOD_URL}?t=9s\n"
    )
    with pytest.raises(LabelsError, match="line 2: clip A-a is already on line 1"):
        parse_links(text, "links.txt")


@pytest.mark.parametrize("text", ["", "\n# only a comment\n   \n"])
def test_parse_links_needs_a_clip(text: str) -> None:
    with pytest.raises(LabelsError, match="links.txt lists no clips"):
        parse_links(text, "links.txt")


def _link(clip_id: str, end: float) -> ClipLink:
    return ClipLink(line=1, clip_id=clip_id, vod=_VOD, end_seconds=end)


def _spans(
    links: list[ClipLink], infos: list[ClipInfo]
) -> list[tuple[float, float, str | None]]:
    labeled = clip_labels("v1234567890.mp4", links, infos)
    assert labeled.source == "v1234567890.mp4"
    return [(lb.start_seconds, lb.end_seconds, lb.note) for lb in labeled.labels]


def test_a_clip_spans_t_minus_its_length_to_t() -> None:
    assert _spans(
        [_link("a", 16605)], [ClipInfo(title="One", duration_seconds=60)]
    ) == [(16545.0, 16605.0, "Twitch clips: One")]


def test_labels_are_in_time_order_and_overlaps_merge_transitively() -> None:
    links = [_link("late", 500), _link("b", 130), _link("a", 100), _link("c", 150)]
    infos = [
        ClipInfo(title="Late", duration_seconds=30),
        ClipInfo(title="B", duration_seconds=40),  # 90-130 overlaps A
        ClipInfo(title="A", duration_seconds=30),  # 70-100
        ClipInfo(title="C", duration_seconds=25),  # 125-150 overlaps B only
    ]
    assert _spans(links, infos) == [
        (70.0, 150.0, "Twitch clips: A; B; C"),
        (470.0, 500.0, "Twitch clips: Late"),
    ]


def test_a_clip_inside_another_keeps_the_outer_end() -> None:
    links = [_link("outer", 100), _link("inner", 90)]
    infos = [
        ClipInfo(title="Outer", duration_seconds=60),
        ClipInfo(title="Inner", duration_seconds=10),
    ]
    assert _spans(links, infos) == [(40.0, 100.0, "Twitch clips: Outer; Inner")]


def test_touching_clips_stay_separate() -> None:
    links = [_link("a", 100), _link("b", 130)]
    infos = [
        ClipInfo(title="A", duration_seconds=30),
        ClipInfo(title="B", duration_seconds=30),
    ]
    assert _spans(links, infos) == [
        (70.0, 100.0, "Twitch clips: A"),
        (100.0, 130.0, "Twitch clips: B"),
    ]


def test_identical_spans_merge_without_comparing_infos() -> None:
    links = [_link("a", 100), _link("b", 100)]
    infos = [
        ClipInfo(title="A", duration_seconds=30),
        ClipInfo(title="B", duration_seconds=30),
    ]
    assert _spans(links, infos) == [(70.0, 100.0, "Twitch clips: A; B")]


def test_a_start_before_the_vod_is_clamped_to_zero() -> None:
    assert _spans([_link("a", 20)], [ClipInfo(title="Early", duration_seconds=30)]) == [
        (0.0, 20.0, "Twitch clips: Early")
    ]


def test_fractional_lengths_keep_milliseconds() -> None:
    assert _spans([_link("a", 100)], [ClipInfo(title="A", duration_seconds=29.5)]) == [
        (70.5, 100.0, "Twitch clips: A")
    ]


def test_links_and_infos_must_pair_up() -> None:
    with pytest.raises(ValueError, match="same length"):
        clip_labels("v1.mp4", [_link("a", 100)], [])
