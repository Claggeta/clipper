from pathlib import Path

import pytest

from clipper.adapters.rendering.fake import FakeClipRenderer
from clipper.adapters.results.labels_json import load_labels
from clipper.adapters.results.review_files import JsonReviewFiles
from clipper.domain.errors import JudgingError, ReviewError
from clipper.domain.models import (
    CaptionEdits,
    ClipCandidate,
    ClipVariant,
    ManifestClip,
    OnScreenText,
    RenderManifest,
    Segment,
    Transcript,
    Word,
)
from clipper.review.store import (
    BadRequest,
    RenderInputs,
    ReviewStore,
    SaveRequest,
    parse_caption_edits,
)

# Constructed manifest and transcript, not the output of a real run.

TRANSCRIPT = Transcript(
    segments=(
        Segment(
            start_seconds=12.0,
            end_seconds=14.0,
            text="Hello there.",
            words=(
                Word(start_seconds=12.0, end_seconds=12.6, text="Hello"),
                Word(start_seconds=12.7, end_seconds=13.4, text="there."),
            ),
        ),
    )
)


def _clip(rank: int, start: float, end: float, file: str) -> ManifestClip:
    return ManifestClip(
        rank=rank,
        start_seconds=start,
        end_seconds=end,
        score=0.5,
        reasons=("test",),
        file=file,
    )


def _manifest(*clips: ManifestClip, captions: bool = True) -> RenderManifest:
    return RenderManifest(
        source="talk.mp4",
        analysis="data/output/talk.json",
        detector="heuristic",
        vertical=True,
        captions=captions,
        titles=False,
        clips=clips,
    )


class Env:
    def __init__(
        self,
        tmp_path: Path,
        *clips: ManifestClip,
        renderer: FakeClipRenderer | None = None,
        captions: bool = True,
    ) -> None:
        self.folder = tmp_path / "clips" / "talk"
        self.folder.mkdir(parents=True)
        clips = clips or (_clip(1, 10.0, 40.0, "talk_1.mp4"),)
        for clip in clips:
            if clip.file is not None:
                (self.folder / clip.file).write_bytes(b"clip")
        self.labels_path = tmp_path / "labels" / "picks" / "talk.json"
        self.renderer = renderer or FakeClipRenderer()
        self.store = ReviewStore(
            self.folder,
            _manifest(*clips, captions=captions),
            None,
            self.labels_path,
            JsonReviewFiles(),
            RenderInputs(tmp_path / "talk.mp4", self.renderer, TRANSCRIPT),
        )

    def save(self, rank: int, start: float, end: float) -> ManifestClip:
        return self.store.save(rank, SaveRequest(start, end, None, None)).clip


def test_a_re_render_keeps_the_draft_span_so_a_second_save_finds_its_label(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)

    first = env.save(1, 9.0, 42.0)
    assert (first.start_seconds, first.end_seconds) == (9.0, 42.0)
    assert first.draft_span == (10.0, 40.0)
    assert first.captions == "words"

    second = env.save(1, 10.0, 40.0)
    assert second.file == "talk_000010-000040.mp4"
    assert second.draft_span == (10.0, 40.0)
    labels = load_labels(env.labels_path)
    assert [label.verdict for label in labels.labels] == ["post"]
    assert sorted(p.name for p in env.folder.iterdir()) == [
        "manifest.json",
        "talk_000010-000040.mp4",
    ]


def _titled(title: str | None) -> ManifestClip:
    return _clip(1, 10.0, 40.0, "talk_1.mp4").model_copy(
        update={"title": title, "hook": "A hook." if title else None}
    )


def test_the_first_save_keeps_the_models_title_and_later_ones_leave_it(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path, _titled("$134 to fill a tank"))

    first = env.store.save(1, SaveRequest(10.0, 40.0, "Ron at the pump", None))
    second = env.store.save(1, SaveRequest(10.0, 40.0, "Ron shocked", None))

    assert first.clip.model_title == "$134 to fill a tank"
    assert (second.clip.title, second.clip.model_title) == (
        "Ron shocked",
        "$134 to fill a tank",
    )


