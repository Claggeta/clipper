from pathlib import Path

import pytest

from clipper.core.naming import clip_file_name, plan_clip_paths
from clipper.domain.errors import ClipperError, ClipRenderError
from clipper.domain.models import ClipCandidate


def _c(start: float, end: float) -> ClipCandidate:
    # Constructed candidate; the reason text is arbitrary.
    return ClipCandidate(
        start_seconds=start, end_seconds=end, score=0.5, reasons=("test",)
    )


def test_name_floors_start_and_ceils_end() -> None:
    # Constructed from the test_input excerpt (32.43-73.78), worked out by hand.
    name = clip_file_name(Path("test_input.mp4"), _c(32.43, 73.78))
    assert name == "test_input_000032-000114.mp4"


def test_name_keeps_whole_seconds_exact() -> None:
    assert clip_file_name(Path("talk.mp4"), _c(32.0, 73.0)) == "talk_000032-000113.mp4"


def test_name_ignores_float_noise() -> None:
    name = clip_file_name(Path("t.mp4"), _c(2.9999999999, 73.0000000001))
    assert name == "t_000003-000113.mp4"


def test_name_formats_hours_minutes_seconds() -> None:
    name = clip_file_name(Path("t.mp4"), _c(3661.0, 7322.0))
    assert name == "t_010101-020202.mp4"


def test_name_widens_past_99_hours() -> None:
    name = clip_file_name(Path("t.mp4"), _c(360000.0, 360010.0))
    assert name == "t_1000000-1000010.mp4"


def test_name_uses_stem_only() -> None:
    name = clip_file_name(Path("a/b/My Talk.mkv"), _c(0.0, 1.0))
    assert name.startswith("My Talk_")


def test_plan_returns_paths_in_order() -> None:
    paths = plan_clip_paths(
        Path("talk.mp4"), [_c(10, 20), _c(0, 5)], Path("data/clips")
    )
    assert paths == [
        Path("data/clips/talk_000010-000020.mp4"),
        Path("data/clips/talk_000000-000005.mp4"),
    ]


def test_plan_of_no_candidates_is_empty() -> None:
    assert plan_clip_paths(Path("talk.mp4"), [], Path("clips")) == []


def test_plan_rejects_duplicate_names() -> None:
    with pytest.raises(ClipRenderError, match=r"talk_000001-000010\.mp4"):
        plan_clip_paths(Path("talk.mp4"), [_c(1.2, 9.5), _c(1.8, 9.1)], Path("clips"))


def test_clip_render_error_is_a_clipper_error() -> None:
    assert issubclass(ClipRenderError, ClipperError)
