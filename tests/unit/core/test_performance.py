import re
from datetime import date
from pathlib import Path

import pytest

from clipper.core.performance import (
    BAND_NAMES,
    DraftClip,
    ShortLink,
    by_channel,
    by_length,
    draft_clips,
    fold_title,
    group_shorts,
    length_band,
    link_shorts,
    matched_posts,
    parse_export_name,
    parse_table,
)
from clipper.core.posting import DraftsFolder
from clipper.domain.errors import PerformanceError
from clipper.domain.models import (
    ClipVariant,
    Combined,
    CombinedPart,
    ManifestClip,
    PostLog,
    PostRecord,
    RenderManifest,
    SequenceBlock,
    StudioRow,
)

# Tables shaped like the first real export (ADR 0027 amendment), cut down.

HEADER = (
    "Content,Video title,Video publish time,Duration,Engaged views,"
    "Average view duration,Average percentage viewed (%),Stayed to watch (%),"
    "Likes,Shares,Comments added,Subscribers gained,Views,Extra column"
)


def make_table(*rows: str) -> str:
    total = "Total,,,,4914,0:00:23,62.03,58.28,137,1,4,10,9242,x"
    return "\n".join([HEADER, total, *rows]) + "\n"


def test_parse_table_reads_figures_by_name_and_skips_the_total() -> None:
    rows = parse_table(
        make_table(
            'EGx,Ron Gets Heated,"Nov 11, 2026",43,863,0:00:21,49.43,65.63,'
            '29,0,0,0,"1,439",y'
        )
    )
    assert rows == (
        StudioRow(
            video_id="EGx",
            title="Ron Gets Heated",
            published=date(2026, 11, 11),
            duration_seconds=43,
            views=1439,
            engaged_views=863,
            stayed_to_watch=65.63,
            average_percentage_viewed=49.43,
            average_view_duration_seconds=21,
            likes=29,
            shares=0,
            comments=0,
            subscribers_gained=0,
        ),
    )


def test_parse_table_keeps_blanks_as_not_reported() -> None:
    (row,) = parse_table(make_table('tLk,Octopus,"Nov 11, 2026",44,,,,,,,,,,'))
    assert row.views is None
    assert row.stayed_to_watch is None
    assert row.average_view_duration_seconds is None


def test_parse_table_needs_only_the_four_columns() -> None:
    text = "Content,Video title,Video publish time,Duration\nabc,T,2026-11-12,30\n"
    (row,) = parse_table(text)
    assert (row.published, row.duration_seconds, row.views) == (
        date(2026, 11, 12),
        30,
        None,
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Content,Video title\nabc,T\n", "missing column(s): Video publish time"),
        (
            make_table('abc,T,"Nov 11, 2026",,1,0:01,1,1,1,1,1,1,1,x'),
            "Duration is blank",
        ),
        (make_table("abc,T,yesterday,30,,,,,,,,,,"), "row 3 (abc): Video publish"),
        (make_table('abc,T,"Nov 11, 2026",30,,1:2:3:4,,,,,,,,'), "not a time"),
        (make_table('abc,T,"Nov 11, 2026",30,,,lots,,,,,,,'), "not a number"),
        (
            make_table(
                'abc,T,"Nov 11, 2026",30,,,,,,,,,,', 'abc,T,"Nov 11, 2026",30,,,,,,,,,,'
            ),
            "abc is listed twice",
        ),
        (make_table(), "no Shorts listed"),
    ],
)
def test_parse_table_refuses_bad_tables(text: str, message: str) -> None:
    with pytest.raises(PerformanceError, match=re.escape(message)):
        parse_table(text)


def test_parse_export_name() -> None:
    assert parse_export_name("Content 2026-10-20_2026-11-17 Stream-o-sphere") == (
        date(2026, 10, 20),
        date(2026, 11, 17),
        "Stream-o-sphere",
    )
    assert parse_export_name("my export") is None


def test_fold_title_drops_case_emoji_and_punctuation() -> None:
    assert fold_title("Lacy Reacts 400 Flights Stuck in Air! ✈️") == fold_title(
        "lacy reacts 400 flights stuck in air"
    )
    assert fold_title("Café  déjà-vu.") == "cafe deja vu"


def manifest(*clips: ManifestClip, vertical: bool = True) -> RenderManifest:
    return RenderManifest(
        source="v1.mp4",
        analysis="a.json",
        detector=None,
        vertical=vertical,
        captions=True,
        titles=True,
        clips=clips,
    )


def clip(rank: int, **fields: object) -> ManifestClip:
    values: dict[str, object] = {
        "rank": rank,
        "start_seconds": 100.0,
        "end_seconds": 140.0,
        "score": 0.5,
        "reasons": ("r",),
        "file": f"v1_{rank}.mp4",
    }
    values.update(fields)
    return ManifestClip.model_validate(values)


def folder(
    m: RenderManifest, location: str | None = "Lacy/11-10-2026/v1"
) -> DraftsFolder:
    return DraftsFolder(
        name="v1",
        manifest=m,
        labels=None,
        channel="Lacy ",
        location=Path(location) if location else None,
    )


