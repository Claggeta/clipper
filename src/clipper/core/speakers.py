from clipper.domain.models import FaceBox, FaceTrack

# Light-ASD's demo tracker (ADR 0025, speaker scorer amendment): a face
# continues a track when it overlaps the track's last box by more than half
# (intersection over union), within 0.4 s (its 10 frames at 25 fps); a track
# needs more than 0.4 s of detections to count.
MIN_OVERLAP = 0.5
MAX_GAP_SECONDS = 0.4
MIN_TRACK_SECONDS = 0.4


def overlap(a: FaceBox, b: FaceBox) -> float:
    """Intersection over union of two boxes."""
    width = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    height = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    inter = max(0.0, width) * max(0.0, height)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def link_faces(track: FaceTrack) -> list[list[tuple[float, FaceBox]]]:
    """Each face followed through the samples, as (seconds, box) in time order.

    Greedy, as Light-ASD's `track_shot`: a track takes the first face left
    over, then at each later sample the first face that continues it, and
    ends at a gap over `MAX_GAP_SECONDS`; repeated until no face is left.
    Tracks with too few detections are dropped. Ordered by first detection.
    """
    # A sample is one frame, so a track never takes two faces from it.
    left = [(sample.seconds, list(sample.faces)) for sample in track.samples]
    min_detections = MIN_TRACK_SECONDS * track.samples_per_second
    found: list[list[tuple[float, FaceBox]]] = []
    while True:
        current: list[tuple[float, FaceBox]] = []
        for seconds, faces in left:
            if current and seconds - current[-1][0] > MAX_GAP_SECONDS + 1e-6:
                break
            for face in faces:
                if not current or overlap(face, current[-1][1]) > MIN_OVERLAP:
                    current.append((seconds, face))
                    faces.remove(face)
                    break
        if not current:
            return sorted(found, key=lambda faces: faces[0][0])
        if len(current) > min_detections:
            found.append(current)
