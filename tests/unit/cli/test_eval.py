import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.transcription.fake import FakeTranscriber
from clipper.cli.context import AppContext, DetectorName
from clipper.cli.main import app
from clipper.core.config import Settings
from clipper.domain.errors import DetectionError
from clipper.domain.models import (
    AnalysisResult,
    ClipCandidate,
    Segment,
    Transcript,
)

# All fixtures here are constructed, not produced by a real analyze run or
# written by the maintainer.


def _c(start: float, end: float, score: float = 0.5) -> ClipCandidate:
    return ClipCandidate(
        start_seconds=start, end_seconds=end, score=score, reasons=("test",)
    )


def _transcript(end: float) -> Transcript:
    return Transcript(
        segments=(Segment(start_seconds=0.0, end_seconds=end, text="words"),)
    )


TRANSCRIPT = _transcript(300.0)
CANDIDATES = [_c(10, 55, 0.9), _c(100, 145, 0.7), _c(200, 245, 0.5)]
LABELS = [
    {"start": "0:00", "end": "1:00", "note": "the opener"},
    {"start": "1:00", "end": "1:30"},
    {"start": "4:00", "end": "4:30", "note": "[bold]not markup[/bold]"},
]


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.json = tmp_path / "output" / "talk.json"
        self.labels_dir = tmp_path / "labels"
        self.labels = self.labels_dir / "talk.json"
        self.monkeypatch = monkeypatch
        monkeypatch.setenv("CLIPPER_LABELS_DIR", str(self.labels_dir))
        self.save()
        self.write_labels()
        self.detect(CANDIDATES)

    def save(
        self, source: str = "talk.mp4", transcript: Transcript = TRANSCRIPT
    ) -> None:
        result = AnalysisResult(source=source, transcript=transcript, candidates=())
        self.json.parent.mkdir(parents=True, exist_ok=True)
        self.json.write_text(result.model_dump_json(), encoding="utf-8")

    def write_labels(
        self,
        labels: list[dict[str, str]] = LABELS,
        source: str = "talk.mp4",
        path: Path | None = None,
    ) -> Path:
        path = path or self.labels
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"source": source, "labels": labels}), encoding="utf-8"
        )
        return path

    def detect(self, candidates: list[ClipCandidate]) -> None:
        self.seen: list[Transcript] = []
        self.names: list[DetectorName] = []
        env = self

        class StubDetector:
            def detect(self, transcript: Transcript) -> list[ClipCandidate]:
                env.seen.append(transcript)
                return candidates

        def build(settings: Settings, name: DetectorName) -> StubDetector:
            self.names.append(name)
            return StubDetector()

        self.context = AppContext(detector=build)

    def run(self, *extra: str) -> Result:
        return CliRunner().invoke(
            app, ["eval", str(self.json), *extra], obj=self.context
        )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


def test_reports_counts_and_ratios(env: Env) -> None:
    result = env.run()

    assert result.exit_code == 0, result.output
    # Candidate 1 (10-55) hits label 1 (0-60). Candidate 2 (100-145) does not
    # touch label 2 (60-90). Candidate 3 (200-245) overlaps label 3 (240-270)
    # by 5 s, under half of 30 s.
    assert "Detector: heuristic (top 5)" in result.output
    assert "1 video, 3 labels, 3 candidates" in result.output
    assert "Recall: 1 of 3 labels (33%)" in result.output
    assert "Precision: 1 of 3 candidates (33%)" in result.output
    assert "Indicative only: one labeler, 1 video, 3 labels (ADR 0012)." in (
        result.output
    )


def test_tables_show_labels_and_notes(env: Env) -> None:
    result = env.run()

    assert "Labels: talk.mp4" in result.output
    assert "Candidates" in result.output
    assert "#1" in result.output
    assert "the opener" in result.output
    assert "[bold]not markup[/bold]" in result.output


def test_runs_the_detector_on_the_saved_transcript(env: Env) -> None:
    env.run()
    assert env.seen == [TRANSCRIPT]


def test_uses_the_heuristic_by_default(env: Env) -> None:
    env.run()
    assert env.names == [DetectorName.HEURISTIC]


def test_detector_can_be_named(env: Env) -> None:
    result = env.run("--detector", "heuristic")

    assert result.exit_code == 0, result.output
    assert env.names == [DetectorName.HEURISTIC]
    assert "Detector: heuristic (top 5)" in result.output


@pytest.mark.parametrize("name", ["fake", "ollama"])
def test_rejects_an_unknown_detector(env: Env, name: str) -> None:
    result = env.run("--detector", name)

    assert result.exit_code == 2
    assert "Invalid value" in result.output
    assert env.names == []


def test_llm_detector_is_named_with_its_model(env: Env) -> None:
    env.monkeypatch.setenv("CLIPPER_LLM_MODEL", "some-model:tag")

    result = env.run("--detector", "llm")

    assert result.exit_code == 0, result.output
    assert env.names == [DetectorName.LLM]
    assert "Detector: llm:some-model:tag (top 5)" in result.output


def test_llm_with_an_empty_model_is_an_error(env: Env) -> None:
    env.monkeypatch.setenv("CLIPPER_LLM_MODEL", "")
    env.context = AppContext()

    result = env.run("--detector", "llm")

    assert result.exit_code == 1
    assert "Error: Set CLIPPER_LLM_MODEL" in result.output