def test_draft_clips_lengths_titles_styles_and_day() -> None:
    m = manifest(
        clip(1, title="Mine", model_title="Model's", cuts=((110.0, 115.0),)),
        clip(2, sequence=(SequenceBlock(start_seconds=0, end_seconds=12),)),
        clip(
            3,
            file="v1_3.mp4",
            variants=(
                ClipVariant(style="fit", file="v1_3.mp4"),
                ClipVariant(style="face", file="v1_3.face.mp4"),
            ),
        ),
        clip(4, file="v1_4.face.mp4", reviewed="saved"),
        clip(5, file=None, error="failed"),
    ).model_copy(
        update={
            "combined": Combined(
                file="v1_combined.mp4",
                parts=(CombinedPart(rank=1),),
                duration_seconds=70.0,
                title="All of it",
            )
        }
    )
    drafts = draft_clips([folder(m)])
    assert [(d.key, d.titles, d.length_seconds, d.style) for d in drafts] == [
        ("v1/v1_1.mp4", ("Mine", "Model's"), 35.0, "fit"),
        ("v1/v1_2.mp4", (), 12.0, "fit"),
        ("v1/v1_3.mp4", (), 40.0, None),
        ("v1/v1_4.face.mp4", (), 40.0, "face"),
        ("v1/v1_combined.mp4", ("All of it",), 70.0, "combined"),
    ]
    assert {d.day for d in drafts} == {date(2026, 11, 10)}
    assert draft_clips([folder(m, location="v1")])[0].day is None
    assert draft_clips([folder(manifest(clip(1), vertical=False))])[0].style == (
        "landscape"
    )


def row(video_id: str, title: str, duration: int = 40, **fields: object) -> StudioRow:
    values: dict[str, object] = {
        "video_id": video_id,
        "title": title,
        "published": date(2026, 11, 12),
        "duration_seconds": duration,
    }
    values.update(fields)
    return StudioRow.model_validate(values)


def draft(key: str, title: str, length: float = 40.0, **fields: object) -> DraftClip:
    values: dict[str, object] = {
        "key": key,
        "titles": (title,),
        "length_seconds": length,
        "day": date(2026, 11, 10),
        "style": "fit",
        "channel": "Lacy",
    }
    values.update(fields)
    return DraftClip(**values)  # type: ignore[arg-type]


def test_link_shorts_by_posts_record_title_length_and_day() -> None:
    drafts = [
        draft("a/1.mp4", "Lacy Eats Fish"),
        draft("a/2.mp4", "Twice"),
        draft("b/2.mp4", "Twice"),
        draft("a/3.mp4", "Too Long", length=50.0),
        draft("a/4.mp4", "Too Late", day=date(2026, 11, 13)),
        draft("a/5.mp4", "Lacy Eats Squid"),
        draft("a/6.mp4", "Posted one"),
    ]
    posts = PostLog(
        posts=(
            PostRecord(
                clip="a/6.mp4", url="u", video_id="p6", posted=date(2026, 11, 12)
            ),
        )
    )
    links = link_shorts(
        [
            row("p6", "Renamed at upload"),
            row("m1", "Lacy Eats Fish 🐟!", duration=42),
            row("t2", "Twice"),
            row("l3", "Too Long"),
            row("d4", "Too Late"),
            row("n5", "Lacy Eats Squids"),
            row("n6", "Nothing like it"),
        ],
        drafts,
        posts,
    )
    got = [(x.row.video_id, x.status, x.draft and x.draft.key) for x in links]
    assert got == [
        ("p6", "posted", "a/6.mp4"),
        ("m1", "matched", "a/1.mp4"),
        ("t2", "ambiguous", None),
        ("l3", "ambiguous", None),
        ("d4", "ambiguous", None),
        ("n5", "none", None),
        ("n6", "none", None),
    ]
    assert links[2].candidates == ("a/2.mp4", "b/2.mp4")
    assert links[3].candidates == ("a/3.mp4",)
    assert links[5].close[0] == "Lacy Eats Squid (a/5.mp4)"
    assert links[6].close == ()


def test_close_titles_prefer_drafts_whose_length_fits() -> None:
    links = link_shorts(
        [row("n", "Lacy Eats Squid Live", duration=20)],
        [
            draft("a/1.mp4", "Lacy Eats Squid Live Again", length=60.0),
            draft("a/2.mp4", "Lacy Eats Squid", length=20.0),
        ],
        PostLog(),
    )
    assert links[0].close == ("Lacy Eats Squid (a/2.mp4)",)


def test_matched_posts_are_dated_when_published() -> None:
    links = [
        ShortLink(row("m1", "T"), "matched", draft=draft("a/1.mp4", "T")),
        ShortLink(row("p", "T"), "posted", draft=draft("a/2.mp4", "T")),
        ShortLink(row("n", "T"), "none"),
    ]
    assert matched_posts(links) == [
        PostRecord(
            clip="a/1.mp4",
            url="https://youtube.com/shorts/m1",
            video_id="m1",
            posted=date(2026, 11, 12),
        )
    ]


def test_length_band() -> None:
    assert [length_band(s) for s in (12, 30, 39.9, 45, 161)] == [
        "under 30 s",
        "30-40 s",
        "30-40 s",
        "40-50 s",
        "50 s +",
    ]


def test_group_shorts_medians_order_and_trimmed_channels() -> None:
    links = [
        ShortLink(row("a", "T", 45, views=10, stayed_to_watch=50.0), "none"),
        ShortLink(row("b", "T", 20, views=1000, stayed_to_watch=70.0), "none"),
        ShortLink(row("c", "T", 25, views=500), "none"),
        ShortLink(
            row("d", "T", 41, views=2),
            "posted",
            draft=draft("k", "T", channel="Lacy "),
        ),
    ]
    groups = group_shorts(links, by_length, BAND_NAMES)
    assert [(g.name, g.shorts, g.views, g.stayed_to_watch) for g in groups] == [
        ("under 30 s", 2, 750.0, 70.0),
        ("40-50 s", 2, 6.0, 50.0),
    ]
    assert [g.name for g in group_shorts(links, by_channel)] == ["Lacy"]
