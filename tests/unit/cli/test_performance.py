from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from clipper.adapters.results.manifest_json import save_manifest
from clipper.adapters.results.posting_files import load_posts
from clipper.cli.main import app
from clipper.domain.models import ManifestClip, RenderManifest

# A constructed drafts folder and export, not real runs.

TABLE = (
    "Content,Video title,Video publish time,Duration,Views,"
    "Stayed to watch (%),Average percentage viewed (%)\n"
    "Total,,,,1500,60,60\n"
    'mat1,Lacy Eats Fish 🐟,"Nov 12, 2026",30,1200,65.5,80.25\n'
    'pos2,Renamed,"Nov 13, 2026",45,300,50,40\n'
    'non3,Something Else,"Nov 13, 2026",41,,,\n'
)


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.clips = tmp_path / "clips"
        self.posts = tmp_path / "posts.json"
        self.perf = tmp_path / "performance"
        for name, value in [
            ("CLIPS_DIR", self.clips),
            ("LABELS_DIR", tmp_path / "labels"),
            ("VIDEOS_DIR", tmp_path / "videos"),
            ("POSTS_FILE", self.posts),
            ("PERFORMANCE_DIR", self.perf),
        ]:
            monkeypatch.setenv(f"CLIPPER_{name}", str(value))
        monkeypatch.setenv("COLUMNS", "250")
        clips = tuple(
            ManifestClip(
                rank=n,
                start_seconds=60.0 * n,
                end_seconds=60.0 * n + length,
                score=0.5,
                reasons=("r",),
                file=f"v1_{n}.mp4",
                title=title,
            )
            for n, (title, length) in enumerate(
                [("Lacy eats fish", 30.0), ("Posted", 45.0)], start=1
            )
        )
        save_manifest(
            RenderManifest(
                source="v1.mp4",
                analysis="a.json",
                detector=None,
                vertical=True,
                captions=True,
                titles=True,
                clips=clips,
            ),
            self.clips / "Lacy" / "11-10-2026" / "v1",
        )
        self.posts.write_text(
            '{"posts": [{"clip": "v1/v1_2.mp4", "url": "https://youtube.com/'
            'shorts/pos2", "video_id": "pos2", "posted": "2026-11-13"}]}',
            encoding="utf-8",
        )
        self.export = tmp_path / "Content 2026-10-20_2026-11-17 Chan"
        self.export.mkdir()
        (self.export / "Table data.csv").write_text(TABLE, encoding="utf-8")

    def run(self, *args: str) -> Result:
        return CliRunner().invoke(app, ["performance", *args])


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


def test_performance_reports_links_and_keeps_a_snapshot(env: Env) -> None:
    result = env.run(str(env.export))
    assert result.exit_code == 0, result.output
    assert "65.5%" in result.output
    assert "80.2%" in result.output
    assert "30-40 s" in result.output
    assert "Not linked" in result.output
    assert "Linked: 1 by the posts record, 1 matched, 0 ambiguous, 1 not found" in (
        result.output
    )
    assert (env.perf / "2026-11-17" / "Table data.csv").is_file()
    assert len(load_posts(env.posts).posts) == 1


def test_performance_link_adds_matched_shorts_to_the_posts_record(env: Env) -> None:
    result = env.run(str(env.export), "--link")
    assert result.exit_code == 0, result.output
    assert "Added 1 post(s)" in result.output
    posts = load_posts(env.posts).posts
    assert [(p.clip, p.video_id, p.posted.isoformat()) for p in posts] == [
        ("v1/v1_2.mp4", "pos2", "2026-11-13"),
        ("v1/v1_1.mp4", "mat1", "2026-11-12"),
    ]
    again = env.run(str(env.export), "--link")
    assert "Added 0 post(s)" in again.output
    assert "2 by the posts record" in again.output


def test_performance_names_a_bad_export(env: Env, tmp_path: Path) -> None:
    result = env.run(str(tmp_path / "missing"))
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "No Table data.csv" in result.output
