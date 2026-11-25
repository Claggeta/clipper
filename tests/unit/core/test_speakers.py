import pytest

from clipper.core.speakers import link_faces, overlap
from clipper.domain.models import FaceBox, FaceSample, FaceTrack


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
