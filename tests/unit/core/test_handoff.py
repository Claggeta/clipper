import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from clipper.core.captions import CaptionWord
from clipper.core.handoff import (
    Cue,
    Media,
    Shot,
    build_fcpxml,
    build_srt,
    clip_shots,
    fill_scale,
    import_steps,
    subtitle_cues,
    timeline_seconds,
)
from clipper.domain.models import ManifestClip, SequenceBlock


def _clip(**fields: object) -> ManifestClip:
    return ManifestClip.model_validate(
        {
            "rank": 1,
            "start_seconds": 10.0,
            "end_seconds": 20.0,
            "score": 0.5,
            "reasons": ["r"],
            "file": "v_1.mp4",
            **fields,
        }
    )


def test_a_clip_is_its_span_less_its_cuts() -> None:
    clip = _clip(cuts=[[12.0, 13.0], [15.0, 16.0]])
    assert clip_shots(0, clip, "v.mp4") == [
        Shot(0, "v.mp4", 10.0, 12.0),
        Shot(0, "v.mp4", 13.0, 15.0),
        Shot(0, "v.mp4", 16.0, 20.0),
    ]
    assert clip_shots(2, _clip(), "v.mp4") == [Shot(2, "v.mp4", 10.0, 20.0)]


def test_a_clip_with_a_sequence_is_its_blocks_with_broll() -> None:
    clip = _clip(
        sequence=[
            SequenceBlock(start_seconds=15.0, end_seconds=18.0),
            SequenceBlock(
                start_seconds=0.0, end_seconds=2.0, broll="g.mp4", sound=False
            ),
            SequenceBlock(start_seconds=10.0, end_seconds=12.0),
        ]
    )
    assert clip_shots(1, clip, "v.mp4") == [
        Shot(1, "v.mp4", 15.0, 18.0),
        Shot(1, "g.mp4", 0.0, 2.0, broll=True, sound=False),
        Shot(1, "v.mp4", 10.0, 12.0),
    ]


def _w(start: float, text: str) -> CaptionWord:
    return CaptionWord(start, start + 0.4, text)


def test_captions_follow_the_shots_into_timeline_time() -> None:
    shots = [
        Shot(0, "v.mp4", 10.0, 12.0),
        Shot(0, "g.mp4", 0.0, 1.0, broll=True),
        Shot(0, "v.mp4", 13.0, 15.0),
        Shot(1, "w.mp4", 100.0, 101.0),
    ]
    chunks = {
        0: [[_w(10.5, "one"), _w(11.0, "two")], [_w(11.8, "split"), _w(13.2, "here")]],
        1: [[_w(100.2, "other")]],
    }
    cues = subtitle_cues(shots, chunks)
    assert [c.text for c in cues] == ["one two", "split", "here", "other"]
    # "split" ends at the cut; "here" comes after 2 s of clip and 1 s of B-roll.
    assert cues[1].end == pytest.approx(2.0)
    assert cues[2].start == pytest.approx(3.2)
    assert cues[3].start == pytest.approx(5.2)


def test_srt_numbers_and_stamps_each_cue() -> None:
    srt = build_srt([Cue(0.0, 1.5, "hi there"), Cue(3661.25, 3662.0, "later")])
    assert srt == (
        "1\n00:00:00,000 --> 00:00:01,500\nhi there\n\n"
        "2\n01:01:01,250 --> 01:01:02,000\nlater\n"
    )


def test_fill_scale_fills_the_height_with_a_wide_picture() -> None:
    assert fill_scale(1920, 1080) == pytest.approx(1920 / 607.5)
    assert fill_scale(1080, 1920) is None
    assert fill_scale(None, 1080) is None


def test_the_timeline_puts_shots_in_turn_and_fills_only_the_source() -> None:
    media = {
        "v.mp4": Media(Path("D:/videos/v.mp4"), 3600.0, 1920, 1080, True),
        "lib/g.mp4": Media(Path("D:/broll/lib/a clip.mp4"), 5.0, 1080, 1920, True),
    }
    shots = [
        Shot(0, "v.mp4", 10.0, 12.0),
        Shot(0, "lib/g.mp4", 0.0, 1.5, broll=True, sound=False),
        Shot(0, "v.mp4", 13.0, 15.0),
    ]
    root = ET.fromstring(build_fcpxml("Jason & Ron", shots, media))
    assert root.get("version") == "1.9"
    assets = root.findall("./resources/asset")
    assert [a.get("duration") for a in assets] == ["216000/60s", "300/60s"]
    assert assets[0].get("format") == "r2"
    reps = [a.find("media-rep") for a in assets]
    assert [r.get("src") for r in reps if r is not None] == [
        "file:///D:/videos/v.mp4",
        "file:///D:/broll/lib/a%20clip.mp4",
    ]
    assert root.find("./library/event/project").get("name") == "Jason & Ron"  # type: ignore[union-attr]
    spine = root.findall(".//spine/asset-clip")
    assert [(c.get("offset"), c.get("start"), c.get("duration")) for c in spine] == [
        ("0s", "600/60s", "120/60s"),
        ("120/60s", "0s", "90/60s"),
        ("210/60s", "780/60s", "120/60s"),
    ]
    assert spine[0].find("adjust-transform") is not None
    assert spine[1].find("adjust-transform") is None
    assert spine[1].find("adjust-volume").get("amount") == "-96dB"  # type: ignore[union-attr]
    assert timeline_seconds(shots) == pytest.approx(5.5)


def test_a_still_has_the_length_it_is_used_for() -> None:
    media = {"s.png": Media(Path("D:/broll/s.png"), None, None, None, False)}
    root = ET.fromstring(build_fcpxml("x", [Shot(0, "s.png", 0.0, 3.0, True)], media))
    asset = root.find("./resources/asset")
    assert asset is not None
    assert asset.get("duration") == "180/60s"
    assert asset.get("format") is None
    assert asset.get("hasAudio") is None


def test_the_steps_mention_subtitles_only_with_captions() -> None:
    assert "captions.srt" in import_steps("x", captions=True)
    assert "captions.srt" not in import_steps("x", captions=False)
