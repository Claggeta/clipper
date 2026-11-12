# 0020: Fetch one YouTube video or Twitch VOD by URL, with yt-dlp

- Status: Accepted
- Date: 2026-11-03

## Context

V4's input is "a video URL (YouTube or Twitch) or a local file". Today the
maintainer downloads by hand with `uv tool run yt-dlp` into `data/videos/`,
named by video ID, then runs `analyze` on the file. `clipper fetch` would do
that step from the tool. The V4 list made this item start with the platforms'
terms, which nobody had checked.

### What the terms say

Read on 2026-11-03 from the platforms' own pages. This is the maintainer's
reading of a contract, not legal advice.

**YouTube**, Terms of Service at <https://www.youtube.com/static?template=terms>,
which showed "Dated: December 15, 2023". Under "Permissions and Restrictions",
you may not:

- "access, reproduce, download, distribute, transmit, broadcast, display, sell,
  license, alter, modify or otherwise use any part of the Service or any
  Content except: (a) as expressly authorized by the Service; or (b) with prior
  written permission from YouTube and, if applicable, the respective rights
  holders" (item 1);
- "access the Service using any automated means (such as robots, botnets or
  scrapers)", except public search engines under its robots.txt, or with
  YouTube's prior written permission (item 3);
- use the Service "to view or listen to Content other than for personal,
  non-commercial use" (item 9).

**Twitch**, Terms of Service at
<https://legal.twitch.com/en/legal/terms-of-service/>, "Last modified on
08/12/2026". The page builds its text with JavaScript, so the text was taken
from the page's HTML.

- Section 7, License: the licence is "to access and use the Twitch Services for
  your personal use or internal business use only", and does not permit
  "(d) use of any data mining, robots, or similar data gathering or extraction
  methods; (e) downloading (except page caching) of any portion of the Twitch
  Services, the Materials, or any information contained in them, except as
  expressly permitted on the Twitch Services". "Materials" there excludes User
  Content (section 8), which is what a streamer's VOD is.
- Section 8, User Content: other users get a licence to a streamer's content
  outside Twitch only "If this sharing functionality is turned on in your
  account settings", and "solely as necessary to enable such functionality".
- Section 10, Prohibited Conduct, xi: you will not "access any website,
  server, software application, or other computer resource owned, used, and/or
  licensed by Twitch, including but not limited to the Twitch Services, by
  means of any robot, spider, scraper, crawler, or other automated means for
  any purpose, or bypass any measures Twitch may use to prevent or restrict
  access".

Read plainly, both forbid downloading with a tool such as yt-dlp. YouTube's
clause (b) needs YouTube's written permission as well as the rights holder's,
so a campaign's permission alone does not meet it. Neither page grants a right
to download another creator's video.

**Campaign sources** (Vyro, Whop): what they supply, whether a file, a link to
one, or only a platform URL, is **unknown**. The maintainer will find out once
the clips are good enough to use.

## Decision

1. **`clipper fetch <url>` downloads one YouTube video or one Twitch VOD**,
   chosen by the maintainer, for the maintainer's own use. It is a
   convenience on top of local files, which stay the main input: `analyze`
   and `render` are unchanged and still take a file.
2. **One URL per call, by hand.** No playlists, channels, searches, schedules
   or monitoring. A playlist or channel URL is refused, not expanded. The tool
   does what the maintainer already does by hand, one video at a time; it does
   not crawl.
3. **No getting around access controls.** No cookies, logins, member-only or
   subscriber-only videos, and nothing that gets around DRM. A video the
   platform does not serve to a signed-out viewer fails with yt-dlp's
   message.
4. **The yt-dlp program, as a subprocess, behind a `Downloader` Protocol in
   `core/`.** It is not a Python dependency of Clipper: its releases follow the
   platforms' changes, and a pinned library would break the way the
   transcription stack did (0003). A missing or failing program is a clear
   error, not a fallback.
5. **Saved as `data/videos/<id>.<ext>`**, named by the platform's video ID as
   the hand downloads are, so `analyze` and the label files keep the same
   stem. The setting name, the container (`mp4` or what yt-dlp picks), and how
   an existing file is treated are decided in the implementing PR.
6. **The risk is the maintainer's and is stated where it is used.** The README
   and `fetch --help` say that downloading from YouTube or Twitch is against
   their terms as read on the date above, and that the user is responsible for
   having the right to download. Clipper is a personal tool; it is not offered
   as a download service.
7. A manual-test step runs `fetch` against one real public video per platform.
   CI never downloads.

## Alternatives considered

