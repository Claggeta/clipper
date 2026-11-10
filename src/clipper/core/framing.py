from clipper.domain.models import CropPath, FaceSample, FaceTrack

# The face style's window (ADR 0025): it holds while the face stays within
# DEAD_ZONE of the window's width of its centre, moves EASE of the way to the
# face per sample, and jumps at once when the face jumps more than CUT_JUMP of
# the frame's width between samples, taken as a camera cut. The spike's
# settings, with a jump in place of a fast ease at cuts.
DEAD_ZONE = 0.15
EASE = 0.25
CUT_JUMP = 0.30


def window_width(frame_width: int, frame_height: int) -> int:
    """A full-height 9:16 window, even for yuv420p, and no wider than the frame."""
    return min(round(frame_height * 9 / 16), frame_width) // 2 * 2


def plan_face_crop(
    track: FaceTrack, frame_width: int, frame_height: int
) -> CropPath | None:
    """Where a full-height 9:16 window sits over time, following the largest face.

    None if no sample has a face. The window holds where no face is found and
    never leaves the frame. Points are kept only where the window moves.
    """
    centres = [_largest_centre(s, frame_width) for s in track.samples]
    found = [c for c in centres if c is not None]
    if not found:
        return None
    window = window_width(frame_width, frame_height)
    height = frame_height // 2 * 2
    centre = found[0]
    last_face: float | None = None
    points: list[tuple[float, int]] = []
    for sample, face in zip(track.samples, centres, strict=True):
        if face is not None:
            if last_face is not None and abs(face - last_face) > CUT_JUMP * frame_width:
                centre = face
            elif abs(face - centre) > DEAD_ZONE * window:
                centre += (face - centre) * EASE
            last_face = face
        left = min(max(round(centre - window / 2), 0), frame_width - window)
        if not points or points[-1][1] != left:
            seconds = 0.0 if not points else sample.seconds - track.start_seconds
            points.append((round(seconds, 3), left))
    return CropPath(width=window, height=height, points=tuple(points))


def _largest_centre(sample: FaceSample, frame_width: int) -> float | None:
    """The largest face's centre, in source pixels across; None if no face."""
    if not sample.faces:
        return None
    face = max(sample.faces, key=lambda f: f.h)
    return (face.x + face.w / 2) * frame_width
