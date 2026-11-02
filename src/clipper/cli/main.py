import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from clipper.adapters.detection.heuristic import HeuristicClipDetector
from clipper.adapters.media.ffmpeg import FfmpegAudioExtractor
from clipper.adapters.media.ffprobe import FfprobeMediaProbe
from clipper.adapters.rendering.ffmpeg import FfmpegClipRenderer
from clipper.adapters.results.analysis_json import (
    check_source_matches,
    load_analysis_result,
)
from clipper.adapters.results.labels_json import check_labels_match, load_labels
from clipper.adapters.transcription.caching import CachingTranscriber
from clipper.adapters.transcription.faster_whisper import FasterWhisperTranscriber
from clipper.adapters.transcription.video import VideoTranscriber
from clipper.core.captions import build_srt
from clipper.core.config import Settings
from clipper.core.evaluation import evaluate
from clipper.core.interfaces import ClipRenderer, MediaProbe, Transcriber
from clipper.core.naming import plan_clip_paths
from clipper.core.pipeline import AnalysisPipeline
from clipper.domain.errors import ClipperError, ClipRenderError
from clipper.domain.models import (
    AnalysisResult,
    ClipCandidate,
    Evaluation,
    MediaInfo,
    Transcript,
)

app = typer.Typer(help="Find clip-worthy moments in long-form video.")
console = Console()


def _fmt(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}"


def _render(result: AnalysisResult) -> None:
    table = Table(title=f"Top clips: {result.source}")
    for column in ("#", "Start", "End", "Score", "Why"):
        table.add_column(column)
    for rank, c in enumerate(result.candidates, start=1):
        table.add_row(
            str(rank),
            _fmt(c.start_seconds),
            _fmt(c.end_seconds),
            f"{c.score:.2f}",
            "; ".join(c.reasons),
        )
    console.print(table)


def _build_transcriber(settings: Settings) -> Transcriber:
    """Compose the real transcriber. Tests replace this to avoid Whisper/ffmpeg."""
    whisper = FasterWhisperTranscriber(model_size=settings.whisper_model)
    return CachingTranscriber(
        VideoTranscriber(FfmpegAudioExtractor(), whisper),
        settings.cache_dir,
        namespace=whisper.name,
    )


def _build_renderer(settings: Settings) -> ClipRenderer:
    """Compose the real clip renderer. Tests replace this to avoid ffmpeg."""
    return FfmpegClipRenderer()


def _build_probe() -> MediaProbe:
    """Compose the real media probe. Tests replace this to avoid ffprobe."""
    return FfprobeMediaProbe()


def _select_ranks(result: AnalysisResult, ranks: list[int] | None) -> list[int]:
    """Return the 1-based ranks to render: all, or the given ones sorted, deduped."""
    total = len(result.candidates)
    if total == 0:
        raise ClipRenderError("The analysis result has no candidates to render")
    if not ranks:
        return list(range(1, total + 1))
    bad = sorted({r for r in ranks if not 1 <= r <= total})
    if bad:
        listed = ", ".join(str(r) for r in bad)
        raise ClipRenderError(
            f"Rank {listed} out of range: the result has {total} candidates"
        )
    return sorted(set(ranks))


def _check_media(selected: list[ClipCandidate], info: MediaInfo) -> None:
    """Fail if the file has no video or a selected candidate runs past its end."""
    if not info.has_video:
        raise ClipRenderError("The video file has no video stream")
    for candidate in selected:
        if candidate.end_seconds > info.duration_seconds:
            raise ClipRenderError(
                f"Candidate {_fmt(candidate.start_seconds)}-"
                f"{_fmt(candidate.end_seconds)} ends after the end of the video "
                f"({info.duration_seconds:.1f}s)"
            )


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _ratio(hit: int, total: int, noun: str, value: float | None) -> str:
    if value is None:
        return f"n/a (no {noun}s)"
    return f"{hit} of {_count(total, noun)} ({value:.0%})"


