"""The local review page for a folder of drafts. See docs/decisions/0024.

The server binds to the loopback address only and serves a fixed set of
paths: the page's own files, the clips as JSON, the source video with HTTP
range requests so the browser can seek in it, and cached still frames for
the film strip. Two POST paths save or trash a clip; they answer only the
page itself (see `_trusted`).
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

from clipper.core.interfaces import ClipRenderer, FrameExtractor, ReviewFiles
from clipper.domain.errors import ClipperError, FrameError, JudgingError, ReviewError
from clipper.domain.models import (
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
    parse_save,
    parse_trash,
)

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# How much of the video either side of a clip the page shows (ADR 0024).
RANGE_SECONDS = 120.0
_CHUNK = 1024 * 1024

# Path -> (file in static/, content type). Nothing else is served from disk.
_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
# A film strip frame, at a whole second: /frames/<second>.jpg.
_FRAME_PATH = re.compile(r"/frames/(\d{1,7})\.jpg")
# Frames never change for a given video, so the browser may keep them.
_FRAME_CACHE = "max-age=86400"
# A judgment on one clip: /api/clips/<rank>/save or /trash; or a new clip.
_ACTION_PATH = re.compile(r"/api/clips/(\d{1,4})/(save|trash)")
_ADD_PATH = "/api/clips/add"
_MAX_BODY = 64 * 1024


@dataclass(frozen=True)
class ReviewSession:
    """What one `clipper review` run serves.

    `labels` are the judged labels at `labels_path` when the run started, or
    None if there were none yet; `files` writes them and the manifest.
    `renderer` re-renders a saved clip, with captions from `transcript`
    (None when the folder has no captions).
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


def frames_folder(cache_dir: Path, video: Path) -> Path:
    """Where one video's film strip frames are cached: by name and size."""
    return cache_dir / "frames" / f"{video.stem}-{video.stat().st_size}"


def cached_frame(session: ReviewSession, second: int) -> Path:
    """The frame at `second`, made once and then read from the cache.

    The extractor writes to a name unique to the thread, then the file is
    renamed into place, so two requests for one frame never half-write it.
    On Windows the rename fails while another request reads the frame; the
    frame is there, so that is not an error.
    """
    path = session.frames_dir / f"{second}.jpg"
    if path.is_file():
        return path
    temp = path.with_name(f"{second}.{threading.get_ident()}.part.jpg")
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
        RenderInputs(session.video, session.renderer, session.transcript),
    )


def clip_payload(
    session: ReviewSession, store: ReviewStore, clip: ManifestClip
) -> dict[str, object]:
    """One clip as the page sees it: its range, clamped, and its judgment.

    `start` and `end` are what the file covers; `draft_start` and `draft_end`
    are the detector's span, the same until a re-render. `model_title` is
    the model's title, kept once the page has written the clip.
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
        "error": clip.error,
        "reviewed": clip.reviewed,
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
        "clips": [clip_payload(session, store, clip) for clip in manifest.clips],
    }


def _handler(session: ReviewSession) -> type[BaseHTTPRequestHandler]:
    video_type = mimetypes.guess_type(session.video.name)[0] or "video/mp4"
    store = make_store(session)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            try:
                if path in _STATIC:
                    name, content_type = _STATIC[path]
                    body = (files("clipper.review") / "static" / name).read_bytes()
                    self._send(HTTPStatus.OK, content_type, body)
                elif path == "/api/clips":
                    self._send_json(HTTPStatus.OK, clips_payload(session, store))
                elif path == "/video":
                    self._send_video()
                elif frame := _FRAME_PATH.fullmatch(path):
                    self._send_frame(int(frame.group(1)))
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
                if verb == "trash":
                    trashed = store.trash(rank, parse_trash(data))
                    if trashed is None:
                        reply = {"removed": rank}
                    else:
                        reply = clip_payload(session, store, trashed)
                else:
                    request = parse_save(data, session.duration_seconds)
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
                raise BadRequest(f"the body must be at most {_MAX_BODY} bytes")
            try:
                return json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError) as exc:
                raise BadRequest("the body is not valid JSON") from exc

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

        def _send_frame(self, second: int) -> None:
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

        def _send_video(self) -> None:
            size = session.video.stat().st_size
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
            self.send_header("Content-Type", video_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with session.video.open("rb") as source:
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
