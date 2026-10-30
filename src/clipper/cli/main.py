import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from clipper.adapters.detection.heuristic import HeuristicClipDetector
from clipper.adapters.media.ffmpeg import FfmpegAudioExtractor
from clipper.adapters.transcription.caching import CachingTranscriber
from clipper.adapters.transcription.faster_whisper import FasterWhisperTranscriber
from clipper.adapters.transcription.video import VideoTranscriber
from clipper.core.config import Settings
from clipper.core.interfaces import Transcriber
from clipper.core.pipeline import AnalysisPipeline
from clipper.domain.errors import ClipperError
from clipper.domain.models import AnalysisResult

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
