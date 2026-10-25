"""Nonblocking, project-scoped evaluation discovery for the picker."""
import json,sys,threading,time
from pathlib import Path
from process_show.storage.core import atomic_json

class RunCatalog:
    def __init__(self,scope,store):
        self.scope=scope;self.store=store;self.lock=threading.Lock();self.busy=False;self.finished=0
        self.value={'runs':[],'expired_runs':0,'ttl_seconds':3600,'errors':[],'scanned_at':0}
        cached=store.root/'run-catalog.json'
        if cached.is_file():
            try:
                value=json.loads(cached.read_text(encoding='utf-8'))
                for run in value.get('runs',[]):scope.source(run['run_root'])
                if time.time()-value.get('scanned_at',0)<600:self.value=value
            except (ValueError,OSError,KeyError):pass
    def get(self):
        with self.lock:
            if not self.busy and time.monotonic()-self.finished>5:
                self.busy=True;threading.Thread(target=self.refresh,daemon=True).start()
            value=dict(self.value,pending=self.busy)
        now=time.time();value['runs']=[r for r in value['runs'] if not r.get('expires_at') or r['expires_at']>now or r.get('retained')]
        return value
    def refresh(self):
        try:
            sys.path.insert(0,str(self.scope.project/'process-show/tools'))
            from runs import discover
            value=discover(self.scope.project,self.store.root)
            atomic_json(self.store.root/'run-catalog.json',value)
        except Exception as exc:value=dict(self.value,errors=[{'error':str(exc)}])
        with self.lock:self.value=value;self.busy=False;self.finished=time.monotonic()
