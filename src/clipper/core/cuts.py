from collections.abc import Sequence

from clipper.domain.errors import ClipRenderError
from clipper.domain.models import CropPath, Transcript

# ADR 0029. A pause is a gap of at least MIN_PAUSE_SECONDS between two
# stretches of speech; its cut leaves CUT_PADDING_SECONDS of the gap at each
# side, so no word is clipped. First values, set by the maintainer's choice,
# not tuned.
MIN_PAUSE_SECONDS = 0.6
CUT_PADDING_SECONDS = 0.15

Cut = tuple[float, float]


def find_pauses(transcript: Transcript, start: float, end: float) -> tuple[Cut, ...]:
    """Proposed cuts for the pauses in speech inside `start`..`end`, in source
    seconds, in order.

    Speech is each word, and the whole of a segment without words, since its
    pauses are unknown. Only gaps between two stretches of speech inside the
    span are cut, never before the first word or after the last.
    """
    speech: list[tuple[float, float]] = []
    for segment in transcript.segments:
        if segment.words:
            speech.extend((w.start_seconds, w.end_seconds) for w in segment.words)
        else:
            speech.append((segment.start_seconds, segment.end_seconds))
    inside = sorted((a, b) for a, b in speech if b > start and a < end)
    cuts: list[Cut] = []
    reach: float | None = None
    for a, b in inside:
        if reach is not None and a - reach >= MIN_PAUSE_SECONDS:
            cut = (reach + CUT_PADDING_SECONDS, a - CUT_PADDING_SECONDS)
            if start < cut[0] and cut[1] < end:
                cuts.append((round(cut[0], 3), round(cut[1], 3)))
        reach = b if reach is None else max(reach, b)
    return tuple(cuts)


def check_cuts(cuts: Sequence[Cut], start: float, end: float) -> None:
    """Raise `ClipRenderError` unless the cuts are in order, apart, each with
    a length, and strictly inside `start`..`end` (ADR 0029)."""
    previous = start
    for a, b in cuts:
        if not (previous < a < b < end):
            raise ClipRenderError(
                f"Cut {a:.3f}-{b:.3f}s must lie inside the clip "
                f"{start:.3f}-{end:.3f}s, after the cut before it, with a length"
            )
        previous = b


class TimeMap:
    """Maps a source time inside a clip to its time in the clip with its cuts
    removed (ADR 0029). Without cuts, a time maps to `time - start`."""

    def __init__(self, start: float, end: float, cuts: Sequence[Cut] = ()) -> None:
        self.start = start
        self.end = end
        self.cuts = tuple(cuts)

    def output(self, time: float) -> float | None:
        """The time in the cut clip, or None for a time inside a cut."""
        removed: float = 0
        for a, b in self.cuts:
            if time < a:
                break
            if time < b:
                return None
            removed += b - a
        return time - self.start - removed

    def clamped(self, time: float) -> float:
        """As `output`, but a time inside a cut maps to where the cut is."""
        removed: float = 0
        for a, b in self.cuts:
            if time < a:
                break
            if time < b:
                return a - self.start - removed
            removed += b - a
        return time - self.start - removed

    @property
    def duration(self) -> float:
        """The clip's length with its cuts removed."""
        return self.end - self.start - sum(b - a for a, b in self.cuts)

    def kept(self) -> list[Cut]:
        """The parts that stay, as source times relative to the clip's start."""
        parts: list[Cut] = []
        position = self.start
        for a, b in self.cuts:
            parts.append((position - self.start, a - self.start))
            position = b
        parts.append((position - self.start, self.end - self.start))
        return parts


def cut_crop(crop: CropPath, time_map: TimeMap) -> CropPath:
    """A face crop path with its times moved to the cut clip (ADR 0029).

    A move inside a cut takes effect where the cut ends; of moves that land
    on one time, the last stands.
    """
    if not time_map.cuts:
        return crop
    moved: dict[float, int] = {}
    for seconds, left in crop.points:
        moved[round(time_map.clamped(time_map.start + seconds), 3)] = left
    points = sorted(moved.items())
    return crop.model_copy(update={"points": tuple(points)})
