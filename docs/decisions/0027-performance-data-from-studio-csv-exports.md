# 0027: Performance data from YouTube Studio CSV exports

- Status: Accepted
- Date: 2026-11-11
- Amends: the V5 non-goal "analytics or platform metrics" (CSV import
  only), and replaces the Parked item on performance data from posted
  clips (0012)

## Context

The maintainer posts Shorts to YouTube by hand, all from `drafts`
folders, and wants their performance to feed the learning in 0026. There
are three free ways to get it:

- **YouTube Studio's export**: a CSV the maintainer downloads by hand from
  the channel's analytics. No code talks to YouTube.
- **The YouTube Data API**: public counts (views, likes, comments) with an
  API key.
- **The YouTube Analytics API**: the channel's own retention and, for
  Shorts, viewed against swiped away, behind an OAuth login.

The maintainer chose the CSV (2026-11-11). Which columns a Shorts export
holds, and whether it includes retention or swipe-away figures, is
unverified: no export has been seen.

Drafts have no stable ID: files are named by time span inside a folder per
video (0008, 0019), and no record says which draft became which Short.

## Decision

1. **Imported from a CSV the maintainer exports.** No YouTube API, no API
   key, no OAuth, no login from Clipper. The import reads the file
   offline.
2. **The parser follows a real export.** The first step is the maintainer
   exporting one CSV; its columns are recorded in an amendment before any
   parser is written. Retention or swipe-away figures, if present, are
   preferred over views, which depend most on luck and channel size.
3. **Shorts are linked to drafts by matching**, for the posts already made
   and for new ones: title against the manifest's `title`, length against
   the clip's span, and publish date after the drafts folder was made.
   A match the rules cannot settle is shown to the maintainer to confirm
   or skip. Confirmed links are kept in one file and reused on the next
   import, so each Short is settled once.
4. **Each import is a dated snapshot.** Views grow with a Short's age, so
   figures are compared at similar ages or per day since posting, and per
   channel; how is set in the report item from the real data.
5. **Imported data stays out of git** (under `data/`, ignored), unlike
   the judged labels: it is the channel's private analytics.

## Alternatives considered

- **The Data API with a key.** Automatic, but public counts only, a key to
  keep, and a hosted service. Rejected; the CSV has at least the same
  counts.
- **The Analytics API with OAuth.** The best Shorts signals, but a login
  from a local tool and a token to keep. Rejected by the maintainer for
  now.
- **Record the YouTube ID by hand at upload time.** Exact, but a step the
  maintainer would skip, and it does nothing for the Shorts already
  posted. Matching covers both; a manual link can still be added if
  matching proves unreliable.

## Consequences

- Performance data arrives only when the maintainer exports, so it is
  weeks behind and as fresh as the last export.
- A title rewritten at upload after the manifest was saved weakens the
  match; length and date carry it, and the rest is confirmed by hand.
- Clips posted for campaigns are judged by the same numbers as the rest;
  campaign payouts are not imported.

## Amended by the first export (V8 rescope, 2026-11-17)

- Seen: one export of the channel's content, 2026-10-20 to 2026-11-17,
  saved by Studio as a folder named `Content <from>_<to> <channel>`
  holding three files:
  - `Table data.csv`: a `Total` row, then one row per Short (69). Its
    columns are those picked in Studio's column chooser; the default set
    had no retention figures, so the maintainer added them and exported
    again. The second export's 32 columns: `Content` (the YouTube video
    ID), `Video title`, `Video publish time` (`Nov 11, 2026`), `Duration`
    (whole seconds), `Engaged views`, `Average view duration` (`0:00:21`),
    `Average percentage viewed (%)`, `Stayed to watch (%)`, `Unique
    viewers`, `Unique reach`, `Views (co-viewed)`, `Average views per
    viewer`, `New viewers`, `Returning viewers`, `Casual viewers`,
    `Regular viewers`, `Subscribers gained`, `Subscribers lost`, `Likes`,
    `Dislikes`, `Likes (vs. dislikes) (%)`, `Shares`, `Comments added`,
    `Hypes`, `Hype points`, `Community clip views`, `Watch time from
    community clips (hours)`, `Views`, `Watch time (hours)`,
    `Subscribers`, `Thumbnail impressions`, `Thumbnail click-through rate
    (%)`.
  - `Chart data.csv`: views per Short per day (`Date`, `Content`, `Video
    title`, `Video publish time`, `Duration`, then the chosen metric).
  - `Totals.csv`: the channel's figure per day.
