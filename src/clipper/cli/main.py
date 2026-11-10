import logging
import statistics
from enum import StrEnum
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
from clipper.adapters.results.labels_json import (
    check_labels_match,
    load_labels,
    load_links,
    save_labels,
)
from clipper.adapters.results.manifest_json import (
    check_manifest_source,
    load_manifest,
    save_manifest,
)
from clipper.adapters.results.video_info_json import (
    load_video_info,
    save_video_info,
    video_info_beside,
)
from clipper.cli.context import AppContext, DetectorName
from clipper.core.captions import build_ass, clip_text
from clipper.core.config import Settings
from clipper.core.evaluation import boundary_gaps, evaluate
from clipper.core.framing import plan_face_crop
from clipper.core.interfaces import TitleWriter
from clipper.core.judging import check_judged_labels, judged_labels_path
from clipper.core.labeling import clip_labels
from clipper.core.naming import clip_folder, plan_clip_paths, variant_file_name
from clipper.core.pipeline import AnalysisPipeline
from clipper.core.sources import parse_video_url, video_info_path, video_path
from clipper.domain.errors import (
    ClipperError,
    ClipRenderError,
    LabelsError,
    ReviewError,
    TitleError,
)
from clipper.domain.models import (
    AnalysisResult,
    ClipCandidate,
    ClipVariant,
    Evaluation,
    ManifestClip,
    MediaInfo,
    RenderManifest,
    Transcript,
    VideoInfo,
    VideoSource,
)
from clipper.review.server import (
    DEFAULT_PORT,
    MAX_START_SECONDS,
    ReviewSession,
    frames_folder,
)

app = typer.Typer(help="Find clip-worthy moments in long-form video.")
console = Console()


class VerticalStyle(StrEnum):
    """Vertical styles selectable with --style (ADR 0025)."""

    FIT = "fit"
    FACE = "face"


# Hand-written names for titles (ADR 0018, about amendment).
_ABOUT_LIMIT = 300
AboutOption = Annotated[
    str | None,
    typer.Option(
        "--about",
        help="Who and what the video is about, for titles: the names the model "
        'should use, one line. Example: "Ron (stableronaldo)".',
        show_default=False,
    ),
]
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


def _report_judged(evaluation: Evaluation) -> None:
    """Print how the candidates fall on judged labels (ADR 0012, V5 amendment)."""
    gaps = boundary_gaps(evaluation)
    ends_early = sum(gap.ends_early for gap in gaps)
    starts_late = sum(gap.starts_late for gap in gaps)
    as_cut = sum(not (gap.ends_early or gap.starts_late) for gap in gaps)
    repeats = evaluation.repeats
    unjudged = evaluation.candidates_unjudged
    console.print(
        f"Judged: {len(gaps)} on a keeper ({as_cut} as cut, {ends_early} "
        f"{'ends' if ends_early == 1 else 'end'} early, {starts_late} "
        f"{'starts' if starts_late == 1 else 'start'} late), "
        f"{_count(repeats, 'repeat')}, {len(evaluation.on_rejected)} rejected, "
        f"{unjudged} unjudged",
        soft_wrap=True,
    )
    for name, values in (
        ("End", [gap.end for gap in gaps]),
        ("Start", [gap.start for gap in gaps]),
    ):
        if values:
            console.print(
                f"{name} gap (label - candidate): median "
                f"{statistics.median(values):+.1f} s, {min(values):+.1f} to "
                f"{max(values):+.1f} s over {_count(len(values), 'keeper')}",
                soft_wrap=True,
            )
    if unjudged:
        console.print(
            f"[yellow]Note:[/yellow] {_count(unjudged, 'unjudged candidate')}; "
            "judge them before comparing variants",
            soft_wrap=True,
        )


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
    on_rejected = set(evaluation.on_rejected)

    def hits_cell(index: int) -> str:
        numbers = hits.get(index, [])
        if not numbers and evaluation.judged:
            return "rejected" if index in on_rejected else "unjudged"
        if index in matched or not numbers:
            return ", ".join(str(number) for number in numbers) or "-"
        return ", ".join(
            f"{number} (repeat of #{matched_by[number - 1] + 1})" for number in numbers
        )

    labels = Table(title=f"Labels: {escape(evaluation.source)}")
    columns = ["#", "Start", "End", "Hit by", "Note"]
    if evaluation.judged:
        columns.insert(3, "Verdict")
    for column in columns:
        labels.add_column(column)
    for index, label in enumerate(evaluation.labels):
        row = [
            str(index + 1),
            _fmt(label.start_seconds),
            _fmt(label.end_seconds),
            ", ".join(f"#{rank}" for rank in hit_by.get(index, [])) or "-",
            escape(label.note or ""),
        ]
        if evaluation.judged:
            row.insert(3, label.verdict or "")
        labels.add_row(*row)
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
    if evaluation.judged:
        _report_judged(evaluation)
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
        _fetch_video(settings, build, parse_video_url(url))
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}", soft_wrap=True)
        raise typer.Exit(code=1) from exc


