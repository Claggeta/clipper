import dataclasses
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.rendering.fake import FakeClipRenderer
from clipper.cli.context import AppContext
from clipper.cli.main import app
from clipper.domain.errors import ClipRenderError, MediaProbeError, TitleError
from clipper.domain.models import (
    AnalysisResult,
    ClipCandidate,
    ClipTitle,
    ManifestClip,
    MediaInfo,
    RenderManifest,
    Segment,
    Transcript,
    VideoInfo,
    Word,
)

# All fixtures here are constructed, not produced by a real analyze run.


def _c(start: float, end: float, score: float = 0.5) -> ClipCandidate:
    return ClipCandidate(
        start_seconds=start, end_seconds=end, score=score, reasons=("test",)
    )


CANDIDATES = (_c(10, 20, 0.9), _c(30, 45, 0.7), _c(60, 70, 0.5))


class FakeProbe:
    def __init__(self, info: MediaInfo | None = None, error: bool = False) -> None:
        self._info = info or MediaInfo(duration_seconds=100.0, has_video=True)
        self._error = error

    def probe(self, media_path: Path) -> MediaInfo:
        if self._error:
            raise MediaProbeError("probe failed")
        return self._info


class RaisingRenderer:
    def render(  # type: ignore[no-untyped-def]
        self,
        video_path: Path,
        candidate: ClipCandidate,
        output_path: Path,
        captions: str | None = None,
        *,
        vertical: bool = False,
    ):
        raise ClipRenderError("[aist#0:0/pcm_s16le] [/bogus] bad")


class FakeTitleWriter:
    """Titles each clip from its text; fails on texts listed in `fail_on`."""

    def __init__(self, fail_on: tuple[str, ...] = ()) -> None:
        self.texts: list[str] = []
        self.videos: list[VideoInfo | None] = []
        self._fail_on = fail_on

    def write(self, text: str, video: VideoInfo | None = None) -> ClipTitle:
        self.texts.append(text)
        self.videos.append(video)
        if text in self._fail_on:
            raise TitleError("Model reply is not a valid title: [bad]")
        return ClipTitle(title=f"Title: {text}", hook=f"Hook: {text}")


def _no_title_writer(settings: object) -> FakeTitleWriter:
    raise AssertionError("the title writer was built without --titles")


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.video = tmp_path / "talk.mp4"
        self.video.write_bytes(b"not a real video")
        self.json = tmp_path / "talk.json"
        self.clips = tmp_path / "clips"
        self.renderer = FakeClipRenderer()
        self.monkeypatch = monkeypatch
        self.context = AppContext()
        monkeypatch.setenv("CLIPPER_CLIPS_DIR", str(self.clips))
        self.save()
        self.use_probe(FakeProbe())
        self.use_renderer(self.renderer)
        self.context = dataclasses.replace(self.context, title_writer=_no_title_writer)

    def save(
        self,
        candidates: tuple[ClipCandidate, ...] = CANDIDATES,
        source: str = "talk.mp4",
        transcript: Transcript | None = None,
    ) -> None:
        result = AnalysisResult(
            source=source,
            transcript=transcript or Transcript(segments=()),
            candidates=candidates,
        )
        self.json.write_text(result.model_dump_json(), encoding="utf-8")

    def use_probe(self, probe: FakeProbe) -> None:
        self.context = dataclasses.replace(self.context, probe=lambda: probe)

    def use_renderer(self, renderer: object) -> None:
        self.renderer = renderer  # type: ignore[assignment]
        self.context = dataclasses.replace(
            self.context,
            renderer=lambda s: renderer,  # type: ignore[arg-type,return-value]
        )

    def use_title_writer(self, writer: FakeTitleWriter) -> None:
        self.context = dataclasses.replace(self.context, title_writer=lambda s: writer)

    def run(self, *extra: str) -> Result:
        args = ["render", str(self.video), str(self.json), *extra]
        return CliRunner().invoke(app, args, obj=self.context)

    @property
    def folder(self) -> Path:
        return self.clips / "talk"

    def written(self) -> list[str]:
        """The clips in the video's folder (ADR 0019), without the manifest."""
        return sorted(p.name for p in self.folder.glob("*.mp4"))

    def manifest(self) -> RenderManifest:
        raw = (self.folder / "manifest.json").read_bytes()
        return RenderManifest.model_validate_json(raw)

    def assert_failed_before_cutting(self, result: Result, text: str) -> None:
        assert result.exit_code == 1, result.output
        assert text in result.output
        assert list(self.clips.rglob("*")) == []
        assert not isinstance(self.renderer, FakeClipRenderer) or (
            self.renderer.calls == []
        )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