def test_a_first_trash_keeps_the_models_title(tmp_path: Path) -> None:
    env = Env(tmp_path, _titled("$134 to fill a tank"))

    env.store.trash(1, None)
    clip = env.store.save(1, SaveRequest(10.0, 40.0, "Mine", None)).clip

    assert (clip.title, clip.model_title) == ("Mine", "$134 to fill a tank")


def test_no_model_title_is_kept_as_none(tmp_path: Path) -> None:
    env = Env(tmp_path, _titled(None))

    clip = env.store.save(1, SaveRequest(10.0, 40.0, "Typed", None)).clip

    assert (clip.title, clip.model_title) == ("Typed", None)


def test_add_saves_a_new_clip_ranked_after_the_drafts(tmp_path: Path) -> None:
    env = Env(tmp_path)

    result = env.store.add(SaveRequest(100.0, 130.0, "Missed moment", "funny"))

    clip = result.clip
    assert (clip.rank, clip.added, clip.reviewed) == (2, True, "saved")
    assert (clip.score, clip.reasons) == (0.0, ("added in review",))
    assert (clip.file, clip.error, clip.title) == (
        "talk_000140-000210.mp4",
        None,
        "Missed moment",
    )
    assert clip.draft_span == (100.0, 130.0)
    assert (env.folder / "talk_000140-000210.mp4").exists()
    [label] = load_labels(env.labels_path).labels
    assert (label.verdict, label.draft, label.note) == ("added", None, "funny")
    assert [c.rank for c in env.store.manifest.clips] == [1, 2]


