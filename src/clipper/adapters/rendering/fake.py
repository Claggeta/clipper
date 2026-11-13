from collections.abc import Iterable
from pathlib import Path

from clipper.domain.errors import ClipRenderError
from clipper.domain.models import (
    AudioMix,
    ClipCandidate,
    CropPath,
    HandCropPlan,
    RenderedClip,
    Zoom,
)


class FakeClipRenderer:
    """A ClipRenderer that writes a placeholder file, for tests and offline runs.

    The file is not a video. Calls are recorded in `calls`, the captions of
    each call in `captions`, its `vertical` flag in `vertical`, its crop
    path in `crops`, its cuts in `cuts`, its zooms in `zooms`, its hand
    crops in `hands` and its audio mix in `audio`. Candidates
    in `fail_on` raise ClipRenderError and write nothing.
    """

    def __init__(self, fail_on: Iterable[ClipCandidate] = ()) -> None:
        self._fail_on = frozenset(fail_on)
        self.calls: list[tuple[Path, ClipCandidate, Path]] = []
        self.captions: list[str | None] = []
        self.vertical: list[bool] = []
        self.crops: list[CropPath | None] = []
        self.cuts: list[tuple[tuple[float, float], ...]] = []
        self.zooms: list[tuple[Zoom, ...]] = []
        self.hands: list[HandCropPlan | None] = []
        self.audio: list[AudioMix | None] = []
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
        zooms: tuple[Zoom, ...] = (),
        hand: HandCropPlan | None = None,
        audio: AudioMix | None = None,
    ) -> RenderedClip:
        self.calls.append((video_path, candidate, output_path))
        self.audio.append(audio)
        self.zooms.append(zooms)
        self.hands.append(hand)
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