def test_renders_all_candidates(env: Env) -> None:
    result = env.run()
    assert result.exit_code == 0, result.output
    assert env.written() == [
        "talk_000010-000020.mp4",
        "talk_000030-000045.mp4",
        "talk_000100-000110.mp4",
    ]
    assert "Rendered 3 of 3 clips" in result.output
    assert "Rendered #1 0:00:10-0:00:20" in result.output


def test_rank_selects_sorted_and_deduped(env: Env) -> None:
    result = env.run("--rank", "3", "--rank", "1", "--rank", "3")
    assert result.exit_code == 0, result.output
    assert isinstance(env.renderer, FakeClipRenderer)
    assert [c.start_seconds for _, c, _ in env.renderer.calls] == [10, 60]
    assert "Rendered 2 of 2 clips" in result.output


@pytest.mark.parametrize("bad", ["0", "4", "-1"])
def test_rank_out_of_range(env: Env, bad: str) -> None:
    env.assert_failed_before_cutting(env.run("--rank", bad), "out of range")


def test_missing_json(env: Env) -> None:
    env.json.unlink()
    env.assert_failed_before_cutting(env.run(), "not found")


def test_invalid_json(env: Env) -> None:
    env.json.write_text("{nope", encoding="utf-8")
    env.assert_failed_before_cutting(env.run(), "Invalid JSON")


def test_source_mismatch(env: Env) -> None:
    env.save(source="other.mp4")
    env.assert_failed_before_cutting(env.run(), "other.mp4")


def test_empty_candidates(env: Env) -> None:
    env.save(candidates=())
    env.assert_failed_before_cutting(env.run(), "no candidates")


def test_probe_error(env: Env) -> None:
    env.use_probe(FakeProbe(error=True))
    env.assert_failed_before_cutting(env.run(), "probe failed")


def test_no_video_stream(env: Env) -> None:
    env.use_probe(FakeProbe(MediaInfo(duration_seconds=100.0, has_video=False)))
    env.assert_failed_before_cutting(env.run(), "no video stream")


def test_candidate_past_end_of_video(env: Env) -> None:
    env.use_probe(FakeProbe(MediaInfo(duration_seconds=65.0, has_video=True)))
    env.assert_failed_before_cutting(env.run(), "ends after the end of the video")


def test_unselected_candidate_past_end_does_not_block(env: Env) -> None:
    env.use_probe(FakeProbe(MediaInfo(duration_seconds=65.0, has_video=True)))
    result = env.run("--rank", "1", "--rank", "2")
    assert result.exit_code == 0, result.output
    assert env.written() == ["talk_000010-000020.mp4", "talk_000030-000045.mp4"]


def test_name_collision_fails_before_cutting(env: Env) -> None:
    env.save(candidates=(_c(10.1, 19.2), _c(10.4, 19.9)))
    env.assert_failed_before_cutting(env.run(), "both map to")


def test_unselected_collision_does_not_block(env: Env) -> None:
    env.save(candidates=(_c(10.1, 19.2), _c(10.4, 19.9), _c(60, 70)))
    result = env.run("--rank", "1", "--rank", "3")
    assert result.exit_code == 0, result.output
    assert env.written() == ["talk_000010-000020.mp4", "talk_000100-000110.mp4"]


