import json
from datetime import date
from pathlib import Path
from typing import Literal

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.results.labels_json import save_labels
from clipper.adapters.results.manifest_json import load_manifest, save_manifest
from clipper.adapters.results.posting_files import load_posts
from clipper.cli.main import app
from clipper.domain.models import (
    LabeledVideo,
    ManifestClip,
    RenderManifest,
    Summary,
    SummaryPart,
)

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

    def folder(self, name: str, ratings: list[str | None], under: str = "") -> Path:
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
            self.clips / under / name,
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
        return self.clips / under / name

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
            "Name the clip by its path (absolute, or from here or the clips dir) "
            "or as <folder>/<file>",
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


def test_folders_by_channel_and_day_are_queued_and_posted(env: Env) -> None:
    folder = env.folder("v2", ["good", "good"], under="Lacy/11-10-2026")
    result = env.run("queue", "--start", "2026-11-12")
    assert result.exit_code == 0, result.output
    page = (env.clips / "queue.md").read_text(encoding="utf-8")
    assert f"`{(folder / 'v2_1.mp4').resolve()}`" in page
    by_name = env.run("posted", "v2/v2_1.mp4", "https://youtu.be/abcDEF12345")
    assert by_name.exit_code == 0, by_name.output
    by_path = env.run(
        "posted", str(folder / "v2_2.mp4"), "https://youtu.be/xyzDEF12345"
    )
    assert by_path.exit_code == 0, by_path.output
    assert [p.clip for p in load_posts(env.posts).posts] == [
        "v2/v2_1.mp4",
        "v2/v2_2.mp4",
    ]


