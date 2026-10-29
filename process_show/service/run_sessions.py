"""One loopback HTTP port per evaluation, sharing project storage and encoder."""
import hashlib,json,threading,time,secrets
from urllib.parse import quote
from process_show.storage.core import atomic_json
from process_show.storage.ports import choose

class RunSessions:
    def __init__(self,store,sync,catalog=None):
        self.store=store;self.sync=sync;self.catalog=catalog;self.sessions={};self.lock=threading.RLock()
        self.path=store.root/'run-sessions.json'
        self.prior=json.loads(self.path.read_text(encoding='utf-8')) if self.path.is_file() else {}
        if catalog and sync.service_stop is not None:threading.Thread(target=self.expiry_loop,daemon=True).start()

    def open(self,chosen):
        from process_show.service.server import make_server
        from process_show.storage.scope import Scope
        scope=Scope.current();scope.source(chosen['run_root'])
        fallback='/evaluations/'+quote(chosen['id'],safe='')+'/?evaluation='+quote(chosen['id'],safe='')
        with self.lock:
            entry=self.sessions.get(chosen['id'])
            if entry:
                entry['server'].bound_run=chosen
                entry['chosen']=chosen
                atomic_json(self.store.root/'selected-run.json',chosen)
                return {'selected':chosen,'url':entry['url'],'fallback_url':fallback,'port':entry['port'],'probe_token':entry['server'].probe_token}
            if len(self.sessions)>=16:raise ValueError('本目录已打开16个测评服务，请先停止不用的测评')
            self.sync.ensure_run(chosen)
            preferred=self.prior.get(chosen['id'],{}).get('port')
            if not preferred:preferred=20000+int(hashlib.sha256((scope.scope_id+chosen['id']).encode()).hexdigest()[:8],16)%20000
            for _ in range(3):
                port=choose(scope.scope_id+'|'+chosen['id'],preferred)
                try:server=make_server(self.store,port=port);break
                except OSError:preferred=port+1
            else:raise OSError('未能绑定空闲测评端口，请重试')
            server.bound_run=chosen;server.sync_manager=self.sync;server.run_sessions=self
            server.probe_token=secrets.token_urlsafe(24)
            if self.catalog:server.catalog_provider=self.catalog
            server.stop_callback=lambda:self.close(chosen['id'])
            url=f'http://127.0.0.1:{server.server_port}/?run='+quote(chosen['run_id'])
            url+='&evaluation='+quote(chosen['id'])
            if chosen.get('catalog_only'):url+='&model='+quote(chosen['model'])
            self.sessions[chosen['id']]={'server':server,'url':url,'port':server.server_port,'chosen':chosen}
            self.prior[chosen['id']]={'url':url,'port':server.server_port,'run_id':chosen['run_id'],'root':chosen['run_root'],'chosen':chosen}
            atomic_json(self.path,self.prior)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            atomic_json(self.store.root/'selected-run.json',chosen)
            return {'selected':chosen,'url':url,'fallback_url':fallback,'port':server.server_port,'probe_token':server.probe_token}

    def close(self,identity,persist=True):
        with self.lock:
            entry=self.sessions.pop(identity,None)
            if persist:self.prior.pop(identity,None);atomic_json(self.path,self.prior)
        if entry:entry['server'].shutdown();entry['server'].server_close()

    def close_all(self):
        for identity in list(self.sessions):self.close(identity,persist=False)

    def restore(self):
        for value in list(self.prior.values())[:16]:
            chosen=value.get('chosen')
            if not chosen:continue
            if chosen.get('expires_at') and chosen['expires_at']<=time.time() and not chosen.get('retained'):continue
            try:self.open(chosen)
            except (ValueError,OSError):continue

    def expiry_loop(self):
        while not self.sync.service_stop.wait(30):
            value=self.catalog.get()
            if not value.get('scanned_at') or value.get('errors'):continue
            allowed={r['id'] for r in value.get('runs',[])}
            with self.lock:expired=[identity for identity in self.sessions if identity not in allowed]
            for identity in expired:self.close(identity)
