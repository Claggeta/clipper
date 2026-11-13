import logging
import tempfile
from collections.abc import Sequence
from pathlib import Path

from clipper.adapters.media._process import format_failure, run_tool
from clipper.core.cuts import check_cuts
from clipper.domain.errors import ClipRenderError
from clipper.domain.models import (
    AudioMix,
    ClipCandidate,
    CropPath,
    HandCropPlan,
    RenderedClip,
    Zoom,
)

logger = logging.getLogger(__name__)

# Quality settings chosen for ADR 0007. Not measured against real footage:
# crf 20 is slightly better than libx264's default of 23 because the source is
# already lossy; the preset is libx264's default.
_CRF = "20"
_PRESET = "medium"
_AUDIO_BITRATE = "128k"

# ADR 0010. The filter reads a bare filename from ffmpeg's working directory, so
# no path ever reaches the filter's escaping rules (`:` and `\` on Windows).
# The captions are ASS and carry their own style (ADR 0017), so there is no
# force_style.
_CAPTIONS_NAME = "clip.ass"
_SUBTITLES_FILTER = f"subtitles={_CAPTIONS_NAME}:charenc=UTF-8"

# ADR 0016. The whole frame, fitted inside 1080x1920 at even sizes, centred on
# a blurred copy of itself scaled to fill the frame. The blur runs at quarter
# size (270x480) and is scaled back up, which is far cheaper than blurring at
# full size. Blur strength is a first guess, checked by eye only. setsar=1
# stops players stretching the result.
_VERTICAL_FILTER = (
    "split[bg][fg];"
    "[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
    "boxblur=10:2,scale=1080:1920[blurred];"
    "[fg]scale=1080:1920:force_original_aspect_ratio=decrease:"
    "force_divisible_by=2[fitted];"
    "[blurred][fitted]overlay=(W-w)/2:(H-h)/2,setsar=1"
)

# ADR 0025. The face style's moving crop: `sendcmd` reads the window's left
# edge over time from a bare filename in ffmpeg's working directory, as the
# captions do, and moves the named crop; then the window is scaled to
# 1080x1920.
_CROP_NAME = "crop.cmd"


def _face_filter(crop: CropPath) -> str:
    left = crop.points[0][1]
    return (
        f"sendcmd=f={_CROP_NAME},crop@face={crop.width}:{crop.height}:{left}:0,"
        "scale=1080:1920,setsar=1"
    )


def _crop_commands(crop: CropPath) -> str:
    """One `sendcmd` line per point: from that time, the crop's left edge."""
    return "".join(f"{_seconds(t)} crop@face x {left};\n" for t, left in crop.points)


def _still_crop(crop: CropPath) -> str:
    """The face style's window, held where it starts (ADR 0030)."""
    left = crop.points[0][1]
    return f"crop={crop.width}:{crop.height}:{left}:0,scale=1080:1920,setsar=1"


# ADR 0025, hand crop amendment: the fit style's hand-placed window moves
# by `sendcmd` from its own file, as the face crop does.
_HAND_NAME = "hand.cmd"
# A smooth zoom eases in and out over this long, or half a short zoom.
ZOOM_EASE_SECONDS = 0.3


def _zoom_weight(zoom: Zoom) -> str:
    """0 outside the zoom, 1 inside it; eased at its ends when smooth."""
    a, b = _seconds(zoom.start_seconds), _seconds(zoom.end_seconds)
    inside = f"between(t,{a},{b})"
    if not zoom.smooth:
        return inside
    ease = _seconds(min(ZOOM_EASE_SECONDS, (zoom.end_seconds - zoom.start_seconds) / 2))
    return f"{inside}*clip(min((t-{a})/{ease},({b}-t)/{ease}),0,1)"


def _zoom_filter(zooms: tuple[Zoom, ...]) -> str:
    """ADR 0016, zoom amendment. The 9:16 frame scaled up per frame by the
    zoom in force and cropped back to 1080x1920 around its centre. The
    crop's offsets are worked out from `t`, not `iw`, which `crop` reads
    once (checked on ffmpeg 9.0.2); zooms do not overlap, so the sums hold
    one term at a time."""
    weights = [_zoom_weight(zoom) for zoom in zooms]
    scale = "1+" + "+".join(
        f"{z.scale - 1:.3f}*{w}" for z, w in zip(zooms, weights, strict=True)
    )
    x = "+".join(
        f"{1080 * (z.scale - 1) * z.x:.1f}*{w}"
        for z, w in zip(zooms, weights, strict=True)
    )
    y = "+".join(
        f"{1920 * (z.scale - 1) * z.y:.1f}*{w}"
        for z, w in zip(zooms, weights, strict=True)
    )
    return (
        f"scale=w='trunc(1080*({scale})/2)*2':h='trunc(1920*({scale})/2)*2':eval=frame,"
        f"crop=1080:1920:'floor({x})':'floor({y})',setsar=1"
    )


