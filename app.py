"""Vidupe Next desktop application. Python 3.11+, Pillow, numpy, opencv-python."""
from __future__ import annotations
import os, sys, threading, queue, subprocess, tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from PIL import Image, ImageTk
# Ensure bundled FFmpeg tools are visible to subprocesses in a frozen Windows build.
if getattr(sys, 'frozen', False):
    bundled_dir = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    os.environ['PATH'] = str(bundled_dir) + os.pathsep + os.environ.get('PATH', '')
from engine import scan, groups, PROFILES

BASE=Path(os.environ.get('APPDATA',Path.home()))/'VidupeNext'
BASE.mkdir(parents=True,exist_ok=True)
BG='#101723'; PANEL='#192333'; LINE='#334155'; TEXT='#e5edf7'; MUTED='#a3b4c9'; BLUE='#4aa8ff'; GREEN='#41d7a7'

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Vidupe Next  |  Video Duplicate Finder')
        self.geometry('1600x980');self.minsize(1100,740);self.configure(bg=BG)
        self.folders=[];self.vids={};self.matches=[];self.groups=[];self.events=queue.Queue();self.stop=threading.Event();self.thumbnails=[]
        self.preview_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='preview')
        self.preview_cache={};self.preview_seq=0;self.active_path=None;self.preview_paths=[None,None];self._preview_resize_job=None
        self.workers=tk.IntVar(value=max(2,min(6,(os.cpu_count() or 4)//2)));self.min_score=tk.DoubleVar(value=65);self.sort_by=tk.StringVar(value='Group size');self.interval=tk.StringVar(value='6');self.depth=tk.StringVar(value='Standard (48)');self.sensitivity=tk.StringVar(value='Balanced');self.status=tk.StringVar(value='Add folders to scan. Original files are never modified during scanning.')
        self.build();self.after(100,self.poll)

    def label(self,parent,text,size=10,color=TEXT,bold=False):
        return tk.Label(parent,text=text,bg=parent.cget('bg'),fg=color,font=('Segoe UI',size,'bold' if bold else 'normal'))

    def build(self):
        style=ttk.Style(self);style.theme_use('clam')
        style.configure('Treeview',background=PANEL,fieldbackground=PANEL,foreground=TEXT,rowheight=32,borderwidth=0,font=('Segoe UI',10))
        style.configure('Treeview.Heading',background='#253449',foreground=TEXT,font=('Segoe UI',10,'bold'),padding=9)
        style.map('Treeview',background=[('selected','#264968')]);style.configure('TProgressbar',background=BLUE,troughcolor=LINE)
        header=tk.Frame(self,bg=BG,padx=24,pady=18);header.pack(fill='x')
        self.label(header,'VIDUPE  NEXT',21,BLUE,True).pack(side='left')
        self.label(header,'  Find matching videos, even when files are different',11,MUTED).pack(side='left',padx=16)
        toolbar=tk.Frame(self,bg=PANEL,padx=16,pady=14);toolbar.pack(fill='x',padx=20)
        tk.Button(toolbar,text='+ Add folder',command=self.add_folder,bg=BLUE,fg='#071421',font=('Segoe UI',10,'bold'),relief='flat',padx=15,pady=9).pack(side='left')
        tk.Button(toolbar,text='Clear folders',command=self.clear_folders,bg=LINE,fg=TEXT,relief='flat',padx=12,pady=9).pack(side='left',padx=8)
        self.folderlabel=self.label(toolbar,'No folders selected',10,MUTED);self.folderlabel.pack(side='left',padx=12)
        self.startbtn=tk.Button(toolbar,text='SCAN VIDEOS',command=self.start_scan,bg=GREEN,fg='#062016',font=('Segoe UI',10,'bold'),relief='flat',padx=18,pady=9)
        self.startbtn.pack(side='right')
        settings=tk.Frame(self,bg=BG,padx=22,pady=13);settings.pack(fill='x')
        self.label(settings,'Matching accuracy',10,MUTED).pack(side='left')
        ttk.Combobox(settings,textvariable=self.sensitivity,values=['Strict','Balanced','Sensitive'],state='readonly',width=15).pack(side='left',padx=(10,24))
        self.label(settings,'Sample every (seconds)',10,MUTED).pack(side='left')
        ttk.Combobox(settings,textvariable=self.interval,values=['3','6','10'],state='readonly',width=7).pack(side='left',padx=10)
        self.label(settings,'Scan depth',10,MUTED).pack(side='left',padx=(15,4))
        ttk.Combobox(settings,textvariable=self.depth,values=['Quick (24)','Standard (48)','Deep (120)'],state='readonly',width=13).pack(side='left',padx=4)
        self.label(settings,'Parallel workers',10,MUTED).pack(side='left',padx=(15,4))
        tk.Spinbox(settings,from_=1,to=16,width=3,textvariable=self.workers).pack(side='left')
        self.label(settings,'Minimum score (%)',10,MUTED).pack(side='left',padx=(15,4))
        tk.Spinbox(settings,from_=0,to=99,increment=5,width=4,textvariable=self.min_score).pack(side='left')
        tk.Button(settings,text='Apply result filter',command=self.filter_results,bg=LINE,fg=TEXT,relief='flat').pack(side='left',padx=8)
        cards=tk.Frame(self,bg=BG,padx=20);cards.pack(fill='x')
        self.count_labels=[]
        for title in ['Videos scanned','Possible matches','Duplicate groups','Potential savings']:
            f=tk.Frame(cards,bg=PANEL,padx=17,pady=10,highlightbackground=LINE,highlightthickness=1);f.pack(side='left',fill='x',expand=True,padx=3)
            self.label(f,title,10,MUTED).pack(anchor='w');val=self.label(f,'0',20,TEXT,True);val.pack(anchor='w',pady=(3,0));self.count_labels.append(val)
        body=tk.PanedWindow(self,orient=tk.HORIZONTAL,bg=BG,sashwidth=5,bd=0);body.pack(fill='both',expand=True,padx=22,pady=14)
        results=tk.Frame(body,bg=PANEL);body.add(results,minsize=420,stretch='always')
        self.label(results,'DUPLICATE GROUPS  •  Select files to remove',12,TEXT,True).pack(anchor='w',padx=14,pady=8)
        actions=tk.Frame(results,bg=PANEL);actions.pack(fill='x',padx=8,pady=5)
        ttk.Combobox(actions,textvariable=self.sort_by,values=['Group size','Potential savings','Best similarity','Group name'],state='readonly',width=17).pack(side='left',padx=4)
        tk.Button(actions,text='Sort',command=self.filter_results,bg=LINE,fg=TEXT).pack(side='left',padx=4)
        tk.Button(actions,text='Select all copies',command=self.select_copies,bg='#34587c',fg=TEXT).pack(side='left',padx=4)
        tk.Button(actions,text='Clear selection',command=lambda:self.tree.selection_remove(*self.tree.selection()),bg=LINE,fg=TEXT).pack(side='left',padx=4)
        tk.Button(actions,text='Recycle selected',command=self.recycle_selected,bg='#a74040',fg='white').pack(side='right',padx=4)
        cols=('similarity','files','size');self.tree=ttk.Treeview(results,columns=cols,show='tree headings',selectmode='extended')
        self.tree.heading('#0',text='Video / group');self.tree.column('#0',width=300,minwidth=200)
        for k,t,w in [('similarity','Match',85),('files','Resolution',95),('size','Size',80)]:
            self.tree.heading(k,text=t);self.tree.column(k,width=w,minwidth=60,anchor='center')
        sb=ttk.Scrollbar(results,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right',fill='y');self.tree.pack(fill='both',expand=True,padx=(10,0),pady=(0,12));self.tree.bind('<<TreeviewSelect>>',self.selected)
        detail=tk.Frame(body,bg=PANEL,padx=12,pady=10);body.add(detail,minsize=520,stretch='always')
        self.label(detail,'REVIEW MATCH  •  Double-click a preview to enlarge',12,TEXT,True).pack(anchor='w',pady=(0,6))
        # Bottom widgets are packed FIRST (side='bottom') so the preview area gets all the remaining height.
        self.label(detail,'Safety: selected files go to the Recycle Bin, never permanently deleted. Review matches first.',9,MUTED).pack(side='bottom',anchor='w',pady=(8,0))
        buttons=tk.Frame(detail,bg=detail.cget('bg'));buttons.pack(side='bottom',fill='x',pady=4)
        tk.Button(buttons,text='Open selected file in Explorer',command=self.open_selected,bg=LINE,fg=TEXT,relief='flat',pady=9).pack(side='left',fill='x',expand=True,padx=(0,4))
        tk.Button(buttons,text='Export matches as CSV',command=self.export,bg=LINE,fg=TEXT,relief='flat',pady=9).pack(side='left',fill='x',expand=True,padx=(4,0))
        self.score=tk.Label(detail,text='Select a matching pair to view details.',bg=detail.cget('bg'),fg=GREEN,font=('Segoe UI',10),wraplength=600,justify='left')
        self.score.pack(side='bottom',anchor='w',pady=(8,6))
        # The two videos are shown SIDE BY SIDE: easier to compare, and each gets the full height of the pane.
        row=tk.Frame(detail,bg=detail.cget('bg'));row.pack(fill='both',expand=True)
        row.rowconfigure(0,weight=1);row.columnconfigure(0,weight=1,uniform='previews');row.columnconfigure(1,weight=1,uniform='previews')
        self.preview=[];self.preview_boxes=[]
        for i in range(2):
            f=tk.Frame(row,bg='#111b29',pady=8,padx=8);f.grid(row=0,column=i,sticky='nsew',padx=(0 if i==0 else 5,5 if i==0 else 0))
            info=tk.Label(f,bg='#111b29',fg=MUTED,font=('Segoe UI',9),wraplength=270,justify='left',anchor='w');info.pack(side='bottom',anchor='w',fill='x')
            name=tk.Label(f,bg='#111b29',fg=TEXT,font=('Segoe UI',10,'bold'),wraplength=270,justify='left',anchor='w');name.pack(side='bottom',anchor='w',fill='x',pady=(5,2))
            # A Label showing an image treats height= as PIXELS (it was 13px, which hid the preview). The picture now
            # sits inside a stage Frame that expands, and is scaled to the stage's real size.
            box=tk.Frame(f,bg='#0b121d',height=120,width=200);box.pack(fill='both',expand=True);box.pack_propagate(False);box.grid_propagate(False)
            image=tk.Label(box,bg='#0b121d',fg=MUTED,text='Select a match',font=('Segoe UI',10),cursor='hand2',wraplength=240)
            image.place(relx=.5,rely=.5,anchor='center')
            for w in (image,box):w.bind('<Double-Button-1>',lambda event,slot=i:self.open_large_preview(slot))
            box.bind('<Configure>',self._schedule_preview_resize)
            self.preview.append((image,name,info));self.preview_boxes.append(box)
        detail.bind('<Configure>',self._schedule_preview_resize)
        footer=tk.Frame(self,bg=BG,padx=25,pady=8);footer.pack(fill='x')
        self.progress=ttk.Progressbar(footer,mode='determinate');self.progress.pack(fill='x')
        tk.Label(footer,textvariable=self.status,bg=BG,fg=MUTED,font=('Segoe UI',9),anchor='w').pack(fill='x',pady=6)

    def add_folder(self):
        p=filedialog.askdirectory()
        if p and p not in self.folders:self.folders.append(p)
        self.folderlabel.configure(text=' | '.join(self.folders)[:105] or 'No folders selected')

    def clear_folders(self):self.folders=[];self.folderlabel.configure(text='No folders selected')

    def start_scan(self):
        if not self.folders:messagebox.showinfo('No folders','Please add at least one folder.');return
        if self.startbtn.cget('text')=='STOP SCAN':self.stop.set();self.status.set('Stopping after current operations...');return
        self.stop.clear();self.startbtn.configure(text='STOP SCAN',bg='#f5aa5b')
        self.tree.delete(*self.tree.get_children());self.vids={};self.matches=[];self.groups=[]
        for lbl in self.count_labels:lbl.configure(text='0')
        self.status.set('Discovering videos...')
        threshold,cover,_overlap=PROFILES[self.sensitivity.get()]; max_samples=int(self.depth.get().split('(')[1].rstrip(')'))
        workers=int(self.workers.get());interval=float(self.interval.get())
        def worker():
            try:
                v,m,errors=scan(self.folders,str(BASE/'fingerprints.json'),interval,threshold,cover,
                    lambda stage,n,total:self.events.put(('progress',stage,n,total)),self.stop,workers,max_samples)
                self.events.put(('done',v,m,errors))
            except Exception as e:self.events.put(('error',str(e)))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        try:
            while True:
                msg=self.events.get_nowait()
                if msg[0]=='progress':
                    _,stage,n,total=msg;self.status.set(f'{stage}: {n} / {total}')
                    if stage == 'fingerprints': self.count_labels[0].configure(text=str(n))
                    self.progress['value']=100*n/max(1,total)
                elif msg[0]=='done':self.render(*msg[1:]);self.startbtn.configure(text='SCAN VIDEOS',bg=GREEN)
                elif msg[0]=='error':messagebox.showerror('Scan failed',msg[1]);self.startbtn.configure(text='SCAN VIDEOS',bg=GREEN)
                elif msg[0]=='preview':
                    _,path,slot,token,frame=msg
                    self._apply_preview(path,slot,token,frame)
        except queue.Empty:pass
        self.after(100,self.poll)

    def render(self,vids,matches,errors):
        self.vids={v.path:v for v in vids};self.matches=matches;self.groups=groups(matches);self.filter_results()
        seen=set();saving=0
        for g in self.groups:
            if len(g)<2:continue
            sizes=sorted((self.vids[x].size for x in g if x in self.vids),reverse=True)
            if sizes:saving+=sum(sizes[1:])
        # Result rendering is handled by filter_results, including individual selectable files.
        stats=[str(len(vids)),str(len(matches)),str(len(self.groups)),f'{saving/1024**3:.2f} GB']
        for lbl,v in zip(self.count_labels,stats):lbl.configure(text=v)
        self.status.set(f'Done. {len(matches)} likely matches found. {len(errors)} unreadable files. Compare manually before deleting.')
        self.progress['value']=100
        if self.tree.get_children():self.tree.see(self.tree.get_children()[0])
        if errors:
            with open(BASE/'scan-errors.txt','w',encoding='utf-8') as f:
                for p,e in errors:f.write(f'{p}: {e}\n')

    def filter_results(self):
        if not hasattr(self,'tree'):return
        self.tree.delete(*self.tree.get_children())
        self.preview_seq+=1
        self.item_path={}; self.group_members={}
        try: floor=float(self.min_score.get())
        except (ValueError,tk.TclError): floor=0
        visible=[m for m in self.matches if m.confidence>=floor]
        indexed=groups(visible)
        def size(p):return self.vids[p].size if p in self.vids else 0
        sort=self.sort_by.get()
        if sort=='Potential savings':indexed.sort(key=lambda g:sum(map(size,g))-max(map(size,g)),reverse=True)
        elif sort=='Group name':indexed.sort(key=lambda g:os.path.basename(g[0]).lower())
        elif sort=='Best similarity':indexed.sort(key=lambda g:max((m.confidence for m in visible if m.left in g and m.right in g),default=0),reverse=True)
        else:indexed.sort(key=len,reverse=True)
        for i,g in enumerate(indexed):
            saving=sum(map(size,g))-max(map(size,g))
            gid=f'g{i}';self.group_members[gid]=list(g)
            self.tree.insert('','end',iid=gid,text=f'Group {i+1}  |  {len(g)} videos',values=('','',f'{saving/1024**2:.0f} MB'),open=i<40)
            # Higher-quality file listed first. The first one is the suggested keeper.
            ordered=sorted(g,key=lambda path:(self.vids[path].width*self.vids[path].height,size(path)),reverse=True)
            for j,path in enumerate(ordered):
                vid=self.vids[path];iid=f'v{i}_{j}';self.item_path[iid]=path
                confid=max((m.confidence for m in visible if m.left==path or m.right==path),default=0)
                self.tree.insert(gid,'end',iid=iid,text=('KEEP  ' if j==0 else 'COPY  ')+os.path.basename(path),
                    values=(f'{confid:.0f}%',f'{vid.width}×{vid.height}',f'{size(path)/1024**2:.1f} MB'))
        self.status.set(f'Showing {len(indexed)} groups above {floor:.0f}% match score. Select copies to recycle, keeping originals.')

    def select_copies(self):
        chosen=[]
        for gid,g in self.group_members.items():
            children=self.tree.get_children(gid)
            # First row is recommended keeper (resolution and file size based).
            chosen.extend(children[1:])
        self.tree.selection_set(chosen)
        self.status.set(f'{len(chosen)} candidate copies selected. Review carefully before recycling.')

    def recycle_selected(self):
        paths={self.item_path[i] for i in self.tree.selection() if i in self.item_path}
        if not paths:
            messagebox.showinfo('No files selected','Select individual COPY rows or click Select all copies.');return
        # Do not permit removing all files from a connected duplicate group.
        for group in self.groups:
            existing={p for p in group if os.path.isfile(p)}
            if existing and existing <= paths:
                messagebox.showerror('Keep one copy','You selected every file in a group. Deselect at least one file per group.');return
        total=sum(os.path.getsize(p) for p in paths if os.path.exists(p))
        if not messagebox.askyesno('Move to Recycle Bin',f'Move {len(paths)} selected videos ({total/1024**3:.2f} GB) to the Recycle Bin?\n\nMatching is approximate. Review your selections first.\n\nThis will NOT permanently delete files.',icon='warning'):return
        try:from send2trash import send2trash
        except ImportError:
            messagebox.showerror('Missing dependency','Install send2trash and rebuild the application.');return
        errors=[];moved=[]
        for path in paths:
            try:send2trash(path);moved.append(path)
            except Exception as exc:errors.append(f'{path}: {exc}')
        deleted=set(moved)
        self.matches=[m for m in self.matches if m.left not in deleted and m.right not in deleted]
        self.groups=groups(self.matches)
        self.filter_results()
        messagebox.showinfo('Recycle completed',f'{len(moved)} files moved to Recycle Bin. '+(f'{len(errors)} failed: '+errors[0][:150] if errors else ''))

    @staticmethod
    def _preview_frame(path: str):
        """Return JPEG bytes or None. Never block Tk's main/UI thread."""
        import io
        try:
            # Probe duration to seek away from intro/black opening frames.
            from engine import probe, SUBPROCESS_FLAGS
            duration, _, _ = probe(path)
            timestamp = max(0.0, min(duration * 0.22, max(0.0, duration - 0.4)))
            cmd = ['ffmpeg','-hide_banner','-loglevel','error','-nostdin',
                   '-ss',f'{timestamp:.2f}','-i',path,'-frames:v','1',
                   '-vf','scale=1280:720:force_original_aspect_ratio=decrease',
                   '-f','image2pipe','-vcodec','mjpeg','pipe:1']
            proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                timeout=15,creationflags=SUBPROCESS_FLAGS)
            if proc.returncode != 0 or not proc.stdout:return None
            im=Image.open(io.BytesIO(proc.stdout)).convert('RGB')
            return im
        except Exception:
            return None

    def _load_preview(self, path, slot, token):
        if path in self.preview_cache:
            self._apply_preview(path,slot,token,self.preview_cache[path]);return
        def job():
            result=self._preview_frame(path)
            self.events.put(('preview',path,slot,token,result))
        self.preview_pool.submit(job)

    def _apply_preview(self,path,slot,token,frame):
        if token != self.preview_seq:return
        if frame is not None:self.preview_cache[path]=frame
        label=self.preview[slot][0]
        if frame is None:
            label.configure(image='',text='Preview unavailable. Open video in Explorer.')
            label.image=None
        else:
            self._render_preview(slot,frame)

    def _render_preview(self, slot, frame):
        label=self.preview[slot][0];box=self.preview_boxes[slot]
        width=max(120,box.winfo_width()-8)
        height=max(100,box.winfo_height()-8)
        # Never upscale pixels beyond the decoded frame size.
        scaled=frame.copy()
        scaled.thumbnail((width,height),Image.Resampling.LANCZOS)
        photo=ImageTk.PhotoImage(scaled,master=self)
        label.configure(image=photo,text='')
        label.image=photo

    def _schedule_preview_resize(self, _event=None):
        if self._preview_resize_job is not None:
            self.after_cancel(self._preview_resize_job)
        self._preview_resize_job=self.after(160,self._refresh_preview_sizes)

    def _refresh_preview_sizes(self):
        self._preview_resize_job=None
        for slot,path in enumerate(self.preview_paths):
            if path and path in self.preview_cache and self.preview_cache[path] is not None:
                self._render_preview(slot,self.preview_cache[path])

    def open_large_preview(self,slot):
        path=self.preview_paths[slot]
        if not path or path not in self.preview_cache or self.preview_cache[path] is None:
            return
        frame=self.preview_cache[path]
        popup=tk.Toplevel(self)
        popup.title('Video preview  |  '+os.path.basename(path))
        popup.geometry('1200x800')
        popup.minsize(500,350)
        popup.configure(bg=BG)
        tk.Label(popup,text=path,bg=BG,fg=TEXT,font=('Segoe UI',11),wraplength=1140).pack(fill='x',padx=14,pady=10)
        canvas=tk.Label(popup,bg=BG)
        canvas.pack(fill='both',expand=True,padx=12,pady=12)
        pending=[None]
        def render():
            pending[0]=None
            size=(max(100,canvas.winfo_width()-12),max(100,canvas.winfo_height()-12))
            scaled=frame.copy()
            scaled.thumbnail(size,Image.Resampling.LANCZOS)
            photo=ImageTk.PhotoImage(scaled,master=popup)
            canvas.configure(image=photo)
            canvas.image=photo
        def resize(_event=None):
            if pending[0] is not None:popup.after_cancel(pending[0])
            pending[0]=popup.after(100,render)
        canvas.bind('<Configure>',resize)
        popup.bind('<Escape>',lambda _event:popup.destroy())
        popup.after(120,render)

    def _display_pair(self, paths, match=None):
        self.preview_seq+=1
        token=self.preview_seq
        for slot in range(2):
            image,name,info=self.preview[slot]
            path=paths[slot] if slot < len(paths) else None
            self.preview_paths[slot]=path
            if not path or path not in self.vids:
                image.configure(image='',text='Select a file to compare')
                image.image=None;name.configure(text='');info.configure(text='')
                continue
            v=self.vids[path]
            image.configure(image='',text='Loading thumbnail...')
            image.image=None
            name.configure(text=os.path.basename(path))
            info.configure(text=f'{v.width} × {v.height} | {v.duration:.1f}s | {v.size/1024**2:.1f} MB\n{path}')
            self._load_preview(path,slot,token)
        if match:
            self.score.configure(text=f'Match: {match.confidence:.1f}%  |  {match.aligned_frames} aligned frames  |  overlap {match.covered:.0%}\n{match.evidence}')
        else:
            self.score.configure(text='Select a video or group to review possible duplicates.')

    def selected(self,_event=None):
        selection=list(self.tree.selection())
        if not selection:return
        file_rows=[i for i in selection if i in self.item_path]
        if file_rows:
            path=self.item_path[file_rows[-1]]
            self.active_path=path
            selected_paths=[self.item_path[i] for i in file_rows]
            match=next((m for m in sorted(self.matches,key=lambda m:m.confidence,reverse=True)
                        if (m.left == path and m.right in self.vids) or
                           (m.right == path and m.left in self.vids)),None)
            if len(selected_paths)>=2:
                other=next((p for p in reversed(selected_paths[:-1]) if p!=path),None)
                pair=[path,other] if other else [path]
            elif match:
                pair=[path,match.right if match.left==path else match.left]
            else:pair=[path]
            self._display_pair(pair,match)
        else:
            group_id=next((i for i in selection if i in self.group_members),None)
            if group_id:
                group=self.group_members[group_id]
                pair=group[:2]
                self.active_path=pair[0] if pair else None
                match=next((m for m in self.matches if m.left in pair and m.right in pair),None)
                self._display_pair(pair,match)

    def open_selected(self):
        if not hasattr(self,'active_path'):return
        p=self.active_path
        try:
            if sys.platform=='win32':subprocess.Popen(['explorer','/select,',os.path.normpath(p)])
            elif sys.platform=='darwin':subprocess.Popen(['open','-R',p])
            else:subprocess.Popen(['xdg-open',os.path.dirname(p)])
        except Exception as e:messagebox.showerror('Explorer',str(e))

    def export(self):
        if not self.matches:return
        p=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV file','*.csv')])
        if not p:return
        import csv
        with open(p,'w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow(['video_a','video_b','confidence_estimate','aligned_frames','shorter_video_coverage','offset_seconds','evidence'])
            for m in self.matches:w.writerow([m.left,m.right,m.confidence,m.aligned_frames,m.covered,m.offset_seconds,m.evidence])
        messagebox.showinfo('Export','Saved matches as CSV. No videos were changed.')

if __name__=='__main__':App().mainloop()
