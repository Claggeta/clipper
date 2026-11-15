import pytest

from clipper.core.sequence import BlockMap
from clipper.domain.errors import ClipRenderError
from clipper.domain.models import CropPath, HandCrop, SequenceBlock, SoundEffect, Zoom


def _block(start: float, end: float, **own: object) -> SequenceBlock:
    return SequenceBlock.model_validate(
        {"start_seconds": start, "end_seconds": end, **own}
    )


# The crash at 20-26 s, played after the drive (10-16 s), then replayed.
DRIVE, CRASH, REPLAY = _block(10.0, 16.0), _block(20.0, 26.0), _block(20.0, 23.0)


def test_blocks_play_in_turn_and_a_repeat_has_two_clip_times() -> None:
    blocks = BlockMap((DRIVE, CRASH, REPLAY))

    assert blocks.duration == 15.0
    assert blocks.ranges == ((10.0, 16.0), (20.0, 26.0), (20.0, 23.0))
    assert blocks.clip_times(21.0) == [7.0, 13.0]
    assert blocks.clip_times(17.0) == []
    assert blocks.source_time(13.0) == 21.0
    assert blocks.source_time(15.0) == 23.0


def test_no_blocks_is_an_error() -> None:
    with pytest.raises(ClipRenderError, match="at least one block"):
        BlockMap(())


def test_a_zoom_plays_at_every_occurrence_unless_the_block_has_its_own() -> None:
    zoom = Zoom(start_seconds=21.0, end_seconds=22.0, scale=1.5)
    own = Zoom(start_seconds=0.5, end_seconds=2.0, scale=2.0)

    every = BlockMap((DRIVE, CRASH, REPLAY)).zooms((zoom,))
    assert [(z.start_seconds, z.end_seconds, z.scale) for z in every] == [
        (7.0, 8.0, 1.5),
        (13.0, 14.0, 1.5),
    ]
    only = BlockMap((DRIVE, CRASH, _block(20.0, 23.0, zooms=(own,)))).zooms((zoom,))
    assert [(z.start_seconds, z.end_seconds, z.scale) for z in only] == [
        (7.0, 8.0, 1.5),
        (12.5, 14.0, 2.0),
    ]


def test_a_range_across_a_cut_is_one_piece_but_across_a_reorder_two() -> None:
    zoom = Zoom(start_seconds=14.0, end_seconds=22.0, smooth=True)

    # 10-16 then 20-26 is the span 10-26 with 16-20 cut: one eased zoom.
    cut = BlockMap((DRIVE, CRASH)).zooms((zoom,))
    assert [(z.start_seconds, z.end_seconds) for z in cut] == [(4.0, 8.0)]
    # The crash first, then the drive: the same zoom is in two places.
    swapped = BlockMap((CRASH, DRIVE)).zooms((zoom,))
    assert [(z.start_seconds, z.end_seconds) for z in swapped] == [
        (0.0, 2.0),
        (10.0, 12.0),
    ]


def test_hand_windows_follow_the_blocks_in_clip_order() -> None:
    hand = HandCrop(start_seconds=11.0, end_seconds=12.0, x=0.3)
    own = HandCrop(start_seconds=0.0, end_seconds=1.0, x=0.7)

    windows = BlockMap((CRASH, _block(10.0, 16.0), REPLAY)).hands((hand,))
    assert windows == [(7.0, 8.0, 0.3)]
    windows = BlockMap((DRIVE, _block(20.0, 23.0, hand_crops=(own,)))).hands((hand,))
    assert windows == [(1.0, 2.0, 0.3), (6.0, 7.0, 0.7)]


def test_effects_play_at_every_occurrence_and_blocks_add_their_own() -> None:
    boom = SoundEffect(file="boom.wav", at_seconds=21.0)
    pop = SoundEffect(file="pop.wav", at_seconds=0.5)

    placed = BlockMap((DRIVE, CRASH, _block(20.0, 23.0, effects=(pop,)))).effects(
        (boom,)
    )
    assert [(e.file, t) for e, t in placed] == [
        ("boom.wav", 7.0),
        ("pop.wav", 12.5),
        ("boom.wav", 13.0),
    ]


def test_face_crops_join_at_the_blocks_and_a_faceless_block_holds_the_centre() -> None:
    first = CropPath(width=608, height=1080, points=((0.0, 100), (2.0, 300)))
    blocks = BlockMap((DRIVE, CRASH, REPLAY))

    joined = blocks.join_crops((first, None, first), centre=656)

    assert joined.points == (
        (0.0, 100),
        (2.0, 300),
        (6.0, 656),
        (12.0, 100),
        (14.0, 300),
    )
    assert (joined.width, joined.height) == (608, 1080)


def test_no_face_in_any_block_is_an_error() -> None:
    with pytest.raises(ClipRenderError, match="No face"):
        BlockMap((DRIVE,)).join_crops((None,), centre=656)