def _hand_filter(hand: HandCropPlan) -> str:
    """ADR 0025, hand crop amendment. The fit frame, and the hand-placed
    window shown over it in its stretches."""
    left = hand.path.points[0][1]
    shown = "+".join(f"between(t,{_seconds(a)},{_seconds(b)})" for a, b in hand.ranges)
    return (
        f"split[zbase][zhand];[zbase]{_VERTICAL_FILTER}[zfit];"
        f"[zhand]sendcmd=f={_HAND_NAME},crop@hand={hand.path.width}:"
        f"{hand.path.height}:{left}:0,scale=1080:1920,setsar=1[zcrop];"
        f"[zfit][zcrop]overlay=enable='{shown}',setsar=1"
    )


def _hand_commands(hand: HandCropPlan) -> str:
    """One `sendcmd` line per window: from its start, its left edge."""
    return "".join(
        f"{_seconds(t)} crop@hand x {left};\n" for t, left in hand.path.points
    )


# ADR 0007, audio amendment. The music dips under the clip's own sound by a
# sidechain compressor keyed on it. Set on v2924392099: a bed at -18 dB fell
# 11.6 dB under speech (mean -39.5 to -51.1 dB); threshold 0.05 and ratio 8
# dipped it 2.6 dB, 0.005 and 20 nearly 19 dB.
DUCK_THRESHOLD = 0.01
DUCK_RATIO = 10
DUCK_ATTACK_MS = 20
DUCK_RELEASE_MS = 400
MUSIC_FADE_SECONDS = 1.0


def _mix_graph(
    audio: AudioMix,
    length: float,
    cut_filter: str | None,
    voice_input: str = "[0:a:0]",
    music_input: int = 1,
) -> str:
    """ADR 0007, audio amendment. The clip's own sound (cut, then at its
    gain), the music looped from input 1 at its level and faded out at the
    end, ducked under the clip's sound, and each sound effect delayed to
    its moment, mixed without normalising, as long as the clip. A summary's
    voice is its joined clips' sound, and its music the input after them
    (ADR 0032)."""
    cut = cut_filter + "," if cut_filter else ""
    voice = f"{voice_input}{cut}volume={audio.gain_db:.1f}dB"
    parts: list[str] = []
    mixed: list[str] = ["[voice]"]
    first_effect = music_input
    if audio.music is not None:
        parts.append(voice + (",asplit=2[voice][key]" if audio.duck else "[voice]"))
        fade = _seconds(max(length - MUSIC_FADE_SECONDS, 0.0))
        bed = (
            f"[{music_input}:a]volume={audio.music_level_db:.1f}dB,"
            f"afade=t=out:st={fade}:d={MUSIC_FADE_SECONDS:.1f}"
        )
        if audio.duck:
            parts.append(bed + "[bed]")
            parts.append(
                f"[bed][key]sidechaincompress=threshold={DUCK_THRESHOLD}:"
                f"ratio={DUCK_RATIO}:attack={DUCK_ATTACK_MS}:"
                f"release={DUCK_RELEASE_MS}[music]"
            )
        else:
            parts.append(bed + "[music]")
        mixed.append("[music]")
        first_effect = music_input + 1
    else:
        parts.append(voice + "[voice]")
    for n, effect in enumerate(audio.effects):
        delay = round(effect.at_seconds * 1000)
        parts.append(
            f"[{first_effect + n}:a]adelay={delay}:all=1,"
            f"volume={effect.level_db:.1f}dB[fx{n}]"
        )
        mixed.append(f"[fx{n}]")
    if len(mixed) == 1:
        parts[-1] = parts[-1].removesuffix("[voice]") + "[aout]"
    else:
        parts.append(
            "".join(mixed)
            + f"amix=inputs={len(mixed)}:normalize=0:duration=first[aout]"
        )
    return ";".join(parts)


def _mix_inputs(audio: AudioMix) -> list[str]:
    """The music, looped, then each sound effect, as ffmpeg inputs."""
    inputs: list[str] = []
    if audio.music is not None:
        inputs += ["-stream_loop", "-1", "-i", str(audio.music.resolve())]
    for effect in audio.effects:
        inputs += ["-i", str(effect.path.resolve())]
    return inputs


def _cut_filters(
    candidate: ClipCandidate, cuts: tuple[tuple[float, float], ...]
) -> tuple[str, str]:
    """ADR 0029. Video and audio filters that drop the cut ranges and close
    the gaps, so the kept parts play back to back.

    Times are from the clip's start, since `-ss` before `-i` starts the
    input's timestamps at 0. `select`/`aselect` with new timestamps (rather
    than `trim` and `concat`) leaves the audio optional (`-map 0:a:0?`):
    with no audio stream the audio filter has nothing to run on.
    """
    removed = "+".join(
        f"between(t,{_seconds(a - candidate.start_seconds)},"
        f"{_seconds(b - candidate.start_seconds)})"
        for a, b in cuts
    )
    return (
        f"select='not({removed})',setpts=N/FRAME_RATE/TB",
        f"aselect='not({removed})',asetpts=N/SR/TB",
    )


