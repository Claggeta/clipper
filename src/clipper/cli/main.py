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
from clipper.adapters.transcription.caching import CachingTranscriber
from clipper.adapters.transcription.faster_whisper import FasterWhisperTranscriber
from clipper.adapters.transcription.video import VideoTranscriber
from clipper.core.config import Settings
from clipper.core.interfaces import ClipRenderer, MediaProbe, Transcriber
from clipper.core.naming import plan_clip_paths
from clipper.core.pipeline import AnalysisPipeline
from clipper.domain.errors import ClipperError, ClipRenderError
from clipper.domain.models import AnalysisResult, ClipCandidate, MediaInfo

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
        try:
            clip = renderer.render(video, candidate, path)
        except ClipperError as exc:
            failures.append(f"#{number} {span}: {escape(str(exc))}")
            continue
        rendered += 1
        console.print(
            f"Rendered #{number} {span} {escape(str(clip.path))}", soft_wrap=True
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
