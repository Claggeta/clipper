from datetime import date
from pathlib import Path

import pytest

from clipper.core.posting import (
    DraftsFolder,
    build_queue,
    caption,
    clip_key,
    eligibility,
    parse_campaigns,
    queue_markdown,
    record_post,
    schedule,
)
from clipper.domain.errors import QueueError
from clipper.domain.models import (
    Label,
    LabeledVideo,
    ManifestClip,
    PostLog,
    PostRecord,
    RenderManifest,
)

# Constructed campaigns, manifests and labels, not real runs or briefs.

CAMPAIGNS = parse_campaigns(
    "# rules\n"
    "CORE | #CORE | 2 | Ron, Jason, Lacy\n"
    "Lacy | #Lacy | 1 | Lacy | Lacy\n"
    "Jason | #Jason | 1 | Jason\n"
)


def _clip(rank: int, start: float, **fields: object) -> ManifestClip:
    values: dict[str, object] = {
        "rank": rank,
        "start_seconds": start,
        "end_seconds": start + 30.0,
        "score": 0.5,
        "reasons": ("r",),
        "file": f"c{rank}.mp4",
        "reviewed": "saved",
        "title": f"Title {rank}",
        "hook": f"Hook {rank}.",
    }
    values.update(fields)
    return ManifestClip.model_validate(values)


def _label(start: float, rating: str | None, people: tuple[str, ...] = ()) -> Label:
    return Label.model_validate(
        {
            "start": f"0:{int(start) // 60:02d}:{int(start) % 60:02d}",
            "end": f"0:{int(start + 30) // 60:02d}:{int(start + 30) % 60:02d}",
            "verdict": "post",
            "rating": rating,
            "people": people,
        }
    )


def _folder(
    name: str,
    clips: tuple[ManifestClip, ...],
    labels: tuple[Label, ...],
    channel: str | None = "Lacy",
) -> DraftsFolder:
    manifest = RenderManifest(
        source=f"{name}.mp4",
        analysis="a.json",
        detector=None,
        vertical=True,
        captions=True,
        titles=True,
        clips=clips,
    )
    labeled = LabeledVideo(source=f"{name}.mp4", labels=labels) if labels else None
    return DraftsFolder(name=name, manifest=manifest, labels=labeled, channel=channel)


def test_parses_campaign_lines() -> None:
    core, lacy, jason = CAMPAIGNS
    assert (core.name, core.hashtag, core.minimum, core.people, core.channels) == (
        "CORE",
        "#CORE",
        2,
        ("Ron", "Jason", "Lacy"),
        (),
    )
    assert lacy.channels == ("Lacy",)
    assert jason.minimum == 1


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("CORE | #CORE | 2", "Line 1: write it as"),
        ("CORE | CORE | 2 | Ron, Jason", "the hashtag must be one #word"),
        ("CORE | #CORE | two | Ron, Jason", "the minimum must be a whole number"),
        ("CORE | #CORE | 0 | Ron", "the minimum must be a whole number"),
        ("CORE | #CORE | 3 | Ron, Jason", "the minimum is more than the people"),
        ("CORE | #CORE | 1 | , ", "name at least one person"),
    ],
)
def test_bad_campaign_lines_raise(text: str, message: str) -> None:
    with pytest.raises(QueueError, match=message):
        parse_campaigns(text)


def test_eligibility_counts_people_and_checks_channels() -> None:
    both = eligibility(CAMPAIGNS, ["lacy", "Ron"], "Lacy ")
    assert [c.name for c in both.campaigns] == ["CORE", "Lacy"]
    assert both.wrong_channel == ()
    elsewhere = eligibility(CAMPAIGNS, ["Lacy", "Jason"], "jasontheween")
    assert [c.name for c in elsewhere.campaigns] == ["CORE", "Jason"]
    assert [c.name for c in elsewhere.wrong_channel] == ["Lacy"]
    assert eligibility(CAMPAIGNS, [], "Lacy").campaigns == ()


