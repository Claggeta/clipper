import subprocess
from pathlib import Path

import pytest

from clipper.adapters.rendering.ffmpeg import _VERTICAL_FILTER, FfmpegClipRenderer
from clipper.core.framing import hand_crop_plan
from clipper.core.interfaces import ClipRenderer
from clipper.domain.errors import ClipRenderError
from clipper.domain.models import AudioMix, ClipCandidate, CropPath, MixEffect, Zoom

_CANDIDATE = ClipCandidate(
    start_seconds=32.5, end_seconds=73.25, score=0.5, reasons=("pace",)
)


def _patch_run(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[list[str]],
    *,
    code: int = 0,
    stderr: str = "",
    write: Path | None = None,
) -> None:
    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        if write is not None:
            write.write_bytes(b"partial")
        return subprocess.CompletedProcess(command, code, "", stderr)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", fake_run)


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")
    return path


def test_satisfies_protocol() -> None:
    assert isinstance(FfmpegClipRenderer(), ClipRenderer)


def test_missing_input_raises(tmp_path: Path) -> None:
    with pytest.raises(ClipRenderError, match="not found"):
        FfmpegClipRenderer().render(
            tmp_path / "nope.mp4", _CANDIDATE, tmp_path / "c.mp4"
        )


def test_missing_ffmpeg_raises(video: Path, tmp_path: Path) -> None:
    renderer = FfmpegClipRenderer(executable="definitely-not-ffmpeg")
    with pytest.raises(ClipRenderError, match="PATH"):
        renderer.render(video, _CANDIDATE, tmp_path / "c.mp4")


def test_output_equal_to_input_is_refused(video: Path) -> None:
    with pytest.raises(ClipRenderError, match="overwrite"):
        FfmpegClipRenderer().render(video, _CANDIDATE, video)


def test_success_returns_rendered_clip_and_creates_parent(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "clips" / "c.mp4"
    _patch_run(monkeypatch, [])

    clip = FfmpegClipRenderer().render(video, _CANDIDATE, out)

    assert clip.path == out
    assert clip.candidate == _CANDIDATE
    assert out.parent.is_dir()


def test_success_builds_expected_command(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "c.mp4"
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)

    FfmpegClipRenderer().render(video, _CANDIDATE, out)

    assert captured == [
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-ss",
            "32.500",
            "-i",
            str(video),
            "-t",
            "40.750",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(out),
        ]
    ]


def test_seek_is_before_input_and_banner_is_hidden(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)

    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4")

    command = captured[0]
    assert command.index("-ss") < command.index("-i")
    assert "-hide_banner" in command
    assert command[command.index("-loglevel") + 1] == "error"


def test_float_noise_is_formatted_as_fixed_milliseconds(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = ClipCandidate(
        start_seconds=0.1 + 0.2, end_seconds=1e3, score=0.5, reasons=("pace",)
    )
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)

    FfmpegClipRenderer().render(video, candidate, tmp_path / "c.mp4")

    command = captured[0]
    assert command[command.index("-ss") + 1] == "0.300"
    assert command[command.index("-t") + 1] == "999.700"