def test_one_failure_renders_the_rest_and_exits_1(env: Env) -> None:
    env.use_renderer(FakeClipRenderer(fail_on=[CANDIDATES[1]]))
    result = env.run()
    assert result.exit_code == 1
    assert env.written() == ["talk_000010-000020.mp4", "talk_000100-000110.mp4"]
    assert "Failed: #2 0:00:30-0:00:45" in result.output
    assert "Rendered 2 of 3 clips" in result.output


def test_failure_message_with_brackets_is_not_parsed_as_markup(env: Env) -> None:
    env.use_renderer(RaisingRenderer())
    result = env.run("--rank", "1")
    assert result.exit_code == 1
    assert "[aist#0:0/pcm_s16le] [/bogus] bad" in result.output


# Constructed transcript: speech only inside the first and second candidates.
_SPEECH = Transcript(
    segments=(
        Segment(start_seconds=12.0, end_seconds=14.0, text="hello there"),
        Segment(start_seconds=31.0, end_seconds=33.0, text="second <i>one</i>"),
    )
)


def test_captions_are_off_by_default(env: Env) -> None:
    env.save(transcript=_SPEECH)
    result = env.run()
    assert result.exit_code == 0, result.output
    assert isinstance(env.renderer, FakeClipRenderer)
    assert env.renderer.captions == [None, None, None]
    assert "(captions)" not in result.output


def test_captions_pass_ass_for_each_clip_window(env: Env) -> None:
    env.save(transcript=_SPEECH)
    result = env.run("--captions", "--rank", "1", "--rank", "2")
    assert result.exit_code == 0, result.output
    assert isinstance(env.renderer, FakeClipRenderer)
    first, second = env.renderer.captions
    assert first is not None and second is not None
    assert first.startswith("[Script Info]\n")
    assert "PlayResX: 1920\n" in first
    assert first.endswith(
        "Dialogue: 0,0:00:02.00,0:00:04.00,Default,,0,0,0,,hello there\n"
    )
    assert second.endswith(",second ione/i\n")
    assert result.output.count("(captions)") == 2


def test_captions_from_segments_without_words_print_a_note(env: Env) -> None:
    env.save(transcript=_SPEECH)
    result = env.run("--captions", "--rank", "1")
    assert result.exit_code == 0, result.output
    assert "#1 0:00:10-0:00:20 has no word timings" in result.output
    assert "(captions)" in result.output


def test_captions_with_word_timings_print_no_note(env: Env) -> None:
    words = (
        Word(start_seconds=12.0, end_seconds=12.5, text="hello"),
        Word(start_seconds=12.6, end_seconds=13.0, text="there"),
    )
    segment = Segment(
        start_seconds=12.0, end_seconds=13.0, text="hello there", words=words
    )
    env.save(transcript=Transcript(segments=(segment,)))
    result = env.run("--captions", "--rank", "1")
    assert result.exit_code == 0, result.output
    assert isinstance(env.renderer, FakeClipRenderer)
    captions = env.renderer.captions[0]
    assert captions is not None
    assert "{\\c&H0000FFFF&}hello{\\r} there" in captions
    assert "Note:" not in result.output


def test_captions_with_no_speech_in_range_renders_plain_with_a_note(env: Env) -> None:
    env.save(transcript=_SPEECH)
    result = env.run("--captions")
    assert result.exit_code == 0, result.output
    assert isinstance(env.renderer, FakeClipRenderer)
    assert env.renderer.captions[2] is None
    assert "#3 0:01:00-0:01:10 has no speech in range" in result.output
    assert "Rendered 3 of 3 clips" in result.output
    assert len(env.written()) == 3


def test_captions_failure_renders_the_rest_and_exits_1(env: Env) -> None:
    env.save(transcript=_SPEECH)
    env.use_renderer(FakeClipRenderer(fail_on=[CANDIDATES[0]]))
    result = env.run("--captions")
    assert result.exit_code == 1
    assert "Rendered 2 of 3 clips" in result.output


