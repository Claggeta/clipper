from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.rendering.fake import FakeClipRenderer
from clipper.cli import main as cli_main
from clipper.cli.main import app
from clipper.domain.errors import ClipRenderError, MediaProbeError
from clipper.domain.models import (
    AnalysisResult,
    ClipCandidate,
    MediaInfo,
    Segment,
    Transcript,
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
    ):
        raise ClipRenderError("[aist#0:0/pcm_s16le] [/bogus] bad")


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.video = tmp_path / "talk.mp4"
        self.video.write_bytes(b"not a real video")
        self.json = tmp_path / "talk.json"
        self.clips = tmp_path / "clips"
        self.renderer = FakeClipRenderer()
        self.monkeypatch = monkeypatch
        monkeypatch.setenv("CLIPPER_CLIPS_DIR", str(self.clips))
        self.save()
        self.use_probe(FakeProbe())
        self.use_renderer(self.renderer)

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
        self.monkeypatch.setattr(cli_main, "_build_probe", lambda: probe)

    def use_renderer(self, renderer: object) -> None:
        self.renderer = renderer  # type: ignore[assignment]
        self.monkeypatch.setattr(cli_main, "_build_renderer", lambda s: renderer)

    def run(self, *extra: str) -> Result:
        args = ["render", str(self.video), str(self.json), *extra]
        return CliRunner().invoke(app, args)

    def written(self) -> list[str]:
        if not self.clips.exists():
            return []
        return sorted(p.name for p in self.clips.glob("*"))

    def assert_failed_before_cutting(self, result: Result, text: str) -> None:
        assert result.exit_code == 1, result.output
        assert text in result.output
        assert self.written() == []
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


def test_captions_pass_srt_for_each_clip_window(env: Env) -> None:
    env.save(transcript=_SPEECH)
    result = env.run("--captions", "--rank", "1", "--rank", "2")
    assert result.exit_code == 0, result.output
    assert isinstance(env.renderer, FakeClipRenderer)
    assert env.renderer.captions == [
        "1\n00:00:02,000 --> 00:00:04,000\nhello there\n",
        "1\n00:00:01,000 --> 00:00:03,000\nsecond ione/i\n",
    ]
    assert result.output.count("(captions)") == 2


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