- Blank cells are common: 7 Shorts have no counts at all, 8 to 10 no
  retention figures, and the four viewer-type columns are blank on every
  row. A blank is "not reported", not 0.
- Both figures decision 2 prefers are present: `Stayed to watch (%)`
  (viewed against swiped away) and `Average percentage viewed (%)`. The
  parser reads columns by name and needs only `Content`, `Video title`,
  `Video publish time` and `Duration`; any other column is kept if
  present.
- Linking (decision 3): `Content` is the video ID, which
  `data/posts.json` already records for posts made with `clipper posted`
  (8 of the 69, all in the export), so those join exactly. The other 61
  go through the matching in decision 3.
- First figures, for the record: views median 6 and max 1,439. Of the
  60 Shorts posted on 2026-11-10 and 11, 2 passed 400 views; of the 9
  posted from 2026-11-12 on, at 1 to 3 a day, 6 did (491 to 1,357).
  Stayed to watch on the 8 over 400: 51 to 69%. Average
  percentage viewed by length: 81.5% under 30 s (4 Shorts), 56.2% at 30
  to 40 s (19), 53.6% at 40 to 50 s (27), 46.4% at 50 s or more (8).
  1 share and 4 comments in all. Small samples.

## Amended by the import (V8 item 6)

- `clipper performance <export folder> [--link]` reads `Table data.csv`
  by column name, copies the export's three files unchanged to
  `<performance dir>/<last day>/` (`CLIPPER_PERFORMANCE_DIR`, default
  `data/performance`, out of git; the day from the folder name, else
  today; a later import of the same day replaces them), and prints a
  report.
- Linking (decision 3): a Short whose video ID is in the posts record is
  linked to that clip. Otherwise its title is folded (case, accents,
  emoji and punctuation dropped) and compared with every clip's title and
  model title in every drafts folder, saved or not (drafts were posted
  before `review`), and with each combined clip's and rendered summary's.
  Its length must be within `LENGTH_TOLERANCE_SECONDS = 2.0` of the clip's
  (the span less cuts, or its sequence's blocks) and it must be published
  on or after the drafts folder's day. One draft fits: matched. Several
  fit, or titles match but length or day does not: ambiguous, the drafts
  listed. No title matches: up to 3 drafts with near titles, those whose
  length and day fit first, as `<title> (<folder>/<file>)`.
- `--link` adds each matched Short to `data/posts.json` as a post dated
  the day it was published, so the posts record is the file of confirmed
  links decision 3 names; an ambiguous or unfound Short is settled by hand
  with `clipper posted <folder>/<file> <link> --on <day>`. The record does
  not say which posts came from the import.
- The report: a row per Short (published, age at the export's last day,
  length, views, stayed to watch, average percentage viewed, likes, how it
  is linked), then medians of views, stayed to watch and average
  percentage viewed by length band (under 30, 30 to 40, 40 to 50, 50 s or
  more), by the draft's channel (spaces trimmed) and by style (the clip
  file's; none for a draft with several styles rendered).
- On the first export: 8 Shorts by the posts record, 7 matched and 54 not
  found. Most titles were rewritten at upload, so a near title is the
  usual lead. `Chart data.csv` (views per day, for views at a similar
  age) is kept but not read yet; manual step P1.

## Revisit when

- The export lacks figures the report needs, or matching leaves many
  Shorts unsettled.
- Performance becomes a training target (0026) and the export's lag
  starts to matter.

## Related

- 0008 and 0019 (clip names and folders), 0012 (labels), 0018 (titles),
  0020 (no logins for downloads), 0026 (the learning loop).
