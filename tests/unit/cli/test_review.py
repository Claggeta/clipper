from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.results.manifest_json import save_manifest
from clipper.cli.context import AppContext
from clipper.cli.main import app
from clipper.domain.errors import MediaProbeError, ReviewError
from clipper.domain.models import ManifestClip, MediaInfo, RenderManifest
from clipper.review.server import DEFAULT_PORT, ReviewSession

# All fixtures here are constructed, not produced by a real render run.

MANIFEST = RenderManifest(
    source="talk.mp4",
    analysis="data/output/talk.json",
    detector="heuristic",
    vertical=True,
    captions=True,
    titles=True,
    clips=(
        ManifestClip(
            rank=1,
            start_seconds=60.0,
            end_seconds=105.0,
            score=0.5,
            reasons=("test",),
            file="talk_000100-000145.mp4",
        ),
    ),
)


class FakeProbe:
    def __init__(self, info: MediaInfo | None = None, error: bool = False) -> None:
        self._info = info or MediaInfo(duration_seconds=600.0, has_video=True)
        self._error = error
        self.seen: list[Path] = []

    def probe(self, media_path: Path) -> MediaInfo:
        self.seen.append(media_path)
        if self._error:
            raise MediaProbeError("probe failed")
        return self._info


class FakeFrames:
    def extract(self, video_path: Path, seconds: float, output_path: Path) -> Path:
        raise AssertionError("review must not take frames before serving")


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.folder = tmp_path / "clips" / "talk"
        self.videos = tmp_path / "videos"
        self.video = self.videos / "talk.mp4"
        monkeypatch.setenv("CLIPPER_VIDEOS_DIR", str(self.videos))
        save_manifest(MANIFEST, self.folder)
        self.videos.mkdir()
        self.video.write_bytes(b"video")
        self.cache = tmp_path / "cache"
        monkeypatch.setenv("CLIPPER_CACHE_DIR", str(self.cache))
        self.probe = FakeProbe()
        self.frames = FakeFrames()
        self.calls: list[tuple[ReviewSession, int, bool]] = []
        self.error: ReviewError | None = None

    def serve(
        self,
        session: ReviewSession,
        port: int,
        open_browser: bool,
        announce: Callable[[str], None],
    ) -> None:
        self.calls.append((session, port, open_browser))
        if self.error:
            raise self.error
        announce(f"http://127.0.0.1:{port}/")

    def run(self, *extra: str) -> Result:
        context = AppContext(
            probe=lambda: self.probe, frames=lambda: self.frames, serve=self.serve
        )
        return CliRunner().invoke(
            app, ["review", str(self.folder), *extra], obj=context
        )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


def test_serves_the_folder_with_the_video_from_the_videos_dir(env: Env) -> None:
    result = env.run()

    assert result.exit_code == 0, result.output
    [(session, port, open_browser)] = env.calls
    assert session.folder == env.folder
    assert session.manifest == MANIFEST
    assert session.video == env.video
    assert session.duration_seconds == 600.0
    assert session.frames is env.frames
    # Named by the video's stem and its size, 5 bytes.
    assert session.frames_dir == env.cache / "frames" / "talk-5"
    assert (port, open_browser) == (DEFAULT_PORT, True)
    assert env.probe.seen == [env.video]
    assert f"Review: http://127.0.0.1:{DEFAULT_PORT}/ (1 clip; Ctrl+C to stop)" in (
        result.output
    )


def test_port_and_browser_can_be_set(env: Env) -> None:
    result = env.run("--port", "9000", "--no-open")

    assert result.exit_code == 0, result.output
    assert [(port, open_browser) for _, port, open_browser in env.calls] == [
        (9000, False)
    ]


def test_video_option_overrides_the_videos_dir(env: Env, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere" / "talk.mp4"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"video")
    env.video.unlink()

    result = env.run("--video", str(elsewhere))

    assert result.exit_code == 0, result.output
    assert env.calls[0][0].video == elsewhere
    assert env.probe.seen == [elsewhere]


def test_missing_manifest(env: Env) -> None:
    (env.folder / "manifest.json").unlink()

    result = env.run()

    assert result.exit_code == 1
    assert "Error: Manifest not found" in result.output
    assert env.calls == []


def test_missing_video_names_the_option(env: Env) -> None:
    env.video.unlink()

    result = env.run()

    assert result.exit_code == 1
    assert "Error: Video not found:" in result.output
    assert "pass --video" in result.output
    assert env.calls == []


def test_video_for_another_source(env: Env, tmp_path: Path) -> None:
    other = tmp_path / "other.mp4"
    other.write_bytes(b"video")

    result = env.run("--video", str(other))

    assert result.exit_code == 1
    assert "Manifest is for 'talk.mp4' but the video is 'other.mp4'" in result.output


def test_probe_failure(env: Env) -> None:
    env.probe = FakeProbe(error=True)

    result = env.run()

    assert result.exit_code == 1
    assert "Error: probe failed" in result.output
    assert env.calls == []


def test_video_without_a_picture(env: Env) -> None:
    env.probe = FakeProbe(MediaInfo(duration_seconds=600.0, has_video=False))

    result = env.run()

    assert result.exit_code == 1
    assert "has no video stream to show" in result.output


def test_server_error_is_reported(env: Env) -> None:
    env.error = ReviewError("Cannot listen on 127.0.0.1:8765: in use")

    result = env.run()

    assert result.exit_code == 1
    assert "Error: Cannot listen on 127.0.0.1:8765: in use" in result.output
