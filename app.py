"""Vidupe Next desktop application. Python 3.11+, Pillow, numpy, opencv-python."""
from __future__ import annotations
import os, sys, threading, queue, subprocess, tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from PIL import Image, ImageTk
# Ensure bundled FFmpeg tools are visible to subprocesses in a frozen Windows build.
if getattr(sys, 'frozen', False):
    bundled_dir = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    os.environ['PATH'] = str(bundled_dir) + os.pathsep + os.environ.get('PATH', '')
from engine import scan, groups

BASE=Path(os.environ.get('APPDATA',Path.home()))/'VidupeNext'
BASE.mkdir(parents=True,exist_ok=True)
BG='#101723'; PANEL='#192333'; LINE='#334155'; TEXT='#e5edf7'; MUTED='#a3b4c9'; BLUE='#4aa8ff'; GREEN='#41d7a7'

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Vidupe Next  |  Video Duplicate Finder')
        self.geometry('1250x820');self.minsize(880,600);self.configure(bg=BG)
        self.folders=[];self.vids={};self.matches=[];self.groups=[];self.events=queue.Queue();self.stop=threading.Event();self.thumbnails=[]
        self.interval=tk.StringVar(value='6');self.sensitivity=tk.StringVar(value='Balanced');self.status=tk.StringVar(value='Add folders to scan. Original files are never modified during scanning.')
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
        self.label(settings,'More frequent sampling finds shorter overlapping clips but takes longer.',9,MUTED).pack(side='left',padx=9)
        cards=tk.Frame(self,bg=BG,padx=20);cards.pack(fill='x')
        self.count_labels=[]
        for title in ['Videos scanned','Possible matches','Duplicate groups','Potential savings']:
            f=tk.Frame(cards,bg=PANEL,padx=17,pady=10,highlightbackground=LINE,highlightthickness=1);f.pack(side='left',fill='x',expand=True,padx=3)
            self.label(f,title,10,MUTED).pack(anchor='w');val=self.label(f,'0',20,TEXT,True);val.pack(anchor='w',pady=(3,0));self.count_labels.append(val)
        body=tk.PanedWindow(self,orient=tk.HORIZONTAL,bg=BG,sashwidth=5,bd=0);body.pack(fill='both',expand=True,padx=22,pady=14)
        results=tk.Frame(body,bg=PANEL);body.add(results,minsize=400,stretch='always')
        self.label(results,'MATCHING VIDEO PAIRS',12,TEXT,True).pack(anchor='w',padx=14,pady=12)
        cols=('similarity','files','size');self.tree=ttk.Treeview(results,columns=cols,show='tree headings',selectmode='browse')
        self.tree.heading('#0',text='Video / group');self.tree.column('#0',width=300,minwidth=200)
        for k,t,w in [('similarity','Confidence',100),('files','Frames',70),('size','Overlap',75)]:
            self.tree.heading(k,text=t);self.tree.column(k,width=w,minwidth=60,anchor='center')
        sb=ttk.Scrollbar(results,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right',fill='y');self.tree.pack(fill='both',expand=True,padx=(10,0),pady=(0,12));self.tree.bind('<<TreeviewSelect>>',self.selected)
        detail=tk.Frame(body,bg=PANEL,padx=15,pady=12);body.add(detail,minsize=300,stretch='always')
        self.label(detail,'REVIEW MATCH',12,TEXT,True).pack(anchor='w',pady=(0,12))
        self.preview=[]
        for i in range(2):
            f=tk.Frame(detail,bg='#111b29',pady=8,padx=9);f.pack(fill='x',pady=6)
            image=tk.Label(f,bg='#111b29',height=125,fg=MUTED,text='Select a match')
            image.pack(fill='x');name=tk.Label(f,bg='#111b29',fg=TEXT,font=('Segoe UI',10,'bold'),wraplength=350,justify='left');name.pack(anchor='w',pady=(5,2))
            info=tk.Label(f,bg='#111b29',fg=MUTED,font=('Segoe UI',9),wraplength=350,justify='left');info.pack(anchor='w')
            self.preview.append((image,name,info))
        self.score=self.label(detail,'Select a matching pair to view details.',10,GREEN);self.score.pack(anchor='w',pady=(12,8))
        tk.Button(detail,text='Open selected file in Explorer',command=self.open_selected,bg=LINE,fg=TEXT,relief='flat',pady=9).pack(fill='x',pady=4)
        tk.Button(detail,text='Export matches as CSV',command=self.export,bg=LINE,fg=TEXT,relief='flat',pady=9).pack(fill='x',pady=4)
        self.label(detail,'Safety: Nothing is deleted automatically. Review videos before removing copies.',9,MUTED).pack(anchor='w',pady=16)
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
        mapping={'Strict':(9,.45),'Balanced':(13,.28),'Sensitive':(17,.22)}; threshold,cover=mapping[self.sensitivity.get()]
        def worker():
            try:
                v,m,errors=scan(self.folders,str(BASE/'fingerprints.json'),float(self.interval.get()),threshold,cover,
                    lambda stage,n,total:self.events.put(('progress',stage,n,total)),self.stop)
                self.events.put(('done',v,m,errors))
            except Exception as e:self.events.put(('error',str(e)))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        try:
            while True:
                msg=self.events.get_nowait()
                if msg[0]=='progress':
                    _,stage,n,total=msg;self.status.set(f'{stage.title()}: {n} / {total}')
                    self.progress['value']=100*n/max(1,total)
                elif msg[0]=='done':self.render(*msg[1:]);self.startbtn.configure(text='SCAN VIDEOS',bg=GREEN)
                elif msg[0]=='error':messagebox.showerror('Scan failed',msg[1]);self.startbtn.configure(text='SCAN VIDEOS',bg=GREEN)
        except queue.Empty:pass
        self.after(100,self.poll)

    def render(self,vids,matches,errors):
        self.vids={v.path:v for v in vids};self.matches=matches;self.groups=groups(matches)
        seen=set();saving=0
        for g in self.groups:
            if len(g)<2:continue
            sizes=sorted((self.vids[x].size for x in g if x in self.vids),reverse=True)
            if sizes:saving+=sum(sizes[1:])
        # Display actual connected groups first, each containing its matched pairs.
        path_group={p:i for i,g in enumerate(self.groups) for p in g}
        group_ids={}
        for i,g in enumerate(self.groups):
            basename=os.path.basename(g[0]) if g else ''
            group_ids[i]=self.tree.insert('','end',iid=f'g{i}',text=f'Group {i+1}: {len(g)} videos  •  {basename}',open=True,
                                        values=('','',f'{len(g)} files'))
        for i,m in enumerate(matches):
            name=os.path.basename(m.left)+'  /  '+os.path.basename(m.right)
            group_id=path_group.get(m.left)
            parent=group_ids.get(group_id,'')
            self.tree.insert(parent,'end',iid=f'm{i}',text=name,values=(f'{m.confidence:.1f}%',m.aligned_frames,f'{m.covered:.0%}'))
        stats=[str(len(vids)),str(len(matches)),str(len(self.groups)),f'{saving/1024**3:.2f} GB']
        for lbl,v in zip(self.count_labels,stats):lbl.configure(text=v)
        self.status.set(f'Done. {len(matches)} likely matches found. {len(errors)} unreadable files. Compare manually before deleting.')
        self.progress['value']=100
        if matches:self.tree.selection_set('m0');self.tree.see('m0');self.selected()
        if errors:
            with open(BASE/'scan-errors.txt','w',encoding='utf-8') as f:
                for p,e in errors:f.write(f'{p}: {e}\n')

    def selected(self,_event=None):
        sel=self.tree.selection()
        if not sel:return
        if not sel[0].startswith('m'):return
        idx=int(sel[0][1:]);m=self.matches[idx];self.active=m
        self.thumbnails.clear()
        for i,path in enumerate((m.left,m.right)):
            img,name,info=self.preview[i];v=self.vids[path]
            img.configure(image='',text='Preview unavailable')
            try:
                # Display a resized frame through the same extraction path as hashing.
                from engine import frame_at
                frame=frame_at(path,max(0,v.duration*.50))
                if frame is not None:
                    pil=Image.fromarray(frame).resize((235,135))
                    photo=ImageTk.PhotoImage(pil);self.thumbnails.append(photo);img.configure(image=photo,text='')
            except Exception:pass
            name.configure(text=os.path.basename(path));info.configure(text=f'{v.width} x {v.height}  |  {v.duration:.1f}s  |  {v.size/1024**2:.1f} MB\n{path}')
        self.score.configure(text=f'{m.confidence:.1f}% estimated confidence  •  {m.evidence}\nOffset {m.offset_seconds:+.1f}s. Review the actual videos.')

    def open_selected(self):
        if not hasattr(self,'active'):return
        p=self.active.left
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
