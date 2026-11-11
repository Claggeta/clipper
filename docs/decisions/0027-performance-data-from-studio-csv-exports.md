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

## Revisit when

- The export lacks figures the report needs, or matching leaves many
  Shorts unsettled.
- Performance becomes a training target (0026) and the export's lag
  starts to matter.

## Related

- 0008 and 0019 (clip names and folders), 0012 (labels), 0018 (titles),
  0020 (no logins for downloads), 0026 (the learning loop).
