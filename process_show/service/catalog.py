"""Nonblocking, project-scoped evaluation discovery for the picker."""
import json,sys,threading,time
from pathlib import Path
from process_show.storage.core import atomic_json


def merge_runs(existing,incoming):
    merged={r['id']:r for r in existing}
    for row in incoming:
        if row.get('task_group'):
            prior=merged.get(row['id'],{})
            members={m['id']:m for m in prior.get('members',[])}
            members.update({m['id']:m for m in row['members']})
            row=dict(row,members=list(members.values()),available_completed_records=sum(m.get('completed',0) for m in members.values()))
            root=Path(row['task_root'])
            merged={identity:r for identity,r in merged.items() if identity==row['id'] or not (Path(r['run_root'])==root or root in Path(r['run_root']).parents)}
        merged[row['id']]=row
    task_roots=[(r['id'],Path(r['task_root'])) for r in merged.values() if r.get('task_group')]
    merged={identity:r for identity,r in merged.items() if r.get('task_group') or not any(identity!=task_id and (Path(r['run_root'])==root or root in Path(r['run_root']).parents) for task_id,root in task_roots)}
    return sorted(merged.values(),key=lambda r:(r.get('sort_time',0),r['id']),reverse=True)

class RunCatalog:
    def __init__(self,scope,store):
        self.scope=scope;self.store=store;self.lock=threading.Lock();self.busy=False;self.finished=0
        self.quick_busy=False;self.quick_finished=0;self.quick_seen={}
        self.value={'runs':[],'expired_runs':0,'ttl_seconds':3600,'errors':[],'scanned_at':0}
        cached=store.root/'run-catalog.json'
        if cached.is_file():
            try:
                value=json.loads(cached.read_text(encoding='utf-8'))
                for run in value.get('runs',[]):scope.source(run['run_root'])
                if time.time()-value.get('scanned_at',0)<600:
                    from process_show.service.task_groups import aggregate
                    self.value=dict(value,runs=aggregate(scope.project,value.get('runs',[]),time.time()))
            except (ValueError,OSError,KeyError):pass
    def get(self,force=False):
        with self.lock:
            if not self.busy and (force or time.monotonic()-self.finished>5):
                self.busy=True;threading.Thread(target=self.refresh,daemon=True).start()
            if not self.quick_busy and time.monotonic()-self.quick_finished>2 and any(r.get('task_group') or r.get('trace_layout') or r.get('catalog_only') or r.get('adapter') for r in self.value.get('runs',[])):
                self.quick_busy=True;threading.Thread(target=self.refresh_known,daemon=True).start()
            value=dict(self.value,pending=self.busy)
            if self.value.get('quick_scan_error'):value['errors']=[*value.get('errors',[]),{'phase':'recent evaluations','error':self.value['quick_scan_error']}]
        now=time.time();value['runs']=[r for r in value['runs'] if not r.get('expires_at') or r['expires_at']>now or r.get('retained')]
        return value
    def refresh_known(self):
        try:
            from runs import discover,quick_roots
            with self.lock:known=[m['run_root'] for r in self.value.get('runs',[]) for m in (r['members'] if r.get('task_group') else [r])]
            roots=quick_roots(self.scope.project,self.store.root,known,self.quick_seen)
            fresh=discover(self.scope.project,self.store.root,roots=roots)
            with self.lock:
                self.value=dict(self.value,runs=merge_runs(self.value.get('runs',[]),fresh['runs']),scanned_at=fresh['scanned_at'],last_quick_scan=fresh['scanned_at'],quick_scan_error=None)
        except Exception as exc:
            with self.lock:self.value=dict(self.value,quick_scan_error=str(exc))
        finally:
            with self.lock:self.quick_busy=False;self.quick_finished=time.monotonic()
    def refresh(self):
        try:
            sys.path.insert(0,str(self.scope.project/'process-show/tools'))
            from runs import discover
            def publish(partial):
                with self.lock:
                    rows=[dict(r,display_name=r.get('display_name') or '/'.join(Path(r['run_root']).parts[-3:])) for r in partial['runs']]
                    self.value=dict(partial,runs=merge_runs(self.value.get('runs',[]),rows))
            value=discover(self.scope.project,self.store.root,None,publish)
            with self.lock:
                merged={r['id']:r for r in value['runs']}
                for row in self.value.get('runs',[]):
                    if row['id'] in merged or max(row.get('sort_time',0),row.get('last_update',0))>=value['scanned_at']:merged[row['id']]=row
                value=dict(value,runs=sorted(merged.values(),key=lambda r:(r.get('sort_time',0),r['id']),reverse=True))
            atomic_json(self.store.root/'run-catalog.json',value)
        except Exception as exc:value=dict(self.value,errors=[{'error':str(exc)}])
        with self.lock:self.value=value;self.busy=False;self.finished=time.monotonic()
