# 0026: Learn from judged labels first, then from performance

- Status: Accepted
- Date: 2026-11-11
- Amends: the V5 non-goals (a trained model; analytics or platform
  metrics, in part, see 0027) and the "Later" item on posting and
  performance data

## Context

Nothing in Clipper changes with the maintainer's judgments. The LLM gets
the same prompt and the heuristic the same weights whichever drafts are
saved or trashed. Judged labels (0012) are used to measure changes made by
hand with `eval`, and every V5 picking decision was made that way.

The maintainer wants a tool that learns from its own use: the drafts they
keep, the titles they write, and, later, how the posted Shorts do on
YouTube. As of 2026-11-11:

- 12 videos judged in `review`: 105 keepers (55 `post`, 48 `fix`, 2
  `added`) and 59 rejected spans, in `data/labels/picks/`. Streamer VODs
  mostly, one business podcast.
- Edited titles beside the model's, in the manifests (`title` and
  `model_title`, 0018 and 0024). On 39 reviewed keepers the model named the
  person once; the maintainer rewrote most titles.
- Every Short the maintainer has posted came from `drafts`, but no record
  says which draft became which Short. The number posted is not recorded.
- The V5 non-goals ruled out a trained model and platform metrics.

The two signals differ:

| Signal | Says | For | Against |
|---|---|---|---|
| Judged labels | worth posting, to the maintainer | many, clean, include rejects, fast | one person's taste |
| YouTube performance | worth watching, to viewers | the real goal | slow, noisy, shaped by title, posting time and channel growth; only posted drafts have it, never the rejects |

## Decision

1. **Judged labels are the first training signal; performance comes
   later.** Performance joins only after a report on linked posts (0027)
   shows it says something the labels do not. Until then the labels carry
   the learning.
2. **In stages, each useful alone:** the maintainer's own titles and
   judged spans as examples in the LLM prompts; then a small trained
   ranker on the judged labels; then performance as a second target. Each
   stage is its own item and may end in nothing.
3. **Measured on videos it has not seen.** Every learned change is scored
   per VOD with the VOD's own labels left out of what it learned from
   (leave one VOD out), against the current default, with V5's measures:
   keepers and rejects in the top 10, keepers cut short, end gaps. Counts
   per VOD, not percentages. It is adopted only if it does better. A
   change that only does better on the VODs it learned from is not.
4. **Local and free.** Training and inference run on the maintainer's
   machine (CPU, or the RTX 4070 Ti). Pretrained encoders (text first;
   audio and video frames later) stay frozen and turn a span into
   embeddings; what is trained is small and sits on top: a simple
   classifier first, a small neural network once the labels are enough
   for it to do better on held-out VODs. Fine-tuning the encoders or the
   LLM, and networks trained from scratch, are out: they need far more
   labels than 4 to 10 VODs a week give. No hosted models or paid
   services. New dependencies go through 0003's check; a GPU deep-learning
   stack (PyTorch with CUDA) is several GB and comes only with an
   encoder that needs it.
5. **Embeddings are kept for lookup.** The embedding of every judged span
   (and, after 0027, every linked Short) is stored with its verdict and
   figures, so a new candidate can be compared with its nearest past
   clips: as a ranker input, to pick the LLM's prompt examples, and to
   explain a pick. A file is enough at this size (thousands of vectors,
   compared in milliseconds); a local embedded vector store (no server)
   only if a file stops being enough. Replaces the V3 non-goal "a vector
   database or retrieval", which had nothing to retrieve then.
6. **Retraining is a command the maintainer runs**, not a background job:
   it reads the current labels (and later the linked posts), trains,
   prints the held-out measures beside the current model's, and only then
   replaces it. The same data gives the same model (0015).
7. **Where labels come from does not change.** `review` stays the way
   drafts are judged; nothing is learned from drafts that were not judged.

## Alternatives considered

- **Learn from performance first.** It is the real goal, but there is no
  link from Shorts to drafts yet, the count is small, and it has no
  rejects. Rejected for now; decision 1 brings it in when it can carry
  weight.
- **Fine-tune the LLM on the judged labels.** Heavier, slower to iterate,
  and 164 judged spans is little to fine-tune on. Prompt examples get most
  of the benefit for a fraction of the cost. Revisit when the labels run
  to thousands.
- **Retrain automatically after each review.** Hides when the picks
  changed and why. Rejected for decision 6's explicit command.

## Consequences

- The maintainer's reviews start to change the drafts directly, not only
  through changes made by hand.
- Each judged VOD is worth more: it is both training data and, when left
  out, a test.
- A learned model is a new file to keep and version, and a new way for
  picks to drift; decision 6's printed comparison is the guard.
- Streamer VODs dominate the labels, so a learned model is tuned to them
  first; other niches need their own labels (as in the V5 scope).

## Amended by the ranker spike (V6 item 5, not adopted)

- Measured with scratch scripts, no product code: every 45 s window
  (`sliding_windows`) of 15 judged VODs embedded with `nomic-embed-text`
  through Ollama (already installed, no new dependency), with and without
  the heuristic's signal strengths as extra inputs; an L2 logistic
  regression with balanced classes, trained leave one VOD out on the
  other VODs' judged windows (a window on a keeper label is 1, on a
  rejected span 0, else unused: 377 and 204). 5pk8HLcAAT0 (podcast) and
  v2924046310 (start offset) left out.
- Embedding is cheap: about 13,400 windows in about 1.5 minutes in all,
  against 15 to 17 minutes per VOD for the LLM detector.
- Keeper over reject on the held-out VOD's judged windows (AUC): 0.59
  with signals only, 0.60 with embeddings, 0.61 with both; per VOD 0.24
  to 0.80, and on two VODs no rejects to compare.
- Its own top 10 cannot be scored yet: of 150 picks on 15 VODs, 141 to
  145 are unjudged, against 82 keepers, 38 rejects and 0 unjudged for
  the LLM's drafts on 12 VODs. The judged labels cover only the drafts
  the LLM and heuristic picked.
- Inside the LLM's judged top 10 (100 drafts, 10 VODs with both kinds),
  keeper over reject: the ranker 0.61, the LLM's own order 0.56; both
  near chance at this size.
- Decision: not adopted, nothing merged; the LLM stays `drafts`'
  detector. Rerun the same measure once the judged windows about double,
  or judge a ranker's top 10 on a few VODs in `review` if a faster
  detector is wanted sooner. No model file, so item 9 (retraining) has
  nothing to retrain yet. The script and embeddings are kept outside
  git, on the maintainer's machine.

## Revisit when

- Labels pass a few thousand judged spans (fine-tuning becomes plausible).
- The performance report (0027) shows a signal, or shows none after a few
  hundred linked posts.
- A learned model does worse on new VODs than on the held-out test
  (the test no longer stands for new work).

## Related

- 0012 (judged labels and `eval`), 0014 (the LLM detector), 0015
  (determinism), 0018 (titles), 0024 (the review page), 0027 (performance
  data).