def test_vertical_is_off_by_default(env: Env) -> None:
    result = env.run()
    assert result.exit_code == 0, result.output
    assert isinstance(env.renderer, FakeClipRenderer)
    assert env.renderer.vertical == [False, False, False]
    assert "(vertical" not in result.output


def test_vertical_is_passed_for_each_clip_and_shown(env: Env) -> None:
    result = env.run("--vertical", "--rank", "1", "--rank", "2")
    assert result.exit_code == 0, result.output
    assert isinstance(env.renderer, FakeClipRenderer)
    assert env.renderer.vertical == [True, True]
    assert result.output.count(" (vertical)") == 2
    assert env.written() == ["talk_000010-000020.mp4", "talk_000030-000045.mp4"]


def test_vertical_with_captions_shows_both(env: Env) -> None:
    env.save(transcript=_SPEECH)
    result = env.run("--vertical", "--captions")
    assert result.exit_code == 0, result.output
    assert result.output.count(" (vertical, captions)") == 2
    # Clip 3 has no speech: rendered vertical, without captions.
    assert "Rendered #3 0:01:00-0:01:10" in result.output
    assert result.output.count(" (vertical)") == 1
    assert isinstance(env.renderer, FakeClipRenderer)
    captions = env.renderer.captions[0]
    assert captions is not None
    assert "PlayResX: 1080\nPlayResY: 1920\n" in captions


def test_clips_go_to_a_folder_per_video_with_a_manifest(env: Env) -> None:
    result = env.run()
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in env.clips.iterdir()) == ["talk"]
    assert f"Rendered 3 of 3 clips to {env.folder}" in result.output
    assert f"Manifest: {env.folder / 'manifest.json'}" in result.output
    assert env.manifest() == RenderManifest(
        source="talk.mp4",
        analysis=str(env.json),
        detector=None,
        vertical=False,
        captions=False,
        titles=False,
        clips=(
            ManifestClip(
                rank=1,
                start_seconds=10,
                end_seconds=20,
                score=0.9,
                reasons=("test",),
                file="talk_000010-000020.mp4",
            ),
            ManifestClip(
                rank=2,
                start_seconds=30,
                end_seconds=45,
                score=0.7,
                reasons=("test",),
                file="talk_000030-000045.mp4",
            ),
            ManifestClip(
                rank=3,
                start_seconds=60,
                end_seconds=70,
                score=0.5,
                reasons=("test",),
                file="talk_000100-000110.mp4",
            ),
        ),
    )


def test_manifest_is_utf8_with_lf_line_endings(env: Env) -> None:
    env.run()
    raw = (env.folder / "manifest.json").read_bytes()
    assert b"\r" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.endswith(b"}\n")


def test_manifest_records_flags_detector_and_what_captions_were_burned(
    env: Env,
) -> None:
    word = Word(start_seconds=12.0, end_seconds=12.5, text="hello")
    transcript = Transcript(
        segments=(
            Segment(start_seconds=12.0, end_seconds=12.5, text="hello", words=(word,)),
            Segment(start_seconds=31.0, end_seconds=33.0, text="no words here"),
        )
    )
    result = AnalysisResult(
        source="talk.mp4",
        transcript=transcript,
        candidates=CANDIDATES,
        detector="llm:qwen3.5:9b",
    )
    env.json.write_text(result.model_dump_json(), encoding="utf-8")
    assert env.run("--captions", "--vertical").exit_code == 0
    manifest = env.manifest()
    assert (manifest.vertical, manifest.captions) == (True, True)
    assert manifest.detector == "llm:qwen3.5:9b"
    assert [c.captions for c in manifest.clips] == ["words", "segments", None]


def test_a_rank_run_replaces_the_manifest(env: Env) -> None:
    env.run()
    result = env.run("--rank", "2")
    assert result.exit_code == 0, result.output
    assert [c.rank for c in env.manifest().clips] == [2]
    # Clips from the earlier run stay in the folder (ADR 0019, decision 3).
    assert len(env.written()) == 3