def test_detection_error_is_reported(env: Env) -> None:
    class FailingDetector:
        def detect(self, transcript: Transcript) -> list[ClipCandidate]:
            raise DetectionError("Model reply for window [0:00:00] is bad")

    env.context = AppContext(detector=lambda settings, name: FailingDetector())

    result = env.run()

    assert result.exit_code == 1
    assert "Error: Model reply for window [0:00:00] is bad" in result.output


def test_top_n_comes_from_settings(env: Env) -> None:
    env.monkeypatch.setenv("CLIPPER_TOP_N", "3")
    assert "Detector: heuristic (top 3)" in env.run().output


def test_explicit_labels_path_overrides_the_default(env: Env, tmp_path: Path) -> None:
    other = env.write_labels(
        [{"start": "0:00", "end": "5:00"}], path=tmp_path / "elsewhere.json"
    )

    result = env.run(str(other))

    assert result.exit_code == 0, result.output
    assert "1 video, 1 label, 3 candidates" in result.output
    assert "Recall: 1 of 1 label (100%)" in result.output
    assert (
        "Precision: 1 of 3 candidates (33%); 2 more repeat a matched label"
    ) in result.output


def test_a_second_candidate_on_a_matched_label_is_a_repeat(env: Env) -> None:
    env.write_labels([{"start": "0:00", "end": "2:00"}])
    env.detect([_c(10, 55, 0.9), _c(60, 105, 0.7)])

    result = env.run()

    assert result.exit_code == 0, result.output
    assert "1 (repeat of #1)" in result.output
    assert "Hit by" in result.output
    assert "#1, #2" in result.output
    assert (
        "Precision: 1 of 2 candidates (50%); 1 more repeats a matched label"
    ) in result.output


def test_default_labels_path_uses_the_source_stem(env: Env) -> None:
    # The analysis JSON is still named talk.json; the source's stem decides.
    env.labels.unlink()
    env.save(source="lecture.mkv")
    env.write_labels(
        [{"start": "0:00", "end": "5:00"}],
        source="lecture.mkv",
        path=env.labels_dir / "lecture.json",
    )

    result = env.run()

    assert result.exit_code == 0, result.output
    assert "Labels: lecture.mkv" in result.output


def test_bracketed_source_is_not_parsed_as_markup(env: Env) -> None:
    source = "Title [oFbD0Ot7nQ8].mp4"
    env.labels.unlink()
    env.save(source=source)
    env.write_labels(
        [{"start": "0:00", "end": "5:00"}],
        source=source,
        path=env.labels_dir / "Title [oFbD0Ot7nQ8].json",
    )

    result = env.run()

    assert result.exit_code == 0, result.output
    # rich may wrap the title at the space, so check the bracketed part.
    assert "[oFbD0Ot7nQ8].mp4" in result.output


def test_missing_default_labels_file(env: Env) -> None:
    env.labels.unlink()

    result = env.run()

    assert result.exit_code == 1
    assert "Labels file not found" in result.output


def test_missing_analysis_json(env: Env) -> None:
    env.json.unlink()
    result = env.run()
    assert result.exit_code == 1
    assert "Analysis result not found" in result.output


def test_invalid_labels_file(env: Env) -> None:
    env.labels.write_text("{", encoding="utf-8")
    result = env.run()
    assert result.exit_code == 1
    assert "Invalid JSON in labels file" in result.output


def test_source_mismatch(env: Env) -> None:
    env.write_labels(source="other.mp4")
    result = env.run()
    assert result.exit_code == 1
    assert "Labels are for 'other.mp4'" in result.output


def test_no_labels_leaves_recall_undefined(env: Env) -> None:
    env.write_labels([])

    result = env.run()

    assert result.exit_code == 0, result.output
    assert "Recall: n/a (no labels)" in result.output
    assert "Precision: 0 of 3 candidates (0%)" in result.output


def test_no_candidates_leaves_precision_undefined(env: Env) -> None:
    env.detect([])

    result = env.run()

    assert result.exit_code == 0, result.output
    assert "Recall: 0 of 3 labels (0%)" in result.output
    assert "Precision: n/a (no candidates)" in result.output


def test_label_past_the_transcript_end_is_noted_and_kept(env: Env) -> None:
    env.save(transcript=_transcript(250.0))

    result = env.run()

    assert result.exit_code == 0, result.output
    assert (
        "Note: label 3 (0:04:00-0:04:30) ends after the end of the transcript (250.0s)"
    ) in result.output
    assert result.output.count("Note:") == 1
    assert "1 video, 3 labels" in result.output


def test_label_ending_with_the_transcript_is_not_noted(env: Env) -> None:
    env.save(transcript=_transcript(270.0))
    assert "Note:" not in env.run().output


def test_real_heuristic_runs_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No detector patch: the heuristic runs on the fake transcriber's sample
    # (0-13.5 s), which fits in one window, so it yields one candidate.
    analysis = tmp_path / "show.json"
    analysis.write_text(
        AnalysisResult(
            source="show.mp4",
            transcript=FakeTranscriber().transcribe(Path("unused")),
            candidates=(),
        ).model_dump_json(),
        encoding="utf-8",
    )
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps(
            {"source": "show.mp4", "labels": [{"start": "0:00", "end": "0:10"}]}
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["eval", str(analysis), str(labels)])

    assert result.exit_code == 0, result.output
    assert "1 video, 1 label, 1 candidate" in result.output
    assert "Recall: 1 of 1 label (100%)" in result.output
