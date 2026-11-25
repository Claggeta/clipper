from pathlib import Path

from clipper.core.interfaces import SpeakerScorer
from clipper.domain.errors import FaceError
from clipper.domain.models import FaceBox, FaceTrack, SpeakerTrack

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


# Who `face` frames (ADR 0025, speaker framing amendment): at each face
# sample, each face's scores are averaged over SCORE_WINDOW seconds around
# it, and the face with the highest average is the speaker when that average
# is above 0 and at least MIN_LEAD above the next face's. Otherwise no one
# clearly is, and `face` frames as it did without speakers.
SCORE_WINDOW = 0.5
MIN_LEAD = 0.5


def speaker_boxes(track: FaceTrack, speakers: SpeakerTrack) -> list[FaceBox | None]:
    """The talking face's box at each of `track`'s samples, or None."""
    half_sample = 0.5 / track.samples_per_second
    found: list[FaceBox | None] = []
    for sample in track.samples:
        heard: list[tuple[float, FaceBox]] = []
        for face in speakers.faces:
            near = min(
                zip(face.seconds, face.boxes, strict=True),
                key=lambda pair: abs(pair[0] - sample.seconds),
            )
            if abs(near[0] - sample.seconds) > half_sample + 1e-6:
                continue
            first = face.seconds[0]
            low = round(
                (sample.seconds - SCORE_WINDOW / 2 - first) * face.frames_per_second
            )
            high = round(
                (sample.seconds + SCORE_WINDOW / 2 - first) * face.frames_per_second
            )
            scores = face.scores[max(low, 0) : max(high + 1, 0)]
            if scores:
                heard.append((sum(scores) / len(scores), near[1]))
        heard.sort(key=lambda pair: pair[0], reverse=True)
        clear = heard and heard[0][0] > 0
        if clear and len(heard) > 1:
            clear = heard[0][0] - heard[1][0] >= MIN_LEAD
        found.append(heard[0][1] if clear else None)
    return found


def score_speakers(
    scorer: SpeakerScorer, video_path: Path, start_seconds: float, end_seconds: float
) -> tuple[SpeakerTrack | None, str | None]:
    """The span's speakers, or None and why not: `face` then frames the
    largest face, as before speakers (ADR 0025)."""
    try:
        return scorer.score(video_path, start_seconds, end_seconds), None
    except FaceError as exc:
        return None, str(exc)
