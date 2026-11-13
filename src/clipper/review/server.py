"""The local review page for a folder of drafts. See docs/decisions/0024.

The server binds to the loopback address only and serves a fixed set of
paths: the page's own files, the clips as JSON, the source video with HTTP
range requests so the browser can seek in it, and cached still frames for
the film strip, the pauses in speech inside a span (ADR 0029), and the
audio levels, word times, caption chunks and face crop for trimming and
the live preview (ADR 0031). POST paths save,
trash or make a thumbnail of a clip; they answer only the page itself
(see `_trusted`).
"""

import contextlib
import json
import mimetypes
import os
import re
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs

from clipper.core.captions import caption_chunks, preview_layout
from clipper.core.cuts import find_pauses
from clipper.core.framing import plan_face_crop
from clipper.core.interfaces import (
    ClipRenderer,
    FaceTracker,
    FrameExtractor,
    PeaksExtractor,
    ReviewFiles,
)
from clipper.domain.errors import (
    ClipperError,
    FrameError,
    JudgingError,
    PeaksError,
    ReviewError,
)
from clipper.domain.models import (
    AUDIO_SUFFIXES,
    ClipCandidate,
    LabeledVideo,
    ManifestClip,
    RenderManifest,
    Transcript,
)
from clipper.review.store import (
    BadRequest,
    RenderInputs,
    ReviewStore,
    UnknownClip,
    audio_payload,
    edits_payload,
    hand_payload,
    parse_caption_edits,
    parse_save,
    parse_thumbnail,
    parse_trash,
    text_payload,
    zoom_payload,
)

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# How much of the video either side of a clip the page shows (ADR 0024).
RANGE_SECONDS = 120.0
# The latest first timestamp a video may have. The browser's player counts
# from it and ffmpeg from the file's start, so beyond this the page's times
# would not be the transcript's (ADR 0024, start offset amendment). A
# Twitch download started at 64 s; normal files start at 0.
MAX_START_SECONDS = 0.1
_CHUNK = 1024 * 1024