def test_failed_clips_are_in_the_manifest_with_their_error(env: Env) -> None:
    env.use_renderer(FakeClipRenderer(fail_on=[CANDIDATES[1]]))
    result = env.run()
    assert result.exit_code == 1
    clips = env.manifest().clips
    assert [c.file for c in clips] == [
        "talk_000010-000020.mp4",
        None,
        "talk_000100-000110.mp4",
    ]
    assert clips[1].error == "Fake render failed for talk_000030-000045.mp4"


def test_manifest_keeps_the_raw_error_text(env: Env) -> None:
    env.use_renderer(RaisingRenderer())
    result = env.run("--rank", "1")
    assert result.exit_code == 1
    assert env.manifest().clips[0].error == "[aist#0:0/pcm_s16le] [/bogus] bad"
    assert "Manifest: " in result.output


def test_a_failure_before_cutting_leaves_the_old_manifest(env: Env) -> None:
    env.run()
    before = (env.folder / "manifest.json").read_bytes()
    result = env.run("--rank", "9")
    assert result.exit_code == 1
    assert "Manifest:" not in result.output
    assert (env.folder / "manifest.json").read_bytes() == before


def test_manifest_write_failure_is_an_error(env: Env) -> None:
    # Constructed: a folder where the manifest file goes.
    (env.folder / "manifest.json").mkdir(parents=True)
    result = env.run("--rank", "1")
    assert result.exit_code == 1
    assert "Error: Cannot write manifest" in result.output
    assert "Rendered 1 of 1 clips" in result.output
    assert env.written() == ["talk_000010-000020.mp4"]


def test_titles_are_off_by_default(env: Env) -> None:
    # The default env's title builder fails the test if it is called.
    env.save(transcript=_SPEECH)
    result = env.run()
    assert result.exit_code == 0, result.output
    manifest = env.manifest()
    assert manifest.titles is False
    assert [c.title for c in manifest.clips] == [None, None, None]
    assert "Title:" not in result.output


def test_titles_are_written_from_each_clips_text_to_the_manifest(env: Env) -> None:
    env.save(transcript=_SPEECH)
    writer = FakeTitleWriter()
    env.use_title_writer(writer)
    result = env.run("--titles", "--rank", "1", "--rank", "2")
    assert result.exit_code == 0, result.output
    # Markup is removed from the text, as in captions.
    assert writer.texts == ["hello there", "second ione/i"]
    manifest = env.manifest()
    assert manifest.titles is True
    assert [(c.title, c.hook) for c in manifest.clips] == [
        ("Title: hello there", "Hook: hello there"),
        ("Title: second ione/i", "Hook: second ione/i"),
    ]
    assert result.output.count(" (titles)") == 2
    assert "  Title: Title: hello there\n  Hook: Hook: hello there\n" in result.output


def test_titles_with_other_flags_are_shown_together(env: Env) -> None:
    env.save(transcript=_SPEECH)
    env.use_title_writer(FakeTitleWriter())
    result = env.run("--titles", "--vertical", "--captions", "--rank", "1")
    assert result.exit_code == 0, result.output
    assert " (vertical, captions, titles)" in result.output


def test_title_text_is_not_parsed_as_markup(env: Env) -> None:
    class Bracketed:
        def write(self, text: str, video: VideoInfo | None = None) -> ClipTitle:
            return ClipTitle(title="[bold]x[/bold]", hook="[red]y")

    env.save(transcript=_SPEECH)
    env.context = dataclasses.replace(env.context, title_writer=lambda s: Bracketed())
    result = env.run("--titles", "--rank", "1")
    assert result.exit_code == 0, result.output
    assert "Title: [bold]x[/bold]" in result.output
    assert "Hook: [red]y" in result.output


def test_a_clip_with_no_speech_gets_a_note_and_no_title(env: Env) -> None:
    env.save(transcript=_SPEECH)
    writer = FakeTitleWriter()
    env.use_title_writer(writer)
    result = env.run("--titles")
    assert result.exit_code == 0, result.output
    assert "#3 0:01:00-0:01:10 has no speech in range; no title" in result.output
    assert len(writer.texts) == 2
    clip = env.manifest().clips[2]
    assert (clip.file, clip.title, clip.hook) == ("talk_000100-000110.mp4", None, None)
    assert "Rendered 3 of 3 clips" in result.output


