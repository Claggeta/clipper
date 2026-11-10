from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.rendering.fake import FakeClipRenderer
from clipper.adapters.transcription.fake import FakeTranscriber
from clipper.cli.context import AppContext, DetectorName
from clipper.cli.main import app
from clipper.core.config import Settings
from clipper.domain.errors import DownloadError, TranscriptionError
from clipper.domain.models import (
    ClipCandidate,
    ClipTitle,
    MediaInfo,
    RenderManifest,
    Transcript,
    VideoInfo,
    VideoSource,
)

# Everything here is a fake or constructed: nothing downloads, transcribes,
# renders or calls a model.
_URL = "https://www.youtube.com/watch?v=abcDEF123_-"
_CANDIDATES = [
    ClipCandidate(start_seconds=0.0, end_seconds=4.5, score=0.9, reasons=("a",)),
    ClipCandidate(start_seconds=4.5, end_seconds=9.0, score=0.5, reasons=("b",)),
]


class Recorder:
    """Records which stages ran, in order."""

    def __init__(self) -> None:
        self.stages: list[str] = []


class RecordingTranscriber(FakeTranscriber):
    def __init__(self, log: Recorder, error: str | None = None) -> None:
        super().__init__()
        self._log = log
        self._error = error

    def transcribe(self, media_path: Path) -> Transcript:
        self._log.stages.append(f"transcribe {media_path.name}")
        if self._error:
            raise TranscriptionError(self._error)
        return super().transcribe(media_path)


class StubDetector:
    def detect(self, transcript: Transcript) -> list[ClipCandidate]:
        return list(_CANDIDATES)


class FakeDownloader:
    def __init__(self, log: Recorder, error: str | None = None) -> None:
        self._log = log
        self._error = error

    def download(self, source: VideoSource, folder: Path) -> Path:
        self._log.stages.append(f"download {source.video_id}")
        if self._error:
            raise DownloadError(self._error)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{source.video_id}.mp4"
        path.write_bytes(b"fake video")
        return path


class FakeVideoInfo:
    def lookup(self, source: VideoSource) -> VideoInfo:
        return VideoInfo(
            platform=source.platform,
            video_id=source.video_id,
            title="A video",
            channel="A channel",
        )


class FakeProbe:
    def probe(self, media_path: Path) -> MediaInfo:
        return MediaInfo(duration_seconds=100.0, has_video=True)


class FakeTitleWriter:
    abouts: list[str | None] = []

    def write(
        self, text: str, video: VideoInfo | None = None, about: str | None = None
    ) -> ClipTitle:
        FakeTitleWriter.abouts.append(about)
        return ClipTitle(title=f"T {text[:10]}", hook=f"H {text[:10]}")


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp_path
        self.log = Recorder()
        self.renderer = FakeClipRenderer()
        self.detector_names: list[DetectorName] = []
        self.title_writers_built = 0
        self.transcribe_error: str | None = None
        self.download_error: str | None = None
        for name in ("output", "clips", "videos", "cache"):
            monkeypatch.setenv(f"CLIPPER_{name.upper()}_DIR", str(tmp_path / name))
        self.monkeypatch = monkeypatch
        self.video = tmp_path / "talk.mp4"
        self.video.write_bytes(b"not a real video")

    def context(self, real_title_writer: bool = False) -> AppContext:
        def detector(settings: Settings, name: DetectorName) -> StubDetector:
            self.detector_names.append(name)
            return StubDetector()

        def title_writer(settings: Settings) -> FakeTitleWriter:
            self.title_writers_built += 1
            self.log.stages.append("title writer")
            return FakeTitleWriter()

        context = AppContext(
            transcriber=lambda s: RecordingTranscriber(self.log, self.transcribe_error),
            detector=detector,
            renderer=lambda s: self.renderer,
            probe=FakeProbe,
            title_writer=title_writer,
            downloader=lambda s: FakeDownloader(self.log, self.download_error),
            video_info=lambda s: FakeVideoInfo(),
        )
        if real_title_writer:
            context = AppContext(
                transcriber=context.transcriber,
                detector=context.detector,
                renderer=context.renderer,
                probe=context.probe,
                downloader=context.downloader,
                video_info=context.video_info,
            )
        return context

    def run(self, *args: str, real_title_writer: bool = False) -> Result:
        return CliRunner().invoke(
            app, ["drafts", *args], obj=self.context(real_title_writer)
        )

    def manifest(self, stem: str) -> RenderManifest:
        raw = (self.tmp / "clips" / stem / "manifest.json").read_bytes()
        return RenderManifest.model_validate_json(raw)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


def test_a_file_becomes_vertical_captioned_titled_drafts(env: Env) -> None:
    result = env.run(str(env.video))
    assert result.exit_code == 0, result.output
    assert env.log.stages == ["title writer", "transcribe talk.mp4"]
    assert env.renderer.vertical == [True, True]
    assert all(c is not None for c in env.renderer.captions)
    manifest = env.manifest("talk")
    assert (manifest.vertical, manifest.captions, manifest.titles) == (
        True,
        True,
        True,
    )
    assert manifest.analysis == str(env.tmp / "output" / "talk.json")
    assert [c.title is not None for c in manifest.clips] == [True, True]
    assert (env.tmp / "output" / "talk.json").is_file()
    # The analysis table and its Saved line come before the clips.
    output = result.output
    assert output.index("Top clips: talk.mp4") < output.index("Rendered #1")
    assert output.index("Saved: ") < output.index("Rendered #1")
    assert output.rstrip().endswith(f"Drafts: {env.tmp / 'clips' / 'talk'}")


