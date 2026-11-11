import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.results.labels_json import save_labels
from clipper.adapters.results.manifest_json import save_manifest
from clipper.adapters.results.posting_files import load_posts
from clipper.cli.main import app
from clipper.domain.models import LabeledVideo, ManifestClip, RenderManifest

# Constructed drafts folders, labels and campaigns, not real runs.


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.clips = tmp_path / "clips"
        self.labels = tmp_path / "labels"
        self.posts = tmp_path / "posts.json"
        campaigns = tmp_path / "campaigns.txt"
        campaigns.write_text("CORE | #CORE | 2 | Ron, Lacy\n", encoding="utf-8")
        for name, value in [
            ("CLIPS_DIR", self.clips),
            ("LABELS_DIR", self.labels),
            ("VIDEOS_DIR", tmp_path / "videos"),
            ("POSTS_FILE", self.posts),
            ("CAMPAIGNS_FILE", campaigns),
        ]:
            monkeypatch.setenv(f"CLIPPER_{name}", str(value))
        monkeypatch.setenv("COLUMNS", "200")

    def folder(self, name: str, ratings: list[str | None]) -> None:
        clips = tuple(
            ManifestClip(
                rank=n,
                start_seconds=n * 60.0,
                end_seconds=n * 60.0 + 30.0,
                score=0.5,
                reasons=("r",),
                file=f"{name}_{n}.mp4",
                reviewed="saved",
                title=f"{name} title {n}",
                hook=f"{name} hook {n}.",
            )
            for n in range(1, len(ratings) + 1)
        )
        save_manifest(
            RenderManifest(
                source=f"{name}.mp4",
                analysis="a.json",
                detector=None,
                vertical=True,
                captions=True,
                titles=True,
                clips=clips,
            ),
            self.clips / name,
        )
        labels = [
            {
                "start": f"0:{n:02d}:00",
                "end": f"0:{n:02d}:30",
                "verdict": "post",
                **({"rating": rating} if rating else {}),
                "people": ["Ron", "Lacy"] if n == 1 else [],
            }
            for n, rating in enumerate(ratings, start=1)
        ]
        save_labels(
            LabeledVideo.model_validate({"source": f"{name}.mp4", "labels": labels}),
            self.labels / "picks" / f"{name}.json",
        )

    def run(self, *args: str) -> Result:
        return CliRunner().invoke(app, list(args))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


def test_queue_lists_good_clips_and_writes_the_page(env: Env) -> None:
    env.folder("v1", ["good", "ok", None])
    result = env.run("queue", "--start", "2026-11-12", "--per-day", "1")
    assert result.exit_code == 0, result.output
    assert "v1 title 1" in result.output
    assert "v1 title 2" not in result.output
    page = (env.clips / "queue.md").read_text(encoding="utf-8")
    assert "## 1. 2026-11-12: v1 #1" in page
    assert "v1 hook 1. With Ron and Lacy. #CORE #Shorts" in page
    assert "Queue: " in result.output


def test_queue_with_ok_clips_and_another_page(env: Env, tmp_path: Path) -> None:
    env.folder("v1", ["good", "ok"])
    out = tmp_path / "week.md"
    result = env.run("queue", "--include-ok", "--out", str(out))
    assert result.exit_code == 0, result.output
    assert "v1 title 2" in result.output
    assert date.today().isoformat() in out.read_text(encoding="utf-8")


def test_queue_with_nothing_rated(env: Env) -> None:
    env.folder("v1", [None])
    result = env.run("queue")
    assert result.exit_code == 0, result.output
    assert "Nothing to post" in result.output


def test_queue_refuses_a_bad_start_day(env: Env) -> None:
    result = env.run("queue", "--start", "12/11/2026")
    assert result.exit_code == 1
    assert "--start must be a date as YYYY-MM-DD" in result.output


def test_posted_records_the_clip_and_it_leaves_the_queue(env: Env) -> None:
    env.folder("v1", ["good", "good"])
    result = env.run(
        "posted",
        "v1/v1_1.mp4",
        "https://youtube.com/shorts/abcDEF12345",
        "--on",
        "2026-11-12",
    )
    assert result.exit_code == 0, result.output
    assert "Posted v1/v1_1.mp4: abcDEF12345 on 2026-11-12" in result.output
    [post] = load_posts(env.posts).posts
    assert (post.clip, post.video_id, post.posted) == (
        "v1/v1_1.mp4",
        "abcDEF12345",
        date(2026, 11, 12),
    )
    queued = env.run("queue")
    assert "v1 title 1" not in queued.output
    assert "v1 title 2" in queued.output
    again = env.run(
        "posted", str(env.clips / "v1" / "v1_1.mp4"), "https://youtu.be/abcDEF12345"
    )
    assert "Replaced the post for v1/v1_1.mp4" in again.output
    assert json.loads(env.posts.read_text(encoding="utf-8"))["posts"][0]["clip"] == (
        "v1/v1_1.mp4"
    )


@pytest.mark.parametrize(
    ("clip", "url", "message"),
    [
        ("v1/none.mp4", "https://youtu.be/abcDEF12345", "lists no clip none.mp4"),
        (
            "v1_1.mp4",
            "https://youtu.be/abcDEF12345",
            "Name the clip as <folder>/<file>",
        ),
        ("v1/v1_1.mp4", "https://www.twitch.tv/videos/123", "Not a YouTube link"),
        ("v1/v1_1.mp4", "https://example.com/x", "Not a"),
    ],
)
def test_posted_refuses_bad_input(env: Env, clip: str, url: str, message: str) -> None:
    env.folder("v1", ["good"])
    result = env.run("posted", clip, url)
    assert result.exit_code == 1
    assert message in " ".join(result.output.split())
    assert not env.posts.exists()
