"""Single-writer incremental source sync, with observable history backfill and retry."""
import json
import threading
import time
from collections import deque
from process_show.storage.core import dumps, confined


class SourceSync:
    def __init__(self, importer, history=False, traces=None, limit=0):
        self.importer=importer;self.root=str(importer.root);self.history=history
        self.traces=traces;self.limit=limit;self.wake=threading.Event();self.lock=threading.Lock()
        self.queue=deque();self.signatures={};self.failures={};self.next_scan=0
        self.halt=threading.Event();self.done=threading.Event();self.done.set()
        self.state={'source':importer.root.name,'history_enabled':history,'phase':'waiting','discovered':0,'excluded_history':0,'pending':0,'expired':0,'indexed':0,'last_scan':None,'last_progress':None,'errors':[],'limit':limit}
        self.excluded=None
        self.live_paths=[];self.next_live_poll=0

    def request(self, history):
        with self.lock:
            self.history=history;self.state['history_enabled']=history
            self.next_scan=0;self.state.update(phase='waiting',last_scan=None)
            # Retry failed/unchanged partial sources, but never reset durable offsets.
            self.signatures.clear();self.failures.clear()
        self.wake.set()

    def snapshot(self):
        with self.lock:return dict(self.state,errors=list(self.state['errors']))

    def scan(self):
        paths=[]
        for path in (self.traces or self.importer.discover()):
            try:paths.append(confined(self.importer.root,path))
            except ValueError as exc:self.failed(path,exc)
        if self.limit:paths=paths[:self.limit]
        with self.importer.store.db() as db:
            cursors={r['path']:(r['offset'],json.loads(r['state'])) for r in db.execute('SELECT * FROM imports')}
            sealed={r['id']:(r['status'],json.loads(r['meta']).get('source_completed',False)) for r in db.execute('SELECT id,status,meta FROM episodes')}
        with self.lock:
            if self.excluded is None:
                self.excluded={str(p) for p in paths if str(p) not in cursors and (p.parent/'result.json').is_file()}
            history=self.history
        queue=[];expired=excluded=indexed=0
        for path in paths:
            key=str(path);offset,state=cursors.get(key,(0,{}))
            if key in cursors:indexed+=1
            if state.get('expired'):expired+=1;continue
            if not history and key in self.excluded:excluded+=1;continue
            try:
                stat=path.stat();result=path.parent/'result.json';signature=(stat.st_size,stat.st_mtime_ns,stat.st_dev,stat.st_ino,result.stat().st_mtime_ns if result.is_file() else 0)
                if self.signatures.get(key)==signature:continue
                episode=sealed.get(state.get('eid'))
                late_result=bool(episode and episode[0]=='live' and not episode[1] and signature[4])
                identity_ok=state.get('file_identity') in (None,[stat.st_dev,stat.st_ino])
                if key in cursors and offset==stat.st_size and identity_ok and not late_result:
                    self.signatures[key]=signature;continue
                failure=self.failures.get(key)
                if failure and failure['retry_at']>time.monotonic():continue
                # Newly appended active sources first; rotate all pending traces fairly.
                queue.append(((path.parent/'result.json').is_file(),path))
            except OSError as exc:self.failed(path,exc)
        self.live_paths=[p for p in paths if sealed.get(cursors.get(str(p),(0,{}))[1].get('eid'),('live',False))[0]=='live']
        active=[p for historical,p in queue if not historical]
        history_paths={str(p):p for historical,p in queue if historical}
        remaining=[p for p in self.queue if str(p) in history_paths]
        queued={str(p) for p in remaining}
        self.queue=deque(active+remaining+[p for key,p in history_paths.items() if key not in queued])
        with self.lock:
            self.state.update(discovered=len(paths),indexed=indexed,expired=expired,excluded_history=excluded,pending=len(self.queue),last_scan=time.time(),phase='importing' if self.queue else 'idle')
        self.next_scan=time.monotonic()+5

    def failed(self,path,exc):
        key=str(path);attempt=self.failures.get(key,{}).get('attempt',0)+1
        self.failures[key]={'path':str(path.relative_to(self.importer.root)) if self.importer.root in path.parents else 'rejected external path','message':str(exc),'attempt':attempt,'retry_at':time.monotonic()+min(60,2**min(attempt,6))}
        with self.lock:self.state['errors']=[{k:v for k,v in f.items() if k!='retry_at'} for f in list(self.failures.values())[:20]]

    def tick(self):
        if time.monotonic()>=self.next_scan:self.scan()
        elif time.monotonic()>=self.next_live_poll:
            queued={str(p) for p in self.queue}
            for path in self.live_paths:
                key=str(path)
                if key in queued or self.failures.get(key,{}).get('retry_at',0)>time.monotonic():continue
                try:
                    stat=path.stat();result=path.parent/'result.json'
                    signature=(stat.st_size,stat.st_mtime_ns,stat.st_dev,stat.st_ino,result.stat().st_mtime_ns if result.is_file() else 0)
                    if self.signatures.get(key)!=signature:self.queue.appendleft(path)
                except OSError as exc:self.failed(path,exc)
            self.next_live_poll=time.monotonic()+.5
        deadline=time.monotonic()+.3
        while self.queue and time.monotonic()<deadline:
            path=self.queue.popleft();key=str(path)
            try:
                stat=path.stat();result=path.parent/'result.json';signature=(stat.st_size,stat.st_mtime_ns,stat.st_dev,stat.st_ino,result.stat().st_mtime_ns if result.is_file() else 0)
                with self.importer.store.db() as db:
                    row=db.execute('SELECT offset FROM imports WHERE path=?',(key,)).fetchone()
                before=row[0] if row else 0
                self.importer.poll(path,max_events=16,max_seconds=.05)
                with self.importer.store.db() as db:
                    row=db.execute('SELECT offset,state FROM imports WHERE path=?',(key,)).fetchone()
                after=row['offset'] if row else 0
                if row and json.loads(row['state']).get('expired'):pass
                elif after<signature[0] and after>before:self.queue.append(path)
                else:self.signatures[key]=signature  # incomplete final line waits for a new append
                self.failures.pop(key,None)
                with self.lock:self.state['last_progress']=time.time()
            except Exception as exc:self.failed(path,exc)
        with self.lock:
            self.state['pending']=len(self.queue)
            self.state['phase']='importing' if self.queue else 'error' if self.failures else 'idle'
            self.state['errors']=[{k:v for k,v in f.items() if k!='retry_at'} for f in list(self.failures.values())[:20]]

    def run(self,stop):
        self.done.clear()
        try:
            while not stop.is_set() and not self.halt.is_set():
                try:self.tick()
                except Exception as exc:
                    with self.lock:self.state.update(phase='error',errors=[{'path':'source discovery','message':str(exc)}])
                    self.next_scan=time.monotonic()+5
                self.wake.wait(.1 if self.queue else .5);self.wake.clear()
        finally:self.done.set()