def _fetch_video(settings: Settings, build: AppContext, source: VideoSource) -> Path:
    """Download one video, or reuse the file already there (ADR 0020).

    Returns the video's path. Raises `ClipperError` if the download fails.
    """
    video = video_path(source, settings.videos_dir)
    if video.is_file():
        console.print(f"Already fetched: {escape(str(video))}", soft_wrap=True)
    else:
        video = build.downloader(settings).download(source, settings.videos_dir)
        console.print(f"Saved: {escape(str(video))}", soft_wrap=True)
    _save_video_info(settings, build, source)
    return video


def _save_video_info(
    settings: Settings, build: AppContext, source: VideoSource
) -> None:
    """Save the video's metadata beside it, once (ADR 0020, video info).

    A failure is a warning: the video is there, and only titles use the info.
    """
    path = video_info_path(source, settings.videos_dir)
    if path.is_file():
        return
    try:
        save_video_info(build.video_info(settings).lookup(source), path)
    except ClipperError as exc:
        console.print(
            f"[yellow]Warning:[/yellow] no video info saved: {escape(str(exc))}",
            soft_wrap=True,
        )
        return
    console.print(f"Video info: {escape(str(path))}", soft_wrap=True)


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
    style: Annotated[
        VerticalStyle,
        typer.Option(
            "--style",
            help="With --vertical: fit (the whole frame on a blurred fill) or "
            "face (a full-height crop that follows the largest face).",
        ),
    ] = VerticalStyle.FIT,
    titles: Annotated[
        bool,
        typer.Option(
            "--titles",
            help="Suggest a title and hook per clip with the local LLM "
            "(CLIPPER_LLM_MODEL), written to the manifest.",
        ),
    ] = False,
    about: AboutOption = None,
) -> None:
    """Render the clip candidates in RESULT_JSON from VIDEO to video files."""
    settings = Settings()
    build = _context(ctx)
    try:
        about = _check_about(about, titles)
        face = style is VerticalStyle.FACE
        if face and not vertical:
            raise ClipRenderError("--style face needs --vertical")
        result = load_analysis_result(result_json)
        check_source_matches(result, video)
        plan = _plan_render(settings, build, video, result, rank)
        _check_frame_size((style,) if face else (), plan.media, video)
        writer = build.title_writer(settings) if titles else None
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc

    flags = _RenderFlags(
        captions=captions,
        vertical=vertical,
        titles=titles,
        about=about,
        styles=(style,) if vertical else (),
    )
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
    media: MediaInfo


class _RenderFlags(NamedTuple):
    captions: bool
    vertical: bool
    titles: bool
    about: str | None = None
    # The vertical styles to render, in order; empty for a landscape run.
    styles: tuple[VerticalStyle, ...] = ()


def _drafts_styles(
    chosen: list[VerticalStyle] | None, vertical: bool
) -> tuple[VerticalStyle, ...]:
    """The styles `drafts` renders, in order without repeats (ADR 0025).

    Fit and face by default; none without --vertical, where --style is an error.
    """
    if not vertical:
        if chosen:
            raise ClipRenderError("--style needs --vertical")
        return ()
    picked = chosen or [VerticalStyle.FIT, VerticalStyle.FACE]
    return tuple(dict.fromkeys(picked))


def _check_frame_size(
    styles: tuple[VerticalStyle, ...], media: MediaInfo, video: Path
) -> None:
    """The face style needs the frame size; fail before cutting without it."""
    if VerticalStyle.FACE in styles and (media.width is None or media.height is None):
        raise ClipRenderError(
            f"Cannot tell the frame size of {video}, which --style face needs"
        )


