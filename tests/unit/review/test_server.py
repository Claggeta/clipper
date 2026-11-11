import http.client
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import clipper.review.server as server_module
from clipper.adapters.rendering.fake import FakeClipRenderer
from clipper.adapters.results.labels_json import load_labels
from clipper.adapters.results.manifest_json import load_manifest
from clipper.adapters.results.review_files import JsonReviewFiles
from clipper.domain.errors import ClipperError, FrameError, ReviewError
from clipper.domain.models import (
    ClipCandidate,
    ManifestClip,
    RenderManifest,
    Segment,
    Transcript,
)
from clipper.review.server import (
    RANGE_SECONDS,
    ReviewSession,
    UnsatisfiableRange,
    cached_frame,
    clips_payload,
    frames_folder,
    make_server,
    make_store,
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
# Speech in clip 1 only; segments without word times, so whole-segment cues.
TRANSCRIPT = Transcript(
    segments=(Segment(start_seconds=62.0, end_seconds=70.0, text="Hello there."),)
)


class FakeFrames:
    """Writes `frame <seconds>` as the JPEG, or half a file and fails."""

    def __init__(self, error: bool = False) -> None:
        self.error = error
        self.calls: list[tuple[Path, float]] = []

    def extract(self, video_path: Path, seconds: float, output_path: Path) -> Path:
        self.calls.append((video_path, seconds))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.error:
            output_path.write_bytes(b"half a fr")
            raise FrameError("ffmpeg failed (exit 1): bad frame")
        output_path.write_bytes(f"frame {seconds:g}".encode())
        return output_path


def _session(
    tmp_path: Path,
    frames: FakeFrames | None = None,
    renderer: FakeClipRenderer | None = None,
) -> ReviewSession:
    video = tmp_path / "talk.mp4"
    video.write_bytes(VIDEO)
    folder = tmp_path / "clips" / "talk"
    folder.mkdir(parents=True, exist_ok=True)
    for clip in MANIFEST.clips:
        if clip.file is not None:
            (folder / clip.file).write_bytes(b"clip")
    return ReviewSession(
        folder=folder,
        manifest=MANIFEST,
        video=video,
        duration_seconds=600.0,
        frames=frames or FakeFrames(),
        frames_dir=tmp_path / "cache" / "frames" / "talk-1024",
        labels=None,
        labels_path=tmp_path / "labels" / "picks" / "talk.json",
        files=JsonReviewFiles(),
        renderer=renderer or FakeClipRenderer(),
        transcript=TRANSCRIPT,
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
    session = _session(tmp_path)
    payload = clips_payload(session, make_store(session))

    assert RANGE_SECONDS == 120.0
    assert payload["source"] == "talk.mp4"
    assert (payload["vertical"], payload["captions"], payload["titles"]) == (
        True,
        True,
        False,
    )
    assert payload["duration"] == 600.0
    # Frame URLs name the video, by its cache folder (see _FRAME_PATH).
    assert payload["frames_key"] == "talk-1024"
    clips = payload["clips"]
    assert isinstance(clips, list)
    assert [(c["range_start"], c["range_end"]) for c in clips] == [
        (0.0, 225.0),
        (280.0, 560.0),
        (380.0, 600.0),
    ]
    assert clips[0]["title"] == "A title"
    # Before the page writes a clip, its title is the model's.
    assert clips[0]["model_title"] == "A title"
    assert clips[1]["model_title"] is None
    assert clips[0]["hook"] == "A hook."
    assert clips[2]["file"] is None
    assert clips[2]["error"] == "ffmpeg failed"
    assert [(c["reviewed"], c["judgment"]) for c in clips] == [(None, None)] * 3


@pytest.fixture
def frames() -> FakeFrames:
    return FakeFrames()


@pytest.fixture
def renderer() -> FakeClipRenderer:
    return FakeClipRenderer()


@pytest.fixture
def base_url(
    tmp_path: Path, frames: FakeFrames, renderer: FakeClipRenderer
) -> Iterator[str]:
    server = make_server(_session(tmp_path, frames, renderer), 0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers or {})
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


def test_serves_a_frame_and_lets_the_browser_keep_it(
    base_url: str, frames: FakeFrames, tmp_path: Path
) -> None:
    status, headers, body = _get(base_url + "/frames/talk-1024/12.jpg")

    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert headers["Cache-Control"] == "max-age=86400"
    assert body == b"frame 12"
    assert frames.calls == [(tmp_path / "talk.mp4", 12.0)]


def test_a_second_request_reads_the_cache(base_url: str, frames: FakeFrames) -> None:
    _get(base_url + "/frames/talk-1024/30.jpg")
    status, _, body = _get(base_url + "/frames/talk-1024/30.jpg")

    assert (status, body) == (200, b"frame 30")
    assert len(frames.calls) == 1


@pytest.mark.parametrize(
    "path",
    ["/frames/talk-1024/600.jpg", "/frames/talk-1024/-1.jpg",
     "/frames/talk-1024/1.5.jpg", "/frames/talk-1024/abc.jpg",
     "/frames/talk-1024/12.png", "/frames/talk-1024/12345678.jpg",
     "/frames/talk-1024/", "/frames/12.jpg",
     # Another video's frame: the browser must not get one for this video.
     "/frames/other-999/12.jpg", "/frames/talk-1024/x/12.jpg"],
)  # fmt: skip
def test_frames_outside_the_video_or_badly_named_are_not_found(
    base_url: str, frames: FakeFrames, path: str
) -> None:
    assert _get(base_url + path)[0] == 404
    assert frames.calls == []


# --- Save and Trash ---


def _post(
    base_url: str,
    path: str,
    data: object,
    headers: dict[str, str] | None = None,
    raw: bytes | None = None,
) -> tuple[int, dict[str, Any]]:
    sent = {
        "Content-Type": "application/json",
        "Origin": base_url,
        **(headers or {}),
    }
    body = raw if raw is not None else json.dumps(data).encode()
    status, _, reply = _get(base_url + path, sent, body)
    return status, json.loads(reply) if reply.startswith(b"{") else {}


def _labels_path(tmp_path: Path) -> Path:
    return tmp_path / "labels" / "picks" / "talk.json"


def _folder(tmp_path: Path) -> Path:
    return tmp_path / "clips" / "talk"


def test_save_unchanged_writes_a_post_label_and_the_title(
    base_url: str, tmp_path: Path
) -> None:
    status, clip = _post(
        base_url,
        "/api/clips/1/save",
        {"start": 60.0, "end": 105.0, "title": " Ron at the pump ", "note": "good"},
    )

    assert status == 200
    assert clip["reviewed"] == "saved"
    assert clip["title"] == "Ron at the pump"
    assert clip["model_title"] == "A title"
    assert clip["hook"] == "A hook."
    assert clip["judgment"] == {
        "verdict": "post",
        "start": 60.0,
        "end": 105.0,
        "note": "good",
        "style": None,
        "rating": None,
        "people": [],
    }
    [label] = load_labels(_labels_path(tmp_path)).labels
    assert (label.verdict, label.start_seconds, label.end_seconds) == (
        "post",
        60.0,
        105.0,
    )
    saved = load_manifest(_folder(tmp_path)).clips[0]
    assert (saved.reviewed, saved.title, saved.file) == (
        "saved",
        "Ron at the pump",
        "talk_1.mp4",
    )


def test_save_with_moved_handles_writes_a_fix_and_re_renders(
    base_url: str, tmp_path: Path, renderer: FakeClipRenderer
) -> None:
    status, clip = _post(base_url, "/api/clips/2/save", {"start": 398.5, "end": 446.25})

    assert status == 200
    [label] = load_labels(_labels_path(tmp_path)).labels
    assert label.verdict == "fix"
    assert (label.start_seconds, label.end_seconds) == (398.5, 446.25)
    assert label.draft is not None
    assert (label.draft.start_seconds, label.draft.end_seconds) == (400.0, 440.0)
    assert label.note is None
    # An empty title box clears the title.
    assert clip["title"] is None
    # Re-rendered under the new span's name, with the folder's flags.
    assert (clip["file"], clip["start"], clip["end"]) == (
        "talk_000638-000727.mp4",
        398.5,
        446.25,
    )
    assert (clip["draft_start"], clip["draft_end"]) == (400.0, 440.0)
    assert (clip["render_error"], clip["warning"]) == (None, None)
    [(_, candidate, output)] = renderer.calls
    assert (candidate.start_seconds, candidate.end_seconds) == (398.5, 446.25)
    assert output.name == "talk_000638-000727.part.mp4"
    assert renderer.vertical == [True]
    folder = _folder(tmp_path)
    assert (folder / "talk_000638-000727.mp4").exists()
    assert not (folder / "talk_2.mp4").exists()
    assert not output.exists()


def test_save_unchanged_does_not_render(
    base_url: str, renderer: FakeClipRenderer
) -> None:
    _post(base_url, "/api/clips/2/save", {"start": 400.0, "end": 440.0, "title": "T"})

    assert renderer.calls == []


def test_saving_again_replaces_the_clips_label(base_url: str, tmp_path: Path) -> None:
    _post(base_url, "/api/clips/1/save", {"start": 55.0, "end": 110.0})
    _post(base_url, "/api/clips/1/save", {"start": 60.0, "end": 105.0, "note": "n"})

    [label] = load_labels(_labels_path(tmp_path)).labels
    assert (label.verdict, label.note) == ("post", "n")


def test_a_span_over_another_clips_label_is_refused(
    base_url: str, tmp_path: Path
) -> None:
    _post(base_url, "/api/clips/2/save", {"start": 400.0, "end": 440.0})

    status, reply = _post(base_url, "/api/clips/1/save", {"start": 60.0, "end": 405.0})

    assert status == 409
    assert "Overlaps the label 0:06:40-0:07:20 (post)" in str(reply["error"])
    assert len(load_labels(_labels_path(tmp_path)).labels) == 1


def test_trash_deletes_the_file_and_writes_a_rejected_span(
    base_url: str, tmp_path: Path
) -> None:
    status, clip = _post(base_url, "/api/clips/2/trash", {"note": "boring"})

    assert status == 200
    assert (clip["reviewed"], clip["file"]) == ("trashed", None)
    assert clip["judgment"] == {"verdict": "trashed", "note": "boring"}
    assert not (_folder(tmp_path) / "talk_2.mp4").exists()
    labels = load_labels(_labels_path(tmp_path))
    assert labels.labels == ()
    assert [(r.start_seconds, r.note) for r in labels.rejected] == [(400.0, "boring")]
    assert load_manifest(_folder(tmp_path)).clips[1].reviewed == "trashed"


def test_trash_after_save_replaces_the_label(base_url: str, tmp_path: Path) -> None:
    _post(base_url, "/api/clips/1/save", {"start": 50.0, "end": 105.0})
    _post(base_url, "/api/clips/1/trash", {})

    labels = load_labels(_labels_path(tmp_path))
    assert labels.labels == ()
    assert len(labels.rejected) == 1


def test_a_failed_clip_can_be_trashed(base_url: str) -> None:
    status, clip = _post(base_url, "/api/clips/3/trash", {})

    assert status == 200
    assert (clip["reviewed"], clip["error"]) == ("trashed", "ffmpeg failed")


def test_saving_a_trashed_clip_renders_it_again(base_url: str, tmp_path: Path) -> None:
    _post(base_url, "/api/clips/2/trash", {})

    status, clip = _post(base_url, "/api/clips/2/save", {"start": 400, "end": 440})

    assert status == 200
    assert (clip["reviewed"], clip["file"]) == ("saved", "talk_000640-000720.mp4")
    assert clip["judgment"]["verdict"] == "post"
    assert (_folder(tmp_path) / "talk_000640-000720.mp4").exists()


def test_a_failed_re_render_keeps_the_label_and_says_so(
    tmp_path: Path, frames: FakeFrames
) -> None:
    moved = ClipCandidate(
        start_seconds=398.0, end_seconds=440.0, score=0.5, reasons=("test",)
    )
    server = make_server(
        _session(tmp_path, frames, FakeClipRenderer(fail_on=[moved])), 0
    )
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        status, clip = _post(url, "/api/clips/2/save", {"start": 398, "end": 440})
    finally:
        server.shutdown()
        server.server_close()

    assert status == 200
    assert clip["render_error"] == "Fake render failed for talk_000638-000720.part.mp4"
    # The old file and its span stay; the label is written.
    assert (clip["file"], clip["start"], clip["error"]) == ("talk_2.mp4", 400.0, None)
    assert clip["judgment"]["verdict"] == "fix"
    assert (_folder(tmp_path) / "talk_2.mp4").exists()


def test_add_creates_a_clip_and_trash_removes_it(base_url: str, tmp_path: Path) -> None:
    status, clip = _post(
        base_url,
        "/api/clips/add",
        {"start": 200.0, "end": 230.0, "title": "Missed", "note": "good"},
    )

    assert status == 200
    assert (clip["rank"], clip["added"], clip["file"]) == (
        4,
        True,
        "talk_000320-000350.mp4",
    )
    assert clip["judgment"] == {
        "verdict": "added",
        "start": 200.0,
        "end": 230.0,
        "note": "good",
        "style": None,
        "rating": None,
        "people": [],
    }
    assert (clip["render_error"], clip["model_title"]) == (None, None)
    listed = json.loads(_get(base_url + "/api/clips")[2])["clips"]
    assert [c["rank"] for c in listed] == [1, 2, 3, 4]

    status, reply = _post(base_url, "/api/clips/4/trash", {})

    assert (status, reply) == (200, {"removed": 4})
    listed = json.loads(_get(base_url + "/api/clips")[2])["clips"]
    assert [c["rank"] for c in listed] == [1, 2, 3]
    assert not (_folder(tmp_path) / "talk_000320-000350.mp4").exists()


def test_an_add_needs_a_good_body_and_the_page_itself(base_url: str) -> None:
    status, reply = _post(base_url, "/api/clips/add", {"start": 30, "end": 20})
    assert (status, reply["error"]) == (400, "the end must be after the start")

    headers = {"Origin": "https://example.com"}
    status, _ = _post(base_url, "/api/clips/add", {"start": 1, "end": 20}, headers)
    assert status == 403


def test_the_clip_list_shows_judgments_after_writes(base_url: str) -> None:
    _post(base_url, "/api/clips/1/save", {"start": 60.0, "end": 100.0})
    _post(base_url, "/api/clips/2/trash", {})

    clips = json.loads(_get(base_url + "/api/clips")[2])["clips"]

    assert [c["reviewed"] for c in clips] == ["saved", "trashed", None]
    assert clips[0]["judgment"]["verdict"] == "fix"
    assert clips[1]["judgment"]["verdict"] == "trashed"


def test_an_unknown_rank_or_action_is_not_found(base_url: str) -> None:
    assert _post(base_url, "/api/clips/9/save", {"start": 1, "end": 2})[0] == 404
    assert _post(base_url, "/api/clips/1/delete", {})[0] == 404
    assert _post(base_url, "/api/clips", {})[0] == 404


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "JSON object"),
        ({"start": 60, "end": 60}, "end must be after the start"),
        ({"start": -1, "end": 60}, "inside the video"),
        ({"start": 60, "end": 601}, "inside the video"),
        ({"start": True, "end": 60}, "start must be a number"),
        ({"start": "60", "end": 90}, "start must be a number"),
        ({"end": 90}, "start must be a number"),
        ({"start": 60, "end": 90, "title": "a\nb"}, "one line"),
        ({"start": 60, "end": 90, "title": "x" * 201}, "at most 200"),
        ({"start": 60, "end": 90, "note": 5}, "note must be text"),
    ],
)
def test_a_bad_save_body_is_a_bad_request(
    base_url: str, tmp_path: Path, data: object, message: str
) -> None:
    status, reply = _post(base_url, "/api/clips/1/save", data)

    assert status == 400
    assert message in str(reply["error"])
    assert not _labels_path(tmp_path).exists()