def test_saving_an_added_clip_again_keeps_added_and_moves_it(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.store.add(SaveRequest(100.0, 130.0, None, None))

    clip = env.save(2, 95.0, 131.0)

    assert (clip.start_seconds, clip.end_seconds) == (95.0, 131.0)
    assert clip.draft_span == (95.0, 131.0)
    [label] = load_labels(env.labels_path).labels
    assert (label.verdict, label.start_seconds) == ("added", 95.0)
    assert sorted(p.name for p in env.folder.iterdir()) == [
        "manifest.json",
        "talk_000135-000211.mp4",
        "talk_1.mp4",
    ]


def test_an_add_over_a_label_writes_nothing(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.save(1, 10.0, 40.0)

    with pytest.raises(JudgingError, match="Overlaps the label"):
        # 20-50 overlaps 10-40 by 20 s, over half of either 30 s span.
        env.store.add(SaveRequest(20.0, 50.0, None, None))

    assert [c.rank for c in env.store.manifest.clips] == [1]
    assert len(load_labels(env.labels_path).labels) == 1


def test_an_added_clip_whose_render_fails_records_the_error(tmp_path: Path) -> None:
    moved = ClipCandidate(
        start_seconds=100.0, end_seconds=130.0, score=0.0, reasons=("added in review",)
    )
    env = Env(tmp_path, renderer=FakeClipRenderer(fail_on=[moved]))

    result = env.store.add(SaveRequest(100.0, 130.0, None, None))

    assert result.render_error is not None
    assert (result.clip.file, result.clip.error) == (None, result.render_error)
    assert load_labels(env.labels_path).labels[0].verdict == "added"


def test_trash_removes_an_added_clip_outright(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.store.add(SaveRequest(100.0, 130.0, None, None))

    assert env.store.trash(2, "changed my mind") is None

    assert [c.rank for c in env.store.manifest.clips] == [1]
    labels = load_labels(env.labels_path)
    assert (labels.labels, labels.rejected) == ((), ())
    assert sorted(p.name for p in env.folder.iterdir()) == [
        "manifest.json",
        "talk_1.mp4",
    ]


def test_a_re_render_under_the_same_name_replaces_the_file(tmp_path: Path) -> None:
    env = Env(tmp_path, _clip(1, 10.2, 39.8, "talk_000010-000040.mp4"))

    clip = env.save(1, 10.1, 39.9)

    assert clip.file == "talk_000010-000040.mp4"
    assert (env.folder / clip.file).read_bytes() == b"fake clip"


def test_a_name_taken_by_another_clip_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    env = Env(
        tmp_path,
        _clip(1, 10.0, 40.0, "talk_1.mp4"),
        _clip(2, 60.0, 90.0, "talk_000011-000040.mp4"),
    )

    with pytest.raises(JudgingError, match="is clip 2's"):
        env.save(1, 11.0, 40.0)
    assert not env.labels_path.exists()
    assert env.renderer.calls == []


def test_a_failed_render_of_a_clip_without_a_file_records_the_error(
    tmp_path: Path,
) -> None:
    moved = ClipCandidate(
        start_seconds=10.0, end_seconds=40.0, score=0.5, reasons=("test",)
    )
    env = Env(tmp_path, renderer=FakeClipRenderer(fail_on=[moved]))
    env.store.trash(1, None)

    result = env.store.save(1, SaveRequest(10.0, 40.0, None, None))

    assert result.render_error == "Fake render failed for talk_000010-000040.part.mp4"
    assert (result.clip.reviewed, result.clip.file) == ("saved", None)
    assert result.clip.error == result.render_error
    assert [p.name for p in env.folder.iterdir()] == ["manifest.json"]


def test_an_old_file_that_cannot_be_deleted_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)
    real_unlink = Path.unlink

    def held_open(self: Path, missing_ok: bool = False) -> None:
        if self.name == "talk_1.mp4":
            raise PermissionError("in use")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", held_open)
    result = env.store.save(1, SaveRequest(9.0, 42.0, None, None))

    assert result.render_error is None
    assert result.warning == (
        "Re-rendered, but the old file talk_1.mp4 was not deleted: in use"
    )
    assert result.clip.file == "talk_000009-000042.mp4"


def test_without_captions_the_folder_re_renders_without_them(tmp_path: Path) -> None:
    env = Env(tmp_path, captions=False)

    clip = env.save(1, 9.0, 42.0)

    assert env.renderer.captions == [None]
    assert clip.captions is None


def test_a_span_without_speech_renders_without_captions(tmp_path: Path) -> None:
    env = Env(tmp_path, _clip(1, 100.0, 130.0, "talk_1.mp4"))

    clip = env.save(1, 100.0, 131.0)

    assert env.renderer.captions == [None]
    assert clip.captions is None


def test_a_file_that_cannot_be_deleted_leaves_the_manifest_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)
    store = env.store
    real_unlink = Path.unlink

    def held_open(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("in use by another process")

    monkeypatch.setattr(Path, "unlink", held_open)
    with pytest.raises(ReviewError, match="Cannot delete talk_1.mp4: in use"):
        store.trash(1, "dull")

    # The rejected span is written; the clip is not marked trashed yet.
    assert len(load_labels(env.labels_path).rejected)
    assert store.manifest.clips[0].reviewed is None

    monkeypatch.setattr(Path, "unlink", real_unlink)
    trashed = store.trash(1, "dull")
    assert trashed is not None
    assert trashed.reviewed == "trashed"
    assert len(store.labels.rejected if store.labels else ()) == 1


def test_trash_deletes_only_a_plain_file_name(tmp_path: Path) -> None:
    env = Env(tmp_path, _clip(1, 10.0, 40.0, "../talk_1.mp4"))

    with pytest.raises(ReviewError, match="not a file name"):
        env.store.trash(1, None)


def test_a_re_render_keeps_an_odd_old_file_name(tmp_path: Path) -> None:
    env = Env(tmp_path, _clip(1, 10.0, 40.0, "../talk_1.mp4"))

    result = env.store.save(1, SaveRequest(9.0, 42.0, None, None))

    assert (
        result.warning == "Re-rendered; not deleting '../talk_1.mp4': not a file name"
    )


def test_a_render_that_cannot_be_moved_into_place_keeps_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)

    def blocked(source: Path, target: Path) -> None:
        raise PermissionError("in use")

    monkeypatch.setattr("os.replace", blocked)
    result = env.store.save(1, SaveRequest(9.0, 42.0, None, None))

    assert result.render_error == "Cannot replace talk_000009-000042.mp4: in use"
    assert result.clip.file == "talk_1.mp4"
    assert sorted(p.name for p in env.folder.iterdir()) == [
        "manifest.json",
        "talk_1.mp4",
    ]


def _with_face(rank: int, start: float, end: float, file: str) -> ManifestClip:
    face = file.removesuffix(".mp4") + ".face.mp4"
    variants = (
        ClipVariant(style="fit", file=file),
        ClipVariant(style="face", file=face),
    )
    return _clip(rank, start, end, file).model_copy(update={"variants": variants})


def test_a_re_render_keeps_the_clips_style_and_drops_the_others(
    tmp_path: Path,
) -> None:
    clip = _with_face(1, 10.0, 40.0, "talk_1.mp4")
    env = Env(tmp_path, clip)
    (env.folder / "talk_1.face.mp4").write_bytes(b"face")

    saved = env.save(1, 9.0, 42.0)

    assert saved.variants == (ClipVariant(style="fit", file="talk_000009-000042.mp4"),)
    assert sorted(p.name for p in env.folder.iterdir()) == [
        "manifest.json",
        "talk_000009-000042.mp4",
    ]


def test_trash_deletes_every_style(tmp_path: Path) -> None:
    clip = _with_face(1, 10.0, 40.0, "talk_1.mp4")
    env = Env(tmp_path, clip)
    (env.folder / "talk_1.face.mp4").write_bytes(b"face")

    trashed = env.store.trash(1, None)

    assert trashed is not None
    assert (trashed.file, trashed.variants) == (None, ())
    assert sorted(p.name for p in env.folder.iterdir()) == ["manifest.json"]


def test_a_style_file_that_cannot_be_deleted_does_not_stop_trash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clip = _with_face(1, 10.0, 40.0, "talk_1.mp4")
    env = Env(tmp_path, clip)
    # A folder where the face file should be cannot be unlinked.
    (env.folder / "talk_1.face.mp4").mkdir()

    trashed = env.store.trash(1, None)

    assert trashed is not None and trashed.reviewed == "trashed"
    assert "face file talk_1.face.mp4 was not deleted" in caplog.text


def test_an_odd_style_file_name_is_not_deleted(tmp_path: Path) -> None:
    clip = _clip(1, 10.0, 40.0, "talk_1.mp4").model_copy(
        update={
            "variants": (
                ClipVariant(style="fit", file="talk_1.mp4"),
                ClipVariant(style="face", file="../elsewhere.mp4"),
            )
        }
    )
    env = Env(tmp_path, clip)

    saved = env.store.save(1, SaveRequest(9.0, 42.0, None, None))

    assert saved.warning == (
        "Re-rendered; not deleting '../elsewhere.mp4': not a file name"
    )
    assert [v.style for v in saved.clip.variants] == ["fit"]


class FakeFaceTracker:
    """One face at the frame's centre, or none."""

    def __init__(self, faces: bool = True) -> None:
        self._faces = faces

    def track(self, video_path: Path, start_seconds: float, end_seconds: float):  # type: ignore[no-untyped-def]
        from clipper.domain.models import FaceBox, FaceSample, FaceTrack

        face = FaceBox(x=0.45, y=0.2, w=0.1, h=0.3, score=0.9)
        return FaceTrack(
            source=video_path.name,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            samples_per_second=5.0,
            samples=(
                FaceSample(seconds=start_seconds, faces=(face,) if self._faces else ()),
            ),
        )


def _styled_env(tmp_path: Path, tracker: FakeFaceTracker | None = None) -> Env:
    env = Env(tmp_path, _with_face(1, 10.0, 40.0, "talk_1.mp4"))
    (env.folder / "talk_1.face.mp4").write_bytes(b"face")
    env.store._render = RenderInputs(  # noqa: SLF001
        tmp_path / "talk.mp4",
        env.renderer,
        TRANSCRIPT,
        tracker or FakeFaceTracker(),
        (1920, 1080),
    )
    return env


def test_keeping_a_style_without_a_re_render_drops_the_other(
    tmp_path: Path,
) -> None:
    env = _styled_env(tmp_path)

    result = env.store.save(1, SaveRequest(10.0, 40.0, None, None, "face"))

    assert result.clip.file == "talk_1.face.mp4"
    assert result.clip.variants == (ClipVariant(style="face", file="talk_1.face.mp4"),)
    assert env.renderer.calls == []
    assert sorted(p.name for p in env.folder.iterdir()) == [
        "manifest.json",
        "talk_1.face.mp4",
    ]
    [label] = load_labels(env.labels_path).labels
    assert (label.verdict, label.style) == ("post", "face")


def test_a_save_with_no_style_keeps_the_clips_own(tmp_path: Path) -> None:
    env = _styled_env(tmp_path)

    result = env.store.save(1, SaveRequest(10.0, 40.0, None, None))

    assert result.clip.file == "talk_1.mp4"
    assert [v.style for v in result.clip.variants] == ["fit"]
    assert not (env.folder / "talk_1.face.mp4").exists()


def test_a_re_render_in_the_face_style_crops_on_the_face(tmp_path: Path) -> None:
    env = _styled_env(tmp_path)

    result = env.store.save(1, SaveRequest(9.0, 42.0, None, None, "face"))

    assert result.render_error is None
    assert result.clip.file == "talk_000009-000042.face.mp4"
    [crop] = env.renderer.crops
    assert crop is not None and crop.width == 608
    assert sorted(p.name for p in env.folder.iterdir()) == [
        "manifest.json",
        "talk_000009-000042.face.mp4",
    ]


def test_a_face_re_render_without_a_face_keeps_the_old_files(
    tmp_path: Path,
) -> None:
    env = _styled_env(tmp_path, FakeFaceTracker(faces=False))

    result = env.store.save(1, SaveRequest(9.0, 42.0, None, None, "face"))

    assert result.render_error == "No face in the new span to frame in the face style"
    assert result.clip.file == "talk_1.mp4"
    assert (env.folder / "talk_1.face.mp4").exists()


def test_a_face_re_render_needs_the_tracker(tmp_path: Path) -> None:
    env = Env(tmp_path, _with_face(1, 10.0, 40.0, "talk_1.mp4"))

    result = env.store.save(1, SaveRequest(9.0, 42.0, None, None, "face"))

    assert result.render_error == (
        "A face re-render needs the face tracker and frame size"
    )


def test_a_style_the_clip_lacks_is_refused(tmp_path: Path) -> None:
    env = Env(tmp_path)
    with pytest.raises(JudgingError, match="no face draft"):
        env.store.save(1, SaveRequest(10.0, 40.0, None, None, "face"))
    assert not env.labels_path.exists()


@pytest.mark.parametrize("style", ["wide", 3, ""])
def test_a_bad_style_in_the_body_is_refused(style: object) -> None:
    from clipper.review.store import BadRequest, parse_save

    body = {"start": 1.0, "end": 20.0, "style": style}
    with pytest.raises(BadRequest, match="the style must be one of fit, face"):
        parse_save(body, 100.0)


def test_a_style_in_the_body_is_read() -> None:
    from clipper.review.store import parse_save

    assert parse_save({"start": 1.0, "end": 20.0, "style": "face"}, 100.0).style == (
        "face"
    )
    assert parse_save({"start": 1.0, "end": 20.0}, 100.0).style is None


def test_trash_logs_an_odd_style_file_name_and_still_trashes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clip = _clip(1, 10.0, 40.0, "talk_1.mp4").model_copy(
        update={
            "variants": (
                ClipVariant(style="fit", file="talk_1.mp4"),
                ClipVariant(style="face", file="../elsewhere.mp4"),
            )
        }
    )
    env = Env(tmp_path, clip)

    trashed = env.store.trash(1, None)

    assert trashed is not None and trashed.reviewed == "trashed"
    assert "Not deleting '../elsewhere.mp4': not a file name" in caplog.text


def test_a_rating_and_people_in_the_body_are_read_in_the_lists_order() -> None:
    from clipper.review.store import parse_save

    body = {"start": 1.0, "end": 20.0, "rating": "good", "people": ["Lacy", "Ron"]}
    request = parse_save(body, 100.0, ("Ron", "Jason", "Lacy"))
    assert (request.rating, request.people) == ("good", ("Ron", "Lacy"))
    plain = parse_save({"start": 1.0, "end": 20.0}, 100.0, ("Ron",))
    assert (plain.rating, plain.people) == (None, ())


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"rating": "great"}, "the rating must be one of good, ok"),
        ({"people": "Ron"}, "people must be a list of names"),
        ({"people": [3]}, "people must be a list of names"),
        ({"people": ["Keith"]}, "'Keith' is not in the people list"),
    ],
)
def test_a_bad_rating_or_people_in_the_body_is_refused(
    extra: dict[str, object], message: str
) -> None:
    from clipper.review.store import BadRequest, parse_save

    with pytest.raises(BadRequest, match=message):
        parse_save({"start": 1.0, "end": 20.0, **extra}, 100.0, ("Ron",))