# Path -> (file in static/, content type). Nothing else is served from disk.
_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/keys.js": ("keys.js", "text/javascript; charset=utf-8"),
    "/precision.js": ("precision.js", "text/javascript; charset=utf-8"),
    "/captions.js": ("captions.js", "text/javascript; charset=utf-8"),
    "/frame.js": ("frame.js", "text/javascript; charset=utf-8"),
    "/audio.js": ("audio.js", "text/javascript; charset=utf-8"),
    "/preview.js": ("preview.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
# A film strip frame, at a whole or quarter second (ADR 0031):
# /frames/<video key>/<second>.jpg, or <second>.25, .5 or .75. The key
# names the video (its cache folder), so the browser, which keeps frames by
# URL, never shows one VOD's frame in another's strip: every run is served
# from the same address.
_FRAME_PATH = re.compile(r"/frames/([^/]+)/(\d{1,7}(?:\.25|\.5|\.75)?)\.jpg")
# The maintainer's audio files, for the page to play: /music/<name> and
# /sfx/<name>, listed names only (ADR 0007, audio amendment).
_AUDIO_PATH = re.compile(r"/(music|sfx)/([^/\\]+)")
# The video's audio levels for the waveform: /peaks/<video key>.bin.
_PEAKS_PATH = re.compile(r"/peaks/([^/]+)\.bin")
# A rendered draft listed in the manifest: /clips/<file name> (ADR 0025).
_CLIP_PATH = re.compile(r"/clips/([^/\\]+\.mp4)")
# Frames never change for a given video, so the browser may keep them.
_FRAME_CACHE = "max-age=86400"
# A judgment on one clip: /api/clips/<rank>/save or /trash, or its
# thumbnail (ADR 0030); or a new clip.
_ACTION_PATH = re.compile(r"/api/clips/(\d{1,4})/(save|trash|thumbnail)")
# A thumbnail the manifest lists: /thumbs/<file name> (ADR 0030).
_THUMB_PATH = re.compile(r"/thumbs/([^/\\]+\.jpg)")
_ADD_PATH = "/api/clips/add"
_MAX_BODY = 64 * 1024
# A refused body is read and dropped up to this size before the reply.
_MAX_DRAIN = 1024 * 1024


@dataclass(frozen=True)
class ReviewSession:
    """What one `clipper review` run serves.

    `labels` are the judged labels at `labels_path` when the run started, or
    None if there were none yet; `files` writes them and the manifest.
    `renderer` re-renders a saved clip, with captions from `transcript`
    (None when the folder has no captions); a `face` re-render uses
    `face_tracker` and `frame_size`, the video's width and height (ADR 0025).
    `people` are the names the page offers to mark as on screen (ADR 0024,
    rating amendment). `peaks` writes the waveform's levels to `peaks_path`
    once, on the first request (ADR 0031); without it there is none.
    `music_dir` and `sfx_dir` hold the maintainer's audio files (ADR 0007).
    """

    folder: Path
    manifest: RenderManifest
    video: Path
    duration_seconds: float
    frames: FrameExtractor
    frames_dir: Path
    labels: LabeledVideo | None
    labels_path: Path
    files: ReviewFiles
    renderer: ClipRenderer
    transcript: Transcript | None
    face_tracker: FaceTracker | None = None
    frame_size: tuple[int, int] | None = None
    people: tuple[str, ...] = ()
    peaks: PeaksExtractor | None = None
    peaks_path: Path | None = None
    music_dir: Path | None = None
    sfx_dir: Path | None = None


def frames_folder(cache_dir: Path, video: Path) -> Path:
    """Where one video's film strip frames are cached: by name and size."""
    return cache_dir / "frames" / f"{video.stem}-{video.stat().st_size}"


def peaks_file(cache_dir: Path, video: Path) -> Path:
    """Where one video's audio levels are cached, named as its frames are."""
    return cache_dir / "peaks" / f"{video.stem}-{video.stat().st_size}.bin"


def frames_key(session: ReviewSession) -> str:
    """The video's part of a frame URL: its cache folder's name."""
    return session.frames_dir.name


def _frame_name(seconds: float) -> str:
    """`12` for a whole second, as before quarter seconds; else `12.25`."""
    return str(int(seconds)) if float(seconds).is_integer() else f"{seconds:g}"


def cached_frame(session: ReviewSession, second: float) -> Path:
    """The frame at `second`, made once and then read from the cache.

    The extractor writes to a name unique to the thread, then the file is
    renamed into place, so two requests for one frame never half-write it.
    On Windows the rename fails while another request reads the frame; the
    frame is there, so that is not an error.
    """
    name = _frame_name(second)
    path = session.frames_dir / f"{name}.jpg"
    if path.is_file():
        return path
    temp = path.with_name(f"{name}.{threading.get_ident()}.part.jpg")
    try:
        session.frames.extract(session.video, float(second), temp)
        try:
            os.replace(temp, path)
        except PermissionError:
            if not path.is_file():
                raise
    finally:
        temp.unlink(missing_ok=True)
    return path


_PEAKS_LOCK = threading.Lock()


def cached_peaks(session: ReviewSession) -> bytes:
    """The video's audio levels, made once and then read from the cache.

    One request makes them at a time (a long VOD takes tens of seconds);
    another waits and then reads the file. Written through a `.part` file
    and renamed into place. Raises `PeaksError` when the session has none.
    """
    if session.peaks is None or session.peaks_path is None:
        raise PeaksError("No waveform for this run")
    path = session.peaks_path
    with _PEAKS_LOCK:
        if not path.is_file():
            temp = path.with_name(f"{path.stem}.part.bin")
            try:
                session.peaks.extract(session.video, temp)
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)
    return path.read_bytes()


