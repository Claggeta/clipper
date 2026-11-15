from collections.abc import Callable, Sequence
from typing import NamedTuple

from clipper.core.framing import HandWindow
from clipper.domain.errors import ClipRenderError
from clipper.domain.models import CropPath, HandCrop, SequenceBlock, SoundEffect, Zoom

# ADR 0034. A clip's blocks play in turn; a range of the source may repeat.
Range = tuple[float, float]


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


class Placed(NamedTuple):
    """A block and where it starts in the finished clip, in seconds."""

    block: SequenceBlock
    at: float


class BlockMap:
    """Maps between source time and a sequence's clip time (ADR 0034).

    A clip time is in exactly one block, so it has one source time; a
    source time is in every block that repeats it, so it has a list of
    clip times.
    """

    def __init__(self, blocks: Sequence[SequenceBlock]) -> None:
        if not blocks:
            raise ClipRenderError("A sequence needs at least one block")
        placed: list[Placed] = []
        at = 0.0
        for block in blocks:
            placed.append(Placed(block, at))
            at += block.duration_seconds
        self.placed = tuple(placed)
        self.duration = at

    @property
    def ranges(self) -> tuple[Range, ...]:
        """Each block's source range, in play order."""
        return tuple((p.block.start_seconds, p.block.end_seconds) for p in self.placed)

    def clip_times(self, seconds: float) -> list[float]:
        """Every time in the clip that shows this source second."""
        return [
            p.at + seconds - p.block.start_seconds
            for p in self.placed
            if p.block.start_seconds <= seconds < p.block.end_seconds
        ]

    def source_time(self, seconds: float) -> float:
        """The source second shown at this time in the clip."""
        for p in self.placed:
            if seconds < p.at + p.block.duration_seconds:
                return p.block.start_seconds + max(seconds - p.at, 0.0)
        last = self.placed[-1].block
        return last.end_seconds

    def pieces(
        self, start: float, end: float, skip: Callable[[int], bool]
    ) -> list[Range]:
        """A source range at every block it falls in, in clip seconds, in play
        order, leaving out blocks for which `skip(index)` is true.

        Where the range runs across the join of two blocks in source order
        (a cut, not a reorder), the two pieces are one, as a range across a
        cut was before sequences (ADR 0029).
        """
        found: list[tuple[int, Range]] = []
        for index, (block, at) in enumerate(self.placed):
            if skip(index):
                continue
            a = max(start, block.start_seconds)
            b = min(end, block.end_seconds)
            if b <= a:
                continue
            piece = (at + a - block.start_seconds, at + b - block.start_seconds)
            if found and self._continues(found[-1], index, piece):
                previous = found[-1][1]
                found[-1] = (index, (previous[0], piece[1]))
            else:
                found.append((index, piece))
        return [piece for _, piece in found]

    def _continues(self, last: tuple[int, Range], index: int, piece: Range) -> bool:
        before, (_, last_end) = last
        if before != index - 1:
            return False
        a, b = self.placed[before].block, self.placed[index].block
        return (
            _ms(a.end_seconds) <= _ms(b.start_seconds)
            and _ms(last_end) == _ms(self.placed[index].at)
            and _ms(piece[0]) == _ms(self.placed[index].at)
        )

    def zooms(self, zooms: Sequence[Zoom]) -> tuple[Zoom, ...]:
        """The clip's zooms (source seconds, every occurrence) and the
        blocks' own, in clip seconds and in order. A block with its own
        zooms takes only those."""

        def own(index: int) -> bool:
            return bool(self.placed[index].block.zooms)

        placed = [
            zoom.model_copy(update={"start_seconds": a, "end_seconds": b})
            for zoom in zooms
            for a, b in self.pieces(zoom.start_seconds, zoom.end_seconds, own)
        ]
        for block, at in self.placed:
            placed += [
                zoom.model_copy(
                    update={
                        "start_seconds": at + zoom.start_seconds,
                        "end_seconds": at + zoom.end_seconds,
                    }
                )
                for zoom in block.zooms
            ]
        return tuple(sorted(placed, key=lambda zoom: zoom.start_seconds))

    def hands(self, hands: Sequence[HandCrop]) -> list[HandWindow]:
        """Hand windows as `zooms` places zooms: `(start, end, x)` in clip
        seconds, in order."""

        def own(index: int) -> bool:
            return bool(self.placed[index].block.hand_crops)

        windows: list[HandWindow] = [
            (a, b, hand.x)
            for hand in hands
            for a, b in self.pieces(hand.start_seconds, hand.end_seconds, own)
        ]
        for block, at in self.placed:
            windows += [
                (at + h.start_seconds, at + h.end_seconds, h.x)
                for h in block.hand_crops
            ]
        return sorted(windows)

    def effects(
        self, effects: Sequence[SoundEffect]
    ) -> list[tuple[SoundEffect, float]]:
        """Each sound effect with its time in the clip: the clip's (source
        seconds) at every occurrence, and each block's own, in time order."""
        placed = [
            (effect, t)
            for effect in effects
            for t in self.clip_times(effect.at_seconds)
        ]
        for block, at in self.placed:
            placed += [(effect, at + effect.at_seconds) for effect in block.effects]
        return sorted(placed, key=lambda pair: pair[1])

    def join_crops(self, crops: Sequence[CropPath | None], centre: int) -> CropPath:
        """One face crop path for the clip from each block's own (times from
        the block's start), joined at the blocks' places; a block with no
        face holds the window at `centre`. Raises `ClipRenderError` when no
        block has a face."""
        found = [crop for crop in crops if crop is not None]
        if not found:
            raise ClipRenderError("No face in any block to frame in the face style")
        width, height = found[0].width, found[0].height
        points: list[tuple[float, int]] = []
        for (_, at), crop in zip(self.placed, crops, strict=True):
            block_points = crop.points if crop is not None else ((0.0, centre),)
            for seconds, left in block_points:
                time = round(at + seconds, 3)
                if points and points[-1][0] == time:
                    points[-1] = (time, left)
                elif not points or points[-1][1] != left:
                    points.append((time, left))
        return CropPath(width=width, height=height, points=tuple(points))
