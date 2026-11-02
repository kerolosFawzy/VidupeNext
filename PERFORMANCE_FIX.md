# Vidupe Next performance revision

Changes from FastScan:
- Three batched FFmpeg commands per video (4 keyframe-derived images per scene region), rather than one command per frame.
- Keyframe-only decoding on the initial pass avoids decoding whole videos for every frame.
- Lower-latency progress and less strict timeline requirements for candidate matches.
- Fingerprint cache bumped to version 6, so the first scan will rebuild the fingerprints; repeat scans reuse them.
- ffmpeg subprocesses remain hidden on Windows and scans use one worker.

Important: This is a fast candidate discovery implementation, not a validated high-precision final duplicate detector. Common scenes may produce false positives, and clips with heavy edits may be missed. Review all matches manually. Zero automatic deletions.

Local smoke test: synthetic 15-second pattern, transcoded and resized, was matched (6 timeline-aligned frames) and fingerprints for the pair computed in 0.73s in the test environment. This does not predict Windows performance or performance on your videos.

How to build locally: install Python 3.11+, FFmpeg and Inno Setup (optional); run `powershell -ExecutionPolicy Bypass -File .\build_windows.ps1` from this folder. Run newly built EXE, not old executable.
