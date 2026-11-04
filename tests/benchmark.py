"""Regression benchmark for the matching engine (synthetic footage, needs: pip install scikit-image).
Builds realistic test videos (pans/zooms/cuts over natural photos), makes re-encoded copies, letterboxed copies,
clips cut at fractional offsets and unrelated look-alike videos, then reports found / missed / false pairs per profile.
Run:  python tests/benchmark.py        (about 2-3 minutes; ffmpeg must be in PATH)"""
import sys, os, random, itertools, subprocess, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import numpy as np, cv2
from skimage import data
import engine

NAMES = ['astronaut','camera','chelsea','coffee','rocket','coins','moon','retina','immunohistochemistry','brick','hubble_deep_field','horse','cell','grass','gravel','text']
def _img(n):
    im = getattr(data, n)()
    if im.dtype == bool: im = im.astype(np.uint8) * 255
    if im.ndim == 2: im = cv2.cvtColor(im, cv2.COLOR_GRAY2RGB)
    return im[:, :, :3]
IM = [_img(n) for n in NAMES]
W, H, FPS = 320, 240, 15

def render(seed, dur, out, fast=False):
    rng = random.Random(seed)
    p = subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),
        '-i','-','-c:v','libx264','-crf','20','-g','30','-pix_fmt','yuv420p',out], stdin=subprocess.PIPE)
    total, n = int(dur * FPS), 0
    def win(img):
        h, w = img.shape[:2]; s = rng.uniform(.25, .6) if fast else rng.uniform(.35, .8)
        ww = int(w * s); hh = int(ww * H / W)
        if hh > h: hh = h; ww = int(hh * W / H)
        return rng.randrange(0, max(1, w - ww)), rng.randrange(0, max(1, h - hh)), ww, hh
    while n < total:
        img = IM[rng.randrange(len(IM))]; a, b = win(img), win(img)
        cnt = int((rng.uniform(1.5, 3.2) if fast else rng.uniform(4, 9)) * FPS)
        for k in range(min(cnt, total - n)):
            f = k / cnt; x, y, ww, hh = [int(a[i] + (b[i] - a[i]) * f) for i in range(4)]
            p.stdin.write(cv2.resize(img[y:y+hh, x:x+ww], (W, H), interpolation=cv2.INTER_AREA).tobytes()); n += 1
    p.stdin.close(); p.wait()

def ff(*a): subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*a], check=True)

def build(d):
    for fast, tag, seed in [(False, 'S', 100), (True, 'F', 500)]:
        for i, n in enumerate('ABC'): render(seed + i, 70, f'{d}/{tag}{n}.mp4', fast)
    for tag in 'SF':
        ff('-i', f'{d}/{tag}A.mp4', '-vf', 'scale=160:120', '-c:v', 'libx264', '-crf', '34', '-g', '250', f'{d}/{tag}A_lowres_longgop.mp4')
        ff('-i', f'{d}/{tag}B.mp4', '-vf', 'pad=320:320:0:40:black,scale=240:240', '-c:v', 'libx265', '-crf', '30', f'{d}/{tag}B_letterbox_hevc.mp4')
        ff('-i', f'{d}/{tag}C.mp4', '-vf', 'eq=brightness=0.08:contrast=1.2', '-c:v', 'libx264', '-crf', '36', '-g', '90', f'{d}/{tag}C_bright_lowq.mp4')
        ff('-ss', '20.37', '-t', '28', '-i', f'{d}/{tag}A.mp4', '-vf', 'scale=256:192', '-c:v', 'libx264', '-crf', '28', f'{d}/{tag}A_clip.mp4')
    rng = random.Random(7)
    for i in range(14): render(1000 + i, rng.choice([20, 40, 60]), f'{d}/neg{i:02d}.mp4', fast=bool(i % 2))
    truth = set()
    for t in 'SF':
        truth |= {frozenset(p) for p in itertools.combinations([f'{t}A', f'{t}A_lowres_longgop', f'{t}A_clip'], 2)}
        truth.add(frozenset([f'{t}B', f'{t}B_letterbox_hevc'])); truth.add(frozenset([f'{t}C', f'{t}C_bright_lowq']))
    return truth

if __name__ == '__main__':
    d = tempfile.mkdtemp(prefix='vidupe_bench_'); print('building test videos in', d); truth = build(d)
    for name, (thr, cover, _) in engine.PROFILES.items():
        cache = os.path.join(d, 'cache_%s.json' % name)
        t = time.time(); vids, matches, errors = engine.scan([d], cache, 6.0, thr, cover, None, None, 4)
        found = {frozenset(os.path.basename(x)[:-4] for x in (m.left, m.right)): m for m in matches}
        tp = [k for k in found if k in truth]; fp = [k for k in found if k not in truth]; fn = [k for k in truth if k not in found]
        print(f'\n{name:9s} found {len(tp)}/{len(truth)}  false pairs {len(fp)}  errors {len(errors)}  ({time.time()-t:.0f}s)')
        for k in fp: print('   FALSE', sorted(k), found[k].confidence, found[k].evidence)
        for k in fn: print('   MISSED', sorted(k))
