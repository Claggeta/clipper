import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

import clipper.review.server as server_module
from clipper.domain.errors import ClipperError, ReviewError
from clipper.domain.models import ManifestClip, RenderManifest
from clipper.review.server import (
    RANGE_SECONDS,
    ReviewSession,
    UnsatisfiableRange,
    clips_payload,
    make_server,
    parse_range,
)

# Constructed manifest and video bytes, not the output of a real run.


def _clip(rank: int, start: float, end: float, **fields: object) -> ManifestClip:
    values: dict[str, object] = {
        "rank": rank,
        "start_seconds": start,
        "end_seconds": end,
        "score": 0.5,
        "reasons": ("test",),
        "file": f"talk_{rank}.mp4",
        **fields,
    }
    return ManifestClip.model_validate(values)


MANIFEST = RenderManifest(
    source="talk.mp4",
    analysis="data/output/talk.json",
    detector="heuristic",
    vertical=True,
    captions=True,
    titles=False,
    clips=(
        _clip(1, 60.0, 105.0, title="A title", hook="A hook."),
        _clip(2, 400.0, 440.0),
        _clip(3, 500.0, 530.0, file=None, error="ffmpeg failed"),
    ),
)
VIDEO = bytes(range(256)) * 4  # 1,024 bytes


def _session(tmp_path: Path) -> ReviewSession:
    video = tmp_path / "talk.mp4"
    video.write_bytes(VIDEO)
    return ReviewSession(
        folder=tmp_path, manifest=MANIFEST, video=video, duration_seconds=600.0
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("bytes=0-99", (0, 99)),
        ("bytes=100-", (100, 1023)),
        ("bytes=-24", (1000, 1023)),
        ("bytes=-5000", (0, 1023)),
        ("bytes=1000-5000", (1000, 1023)),
        ("Bytes = 5-9", (5, 9)),
        ("bytes=0-0", (0, 0)),
        # Ignored, so the whole file is sent.
        ("bytes=0-9,20-29", None),
        ("items=0-9", None),
        ("bytes=", None),
        ("bytes=-", None),
        ("bytes=a-9", None),
        ("bytes=9-5", None),
        ("bytes=5", None),
    ],
)
def test_parse_range(header: str | None, expected: tuple[int, int] | None) -> None:
    assert parse_range(header, 1024) == expected


@pytest.mark.parametrize(
    ("header", "size"),
    [("bytes=1024-", 1024), ("bytes=5000-6000", 1024), ("bytes=-0", 1024),
     ("bytes=0-", 0), ("bytes=-5", 0)],
)  # fmt: skip
def test_unsatisfiable_ranges(header: str, size: int) -> None:
    with pytest.raises(UnsatisfiableRange):
        parse_range(header, size)


def test_payload_clamps_each_range_to_the_video(tmp_path: Path) -> None:
    payload = clips_payload(_session(tmp_path))

    assert RANGE_SECONDS == 120.0
    assert payload["source"] == "talk.mp4"
    assert (payload["vertical"], payload["captions"], payload["titles"]) == (
        True,
        True,
        False,
    )
    assert payload["duration"] == 600.0
    clips = payload["clips"]
    assert isinstance(clips, list)
    assert [(c["range_start"], c["range_end"]) for c in clips] == [
        (0.0, 225.0),
        (280.0, 560.0),
        (380.0, 600.0),
    ]
    assert clips[0]["title"] == "A title"
    assert clips[0]["hook"] == "A hook."
    assert clips[2]["file"] is None
    assert clips[2]["error"] == "ffmpeg failed"


@pytest.fixture
def base_url(tmp_path: Path) -> Iterator[str]:
    server = make_server(_session(tmp_path), 0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(
    url: str, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


@pytest.mark.parametrize(
    ("path", "content_type", "marker"),
    [
        ("/", "text/html", b"<video"),
        ("/app.js", "text/javascript", b"/api/clips"),
        ("/style.css", "text/css", b"--bg"),
    ],
)
def test_serves_the_page_files(
    base_url: str, path: str, content_type: str, marker: bytes
) -> None:
    status, headers, body = _get(base_url + path)

    assert status == 200
    assert headers["Content-Type"].startswith(content_type)
    assert marker in body


def test_serves_the_clips_as_json(base_url: str) -> None:
    status, headers, body = _get(base_url + "/api/clips")

    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert [clip["rank"] for clip in json.loads(body)["clips"]] == [1, 2, 3]


def test_serves_the_whole_video_without_a_range(base_url: str) -> None:
    status, headers, body = _get(base_url + "/video")

    assert status == 200
    assert headers["Content-Type"] == "video/mp4"
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Content-Length"] == "1024"
    assert body == VIDEO


def test_serves_part_of_the_video_for_a_range(base_url: str) -> None:
    status, headers, body = _get(base_url + "/video", {"Range": "bytes=1000-"})

    assert status == 206
    assert headers["Content-Range"] == "bytes 1000-1023/1024"
    assert headers["Content-Length"] == "24"
    assert body == VIDEO[1000:]


def test_refuses_a_range_past_the_end(base_url: str) -> None:
    status, headers, body = _get(base_url + "/video", {"Range": "bytes=2000-"})

    assert status == 416
    assert headers["Content-Range"] == "bytes */1024"
    assert body == b""


@pytest.mark.parametrize(
    "path", ["/index.html", "/static/app.js", "/talk.mp4", "/../pyproject.toml"]
)
def test_other_paths_are_not_found(base_url: str, path: str) -> None:
    assert _get(base_url + path)[0] == 404


def test_query_strings_are_ignored(base_url: str) -> None:
    assert _get(base_url + "/api/clips?x=1")[0] == 200


def test_serve_announces_the_url_and_opens_the_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    announced: list[str] = []

    def interrupt(self: object, poll_interval: float = 0.5) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(server_module._Server, "serve_forever", interrupt)
    monkeypatch.setattr("webbrowser.open", opened.append)

    server_module.serve(_session(tmp_path), 0, True, announced.append)

    [url] = announced
    assert url.startswith("http://127.0.0.1:")
    assert url.endswith("/")
    assert opened == [url]


def test_serve_can_leave_the_browser_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    def interrupt(self: object, poll_interval: float = 0.5) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(server_module._Server, "serve_forever", interrupt)
    monkeypatch.setattr("webbrowser.open", opened.append)

    server_module.serve(_session(tmp_path), 0, False, lambda url: None)

    assert opened == []


def test_a_busy_port_is_a_review_error(tmp_path: Path) -> None:
    session = _session(tmp_path)
    with make_server(session, 0) as server:
        port = server.server_address[1]
        with pytest.raises(ReviewError, match=f"Cannot listen on 127.0.0.1:{port}"):
            make_server(session, port)
    assert issubclass(ReviewError, ClipperError)