def test_save_writes_the_rating_and_people_and_shows_them(tmp_path: Path) -> None:
    env = Env(tmp_path)
    request = SaveRequest(10.0, 40.0, None, None, rating="good", people=("Ron",))
    env.store.save(1, request)
    [label] = load_labels(env.labels_path).labels
    assert (label.rating, label.people) == ("good", ("Ron",))
    judgment = env.store.judgment(env.store.manifest.clips[0])
    assert judgment is not None
    assert (judgment["rating"], judgment["people"]) == ("good", ["Ron"])


def test_cuts_re_render_the_same_span_and_are_recorded(tmp_path: Path) -> None:
    env = Env(tmp_path)

    result = env.store.save(
        1, SaveRequest(10.0, 40.0, None, None, cuts=((13.5, 20.0),))
    )

    assert env.renderer.cuts == [((13.5, 20.0),)]
    assert result.clip.cuts == ((13.5, 20.0),)
    assert result.clip.file == "talk_000010-000040.mp4"
    [label] = load_labels(env.labels_path).labels
    assert label.verdict == "post"  # cuts do not move the span
    assert [(c.start_seconds, c.end_seconds) for c in label.cuts] == [(13.5, 20.0)]
    assert env.store.judgment(result.clip) is not None
    assert env.store.judgment(result.clip)["cuts"] == [[13.5, 20.0]]  # type: ignore[index]


