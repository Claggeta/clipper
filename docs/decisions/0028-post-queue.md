# 0028: A post queue of the best clips, and a record of posts

- Status: Accepted
- Date: 2026-11-11
- Amends: 0027 (a manual link from each post to its draft, alongside
  matching)

## Context

The maintainer posted many Shorts, most of what they kept, and believes
it cost views. They now want to post only the best clips, spread over
several videos and over time. Their review notes already split keepers
into "Good" (30) and "OK" (47); V6 item 4a put that rating, and who is on
screen, on each judged label (0024, rating amendment).

Three campaigns are open (CLAUDE.md, "Campaign briefs read"), each with
rules on who is on screen and on the caption: CORE needs two members and
`#CORE`, JasonTheWeen needs Jason and `#JasonTheWeen`, Lacy needs Lacy,
`#Lacy` and a clip from Lacy's own accounts. One clip may go to every
campaign it fits. Posting stays manual (the V6 non-goals); YouTube Studio
schedules uploads for free.

Drafts have no stable ID and no record of which became which Short; 0027
planned to match them by title, length and date.

## Decision

1. **`clipper queue`** lists the saved clips rated `good`, with a file, not
   yet posted, from every drafts folder under the clips dir. Folders come
   newest manifest first and the queue takes one clip from each in turn,
   in rank order, so two clips of one video are not next to each other
   while others wait. `--include-ok` adds the `ok` clips after all the
   good ones.
2. **A day per clip**: `--per-day N` (default 2) from `--start YYYY-MM-DD`
   (default today), for scheduling a week in YouTube Studio. The queue is
   computed again each run; nothing is reserved.
3. **A queue page**, `<clips dir>/queue.md` or `--out`: per clip its day,
   full path, title and caption in code blocks for copying, its campaigns
   and who is on screen, the layout the maintainer asked for by hand. The
   caption is the hook (or the title), "With <names>." and the hashtag of
   every campaign the clip fits, then `#Shorts`.
4. **Campaigns from a committed file**, `data/campaigns.txt`
   (`CLIPPER_CAMPAIGNS_FILE`): `<name> | <hashtag> | <minimum> | <people>
   [| <channels>]`. A clip fits when at least the minimum of the people are
   marked on screen and, with channels given, its video's channel (from
   its info file) is one of them; a clip that fits but for its channel is
   listed as "not from its channels", not tagged. People come only from
   the review page's marks, never guessed from the transcript.
5. **`clipper posted <clip> <YouTube link> [--on YYYY-MM-DD]`** records the
   post in `data/posts.json` (`CLIPPER_POSTS_FILE`, committed): the clip as
   `<folder>/<file>`, which a manifest must list, the link, its video ID
   and the day. A second post of the same clip replaces the first, and
   says so. A posted clip leaves the queue. The record is a list of links,
   not analytics, so it is committed like the labels (0027 keeps imported
   figures out of git).
6. **Links serve 0027**: a recorded post ties a Short to its draft exactly;
   the CSV import matches only Shorts without a record.

## Alternatives considered

- **Post everything and learn from the numbers.** The maintainer's
  experience is that volume costs views, and learning comes from reviews
  (0026), not from posting volume. Rejected.
- **A queue inside the review page.** More work in plain JavaScript, and
  the queue spans folders while a review run serves one. A command and a
  page in Markdown are enough to start.
- **Guess people from the transcript for campaigns.** Names in speech are
  often not who is on screen (a story about Ron told by Lacy). Rejected:
  only marked people count.
- **Upload or schedule from Clipper.** Out of scope (posting stays manual).

## Consequences

- Clips reviewed before item 4a have no rating and never enter the queue
  until rated again in `review`.
- The schedule assumes every listed clip gets posted in order; a skipped
  clip shifts the rest on the next run.
- Lacy's own-accounts rule is checked by channel name; a channel renamed
  or spelled differently in the info file needs a line change.

## Amended by relative paths for `posted` (V7 item 7f)

- The maintainer named a clip by a relative path (from the repo, and from
  the clips dir) and both were refused: only an absolute path or
  `<stem>/<file>` worked.
- A relative path is now tried from the current folder, then from the
  clips dir; the first that is a file under the clips dir is used as an
  absolute path would be. Otherwise it is `<stem>/<file>` as before. The
  clip must still be in its folder's manifest, and the record is still
  `<stem>/<file>`, so the queue and `posts.json` are unchanged. Manual
  step Q1.

## Amended by summaries (V7 item 9k-2, ADR 0032)

- A folder's summary rated `good` (or `ok` with `--include-ok`) and not
  yet posted joins the queue after that folder's clips, in its turn. Its
  people are everyone marked on its clips, in order, so its campaigns
  follow theirs; with no hook its caption opens with its title, as for
  any clip without one; its thumbnail is listed. It shows as `summary`
  in place of a rank, in the table and the page.
- `posted` accepts the summary's file (`<stem>/<stem>_summary.mp4`, or
  its path), and a posted summary leaves the queue. Manual step Q3.

## Revisit when

- Posting from the review page would save a step.
- The CSV import (0027) shows the good/ok rating predicts views, or does
  not.
- A campaign has a rule the file cannot say.

## Related

- 0019 (manifests), 0024 (the review page and its ratings), 0026 (learning),
  0027 (performance data).
