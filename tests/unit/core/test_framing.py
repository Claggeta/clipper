from clipper.core.framing import (
    CUT_JUMP,
    MIN_HOLD,
    MIN_MOVE,
    PAIR_FIT,
    PAIR_SIZE,
    hand_crop_plan,
    hand_left,
    plan_face_crop,
    window_width,
    with_hand_crops,
)
from clipper.domain.models import CropPath, FaceBox, FaceSample, FaceTrack

# Constructed tracks on a 1920x1080 frame, 5 samples a second: the window is
# 608 px wide. A face 96 px wide (0.05) is inside the window while its centre
# is within 256 px of the window's centre.
W, H = 1920, 1080


def _face(centre_px: float, height: float = 0.3) -> FaceBox:
    width = 0.05
    return FaceBox(x=centre_px / W - width / 2, y=0.2, w=width, h=height, score=0.9)


def _track(*samples: tuple[FaceBox, ...] | None, start: float = 100.0) -> FaceTrack:
    return FaceTrack(
        source="talk.mp4",
        start_seconds=start,
        end_seconds=start + 0.2 * len(samples),
        samples_per_second=5.0,
        samples=tuple(
            FaceSample(seconds=start + i * 0.2, faces=s or ())
            for i, s in enumerate(samples)
        ),
    )


def _at(centre_px: float, seconds: float) -> list[tuple[FaceBox, ...]]:
    """A face at one place for a number of seconds."""
    return [(_face(centre_px),)] * round(seconds * 5)


def _points(track: FaceTrack) -> list[tuple[float, int]]:
    crop = plan_face_crop(track, W, H)
    assert crop is not None
    return list(crop.points)


def test_settings_match_adr_0025() -> None:
    assert (CUT_JUMP, MIN_HOLD, MIN_MOVE) == (0.30, 2.0, 0.05)
    assert (PAIR_SIZE, PAIR_FIT) == (0.6, 1.15)


def test_the_window_is_full_height_9_16_and_even() -> None:
    assert window_width(1920, 1080) == 608
    assert window_width(1280, 720) == 404
    # A source narrower than 9:16 keeps its whole width.
    assert window_width(600, 1200) == 600


def test_no_face_anywhere_gives_no_crop() -> None:
    assert plan_face_crop(_track(None, None), W, H) is None


def test_the_window_locks_on_the_median_of_a_shot() -> None:
    track = _track(*_at(900, 2), *_at(1000, 2), *_at(960, 2))
    assert plan_face_crop(track, W, H) == CropPath(
        width=608, height=1080, points=((0.0, 656),)
    )


def test_a_brief_exit_from_the_window_is_ignored() -> None:
    # 1.6 s with the face 300 px off the window's centre: outside, but brief.
    track = _track(*_at(960, 3), *_at(1260, 1.6), *_at(960, 3))
    assert _points(track) == [(0.0, 656)]


def test_a_sustained_exit_relocks_where_it_began() -> None:
    track = _track(*_at(960, 3), *_at(1250, 3))
    assert _points(track) == [(0.0, 656), (2.9, 946)]


def test_a_camera_cut_moves_the_window_at_the_cut() -> None:
    # Half a sample (0.1 s) before the first sample of the new shot.
    track = _track(*_at(400, 3), *_at(1500, 3))
    assert _points(track) == [(0.0, 96), (2.9, 1196)]


def test_a_cut_is_seen_across_samples_without_a_face() -> None:
    track = _track(*_at(400, 3), None, None, *_at(1500, 3))
    assert _points(track) == [(0.0, 96), (3.3, 1196)]


def test_a_position_shorter_than_the_minimum_hold_is_skipped() -> None:
    track = _track(*_at(400, 3), *_at(1500, 1), *_at(400, 3))
    assert _points(track) == [(0.0, 96)]


def test_a_small_move_does_not_move_the_window() -> None:
    # The 1 s at 1700 is too short to hold; 990 is 30 px from 960, under 5%
    # of the window (30.4 px).
    track = _track(*_at(960, 3), *_at(1700, 1), *_at(990, 3))
    assert _points(track) == [(0.0, 656)]


def test_two_similar_faces_close_together_are_framed_as_a_pair() -> None:
    pair = (_face(800, height=0.3), _face(1200, height=0.25))
    track = _track(*[pair] * 15)
    # The pair spans 752 to 1248 px; its centre is 1000.
    assert _points(track) == [(0.0, 696)]


def test_a_much_smaller_or_distant_second_face_is_not_paired() -> None:
    small = (_face(800, height=0.3), _face(1200, height=0.1))
    distant = (_face(300, height=0.3), _face(1600, height=0.3))
    assert _points(_track(*[small] * 15)) == [(0.0, 496)]
    # Too far apart to share the window: the largest (here the first) is kept.
    assert _points(_track(*[distant] * 15)) == [(0.0, 0)]


def test_the_window_never_leaves_the_frame() -> None:
    assert _points(_track(*_at(1900, 3))) == [(0.0, W - 608)]


def test_a_shot_whose_face_appears_late_locks_on_it() -> None:
    # No face in the first 2 s: the lock uses the faces that come later.
    assert _points(_track(*[None] * 12, *_at(1500, 3))) == [(0.0, 1196)]


# --- Hand crops (ADR 0025, hand crop amendment) ---


def test_hand_left_centres_the_window_and_keeps_it_in_the_frame() -> None:
    assert hand_left(0.5, 1920, 608) == 656
    assert hand_left(0.0, 1920, 608) == 0
    assert hand_left(1.0, 1920, 608) == 1312


def test_hand_crops_replace_the_track_and_hand_it_back() -> None:
    tracked = CropPath(
        width=608, height=1080, points=((0.0, 600), (3.0, 900), (8.0, 700))
    )
    merged = with_hand_crops(tracked, [(2.0, 5.0, 0.0)], 1920)
    # The tracked move at 3 s falls inside the hand stretch and is dropped;
    # at 5 s the track's position then (900) comes back.
    assert merged.points == ((0.0, 600), (2.0, 0), (5.0, 900), (8.0, 700))
    assert with_hand_crops(tracked, [], 1920) is tracked


def test_a_hand_crop_from_the_start_replaces_the_first_point() -> None:
    tracked = CropPath(width=608, height=1080, points=((0.0, 600),))
    merged = with_hand_crops(tracked, [(0.0, 1.0, 1.0)], 1920)
    assert merged.points == ((0.0, 1312), (1.0, 600))


def test_hand_crop_plan_moves_one_window_at_each_stretch() -> None:
    plan = hand_crop_plan([(1.0, 2.0, 0.25), (4.0, 5.0, 0.5)], 1920, 1080)
    assert plan is not None
    assert plan.path.points == ((0.0, 176), (4.0, 656))
    assert plan.ranges == ((1.0, 2.0), (4.0, 5.0))
    assert hand_crop_plan([], 1920, 1080) is None
