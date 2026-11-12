from collections.abc import Iterable
from pathlib import Path

from clipper.domain.errors import ClipRenderError
from clipper.domain.models import ClipCandidate, CropPath, RenderedClip


class FakeClipRenderer:
    """A ClipRenderer that writes a placeholder file, for tests and offline runs.

    The file is not a video. Calls are recorded in `calls`, the captions of
    each call in `captions`, its `vertical` flag in `vertical`, its crop
    path in `crops` and its cuts in `cuts`. Candidates
    in `fail_on` raise ClipRenderError and write nothing.
    """

    def __init__(self, fail_on: Iterable[ClipCandidate] = ()) -> None:
        self._fail_on = frozenset(fail_on)
        self.calls: list[tuple[Path, ClipCandidate, Path]] = []
        self.captions: list[str | None] = []
        self.vertical: list[bool] = []
        self.crops: list[CropPath | None] = []
        self.cuts: list[tuple[tuple[float, float], ...]] = []
        self.stills: list[tuple[float, Path, str | None, bool, CropPath | None]] = []
        self.fail_stills = False

    def render(
        self,
        video_path: Path,
        candidate: ClipCandidate,
        output_path: Path,
        captions: str | None = None,
        *,
        vertical: bool = False,
        crop: CropPath | None = None,
        cuts: tuple[tuple[float, float], ...] = (),
    ) -> RenderedClip:
        self.calls.append((video_path, candidate, output_path))
        self.cuts.append(cuts)
        self.captions.append(captions)
        self.vertical.append(vertical)
        self.crops.append(crop)
        if candidate in self._fail_on:
            raise ClipRenderError(f"Fake render failed for {output_path.name}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake clip")
        return RenderedClip(candidate=candidate, path=output_path)

    def still(
        self,
        video_path: Path,
        seconds: float,
        output_path: Path,
        captions: str | None = None,
        *,
        vertical: bool = False,
        crop: CropPath | None = None,
    ) -> Path:
        """Writes a placeholder JPEG and records the call in `stills`."""
        self.stills.append((seconds, output_path, captions, vertical, crop))
        if self.fail_stills:
            raise ClipRenderError(f"Fake still failed for {output_path.name}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake jpeg")
        return output_path
