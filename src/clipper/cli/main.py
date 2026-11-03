import logging
from pathlib import Path
from typing import Annotated, NamedTuple

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from clipper.adapters.results.analysis_json import (
    check_source_matches,
    load_analysis_result,
)
from clipper.adapters.results.labels_json import check_labels_match, load_labels
from clipper.adapters.results.manifest_json import save_manifest
from clipper.cli.context import AppContext, DetectorName
from clipper.core.captions import build_ass, clip_text
from clipper.core.config import Settings
from clipper.core.evaluation import evaluate
from clipper.core.interfaces import TitleWriter
from clipper.core.naming import clip_folder, plan_clip_paths
from clipper.core.pipeline import AnalysisPipeline
from clipper.core.sources import parse_video_url, video_path
from clipper.domain.errors import ClipperError, ClipRenderError
from clipper.domain.models import (
    AnalysisResult,
    ClipCandidate,
    Evaluation,
    ManifestClip,
    MediaInfo,
    RenderManifest,
    Transcript,
)

app = typer.Typer(help="Find clip-worthy moments in long-form video.")
console = Console()


DetectorOption = Annotated[
    DetectorName, typer.Option("--detector", help="Detector that picks the clips.")
]


def _fmt(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}"


def _render(result: AnalysisResult) -> None:
    table = Table(title=f"Top clips: {escape(result.source)}")
    for column in ("#", "Start", "End", "Score", "Why"):
        table.add_column(column)
    for rank, c in enumerate(result.candidates, start=1):
        table.add_row(
            str(rank),
            _fmt(c.start_seconds),
            _fmt(c.end_seconds),
            f"{c.score:.2f}",
            escape("; ".join(c.reasons)),
        )
    console.print(table)


def _context(ctx: typer.Context) -> AppContext:
    """The AppContext the root callback set, or the one a test passed in."""
    if not isinstance(ctx.obj, AppContext):
        raise TypeError(f"Expected an AppContext, got {type(ctx.obj).__name__}")
    return ctx.obj


def _detector_label(settings: Settings, name: DetectorName) -> str:
    """The name recorded in results and printed: the LLM's includes its model."""
    if name is DetectorName.LLM:
        return f"{name.value}:{settings.llm_model}"
    return name.value


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
    matched_by = {label: candidate for label, candidate in evaluation.matches}
    matched = set(matched_by.values())

    def hits_cell(index: int) -> str:
        numbers = hits.get(index, [])
        if index in matched or not numbers:
            return ", ".join(str(number) for number in numbers) or "-"
        return ", ".join(
            f"{number} (repeat of #{matched_by[number - 1] + 1})" for number in numbers
        )

    labels = Table(title=f"Labels: {escape(evaluation.source)}")
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
            hits_cell(index),
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
    console.print(f"Detector: {escape(detector)}", soft_wrap=True)
    console.print(
        f"1 video, {_count(n_labels, 'label')}, {_count(n_candidates, 'candidate')}",
        soft_wrap=True,
    )
    console.print(
        "Recall: "
        + _ratio(evaluation.labels_hit, n_labels, "label", evaluation.recall),
        soft_wrap=True,
    )
    repeats = evaluation.repeats
    console.print(
        "Precision: "
        + _ratio(
            evaluation.candidates_matched,
            n_candidates,
            "candidate",
            evaluation.precision,
        )
        + (
            f"; {repeats} more {'repeats' if repeats == 1 else 'repeat'} "
            "a matched label"
            if repeats
            else ""
        ),
        soft_wrap=True,
    )
    console.print(
        f"Indicative only: one labeler, 1 video, {_count(n_labels, 'label')} "
        "(ADR 0012).",
        soft_wrap=True,
    )


@app.callback()
def _root(ctx: typer.Context) -> None:
    """Clipper command line interface."""
    if ctx.obj is None:
        ctx.obj = AppContext()