class SyncManager:
    def __init__(self,store):self.store=store;self.sources=[];self.service_stop=None;self.selection_lock=threading.Lock()
    def add(self,importer,history=False,traces=None,limit=0):
        with self.store.db() as db:row=db.execute("SELECT value FROM settings WHERE key='sync_history'").fetchone()
        saved=json.loads(row[0]) if row else {}
        source=SourceSync(importer,saved.get(str(importer.root),history),traces,limit)
        self.sources.append(source);return source
    def evaluation_sources(self,chosen=None):
        if chosen is None:return self.sources
        from process_show.service.evaluations import source_key
        return [s for s in self.sources if source_key(s.root)==source_key(chosen['run_root'])
                and (chosen.get('adapter')!='generic' or (getattr(getattr(s,'importer',None),'evaluation',None) or {}).get('id')==chosen['id'])]

    def request(self,history,chosen=None):
        if type(history) is not bool:raise ValueError('history must be boolean')
        with self.store.db() as db:
            row=db.execute("SELECT value FROM settings WHERE key='sync_history'").fetchone()
            saved=json.loads(row[0]) if row else {}
            saved.update({s.root:history for s in self.evaluation_sources(chosen)})
            db.execute("INSERT INTO settings VALUES ('sync_history',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dumps(saved),))
        for source in self.evaluation_sources(chosen):source.request(history)
        return self.snapshot(chosen)
    def snapshot(self,chosen=None):
        with self.store.db() as db:
            rows=db.execute('SELECT meta FROM episodes WHERE deleted IS NOT NULL').fetchall()
            active=db.execute('SELECT meta,updated,status,error FROM episodes WHERE deleted IS NULL').fetchall()
        from process_show.service.evaluations import belongs
        expired=sum(not chosen or belongs(json.loads(r[0]),chosen) for r in rows)
        sources=self.evaluation_sources(chosen)
        active=[r for r in active if not chosen or belongs(json.loads(r['meta']),chosen)]
        last=max((r['updated'] for r in active),default=None)
        return {'sources':[s.snapshot() for s in sources],'expired_episodes':expired,'configured_sources':len(sources),
                'scope':'selected evaluation only' if chosen else 'configured project run roots only',
                'evaluation_id':chosen['id'] if chosen else None,
                'capture_mode':'indexed_producer' if chosen and chosen.get('catalog_only') else 'trace_watcher',
                'last_index_update':last,'record_age_seconds':max(0,time.time()-last) if last else None,
                'live_records':sum(r['status']=='live' for r in active),
                'capture_errors':[r['error'] for r in active if r['error']][:10]}

    def select_run(self,chosen):
        from process_show.capture.adapters import Importer
        from process_show.storage.core import atomic_json
        if self.service_stop is None:raise ValueError('此服务未启用运行选择，请从本目录入口启动')
        with self.selection_lock:
            if len(self.sources)==1 and self.sources[0].root==chosen['run_root']:
                self.sources[0].request(True)
            else:
                for source in self.sources:source.halt.set();source.wake.set()
                for source in self.sources:
                    if not source.done.wait(3):raise ValueError('旧测评正在结束同步，请稍后重试切换')
                self.sources=[]
                if not chosen.get('catalog_only'):
                    importer=Importer(self.store,chosen['run_root'],chosen['stage'],chosen.get('source_model',chosen['model']),lazy=True)
                    source=self.add(importer,True);source.request(True);source.done.clear()
                    threading.Thread(target=source.run,args=(self.service_stop,),daemon=True).start()
            atomic_json(self.store.root/'selected-run.json',chosen)
        return chosen

    def ensure_run(self,chosen):
        from process_show.capture.adapters import Importer
        if self.service_stop is None:raise ValueError('此服务未启用运行选择')
        with self.selection_lock:
            if chosen.get('catalog_only'):return
            for source in self.sources:
                if source.root!=chosen['run_root'] or source.halt.is_set():continue
                if chosen.get('adapter')=='generic':
                    bound=source.importer.evaluation
                    if bound and bound['id']!=chosen['id']:continue
                    if not bound:return  # An explicitly configured directory watcher already owns these cursors.
                source.request(True);return
            importer=Importer(self.store,chosen['run_root'],chosen['stage'],chosen.get('source_model',chosen['model']),adapter=chosen.get('adapter','case1'),lazy=True)
            if chosen.get('adapter')=='generic':importer.evaluation=chosen
            source=self.add(importer,True);source.request(True);source.done.clear()
            threading.Thread(target=source.run,args=(self.service_stop,),daemon=True).start()