def test_the_same_cuts_again_do_not_re_render_and_none_undo_them(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)
    request = SaveRequest(10.0, 40.0, None, None, cuts=((13.5, 20.0),))
    env.store.save(1, request)
    env.store.save(1, request)
    assert len(env.renderer.calls) == 1

    uncut = env.store.save(1, SaveRequest(10.0, 40.0, None, None)).clip
    assert len(env.renderer.calls) == 2
    assert env.renderer.cuts[-1] == ()
    assert uncut.cuts == ()


def test_captions_move_with_the_cuts(tmp_path: Path) -> None:
    env = Env(tmp_path)
    # "Hello" starts 2.0 s in; cutting 10.5 to 11.5 brings it to 1.0 s.
    env.store.save(1, SaveRequest(10.0, 40.0, None, None, cuts=((10.5, 11.5),)))
    captions = env.renderer.captions[0]
    assert captions is not None
    assert "Dialogue: 0,0:00:01.00," in captions


def test_cuts_in_the_body_are_read_and_checked() -> None:
    from clipper.review.store import BadRequest, parse_save

    body = {"start": 10.0, "end": 40.0, "cuts": [[12.0, 13.0], [20, 21.5]]}
    assert parse_save(body, 100.0).cuts == ((12.0, 13.0), (20.0, 21.5))
    for cuts, message in [
        ("x", "cuts must be a list"),
        ([[1.0]], "cuts must be a list"),
        ([["a", 2.0]], "must be a number of seconds"),
        ([[5.0, 12.0]], "must lie inside the clip"),
        ([[12.0, 14.0], [13.0, 15.0]], "must lie inside the clip"),
    ]:
        with pytest.raises(BadRequest, match=message):
            parse_save({"start": 10.0, "end": 40.0, "cuts": cuts}, 100.0)


