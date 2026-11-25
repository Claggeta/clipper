from pathlib import Path

import pytest

from clipper.core.speakers import (
    MIN_LEAD,
    SCORE_WINDOW,
    link_faces,
    overlap,
    score_speakers,
    speaker_boxes,
)
from clipper.domain.errors import FaceError
from clipper.domain.models import (
    FaceBox,
    FaceSample,
    FaceTrack,
    SpeakerTrack,
    TalkingFace,
)


def box(x: float, y: float = 0.2, side: float = 0.2) -> FaceBox:
    return FaceBox(x=x, y=y, w=side, h=side, score=0.9)


def track(*samples: tuple[FaceBox, ...]) -> FaceTrack:
    return FaceTrack(
        source="talk.mp4",
        start_seconds=10.0,
        end_seconds=10.0 + len(samples) / 5,
        samples_per_second=5.0,
        samples=tuple(
            FaceSample(seconds=10.0 + i / 5, faces=faces)
            for i, faces in enumerate(samples)
        ),
    )


def test_overlap_is_intersection_over_union() -> None:
    assert overlap(box(0.1), box(0.1)) == pytest.approx(1.0)
    assert overlap(box(0.1), box(0.2)) == pytest.approx(0.02 / 0.06)
    assert overlap(box(0.1), box(0.5)) == 0.0


def test_two_faces_become_two_tracks_in_order() -> None:
    left, right = box(0.1), box(0.6)

    linked = link_faces(track(*[(right, left)] * 4))

    assert [[b for _, b in faces] for faces in linked] == [[right] * 4, [left] * 4]
    assert [s for s, _ in linked[0]] == pytest.approx([10.0, 10.2, 10.4, 10.6])


def test_a_moving_face_is_followed_while_it_overlaps() -> None:
    linked = link_faces(track((box(0.10),), (box(0.12),), (box(0.14),), (box(0.40),)))

    assert [b.x for _, b in linked[0]] == [0.10, 0.12, 0.14]
    # The jump starts a track of its own, too short to keep.
    assert len(linked) == 1


def test_a_short_gap_is_bridged_and_a_long_one_ends_the_track() -> None:
    face = (box(0.3),)

    bridged = link_faces(track(face, face, (), face, face))
    split = link_faces(track(face, face, face, (), (), (), face, face, face))

    assert [len(faces) for faces in bridged] == [4]
    assert [len(faces) for faces in split] == [3, 3]


def test_tracks_of_two_detections_or_fewer_are_dropped() -> None:
    assert link_faces(track((box(0.3),), (box(0.3),))) == []
    assert link_faces(track((), (), ())) == []


def talking(
    face: FaceBox, start: float, scores: list[float], samples: int = 5
) -> TalkingFace:
    return TalkingFace(
        seconds=tuple(start + i / 5 for i in range(samples)),
        boxes=(face,) * samples,
        frames_per_second=25,
        scores=tuple(scores),
    )


def speakers(*faces: TalkingFace) -> SpeakerTrack:
    return SpeakerTrack(
        source="talk.mp4", start_seconds=10.0, end_seconds=11.0, faces=faces
    )


def test_settings_match_adr_0025() -> None:
    assert (SCORE_WINDOW, MIN_LEAD) == (0.5, 0.5)


def test_the_speaker_is_the_face_clearly_ahead() -> None:
    left, right = box(0.1), box(0.6)
    faces = track(*[(left, right)] * 5)

    ahead = speaker_boxes(
        faces,
        speakers(talking(left, 10.0, [1.0] * 25), talking(right, 10.0, [0.0] * 25)),
    )
    close = speaker_boxes(
        faces,
        speakers(talking(left, 10.0, [0.6] * 25), talking(right, 10.0, [0.3] * 25)),
    )
    silent = speaker_boxes(faces, speakers(talking(left, 10.0, [-0.5] * 25)))

    assert ahead == [left] * 5
    assert close == [None] * 5
    assert silent == [None] * 5


def test_scores_are_averaged_around_each_sample() -> None:
    face = box(0.3)
    # Talking only in frames 10 to 14, around the sample at 10.4 s.
    scores = [-1.0] * 10 + [3.0] * 5 + [-1.0] * 10

    found = speaker_boxes(track(*[(face,)] * 5), speakers(talking(face, 10.0, scores)))

    # 0.5 s around 10.4 s is frames 4 to 16, and around 10.6 s frames 9 to
    # 21: each holds all 5 frames at 3.0, a mean above 0; 10.2 s holds 2.
    # At 10.8 s it is held as the last speaker.
    assert found == [None, None, face, face, face]


def test_the_last_speaker_is_held_through_a_moment_without_a_clear_one() -> None:
    left, right = box(0.1), box(0.6)
    # Left talks, nearly ties with the right at 10.4 s, then talks again.
    left_scores = [1.0] * 8 + [0.3] * 9 + [1.0] * 8
    right_scores = [-1.0] * 8 + [0.2] * 9 + [-1.0] * 8

    found = speaker_boxes(
        track(*[(left, right)] * 5),
        speakers(talking(left, 10.0, left_scores), talking(right, 10.0, right_scores)),
    )

    assert found == [left] * 5


def test_a_held_speaker_is_let_go_once_out_of_sight() -> None:
    left, right = box(0.1), box(0.6)
    # Left talks, then leaves the shot; the right face never talks.
    gone = talking(left, 10.0, [1.0] * 10, samples=2)
    there = talking(right, 10.0, [-1.0] * 25)
    faces = track(*[(left, right)] * 2, *[(right,)] * 3)

    found = speaker_boxes(faces, speakers(gone, there))

    assert found == [left, left, None, None, None]


class FailingScorer:
    def score(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> SpeakerTrack:
        raise FaceError("no audio")


class EmptyScorer:
    def score(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> SpeakerTrack:
        return speakers()


def test_score_speakers_says_why_when_it_cannot() -> None:
    assert score_speakers(FailingScorer(), Path("talk.mp4"), 0.0, 1.0) == (
        None,
        "no audio",
    )
    assert score_speakers(EmptyScorer(), Path("talk.mp4"), 0.0, 1.0) == (
        speakers(),
        None,
    )
