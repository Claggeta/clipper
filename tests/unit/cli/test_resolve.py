import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.results.manifest_json import save_manifest
from clipper.cli.context import AppContext
from clipper.cli.main import app
from clipper.domain.models import (
    AnalysisResult,
    Combined,
    CombinedPart,
    ManifestClip,
    MediaInfo,
    RenderManifest,
    Segment,
    Transcript,
    Word,
)

# Constructed drafts folders and transcripts, not real runs.


class FakeProbe:
    def probe(self, media_path: Path) -> MediaInfo:
        return MediaInfo(
            duration_seconds=3600.0,
            has_video=True,
            width=1920,
            height=1080,
            has_audio=True,
        )


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp_path
        self.clips = tmp_path / "clips"
        self.videos = tmp_path / "videos"
        self.videos.mkdir()
        for name, value in [
            ("CLIPS_DIR", self.clips),
            ("VIDEOS_DIR", self.videos),
            ("BROLL_DIR", tmp_path / "broll"),
        ]:
            monkeypatch.setenv(f"CLIPPER_{name}", str(value))
        monkeypatch.setenv("COLUMNS", "200")

    def folder(self, name: str, *, video: bool = True, combined: bool = False) -> None:
        words = tuple(
            Word(start_seconds=60.0 + i, end_seconds=60.5 + i, text=f"{name}w{i}")
            for i in range(10)
        )
        analysis = self.tmp / f"{name}.json"
        analysis.write_text(
            AnalysisResult(
                source=f"{name}.mp4",
                transcript=Transcript(
                    segments=(
                        Segment(
                            start_seconds=60.0, end_seconds=70.0, text="x", words=words
                        ),
                    )
                ),
                candidates=(),
            ).model_dump_json(),
            encoding="utf-8",
        )
        if video:
            (self.videos / f"{name}.mp4").write_bytes(b"")
        clip = ManifestClip(
            rank=1,
            start_seconds=60.0,
            end_seconds=70.0,
            score=0.5,
            reasons=("r",),
            file=f"{name}_1.mp4",
            reviewed="saved",
            title=f"{name} title",
            cuts=((63.2, 64.8),),
        )
        save_manifest(
            RenderManifest(
                source=f"{name}.mp4",
                analysis=str(analysis),
                detector=None,
                vertical=True,
                captions=True,
                titles=True,
                clips=(clip,),
                combined=Combined(
                    parts=(CombinedPart(rank=1),),
                    file=f"{name}_combined.mp4",
                    duration_seconds=20.0,
                )
                if combined
                else None,
            ),
            self.clips / name,
        )

    def run(self, *args: str) -> Result:
        return CliRunner().invoke(
            app, list(args), obj=AppContext(probe=lambda: FakeProbe())
        )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


def test_clips_from_two_videos_become_one_timeline_with_subtitles(env: Env) -> None:
    env.folder("v1")
    env.folder("v2")
    result = env.run("resolve", "v1 title", "v2/v2_1.mp4")
    assert result.exit_code == 0, result.output
    assert "Timeline: 2 clip(s), 4 shot(s), 16.8 s" in result.output
    out = env.clips / "resolve" / "v1-title"
    root = ET.fromstring((out / "timeline.fcpxml").read_text(encoding="utf-8"))
    assert root.find("./library/event/project").get("name") == "v1 title"  # type: ignore[union-attr]
    assert len(root.findall("./resources/asset")) == 2
    assert len(root.findall(".//spine/asset-clip")) == 4
    srt = (out / "captions.srt").read_text(encoding="utf-8")
    # The cut drops v1's words 4 (at 64.0 s) and nothing else.
    assert "v1w3" in srt and "v1w4" not in srt and "v1w5" in srt
    assert "v2w0" in srt
    assert "captions.srt" in (out / "steps.txt").read_text(encoding="utf-8")


def test_no_captions_leaves_out_the_subtitles(env: Env) -> None:
    env.folder("v1")
    assert env.run("resolve", "v1 title").exit_code == 0
    result = env.run("resolve", "v1 title", "--no-captions", "--name", "v1 title")
    assert result.exit_code == 0, result.output
    out = env.clips / "resolve" / "v1-title"
    assert (out / "timeline.fcpxml").is_file()
    assert not (out / "captions.srt").exists()


@pytest.mark.parametrize(
    ("clip", "message"),
    [
        ("no such title", "No saved clip is titled"),
        ("v3 title", "no source video"),
        ("v1/v1_combined.mp4", "combined clip or summary"),
    ],
)
def test_resolve_refuses_what_it_cannot_hand_off(
    env: Env, clip: str, message: str
) -> None:
    env.folder("v1", combined=True)
    env.folder("v3", video=False)
    result = env.run("resolve", clip)
    assert result.exit_code == 1
    assert message in result.output
    assert not (env.clips / "resolve").exists()
