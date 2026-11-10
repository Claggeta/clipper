from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.rendering.fake import FakeClipRenderer
from clipper.adapters.transcription.fake import FakeTranscriber
from clipper.cli.context import AppContext, DetectorName
from clipper.cli.main import app
from clipper.core.config import Settings
from clipper.domain.errors import (
    ClipRenderError,
    DownloadError,
    FaceError,
    TranscriptionError,
)
from clipper.domain.models import (
    ClipCandidate,
    ClipTitle,
    CropPath,
    FaceBox,
    FaceSample,
    FaceTrack,
    MediaInfo,
    RenderedClip,
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
    """Downloads every video, or fails for `fail_on` IDs or with `error`."""

    def __init__(
        self, log: Recorder, error: str | None = None, fail_on: tuple[str, ...] = ()
    ) -> None:
        self._log = log
        self._error = error
        self._fail_on = fail_on

    def download(self, source: VideoSource, folder: Path) -> Path:
        self._log.stages.append(f"download {source.video_id}")
        if self._error or source.video_id in self._fail_on:
            raise DownloadError(self._error or f"{source.video_id} is unavailable")
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
        return MediaInfo(
            duration_seconds=100.0, has_video=True, width=1920, height=1080
        )


class FakeFaceTracker:
    """A face at the frame's centre in every span; `faces=False` finds none."""

    def __init__(self, faces: bool = True, fail: bool = False) -> None:
        self._faces = faces
        self._fail = fail

    def track(
        self, video_path: Path, start_seconds: float, end_seconds: float
    ) -> FaceTrack:
        if self._fail:
            raise FaceError("ffmpeg failed (exit 1): [bad] frames")
        face = FaceBox(x=0.45, y=0.2, w=0.1, h=0.3, score=0.9)
        faces = (face,) if self._faces else ()
        return FaceTrack(
            source=video_path.name,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            samples_per_second=5.0,
            samples=(FaceSample(seconds=start_seconds, faces=faces),),
        )


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
        self.top_ns: list[int] = []
        self.title_writers_built = 0
        self.transcribe_error: str | None = None
        self.download_error: str | None = None
        self.download_fail_on: tuple[str, ...] = ()
        self.tracker = FakeFaceTracker()
        for name in ("output", "clips", "videos", "cache"):
            monkeypatch.setenv(f"CLIPPER_{name.upper()}_DIR", str(tmp_path / name))
        self.monkeypatch = monkeypatch
        self.video = tmp_path / "talk.mp4"
        self.video.write_bytes(b"not a real video")

    def context(self, real_title_writer: bool = False) -> AppContext:
        def detector(settings: Settings, name: DetectorName) -> StubDetector:
            self.detector_names.append(name)
            self.top_ns.append(settings.top_n)
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
            downloader=lambda s: FakeDownloader(
                self.log, self.download_error, self.download_fail_on
            ),
            video_info=lambda s: FakeVideoInfo(),
            face_tracker=lambda s: self.tracker,
        )
        if real_title_writer:
            context = AppContext(
                transcriber=context.transcriber,
                detector=context.detector,
                renderer=context.renderer,
                probe=context.probe,
                downloader=context.downloader,
                video_info=context.video_info,
                face_tracker=context.face_tracker,
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
    # Each clip in both styles by default: fit, then face (ADR 0025).
    assert env.renderer.vertical == [True, True, True, True]
    assert [c is not None for c in env.renderer.crops] == [False, True, False, True]
    assert all(c is not None for c in env.renderer.captions)
    manifest = env.manifest("talk")
    assert manifest.styles == ("fit", "face")
    first = manifest.clips[0]
    assert [(v.style, v.file) for v in first.variants] == [
        ("fit", first.file),
        ("face", first.file.removesuffix(".mp4") + ".face.mp4"),  # type: ignore[union-attr]
    ]
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


def test_the_llm_picks_the_top_10_by_default(env: Env) -> None:
    # CLIPPER_TOP_N is for analyze and eval (ADR 0014, item 11 amendment).
    env.monkeypatch.setenv("CLIPPER_TOP_N", "5")
    env.monkeypatch.setenv("CLIPPER_LLM_MODEL", "some-model:tag")
    result = env.run(str(env.video), "--no-titles")
    assert result.exit_code == 0, result.output
    assert env.detector_names == [DetectorName.LLM]
    assert env.top_ns == [10]
    assert env.manifest("talk").detector == "llm:some-model:tag"


def test_the_detector_and_top_options_are_passed_through(env: Env) -> None:
    result = env.run(str(env.video), "--detector", "heuristic", "--top", "3")
    assert result.exit_code == 0, result.output
    assert env.detector_names == [DetectorName.HEURISTIC]
    assert env.top_ns == [3]
    assert env.manifest("talk").detector == "heuristic"


def test_top_must_be_at_least_1(env: Env) -> None:
    result = env.run(str(env.video), "--top", "0")
    assert result.exit_code == 2
    assert env.log.stages == []


def test_an_empty_model_fails_before_downloading(env: Env) -> None:
    env.monkeypatch.setenv("CLIPPER_LLM_MODEL", "")
    result = env.run(_URL, real_title_writer=True)
    assert result.exit_code == 1
    assert "Error: Set CLIPPER_LLM_MODEL" in result.output
    assert env.log.stages == []
    assert not (env.tmp / "output").exists()


def test_an_empty_model_fails_before_downloading_without_titles(env: Env) -> None:
    # The LLM is the default detector, so it needs the model with titles off.
    env.monkeypatch.setenv("CLIPPER_LLM_MODEL", "")
    result = env.run(_URL, "--no-titles")
    assert result.exit_code == 1
    assert "Error: Set CLIPPER_LLM_MODEL to an Ollama model name to use " in (
        result.output
    )
    assert env.log.stages == []
    assert not (env.tmp / "output").exists()


def test_the_heuristic_needs_no_model(env: Env) -> None:
    env.monkeypatch.setenv("CLIPPER_LLM_MODEL", "")
    result = env.run(str(env.video), "--no-titles", "--detector", "heuristic")
    assert result.exit_code == 0, result.output


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


def test_one_style_can_be_chosen(env: Env) -> None:
    result = env.run(str(env.video), "--style", "fit")
    assert result.exit_code == 0, result.output
    assert env.renderer.crops == [None, None]
    manifest = env.manifest("talk")
    assert manifest.styles == ("fit",)
    assert [len(c.variants) for c in manifest.clips] == [1, 1]


def test_no_vertical_renders_landscape_without_styles(env: Env) -> None:
    result = env.run(str(env.video), "--no-vertical")
    assert result.exit_code == 0, result.output
    assert env.renderer.vertical == [False, False]
    manifest = env.manifest("talk")
    assert manifest.styles == ()
    assert all(c.variants == () and c.file for c in manifest.clips)


def test_a_style_without_vertical_fails_before_any_download(env: Env) -> None:
    result = env.run(_URL, "--no-vertical", "--style", "face")
    assert result.exit_code == 1
    assert "Error: --style needs --vertical" in result.output
    assert env.log.stages == []


class FaceFailingRenderer(FakeClipRenderer):
    """Renders `fit` but fails every face crop."""

    def render(
        self,
        video_path: Path,
        candidate: ClipCandidate,
        output_path: Path,
        captions: str | None = None,
        *,
        vertical: bool = False,
        crop: CropPath | None = None,
    ) -> RenderedClip:
        if crop is not None:
            raise ClipRenderError("[face] crop failed")
        return super().render(
            video_path, candidate, output_path, captions, vertical=vertical
        )


def test_a_clip_with_no_face_still_renders_in_fit(env: Env) -> None:
    env.tracker = FakeFaceTracker(faces=False)
    result = env.run(str(env.video))
    assert result.exit_code == 0, result.output
    assert "has no face in range; not rendered in the face style" in result.output
    manifest = env.manifest("talk")
    assert [[v.style for v in c.variants] for c in manifest.clips] == [["fit"]] * 2


def test_a_face_tracking_failure_keeps_fit_and_exits_1(env: Env) -> None:
    env.tracker = FakeFaceTracker(fail=True)
    result = env.run(str(env.video))
    assert result.exit_code == 1
    assert "(face): ffmpeg failed (exit 1): [bad] frames" in result.output
    manifest = env.manifest("talk")
    assert all(c.file and c.error is None for c in manifest.clips)


def test_a_face_render_failure_keeps_the_fit_file_and_exits_1(env: Env) -> None:
    env.renderer = FaceFailingRenderer()
    result = env.run(str(env.video))
    assert result.exit_code == 1
    assert "(face): [face] crop failed" in result.output
    clip = env.manifest("talk").clips[0]
    assert [v.style for v in clip.variants] == ["fit"]
    assert clip.file == clip.variants[0].file


def test_a_fit_render_failure_skips_the_face_style(env: Env) -> None:
    env.renderer = FakeClipRenderer(fail_on=[_CANDIDATES[0]])
    result = env.run(str(env.video))
    assert result.exit_code == 1
    assert len(env.renderer.calls) == 3
    first = env.manifest("talk").clips[0]
    assert (first.file, first.variants) == (None, ())
    assert first.error is not None


_URL2 = "https://www.youtube.com/watch?v=zzzZZZ999_-"


def test_several_sources_run_in_order_with_a_summary(env: Env) -> None:
    FakeTitleWriter.abouts = []
    result = env.run(_URL, str(env.video), "--about", "Ron")
    assert result.exit_code == 0, result.output
    assert env.log.stages == [
        "title writer",
        "download abcDEF123_-",
        "transcribe abcDEF123_-.mp4",
        "transcribe talk.mp4",
    ]
    assert env.title_writers_built == 1
    assert "Source 1 of 2: " + _URL in result.output
    assert "Source 2 of 2: " + str(env.video) in result.output
    assert set(FakeTitleWriter.abouts) == {"Ron"}
    summary = result.output[result.output.index("Folder or error") :]
    assert summary.count("2 of 2") == 2
    assert env.manifest("abcDEF123_-").clips
    assert env.manifest("talk").clips


def test_a_failing_source_does_not_stop_the_rest(env: Env) -> None:
    env.download_fail_on = ("abcDEF123_-",)
    result = env.run(_URL, _URL2)
    assert result.exit_code == 1
    assert "Error: abcDEF123_- is unavailable" in result.output
    assert env.log.stages[-1] == "transcribe zzzZZZ999_-.mp4"
    assert env.manifest("zzzZZZ999_-").clips


def test_a_list_file_gives_sources_and_their_own_about(
    env: Env, tmp_path: Path
) -> None:
    FakeTitleWriter.abouts = []
    listing = tmp_path / "sources.txt"
    listing.write_text(
        f"# queue\n{_URL} | Lacy (he/him)\n{env.video}\n{_URL}\n", encoding="utf-8"
    )
    result = env.run("--list", str(listing), "--about", "Ron")
    assert result.exit_code == 0, result.output
    # The repeated URL runs once.
    assert env.log.stages.count("download abcDEF123_-") == 1
    assert "Source 2 of 2" in result.output
    assert FakeTitleWriter.abouts == ["Lacy (he/him)"] * 2 + ["Ron"] * 2


def test_every_bad_source_is_named_before_anything_runs(
    env: Env, tmp_path: Path
) -> None:
    listing = tmp_path / "sources.txt"
    listing.write_text(
        f"{_URL}\nhttps://www.youtube.com/playlist?list=PLx\nmissing.mp4\n",
        encoding="utf-8",
    )
    result = env.run("--list", str(listing))
    assert result.exit_code == 1
    assert "Error: Line 2: Playlist URLs are not fetched" in result.output
    assert "Error: Line 3: Input file not found: missing.mp4" in result.output
    assert env.log.stages == []


def test_a_bad_source_on_the_command_line_is_named(env: Env) -> None:
    result = env.run(_URL, "missing.mp4")
    assert result.exit_code == 1
    assert "Error: missing.mp4: Input file not found: missing.mp4" in result.output
    assert env.log.stages == []


def test_drafts_needs_a_source(env: Env, tmp_path: Path) -> None:
    result = env.run()
    assert result.exit_code == 1
    assert "Error: Give a source, or a --list file with sources" in result.output
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n", encoding="utf-8")
    assert "Give a source" in env.run("--list", str(empty)).output


def test_an_unreadable_list_is_an_error(env: Env, tmp_path: Path) -> None:
    result = env.run("--list", str(tmp_path / "none.txt"))
    assert result.exit_code == 1
    assert "Error: Cannot read the list" in result.output


def test_a_manifest_that_cannot_be_written_fails_that_source(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clipper.domain.errors import ManifestError

    calls: list[Path] = []

    def save(manifest: RenderManifest, folder: Path) -> Path:
        calls.append(folder)
        raise ManifestError(f"Cannot write manifest {folder}")

    monkeypatch.setattr("clipper.cli.main.save_manifest", save)
    result = env.run(_URL, _URL2)
    assert result.exit_code == 1
    assert len(calls) == 2
    assert "manifest not written" in result.output


def test_without_titles_a_lists_about_text_is_set_aside(
    env: Env, tmp_path: Path
) -> None:
    listing = tmp_path / "sources.txt"
    listing.write_text(f"{env.video} | Lacy (he/him)\n", encoding="utf-8")
    result = env.run("--list", str(listing), "--no-titles")
    assert result.exit_code == 0, result.output
    assert "Note: titles are off; the list's about text is not used" in result.output
    assert env.title_writers_built == 0
