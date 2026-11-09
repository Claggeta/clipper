from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from clipper.adapters.detection.heuristic import HeuristicClipDetector
from clipper.adapters.detection.llm import LlmClipDetector
from clipper.adapters.detection.ollama import OllamaClient
from clipper.adapters.download.ytdlp import YtDlpClipLookup, YtDlpDownloader
from clipper.adapters.media.ffmpeg import FfmpegAudioExtractor
from clipper.adapters.media.ffprobe import FfprobeMediaProbe
from clipper.adapters.media.frames import FfmpegFrameExtractor
from clipper.adapters.rendering.ffmpeg import FfmpegClipRenderer
from clipper.adapters.titles.ollama import OllamaTitleWriter
from clipper.adapters.transcription.caching import CachingTranscriber
from clipper.adapters.transcription.faster_whisper import FasterWhisperTranscriber
from clipper.adapters.transcription.video import VideoTranscriber
from clipper.core.config import Settings
from clipper.core.interfaces import (
    ClipDetector,
    ClipLookup,
    ClipRenderer,
    Downloader,
    FrameExtractor,
    MediaProbe,
    TitleWriter,
    Transcriber,
)
from clipper.domain.errors import DetectionError, TitleError
from clipper.review.server import ReviewSession
from clipper.review.server import serve as serve_review


class DetectorName(StrEnum):
    """Detectors selectable with --detector (ADR 0013)."""

    HEURISTIC = "heuristic"
    LLM = "llm"


def build_transcriber(settings: Settings) -> Transcriber:
    """Compose the real transcriber."""
    whisper = FasterWhisperTranscriber(model_size=settings.whisper_model)
    return CachingTranscriber(
        VideoTranscriber(FfmpegAudioExtractor(), whisper),
        settings.cache_dir,
        namespace=whisper.name,
    )


def build_detector(settings: Settings, name: DetectorName) -> ClipDetector:
    """Compose the chosen detector."""
    match name:
        case DetectorName.HEURISTIC:
            return HeuristicClipDetector(top_n=settings.top_n)
        case DetectorName.LLM:
            if not settings.llm_model:
                raise DetectionError(
                    "Set CLIPPER_LLM_MODEL to an Ollama model name to use "
                    "--detector llm"
                )
            client = OllamaClient(settings.llm_url, settings.llm_model)
            return LlmClipDetector(client, top_n=settings.top_n)


def build_renderer(settings: Settings) -> ClipRenderer:
    """Compose the real clip renderer."""
    return FfmpegClipRenderer()


def build_probe() -> MediaProbe:
    """Compose the real media probe."""
    return FfprobeMediaProbe()


def build_frames() -> FrameExtractor:
    """Compose the frame extractor for the review page's film strip."""
    return FfmpegFrameExtractor()


def build_title_writer(settings: Settings) -> TitleWriter:
    """Compose the title writer, on the detector's model (ADR 0018)."""
    if not settings.llm_model:
        raise TitleError(
            "Set CLIPPER_LLM_MODEL to an Ollama model name to use --titles"
        )
    return OllamaTitleWriter(OllamaClient(settings.llm_url, settings.llm_model))


def build_downloader(settings: Settings) -> Downloader:
    """Compose the real downloader (ADR 0020)."""
    return YtDlpDownloader()


def build_clip_lookup(settings: Settings) -> ClipLookup:
    """Compose the real clip lookup (ADR 0012, Twitch clips amendment)."""
    return YtDlpClipLookup()


@dataclass(frozen=True)
class AppContext:
    """How the commands build their adapters. See docs/decisions/0013.

    `cli/` is the composition root. The fields are builders, not built
    objects, so each command builds only what it uses, when it uses it.
    Tests pass their own through `CliRunner().invoke(..., obj=AppContext(...))`;
    nothing at runtime selects a test double (ADR 0006).
    """

    transcriber: Callable[[Settings], Transcriber] = build_transcriber
    detector: Callable[[Settings, DetectorName], ClipDetector] = build_detector
    renderer: Callable[[Settings], ClipRenderer] = build_renderer
    probe: Callable[[], MediaProbe] = build_probe
    frames: Callable[[], FrameExtractor] = build_frames
    title_writer: Callable[[Settings], TitleWriter] = build_title_writer
    downloader: Callable[[Settings], Downloader] = build_downloader
    clip_lookup: Callable[[Settings], ClipLookup] = build_clip_lookup
    # Serves the review page until Ctrl+C (ADR 0024); tests pass a stub so
    # `review` does not block.
    serve: Callable[[ReviewSession, int, bool, Callable[[str], None]], None] = (
        serve_review
    )
