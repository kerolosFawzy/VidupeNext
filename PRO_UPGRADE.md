# Vidupe Next Professional Review Upgrade

## Changes
- Up to 16 parallel ffmpeg fingerprint workers. Default is half logical CPUs, capped at 6.
- Cached fingerprints reused. Cache written in batches of 20 rather than for every video.
- Minimum match score filter applies to results, not initial fingerprint generation.
- Sort groups by number of files, savings, similarity, or name.
- Each file appears individually and can be selected with Ctrl/Shift.
- Select all copies selects all but one proposed keeper in each group.
- Recycle selected uses Windows Recycle Bin (send2trash), never permanent unlink.
- Reject deletion if all members of a connected match group are selected.

## Important limitations
- Confidence is a heuristic. Verify sample frames and footage before deleting.
- Choosing a keeper by resolution and filesize is not a guarantee of visual quality.
- Match candidate generation and similarity detection are inherited from the prior prototype; there is no full dataset performance benchmark.
- CPU/disk load may be high when worker count is raised. Try 4, 6, then 8 and measure performance.
- Scan speed on Windows has not been measured or validated here.
- This upgrade has no undo button inside the app. Use Windows Recycle Bin to restore files.

## Build
Install Python, FFmpeg/ffprobe, and optionally Inno Setup. From the project folder run:
`powershell -ExecutionPolicy Bypass -File .\build_windows.ps1`
