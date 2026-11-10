import statistics

from clipper.domain.models import CropPath, FaceTrack

# The face style's window (ADR 0025, locked framing amendment). It holds one
# position per shot and moves only as a cut: at a camera cut, where the
# subject jumps more than CUT_JUMP of the frame's width between samples, or
# where the subject stays outside the window for MIN_HOLD seconds. Every
# position lasts at least MIN_HOLD seconds. Two faces whose heights are within
# PAIR_SIZE of each other, close enough to fit PAIR_FIT windows, are framed as
# a pair rather than guessing which one is speaking. A new position less than
# MIN_MOVE of the window's width from the current one is not worth a cut.
CUT_JUMP = 0.30
MIN_HOLD = 2.0
MIN_MOVE = 0.05
PAIR_SIZE = 0.6
PAIR_FIT = 1.15


class _Point:
    """One sample's subject: its centre and half its width, in source pixels."""

    def __init__(self, seconds: float, centre: float | None, half: float) -> None:
        self.seconds = seconds
        self.centre = centre
        self.half = half


def window_width(frame_width: int, frame_height: int) -> int:
    """A full-height 9:16 window, even for yuv420p, and no wider than the frame."""
    return min(round(frame_height * 9 / 16), frame_width) // 2 * 2


def plan_face_crop(
    track: FaceTrack, frame_width: int, frame_height: int
) -> CropPath | None:
    """Where a full-height 9:16 window sits over time: locked on the subject.

    None if no sample has a face. Within a shot the window does not move;
    brief movement is ignored. Points are kept only where the window moves.
    """
    window = window_width(frame_width, frame_height)
    points = [
        _subject(track, sample_index, frame_width, window)
        for sample_index in range(len(track.samples))
    ]
    if all(p.centre is None for p in points):
        return None
    holds: list[tuple[float, float | None]] = []
    for shot in _shots(points, frame_width):
        holds.extend(_holds(shot, window))
    end = points[-1].seconds
    kept: list[tuple[float, float | None]] = []
    for i, (seconds, centre) in enumerate(holds):
        following = holds[i + 1][0] if i + 1 < len(holds) else end
        if kept and following - seconds < MIN_HOLD:
            continue
        kept.append((seconds, centre))
    lefts: list[tuple[float, int]] = []
    early = 0.5 / track.samples_per_second
    previous = frame_width / 2
    for seconds, centre in kept:
        previous = centre if centre is not None else previous
        left = min(max(round(previous - window / 2), 0), frame_width - window)
        if not lefts or abs(lefts[-1][1] - left) >= MIN_MOVE * window:
            # A cut falls between two samples; moving half a sample early
            # halves how long the old position shows on the new shot.
            moved = max(seconds - early, 0.0)
            lefts.append((0.0 if not lefts else round(moved, 3), left))
    return CropPath(width=window, height=frame_height // 2 * 2, points=tuple(lefts))


def _subject(track: FaceTrack, index: int, frame_width: int, window: int) -> _Point:
    """The largest face, or a close pair of similar faces, in one sample."""
    sample = track.samples[index]
    seconds = sample.seconds - track.start_seconds
    if not sample.faces:
        return _Point(seconds, None, 0.0)
    ranked = sorted(sample.faces, key=lambda f: f.h, reverse=True)
    face = ranked[0]
    left, right = face.x * frame_width, (face.x + face.w) * frame_width
    if len(ranked) > 1 and ranked[1].h >= PAIR_SIZE * face.h:
        other = ranked[1]
        pair_left = min(face.x, other.x) * frame_width
        pair_right = max(face.x + face.w, other.x + other.w) * frame_width
        if pair_right - pair_left <= PAIR_FIT * window:
            left, right = pair_left, pair_right
    return _Point(seconds, (left + right) / 2, min((right - left) / 2, window / 2))


def _shots(points: list[_Point], frame_width: int) -> list[list[_Point]]:
    """Split at camera cuts: the subject jumping across the frame."""
    shots: list[list[_Point]] = [[]]
    last: float | None = None
    for point in points:
        if point.centre is not None:
            if last is not None and abs(point.centre - last) > CUT_JUMP * frame_width:
                shots.append([])
            last = point.centre
        shots[-1].append(point)
    return shots


def _holds(shot: list[_Point], window: int) -> list[tuple[float, float | None]]:
    """Positions for one shot. The window locks where the subject is over the
    first MIN_HOLD seconds; a stretch ends where the subject then stays outside
    it for MIN_HOLD seconds, and each stretch holds at its own median."""
    if not any(p.centre is not None for p in shot):
        return [(shot[0].seconds, None)]
    first = shot[0].seconds
    opening = [
        p.centre for p in shot if p.centre is not None and p.seconds - first < MIN_HOLD
    ]
    anchor = statistics.median(opening or [p.centre for p in shot if p.centre])
    run_start: int | None = None
    for i, point in enumerate(shot):
        if point.centre is None:
            continue
        if abs(point.centre - anchor) > window / 2 - point.half:
            run_start = i if run_start is None else run_start
            if point.seconds - shot[run_start].seconds >= MIN_HOLD and run_start > 0:
                return [
                    *_holds(shot[:run_start], window),
                    *_holds(shot[run_start:], window),
                ]
        else:
            run_start = None
    centres = [p.centre for p in shot if p.centre is not None]
    return [(first, statistics.median(centres))]