def test_odd_bodies_are_bad_requests(base_url: str) -> None:
    # Python's json reads NaN, which is no time.
    raw = b'{"start": NaN, "end": 90}'
    status, reply = _post(base_url, "/api/clips/1/save", None, raw=raw)
    assert (status, reply["error"]) == (400, "start must be a number of seconds")

    status, reply = _post(base_url, "/api/clips/1/trash", ["not", "an", "object"])
    assert (status, reply["error"]) == (400, "the body must be a JSON object")


def test_a_body_without_a_length_is_a_bad_request(base_url: str) -> None:
    host = base_url.removeprefix("http://")
    connection = http.client.HTTPConnection(host, timeout=5)
    connection.putrequest("POST", "/api/clips/1/trash")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Origin", base_url)
    connection.endheaders()
    response = connection.getresponse()

    assert response.status == 400
    assert json.loads(response.read())["error"] == "a Content-Length is needed"
    connection.close()


def test_invalid_json_and_oversized_bodies_are_bad_requests(base_url: str) -> None:
    status, reply = _post(base_url, "/api/clips/1/trash", None, raw=b"{nope")
    assert (status, reply["error"]) == (400, "the body is not valid JSON")

    status, reply = _post(base_url, "/api/clips/1/trash", None, raw=b" " * 70_000)
    assert status == 400
    assert "at most 65536 bytes" in str(reply["error"])


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://example.com"},
        {"Origin": ""},
        {"Content-Type": "text/plain"},
        {"Content-Type": "application/x-www-form-urlencoded"},
        {"Host": "evil.example"},
    ],
    ids=["other origin", "empty origin", "text", "form", "other host"],
)
def test_writes_from_anywhere_but_the_page_are_forbidden(
    base_url: str, tmp_path: Path, headers: dict[str, str]
) -> None:
    status, reply = _post(base_url, "/api/clips/2/trash", {}, headers)

    assert status == 403
    assert reply["error"] == "Only the review page may write"
    assert (_folder(tmp_path) / "talk_2.mp4").exists()