def test_posted_takes_paths_relative_to_here_or_the_clips_dir(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = env.folder("v2", ["good", "good"], under="Lacy/11-10-2026")
    for name in ("v2_1.mp4", "v2_2.mp4"):
        (folder / name).write_bytes(b"")
    monkeypatch.chdir(tmp_path)
    from_here = env.run(
        "posted", "clips/Lacy/11-10-2026/v2/v2_1.mp4", "https://youtu.be/abcDEF12345"
    )
    assert from_here.exit_code == 0, from_here.output
    from_clips = env.run(
        "posted", "Lacy/11-10-2026/v2/v2_2.mp4", "https://youtu.be/xyzDEF12345"
    )
    assert from_clips.exit_code == 0, from_clips.output
    assert [p.clip for p in load_posts(env.posts).posts] == [
        "v2/v2_1.mp4",
        "v2/v2_2.mp4",
    ]


def test_posted_refuses_a_relative_path_outside_the_clips_dir(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.folder("v1", ["good"])
    other = tmp_path / "other" / "deep" / "v1"
    other.mkdir(parents=True)
    (other / "v1_1.mp4").write_bytes(b"")
    monkeypatch.chdir(tmp_path)
    result = env.run("posted", "other/deep/v1/v1_1.mp4", "https://youtu.be/abcDEF12345")
    assert result.exit_code == 1
    assert "Name the clip by its path" in " ".join(result.output.split())
    assert not env.posts.exists()


def test_posted_names_a_missing_folder(env: Env) -> None:
    env.folder("v1", ["good"])
    result = env.run("posted", "v9/v9_1.mp4", "https://youtu.be/abcDEF12345")
    assert result.exit_code == 1
    assert "no drafts folder v9" in " ".join(result.output.split())


# --- Summaries (ADR 0032) ---


def _summarize(
    folder: Path, rating: Literal["good", "ok"] | None, ranks: tuple[int, ...] = (1, 2)
) -> None:
    manifest = load_manifest(folder)
    summary = Summary(
        file=f"{folder.name}_summary.mp4",
        parts=tuple(SummaryPart(rank=rank) for rank in ranks),
        duration_seconds=60.0,
        title=f"{folder.name} all of it",
        rating=rating,
        thumbnail=f"{folder.name}_summary.thumb.jpg",
    )
    save_manifest(manifest.model_copy(update={"summary": summary}), folder)


def test_a_good_summary_is_queued_after_its_clips_with_their_people(env: Env) -> None:
    folder = env.folder("v1", ["good", "good"])
    _summarize(folder, "good")
    result = env.run("queue", "--start", "2026-11-12", "--per-day", "3")
    assert result.exit_code == 0, result.output
    assert "v1 all of it" in result.output
    page = (env.clips / "queue.md").read_text(encoding="utf-8")
    assert "## 3. 2026-11-12: v1 summary" in page
    # Clip 1 has Ron and Lacy marked, so the summary fits CORE; with no hook
    # the caption opens with the title, as for any clip without one.
    assert "v1 all of it With Ron and Lacy. #CORE #Shorts" in page
    assert "v1_summary.thumb.jpg" in page


def test_an_ok_or_unrated_summary_waits(env: Env) -> None:
    folder = env.folder("v1", ["good"])
    _summarize(folder, "ok")
    assert "all of it" not in env.run("queue").output
    assert "all of it" in env.run("queue", "--include-ok").output
    _summarize(folder, None)
    assert "all of it" not in env.run("queue", "--include-ok").output


def test_a_posted_summary_leaves_the_queue(env: Env) -> None:
    folder = env.folder("v1", ["good"])
    _summarize(folder, "good", ranks=(1,))
    result = env.run("posted", "v1/v1_summary.mp4", "https://youtu.be/abcDEF12345")
    assert result.exit_code == 0, result.output
    assert "Posted v1/v1_summary.mp4" in result.output
    assert "all of it" not in env.run("queue").output


# --- Posting by title (ADR 0028, title amendment) ---


def test_posted_finds_a_clip_by_its_title_case_and_spaces_aside(env: Env) -> None:
    env.folder("v1", ["good", "good"])
    result = env.run("posted", "  V1  TITLE 2 ", "https://youtu.be/abcDEF12345")
    assert result.exit_code == 0, result.output
    assert "Posted v1/v1_2.mp4" in result.output
    assert [p.clip for p in load_posts(env.posts).posts] == ["v1/v1_2.mp4"]


def test_posted_finds_a_summary_by_its_title(env: Env) -> None:
    folder = env.folder("v1", ["good"])
    _summarize(folder, "good", ranks=(1,))
    result = env.run("posted", "v1 all of it", "https://youtu.be/abcDEF12345")
    assert result.exit_code == 0, result.output
    assert "Posted v1/v1_summary.mp4" in result.output


def test_posted_by_an_unknown_title_suggests_close_ones(env: Env) -> None:
    env.folder("v1", ["good", "good"])
    result = env.run("posted", "title", "https://youtu.be/abcDEF12345")
    output = " ".join(result.output.split())
    assert result.exit_code == 1
    assert "No saved clip is titled 'title'" in output
    assert "Close titles: v1 title 1; v1 title 2." in output
    assert not env.posts.exists()


def test_posted_refuses_a_title_two_clips_share(env: Env) -> None:
    env.folder("v1", ["good"])
    env.folder("v2", ["good"])
    folder = env.clips / "v2"
    manifest = load_manifest(folder)
    clips = tuple(c.model_copy(update={"title": "v1 title 1"}) for c in manifest.clips)
    save_manifest(manifest.model_copy(update={"clips": clips}), folder)
    result = env.run("posted", "v1 title 1", "https://youtu.be/abcDEF12345")
    output = " ".join(result.output.split())
    assert result.exit_code == 1
    assert "2 saved clips are titled 'v1 title 1'" in output
    assert "v1/v1_1.mp4" in output and "v2/v2_1.mp4" in output
    assert not env.posts.exists()


def test_a_path_like_miss_keeps_the_path_error(env: Env) -> None:
    env.folder("v1", ["good"])
    result = env.run("posted", "v9/v9_1.mp4", "https://youtu.be/abcDEF12345")
    assert "no drafts folder v9" in " ".join(result.output.split())
