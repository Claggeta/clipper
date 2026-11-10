from clipper.core.framing import (
    CUT_JUMP,
    DEAD_ZONE,
    EASE,
    plan_face_crop,
    window_width,
)
from clipper.domain.models import CropPath, FaceBox, FaceSample, FaceTrack

# Constructed tracks on a 1920x1080 frame: the window is 608 px wide, its
# dead zone 91.2 px either side of its centre.
W, H = 1920, 1080


def _face(centre_px: float, height: float = 0.3) -> FaceBox:
    width = 0.1
    return FaceBox(x=centre_px / W - width / 2, y=0.2, w=width, h=height, score=0.9)


def _track(*faces: FaceBox | None, start: float = 100.0) -> FaceTrack:
    samples = tuple(
        FaceSample(seconds=start + i * 0.2, faces=() if f is None else (f,))
        for i, f in enumerate(faces)
    )
    return FaceTrack(
        source="talk.mp4",
        start_seconds=start,
        end_seconds=start + 0.2 * len(faces),
        samples_per_second=5.0,
        samples=samples,
    )


def _lefts(crop: CropPath | None) -> list[tuple[float, int]]:
    assert crop is not None
    return list(crop.points)


def test_settings_match_adr_0025() -> None:
    assert (DEAD_ZONE, EASE, CUT_JUMP) == (0.15, 0.25, 0.30)


def test_the_window_is_full_height_9_16_and_even() -> None:
    assert window_width(1920, 1080) == 608
    assert window_width(1280, 720) == 404
    # A source narrower than 9:16 keeps its whole width.
    assert window_width(600, 1200) == 600


def test_no_face_anywhere_gives_no_crop() -> None:
    assert plan_face_crop(_track(None, None), W, H) is None


def test_the_window_starts_on_the_first_face() -> None:
    crop = plan_face_crop(_track(None, _face(960)), W, H)
    assert crop == CropPath(width=608, height=1080, points=((0.0, 656),))


def test_a_face_inside_the_dead_zone_holds_the_window() -> None:
    crop = plan_face_crop(_track(_face(960), _face(1040), _face(890)), W, H)
    assert _lefts(crop) == [(0.0, 656)]


def test_a_face_past_the_dead_zone_is_eased_toward() -> None:
    # 200 px off centre: each sample moves the window a quarter of the way.
    crop = plan_face_crop(_track(_face(960), _face(1160), _face(1160)), W, H)
    assert _lefts(crop) == [(0.0, 656), (0.2, 706), (0.4, 744)]


def test_a_camera_cut_jumps_at_once() -> None:
    crop = plan_face_crop(_track(_face(400), _face(1500)), W, H)
    assert _lefts(crop) == [(0.0, 96), (0.2, 1196)]


def test_a_gap_without_faces_holds_and_still_sees_a_cut() -> None:
    crop = plan_face_crop(_track(_face(400), None, None, _face(1500)), W, H)
    assert _lefts(crop) == [(0.0, 96), (0.6, 1196)]


def test_the_window_never_leaves_the_frame() -> None:
    crop = plan_face_crop(_track(_face(50), _face(1900)), W, H)
    assert _lefts(crop) == [(0.0, 0), (0.2, W - 608)]


def test_the_largest_face_is_followed() -> None:
    both = FaceSample(
        seconds=100.0, faces=(_face(400, height=0.1), _face(1500, height=0.4))
    )
    track = _track(None).model_copy(update={"samples": (both,)})
    assert _lefts(plan_face_crop(track, W, H)) == [(0.0, 1196)]
