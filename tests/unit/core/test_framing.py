from clipper.core.framing import (
    CUT_JUMP,
    MIN_HOLD,
    MIN_MOVE,
    PAIR_FIT,
    PAIR_SIZE,
    hand_crop_plan,
    hand_left,
    plan_face_crop,
    plan_facecam,
    window_width,
    with_hand_crops,
)
from clipper.domain.models import (
    CropPath,
    FaceBox,
    FacecamBox,
    FaceSample,
    FaceTrack,
    SpeakerTrack,
    TalkingFace,
)

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


def _speakers(track: FaceTrack, *talk: list[float]) -> SpeakerTrack:
    """Each sample's i-th face scored `talk[i][sample]` for its 5 frames."""
    faces = []
    for i, scores in enumerate(talk):
        faces.append(
            TalkingFace(
                seconds=tuple(sample.seconds for sample in track.samples),
                boxes=tuple(sample.faces[i] for sample in track.samples),
                frames_per_second=25,
                scores=tuple(score for score in scores for _ in range(5)),
            )
        )
    return SpeakerTrack(
        source="talk.mp4",
        start_seconds=track.start_seconds,
        end_seconds=track.end_seconds,
        faces=tuple(faces),
    )


def _spoken(track: FaceTrack, *talk: list[float]) -> list[tuple[float, int]]:
    crop = plan_face_crop(track, W, H, _speakers(track, *talk))
    assert crop is not None
    return list(crop.points)


# Two faces of a size, too far apart to share the window: a podcast two-shot.
_TWO_SHOT = (_face(300), _face(1600))


def test_the_face_talking_is_framed_when_the_pair_does_not_fit() -> None:
    track = _track(*[_TWO_SHOT] * 15)
    assert _spoken(track, [-1.5] * 15, [1.2] * 15) == [(0.0, 1296)]


def test_a_speaker_switch_moves_the_window_like_a_cut() -> None:
    track = _track(*[_TWO_SHOT] * 40)
    left = [1.0] * 20 + [-1.0] * 20
    right = [-1.0] * 20 + [1.0] * 20
    # At 4.0 s the averaged scores nearly tie; from 4.2 s the right face is
    # clearly talking, and the move comes half a sample early, as a cut.
    assert _spoken(track, left, right) == [(0.0, 0), (4.1, 1296)]


def test_a_reply_shorter_than_the_minimum_hold_does_not_move_it() -> None:
    track = _track(*[_TWO_SHOT] * 40)
    reply = [False] * 15 + [True] * 5 + [False] * 20
    left = [-1.0 if r else 1.0 for r in reply]
    right = [1.0 if r else -1.0 for r in reply]
    assert _spoken(track, left, right) == [(0.0, 0)]


def test_a_moment_without_a_clear_speaker_does_not_split_the_shot() -> None:
    # Found on a podcast: a fifth of a second's near-tie jumped to the other
    # face, which split the shot and framed half the speaker.
    track = _track(*[_TWO_SHOT] * 25)
    left = [1.0] * 12 + [0.3] + [1.0] * 12
    right = [-1.0] * 12 + [0.2] + [-1.0] * 12
    assert _spoken(track, left, right) == [(0.0, 0)]


def test_no_clear_speaker_frames_the_largest_face() -> None:
    track = _track(*[_TWO_SHOT] * 15)
    # Close scores (crosstalk, a handheld crowd) and silence.
    assert _spoken(track, [0.2] * 15, [0.4] * 15) == [(0.0, 0)]
    assert _spoken(track, [-1.0] * 15, [-0.8] * 15) == [(0.0, 0)]


def test_a_pair_that_fits_is_still_framed_together() -> None:
    pair = (_face(800, height=0.3), _face(1200, height=0.25))
    track = _track(*[pair] * 15)
    assert _spoken(track, [-1.5] * 15, [1.2] * 15) == [(0.0, 696)]


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


# The facecam style's frame (ADR 0025, facecam style amendment), on a
# constructed box like a streamer's at the bottom right of a 1920x1080 frame.
def test_plan_facecam_stacks_the_cam_over_the_gameplay() -> None:
    box = FacecamBox(x=0.73, y=0.57, w=0.26, h=0.29)
    layout = plan_facecam(box, W, H)
    assert layout.cam == (1402, 616, 498, 312)
    # 1080 * 312 / 498 = 676.6, down to even; the game fills the other 1244.
    assert layout.cam_height == 676
    # Centred at 492 the crop would reach 1428, past the cam's 1402; it
    # slides left to end there.
    assert layout.game == (466, 0, 936, 1080)
    assert layout.position == "top"


def test_plan_facecam_keeps_the_position() -> None:
    box = FacecamBox(x=0.73, y=0.57, w=0.26, h=0.29)
    assert plan_facecam(box, W, H, "bottom").position == "bottom"


def test_a_tall_cam_takes_at_most_half_the_frame() -> None:
    layout = plan_facecam(FacecamBox(x=0.0, y=0.0, w=0.2, h=1.0), W, H)
    assert layout.cam_height == 960
    assert layout.game == (384, 0, 1214, 1080)


def test_a_narrow_source_crops_the_game_by_height() -> None:
    layout = plan_facecam(FacecamBox(x=0.1, y=0.1, w=0.8, h=0.3), 600, 1080)
    _, gy, gw, gh = layout.game
    assert gw == 600
    assert gh < 1080 and gh % 2 == 0
    # Centred, the crop would show the cam (rows 108 to 432); it slides down
    # as far as the frame allows.
    assert gy == 1080 - gh
    # The crop has the game area's shape, 1080 wide by the rest of 1920.
    assert abs(gw / gh - 1080 / (1920 - layout.cam_height)) < 0.01


# ADR 0025, 12a amendment: the gameplay crop slides clear of the cam.
def test_a_corner_cam_is_left_out_of_the_gameplay() -> None:
    # The box found on a Lacy VOD (v2937465519), top left; the cam ends at
    # 494 and a centred crop would start at 353.
    box = FacecamBox(x=0.10078125, y=0.0, w=0.15625, h=0.2569444444444444)
    gx, _, gw, _ = plan_facecam(box, W, H).game
    assert (gx, gw) == (494, 1214)
    assert gx + gw <= W


def test_a_cam_clear_of_the_centre_crop_leaves_it_centred() -> None:
    box = FacecamBox(x=0.0, y=0.0, w=0.15, h=0.2)
    gx, _, gw, _ = plan_facecam(box, W, H).game
    assert gx == (W - gw) // 2
    assert gx >= 0.15 * W


def test_a_cam_no_slide_can_clear_keeps_the_crop_centred() -> None:
    box = FacecamBox(x=0.45, y=0.0, w=0.1, h=0.3)
    gx, _, gw, _ = plan_facecam(box, W, H).game
    assert gx == (W - gw) // 2


def test_the_crop_slides_only_as_far_as_the_frame_allows() -> None:
    # Cam from 576 to 960: clearing it would need the crop to start at 960,
    # past the frame's 1920 - 1214 = 706, so it stops at the edge.
    box = FacecamBox(x=0.3, y=0.0, w=0.2, h=0.5)
    gx, _, gw, _ = plan_facecam(box, W, H).game
    assert (gx, gx + gw) == (706, 1920)