def test_localhost_is_trusted_like_the_loopback_address(base_url: str) -> None:
    port = base_url.rsplit(":", 1)[1]
    origin = f"http://localhost:{port}"
    headers = {"Host": f"localhost:{port}", "Origin": origin}

    assert _post(base_url, "/api/clips/3/trash", {}, headers)[0] == 200


def test_a_write_failure_is_a_server_error(base_url: str, tmp_path: Path) -> None:
    _labels_path(tmp_path).mkdir(parents=True)

    status, reply = _post(base_url, "/api/clips/1/save", {"start": 60, "end": 105})

    assert status == 500
    assert "Cannot write labels file" in str(reply["error"])


def test_a_frame_failure_is_a_server_error(base_url: str, frames: FakeFrames) -> None:
    frames.error = True

    status, _, body = _get(base_url + "/frames/talk-1024/12.jpg")

    assert status == 500
    assert b"bad frame" in body


def test_cached_frame_leaves_no_partial_file(tmp_path: Path) -> None:
    session = _session(tmp_path, FakeFrames(error=True))
    with pytest.raises(FrameError):
        cached_frame(session, 5)
    assert list(session.frames_dir.iterdir()) == []

    session = _session(tmp_path)
    assert cached_frame(session, 5).read_bytes() == b"frame 5"
    assert [p.name for p in session.frames_dir.iterdir()] == ["5.jpg"]