def _report_evaluation(
    evaluation: Evaluation, transcript: Transcript, detector: str
) -> None:
    """Print labels and candidates with what each hit, then the counts."""
    hit_by: dict[int, list[int]] = {}
    hits: dict[int, list[int]] = {}
    for label_index, candidate_index in evaluation.hits:
        hit_by.setdefault(label_index, []).append(candidate_index + 1)
        hits.setdefault(candidate_index, []).append(label_index + 1)

    labels = Table(title=f"Labels: {evaluation.source}")
    for column in ("#", "Start", "End", "Hit by", "Note"):
        labels.add_column(column)
    for index, label in enumerate(evaluation.labels):
        labels.add_row(
            str(index + 1),
            _fmt(label.start_seconds),
            _fmt(label.end_seconds),
            ", ".join(f"#{rank}" for rank in hit_by.get(index, [])) or "-",
            escape(label.note or ""),
        )
    console.print(labels)

    candidates = Table(title="Candidates")
    for column in ("#", "Start", "End", "Score", "Hits label"):
        candidates.add_column(column)
    for index, candidate in enumerate(evaluation.candidates):
        candidates.add_row(
            str(index + 1),
            _fmt(candidate.start_seconds),
            _fmt(candidate.end_seconds),
            f"{candidate.score:.2f}",
            ", ".join(str(number) for number in hits.get(index, [])) or "-",
        )
    console.print(candidates)

    end = transcript.duration_seconds
    for index, label in enumerate(evaluation.labels):
        if round(label.end_seconds * 1000) > round(end * 1000):
            console.print(
                f"[yellow]Note:[/yellow] label {index + 1} "
                f"({_fmt(label.start_seconds)}-{_fmt(label.end_seconds)}) ends "
                f"after the end of the transcript ({end:.1f}s); "
                "no detector can reach the part past it",
                soft_wrap=True,
            )

    n_labels, n_candidates = len(evaluation.labels), len(evaluation.candidates)
    console.print(f"Detector: {detector}", soft_wrap=True)
    console.print(
        f"1 video, {_count(n_labels, 'label')}, {_count(n_candidates, 'candidate')}",
        soft_wrap=True,
    )
    console.print(
        "Recall: "
        + _ratio(evaluation.labels_hit, n_labels, "label", evaluation.recall),
        soft_wrap=True,
    )
    console.print(
        "Precision: "
        + _ratio(
            evaluation.candidates_hit,
            n_candidates,
            "candidate",
            evaluation.precision,
        ),
        soft_wrap=True,
    )
    console.print(
        f"Indicative only: one labeler, 1 video, {_count(n_labels, 'label')} "
        "(ADR 0012).",
        soft_wrap=True,
    )


@app.callback()
def _root() -> None:
    """Clipper command line interface."""


@app.command()
def analyze(
    video: Annotated[Path, typer.Argument(help="Path to a video file.")],
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Transcribe VIDEO and print the top clip candidates."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = Settings()
    pipeline = AnalysisPipeline(
        _build_transcriber(settings), HeuristicClipDetector(top_n=settings.top_n)
    )

    try:
        result = pipeline.run(video)
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    out_file = settings.output_dir / f"{video.stem}.json"
    out_file.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    _render(result)
    console.print(f"Saved: {out_file}")


@app.command()
def render(
    video: Annotated[Path, typer.Argument(help="Path to the original video file.")],
    result_json: Annotated[
        Path, typer.Argument(help="JSON saved by `clipper analyze`.")
    ],
    rank: Annotated[
        list[int] | None,
        typer.Option(
            "--rank", help="Render only this 1-based rank. Repeat for several."
        ),
    ] = None,
    captions: Annotated[
        bool, typer.Option("--captions", help="Burn captions into each clip.")
    ] = False,
) -> None:
    """Render the clip candidates in RESULT_JSON from VIDEO to video files."""
    settings = Settings()
    try:
        result = load_analysis_result(result_json)
        check_source_matches(result, video)
        ranks = _select_ranks(result, rank)
        selected = [result.candidates[r - 1] for r in ranks]
        _check_media(selected, _build_probe().probe(video))
        paths = plan_clip_paths(video, selected, settings.clips_dir)
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    renderer = _build_renderer(settings)
    failures: list[str] = []
    rendered = 0
    for number, candidate, path in zip(ranks, selected, paths, strict=True):
        span = f"{_fmt(candidate.start_seconds)}-{_fmt(candidate.end_seconds)}"
        srt = build_srt(result.transcript, candidate) if captions else ""
        if captions and not srt:
            console.print(
                f"[yellow]Note:[/yellow] #{number} {span} has no speech in range; "
                "rendered without captions",
                soft_wrap=True,
            )
        try:
            clip = renderer.render(video, candidate, path, captions=srt or None)
        except ClipperError as exc:
            failures.append(f"#{number} {span}: {escape(str(exc))}")
            continue
        rendered += 1
        console.print(
            f"Rendered #{number} {span} {escape(str(clip.path))}"
            f"{' (captions)' if srt else ''}",
            soft_wrap=True,
        )

    for failure in failures:
        console.print(f"[red]Failed:[/red] {failure}", soft_wrap=True)
    console.print(
        f"Rendered {rendered} of {len(selected)} clips to "
        f"{escape(str(settings.clips_dir))}",
        soft_wrap=True,
    )
    if failures:
        raise typer.Exit(code=1)


@app.command("eval")
def eval_(
    result_json: Annotated[
        Path, typer.Argument(help="JSON saved by `clipper analyze`.")
    ],
    labels_json: Annotated[
        Path | None,
        typer.Argument(
            help="Labels file. Default: <labels dir>/<video name>.json.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Rerun the detector on RESULT_JSON's transcript and compare with labels."""
    settings = Settings()
    try:
        result = load_analysis_result(result_json)
        if labels_json is None:
            labels_json = settings.labels_dir / f"{Path(result.source).stem}.json"
        labeled = load_labels(labels_json)
        check_labels_match(labeled, result)
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    detector = HeuristicClipDetector(top_n=settings.top_n)
    evaluation = evaluate(labeled, detector.detect(result.transcript))
    _report_evaluation(
        evaluation, result.transcript, f"heuristic (top {settings.top_n})"
    )