def test_nonzero_exit_raises_with_cause_and_exit_code(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run(monkeypatch, [], code=1, stderr="noise\n\nUnknown encoder 'libx264'\n")

    with pytest.raises(ClipRenderError) as excinfo:
        FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4")

    assert "exit 1" in str(excinfo.value)
    assert "Unknown encoder 'libx264'" in str(excinfo.value)


def test_failure_removes_partial_output(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "c.mp4"
    _patch_run(monkeypatch, [], code=1, stderr="boom", write=out)

    with pytest.raises(ClipRenderError):
        FfmpegClipRenderer().render(video, _CANDIDATE, out)

    assert not out.exists()


class _Run:
    """Records each ffmpeg call and what was on disk in its working directory."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, code: int = 0) -> None:
        self.commands: list[list[str]] = []
        self.cwds: list[Path] = []
        self.captions: list[bytes] = []

        def fake_run(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            cwd = kwargs["cwd"]
            assert isinstance(cwd, Path)
            self.commands.append(command)
            self.cwds.append(cwd)
            self.captions.append((cwd / "clip.ass").read_bytes())
            return subprocess.CompletedProcess(command, code, "", "boom")

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
        monkeypatch.setattr(subprocess, "run", fake_run)


# Constructed ASS text; the renderer does not parse it.
_ASS = (
    "[Script Info]\nDialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,café “quoted”\n"
)


def test_no_captions_means_no_filter_and_no_cwd(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwds: list[object] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        cwds.append(kwargs.get("cwd"))
        assert "-vf" not in command
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "run", fake_run)

    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "a.mp4")
    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "b.mp4", captions="")

    assert cwds == [None, None]


def test_captions_add_subtitles_filter_before_video_codec(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Run(monkeypatch)

    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4", captions=_ASS)

    command = run.commands[0]
    vf = command[command.index("-vf") + 1]
    assert vf == "subtitles=clip.ass:charenc=UTF-8"
    assert command.index("-vf") < command.index("-c:v")
    assert command[command.index("-loglevel") + 1] == "error"
    assert "-hide_banner" in command


def test_captions_file_is_utf8_without_bom_or_crlf(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Run(monkeypatch)

    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4", captions=_ASS)

    assert run.captions == [_ASS.encode("utf-8")]
    assert not run.captions[0].startswith(b"\xef\xbb\xbf")
    assert b"\r" not in run.captions[0]


def test_captions_run_in_temp_dir_with_absolute_paths(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Run(monkeypatch)
    monkeypatch.chdir(tmp_path)

    FfmpegClipRenderer().render(
        Path(video.name), _CANDIDATE, Path("rel") / "c.mp4", captions=_ASS
    )

    command = run.commands[0]
    assert run.cwds[0] != tmp_path
    assert Path(command[command.index("-i") + 1]).is_absolute()
    assert Path(command[-1]) == (tmp_path / "rel" / "c.mp4").resolve()


def test_captions_returns_the_callers_output_path(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Run(monkeypatch)
    monkeypatch.chdir(tmp_path)

    clip = FfmpegClipRenderer().render(video, _CANDIDATE, Path("c.mp4"), captions=_ASS)

    assert clip.path == Path("c.mp4")


def test_captions_temp_dir_is_removed_after_success(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Run(monkeypatch)

    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4", captions=_ASS)

    assert not run.cwds[0].exists()


_VERTICAL = (
    "split[bg][fg];"
    "[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
    "boxblur=10:2,scale=1080:1920[blurred];"
    "[fg]scale=1080:1920:force_original_aspect_ratio=decrease:"
    "force_divisible_by=2[fitted];"
    "[blurred][fitted]overlay=(W-w)/2:(H-h)/2,setsar=1"
)


def test_vertical_adds_fit_and_blur_filter(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)

    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4", vertical=True)

    command = captured[0]
    assert command[command.index("-vf") + 1] == _VERTICAL
    assert command.index("-map") < command.index("-vf") < command.index("-c:v")
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"


def test_vertical_with_captions_fits_before_subtitles(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Run(monkeypatch)

    FfmpegClipRenderer().render(
        video, _CANDIDATE, tmp_path / "c.mp4", captions=_ASS, vertical=True
    )

    command = run.commands[0]
    assert command.count("-vf") == 1
    vf = command[command.index("-vf") + 1]
    assert vf == _VERTICAL + ",subtitles=clip.ass:charenc=UTF-8"


def test_captions_temp_dir_is_removed_after_failure(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Run(monkeypatch, code=1)

    with pytest.raises(ClipRenderError, match="boom"):
        FfmpegClipRenderer().render(
            video, _CANDIDATE, tmp_path / "c.mp4", captions=_ASS
        )

    assert not run.cwds[0].exists()


_CROP = CropPath(width=608, height=1080, points=((0.0, 656), (1.2, 700)))
_FACE = "sendcmd=f=crop.cmd,crop@face=608:1080:656:0,scale=1080:1920,setsar=1"


class _CropRun:
    """Records each ffmpeg call and the crop commands in its working directory."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.commands: list[list[str]] = []
        self.files: list[dict[str, bytes]] = []

        def fake_run(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            cwd = kwargs["cwd"]
            assert isinstance(cwd, Path)
            self.commands.append(command)
            self.files.append({p.name: p.read_bytes() for p in cwd.iterdir()})
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")
        monkeypatch.setattr(subprocess, "run", fake_run)


def test_a_crop_path_moves_a_named_crop_in_place_of_the_fit(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _CropRun(monkeypatch)

    FfmpegClipRenderer().render(
        video, _CANDIDATE, tmp_path / "c.mp4", vertical=True, crop=_CROP
    )

    command = run.commands[0]
    assert command[command.index("-vf") + 1] == _FACE
    assert run.files[0] == {
        "crop.cmd": b"0.000 crop@face x 656;\n1.200 crop@face x 700;\n"
    }


def test_a_crop_path_with_captions_crops_before_subtitles(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _CropRun(monkeypatch)

    FfmpegClipRenderer().render(
        video, _CANDIDATE, tmp_path / "c.mp4", captions=_ASS, vertical=True, crop=_CROP
    )

    command = run.commands[0]
    assert command[command.index("-vf") + 1] == (
        _FACE + ",subtitles=clip.ass:charenc=UTF-8"
    )
    assert sorted(run.files[0]) == ["clip.ass", "crop.cmd"]


def test_cuts_drop_their_ranges_from_video_and_audio_first(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)

    FfmpegClipRenderer().render(
        video,
        _CANDIDATE,
        tmp_path / "c.mp4",
        vertical=True,
        cuts=((40.0, 41.5), (50.25, 52.0)),
    )

    command = captured[0]
    removed = "between(t,7.500,9.000)+between(t,17.750,19.500)"
    vf = command[command.index("-vf") + 1]
    assert vf == f"select='not({removed})',setpts=N/FRAME_RATE/TB," + _VERTICAL
    af = command[command.index("-af") + 1]
    assert af == f"aselect='not({removed})',asetpts=N/SR/TB"
    # The length limits the input, before `-i`, so dropped frames are not
    # made up from past the span.
    assert command.index("-ss") < command.index("-t") < command.index("-i")
    assert command[command.index("-t") + 1] == "40.750"


def test_no_cuts_means_no_audio_filter(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)

    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4")

    assert "-af" not in captured[0]


@pytest.mark.parametrize(
    "cuts",
    [((30.0, 35.0),), ((70.0, 74.0),), ((40.0, 45.0), (44.0, 46.0)), ((40.0, 40.0),)],
)
def test_cuts_outside_the_span_or_overlapping_are_refused(
    video: Path, tmp_path: Path, cuts: tuple[tuple[float, float], ...]
) -> None:
    with pytest.raises(ClipRenderError, match="must lie inside the clip"):
        FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4", cuts=cuts)


def test_a_still_is_one_frame_framed_and_captioned_as_a_jpeg(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    out = tmp_path / "c.thumb.jpg"
    _patch_run(monkeypatch, captured, write=out)

    FfmpegClipRenderer().still(video, 61.25, out, captions=_ASS, vertical=True)

    command = captured[0]
    assert command[command.index("-ss") + 1] == "61.250"
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-frames:v") + 1] == "1"
    vf = command[command.index("-vf") + 1]
    assert vf == _VERTICAL + ",subtitles=clip.ass:charenc=UTF-8"
    assert command[-1] == str(out.resolve())


def test_a_face_still_holds_the_crop_where_it_starts(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    out = tmp_path / "c.thumb.jpg"
    _patch_run(monkeypatch, captured, write=out)
    crop = CropPath(width=608, height=1080, points=((0.0, 400), (1.0, 500)))

    FfmpegClipRenderer().still(video, 5.0, out, crop=crop)

    vf = captured[0][captured[0].index("-vf") + 1]
    assert vf == "crop=608:1080:400:0,scale=1080:1920,setsar=1"


def test_a_failed_still_raises_and_leaves_no_file(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    out = tmp_path / "c.thumb.jpg"
    _patch_run(monkeypatch, captured, code=1, stderr="bad", write=out)

    with pytest.raises(ClipRenderError, match="bad"):
        FfmpegClipRenderer().still(video, 5.0, out)
    assert not out.exists()


# --- Zooms and hand crops (ADR 0016 and 0025 amendments) ---


def test_a_zoom_scales_the_frame_by_time_and_crops_around_its_centre(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)
    zoom = Zoom(start_seconds=1.0, end_seconds=3.0, scale=1.5, x=0.5, y=0.25)

    FfmpegClipRenderer().render(
        video, _CANDIDATE, tmp_path / "c.mp4", vertical=True, zooms=(zoom,)
    )

    command = captured[0]
    w = "between(t,1.000,3.000)"
    assert command[command.index("-vf") + 1] == (
        _VERTICAL_FILTER + f",scale=w='trunc(1080*(1+0.500*{w})/2)*2':"
        f"h='trunc(1920*(1+0.500*{w})/2)*2':eval=frame,"
        f"crop=1080:1920:'floor(270.0*{w})':'floor(240.0*{w})',setsar=1"
    )


def test_a_smooth_zoom_eases_in_and_out(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)
    # Shorter than two eases: half its length each way.
    zoom = Zoom(start_seconds=2.0, end_seconds=2.4, smooth=True)

    FfmpegClipRenderer().render(
        video, _CANDIDATE, tmp_path / "c.mp4", vertical=True, zooms=(zoom,)
    )

    vf = captured[0][captured[0].index("-vf") + 1]
    assert "between(t,2.000,2.400)*clip(min((t-2.000)/0.200,(2.400-t)/0.200),0,1)" in vf


def test_without_zooms_or_hands_the_filter_is_unchanged(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)
    FfmpegClipRenderer().render(video, _CANDIDATE, tmp_path / "c.mp4", vertical=True)
    assert captured[0][captured[0].index("-vf") + 1] == _VERTICAL_FILTER


def test_hand_crops_lay_a_moving_window_over_the_fit_in_their_stretches(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _CropRun(monkeypatch)
    hand = hand_crop_plan([(1.0, 2.0, 0.25), (4.0, 5.5, 0.75)], 1920, 1080)

    FfmpegClipRenderer().render(
        video, _CANDIDATE, tmp_path / "c.mp4", vertical=True, hand=hand
    )

    vf = run.commands[0][run.commands[0].index("-vf") + 1]
    assert vf == (
        f"split[zbase][zhand];[zbase]{_VERTICAL_FILTER}[zfit];"
        "[zhand]sendcmd=f=hand.cmd,crop@hand=608:1080:176:0,scale=1080:1920,"
        "setsar=1[zcrop];[zfit][zcrop]overlay=enable="
        "'between(t,1.000,2.000)+between(t,4.000,5.500)',setsar=1"
    )
    # 0.25 of 1920 is 480, less half the 608 window: 176; 0.75: 1136.
    assert run.files[0]["hand.cmd"] == (
        b"0.000 crop@hand x 176;\n4.000 crop@hand x 1136;\n"
    )


def test_zooms_need_the_vertical_frame(video: Path, tmp_path: Path) -> None:
    zoom = Zoom(start_seconds=1.0, end_seconds=2.0)
    with pytest.raises(ClipRenderError, match="need the vertical frame"):
        FfmpegClipRenderer().render(
            video, _CANDIDATE, tmp_path / "c.mp4", zooms=(zoom,)
        )


# --- Clip audio (ADR 0007, audio amendment) ---


def _audio_command(
    video: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    audio: AudioMix,
    **kw: object,
) -> list[str]:
    captured: list[list[str]] = []
    _patch_run(monkeypatch, captured)
    FfmpegClipRenderer().render(
        video,
        _CANDIDATE,
        tmp_path / "c.mp4",
        vertical=True,
        audio=audio,
        **kw,  # type: ignore[arg-type]
    )
    return captured[0]


def test_music_and_an_effect_are_mixed_from_their_own_inputs(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bed, boom = tmp_path / "bed.mp3", tmp_path / "boom.wav"
    audio = AudioMix(
        gain_db=3.0,
        music=bed,
        music_level_db=-18.0,
        effects=(MixEffect(path=boom, at_seconds=2.5, level_db=-3.0),),
    )
    command = _audio_command(video, tmp_path, monkeypatch, audio)
    # The music loops; both follow the video, before the output length.
    i = command.index(str(video))
    assert command[i + 1 : i + 7] == [
        "-stream_loop", "-1", "-i", str(bed.resolve()), "-i", str(boom.resolve())
    ]  # fmt: skip
    assert command[i + 7 : i + 9] == ["-t", "40.750"]
    assert command[command.index("-filter_complex") + 1] == (
        "[0:a:0]volume=3.0dB,asplit=2[voice][key];"
        "[1:a]volume=-18.0dB,afade=t=out:st=39.750:d=1.0[bed];"
        "[bed][key]sidechaincompress=threshold=0.01:ratio=10:attack=20:release=400[music];"
        "[2:a]adelay=2500:all=1,volume=-3.0dB[fx0];"
        "[voice][music][fx0]amix=inputs=3:normalize=0:duration=first[aout]"
    )
    graph_at = command.index("-filter_complex")
    assert command[graph_at + 2 : graph_at + 4] == ["-map", "[aout]"]
    assert "-af" not in command and "0:a:0?" not in command


def test_a_gain_alone_needs_no_mix_inputs_and_music_may_skip_ducking(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _audio_command(video, tmp_path, monkeypatch, AudioMix(gain_db=-6.0))
    assert command[command.index("-filter_complex") + 1] == "[0:a:0]volume=-6.0dB[aout]"
    assert command.count("-i") == 1
    undocked = _audio_command(
        video, tmp_path, monkeypatch, AudioMix(music=tmp_path / "bed.mp3", duck=False)
    )
    graph = undocked[undocked.index("-filter_complex") + 1]
    assert "sidechaincompress" not in graph and "[voice][music]amix=inputs=2" in graph


def test_with_cuts_the_mix_takes_the_cut_filter_and_the_length_moves_to_the_input(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cuts = ((40.0, 45.0),)
    command = _audio_command(
        video, tmp_path, monkeypatch, AudioMix(music=tmp_path / "bed.mp3"), cuts=cuts
    )
    graph = command[command.index("-filter_complex") + 1]
    assert graph.startswith(
        "[0:a:0]aselect='not(between(t,7.500,12.500))',asetpts=N/SR/TB,volume=0.0dB"
    )
    # 40.75 s less the 5 s cut: the music fades over the last second.
    assert "afade=t=out:st=34.750" in graph
    assert command.index("-t") < command.index(str(video))


def test_no_audio_edits_keep_todays_command(
    video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _audio_command(video, tmp_path, monkeypatch, AudioMix())
    assert "-filter_complex" not in command
    assert "0:a:0?" in command
