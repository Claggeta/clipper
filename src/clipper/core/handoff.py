from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote
from xml.sax.saxutils import quoteattr

from clipper.core.captions import CaptionWord
from clipper.domain.models import ManifestClip

# ADR 0036. A hand-off to DaVinci Resolve is a vertical timeline at a fixed
# rate, whatever the sources' rates: Resolve conforms them, and one rate
# keeps every time a whole number of frames.
TIMELINE_FPS = 60
TIMELINE_WIDTH = 1080
TIMELINE_HEIGHT = 1920
# Hand-offs go to <clips dir>/resolve/<name>/.
HANDOFF_FOLDER = "resolve"


class Shot(NamedTuple):
    """One clip on the hand-off timeline: `start` to `end` seconds of a
    source video (its manifest's `source` name), or of a B-roll file with
    `broll` set. `clip` is the index of the Clipper clip it came from."""

    clip: int
    media: str
    start: float
    end: float
    broll: bool = False
    sound: bool = True


@dataclass(frozen=True)
class Media:
    """A file the timeline uses, as probed: `width`, `height` and
    `duration` are None for a still or when the probe could not tell."""

    path: Path
    duration: float | None
    width: int | None
    height: int | None
    audio: bool


class Cue(NamedTuple):
    """One subtitle, in timeline seconds."""

    start: float
    end: float
    text: str


def clip_shots(index: int, clip: ManifestClip, source: str) -> list[Shot]:
    """A clip as timeline shots: its sequence's blocks when it has one (ADR
    0034), else its span less its cuts (ADR 0029)."""
    if clip.sequence:
        return [
            Shot(
                index,
                block.broll or source,
                block.start_seconds,
                block.end_seconds,
                broll=block.broll is not None,
                sound=block.sound,
            )
            for block in clip.sequence
        ]
    shots: list[Shot] = []
    at = clip.start_seconds
    for start, end in clip.cuts:
        if start > at:
            shots.append(Shot(index, source, at, start))
        at = max(at, end)
    if clip.end_seconds > at:
        shots.append(Shot(index, source, at, clip.end_seconds))
    return shots


def _frame(seconds: float) -> int:
    return round(seconds * TIMELINE_FPS)


def _length(shot: Shot) -> int:
    """A shot's length in timeline frames, at least one."""
    return max(1, _frame(shot.end) - _frame(shot.start))


def subtitle_cues(
    shots: Sequence[Shot], chunks: Mapping[int, Sequence[Sequence[CaptionWord]]]
) -> list[Cue]:
    """Each caption chunk's words in each source shot that holds them, in
    timeline seconds; `chunks` are per clip, in source seconds (as
    `caption_chunks` gives them). A chunk split by a cut is a cue each side;
    B-roll shots have none."""
    cues: list[Cue] = []
    at = 0
    for shot in shots:
        offset = at / TIMELINE_FPS
        start, end = _frame(shot.start) / TIMELINE_FPS, shot.end
        if not shot.broll:
            for chunk in chunks.get(shot.clip, ()):
                words = [w for w in chunk if start <= w.start < end]
                if words:
                    cues.append(
                        Cue(
                            offset + words[0].start - start,
                            offset + min(words[-1].end, end) - start,
                            " ".join(w.text for w in words),
                        )
                    )
        at += _length(shot)
    return cues


def _stamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    hours, minutes = ms // 3_600_000, ms // 60_000 % 60
    return f"{hours:02}:{minutes:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def build_srt(cues: Sequence[Cue]) -> str:
    """Numbered SubRip cues, for Resolve's subtitle track."""
    return "\n".join(
        f"{n}\n{_stamp(cue.start)} --> {_stamp(cue.end)}\n{cue.text}\n"
        for n, cue in enumerate(cues, 1)
    )


def fill_scale(width: int | None, height: int | None) -> float | None:
    """The scale over Resolve's fit that fills the vertical frame's height
    with a wider picture, cropping its sides; None when it already fills or
    the size is unknown."""
    if not width or not height:
        return None
    fit = min(TIMELINE_WIDTH / width, TIMELINE_HEIGHT / height)
    fill = max(TIMELINE_WIDTH / width, TIMELINE_HEIGHT / height)
    return fill / fit if fill / fit > 1.0001 else None


def _time(frames: int) -> str:
    return f"{frames}/{TIMELINE_FPS}s" if frames else "0s"


