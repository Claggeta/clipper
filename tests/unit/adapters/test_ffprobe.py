import json
import subprocess
from pathlib import Path

import pytest

from clipper.adapters.media.ffprobe import FfprobeMediaProbe
from clipper.core.interfaces import MediaProbe
from clipper.domain.errors import MediaProbeError

# Real ffprobe output (ffmpeg 9.0.2, gyan build, Windows 11) for
# `ffprobe -v error -show_entries ... -of json data\no-audio.mp4`.
_VIDEO_ONLY = """{
    "programs": [

    ],
    "stream_groups": [

    ],
    "streams": [
        {
            "codec_type": "video",
            "disposition": {
                "attached_pic": 0
            }
        }
    ],
    "format": {
        "duration": "97.040000"
    }
}
"""

# Real output for `data\test_input.mp4` (video, then audio).
_VIDEO_AND_AUDIO = """{
    "programs": [

    ],
    "stream_groups": [

    ],
    "streams": [
        {
            "codec_type": "video",
            "disposition": {
                "attached_pic": 0
            }
        },
        {
            "codec_type": "audio",
            "disposition": {
                "attached_pic": 0
            }
        }
    ],
    "format": {
        "duration": "314.816145"
    }
}
"""

# Real failure on a text file named bad.mp4: exit 1, bare "{" on stdout.
_BAD_STDOUT = "{\n\n"
_BAD_STDERR = (
    "[mov,mp4,m4a,3gp,3g2,mj2 @ 0000025e49541380] moov atom not found\n"
    r"C:\Users\ALEXCL~1\AppData\Local\Temp\manual-test-clipper\bad.mp4: "
    "Invalid data found when processing input\n"
)


def _stream(codec_type: str, attached_pic: int = 0) -> dict[str, object]:
    return {"codec_type": codec_type, "disposition": {"attached_pic": attached_pic}}


def _output(streams: list[dict[str, object]], duration: object = "10.0") -> str:
    return json.dumps({"streams": streams, "format": {"duration": duration}})


def _patch_run(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    returncode: int = 0,
    stderr: str = "",
    captured: list[list[str]] | None = None,
) -> None:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if captured is not None:
            captured.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(subprocess, "run", fake_run)


@pytest.fixture
def media(tmp_path: Path) -> Path:
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")
    return path


def test_satisfies_protocol() -> None:
    assert isinstance(FfprobeMediaProbe(), MediaProbe)


def test_missing_input_raises(tmp_path: Path) -> None:
    with pytest.raises(MediaProbeError, match="Input file not found"):
        FfprobeMediaProbe().probe(tmp_path / "nope.mp4")


def test_missing_ffprobe_raises(media: Path) -> None:
    probe = FfprobeMediaProbe(executable="definitely-not-ffprobe")
    with pytest.raises(MediaProbeError, match="'definitely-not-ffprobe' not found"):
        probe.probe(media)


def test_video_only_fixture(media: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, _VIDEO_ONLY)

    info = FfprobeMediaProbe().probe(media)

    assert info.duration_seconds == 97.04
    assert info.has_video is True


def test_video_and_audio_fixture(media: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, _VIDEO_AND_AUDIO)

    info = FfprobeMediaProbe().probe(media)

    assert info.duration_seconds == 314.816145
    assert info.has_video is True


def test_command_is_exact(media: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, _VIDEO_ONLY, captured=captured)

    FfprobeMediaProbe().probe(media)

    assert captured == [
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type:stream_disposition=attached_pic",
            "-of",
            "json",
            str(media),
        ]
    ]


def test_failure_reports_exit_code_and_cause_without_parsing(
    media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # stdout is the bare "{" a failed run prints; parsing it would raise a
    # different (invalid JSON) error, so this proves the exit code wins.
    _patch_run(monkeypatch, _BAD_STDOUT, returncode=1, stderr=_BAD_STDERR)

    with pytest.raises(MediaProbeError) as excinfo:
        FfprobeMediaProbe().probe(media)

    assert "ffprobe failed (exit 1)" in str(excinfo.value)
    assert "moov atom not found" in str(excinfo.value)
    assert "invalid JSON" not in str(excinfo.value)


# The cases below use constructed output, not observed ffprobe output.


def test_audio_only_has_no_video(media: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, _output([_stream("audio")]))

    assert FfprobeMediaProbe().probe(media).has_video is False


def test_cover_art_alone_is_not_video(
    media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Constructed: an attached_pic of 1 has not been observed on a real file.
    _patch_run(monkeypatch, _output([_stream("audio"), _stream("video", 1)]))

    assert FfprobeMediaProbe().probe(media).has_video is False


def test_real_video_alongside_cover_art_is_video(
    media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Constructed, as above.
    _patch_run(monkeypatch, _output([_stream("video", 1), _stream("video", 0)]))

    assert FfprobeMediaProbe().probe(media).has_video is True


def test_missing_disposition_counts_as_video(
    media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run(monkeypatch, _output([{"codec_type": "video"}]))

    assert FfprobeMediaProbe().probe(media).has_video is True


def test_missing_streams_key_means_no_video(
    media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run(monkeypatch, json.dumps({"format": {"duration": "5.0"}}))

    assert FfprobeMediaProbe().probe(media).has_video is False


@pytest.mark.parametrize(
    "duration", ["N/A", "abc", "", None, "0.000000", "-3", "nan", "inf"]
)
def test_unusable_duration_raises(
    duration: object, media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run(monkeypatch, _output([_stream("video")], duration))

    with pytest.raises(MediaProbeError, match="No usable duration"):
        FfprobeMediaProbe().probe(media)


def test_missing_duration_key_raises(
    media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run(monkeypatch, json.dumps({"streams": [], "format": {}}))

    with pytest.raises(MediaProbeError, match="No usable duration"):
        FfprobeMediaProbe().probe(media)


def test_missing_format_raises(media: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_run(monkeypatch, json.dumps({"streams": []}))

    with pytest.raises(MediaProbeError, match="Unexpected ffprobe output"):
        FfprobeMediaProbe().probe(media)


@pytest.mark.parametrize("stdout", ["[]", '"x"', "3"])
def test_non_object_json_raises(
    stdout: str, media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run(monkeypatch, stdout)

    with pytest.raises(MediaProbeError, match="Unexpected ffprobe output"):
        FfprobeMediaProbe().probe(media)


@pytest.mark.parametrize("stdout", ["", "{", "not json"])
def test_invalid_json_on_success_raises(
    stdout: str, media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run(monkeypatch, stdout)

    with pytest.raises(MediaProbeError, match="invalid JSON"):
        FfprobeMediaProbe().probe(media)


def test_extra_keys_are_ignored(media: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "programs": [{"x": 1}],
        "streams": [{**_stream("video"), "width": 1920}],
        "format": {"duration": "2.5", "filename": "v.mp4"},
    }
    _patch_run(monkeypatch, json.dumps(payload))

    assert FfprobeMediaProbe().probe(media).duration_seconds == 2.5