def test_the_queue_takes_good_unposted_clips_one_video_after_another() -> None:
    newer = _folder(
        "new",
        (_clip(1, 0.0), _clip(2, 60.0), _clip(3, 120.0)),
        (_label(0.0, "good"), _label(60.0, "good"), _label(120.0, "ok")),
    )
    older = _folder(
        "old",
        (_clip(1, 0.0), _clip(2, 60.0, reviewed="trashed", file=None)),
        (_label(0.0, "good", ("Lacy", "Ron")),),
    )
    queue = build_queue([newer, older], CAMPAIGNS, PostLog())
    assert [item.clip for item in queue] == ["new/c1.mp4", "old/c1.mp4", "new/c2.mp4"]
    assert [c.name for c in queue[1].eligible.campaigns] == ["CORE", "Lacy"]


def test_the_queue_leaves_out_posted_unrated_and_unsaved_clips() -> None:
    folder = _folder(
        "v",
        (_clip(1, 0.0), _clip(2, 60.0), _clip(3, 120.0, reviewed=None)),
        (_label(0.0, "good"), _label(60.0, None), _label(120.0, "good")),
    )
    posts = PostLog(
        posts=(
            PostRecord(
                clip="v/c1.mp4", url="u", video_id="abc", posted=date(2026, 11, 1)
            ),
        )
    )
    assert build_queue([folder], CAMPAIGNS, posts) == []


def test_ok_clips_come_after_good_ones_when_included() -> None:
    folder = _folder(
        "v",
        (_clip(1, 0.0), _clip(2, 60.0)),
        (_label(0.0, "ok"), _label(60.0, "good")),
    )
    queue = build_queue([folder], CAMPAIGNS, PostLog(), include_ok=True)
    assert [(item.rank, item.rating) for item in queue] == [(2, "good"), (1, "ok")]


def test_schedule_spreads_clips_over_days() -> None:
    folder = _folder(
        "v",
        tuple(_clip(n, n * 60.0) for n in range(1, 6)),
        tuple(_label(n * 60.0, "good") for n in range(1, 6)),
    )
    queue = build_queue([folder], CAMPAIGNS, PostLog())
    days = [day.day for day, _ in schedule(queue, date(2026, 11, 12), 2)]
    assert days == [12, 12, 13, 13, 14]
    with pytest.raises(QueueError, match="at least one clip a day"):
        schedule(queue, date(2026, 11, 12), 0)


def test_caption_names_the_people_and_tags_each_campaign() -> None:
    folder = _folder("v", (_clip(1, 0.0),), (_label(0.0, "good", ("Ron", "Lacy")),))
    [item] = build_queue([folder], CAMPAIGNS, PostLog())
    assert caption(item) == "Hook 1. With Ron and Lacy. #CORE #Lacy #Shorts"


def test_caption_without_people_or_hook() -> None:
    folder = _folder("v", (_clip(1, 0.0, hook=None),), (_label(0.0, "good"),))
    [item] = build_queue([folder], CAMPAIGNS, PostLog())
    assert caption(item) == "Title 1 #Shorts"


def test_record_post_replaces_an_earlier_post_of_the_clip() -> None:
    first = PostRecord(clip="v/c1.mp4", url="a", video_id="a", posted=date(2026, 11, 1))
    again = PostRecord(clip="v/c1.mp4", url="b", video_id="b", posted=date(2026, 11, 2))
    log, replaced = record_post(PostLog(), first)
    assert not replaced
    log, replaced = record_post(log, again)
    assert replaced
    assert log.posts == (again,)


def test_queue_markdown_lists_each_clip_ready_to_copy() -> None:
    folder = _folder("v", (_clip(1, 0.0),), (_label(0.0, "good", ("Lacy",)),))
    scheduled = schedule(
        build_queue([folder], CAMPAIGNS, PostLog()), date(2026, 11, 12), 1
    )
    page = queue_markdown(scheduled, Path("clips"))
    assert "## 1. 2026-11-12: v #1" in page
    assert "```text\nTitle 1\n```" in page
    assert "```text\nHook 1. With Lacy. #Lacy #Shorts\n```" in page
    assert "- Campaigns: Lacy" in page
    assert queue_markdown([], Path("clips")).endswith(
        "no saved clip rated good is left."
    )


def test_clip_key() -> None:
    assert clip_key("v1", "v1_000100-000130.mp4") == "v1/v1_000100-000130.mp4"
