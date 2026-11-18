# 0035: A clip library as folders of B-roll

- Status: Accepted
- Date: 2026-11-17
- Amends: 0034 (B-roll files may sit in one library folder) and 0031 (the
  review page's bin)

## Context

Drafts are not good enough to post as cut, so the maintainer wants to
splice clips from different VODs into one Short with B-roll (V8 rescope,
2026-11-17): grab clips into folders kept on disk, start each edit with a
blank pad, and drag clips from a folder onto the editing timeline.

A clip's sequence (0034) is made of ranges of its own source video, or
B-roll files. A block from another VOD would need that VOD's video as a
render input, its transcript for captions, its face tracks for the face
style and its caption edits, so every place that assumes one source would
change.

A saved clip, though, is already a finished file: 1080x1920, its captions,
texts, cuts, zooms and sound burned in. B-roll inserts (0034, B-roll
amendment) already take a file, fit it to the frame (a 9:16 file fits
unchanged), play its own sound, and give it no captions of the clip it
lands in, which is right for a clip with its own.

## Decision

1. **The library is folders under the B-roll folder**
   (`CLIPPER_BROLL_DIR`, `data/broll/`): each subfolder is a library
   folder, its videos and stills are B-roll named `<folder>/<file>`, and
   the loose files stay B-roll as before. One level only; a folder name is
   1 to 40 letters, digits, spaces, hyphens, underscores or dots, not
   starting with a dot or ending with a dot or space.
2. **A clip goes into the library as a copy of its rendered file**, named
   after its title as the upload copies are (0028), `-2` and on for a name
   already there; a hard link where the disk allows, else a copy. What is
   copied is the file as last rendered, so a clip is saved first.
3. **A library clip is used as B-roll**: inserted on the Sequence lane (its
   own sound) or laid over the clip, rendered, previewed and checked by the
   B-roll code unchanged. Sequences store its `<folder>/<file>` name.
4. **The review page lists the library in the bin** (0031), by folder,
   with "To library" on a saved clip and a way to make a folder. A folder
   is made from the page or by hand in Explorer; files are removed by
   hand.
5. **Endpoints**: `GET /api/broll` also returns the folders; `GET
   /broll/<folder>/<file>`, each part URL-encoded and only listed files;
   `POST /api/library/folders` with a name; `POST
   /api/clips/<rank>/library` with a folder. Both POSTs need the page's
   Origin, as every write does (0024).

## Alternatives considered

- **Blocks from another VOD's source.** The most flexible (re-trim a
  borrowed clip, change its captions), but a core change across rendering,
  captions, faces and the time maps, for edits the maintainer can make in
  the borrowed clip's own folder first. Not now.
- **A library of VOD ranges, not files.** Cheap to keep, but each use would
  render from another source, the same core change.
- **A separate library folder and setting.** Clean names, but a second
  root that every B-roll path would have to know; folders under B-roll
  reuse it all.
- **Library items on the pad.** The pad holds ranges of this video (0034);
  the library sits in the same bin, a tab away, and its clips are dragged
  onto the lane from there.

## Consequences

- Splicing clips from several VODs needs no new render path, and a library
  clip looks exactly as it did when saved.
- A library clip cannot be re-trimmed or re-captioned inside another clip;
  that is done in its own folder, saved, and copied again.
- A removed or renamed library file makes the clips using it fail to
  re-render ("No B-roll file"), as with any B-roll.
- Library copies are under `data/`, out of git, and a copy costs disk space
  only where a hard link cannot be made (across drives).

## Amended by the page (V8 item 9c-2)

- The bin's B-roll tab is now **Library**: the loose B-roll under
  "B-roll", then each library folder folded open with its count, each
  clip with Insert and Overlay and draggable onto the Sequence lane as
  before. "New folder" opens a name box; "To library" copies the chosen
  clip into the folder picked in a list (remembered in local storage).
- To library refuses a new clip, one without a file, or one with
  unsaved edits ("Save first"), since the copy is the file as last
  rendered.
- A library clip is inserted whole (its length to the hundredth below);
  other B-roll still goes in for 3 s.
- Checked in headless Edge on a scratch copy of v2924392099 with a
  scratch B-roll folder: a folder made, a saved clip hard-linked in,
  inserted whole (37.75 s) into another clip, Save re-rendered it to
  76.75 s at 1080x1920. Manual step RV41.

## Revisit when

- The maintainer often wants to re-trim or re-caption a borrowed clip in
  place.
- Folders need nesting, renaming or deleting from the page.

## Related

- 0007 (one encode), 0024 and 0031 (the review page), 0028 (upload names),
  0034 (sequences, the pad and B-roll).