def _hook(text: str = "Ron at the pump", place: str = "top") -> OnScreenText:
    return OnScreenText.model_validate(
        {"text": text, "start_seconds": 0.0, "end_seconds": 3.0, "place": place}
    )


def test_texts_re_render_and_are_recorded_on_manifest_and_label(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)

    request = SaveRequest(10.0, 40.0, None, None, texts=(_hook(),))
    clip = env.store.save(1, request).clip

    captions = env.renderer.captions[0]
    assert captions is not None
    assert "Ron at the pump" in captions
    assert clip.texts == (_hook(),)
    assert clip.captions == "words"
    [label] = load_labels(env.labels_path).labels
    assert label.texts == (_hook(),)
    assert env.store.judgment(clip)["texts"] == [  # type: ignore[index]
        {"text": "Ron at the pump", "start": 0.0, "end": 3.0, "place": "top"}
    ]
    env.store.save(1, request)
    assert len(env.renderer.calls) == 1  # the same texts do not re-render


def test_texts_burn_in_a_folder_without_captions(tmp_path: Path) -> None:
    env = Env(tmp_path, captions=False)

    request = SaveRequest(10.0, 40.0, None, None, texts=(_hook(),))
    clip = env.store.save(1, request).clip

    captions = env.renderer.captions[0]
    assert captions is not None
    assert "Ron at the pump" in captions
    assert "Hello" not in captions
    assert clip.captions is None


