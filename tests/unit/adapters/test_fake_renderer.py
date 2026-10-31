from pathlib import Path

import pytest

from clipper.adapters.rendering.fake import FakeClipRenderer
from clipper.core.interfaces import ClipRenderer
from clipper.domain.errors import ClipRenderError
from clipper.domain.models import ClipCandidate


def _c(start: float = 1.0, end: float = 5.0) -> ClipCandidate:
    return ClipCandidate(
        start_seconds=start, end_seconds=end, score=0.5, reasons=("test",)
    )


def test_fake_renderer_satisfies_protocol() -> None:
    renderer: ClipRenderer = FakeClipRenderer()
    assert isinstance(renderer, ClipRenderer)


def test_fake_renderer_writes_placeholder_and_returns_clip(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "a.mp4"
    clip = FakeClipRenderer().render(Path("v.mp4"), _c(), out)
    assert clip.path == out
    assert clip.candidate == _c()
    assert out.read_bytes() == b"fake clip"


def test_fake_renderer_overwrites_existing_file(tmp_path: Path) -> None:
    out = tmp_path / "a.mp4"
    out.write_bytes(b"old")
    FakeClipRenderer().render(Path("v.mp4"), _c(), out)
    assert out.read_bytes() == b"fake clip"


def test_fake_renderer_records_calls(tmp_path: Path) -> None:
    renderer = FakeClipRenderer()
    out = tmp_path / "a.mp4"
    renderer.render(Path("v.mp4"), _c(), out)
    assert renderer.calls == [(Path("v.mp4"), _c(), out)]


def test_fake_renderer_fails_on_chosen_candidates(tmp_path: Path) -> None:
    bad = _c(10.0, 20.0)
    renderer = FakeClipRenderer(fail_on=[bad])
    out = tmp_path / "bad.mp4"
    with pytest.raises(ClipRenderError):
        renderer.render(Path("v.mp4"), bad, out)
    assert not out.exists()
    assert renderer.calls == [(Path("v.mp4"), bad, out)]
    assert renderer.render(Path("v.mp4"), _c(), tmp_path / "ok.mp4")