@app.command()
def fetch(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="A YouTube video or Twitch VOD URL.")],
) -> None:
    """Download one YouTube video or Twitch VOD to the videos folder.

    Downloading from YouTube or Twitch is against their terms of service (see
    docs/decisions/0020). You are responsible for having the right to download
    the video. One video per call; playlists and channels are refused.
    """
    settings = Settings()
    build = _context(ctx)
    try:
        source = parse_video_url(url)
        existing = video_path(source, settings.videos_dir)
        if existing.is_file():
            console.print(f"Already fetched: {escape(str(existing))}", soft_wrap=True)
            return
        saved = build.downloader(settings).download(source, settings.videos_dir)
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}", soft_wrap=True)
        raise typer.Exit(code=1) from exc
    console.print(f"Saved: {escape(str(saved))}", soft_wrap=True)


@app.command()
def analyze(
    ctx: typer.Context,
    video: Annotated[Path, typer.Argument(help="Path to a video file.")],
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    detector: DetectorOption = DetectorName.HEURISTIC,
) -> None:
    """Transcribe VIDEO and print the top clip candidates."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = Settings()
    build = _context(ctx)
    try:
        _analyze_video(settings, build, video, detector)
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc


@app.command()
def render(
    ctx: typer.Context,
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
    vertical: Annotated[
        bool,
        typer.Option(
            "--vertical",
            help="Fit the whole frame into 9:16 at 1080x1920, on a blurred fill.",
        ),
    ] = False,
    titles: Annotated[
        bool,
        typer.Option(
            "--titles",
            help="Suggest a title and hook per clip with the local LLM "
            "(CLIPPER_LLM_MODEL), written to the manifest.",
        ),
    ] = False,
) -> None:
    """Render the clip candidates in RESULT_JSON from VIDEO to video files."""
    settings = Settings()
    build = _context(ctx)
    try:
        result = load_analysis_result(result_json)
        check_source_matches(result, video)
        plan = _plan_render(settings, build, video, result, rank)
        writer = build.title_writer(settings) if titles else None
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    flags = _RenderFlags(captions=captions, vertical=vertical, titles=titles)
    if _cut_clips(settings, build, video, result, result_json, plan, writer, flags):
        raise typer.Exit(code=1)


def _analyze_video(
    settings: Settings, build: AppContext, video: Path, detector: DetectorName
) -> tuple[AnalysisResult, Path]:
    """Transcribe VIDEO, detect candidates, save the result JSON and print it.

    Returns the result and the JSON's path. Raises `ClipperError` before
    anything is written if transcription or detection fails.
    """
    label = _detector_label(settings, detector)
    pipeline = AnalysisPipeline(
        build.transcriber(settings),
        build.detector(settings, detector),
        detector_name=label,
    )
    result = pipeline.run(video)

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    out_file = settings.output_dir / f"{video.stem}.json"
    out_file.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    _render(result)
    console.print(f"Detector: {escape(label)}", soft_wrap=True)
    console.print(f"Saved: {escape(str(out_file))}")
    return result, out_file


class _RenderPlan(NamedTuple):
    """The selected candidates, by rank, and where each clip goes."""

    ranks: list[int]
    selected: list[ClipCandidate]
    folder: Path
    paths: list[Path]


class _RenderFlags(NamedTuple):
    captions: bool
    vertical: bool
    titles: bool


def _plan_render(
    settings: Settings,
    build: AppContext,
    video: Path,
    result: AnalysisResult,
    rank: list[int] | None,
) -> _RenderPlan:
    """The checks before cutting (ADR 0011). Raises `ClipperError`."""
    ranks = _select_ranks(result, rank)
    selected = [result.candidates[r - 1] for r in ranks]
    _check_media(selected, build.probe().probe(video))
    folder = clip_folder(video, settings.clips_dir)
    paths = plan_clip_paths(video, selected, folder)
    return _RenderPlan(ranks, selected, folder, paths)


def _cut_clips(
    settings: Settings,
    build: AppContext,
    video: Path,
    result: AnalysisResult,
    result_json: Path,
    plan: _RenderPlan,
    writer: TitleWriter | None,
    flags: _RenderFlags,
) -> bool:
    """Render every planned clip, then write the manifest (ADRs 0011, 0019).

    Returns True if any clip failed. A manifest that cannot be written is
    `Error:` and exit 1.
    """
    ranks, selected, folder, paths = plan
    captions, vertical, titles = flags
    renderer = build.renderer(settings)
    failures: list[str] = []
    entries: list[ManifestClip] = []
    rendered = 0
    for number, candidate, path in zip(ranks, selected, paths, strict=True):
        entry = {
            "rank": number,
            "start_seconds": candidate.start_seconds,
            "end_seconds": candidate.end_seconds,
            "score": candidate.score,
            "reasons": candidate.reasons,
        }
        span = f"{_fmt(candidate.start_seconds)}-{_fmt(candidate.end_seconds)}"
        built = (
            build_ass(result.transcript, candidate, vertical=vertical)
            if captions
            else None
        )
        ass = built.text if built else ""
        if captions and not ass:
            console.print(
                f"[yellow]Note:[/yellow] #{number} {span} has no speech in range; "
                "rendered without captions",
                soft_wrap=True,
            )
        elif built and built.from_segments:
            console.print(
                f"[yellow]Note:[/yellow] #{number} {span} has no word timings; "
                "captions show whole segments",
                soft_wrap=True,
            )
        text = clip_text(result.transcript, candidate) if writer else ""
        if writer and not text:
            console.print(
                f"[yellow]Note:[/yellow] #{number} {span} has no speech in range; "
                "no title",
                soft_wrap=True,
            )
        try:
            # The title comes first, so a clip whose title fails is not cut
            # (ADR 0018).
            title = writer.write(text) if writer and text else None
            clip = renderer.render(
                video, candidate, path, captions=ass or None, vertical=vertical
            )
        except ClipperError as exc:
            failures.append(f"#{number} {span}: {escape(str(exc))}")
            entries.append(ManifestClip(**entry, error=str(exc)))
            continue
        rendered += 1
        burned = None
        if ass and built:
            burned = "segments" if built.from_segments else "words"
        entries.append(
            ManifestClip(
                **entry,
                file=path.name,
                captions=burned,
                title=title.title if title else None,
                hook=title.hook if title else None,
            )
        )
        shown = (("vertical", vertical), ("captions", ass), ("titles", title))
        tags = [t for t, on in shown if on]
        suffix = f" ({', '.join(tags)})" if tags else ""
        console.print(
            f"Rendered #{number} {span} {escape(str(clip.path))}{suffix}",
            soft_wrap=True,
        )
        if title:
            console.print(f"  Title: {escape(title.title)}", soft_wrap=True)
            console.print(f"  Hook: {escape(title.hook)}", soft_wrap=True)

    for failure in failures:
        console.print(f"[red]Failed:[/red] {failure}", soft_wrap=True)
    console.print(
        f"Rendered {rendered} of {len(selected)} clips to {escape(str(folder))}",
        soft_wrap=True,
    )
    manifest = RenderManifest(
        source=video.name,
        analysis=str(result_json),
        detector=result.detector,
        vertical=vertical,
        captions=captions,
        titles=titles,
        clips=tuple(entries),
    )
    try:
        written = save_manifest(manifest, folder)
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}", soft_wrap=True)
        raise typer.Exit(code=1) from exc
    console.print(f"Manifest: {escape(str(written))}", soft_wrap=True)
    return bool(failures)


@app.command("eval")
def eval_(
    ctx: typer.Context,
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
    detector: DetectorOption = DetectorName.HEURISTIC,
) -> None:
    """Rerun the detector on RESULT_JSON's transcript and compare with labels."""
    settings = Settings()
    build = _context(ctx)
    try:
        result = load_analysis_result(result_json)
        if labels_json is None:
            labels_json = settings.labels_dir / f"{Path(result.source).stem}.json"
        labeled = load_labels(labels_json)
        check_labels_match(labeled, result)
        candidates = build.detector(settings, detector).detect(result.transcript)
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    evaluation = evaluate(labeled, candidates)
    label = _detector_label(settings, detector)
    _report_evaluation(evaluation, result.transcript, f"{label} (top {settings.top_n})")
