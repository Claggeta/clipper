import pytest

from clipper.core.channels import (
    ABOUT_LIMIT,
    PERSON_LIMIT,
    channel_about,
    parse_channel_abouts,
    parse_people,
)
from clipper.domain.errors import ReviewError, TitleError
from clipper.domain.models import VideoInfo

# Constructed channel lines, not the maintainer's file.


def _video(channel: str) -> VideoInfo:
    return VideoInfo(platform="twitch", video_id="v1", title="Stream", channel=channel)


def test_parses_channel_lines_ignoring_comments_and_blanks() -> None:
    text = "# names\n\nstableronaldo | Ron (he/him)\n  Lacy|Lacy (he/him)  \n"
    assert parse_channel_abouts(text) == {
        "stableronaldo": "Ron (he/him)",
        "lacy": "Lacy (he/him)",
    }


def test_the_about_text_may_hold_the_separator() -> None:
    assert parse_channel_abouts("core | Ron | Jason") == {"core": "Ron | Jason"}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("lacy", "Line 1: write it as <channel> | <about text>"),
        ("# c\nlacy |  ", "Line 2: write it as"),
        ("| Lacy", "Line 1: write it as"),
        ("lacy | a\nLACY | b", "Line 2: 'LACY' is given twice"),
        (
            f"lacy | {'x' * (ABOUT_LIMIT + 1)}",
            f"Line 1: the about text is {ABOUT_LIMIT + 1} characters",
        ),
    ],
)
def test_bad_lines_raise_title_errors(text: str, message: str) -> None:
    with pytest.raises(TitleError, match=message):
        parse_channel_abouts(text)


def test_finds_the_channel_ignoring_case_and_spaces() -> None:
    abouts = {"lacy": "Lacy (he/him)"}
    assert channel_about(abouts, _video("Lacy ")) == "Lacy (he/him)"
    assert channel_about(abouts, _video("other")) is None
    assert channel_about(abouts, None) is None


def test_parses_people_in_order() -> None:
    assert parse_people("# CORE\nRon\n\n Jason \nLacy\n") == ("Ron", "Jason", "Lacy")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Ron\nron", "Line 2: 'ron' is given twice"),
        (f"{'x' * (PERSON_LIMIT + 1)}", f"Line 1: a name is at most {PERSON_LIMIT}"),
    ],
)
def test_bad_people_lines_raise_review_errors(text: str, message: str) -> None:
    with pytest.raises(ReviewError, match=message):
        parse_people(text)


# Saved cam boxes per channel (ADR 0025, 12b amendment).


def test_parses_cam_lines_ignoring_comments_and_blanks() -> None:
    from clipper.core.channels import parse_channel_cams
    from clipper.domain.models import FacecamBox

    text = "# cams\n\nLacy | 0 0 0.255 0.255\n  sypher|0.7 0.6 0.3 0.4 \n"
    assert parse_channel_cams(text) == {
        "lacy": FacecamBox(x=0.0, y=0.0, w=0.255, h=0.255),
        "sypher": FacecamBox(x=0.7, y=0.6, w=0.3, h=0.4),
    }


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Lacy | 0 0 0.25\n", "Line 1: write it as"),
        ("Lacy | a b c d\n", "Line 1: write it as"),
        ("Lacy | 0.9 0 0.2 0.2\n", "Line 1: write it as"),
        ("| 0 0 0.2 0.2\n", "Line 1: write it as"),
        ("Lacy | 0 0 0.2 0.2\nlacy | 0 0 0.3 0.3\n", "Line 2: 'lacy' is given twice"),
    ],
)
def test_bad_cam_lines_are_refused(text: str, message: str) -> None:
    from clipper.core.channels import parse_channel_cams
    from clipper.domain.errors import FaceError

    with pytest.raises(FaceError, match=message):
        parse_channel_cams(text)


def test_a_channels_cam_matches_ignoring_case_and_spaces() -> None:
    from clipper.core.channels import channel_cam
    from clipper.domain.models import FacecamBox

    box = FacecamBox(x=0.0, y=0.0, w=0.25, h=0.25)
    assert channel_cam({"lacy": box}, "Lacy ") == box
    assert channel_cam({"lacy": box}, "Ron") is None
    assert channel_cam({"lacy": box}, None) is None


def test_setting_a_cam_line_replaces_adds_or_removes_it() -> None:
    from clipper.core.channels import parse_channel_cams, set_channel_cam
    from clipper.domain.models import FacecamBox

    text = "# cams\nLacy | 0 0 0.2 0.2\nsypher | 0.7 0.6 0.3 0.4\n"
    box = FacecamBox(x=0.1, y=0.0, w=0.25, h=0.25)
    replaced = set_channel_cam(text, "lacy", box)
    assert replaced == (
        "# cams\nsypher | 0.7 0.6 0.3 0.4\nlacy | 0.1000 0.0000 0.2500 0.2500\n"
    )
    assert (
        set_channel_cam(replaced, "Lacy", None) == "# cams\nsypher | 0.7 0.6 0.3 0.4\n"
    )
    assert set_channel_cam("", "Ron", box) == "Ron | 0.1000 0.0000 0.2500 0.2500\n"
    # Rounded inside the frame, so the line reads back.
    edge = FacecamBox(x=0.33333, y=0.0, w=0.66667, h=1.0)
    assert parse_channel_cams(set_channel_cam("", "Ron", edge))["ron"].x == 0.3333
