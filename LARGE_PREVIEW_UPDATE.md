# Larger video previews

- Default window enlarged to 1600 x 980 (minimum 1100 x 740).
- Preview panel now has at least 520 px of horizontal room and expands with the window.
- Both thumbnails use larger available areas and regenerate display images when the panel is resized.
- Preview extraction now retains up to 1280 x 720 pixels instead of reducing to 490 x 175.
- Double-click either thumbnail to open a resizable, large preview window. Press Escape to close.
- No scanning, matching, or deletion behavior was changed.

Rebuild on Windows with `powershell -ExecutionPolicy Bypass -File .\\build_windows.ps1`.
