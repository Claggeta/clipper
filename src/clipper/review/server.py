"""The local review page for a folder of drafts. See docs/decisions/0024.

The server binds to the loopback address only and serves a fixed set of
paths: the page's own files, the clips as JSON, the source video with HTTP
range requests so the browser can seek in it, and cached still frames for
the film strip.
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

from clipper.core.interfaces import FrameExtractor
from clipper.domain.errors import FrameError, ReviewError
from clipper.domain.models import RenderManifest

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


@dataclass(frozen=True)
class ReviewSession:
    """What one `clipper review` run serves."""

    folder: Path
    manifest: RenderManifest
    video: Path
    duration_seconds: float
    frames: FrameExtractor
    frames_dir: Path


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


def clips_payload(session: ReviewSession) -> dict[str, object]:
    """The page's view of the manifest: each clip with its range, clamped."""
    manifest = session.manifest
    return {
        "source": manifest.source,
        "vertical": manifest.vertical,
        "captions": manifest.captions,
        "titles": manifest.titles,
        "duration": session.duration_seconds,
        "clips": [
            {
                "rank": clip.rank,
                "start": clip.start_seconds,
                "end": clip.end_seconds,
                "range_start": max(clip.start_seconds - RANGE_SECONDS, 0.0),
                "range_end": min(
                    clip.end_seconds + RANGE_SECONDS, session.duration_seconds
                ),
                "title": clip.title,
                "hook": clip.hook,
                "file": clip.file,
                "error": clip.error,
            }
            for clip in manifest.clips
        ],
    }


def _handler(session: ReviewSession) -> type[BaseHTTPRequestHandler]:
    video_type = mimetypes.guess_type(session.video.name)[0] or "video/mp4"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            try:
                if path in _STATIC:
                    name, content_type = _STATIC[path]
                    body = (files("clipper.review") / "static" / name).read_bytes()
                    self._send(HTTPStatus.OK, content_type, body)
                elif path == "/api/clips":
                    body = json.dumps(clips_payload(session)).encode("utf-8")
                    self._send(HTTPStatus.OK, "application/json", body)
                elif path == "/video":
                    self._send_video()
                elif frame := _FRAME_PATH.fullmatch(path):
                    self._send_frame(int(frame.group(1)))
                else:
                    self._not_found()
            except ConnectionError:
                # The browser drops video requests whenever it seeks.
                pass

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