def _check_about(about: str | None, titles: bool) -> str | None:
    """The `--about` text, stripped, or None. Raises `TitleError` if unusable."""
    if about is None:
        return None
    about = about.strip()
    if not titles:
        raise TitleError("--about is for titles; use it with titles on")
    if not about:
        raise TitleError("--about is empty")
    if "\n" in about or "\r" in about:
        raise TitleError("--about must be one line")
    if len(about) > _ABOUT_LIMIT:
        raise TitleError(
            f"--about is {len(about)} characters; keep it to {_ABOUT_LIMIT}"
        )
    return about


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
    media = build.probe().probe(video)
    _check_media(selected, media)
    folder = clip_folder(video, settings.clips_dir)
    paths = plan_clip_paths(video, selected, folder)
    return _RenderPlan(ranks, selected, folder, paths, media)


def _title_context(video: Path) -> VideoInfo | None:
    """The source's saved metadata for titles, if any (ADR 0018, 8b).

    No file means titles from the clip's words alone; a bad file is a warning.
    """
    try:
        info = load_video_info(video_info_beside(video))
    except ClipperError as exc:
        console.print(
            f"[yellow]Warning:[/yellow] titles without video info: {escape(str(exc))}",
            soft_wrap=True,
        )
        return None
    if info is not None:
        console.print(f"Title context: {escape(info.channel)}", soft_wrap=True)
    return info


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
    ranks, selected, folder, paths, media = plan
    captions, vertical, titles, about, styles = flags
    renderer = build.renderer(settings)
    face_only = styles == (VerticalStyle.FACE,)
    tracker = build.face_tracker(settings) if VerticalStyle.FACE in styles else None
    video_info = _title_context(video) if writer else None
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
        crop = None
        if tracker is not None:
            # Faces come before the title, so a face-only clip with none costs
            # no title request (ADR 0025). With `fit` too, the clip still
            # renders in `fit`.
            try:
                track = tracker.track(
                    video, candidate.start_seconds, candidate.end_seconds
                )
            except ClipperError as exc:
                if face_only:
                    failures.append(f"#{number} {span}: {escape(str(exc))}")
                    entries.append(ManifestClip(**entry, error=str(exc)))
                    continue
                failures.append(f"#{number} {span} (face): {escape(str(exc))}")
            else:
                assert media.width is not None and media.height is not None
                crop = plan_face_crop(track, media.width, media.height)
                if crop is None:
                    console.print(
                        f"[yellow]Note:[/yellow] #{number} {span} has no face in "
                        "range; not rendered in the face style",
                        soft_wrap=True,
                    )
                    if face_only:
                        entries.append(ManifestClip(**entry, error="no face found"))
                        continue
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
            # (ADR 0018). One title serves every style.
            title = writer.write(text, video_info, about) if writer and text else None
        except ClipperError as exc:
            failures.append(f"#{number} {span}: {escape(str(exc))}")
            entries.append(ManifestClip(**entry, error=str(exc)))
            continue
        # A landscape run renders once, with no style and no variants.
        wanted: list[VerticalStyle | None] = (
            [s for s in styles if s is not VerticalStyle.FACE or crop is not None]
            if styles
            else [None]
        )
        variants: list[ClipVariant] = []
        files: list[str] = []
        error = ""
        for style in wanted:
            name = variant_file_name(path.name, (style or VerticalStyle.FIT).value)
            try:
                clip = renderer.render(
                    video,
                    candidate,
                    path.with_name(name),
                    captions=ass or None,
                    vertical=vertical,
                    crop=crop if style is VerticalStyle.FACE else None,
                )
            except ClipperError as exc:
                label = f" ({style.value})" if style and len(wanted) > 1 else ""
                failures.append(f"#{number} {span}{label}: {escape(str(exc))}")
                error = str(exc)
                break
            files.append(name)
            if style is not None:
                variants.append(ClipVariant(style=style.value, file=name))
            shown = (
                ("vertical", vertical),
                ("face", style is VerticalStyle.FACE),
                ("captions", ass),
                ("titles", title),
            )
            tags = [t for t, on in shown if on]
            suffix = f" ({', '.join(tags)})" if tags else ""
            console.print(
                f"Rendered #{number} {span} {escape(str(clip.path))}{suffix}",
                soft_wrap=True,
            )
        if not files:
            entries.append(ManifestClip(**entry, error=error))
            continue
        rendered += 1
        burned = None
        if ass and built:
            burned = "segments" if built.from_segments else "words"
        entries.append(
            ManifestClip(
                **entry,
                file=files[0],
                captions=burned,
                title=title.title if title else None,
                hook=title.hook if title else None,
                variants=tuple(variants),
            )
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
        styles=tuple(s.value for s in styles),
    )
    try:
        written = save_manifest(manifest, folder)
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}", soft_wrap=True)
        raise typer.Exit(code=1) from exc
    console.print(f"Manifest: {escape(str(written))}", soft_wrap=True)
    return bool(failures)