def test_a_url_is_fetched_then_analysed_by_its_id(env: Env) -> None:
    result = env.run(_URL)
    assert result.exit_code == 0, result.output
    assert env.log.stages == [
        "title writer",
        "download abcDEF123_-",
        "transcribe abcDEF123_-.mp4",
    ]
    assert "Saved: " + str(env.tmp / "videos" / "abcDEF123_-.mp4") in result.output
    manifest = env.manifest("abcDEF123_-")
    assert manifest.source == "abcDEF123_-.mp4"
    assert (env.tmp / "output" / "abcDEF123_-.json").is_file()


def test_an_already_fetched_video_is_reused(env: Env) -> None:
    videos = env.tmp / "videos"
    videos.mkdir()
    (videos / "abcDEF123_-.mp4").write_bytes(b"earlier")
    result = env.run(_URL)
    assert result.exit_code == 0, result.output
    assert "download abcDEF123_-" not in env.log.stages
    assert "Already fetched: " in result.output


def test_each_default_can_be_turned_off(env: Env) -> None:
    result = env.run(str(env.video), "--no-vertical", "--no-captions", "--no-titles")
    assert result.exit_code == 0, result.output
    assert env.title_writers_built == 0
    assert env.renderer.vertical == [False, False]
    assert env.renderer.captions == [None, None]
    manifest = env.manifest("talk")
    assert (manifest.vertical, manifest.captions, manifest.titles) == (
        False,
        False,
        False,
    )
    assert [c.title for c in manifest.clips] == [None, None]


def test_the_detector_option_is_passed_through(env: Env) -> None:
    env.monkeypatch.setenv("CLIPPER_LLM_MODEL", "some-model:tag")
    result = env.run(str(env.video), "--detector", "llm", "--no-titles")
    assert result.exit_code == 0, result.output
    assert env.detector_names == [DetectorName.LLM]
    assert env.manifest("talk").detector == "llm:some-model:tag"


def test_an_empty_model_fails_before_downloading(env: Env) -> None:
    env.monkeypatch.setenv("CLIPPER_LLM_MODEL", "")
    result = env.run(_URL, real_title_writer=True)
    assert result.exit_code == 1
    assert "Error: Set CLIPPER_LLM_MODEL" in result.output
    assert env.log.stages == []
    assert not (env.tmp / "output").exists()


def test_a_refused_url_runs_nothing(env: Env) -> None:
    result = env.run("https://www.youtube.com/playlist?list=PLxyz")
    assert result.exit_code == 1
    assert "Error: Playlist URLs are not fetched" in result.output
    assert env.log.stages == []


def test_a_missing_file_runs_nothing(env: Env) -> None:
    result = env.run(str(env.tmp / "nope.mp4"))
    assert result.exit_code == 1
    assert "Error: Input file not found" in result.output
    assert env.log.stages == []


def test_a_fetch_failure_stops_before_analysis(env: Env) -> None:
    env.download_error = "yt-dlp failed (exit 1): ERROR: unavailable"
    result = env.run(_URL)
    assert result.exit_code == 1
    assert "Error: yt-dlp failed (exit 1)" in result.output
    assert not any(s.startswith("transcribe") for s in env.log.stages)
    assert not (env.tmp / "output").exists()


def test_an_analysis_failure_renders_nothing(env: Env) -> None:
    env.transcribe_error = "Transcription failed: boom"
    result = env.run(str(env.video))
    assert result.exit_code == 1
    assert "Error: Transcription failed: boom" in result.output
    assert env.renderer.calls == []
    assert not (env.tmp / "output").exists()
    assert "Drafts:" not in result.output


def test_one_failed_clip_still_lists_the_folder_and_exits_1(env: Env) -> None:
    env.renderer = FakeClipRenderer(fail_on=[_CANDIDATES[1]])
    result = env.run(str(env.video))
    assert result.exit_code == 1
    assert "Failed: #2" in result.output
    assert "Drafts: " in result.output
    clips = env.manifest("talk").clips
    assert [c.error is None for c in clips] == [True, False]


def test_the_about_text_reaches_the_titles(env: Env) -> None:
    FakeTitleWriter.abouts = []
    result = env.run(str(env.video), "--about", "Greg LaVecchia")
    assert result.exit_code == 0, result.output
    assert FakeTitleWriter.abouts
    assert set(FakeTitleWriter.abouts) == {"Greg LaVecchia"}


def test_about_without_titles_fails_before_any_download(env: Env) -> None:
    result = env.run(_URL, "--no-titles", "--about", "Greg LaVecchia")
    assert result.exit_code == 1
    assert "Error: --about is for titles; use it with titles on" in result.output
    assert env.log.stages == []