def test_a_title_failure_fails_that_clip_before_it_is_cut(env: Env) -> None:
    env.save(transcript=_SPEECH)
    env.use_title_writer(FakeTitleWriter(fail_on=("hello there",)))
    result = env.run("--titles")
    assert result.exit_code == 1
    assert isinstance(env.renderer, FakeClipRenderer)
    assert [c for _, c, _ in env.renderer.calls] == [CANDIDATES[1], CANDIDATES[2]]
    assert env.written() == ["talk_000030-000045.mp4", "talk_000100-000110.mp4"]
    assert (
        "Failed: #1 0:00:10-0:00:20: Model reply is not a valid title: [bad]"
        in result.output
    )
    assert "Rendered 2 of 3 clips" in result.output
    clip = env.manifest().clips[0]
    assert (clip.file, clip.title, clip.hook) == (None, None, None)
    assert clip.error == "Model reply is not a valid title: [bad]"


def test_a_render_failure_after_its_title_records_no_title(env: Env) -> None:
    env.save(transcript=_SPEECH)
    env.use_title_writer(FakeTitleWriter())
    env.use_renderer(FakeClipRenderer(fail_on=[CANDIDATES[0]]))
    result = env.run("--titles", "--rank", "1")
    assert result.exit_code == 1
    clip = env.manifest().clips[0]
    assert (clip.title, clip.hook) == (None, None)
    assert clip.error == "Fake render failed for talk_000010-000020.mp4"


def test_titles_with_an_empty_model_fail_before_cutting(env: Env) -> None:
    env.run()
    before = (env.folder / "manifest.json").read_bytes()
    env.monkeypatch.setenv("CLIPPER_LLM_MODEL", "")
    # The real builder, so its empty-model check runs.
    env.context = dataclasses.replace(
        env.context, title_writer=AppContext().title_writer
    )
    env.use_renderer(FakeClipRenderer())
    result = env.run("--titles")
    assert result.exit_code == 1
    assert "Error: Set CLIPPER_LLM_MODEL" in result.output
    assert isinstance(env.renderer, FakeClipRenderer)
    assert env.renderer.calls == []
    assert (env.folder / "manifest.json").read_bytes() == before


_INFO = VideoInfo(
    platform="twitch", video_id="v1", title="Stream title", channel="streamer"
)


def test_titles_get_the_video_info_beside_the_video(env: Env) -> None:
    env.save(transcript=_SPEECH)
    (env.video.parent / "talk.info.json").write_text(
        _INFO.model_dump_json(), encoding="utf-8"
    )
    writer = FakeTitleWriter()
    env.use_title_writer(writer)
    result = env.run("--titles", "--rank", "1", "--rank", "2")
    assert result.exit_code == 0, result.output
    assert writer.videos == [_INFO, _INFO]
    assert "Title context: streamer" in result.output


def test_titles_without_video_info_get_none(env: Env) -> None:
    env.save(transcript=_SPEECH)
    writer = FakeTitleWriter()
    env.use_title_writer(writer)
    result = env.run("--titles", "--rank", "1")
    assert result.exit_code == 0, result.output
    assert writer.videos == [None]
    assert "Title context" not in result.output


def test_a_bad_video_info_file_is_a_warning(env: Env) -> None:
    env.save(transcript=_SPEECH)
    (env.video.parent / "talk.info.json").write_text("{", encoding="utf-8")
    writer = FakeTitleWriter()
    env.use_title_writer(writer)
    result = env.run("--titles", "--rank", "1")
    assert result.exit_code == 0, result.output
    assert writer.videos == [None]
    assert "Warning: titles without video info: Cannot read video info" in result.output
    assert env.manifest().clips[0].title == "Title: hello there"