def _seconds(value: float) -> str:
    """Fixed-point milliseconds, never scientific notation."""
    return f"{value:.3f}"


class FfmpegClipRenderer:
    """Renders a clip by re-encoding with ffmpeg. See docs/decisions/0007."""

    def __init__(self, executable: str = "ffmpeg") -> None:
        self._executable = executable

    def render(
        self,
        video_path: Path,
        candidate: ClipCandidate,
        output_path: Path,
        captions: str | None = None,
        *,
        vertical: bool = False,
        crop: CropPath | None = None,
        cuts: tuple[tuple[float, float], ...] = (),
        zooms: tuple[Zoom, ...] = (),
        hand: HandCropPlan | None = None,
        audio: AudioMix | None = None,
    ) -> RenderedClip:
        if not video_path.is_file():
            raise ClipRenderError(f"Input file not found: {video_path}")
        if output_path.resolve() == video_path.resolve():
            raise ClipRenderError(
                f"Output path is the input file, refusing to overwrite: {video_path}"
            )

        check_cuts(cuts, candidate.start_seconds, candidate.end_seconds)
        if (zooms or hand is not None) and not (vertical or crop is not None):
            raise ClipRenderError("Zooms and hand crops need the vertical frame")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Drop the cuts first, so the frame, crop moves and captions all run
        # on the cut clip's times (ADR 0029). Make the vertical frame next, so
        # captions are laid out on it. A crop path is the face style, in
        # place of the fit (ADR 0025).
        filters: list[str] = []
        audio_filter: str | None = None
        if cuts:
            video_cut, audio_filter = _cut_filters(candidate, cuts)
            filters.append(video_cut)
        # UTF-8 with no BOM and LF line endings, whatever the platform.
        files: dict[str, bytes] = {}
        if crop is not None:
            filters.append(_face_filter(crop))
            files[_CROP_NAME] = _crop_commands(crop).encode("utf-8")
        elif hand is not None:
            filters.append(_hand_filter(hand))
            files[_HAND_NAME] = _hand_commands(hand).encode("utf-8")
        elif vertical:
            filters.append(_VERTICAL_FILTER)
        # Zooms after the frame, before captions, so text never zooms.
        if zooms:
            filters.append(_zoom_filter(zooms))
        if captions:
            filters.append(_SUBTITLES_FILTER)
            files[_CAPTIONS_NAME] = captions.encode("utf-8")
        video_filter = ",".join(filters) or None
        # The clip's audio edits replace the cut's audio filter with a graph
        # over the music and effect inputs (ADR 0007, audio amendment).
        mix = None
        extra: list[str] = []
        if audio is not None and audio != AudioMix():
            length = candidate.duration_seconds - sum(b - a for a, b in cuts)
            mix = _mix_graph(audio, length, audio_filter)
            extra = _mix_inputs(audio)
            audio_filter = None
        if not files:
            self._cut(
                video_path,
                candidate,
                output_path,
                video_filter=video_filter,
                audio_filter=audio_filter,
                cut=bool(cuts),
                mix=mix,
                extra_inputs=extra,
            )
        else:
            with tempfile.TemporaryDirectory(
                prefix="clipper-render-", ignore_cleanup_errors=True
            ) as folder:
                for name, data in files.items():
                    (Path(folder) / name).write_bytes(data)
                # ffmpeg runs in `folder`, so the other paths must not depend on
                # the caller's working directory.
                self._cut(
                    video_path.resolve(),
                    candidate,
                    output_path.resolve(),
                    cwd=Path(folder),
                    video_filter=video_filter,
                    audio_filter=audio_filter,
                    cut=bool(cuts),
                    mix=mix,
                    extra_inputs=extra,
                )
        return RenderedClip(candidate=candidate, path=output_path)

    def still(
        self,
        video_path: Path,
        seconds: float,
        output_path: Path,
        captions: str | None = None,
        *,
        vertical: bool = False,
        crop: CropPath | None = None,
    ) -> Path:
        """A thumbnail: one frame, framed as the clip, as a JPEG (ADR 0030)."""
        if not video_path.is_file():
            raise ClipRenderError(f"Input file not found: {video_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        filters: list[str] = []
        if crop is not None:
            filters.append(_still_crop(crop))
        elif vertical:
            filters.append(_VERTICAL_FILTER)
        if captions:
            filters.append(_SUBTITLES_FILTER)
        with tempfile.TemporaryDirectory(
            prefix="clipper-still-", ignore_cleanup_errors=True
        ) as folder:
            if captions:
                (Path(folder) / _CAPTIONS_NAME).write_bytes(captions.encode("utf-8"))
            args = [
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-ss",
                _seconds(seconds),
                "-i",
                str(video_path.resolve()),
                "-frames:v",
                "1",
                *(["-vf", ",".join(filters)] if filters else []),
                # JPEG quality 2 of 31 (lower is better), as for an upload.
                "-q:v",
                "2",
                str(output_path.resolve()),
            ]
            result = run_tool(
                self._executable,
                args,
                error_type=ClipRenderError,
                log=logger,
                cwd=Path(folder),
            )
        if result.returncode != 0 or not output_path.is_file():
            output_path.unlink(missing_ok=True)
            raise ClipRenderError(format_failure(self._executable, result))
        return output_path

    def join(
        self,
        clips: Sequence[Path],
        output_path: Path,
        captions: str | None = None,
        *,
        audio: AudioMix | None = None,
        length: float = 0.0,
    ) -> Path:
        """ADR 0032. Rendered clips joined end to end in one more encode,
        with label cards (`captions`, timed on the joined clip) and a music
        bed of `length` seconds; the clips must share a frame size."""
        missing = [path for path in clips if not path.is_file()]
        if missing:
            raise ClipRenderError(f"Input file not found: {missing[0]}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        count = len(clips)
        graph = [f"[{i}:v:0]setsar=1[v{i}]" for i in range(count)]
        graph.append(
            "".join(f"[v{i}][{i}:a:0]" for i in range(count))
            + f"concat=n={count}:v=1:a=1[vj][aj]"
        )
        graph.append(f"[vj]{_SUBTITLES_FILTER if captions else 'null'}[vout]")
        if audio is not None and audio != AudioMix():
            graph.append(_mix_graph(audio, length, None, "[aj]", count))
        else:
            graph.append("[aj]anull[aout]")
        inputs: list[str] = []
        for path in clips:
            inputs += ["-i", str(path.resolve())]
        if audio is not None and audio.music is not None:
            inputs += ["-stream_loop", "-1", "-i", str(audio.music.resolve())]
        with tempfile.TemporaryDirectory(
            prefix="clipper-join-", ignore_cleanup_errors=True
        ) as folder:
            if captions:
                (Path(folder) / _CAPTIONS_NAME).write_bytes(captions.encode("utf-8"))
            args = [
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                *inputs,
                "-filter_complex",
                ";".join(graph),
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                "-c:v",
                "libx264",
                "-preset",
                _PRESET,
                "-crf",
                _CRF,
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                _AUDIO_BITRATE,
                "-movflags",
                "+faststart",
                str(output_path.resolve()),
            ]
            result = run_tool(
                self._executable,
                args,
                error_type=ClipRenderError,
                log=logger,
                cwd=Path(folder),
            )
        if result.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise ClipRenderError(format_failure(self._executable, result))
        return output_path

    def _cut(
        self,
        video_path: Path,
        candidate: ClipCandidate,
        output_path: Path,
        *,
        cwd: Path | None = None,
        video_filter: str | None = None,
        audio_filter: str | None = None,
        cut: bool = False,
        mix: str | None = None,
        extra_inputs: list[str] | None = None,
    ) -> None:
        # With cuts the length goes on the input: as an output limit, ffmpeg
        # would read past the span's end to make up the frames `select`
        # dropped (ADR 0029).
        duration = ["-t", _seconds(candidate.duration_seconds)]
        on_input = cut or audio_filter is not None
        # A mix's own inputs follow the video, before any output option, and
        # its graph's output is the audio (ADR 0007, audio amendment).
        audio_map = (
            ["-filter_complex", mix, "-map", "[aout]"] if mix else ["-map", "0:a:0?"]
        )
        args = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-ss",
            _seconds(candidate.start_seconds),
            *(duration if on_input else []),
            "-i",
            str(video_path),
            *(extra_inputs or []),
            *([] if on_input else duration),
            # Video is required. Audio is the first stream only, the one the
            # transcriber heard, and optional so a silent source still renders
            # (not with a mix, whose graph needs it).
            "-map",
            "0:v:0",
            *audio_map,
            *(["-vf", video_filter] if video_filter else []),
            *(["-af", audio_filter] if audio_filter else []),
            "-c:v",
            "libx264",
            "-preset",
            _PRESET,
            "-crf",
            _CRF,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            _AUDIO_BITRATE,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = run_tool(
            self._executable, args, error_type=ClipRenderError, log=logger, cwd=cwd
        )
        if result.returncode != 0:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                logger.info("Could not remove partial output %s", output_path)
            raise ClipRenderError(format_failure(self._executable, result))