def test_texts_in_the_body_are_read_and_checked() -> None:
    from clipper.review.store import BadRequest, parse_save

    text = {"text": "Wait", "start": 1, "end": 2.5, "place": "middle"}
    request = parse_save({"start": 10.0, "end": 40.0, "texts": [text]}, 100.0)
    assert request.texts == (
        _hook("Wait", "middle").model_copy(
            update={"start_seconds": 1.0, "end_seconds": 2.5}
        ),
    )
    for texts, message in [
        ("x", "texts must be a list"),
        ([text] * 6, "at most 5"),
        (["x"], "each text must be an object"),
        ([{"text": "a", "start": "0", "end": 1}], "must be a number of seconds"),
        ([{"text": " ", "start": 0, "end": 1}], "text: a text is empty"),
        ([{"text": "a", "start": 2, "end": 1}], "end after it starts"),
        ([{"text": "a", "start": 0, "end": 1, "place": "bottom"}], "text: "),
    ]:
        with pytest.raises(BadRequest, match=message):
            parse_save({"start": 10.0, "end": 40.0, "texts": texts}, 100.0)


def test_a_thumbnail_is_written_beside_the_clip_and_recorded(tmp_path: Path) -> None:
    from clipper.review.store import ThumbnailRequest

    env = Env(tmp_path)
    text = _hook("Ron at the pump", "middle")

    clip, note = env.store.thumbnail(1, ThumbnailRequest(25.0, text))

    assert note is None
    assert clip.thumbnail == "talk_1.thumb.jpg"
    assert (env.folder / "talk_1.thumb.jpg").read_bytes() == b"fake jpeg"
    [(at, _, captions, vertical, crop)] = env.renderer.stills
    assert (at, vertical, crop) == (25.0, True, None)
    assert captions is not None
    assert "Ron at the pump" in captions
    # Making it again replaces it; no text means no captions.
    again, _ = env.store.thumbnail(1, ThumbnailRequest(30.0, None))
    assert again.thumbnail == "talk_1.thumb.jpg"
    assert env.renderer.stills[-1][2] is None


def test_a_face_clips_thumbnail_without_a_face_is_framed_as_fit(
    tmp_path: Path,
) -> None:
    from clipper.review.store import ThumbnailRequest

    env = _styled_env(tmp_path, FakeFaceTracker(faces=False))

    clip, note = env.store.thumbnail(1, ThumbnailRequest(25.0, None, "face"))

    assert note == "No face at that moment; framed as Fit."
    assert env.renderer.stills[0][4] is None
    assert clip.thumbnail is not None
    # With a face, the window is held on it; Fit is the clip's own style.
    env.store._render = RenderInputs(  # noqa: SLF001
        tmp_path / "talk.mp4", env.renderer, TRANSCRIPT, FakeFaceTracker(), (1920, 1080)
    )
    _, note = env.store.thumbnail(1, ThumbnailRequest(25.0, None, "face"))
    assert note is None
    assert env.renderer.stills[-1][4] is not None
    env.store.thumbnail(1, ThumbnailRequest(25.0, None))
    assert env.renderer.stills[-1][4] is None


def test_a_clip_without_a_file_has_no_thumbnail(tmp_path: Path) -> None:
    from clipper.review.store import ThumbnailRequest

    env = Env(tmp_path)
    env.store.trash(1, None)
    with pytest.raises(JudgingError, match="Save the clip before"):
        env.store.thumbnail(1, ThumbnailRequest(25.0, None))