class UnsatisfiableRange(Exception):
    """The requested byte range lies outside the file (HTTP 416)."""


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The inclusive byte range a `Range` header asks for, or None for all.

    Only a single `bytes=` range is honoured. A missing, malformed or
    multi-range header is ignored, as HTTP allows, and the whole file is
    sent. A range starting at or past the end, or an empty suffix, raises
    `UnsatisfiableRange`. An end past the file is cut to the last byte.
    """
    if header is None:
        return None
    unit, _, spec = header.strip().partition("=")
    if unit.strip().lower() != "bytes" or "," in spec:
        return None
    first, dash, last = spec.strip().partition("-")
    if not dash or not (first + last).isdigit():
        return None
    if not first:
        suffix = int(last)
        if suffix == 0 or size == 0:
            raise UnsatisfiableRange
        return max(size - suffix, 0), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if last and end < start:
        return None
    if start >= size:
        raise UnsatisfiableRange
    return start, min(end, size - 1)


def make_store(session: ReviewSession) -> ReviewStore:
    """The run's store, starting from the session's manifest and labels."""
    return ReviewStore(
        session.folder,
        session.manifest,
        session.labels,
        session.labels_path,
        session.files,
        RenderInputs(
            session.video,
            session.renderer,
            session.transcript,
            session.face_tracker,
            session.frame_size,
            session.music_dir,
            session.sfx_dir,
        ),
    )


def clip_payload(
    session: ReviewSession, store: ReviewStore, clip: ManifestClip
) -> dict[str, object]:
    """One clip as the page sees it: its range, clamped, and its judgment.

    `start` and `end` are what the file covers; `draft_start` and `draft_end`
    are the detector's span, the same until a re-render. `model_title` is
    the model's title, kept once the page has written the clip. `variants`
    are the rendered styles and `style` the clip's own, its first (ADR 0025).
    `cuts` are the ranges left out of the file (ADR 0029), and `texts`
    the lines burned over it (ADR 0030).
    """
    draft_start, draft_end = clip.draft_span
    return {
        "rank": clip.rank,
        "start": clip.start_seconds,
        "end": clip.end_seconds,
        "draft_start": draft_start,
        "draft_end": draft_end,
        "model_title": clip.model_title if clip.reviewed else clip.title,
        "added": clip.added,
        "range_start": max(clip.start_seconds - RANGE_SECONDS, 0.0),
        "range_end": min(clip.end_seconds + RANGE_SECONDS, session.duration_seconds),
        "title": clip.title,
        "hook": clip.hook,
        "file": clip.file,
        "variants": [{"style": v.style, "file": v.file} for v in clip.variants],
        "style": clip.variants[0].style if clip.variants else None,
        "error": clip.error,
        "reviewed": clip.reviewed,
        "cuts": [list(cut) for cut in clip.cuts],
        "texts": [text_payload(text) for text in clip.texts],
        "thumbnail": clip.thumbnail,
        "caption_edits": edits_payload(clip.caption_edits),
        "zooms": [zoom_payload(zoom) for zoom in clip.zooms],
        "hand_crops": [hand_payload(hand) for hand in clip.hand_crops],
        "audio": audio_payload(clip.audio),
        "judgment": store.judgment(clip),
    }


def clips_payload(session: ReviewSession, store: ReviewStore) -> dict[str, object]:
    """The page's view of the manifest and the judgments so far."""
    manifest = store.manifest
    return {
        "source": manifest.source,
        "vertical": manifest.vertical,
        "captions": manifest.captions,
        "titles": manifest.titles,
        "duration": session.duration_seconds,
        "frames_key": frames_key(session),
        "people": list(session.people),
        "layout": preview_layout(manifest.vertical),
        "clips": [clip_payload(session, store, clip) for clip in manifest.clips],
    }


def _span_query(session: ReviewSession, query: str) -> tuple[float, float]:
    """`?start=&end=` in seconds, inside the video; else `BadRequest`."""
    values = parse_qs(query)
    try:
        start = float(values["start"][0])
        end = float(values["end"][0])
    except (KeyError, ValueError) as exc:
        raise BadRequest("give start and end in seconds") from exc
    if not 0 <= start < end <= session.duration_seconds:
        raise BadRequest("the span must be inside the video")
    return start, end


def pauses_payload(session: ReviewSession, query: str) -> dict[str, object]:
    """The proposed cuts for the pauses in speech inside `?start=&end=`, in
    source seconds (ADR 0029). Raises `BadRequest` for a bad span and
    `ReviewError` when the folder has no transcript."""
    start, end = _span_query(session, query)
    if session.transcript is None:
        raise ReviewError("This folder has no transcript to find pauses in")
    cuts = find_pauses(session.transcript, start, end)
    return {"cuts": [list(cut) for cut in cuts]}


