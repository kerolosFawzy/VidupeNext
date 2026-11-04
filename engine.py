"""Vidupe Next, timeline fingerprint matching engine (v2).
Independent GPLv3-compatible implementation. ffmpeg and ffprobe must be in PATH.

How it works
  1. Fingerprint: ONE ffmpeg pass per video samples frames on a regular time grid across
     the WHOLE video (not just three spots). Letterbox/pillarbox bars are detected per video
     and cropped away, then every frame gets a 256-bit DCT perceptual hash.
  2. Candidates: frame hashes are split into 16 bands of 16 bits (LSH). Videos that share
     several band buckets become candidate pairs, so we never compare every pair.
  3. Verification: for each candidate pair a full frame-distance matrix is computed (one
     matrix multiply), the best time offset between the two videos is found by voting, and
     the match is scored by how much of the OVERLAPPING part of both videos really matches.
     Static frames (logos, black, title cards) are discounted by counting distinct scenes.
"""
from __future__ import annotations
import json, os, math, subprocess, hashlib, pathlib, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from collections import defaultdict
from typing import Callable
import numpy as np
import cv2

EXTENSIONS = {'.mp4','.mkv','.avi','.mov','.wmv','.m4v','.webm','.mpeg','.mpg','.flv','.3gp','.ts','.mts'}
CACHE_VERSION = 9
FP_TAG = 'timeline-v7'
MAX_FRAMES = 48            # default cap on samples per video (long movies get a wider grid)
MIN_SAMPLES = 30           # short videos are sampled at least this densely (helps find clips inside longer videos)
ALL_PAIRS_LIMIT = 800      # compare every pair up to this many videos; use LSH candidate search above
BATCH = 16                 # seeks per ffmpeg process
MAX_KEYFRAMES = 300        # keyframes kept per video for partial-overlap (clip) matching
DECODE_SIDE = 64           # ffmpeg returns 64x64 gray frames (aspect ratio is normalised)
HASH_BITS = 256
# name -> (max hash distance as a fraction of 256 bits, min fraction of the overlap that must match,
#          min overlap relative to the shorter video)
PROFILES = {'Strict': (.19, .65, .50), 'Balanced': (.23, .50, .30), 'Sensitive': (.27, .38, .15)}
SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
cv2.setNumThreads(1)


@dataclass
class Video:
    path: str
    duration: float
    width: int
    height: int
    size: int
    step: float            # seconds between samples
    times: list            # sample times (seconds)
    hashes: str            # concatenated 64-hex-char (256-bit) hashes, one per sample
    ktimes: list           # real timestamps of (up to MAX_KEYFRAMES) keyframes
    khashes: str           # their hashes. Keyframes sit on scene cuts, so they line up between a clip and its source


@dataclass
class Match:
    left: str
    right: str
    confidence: float
    aligned_frames: int
    covered: float         # share of the overlapping part of both videos that matches
    offset_seconds: float
    evidence: str