@app.command()
def drafts(
    ctx: typer.Context,
    source: Annotated[
        str,
        typer.Argument(help="A YouTube video or Twitch VOD URL, or a video file."),
    ],
    vertical: Annotated[
        bool,
        typer.Option(
            "--vertical/--no-vertical",
            help="Fit the whole frame into 9:16 at 1080x1920, on a blurred fill.",
        ),
    ] = True,
    captions: Annotated[
        bool,
        typer.Option("--captions/--no-captions", help="Burn captions into each clip."),
    ] = True,
    titles: Annotated[
        bool,
        typer.Option(
            "--titles/--no-titles",
            help="Suggest a title and hook per clip with the local LLM "
            "(CLIPPER_LLM_MODEL), written to the manifest.",
        ),
    ] = True,
    style: Annotated[
        list[VerticalStyle] | None,
        typer.Option(
            "--style",
            help="A vertical style to render; repeat for several. Default: fit "
            "and face, so each clip comes in both to pick from.",
            show_default=False,
        ),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    detector: DetectorOption = DetectorName.HEURISTIC,
    about: AboutOption = None,
) -> None:
    """Turn SOURCE into a folder of clip drafts: fetch, analyze, then render.

    SOURCE starting with http:// or https:// is fetched as `fetch` does (see
    docs/decisions/0020); anything else is a local video file. Every
    candidate is rendered, by default vertical in both styles, captioned and
    titled (see docs/decisions/0022 and 0025).
    """
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = Settings()
    build = _context(ctx)
    try:
        # Everything that can fail on input or settings fails before a
        # download or a transcription starts (ADR 0022).
        about = _check_about(about, titles)
        styles = _drafts_styles(style, vertical)
        url = source.lower().startswith(("http://", "https://"))
        fetched = parse_video_url(source) if url else None
        video = Path(source)
        if fetched is None and not video.is_file():
            raise ClipperError(f"Input file not found: {video}")
        writer = build.title_writer(settings) if titles else None
        if fetched is not None:
            video = _fetch_video(settings, build, fetched)
        result, result_json = _analyze_video(settings, build, video, detector)
        plan = _plan_render(settings, build, video, result, None)
        _check_frame_size(styles, plan.media, video)
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}", soft_wrap=True)
        raise typer.Exit(code=1) from exc

    flags = _RenderFlags(
        captions=captions,
        vertical=vertical,
        titles=titles,
        about=about,
        styles=styles,
    )
    failed = _cut_clips(
        settings, build, video, result, result_json, plan, writer, flags
    )
    console.print(f"Drafts: {escape(str(plan.folder))}", soft_wrap=True)
    if failed:
        raise typer.Exit(code=1)


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


