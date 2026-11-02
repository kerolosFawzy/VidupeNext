# Vidupe Next (experimental Windows desktop prototype)

Modern dark-mode desktop interface and a new multi-frame video similarity engine, built independently after studying the open-source [Vidupe](https://github.com/kristiankoskimaki/vidupe) project. This prototype is not affiliated with its author.

## Features
- Recursive folder scanning, FFmpeg/FFprobe media inspection
- Time-distributed pHash fingerprints, configurable sampling and sensitivity
- Candidate indexing to avoid checking every full pair
- Approximate timeline shift matching, including some trimmed/partial videos
- Per-pair confidence estimate, aligned-frame evidence, and coverage metric
- Video metadata and frame previews, grouped-match statistics, CSV export
- Persistent fingerprint cache keyed by file path, size, and modification time
- Read-only scan: never automatically deletes or moves videos

## Run on Windows
1. Install Python 3.11+ (check "Add Python to PATH").
2. Install [FFmpeg](https://ffmpeg.org/download.html), ensuring both `ffmpeg.exe` and `ffprobe.exe` are on PATH.
3. Open PowerShell in this folder and run:

```powershell
py -m pip install -r requirements.txt
py app.py
```

Optional packaged EXE (build locally on Windows):

```powershell
py -m pip install pyinstaller
pyinstaller --noconfirm --onedir --windowed --name VidupeNext app.py
```

The output is in `dist/VidupeNext/`. FFmpeg and FFprobe still need to be installed/on PATH.

## Caveats
This is a first prototype, not an accuracy-validated replacement. Visual similarity can produce false positives on common static shots, intros, logos, subtitles, and re-encoded scenes. Very short overlapping extracts and heavily altered/cropped videos can be missed. The confidence is a heuristic score **not a calibrated probability**. Check every candidate manually before deleting anything. The indexing may miss pairs when their pHashes have no identical 16-bit blocks. For huge collections, a more sophisticated approximate-nearest-neighbor index, stronger second-stage validation (SSIM or learned embeddings), and multi-resolution alignment are recommended.

Unchanged-size edits can still be detected because the cache includes nanosecond modification time; always re-run after video content changes. Cache location: `%APPDATA%\VidupeNext\fingerprints.json`.

## License
This independent prototype is offered under GNU GPLv3, consistent with the license of the source project studied. See LICENSE.txt.

## Make a Windows EXE or installer

### Automatic build via GitHub Actions (no local Python needed)
Upload the contents of this folder into a GitHub repository (including `.github/workflows/build-windows.yml`).
On GitHub, choose **Actions → Build Windows EXE and Installer → Run workflow**.
When finished, download the **VidupeNext-Windows-Installer** artifact and run `VidupeNext_Setup_0.1.0.exe`.
The alternative **VidupeNext-Windows-Portable** artifact contains `VidupeNext.exe` and its DLLs.
The workflow installs FFmpeg and includes `ffmpeg.exe` and `ffprobe.exe` in the packaged app.

### Build on your Windows computer
Install Python 3.11+, FFmpeg and optional Inno Setup 6. Run `powershell -ExecutionPolicy Bypass -File .\build_windows.ps1` in this folder. The app is in `dist/VidupeNext`; the installer is in `installer-output` when Inno Setup is installed.

This application has not been executed on Windows in the current environment. The GitHub Actions build is intended to produce an unsigned executable. Windows SmartScreen may prompt on first launch. Review the code and avoid deleting files without inspecting the matches.