def _url(path: Path) -> str:
    return "file:///" + quote(path.as_posix().lstrip("/"), safe=":/")


def build_fcpxml(name: str, shots: Sequence[Shot], media: Mapping[str, Media]) -> str:
    """An FCPXML 1.9 project of the shots in turn on one track, for Resolve's
    Import Timeline (ADR 0036). Source shots are scaled to fill the frame's
    height; B-roll stays fitted, as review inserts it."""
    keys = {m: f"a{i}" for i, m in enumerate(dict.fromkeys(s.media for s in shots))}
    # A format per picture size, as Final Cut writes them; the first is the
    # timeline's.
    sizes = {(TIMELINE_WIDTH, TIMELINE_HEIGHT): "r1"}
    assets = []
    for medium, key in keys.items():
        found = media[medium]
        size = ""
        if found.width and found.height:
            fmt = sizes.setdefault((found.width, found.height), f"r{len(sizes) + 1}")
            size = f' format="{fmt}"'
        extent = (
            _time(_frame(found.duration))
            if found.duration
            else _time(max(_frame(s.end) for s in shots if s.media == medium))
        )
        sound = (
            ' hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000"'
            if found.audio
            else ""
        )
        assets.append(
            f'    <asset id="{key}" name={quoteattr(found.path.stem)} start="0s" '
            f'duration="{extent}" hasVideo="1"{size}{sound}>\n'
            '      <media-rep kind="original-media" '
            f"src={quoteattr(_url(found.path))}/>\n"
            "    </asset>"
        )
    spine = []
    at = 0
    for shot in shots:
        length = _length(shot)
        found = media[shot.media]
        scale = None if shot.broll else fill_scale(found.width, found.height)
        inner = []
        if scale:
            inner.append(
                f'              <adjust-transform scale="{scale:.5f} {scale:.5f}"/>'
            )
        if not shot.sound and found.audio:
            inner.append('              <adjust-volume amount="-96dB"/>')
        head = (
            f'            <asset-clip ref="{keys[shot.media]}" '
            f'name={quoteattr(Path(shot.media).stem)} offset="{_time(at)}" '
            f'start="{_time(_frame(shot.start))}" duration="{_time(length)}" '
            'tcFormat="NDF"'
        )
        spine.append(
            head + ">\n" + "\n".join(inner) + "\n            </asset-clip>"
            if inner
            else head + "/>"
        )
        at += length
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
        '<fcpxml version="1.9">\n  <resources>\n'
        + "".join(
            f'    <format id="{fmt}" frameDuration="1/{TIMELINE_FPS}s" '
            f'width="{w}" height="{h}"/>\n'
            for (w, h), fmt in sizes.items()
        )
        + "\n".join(assets)
        + '\n  </resources>\n  <library>\n    <event name="Clipper">\n'
        f"      <project name={quoteattr(name)}>\n"
        f'        <sequence format="r1" duration="{_time(at)}" tcStart="0s" '
        'tcFormat="NDF" audioLayout="stereo" audioRate="48k">\n'
        "          <spine>\n"
        + "\n".join(spine)
        + "\n          </spine>\n        </sequence>\n      </project>\n"
        "    </event>\n  </library>\n</fcpxml>\n"
    )


def timeline_seconds(shots: Sequence[Shot]) -> float:
    return sum(_length(s) for s in shots) / TIMELINE_FPS


def import_steps(name: str, captions: bool) -> str:
    """What to do in Resolve with the hand-off folder."""
    steps = [
        "In DaVinci Resolve, open or make a project.",
        "File > Import > Timeline... (Ctrl+Shift+I), pick timeline.fcpxml.",
        'In "Load XML", tick "Automatically set project settings" and '
        '"Automatically import source clips into media pool", then OK.',
        f'Open the timeline "{name}" on the Edit page.',
    ]
    if captions:
        steps += [
            "File > Import > Subtitle..., pick captions.srt.",
            "Drag captions.srt from the Media Pool onto the timeline at 0:00.",
        ]
    steps.append(
        "To open a cut back up: press T (Trim mode), drag the clip's edge, "
        "then A to go back."
    )
    lines = [f"{n}. {step}" for n, step in enumerate(steps, 1)]
    return (
        "\n".join(lines)
        + "\n\nNot carried over: on-screen texts, zooms and crops, B-roll"
        " overlays, music and sound effects, and the captions' styling.\n"
    )