def _cuts_query(
    query: str, start: float, end: float
) -> tuple[tuple[float, float], ...]:
    """`&cuts=a:b,c:d` in source seconds, each inside the span and in order;
    none when absent. Raises `BadRequest` otherwise."""
    text = parse_qs(query).get("cuts", [""])[0]
    if not text:
        return ()
    try:
        cuts = tuple(
            (float(a), float(b))
            for a, b in (part.split(":") for part in text.split(","))
        )
    except ValueError as exc:
        raise BadRequest("give cuts as start:end pairs, comma separated") from exc
    previous = start
    for cut_start, cut_end in cuts:
        if not previous < cut_start < cut_end < end:
            raise BadRequest("cuts must be in order and inside the span")
        previous = cut_end
    return cuts


def captions_payload(session: ReviewSession, query: str) -> dict[str, object]:
    """The caption chunks `build_ass` would burn into `?start=&end=` with
    `&cuts=` and `&edits=` (caption edits as JSON, ADR 0017), in source
    seconds, for the live preview (ADR 0031). Raises `BadRequest` for a bad
    span, cuts or edits and `ReviewError` without a transcript."""
    start, end = _span_query(session, query)
    cuts = _cuts_query(query, start, end)
    edits_text = parse_qs(query).get("edits", [""])[0]
    try:
        edits = parse_caption_edits(json.loads(edits_text)) if edits_text else None
    except json.JSONDecodeError as exc:
        raise BadRequest("give edits as JSON") from exc
    if session.transcript is None:
        raise ReviewError("This folder has no captions to preview")
    candidate = ClipCandidate(
        start_seconds=start, end_seconds=end, score=0.0, reasons=("preview",)
    )
    chunks = caption_chunks(session.transcript, candidate, cuts, edits)
    return {"chunks": [[list(word) for word in chunk] for chunk in chunks]}


def crop_payload(session: ReviewSession, query: str) -> dict[str, object]:
    """The face style's crop for `?start=&end=`, as a re-render would frame
    it (ADR 0025), with times from the span's start; `crop` is None, with a
    note, when no face is found. Tracking a new span takes a while; it is
    cached. Raises `BadRequest` for a bad span and `ReviewError` when the
    run has no face tracker or frame size."""
    start, end = _span_query(session, query)
    if session.face_tracker is None or session.frame_size is None:
        raise ReviewError("This run cannot find faces to frame")
    track = session.face_tracker.track(session.video, start, end)
    crop = plan_face_crop(track, *session.frame_size)
    if crop is None:
        return {"crop": None, "note": "No face in this span; it is framed as fit."}
    return {
        "crop": {
            "width": crop.width,
            "height": crop.height,
            "points": [list(point) for point in crop.points],
        },
        "frame": list(session.frame_size),
    }


def words_payload(session: ReviewSession, query: str) -> dict[str, object]:
    """The words with times that overlap `?start=&end=`, as `[start, end,
    text]` in source seconds, for snapping and the words lane (ADR 0031).
    Segments without word times give none. Raises `BadRequest` for a bad
    span and `ReviewError` when the folder has no transcript."""
    start, end = _span_query(session, query)
    if session.transcript is None:
        raise ReviewError("This folder has no transcript to snap to")
    words = [
        [word.start_seconds, word.end_seconds, word.text.strip()]
        for segment in session.transcript.segments
        if segment.end_seconds > start and segment.start_seconds < end
        for word in segment.words
        if word.end_seconds > start and word.start_seconds < end
    ]
    return {"words": words}


def audio_files(folder: Path | None) -> list[str]:
    """The audio files in a folder, by name, which are all the page may
    pick or fetch (ADR 0007, audio amendment)."""
    if folder is None or not folder.is_dir():
        return []
    return sorted(
        path.name
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
    )


def _listed_thumbnails(store: ReviewStore) -> set[str]:
    """The thumbnails the manifest lists, which are all the page may fetch."""
    return {c.thumbnail for c in store.manifest.clips if c.thumbnail is not None}


def _listed_files(store: ReviewStore) -> set[str]:
    """The draft files the manifest lists, which are all the page may fetch."""
    names = {clip.file for clip in store.manifest.clips if clip.file is not None}
    names |= {v.file for clip in store.manifest.clips for v in clip.variants}
    return names


