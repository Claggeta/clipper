# 0021: GPU transcription measured, not adopted

- Status: Accepted
- Date: 2026-11-03

## Context

V4 item 7 (formerly V3 item 5) asked whether transcribing on the GPU would
make long videos usable faster: time the 5:04:01 stream on the GPU against
the CPU's 20:59, ending in a settings change or in nothing, with no `eval`
claim. `FasterWhisperTranscriber` runs on the CPU with `int8`; its
constructor already takes `device` and `compute_type`, but the CLI never
sets them.

The machine: Windows 11, RTX 4070 Ti 12 GB (driver 610.88), CTranslate2
4.8.4, faster-whisper 1.2.1, Whisper `base`.

## What was measured

A throwaway spike on 2026-11-03, with no repo change: NVIDIA's
`nvidia-cublas-cu12` 12.9.2.10 and `nvidia-cudnn-cu12` 9.27.0.42 were
installed into the project's environment for the spike only and removed
afterwards with `uv sync`. A scratch script ran Clipper's own
`VideoTranscriber` and `FasterWhisperTranscriber` cold (audio extraction and
model load included, no transcript cache) and compared each transcript's
words with the cached CPU transcript of the same file. Each run happened
once.

**Setup on Windows.** CTranslate2's wheel sees the GPU (`float16`,
`int8_float16`, `int8`, `float32` and others), but needs cuBLAS 12 and cuDNN
9 at run time, which were not installed. Without them the first
transcription raises `RuntimeError: Library cublas64_12.dll is not found or
cannot be loaded`. A second model load in the same process then hung in
`detect_language`; Clipper loads once, so it would see the error. With the
two packages installed, their `bin` folders had to be added to the DLL search
path (`os.add_dll_directory` and `PATH`) before loading the model.

**Short video**, `test_input.mp4` (5:14). Word match is the similarity of the
word sequences, ignoring case and punctuation:

| Run | Time | Segments | Word match with CPU | Periods / commas |
|---|---|---|---|---|
| CPU `int8` (today) | 15.4 s | 96 | 100% (reproduced the cache) | 39 / 67 |
| GPU `int8_float16` | 8.0 s | 92 | 99.7% | 37 / 65 |
| GPU `int8` | 9.8 s | 92 | 99.7% (same words as `int8_float16`) | 37 / 65 |
| GPU `float16` | 5.8 s | 53 | 98.4% | 3 / 1 |
| GPU `float32` | 6.1 s | 53 | 98.5% | 3 / 1 |

The unquantised types drop nearly all punctuation. The `int8` types keep it.

**Long video**, `oFbD0Ot7nQ8.mp4` (5:04:01), GPU `int8_float16`: 593.3 s,
against the CPU's 1,260 s (vision criterion 1, measured before word
timestamps; V4 item 3 found no measurable cost from them). About 2.1x
faster. GPU memory in use peaked at 2,816 MiB, about 0.5 GB above the
desktop's baseline, so it fits beside Ollama. For a long first stretch the
GPU was near idle while the CPU decoded the audio and ran the voice-activity
filter, which the GPU does not speed up.

The long transcript drifted from the CPU's: 87.4% word match (63.7% with
punctuation), 9,006 segments against 9,343, sentence-ending periods 1,711
against 8,029, question marks 379 against 696, and changed words such as
"Rest" to "Press". Whisper conditions each window on the text before it, so
small early differences carry forward. Which transcript is more accurate is
unknown: there is no reference transcript, and the label files cannot judge
transcription (ADR 0012).

## Decision

1. Transcription stays on the CPU with `int8`. No device setting, no `gpu`
   extra, no CUDA packages in the lock.
2. The measurements above are the record, so the experiment is not repeated
   by accident.

Reasons:

- The gain is modest: about 11 minutes saved on a 5-hour stream, in a
  workflow meant to run unattended, and part of the run is CPU-bound.
- The cost is real: about 1 GB of optional packages, Windows-specific
  DLL-path code, a second cache key, and a manual step.
- The GPU transcript changes over long videos and loses punctuation that two
  features use: caption chunks end at sentences (ADR 0017), and the
  heuristic's question signal counts `?` (known issue 2).

## Alternatives considered

- **Opt-in `CLIPPER_WHISPER_DEVICE=cuda` with `int8_float16`**, CPU still the
  default, the NVIDIA packages as an optional extra, a separate cache key so
  CPU and GPU transcripts never mix, and a manual step. Feasible from the
  spike. Not chosen for the reasons above.
- **GPU by default.** Rejected: it changes transcripts, and so which moments
  are picked, a V4 non-goal.
- **A system-wide CUDA Toolkit and cuDNN install.** Avoids project
  dependencies but needs a multi-GB manual install per machine. Not pursued.
- **Third-party bundles of the CUDA DLLs.** Rejected: untrusted binaries.

## Consequences

- (+) No new dependency, platform code or setting. Transcripts and caches
  stay as they are.
- (-) Long videos keep the CPU's time: about 21 minutes per 5-hour stream,
  once per video thanks to the cache.
- (-) Larger Whisper models stay impractical for long videos on the CPU.

## Revisit when

- A larger Whisper model (`small`, `medium`) is wanted. That is where the GPU
  pays, and it changes which moments are picked, so it belongs with picking
  (V5). The setup above is the starting point.
- Transcription time becomes the bottleneck of the unattended flow (V4 item
  8 measures the whole run).
- A CTranslate2 or faster-whisper release changes GPU output enough that a
  long transcript matches the CPU's closely; re-measure with the same
  comparison.

## Related

- 0002: Use faster-whisper
- 0003: Pin transcription dependencies (any CUDA packages would fall under it)
- 0012: Hand labels cannot judge transcription
- 0017: Caption chunks end at sentences