@app.command()
def labels(
    ctx: typer.Context,
    links_file: Annotated[
        Path,
        typer.Argument(
            help="Links file: a clip URL and its VOD URL with ?t= per line."
        ),
    ],
    force: Annotated[
        bool, typer.Option("--force", help="Replace an existing labels file.")
    ] = False,
) -> None:
    """Write a labels file for one Twitch VOD from its community clips.

    Each line of LINKS_FILE is a clip URL and the clip's "Watch full video"
    URL; `t` marks the clip's end. Clip titles and lengths are read with
    yt-dlp, without downloading, which is against Twitch's terms of service
    (see docs/decisions/0020). Overlapping clips merge into one label
    (docs/decisions/0012).
    """
    settings = Settings()
    build = _context(ctx)
    try:
        links = load_links(links_file)
        vod = links[0].vod
        target = settings.labels_dir / f"{vod.video_id}.json"
        if target.exists() and not force:
            raise LabelsError(f"{target} already exists; pass --force to replace it")
        lookup = build.clip_lookup(settings)
        infos = []
        for index, link in enumerate(links, start=1):
            info = lookup.lookup(link.clip_id)
            console.print(
                f"Clip {index} of {len(links)}: {escape(info.title)} "
                f"({info.duration_seconds:g} s, ends {_fmt(link.end_seconds)})",
                soft_wrap=True,
            )
            infos.append(info)
        labeled = clip_labels(video_path(vod, Path()).name, links, infos)
        save_labels(labeled, target)
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}", soft_wrap=True)
        raise typer.Exit(code=1) from exc

    minutes = sum(label.duration_seconds for label in labeled.labels) / 60
    console.print(f"Clips read: {len(links)}")
    console.print(f"Labels after merging: {len(labeled.labels)}")
    console.print(f"Minutes covered: {minutes:.1f}")
    console.print(f"Saved: {escape(str(target))}", soft_wrap=True)


@app.command()
def review(
    ctx: typer.Context,
    folder: Annotated[
        Path, typer.Argument(help="A folder of drafts with its manifest.json.")
    ],
    video: Annotated[
        Path | None,
        typer.Option(
            "--video",
            help="The source video. Default: <videos dir>/<manifest source>.",
            show_default=False,
        ),
    ] = None,
    port: Annotated[
        int, typer.Option("--port", help="Port on 127.0.0.1 to serve the page on.")
    ] = DEFAULT_PORT,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the page in a browser.")
    ] = True,
) -> None:
    """Review a folder of drafts in a local browser page (docs/decisions/0024).

    The page plays the source video, with a film strip of frames cached
    under the cache dir. Save and Trash write judged labels to
    <labels dir>/picks/<stem>.json and update the manifest; Save re-renders
    a clip whose span changed, with the folder's flags, and Trash deletes
    the clip's file. It is served on the loopback address only, until Ctrl+C.
    """
    settings = Settings()
    build = _context(ctx)
    try:
        manifest = load_manifest(folder)
        if video is None:
            video = settings.videos_dir / manifest.source
        if not video.is_file():
            raise ReviewError(
                f"Video not found: {video}; pass --video with the source video"
            )
        check_manifest_source(manifest, video)
        info = build.probe().probe(video)
        if not info.has_video:
            raise ReviewError(f"{video} has no video stream to show")
        if abs(info.start_seconds) > MAX_START_SECONDS:
            raise ReviewError(
                f"{video} starts at {info.start_seconds:.1f} s, not 0, so the "
                "page's player would not match the transcript; remux it: "
                f'ffmpeg -i "{video}" -map 0:v:0 -map 0:a:0 -c copy '
                f'-movflags +faststart "{video.with_suffix(".remuxed.mp4")}", '
                "then put it in place of the original"
            )
        files = build.review_files()
        labels_path = judged_labels_path(settings.labels_dir, manifest.source)
        labels = files.load_labels(labels_path)
        if labels is not None:
            check_judged_labels(labels, manifest.source, labels_path)
        transcript = None
        if manifest.captions:
            analysis = Path(manifest.analysis)
            if not analysis.is_file():
                raise ReviewError(
                    f"Analysis not found: {analysis}; Save cannot re-render "
                    "with captions"
                )
            result = load_analysis_result(analysis)
            check_source_matches(result, video)
            transcript = result.transcript
        session = ReviewSession(
            folder=folder,
            manifest=manifest,
            video=video,
            duration_seconds=info.duration_seconds,
            frames=build.frames(),
            frames_dir=frames_folder(settings.cache_dir, video),
            labels=labels,
            labels_path=labels_path,
            files=files,
            renderer=build.renderer(settings),
            transcript=transcript,
        )
        build.serve(
            session,
            port,
            open_browser,
            lambda url: console.print(
                f"Review: {url} ({_count(len(manifest.clips), 'clip')}; "
                "Ctrl+C to stop)",
                soft_wrap=True,
            ),
        )
    except ClipperError as exc:
        console.print(f"[red]Error:[/red] {escape(str(exc))}", soft_wrap=True)
        raise typer.Exit(code=1) from exc