# ----------------------------------------------------------------------------- ffmpeg helpers
def _run(cmd, timeout, stop=None, want_err=False):
    """Run a subprocess, polling so that Stop responds quickly. Returns (returncode, stdout[, stderr])."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE if want_err else subprocess.DEVNULL,
                         creationflags=SUBPROCESS_FLAGS)
    start = time.time(); outs = {}
    def read(name, stream): outs[name] = stream.read()
    readers = [threading.Thread(target=read, args=('o', p.stdout), daemon=True)]
    if want_err: readers.append(threading.Thread(target=read, args=('e', p.stderr), daemon=True))
    for r in readers: r.start()
    try:
        while any(r.is_alive() for r in readers):
            readers[0].join(.25)
            if stop is not None and stop.is_set():
                p.kill(); raise InterruptedError('Scan cancelled')
            if time.time() - start > timeout:
                p.kill(); raise TimeoutError('ffmpeg timed out')
            if not readers[0].is_alive():
                for r in readers[1:]: r.join(1)
    finally:
        if p.poll() is None: p.kill()
        p.wait()
    return (p.returncode, outs.get('o', b''), outs.get('e', b'')) if want_err else (p.returncode, outs.get('o', b''))


def probe(path: str) -> tuple[float, int, int]:
    p = subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
        'stream=width,height:format=duration','-of','json',path],capture_output=True,text=True,timeout=20,creationflags=SUBPROCESS_FLAGS)
    if p.returncode: raise RuntimeError(p.stderr.strip()[:180] or 'ffprobe failed')
    obj=json.loads(p.stdout); stream=(obj.get('streams') or [{}])[0]
    return (float(obj.get('format',{}).get('duration') or 0),int(stream.get('width') or 0),int(stream.get('height') or 0))


def effective_step(duration: float, interval: float, max_samples: int = MAX_FRAMES) -> float:
    """Sampling step in seconds, restricted to 0.5 * 2^n. Because every step is a power-of-two multiple of
    0.5 s and samples sit at 0.25 + k*step, the grid of a short clip is a refinement of a long video's grid,
    which keeps samples of a trimmed clip close to samples of its source."""
    raw = min(interval, max(.5, duration / MIN_SAMPLES))     # short videos: at least ~30 samples
    raw = max(raw, duration / max_samples)                    # long movies: cap the sample count
    step = .5 * 2 ** round(math.log2(raw / .5))
    if duration / step > max_samples * 1.5: step *= 2
    return step


def sample_times(duration: float, step: float) -> list[float]:
    n = max(1, int((duration - .25) / step) + 1)
    return [round(min(duration - .2, .25 + k * step), 3) for k in range(n)]


def _decode_samples(path: str, times: list[float], stop=None, fast: bool = True):
    """Frame-accurate gray DECODE_SIDE^2 frames at the given times.
    Up to BATCH seeks share ONE ffmpeg process (no per-frame process start-up cost).
    fast=True skips only the deblocking filter (~0.3/255 mean pixel change, ~15% faster).
    Skipping B-frames was tested and REJECTED: it returns a different nearby frame for each copy, which
    wrecks matching on fast-moving footage (median hash distance 6 -> 41..71 bits)."""
    import tempfile
    side = DECODE_SIDE; out_t, out_f = [], []
    opts = (['-skip_loop_filter', 'all'] if fast else [])
    with tempfile.TemporaryDirectory(prefix='vidupe_') as tmp:
        for s0 in range(0, len(times), BATCH):
            chunk = times[s0:s0 + BATCH]
            cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']
            for t in chunk: cmd += opts + ['-ss', f'{t:.3f}', '-i', path]
            for k in range(len(chunk)):
                cmd += ['-map', f'{k}:v:0', '-an', '-sn', '-dn', '-frames:v', '1',
                        '-vf', f'scale={side}:{side}:flags=area,format=gray', '-f', 'rawvideo',
                        os.path.join(tmp, f'{s0 + k}.raw')]
            _run(cmd, max(90, 20 * len(chunk)), stop)
            for k, t in enumerate(chunk):
                fp = os.path.join(tmp, f'{s0 + k}.raw')
                if os.path.exists(fp) and os.path.getsize(fp) == side * side:
                    out_t.append(t); out_f.append(np.fromfile(fp, dtype=np.uint8).reshape(side, side))
    return out_t, out_f


def _decode_keyframes(path: str, duration: float, stop=None):
    """All keyframes (cheap: only keyframes are decoded) with their real timestamps via showinfo."""
    import re
    side = DECODE_SIDE
    data = b''
    for sync in (['-fps_mode', 'passthrough'], ['-vsync', '0']):   # no frame duplication; older ffmpeg needs -vsync
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'info', '-nostdin', '-skip_frame', 'nokey', '-i', path, '-an', '-sn', '-dn',
               '-vf', f'scale={side}:{side}:flags=area,format=gray,showinfo'] + sync + \
              ['-frames:v', '4000', '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1']
        _, data, err = _run(cmd, max(60, duration * .25), stop, want_err=True)
        if data: break
    stamps = [float(x) for x in re.findall(rb'pts_time:\s*(-?[0-9.]+)', err)]
    count = min(len(stamps), len(data) // (side * side))
    if count == 0: return [], []
    frames = np.frombuffer(data[:count * side * side], dtype=np.uint8).reshape(count, side, side)
    idx = np.unique(np.linspace(0, count - 1, min(count, MAX_KEYFRAMES)).round().astype(int))
    return [stamps[i] for i in idx], [frames[i] for i in idx]


def _content_box(frames: np.ndarray):
    """Bounding box of the real picture (removes black letterbox / pillarbox bars)."""
    mx = frames.max(axis=0)
    rows = np.flatnonzero(mx.max(axis=1) > 32); cols = np.flatnonzero(mx.max(axis=0) > 32)
    if len(rows) < 8 or len(cols) < 8: return 0, frames.shape[1], 0, frames.shape[2]
    r0, r1, c0, c1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    full = frames.shape[1]
    if (r1 - r0) < .4 * full or (c1 - c0) < .4 * full: return 0, full, 0, full
    if (r0 < 2 and full - r1 < 2 and c0 < 2 and full - c1 < 2): return 0, full, 0, full
    return int(r0), int(r1), int(c0), int(c1)


def phash256(gray: np.ndarray) -> bytes | None:
    """256-bit DCT hash of a gray image. Returns None for flat (blank/black) frames."""
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    if float(small.std()) < 4: return None
    coeffs = cv2.dct(small)[:16, :16].ravel()
    ac = coeffs[1:]
    bits = np.zeros(HASH_BITS, dtype=bool)
    bits[1:] = ac > np.median(ac)
    return np.packbits(bits).tobytes()


def analyze(path: str, interval: float = 4.0, stop: threading.Event | None = None, frame_progress=None,
            max_samples: int = MAX_FRAMES) -> Video:
    duration, w, h = probe(path)
    if duration <= 0 or not w or not h: raise ValueError('No valid video track/duration')
    step = effective_step(duration, interval, max_samples)
    wanted = sample_times(duration, step)
    got_t, got_f = _decode_samples(path, wanted, stop, fast=True)
    if len(got_t) < max(3, len(wanted) // 2):                    # shortcut options unsupported/failed: retry plainly
        got_t, got_f = _decode_samples(path, wanted, stop, fast=False)
    if not got_f: raise ValueError('No readable frames (check ffmpeg codecs)')
    frames = np.stack(got_f)
    r0, r1, c0, c1 = _content_box(frames)
    times, hashes = [], []
    for t, f in zip(got_t, frames):
        hsh = phash256(f[r0:r1, c0:c1])
        if hsh is not None: times.append(t); hashes.append(hsh.hex())
    if len(times) < 3: raise ValueError('Too few distinctive frames (black or static video)')
    ktimes, khashes = [], []
    try:
        kt, kf = _decode_keyframes(path, duration, stop)
        for t, f in zip(kt, kf):
            hsh = phash256(f[r0:r1, c0:c1])
            if hsh is not None: ktimes.append(round(t, 3)); khashes.append(hsh.hex())
    except InterruptedError: raise
    except Exception: pass                                           # keyframe layer is optional
    if frame_progress: frame_progress(1, 1)
    return Video(path, duration, w, h, os.path.getsize(path), round(step, 4), times, ''.join(hashes), ktimes, ''.join(khashes))


# ----------------------------------------------------------------------------- files and cache
def iter_files(folders: list[str]):
    seen = set()
    for folder in folders:
        for root, _, files in os.walk(folder):
            for name in files:
                p = os.path.abspath(os.path.join(root, name))
                if pathlib.Path(p).suffix.lower() in EXTENSIONS and p not in seen:
                    seen.add(p); yield p


def fingerprint_key(path: str) -> str:
    s = os.stat(path)
    return hashlib.sha256((os.path.normcase(path)+'|'+str(s.st_size)+'|'+str(s.st_mtime_ns)).encode()).hexdigest()


def read_cache(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f: data = json.load(f)
        if data.get('version') == CACHE_VERSION: return data.get('videos', {})
    except (OSError, ValueError, TypeError): pass
    return {}


def save_cache(path: str, cache: dict):
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as f: json.dump({'version': CACHE_VERSION, 'videos': cache}, f)
    os.replace(temp, path)


# ----------------------------------------------------------------------------- matching
def _scenes(bits: np.ndarray) -> np.ndarray:
    """Scene id per frame: a new scene starts when a frame differs a lot from the last scene anchor."""
    scene = np.zeros(len(bits), dtype=np.int32); anchor = 0; sid = 0
    for i in range(1, len(bits)):
        if .5 - float(bits[anchor] @ bits[i]) / (2 * HASH_BITS) > .12: sid += 1; anchor = i
        scene[i] = sid
    return scene


def _bits(hexstr: str):
    raw = np.frombuffer(bytes.fromhex(hexstr), dtype=np.uint8).reshape(-1, HASH_BITS // 8)
    return raw, np.unpackbits(raw, axis=1).astype(np.float32) * 2 - 1


# Low-frequency 8x8 block of the 16x16 DCT hash (63 bits). It survives small shifts/time jitter that flip fine bits.
_COARSE = np.array([r * 16 + c for r in range(8) for c in range(8) if r or c])
COARSE_PENALTY = float(os.environ.get('VIDUPE_COARSE_PENALTY', '0.10'))


class _Prep:
    """Numpy view of a Video used for comparison: +/-1 bit matrices, times and scene ids."""
    __slots__ = ('t', 'bits', 'cbits', 'scene', 'step', 'duration', 'packed', 'kt', 'kbits', 'kscene')
    def __init__(self, v: Video):
        self.packed, self.bits = _bits(v.hashes)
        self.cbits = self.bits[:, _COARSE]
        self.t = np.asarray(v.times, dtype=np.float64)
        self.step = v.step; self.duration = v.duration
        self.scene = _scenes(self.bits)
        if v.khashes:
            _, self.kbits = _bits(v.khashes); self.kt = np.asarray(v.ktimes, dtype=np.float64); self.kscene = _scenes(self.kbits)
        else:
            self.kbits = np.zeros((0, HASH_BITS), np.float32); self.kt = np.zeros(0); self.kscene = np.zeros(0, np.int32)


def _distance(a: _Prep, b: _Prep) -> np.ndarray:
    """Normalised Hamming distance matrix (0 = identical, .5 = unrelated) via matrix multiplies.
    A pair is as close as its fine hash, or its coarse (shift-tolerant) hash plus a penalty, whichever is lower."""
    fine = .5 - (a.bits @ b.bits.T) / (2 * HASH_BITS)
    if COARSE_PENALTY >= .5: return fine
    coarse = .5 - (a.cbits @ b.cbits.T) / (2 * len(_COARSE))
    return np.minimum(fine, coarse + COARSE_PENALTY)


def candidates(preps: list[_Prep], min_votes: int = 2, bucket_cap: int = 64):
    """LSH over 16 bands x 16 bits. Pairs sharing >= min_votes distinct buckets are candidates."""
    n = len(preps)
    if n <= ALL_PAIRS_LIMIT: return [(i, j) for i in range(n) for j in range(i + 1, n)]
    keys = []; vids = []
    for i, p in enumerate(preps):
        bands = p.packed.view('<u2')                                  # (frames, 16)
        k = bands.astype(np.int64) + (np.arange(16, dtype=np.int64) * 65536)[None, :]
        keys.append(k.ravel()); vids.append(np.full(k.size, i, dtype=np.int64))
    keys = np.concatenate(keys); vids = np.concatenate(vids)
    order = np.lexsort((vids, keys)); keys = keys[order]; vids = vids[order]
    keep = np.ones(len(keys), dtype=bool); keep[1:] = (keys[1:] != keys[:-1]) | (vids[1:] != vids[:-1])
    keys = keys[keep]; vids = vids[keep]                              # unique (bucket, video)
    bounds = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1], True])
    votes = defaultdict(int)
    for s, e in zip(bounds[:-1], bounds[1:]):
        if e - s < 2 or e - s > bucket_cap: continue
        members = vids[s:e].tolist()
        for x in range(len(members)):
            for y in range(x + 1, len(members)): votes[(members[x], members[y])] += 1
    return [pair for pair, v in votes.items() if v >= min_votes]


def _align(ta: np.ndarray, tb: np.ndarray, D: np.ndarray, mask: np.ndarray, offset: float, tol: float):
    """Count frames of A that match a frame of B near t_b = t_a + offset (one-to-one)."""
    expect = ta[:, None] + offset
    ok = mask & (np.abs(tb[None, :] - expect) <= tol)
    masked = np.where(ok, D, np.inf)
    j = masked.argmin(axis=1); d = masked[np.arange(len(ta)), j]
    rows = np.flatnonzero(np.isfinite(d))
    if len(rows) == 0: return None
    best = {}
    for r in rows:                                    # keep the closest A frame for each B frame
        if j[r] not in best or d[r] < d[best[j[r]]]: best[j[r]] = r
    rows = np.array(sorted(best.values()))
    return rows, j[rows], d[rows]


def compare(a: _Prep, b: _Prep, A: str, B: str, max_dist=.22, min_ratio=.55, min_overlap=.30) -> Match | None:
    if len(a.t) < 3 or len(b.t) < 3: return None
    D = _distance(a, b); mask = D <= max_dist
    if not mask.any(): return None
    # hits: best partner of every frame (rows and columns), within the distance limit
    hits = set()
    for i in np.flatnonzero(mask.any(axis=1)): hits.add((int(i), int(D[i].argmin())))
    for j in np.flatnonzero(mask.any(axis=0)): hits.add((int(D[:, j].argmin()), int(j)))
    hits = [(i, j) for i, j in hits if mask[i, j]]
    if len(hits) < 2: return None
    ia = np.array([h[0] for h in hits]); ja = np.array([h[1] for h in hits])
    off = b.t[ja] - a.t[ia]
    tol = max(a.step, b.step) * .8 + .4
    srt = np.sort(off)
    counts = np.searchsorted(srt, srt + tol, 'right') - np.searchsorted(srt, srt - tol, 'left')
    # evaluate the three strongest distinct offset candidates, keep the best-scoring alignment
    cand = []; seen = []
    for k in np.argsort(-counts):
        o = float(srt[k])
        if all(abs(o - s) > 2 * tol for s in seen):
            seen.append(o); cand.append(o)
        if len(cand) == 3: break
    best = None
    for o0 in cand:
        near = off[np.abs(off - o0) <= tol]
        o = float(np.median(near)) if len(near) else o0
        res = _align(a.t, b.t, D, mask, o, tol)
        if res is None: continue
        rows, cols, dist = res
        in_a = int(np.sum((a.t + o >= -tol) & (a.t + o <= b.duration + tol)))
        in_b = int(np.sum((b.t - o >= -tol) & (b.t - o <= a.duration + tol)))
        expected = max(1, min(in_a, in_b))
        ratio = min(1.0, len(rows) / expected)
        key = (len(rows) * ratio, -float(dist.mean()))
        if best is None or key > best[0]: best = (key, o, rows, cols, dist, expected, ratio)
    if best is None: return None
    _, o, rows, cols, dist, expected, ratio = best
    matched = len(rows)
    overlap = max(0.0, min(a.duration, b.duration - o) - max(0.0, -o))
    overlap_frac = overlap / max(1e-6, min(a.duration, b.duration))
    scenes = len(set(a.scene[rows].tolist()))
    need = min(3, max(2, expected // 2))
    if matched < need or scenes < 2: return None
    if expected < 8: min_ratio += .12            # few frames of evidence: demand a closer match
    if ratio < min_ratio or overlap_frac < min_overlap or overlap < min(6.0, .5 * min(a.duration, b.duration)): return None
    mean_d = float(dist.mean())
    quality = max(0.0, 1 - mean_d / .35)
    conf = 100 * (.45 * ratio + .30 * quality + .15 * min(1, scenes / 6) + .10 * min(1, overlap_frac / .9))
    return Match(A, B, round(min(99.0, conf), 1), matched, round(ratio, 3), round(o, 1),
                 f'{matched}/{expected} frames in the {overlap:.0f}s overlap match, {scenes} distinct scenes, '
                 f'mean hash distance {mean_d*HASH_BITS:.0f}/{HASH_BITS} bits')


def compare_keyframes(a: _Prep, b: _Prep, A: str, B: str, max_dist=.23) -> Match | None:
    """Partial-overlap / clip matching. Encoders put keyframes on scene cuts, so a clip and its source share
    keyframes with (nearly) identical pictures at a constant time offset. Needs many such frames AND a tight,
    consistent offset, so a few coincidentally similar shots are not enough."""
    if len(a.kt) < 5 or len(b.kt) < 5: return None
    D = .5 - (a.kbits @ b.kbits.T) / (2 * HASH_BITS)
    mask = D <= min(max_dist, .20)
    if int(mask.sum()) < 5: return None
    hits = set()
    for i in np.flatnonzero(mask.any(axis=1)): hits.add((int(i), int(D[i].argmin())))
    for j in np.flatnonzero(mask.any(axis=0)): hits.add((int(D[:, j].argmin()), int(j)))
    hits = [(i, j) for i, j in hits if mask[i, j]]
    if len(hits) < 5: return None
    off = np.array([b.kt[j] - a.kt[i] for i, j in hits]); tol = .5
    srt = np.sort(off)
    counts = np.searchsorted(srt, srt + tol, 'right') - np.searchsorted(srt, srt - tol, 'left')
    o0 = float(srt[int(np.argmax(counts))])
    o = float(np.median(off[np.abs(off - o0) <= tol]))
    res = _align(a.kt, b.kt, D, mask, o, tol)
    if res is None: return None
    rows, cols, dist = res
    matched = len(rows); consistency = matched / len(hits); scenes = len(set(a.kscene[rows].tolist()))
    overlap = max(0.0, min(a.duration, b.duration - o) - max(0.0, -o))
    mean_d = float(dist.mean())
    if matched < 5 or scenes < 4 or consistency < .5 or mean_d > .16 or overlap < 8: return None
    quality = max(0.0, 1 - mean_d / .25)
    conf = 35 + min(30, 3 * matched) + 20 * quality + 10 * consistency
    return Match(A, B, round(min(95.0, conf), 1), matched, round(min(1.0, matched / max(1, len(rows))), 3), round(o, 1),
                 f'partial overlap: {matched} scene-cut keyframes match at a constant {o:+.1f}s offset '
                 f'({scenes} distinct scenes, mean hash distance {mean_d*HASH_BITS:.0f}/{HASH_BITS} bits)')


def find_matches(videos: list[Video], threshold=.22, minimum_coverage=.55, callback: Callable | None = None,
                 min_overlap: float | None = None) -> list[Match]:
    if min_overlap is None: min_overlap = minimum_coverage * .55
    preps = [_Prep(v) for v in videos]
    pairs = candidates(preps); results = []
    for k, (i, j) in enumerate(pairs, 1):
        m = (compare(preps[i], preps[j], videos[i].path, videos[j].path, threshold, minimum_coverage, min_overlap)
             or compare_keyframes(preps[i], preps[j], videos[i].path, videos[j].path, threshold))
        if m: results.append(m)
        if callback and (k % 25 == 0 or k == len(pairs)): callback(k, len(pairs))
    return sorted(results, key=lambda m: m.confidence, reverse=True)


def groups(matches: list[Match]):
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for m in matches:
        ra, rb = find(m.left), find(m.right)
        if ra != rb: parent[rb] = ra
    out = defaultdict(list)
    for v in parent: out[find(v)].append(v)
    return sorted((sorted(v) for v in out.values() if len(v) > 1), key=lambda x: -len(x))


def scan(folders: list[str], cache_path: str, interval: float = 4.0, threshold: float = .22, minimum_coverage: float = .55,
         progress: Callable | None = None, stop: threading.Event | None = None, workers: int = 4,
         max_samples: int = MAX_FRAMES):
    """threshold = max hash distance (fraction of 256 bits); minimum_coverage = min matching share of the overlap."""
    files = list(iter_files(folders)); cache = read_cache(cache_path); vids = []; errors = []
    if progress: progress('discovered', 0, len(files))
    pending = []
    for path in files:
        if stop is not None and stop.is_set(): break
        try:
            key = fingerprint_key(path) + f'|{interval:.3f}|{max_samples}|{FP_TAG}'
            if key in cache: vids.append(Video(**cache[key]))
            else: pending.append((path, key))
        except Exception as exc: errors.append((path, str(exc)))
    completed = len(vids) + len(errors)
    if progress: progress('fingerprints', completed, len(files))
    workers = max(1, min(16, int(workers)))
    def job(item):
        path, key = item
        if stop is not None and stop.is_set(): raise InterruptedError('Scan cancelled')
        return key, analyze(path, interval, stop, None, max_samples)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        iterator = iter(pending); futures = {}
        for _ in range(min(workers * 2, len(pending))):
            item = next(iterator, None)
            if item: futures[pool.submit(job, item)] = item
        while futures:
            for future in as_completed(tuple(futures)):
                path, key = futures.pop(future)
                try:
                    k, vid = future.result(); vids.append(vid); cache[k] = asdict(vid)
                    if len(vids) % 20 == 0: save_cache(cache_path, cache)
                except InterruptedError: pass
                except Exception as exc: errors.append((path, str(exc) or exc.__class__.__name__))
                completed += 1
                if progress: progress('fingerprints', completed, len(files))
                if stop is None or not stop.is_set():
                    item = next(iterator, None)
                    if item: futures[pool.submit(job, item)] = item
                break
    save_cache(cache_path, cache)
    if stop is not None and stop.is_set(): return vids, [], errors
    if progress: progress('comparing', 0, 1)
    matches = find_matches(vids, threshold, minimum_coverage,
        callback=(lambda n, total: progress('comparison', n, total)) if progress else None)
    return vids, matches, errors