def test_cached_frame_tolerates_a_rename_blocked_by_a_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path)

    def blocked(source: Path, target: Path) -> None:
        # Another request made the frame first and is reading it.
        Path(target).write_bytes(b"frame 7")
        raise PermissionError("in use")

    monkeypatch.setattr("os.replace", blocked)

    assert cached_frame(session, 7).read_bytes() == b"frame 7"
    assert [p.name for p in session.frames_dir.iterdir()] == ["7.jpg"]


def test_cached_frame_reports_a_rename_that_fails_for_another_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(source: Path, target: Path) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr("os.replace", denied)

    with pytest.raises(PermissionError):
        cached_frame(_session(tmp_path), 7)


def test_frames_folder_is_named_by_video_and_size(tmp_path: Path) -> None:
    video = tmp_path / "talk.mp4"
    video.write_bytes(VIDEO)

    assert frames_folder(tmp_path / "cache", video) == (
        tmp_path / "cache" / "frames" / "talk-1024"
    )


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


def test_a_listed_draft_is_served_and_nothing_else(base_url: str) -> None:
    status, headers, body = _get(base_url + "/clips/talk_1.mp4?v=2")
    assert (status, body) == (200, b"clip")
    assert headers["Content-Type"] == "video/mp4"
    assert headers["Cache-Control"] == "no-store"
    status, _, body = _get(base_url + "/clips/talk_2.mp4", {"Range": "bytes=1-2"})
    assert (status, body) == (206, b"li")
    assert _get(base_url + "/clips/other.mp4")[0] == 404
    assert _get(base_url + "/clips/..%2Fmanifest.json")[0] == 404


def test_a_listed_draft_missing_on_disk_is_not_found(
    base_url: str, tmp_path: Path
) -> None:
    (tmp_path / "clips" / "talk" / "talk_2.mp4").unlink()
    assert _get(base_url + "/clips/talk_2.mp4")[0] == 404


def test_the_payload_lists_each_clips_styles(tmp_path: Path) -> None:
    from clipper.domain.models import ClipVariant
    from clipper.review.server import clip_payload

    session = _session(tmp_path)
    store = make_store(session)
    variants = (
        ClipVariant(style="fit", file="talk_1.mp4"),
        ClipVariant(style="face", file="talk_1.face.mp4"),
    )
    clip = MANIFEST.clips[0].model_copy(update={"variants": variants})
    payload = clip_payload(session, store, clip)
    assert payload["style"] == "fit"
    assert payload["variants"] == [
        {"style": "fit", "file": "talk_1.mp4"},
        {"style": "face", "file": "talk_1.face.mp4"},
    ]
    plain = clip_payload(session, store, MANIFEST.clips[1])
    assert (plain["style"], plain["variants"]) == (None, [])
