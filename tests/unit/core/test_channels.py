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
