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
    clip times. A B-roll block (B-roll amendment) shows no source time, so
    no edit in source seconds lands on it.
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

    def _shows_source(self, index: int) -> bool:
        return self.placed[index].block.broll is None

    @property
    def captioned(self) -> tuple[Range | float, ...]:
        """Each block for the captions, in play order: its source range, or
        the length of a B-roll block, which has no speech."""
        return tuple(
            p.block.duration_seconds
            if p.block.broll is not None
            else (p.block.start_seconds, p.block.end_seconds)
            for p in self.placed
        )

    def clip_times(self, seconds: float) -> list[float]:
        """Every time in the clip that shows this source second."""
        return [
            p.at + seconds - p.block.start_seconds
            for i, p in enumerate(self.placed)
            if self._shows_source(i)
            and p.block.start_seconds <= seconds < p.block.end_seconds
        ]

    def source_time(self, seconds: float) -> float | None:
        """The source second shown at this time in the clip; None in a
        B-roll block."""
        for i, p in enumerate(self.placed):
            if seconds < p.at + p.block.duration_seconds:
                if not self._shows_source(i):
                    return None
                return p.block.start_seconds + max(seconds - p.at, 0.0)
        last = self.placed[-1].block
        return None if last.broll is not None else last.end_seconds

    def pieces(
        self, start: float, end: float, skip: Callable[[int], bool]
    ) -> list[Range]:
        """A source range at every block it falls in, in clip seconds, in play
        order, leaving out B-roll blocks and blocks for which `skip(index)`
        is true.

        Where the range runs across the join of two blocks in source order
        (a cut, not a reorder), the two pieces are one, as a range across a
        cut was before sequences (ADR 0029).
        """
        found: list[tuple[int, Range]] = []
        for index, (block, at) in enumerate(self.placed):
            if skip(index) or not self._shows_source(index):
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

    def block_hands(self, hands: Sequence[HandCrop]) -> list[list[HandWindow]]:
        """Each block's hand windows, `(start, end, x)` in seconds from the
        block's start, in order, for framing the block on its own: the
        clip's (source seconds) where they fall, or the block's own, which
        replace them there. None on a B-roll block."""
        windows: list[list[HandWindow]] = []
        for index, (block, _) in enumerate(self.placed):
            if not self._shows_source(index):
                windows.append([])
            elif block.hand_crops:
                windows.append(
                    [(h.start_seconds, h.end_seconds, h.x) for h in block.hand_crops]
                )
            else:
                windows.append(
                    sorted(
                        (a - block.start_seconds, b - block.start_seconds, hand.x)
                        for hand in hands
                        if (a := max(hand.start_seconds, block.start_seconds))
                        < (b := min(hand.end_seconds, block.end_seconds))
                    )
                )
        return windows

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

    def check_faces(self, crops: Sequence[CropPath | None]) -> None:
        """Raise `ClipRenderError` when no source block has a face to frame
        in the face style; a block without one is held in the middle."""
        if not any(
            crop is not None for i, crop in enumerate(crops) if self._shows_source(i)
        ):
            raise ClipRenderError("No face in any block to frame in the face style")
