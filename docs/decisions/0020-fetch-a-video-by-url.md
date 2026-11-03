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

## Revisit when

- Either platform's terms change, or an account receives a warning.
- Campaign sources become known. If they supply files or links, the
  authorised-sources alternative may cover the real work.
- `fetch` is wanted for anything beyond one video at a time (playlists,
  channels, schedules). That needs a new decision, since decision 2 rules it
  out.
- The project stops being for personal use only.

## Related

- 0003: Pin transcription dependencies (why yt-dlp is not a library here)
- 0004: ffmpeg error reporting (the subprocess error pattern)
- 0012: hand labels; its "Most replayed" idea also depends on YouTube's terms
- 0019: A folder per video (named by stem, which `fetch` keeps as the ID)