- **Only sources whose download is authorised**: the uploader's own videos
  through YouTube Studio or Twitch's creator tools, and direct file links from
  campaigns. Fits the terms, but campaign sources are unknown and it would
  likely cover little of the maintainer's real work. Not chosen now; it is
  the fallback under "Revisit when".
- **No `fetch`; local files only**, with downloading left outside the tool.
  Least work and no terms question inside the project, but loses the V4
  convenience of starting from a URL, and the maintainer would still download
  by hand. Not chosen.
- **yt-dlp as a Python library.** One process and typed calls, but a
  dependency that changes as often as the platforms do, inside the lock that
  0003 pins carefully. Rejected.
- **Official platform APIs.** Neither terms page offers a download of another
  creator's video; no API was checked for one. Not pursued.

## Consequences

- (+) `fetch <url>` then `analyze` then `render` replaces the hand download,
  and item 8 can start from a URL or a file.
- (+) No new Python dependency; yt-dlp updates on its own schedule.
- (-) Using `fetch` on YouTube or Twitch is against their terms as written.
  Possible results include blocked requests and action against an account,
  though `fetch` uses none. The maintainer accepts this for personal use.
- (-) yt-dlp breaks when the platforms change. A `fetch` failure then needs a
  yt-dlp update, not a Clipper change.
- (-) The repo is public, so this decision is visible to anyone reading it,
  including interviewers. The ADR is the record that it was made knowingly.

## Amended by the `fetch` PR (V4 item 6)

- **URL rules in pure code**, `parse_video_url` in `core/sources.py`, before
  yt-dlp runs, so decision 2 is enforced by Clipper and the file name is known
  first. Accepted: YouTube `watch?v=`, `youtu.be/`, `shorts/` and `live/` on
  `youtube.com`, `www.` or `m.`, and Twitch `twitch.tv/videos/<number>`.
  Query parameters other than `v` are ignored, so a video URL carrying
  `list=` names one video. Refused with `Error:` and exit 1, citing this ADR:
  playlist and channel URLs, Twitch channel (live) and clip URLs, and
  anything else. A URL shape not listed is refused until it is added.
- **`VideoSource`** (`domain/models.py`) holds the platform and the video ID.
  The ID is the file stem: YouTube's 11 characters, or `v` and the Twitch VOD
  number, which is also yt-dlp's Twitch ID (manual step D2).
- **`Downloader.download(source, folder) -> Path`** in `core/interfaces.py`,
  `DownloadError` in `domain/errors.py`. yt-dlp is given the plain URL built
  from the source (`video_url`), never the one typed.
- **`YtDlpDownloader`** (`adapters/download/ytdlp.py`) finds `yt-dlp` on
  `PATH`, like ffmpeg; a missing program names `uv tool install yt-dlp` and
  `uv tool update-shell`. Arguments: `--ignore-config` (the user's config,
  which could add cookies, is not read: decision 3), `--no-playlist`,
  `--match-filter "!is_live"`, `-S res:1080,ext:mp4:m4a`,
  `--merge-output-format mp4`, `--remux-video mp4`, `-P <folder>` and
  `-o <id>.%(ext)s`. yt-dlp's progress goes to the console; stderr is
  captured, and a non-zero exit is `yt-dlp failed (exit N): <last 10 lines>`
  through `format_failure` (0004). Exit 0 with no file (a live video, which
  the filter skips) is an error too.
- **1080p cap.** The vertical frame is 1080 wide, so more only costs disk. A
  4K horizontal clip would need it raised.
- **Codec is not chosen.** `ext:mp4` prefers the mp4 container, not H.264: in
  D1 YouTube gave AV1 video. `render` re-encodes (0007), and an AV1 source
  rendered `--vertical --captions` to H.264 in 5.7 s for a 41 s clip. If a
  player or an ffmpeg build cannot decode a fetched file, prefer `vcodec:h264`
  in the sort, in a further amendment.
- **An existing `<videos dir>/<id>.mp4` is reused**: `Already fetched:
  <path>`, no download. Delete it to fetch again. A failed download may leave
  yt-dlp's `.part` files, which it resumes on the next run.
- **Setting** `CLIPPER_VIDEOS_DIR`, default `data/videos`. `AppContext` gains
  `downloader(settings)`. `analyze` still takes only a file; one command from
  a URL is item 8.
- **First real run** (manual steps D0 to D8, 2026-11-03, yt-dlp 2026.10.26,
  run by the assistant in a child process): a 7:54 YouTube video in 7.5 s
  (50.8 MB, AV1 1280x720 and AAC, its highest resolution), a 7:32 Twitch VOD
  in 53.6 s (H.264 1920x1080 and AAC, `v1691281504.mp4`), a second fetch reused
  the file, the four refused URLs failed at once with their messages, a
  made-up YouTube ID gave yt-dlp's `This video is unavailable`, a missing
  yt-dlp gave the install hint, and `analyze` on the fetched file found 5
  candidates. yt-dlp warned on every YouTube request that no JavaScript
  runtime was found ("some formats may be missing"); downloads still worked.