def test_a_failed_thumbnail_keeps_the_old_one(tmp_path: Path) -> None:
    from clipper.domain.errors import ClipRenderError
    from clipper.review.store import ThumbnailRequest

    env = Env(tmp_path)
    env.store.thumbnail(1, ThumbnailRequest(25.0, None))
    env.renderer.fail_stills = True
    with pytest.raises(ClipRenderError):
        env.store.thumbnail(1, ThumbnailRequest(26.0, None))
    assert (env.folder / "talk_1.thumb.jpg").read_bytes() == b"fake jpeg"
    assert not list(env.folder.glob("*.part.jpg"))


def test_trash_deletes_the_thumbnail(tmp_path: Path) -> None:
    from clipper.review.store import ThumbnailRequest

    env = Env(tmp_path)
    env.store.thumbnail(1, ThumbnailRequest(25.0, None))
    trashed = env.store.trash(1, None)
    assert trashed is not None
    assert trashed.thumbnail is None
    assert not (env.folder / "talk_1.thumb.jpg").exists()


def test_thumbnail_bodies_are_checked() -> None:
    from clipper.review.store import BadRequest, parse_thumbnail, thumbnail_name

    plain = parse_thumbnail({"at": 12.5}, 100.0)
    assert (plain.at, plain.text) == (12.5, None)
    texted = parse_thumbnail({"at": 1, "text": " Hi ", "place": "top"}, 100.0)
    assert texted.text is not None
    assert (texted.text.text, texted.text.place) == ("Hi", "top")
    for body, message in [
        ({}, "at must be a number"),
        ({"at": 100.0}, "inside the video"),
        ({"at": 1, "text": "a\nb"}, "text: a text must be one line"),
        ({"at": 1, "text": "a", "place": "bottom"}, "text: "),
        ({"at": 1, "style": "wide"}, "the style must be one of"),
    ]:
        with pytest.raises(BadRequest, match=message):
            parse_thumbnail(body, 100.0)
    assert thumbnail_name("talk_000010-000040.face.mp4") == (
        "talk_000010-000040.thumb.jpg"
    )


# --- Caption edits (ADR 0017, caption editing amendment) ---


def test_caption_edits_are_saved_and_burned_and_re_render_only_on_change(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)
    env.save(1, 10.0, 40.0)
    edits = CaptionEdits.model_validate(
        {"fixes": [{"start_seconds": 12.0, "text": "Hi"}], "keys": [12.7]}
    )
    calls = len(env.renderer.calls)

    saved = env.store.save(1, SaveRequest(10.0, 40.0, None, None, caption_edits=edits))
    assert len(env.renderer.calls) == calls + 1
    assert "Hi{\\r}" in str(env.renderer.captions[-1])
    assert "Hello" not in str(env.renderer.captions[-1])
    assert saved.clip.caption_edits == edits
    [label] = load_labels(env.labels_path).labels
    assert label.caption_edits == edits

    env.store.save(1, SaveRequest(10.0, 40.0, None, None, caption_edits=edits))
    assert len(env.renderer.calls) == calls + 1
    cleared = env.store.save(1, SaveRequest(10.0, 40.0, None, None))
    assert len(env.renderer.calls) == calls + 2
    assert cleared.clip.caption_edits is None


def test_without_captions_edits_are_kept_but_render_nothing(tmp_path: Path) -> None:
    env = Env(tmp_path, captions=False)
    env.save(1, 10.0, 40.0)
    calls = len(env.renderer.calls)
    edits = CaptionEdits(highlight="pink")
    saved = env.store.save(1, SaveRequest(10.0, 40.0, None, None, caption_edits=edits))
    assert len(env.renderer.calls) == calls
    assert saved.clip.caption_edits == edits


def test_parse_caption_edits_checks_and_drops_empty_ones() -> None:
    assert parse_caption_edits(None) is None
    assert parse_caption_edits({"fixes": [], "keys": [], "highlight": "yellow"}) is None
    assert parse_caption_edits({"highlight": "cyan"}) == CaptionEdits(highlight="cyan")
    with pytest.raises(BadRequest, match="caption_edits: highlight"):
        parse_caption_edits({"highlight": "purple"})
    with pytest.raises(BadRequest, match="caption_edits"):
        parse_caption_edits([1, 2])
