"""Vidupe Next, sampled video fingerprint matching engine.
Independent GPLv3-compatible implementation. ffmpeg and ffprobe must be in PATH.
"""
from __future__ import annotations
import json, os, math, subprocess, hashlib, pathlib, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from collections import defaultdict, Counter
from typing import Callable
import numpy as np
import cv2

EXTENSIONS = {'.mp4','.mkv','.avi','.mov','.wmv','.m4v','.webm','.mpeg','.mpg','.flv','.3gp','.ts','.mts'}
CACHE_VERSION = 6
MAX_SAMPLE_FRAMES = 16  # Fast initial pass; lower accuracy for short overlaps.
# Windows GUI applications must not open an FFmpeg console for every frame.
SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
# Prevent OpenCV from taking additional CPU worker threads per scan task.
cv2.setNumThreads(1)

@dataclass
class Video:
    path: str
    duration: float
    width: int
    height: int
    size: int
    frames: list[tuple[float,int]]

@dataclass
class Match:
    left: str
    right: str
    confidence: float
    aligned_frames: int
    covered: float
    offset_seconds: float
    evidence: str


def probe(path: str) -> tuple[float,int,int]:
    p = subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
        'stream=width,height:format=duration','-of','json',path],capture_output=True,text=True,timeout=12,creationflags=SUBPROCESS_FLAGS)
    if p.returncode: raise RuntimeError(p.stderr.strip()[:180] or 'ffprobe failed')
    obj=json.loads(p.stdout); stream=(obj.get('streams') or [{}])[0]
    return (float(obj.get('format',{}).get('duration') or 0),int(stream.get('width') or 0),int(stream.get('height') or 0))