## Amended by labels from Twitch community clips

- `clipper labels <links file>` (ADR 0012, its amendment for Twitch clips)
  asks yt-dlp for each listed clip's metadata, its length and title, with
  `--skip-download`. This is automated access to Twitch, so it falls under
  the risk decision 6 and the consequences accept, not outside it.
- It keeps to decision 2 in spirit: every clip link is chosen and pasted by
  the maintainer, one metadata request per listed clip, and nothing is
  listed, searched or crawled. Clipper does not look up a channel's or a
  VOD's clips; the links file is the only input.
- Decision 3 holds: `--ignore-config`, no cookies or logins.
- Clip URLs stay refused by `fetch`. Clips are not downloaded by Clipper;
  the downloads that checked the span rule were manual, in a scratch folder,
  and deleted.

## Amended by video info (V5 item 8a)

- `fetch`, and `drafts` from a URL, save the video's metadata beside it as
  `<videos dir>/<id>.info.json` (`video_info_path` in `core/sources.py`),
  for the title prompt (V5 item 8b, ADR 0018). It is written once: after a
  download, or for a reused file that has none yet. Local files get none.
- **One metadata request per fetch**, `--ignore-config --skip-download
  --no-playlist --dump-single-json` on the plain URL, through a
  `VideoInfoLookup` Protocol and `YtDlpVideoInfoLookup`. Like the clip
  lookups, it is automated access under the risk decision 6 accepts, for a
  video the maintainer chose (decision 2). The request does not depend on
  the file, so a reused file gets its info the same way, which settles the
  open question in V5 item 8a.
- **Only `VideoInfo` is kept** (`domain/models.py`): platform, video ID,
  title, channel (yt-dlp's `uploader`, else `channel`), `upload_date`
  (`YYYYMMDD`), duration and description. yt-dlp's own JSON is 20 KB for a
  Twitch VOD and 682 KB for a 2-hour YouTube video, mostly format lists with
  signed URLs, so `--write-info-json` was not used. A missing title or
  channel is a `DownloadError`.
- **A failed lookup is a printed warning**, `Warning: no video info saved:
  ...`, and the fetch still succeeds: the video is on disk, and only titles
  use the info. The next fetch of that video tries again.
- Checked by hand with yt-dlp 2026.10.26 (manual step D10a): a Twitch VOD
  gives its login as `uploader`, no `channel` and an empty description; a
  YouTube video gives the channel name for both and a description that
  opens with sponsor links. Neither names the people on screen.

## Amended by the remux PR (V6 item 10)

- After a download, and for a reused file, `fetch` (and so `drafts` from
  a URL) probes the video's start time. Past `MAX_START_SECONDS` (0.1 s,
  the review page's limit), a `Remuxer` rewrites it in place:
  `ffmpeg -i <file> -map 0 -c copy -movflags +faststart <stem>.part.mp4`,
  then the part file replaces the video. ffmpeg subtracts the input's
  start time unless given `-copyts`, so the copy starts at 0, losslessly;
  `+faststart` puts the index first for the browser. It prints
  `Remuxing <file>: it starts at <n> s` and `Remuxed: <path>`. A failure
  is the fetch's error, and leaves the video and no part file.
- A remuxed file is new bytes, so its transcript is made again once.
- `review`'s refusal now says to run `clipper fetch` on the URL again.
- v2924046310, the one file seen starting late (64 s), had already been
  remuxed by hand and starts at 0; no file at hand starts late, and
  ffmpeg's MP4 writer reset every test file made to start late to 0. On
  `test_input.mp4` the remuxer kept the duration and put the index
  first. Manual step D13 checks the next late download.

## Revisit when

- Either platform's terms change, or an account receives a warning.
- YouTube downloads fail or lose formats without a JavaScript runtime (yt-dlp
  asks for deno).
- Campaign sources become known. If they supply files or links, the
  authorised-sources alternative may cover the real work.
- `fetch` is wanted for anything beyond one video at a time (playlists,
  channels, schedules). That needs a new decision, since decision 2 rules it
  out.
- The project stops being for personal use only.

## Related

- 0003: Pin transcription dependencies (why yt-dlp is not a library here)
- 0004: ffmpeg error reporting (the subprocess error pattern)
- 0012: hand labels; its "Most replayed" idea also depends on YouTube's terms,
  and its Twitch-clips amendment reads clip metadata with yt-dlp
- 0019: A folder per video (named by stem, which `fetch` keeps as the ID)
