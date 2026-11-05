# Vidupe Next v7.1: faster scanning + matching engine rewrite + preview fix

## v7.1: scan speed (new)
Profiling showed matching itself is cheap (~0.08 ms per pair); the time goes into **fingerprinting**, because every
exact seek decodes part of a GOP (about 9 s per 90 s of 1080p with a 250-frame GOP, on one core). ffmpeg flag tweaks
(probesize, threads, loop-filter, hwaccel) did not help, so the scan now does less decoding:
* **Fast mode (default):** quick pass on every video (~4-6 samples + keyframes, 1.7 s vs 8.9 s for the 1080p test file),
  then the full pass only for videos that have a possible partner. Cached, so rescans are quick.
* **Thorough mode:** full analysis of every video (v7 behaviour). Use it to find short clips cut from longer videos.
* GUI scan of the 37-video test set: Fast 18 s, Thorough 27 s (those files are tiny, so process start-up dominates;
  the gain is larger for HD and long videos). Fast mode found 8/9 true pairs on the slow-pan set (misses one clip,
  Thorough finds it) and 7/7 on the fast-motion set, 0 false pairs.
* Fixed a hang I introduced while adding the mode option (a Tk variable was read from the worker thread).


## Preview fix (app.py)
The preview was invisible because the image `Label` was created with `height=13`. For a label that shows an
image Tk measures `height` in **pixels**, so each preview was ~15 px tall. Now:
- each preview sits in an expanding stage frame and is scaled to the stage's real size,
- the two videos are shown **side by side** (each gets the full height of the pane),
- the bottom widgets are packed first so they can no longer squeeze the previews out.
Measured in a virtual display: old 633x15 px, new 304x267 px (1500x900 window) and 211x172 px (1100x700).

## New matching engine (engine.py)
| | old | new |
|---|---|---|
| Sampling | 12 keyframes from 3 spots, timestamps guessed | frame-accurate samples on a time grid over the whole video (short videos denser, long ones capped), 16 seeks per ffmpeg process |
| Hash | 64-bit pHash | 256-bit DCT hash + 63-bit shift-tolerant coarse hash, letterbox/pillarbox bars cropped first |
| Comparison | fixed-region offset bins | full frame-distance matrix, offset voting, score = share of the *overlapping* part that matches, static scenes discounted |
| Clips | partial | second layer matches scene-cut keyframes at a constant offset |
| Candidates | all pairs | all pairs up to 800 videos, LSH above that |

New UI setting: **Scan depth** (Quick 24 / Standard 48 / Deep 120 samples per video).
`Strict / Balanced / Sensitive` now map to `engine.PROFILES`.
The cache format changed; old fingerprints are ignored and rebuilt automatically.

## Measured results (synthetic footage, `python tests/benchmark.py`)
Two test sets with re-encodes, long-GOP/low-quality/HEVC copies, letterboxed copies, brightness changes,
clips cut at fractional offsets and 25+ look-alike negatives built from the same photos:
* slow-pan set, 9 true pairs: Strict 9/9, Balanced 9/9 (0 false pairs), Sensitive 9/9 (2 false pairs, both scored <65)
* fast-motion set, 7 true pairs: 7/7 in every profile, 0 false pairs
* old engine on the same sets (before the last test revisions): 1-8 of 9 and 3-6 of 7 depending on profile
* shipped benchmark (`tests/benchmark.py`): 8-9 of 10, 0 false pairs

## Things I learned the hard way (kept as comments in engine.py)
* Keyframe-only sampling returns the *previous* keyframe for a requested time (up to a whole GOP stale), so copies with different GOP sizes disagree.
* Skipping B-frames to speed up decoding made copies disagree even more (median distance 6 -> 41..71 bits). Rejected.
* ffmpeg duplicates frames unless `-fps_mode passthrough` is given, which broke keyframe timestamps.

## Known limitations
* Not tested on real-world footage or on Windows. All numbers above are synthetic.
* Exact seeking costs more than the old keyframe approach: roughly 10 s per 24 samples of 1080p with a very long GOP on ONE core (my worst case). Multi-core machines and shorter GOPs are much faster. Use Quick depth for huge libraries.
* A short clip cut from the middle of a much longer video can be missed if the long video's sample gap is larger than the clip (use Deep depth) or the cut is fractional on slow content (one benchmark case).
* Completely static or black videos have too few distinct frames and are reported as unreadable.
* Mirrored, rotated or speed-changed copies are not detected.
