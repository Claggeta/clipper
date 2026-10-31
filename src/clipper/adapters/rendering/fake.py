from collections.abc import Iterable
from pathlib import Path

from clipper.domain.errors import ClipRenderError
from clipper.domain.models import ClipCandidate, RenderedClip


class FakeClipRenderer:
    """A ClipRenderer that writes a placeholder file, for tests and offline runs.

    The file is not a video. Calls are recorded in `calls`, and the
    captions of each call in `captions`. Candidates in
    `fail_on` raise ClipRenderError and write nothing.
    """

    def __init__(self, fail_on: Iterable[ClipCandidate] = ()) -> None:
        self._fail_on = frozenset(fail_on)
        self.calls: list[tuple[Path, ClipCandidate, Path]] = []
        self.captions: list[str | None] = []

    def render(
        self,
        video_path: Path,
        candidate: ClipCandidate,
        output_path: Path,
        captions: str | None = None,
    ) -> RenderedClip:
        self.calls.append((video_path, candidate, output_path))
        self.captions.append(captions)
        if candidate in self._fail_on:
            raise ClipRenderError(f"Fake render failed for {output_path.name}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake clip")
        return RenderedClip(candidate=candidate, path=output_path)