def frame_at(path: str, t: float) -> np.ndarray | None:
    # -ss before input is fast seek; image decoded to small RGB image.
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-ss',str(round(t,3)),
         '-i',path,'-an','-sn','-frames:v','1','-vf','scale=128:128:force_original_aspect_ratio=decrease,pad=128:128:(ow-iw)/2:(oh-ih)/2',
         '-f','image2pipe','-vcodec','mjpeg','pipe:1']
    try:
        p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8,creationflags=SUBPROCESS_FLAGS)
        if p.returncode or not p.stdout: return None
        return cv2.imdecode(np.frombuffer(p.stdout,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
    except (OSError,subprocess.TimeoutExpired): return None


def phash(gray: np.ndarray) -> int | None:
    if gray is None or np.std(gray) < 5: return None
    dct=cv2.dct(cv2.resize(gray,(32,32),interpolation=cv2.INTER_AREA).astype(np.float32))[:8,:8]
    vals=dct.flatten(); median=float(np.median(vals[1:]))
    bits=0
    for i,x in enumerate(vals):
        if i and x > median: bits |= 1 << i
    return bits


def hamming(a: int,b: int) -> int: return (a^b).bit_count()


def sample_times(duration:float, interval:float=8.0, max_frames:int=MAX_SAMPLE_FRAMES) -> list[float]:
    if duration < .3: return []
    # Use actual time spacing so clips shifted or trimmed by seconds still align.
    count=min(max_frames,max(5, math.ceil(duration/interval)))
    if duration <= 2: count=3
    margin=min(.5,duration*.08)
    return [round(float(x),3) for x in np.linspace(margin,max(margin,duration-margin),count)]


def _batch_frames(path: str, start: float, count: int, step: float = 3.0):
    """One FFmpeg process per scene region, not one process per frame.
    Decode keyframes only, reducing CPU cost on long videos. Keep short region
    sampling to avoid scanning entire movies during the first pass.
    """
    side=64
    vf=f"fps=1/{step},scale={side}:{side}:force_original_aspect_ratio=decrease,pad={side}:{side}:(ow-iw)/2:(oh-ih)/2,format=gray"
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-ss',str(round(start,2)),
         '-skip_frame','nokey','-i',path,'-an','-sn','-vf',vf,
         '-frames:v',str(count),'-f','rawvideo','-pix_fmt','gray','pipe:1']
    try:
        p=subprocess.run(cmd, stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                         timeout=16,creationflags=SUBPROCESS_FLAGS)
        n=min(count,len(p.stdout)//(side*side))
        return [np.frombuffer(p.stdout[i*side*side:(i+1)*side*side],dtype=np.uint8).reshape(side,side)
                for i in range(n)]
    except (OSError,subprocess.TimeoutExpired):return []


def analyze(path:str, interval:float=8.0, max_frames:int=MAX_SAMPLE_FRAMES, stop:threading.Event|None=None, frame_progress:Callable|None=None)->Video:
    duration,w,h=probe(path)
    if duration<=0 or not w or not h: raise ValueError('No valid video track/duration')
    frames=[]
    # 3 inexpensive scene regions across the video; 3 FFmpeg launches rather
    # than 16-75. No dependence on identical total durations.
    per_region=4
    step=max(1.5,min(4.0,interval/2))
    starts=[min(max(0,duration-12),duration*x) for x in (.05,.43,.80)]
    for region,start in enumerate(starts):
        if stop is not None and stop.is_set(): raise InterruptedError('Scan cancelled')
        batch=_batch_frames(path,start,per_region,step)
        for k,gray in enumerate(batch):
            hsh=phash(gray)
            if hsh is not None:frames.append((round(start+k*step,2),hsh))
        if frame_progress:frame_progress(region+1,len(starts))
    if not frames:raise ValueError('No readable frames (check ffmpeg codecs)')
    return Video(path,duration,w,h,os.path.getsize(path),frames)


def iter_files(folders:list[str]):
    seen=set()
    for folder in folders:
        for root,_,files in os.walk(folder):
            for name in files:
                p=os.path.abspath(os.path.join(root,name))
                if pathlib.Path(p).suffix.lower() in EXTENSIONS and p not in seen:
                    seen.add(p);yield p


def fingerprint_key(path:str)->str:
    s=os.stat(path)
    return hashlib.sha256((os.path.normcase(path)+'|'+str(s.st_size)+'|'+str(s.st_mtime_ns)).encode()).hexdigest()


def read_cache(path:str)->dict:
    try:
        with open(path,'r',encoding='utf-8') as f: data=json.load(f)
        if data.get('version')==CACHE_VERSION: return data.get('videos',{})
    except (OSError,ValueError,TypeError): pass
    return {}


def save_cache(path:str,cache:dict):
    temp=path+'.tmp'
    with open(temp,'w',encoding='utf-8') as f:json.dump({'version':CACHE_VERSION,'videos':cache},f)
    os.replace(temp,path)


def candidates(videos:list[Video]):
    """Multi-index hashing. Any frame sharing an exact 16-bit block is a candidate.
    64 bits split into four blocks: any pair <=3 bit differences must share a block.
    Higher differences are not guaranteed, but many independent samples increase recall.
    """
    buckets=defaultdict(set)
    for i,vid in enumerate(videos):
        for _,h in vid.frames:
            for seg in range(4): buckets[(seg,(h>>(seg*16))&65535)].add(i)
    graph=defaultdict(set)
    for group in buckets.values():
        if len(group)>400: continue # ignore uninformative common frames
        for i in group:
            graph[i].update(j for j in group if i<j)
    return [(i,j) for i,js in graph.items() for j in js]


def compare(a:Video,b:Video, threshold:int=13, minimum_coverage:float=.28)->Match|None:
    """Find shifted/trimmed timeline overlaps, reject isolated matching stills.
    Offset bins accommodate sparse frame sampling; secondary verification near predicted t.
    """
    if not a.frames or not b.frames: return None
    near=[]
    for ta,ha in a.frames:
        for tb,hb in b.frames:
            d=hamming(ha,hb)
            if d<=threshold: near.append((ta,tb,d))
    if not near: return None
    # Agreement on a consistent time shift, with 8s sampling allowing +/-8s jitter.
    # Quantization is used only to identify candidate shifts, not to prove a match.
    offsets=defaultdict(list)
    for ta,tb,d in near:
        offset=tb-ta
        for binid in range(round(offset/8)-1,round(offset/8)+2):
            if abs(offset-binid*8)<=9:offsets[binid].append((ta,tb,d))
    if not offsets: return None
    ranked=sorted(offsets.values(),key=lambda x:(len(set(t for t,_,_ in x)),len(set(t for _,t,_ in x))),reverse=True)
    best=None
    for hits in ranked[:6]:
        # collect distinct matches greedily with time consistency
        offsets_values=sorted(tb-ta for ta,tb,_ in hits)
        delta=offsets_values[len(offsets_values)//2]
        pairs=[]; used_a=set();used_b=set()
        for ta,tb,d in sorted(hits,key=lambda z:(abs((z[1]-z[0])-delta),z[2])):
            if ta in used_a or tb in used_b or abs((tb-ta)-delta)>9:continue
            used_a.add(ta);used_b.add(tb);pairs.append((ta,tb,d))
        if len(pairs)<2:continue
        coverage=min(len(pairs)/max(1,len(a.frames)),len(pairs)/max(1,len(b.frames)))
        # For clips with mismatched length, use shorter-video coverage as overlap evidence.
        shorter=min(len(a.frames),len(b.frames))
        short_cover=len(pairs)/shorter
        if short_cover<minimum_coverage:continue
        # Distinct scenes spread over time rather than a few adjacent similar frames.
        span_a=max(p[0] for p in pairs)-min(p[0] for p in pairs)
        span_b=max(p[1] for p in pairs)-min(p[1] for p in pairs)
        if len(pairs)<3 and min(a.duration,b.duration)>40:continue
        if min(span_a,span_b)<min(5,min(a.duration,b.duration)*.10):continue
        avg=sum(d for _,_,d in pairs)/len(pairs)
        confidence=min(99.0,100*(.47*short_cover+.33*(1-avg/64)+.20*min(1,len(pairs)/9)))
        quality=(len(pairs),short_cover,-avg)
        candidate=(quality,Match(a.path,b.path,round(confidence,1),len(pairs),round(short_cover,3),round(delta,1),f'{len(pairs)} timeline-aligned frames, mean pHash distance {avg:.1f}/64'))
        if best is None or candidate[0]>best[0]:best=candidate
    return best[1] if best else None


def find_matches(videos:list[Video],threshold:int=13,minimum_coverage:float=.28,callback:Callable|None=None)->list[Match]:
    pairs=candidates(videos);results=[]
    for k,(i,j) in enumerate(pairs,1):
        result=compare(videos[i],videos[j],threshold,minimum_coverage)
        if result:results.append(result)
        if callback and (k%100==0 or k==len(pairs)):callback(k,len(pairs))
    return sorted(results,key=lambda m:m.confidence,reverse=True)


def groups(matches:list[Match]):
    parent={}
    def find(x):
        parent.setdefault(x,x)
        while parent[x]!=x:
            parent[x]=parent[parent[x]];x=parent[x]
        return x
    for m in matches:
        ra,rb=find(m.left),find(m.right)
        if ra!=rb:parent[rb]=ra
    out=defaultdict(list)
    for v in parent:out[find(v)].append(v)
    return sorted((sorted(v) for v in out.values() if len(v)>1),key=lambda x:-len(x))


def scan(folders:list[str],cache_path:str,interval:float=8.0,threshold:int=13,minimum_coverage:float=.28,
         progress:Callable|None=None,stop:threading.Event|None=None):
    files=list(iter_files(folders));cache=read_cache(cache_path);vids=[];errors=[]
    if progress:progress('discovered',0,len(files))
    for i,path in enumerate(files,1):
        if stop is not None and stop.is_set():break
        if progress:progress('video %d/%d: %s' % (i,len(files),os.path.basename(path)),0,3)
        try:
            key=fingerprint_key(path)+f'|{interval:.3f}|batch-v1'
            saved=cache.get(key)
            if saved:
                vid=Video(**saved)
            else:
                def frame_progress(n,total):
                    if progress:progress('video %d/%d: %s | frames' % (i,len(files),os.path.basename(path)),n,total)
                vid=analyze(path,interval,MAX_SAMPLE_FRAMES,stop,frame_progress)
                cache[key]=asdict(vid)
                save_cache(cache_path,cache)
            vids.append(vid)
        except InterruptedError:
            break
        except Exception as e:
            errors.append((path,str(e)))
        if progress:progress('fingerprints',i,len(files))
    if stop is not None and stop.is_set():return vids,[],errors
    if progress:progress('comparing',0,1)
    # Allow partial candidates when sampling windows do not align after trimming.
    matches=find_matches(vids,threshold,minimum_coverage,
       callback=(lambda n,total:progress('comparison',n,total)) if progress else None)
    return vids,matches,errors