def _handler(session: ReviewSession) -> type[BaseHTTPRequestHandler]:
    video_type = mimetypes.guess_type(session.video.name)[0] or "video/mp4"
    store = make_store(session)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path, _, query = self.path.partition("?")
            try:
                if path in _STATIC:
                    name, content_type = _STATIC[path]
                    body = (files("clipper.review") / "static" / name).read_bytes()
                    self._send(HTTPStatus.OK, content_type, body)
                elif path == "/api/clips":
                    self._send_json(HTTPStatus.OK, clips_payload(session, store))
                elif path == "/api/pauses":
                    self._send_span_reply(pauses_payload, query)
                elif path == "/api/words":
                    self._send_span_reply(words_payload, query)
                elif path == "/api/audio":
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "music": audio_files(session.music_dir),
                            "sfx": audio_files(session.sfx_dir),
                        },
                    )
                elif (sound := _AUDIO_PATH.fullmatch(path)) and self._send_audio(
                    sound.group(1), sound.group(2)
                ):
                    pass
                elif path == "/api/captions":
                    self._send_span_reply(captions_payload, query)
                elif path == "/api/crop":
                    self._send_span_reply(crop_payload, query)
                elif path == "/video":
                    self._send_file(session.video, video_type)
                elif (draft := _CLIP_PATH.fullmatch(path)) and draft.group(
                    1
                ) in _listed_files(store):
                    self._send_file(session.folder / draft.group(1), "video/mp4")
                elif (thumb := _THUMB_PATH.fullmatch(path)) and thumb.group(
                    1
                ) in _listed_thumbnails(store):
                    self._send_file(session.folder / thumb.group(1), "image/jpeg")
                elif (frame := _FRAME_PATH.fullmatch(path)) and frame.group(
                    1
                ) == frames_key(session):
                    self._send_frame(float(frame.group(2)))
                elif (peaks := _PEAKS_PATH.fullmatch(path)) and peaks.group(
                    1
                ) == frames_key(session):
                    self._send_peaks()
                else:
                    self._not_found()
            except ConnectionError:
                # The browser drops video requests whenever it seeks.
                pass

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            action = _ACTION_PATH.fullmatch(path)
            if path == _ADD_PATH:
                rank, verb = 0, "add"
            elif action is not None:
                rank, verb = int(action.group(1)), action.group(2)
            else:
                self._not_found()
                return
            if not self._trusted():
                self._send_error(HTTPStatus.FORBIDDEN, "Only the review page may write")
                return
            reply: dict[str, object]
            try:
                data = self._read_json()
                if verb == "thumbnail":
                    clip, note = store.thumbnail(
                        rank, parse_thumbnail(data, session.duration_seconds)
                    )
                    reply = clip_payload(session, store, clip) | {"note": note}
                elif verb == "trash":
                    trashed = store.trash(rank, parse_trash(data))
                    if trashed is None:
                        reply = {"removed": rank}
                    else:
                        reply = clip_payload(session, store, trashed)
                else:
                    request = parse_save(data, session.duration_seconds, session.people)
                    if verb == "add":
                        saved = store.add(request)
                    else:
                        saved = store.save(rank, request)
                    reply = clip_payload(session, store, saved.clip) | {
                        "render_error": saved.render_error,
                        "warning": saved.warning,
                    }
            except BadRequest as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            except UnknownClip:
                self._send_error(HTTPStatus.NOT_FOUND, f"No clip ranked {rank}")
            except JudgingError as exc:
                self._send_error(HTTPStatus.CONFLICT, str(exc))
            except (ClipperError, OSError) as exc:
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            else:
                self._send_json(HTTPStatus.OK, reply)

        def _send_span_reply(
            self,
            payload: Callable[[ReviewSession, str], dict[str, object]],
            query: str,
        ) -> None:
            try:
                reply = payload(session, query)
            except BadRequest as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            except ReviewError as exc:
                self._send_error(HTTPStatus.CONFLICT, str(exc))
            except ClipperError as exc:
                # Face tracking can fail (ADR 0025); say why.
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            else:
                self._send_json(HTTPStatus.OK, reply)

        def _trusted(self) -> bool:
            """Whether a POST comes from the page itself.

            Any site open in the browser can POST to the loopback address. A
            JSON content type cannot be sent cross-site without a preflight,
            which this server never answers; the Origin must be this page;
            and the Host must be the loopback address, against DNS rebinding.
            """
            address = self.server.server_address
            if not isinstance(address, tuple):
                return False
            port = address[1]
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            host = self.headers.get("Host", "")
            content_type = self.headers.get("Content-Type", "")
            return (
                host in hosts
                and self.headers.get("Origin") == f"http://{host}"
                and content_type.split(";")[0].strip() == "application/json"
            )

        def _read_json(self) -> object:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError as exc:
                raise BadRequest("a Content-Length is needed") from exc
            if not 0 <= length <= _MAX_BODY:
                self._drain(length)
                raise BadRequest(f"the body must be at most {_MAX_BODY} bytes")
            try:
                return json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError) as exc:
                raise BadRequest("the body is not valid JSON") from exc

        def _drain(self, length: int) -> None:
            """Read and drop up to `_MAX_DRAIN` bytes of a body refused for its
            size, so the client is not cut off mid-send: on Windows that
            aborts its connection before it reads the 400."""
            remaining = min(max(length, 0), _MAX_DRAIN)
            while remaining > 0:
                chunk = self.rfile.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)

        def _send_json(self, status: HTTPStatus, data: object) -> None:
            body = json.dumps(data).encode("utf-8")
            self._send(status, "application/json", body)

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json(status, {"error": message})

        def _send(
            self,
            status: HTTPStatus,
            content_type: str,
            body: bytes,
            cache: str = "no-store",
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def _not_found(self) -> None:
            self._send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")

        def _send_audio(self, kind: str, name: str) -> bool:
            """Send a listed music or sound effect file; False if not listed."""
            folder = session.music_dir if kind == "music" else session.sfx_dir
            if folder is None or name not in audio_files(folder):
                return False
            content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            self._send_file(folder / name, content_type)
            return True

        def _send_peaks(self) -> None:
            try:
                body = cached_peaks(session)
            except (PeaksError, OSError) as exc:
                message = str(exc).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, "text/plain", message)
                return
            self._send(
                HTTPStatus.OK, "application/octet-stream", body, cache=_FRAME_CACHE
            )

        def _send_frame(self, second: float) -> None:
            if second >= session.duration_seconds:
                self._not_found()
                return
            try:
                body = cached_frame(session, second).read_bytes()
            except (FrameError, OSError) as exc:
                message = str(exc).encode("utf-8")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, "text/plain", message)
                return
            self._send(HTTPStatus.OK, "image/jpeg", body, cache=_FRAME_CACHE)

        def _send_file(self, path: Path, content_type: str) -> None:
            """Send a video file, or the byte range the player asked for."""
            try:
                size = path.stat().st_size
            except OSError:
                self._not_found()
                return
            try:
                byte_range = parse_range(self.headers.get("Range"), size)
            except UnsatisfiableRange:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = byte_range if byte_range else (0, size - 1)
            length = end - start + 1 if size else 0
            if byte_range:
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            # A re-render can write new content under the same name.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with path.open("rb") as source:
                source.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = source.read(min(_CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def log_message(self, format: str, *args: object) -> None:
            # One line per request would bury the console; nothing is logged.
            pass

    return Handler


class _Server(ThreadingHTTPServer):
    # http.server sets SO_REUSEADDR, which on Windows lets a second server
    # bind a port already in use, silently. Elsewhere it only allows a quick
    # restart after Ctrl+C.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def make_server(session: ReviewSession, port: int) -> ThreadingHTTPServer:
    """Bind the review server on the loopback address; port 0 picks a free one."""
    try:
        server = _Server((HOST, port), _handler(session))
    except OSError as exc:
        raise ReviewError(f"Cannot listen on {HOST}:{port}: {exc}") from exc
    return server


def serve(
    session: ReviewSession,
    port: int,
    open_browser: bool,
    announce: Callable[[str], None],
) -> None:
    """Serve the review page until Ctrl+C. `announce` is given the page's URL."""
    with make_server(session, port) as server:
        url = f"http://{HOST}:{server.server_address[1]}/"
        announce(url)
        if open_browser:
            webbrowser.open(url)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
