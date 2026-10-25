"""Compatibility only: reachable local run URLs for the existing debug bridge.

The three local-only product launchers never import this module.
"""
import json,os,shutil,subprocess,threading,time,urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import quote

class Child:
    def __init__(self,manager,run_id,remote_port,port,identity):
        self.manager=manager;self.owner=manager.owner;self.cfg=manager.cfg
        self.run_id=run_id;self.evaluation_id=identity;self.remote_port=remote_port;self.port=port;self.transport=None
        self.record={};self.state='reconnecting';self.stopped=False
    def status(self):
        value=self.owner.status();state='stopped' if self.stopped or value['state']=='stopped' else self.state
        value.update(state=state,message='已连接' if state=='connected' else '此测评已停止，请从选择页重新打开' if state=='stopped' else '正在自动恢复此测评连接',local_port=self.port,remote_port=self.remote_port,run_id=self.run_id)
        return value
    def get_forwarder(self):return self.manager

class RunForwarder:
    def __init__(self,launcher,owner,handler_factory):
        self.launcher=launcher;self.owner=owner;self.cfg=owner.cfg;self.factory=handler_factory
        self.path=launcher.OUTPUT/'run-forwards.json';self.children={};self.lock=threading.RLock();self.last_tick=0
        try:self.saved=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.saved={}
        import sys
        sys.path.insert(0,str(launcher.PLUGIN))
        from process_show.storage.ports import choose
        self.choose=choose;self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def matches(self,record):
        return record.get('project_id')==self.cfg['project_id'] and record.get('project_root')==str(self.launcher.PLUGIN.parent.resolve()) and record.get('remote_root')==self.cfg['project_roots']['posix']
    def save(self):
        for identity,child in self.children.items():
            self.saved[identity]=dict(child.record,id=identity,run_id=child.run_id,local_port=child.port,transport_port=child.transport,remote_port=child.remote_port,project_id=self.cfg['project_id'],project_root=str(self.launcher.PLUGIN.parent.resolve()),remote_root=self.cfg['project_roots']['posix'])
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps(self.saved,indent=2),encoding='utf-8');temporary.replace(self.path)
    def healthy(self,child):
        try:
            with self.opener.open(f'http://127.0.0.1:{child.transport}/api/health',timeout=1) as response:value=json.load(response)
            return value.get('project_root')==self.cfg['project_roots']['posix'] and value.get('project_id')==self.cfg['project_id'] and value.get('run_id')==child.run_id and value.get('evaluation_id',child.evaluation_id)==child.evaluation_id
        except Exception:return False
    def alive(self,record):
        return bool(record.get('pid') and self.launcher.process_identity(record['pid'])==record.get('identity'))
    def stop_transport(self,child):
        record=child.record
        if not self.alive(record):return
        if os.name=='nt':self.launcher.run(['taskkill','/PID',str(record['pid']),'/F'],timeout=10)
        else:
            import signal
            os.kill(record['pid'],signal.SIGTERM)
    def start_transport(self,child):
        if self.healthy(child):child.state='connected';return
        if self.alive(child.record):return
        ssh=shutil.which('ssh')
        if not ssh:raise RuntimeError('OpenSSH client unavailable')
        argv=[ssh,'-N','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=2','-L',f'127.0.0.1:{child.transport}:127.0.0.1:{child.remote_port}',self.cfg.get('ssh_alias','codex-server')]
        with (self.launcher.OUTPUT/'run-tunnels.log').open('ab') as log:
            process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=self.launcher.FLAGS,start_new_session=os.name!='nt')
        child.record={'pid':process.pid,'identity':self.launcher.process_identity(process.pid)}
        if child.record['identity'] is None:raise RuntimeError('Run transport exited before registration')
        self.save()
    def ensure(self,run,remote_port,preferred=None):
        if type(remote_port) is not int or not 1024<=remote_port<=65534:raise ValueError('Invalid run port')
        identity=run.get('id',run['run_id'])
        with self.lock:
            child=self.children.get(identity)
            if child is None:
                if len(self.children)>=16:raise RuntimeError('Too many open debug run links')
                port=self.choose(self.cfg['project_id']+'|debug|'+identity,preferred or remote_port)
                child=Child(self,run['run_id'],remote_port,port,identity)
                server=ThreadingHTTPServer(('127.0.0.1',port),self.factory(child));server.daemon_threads=True
                child.server=server
                saved=self.saved.get(identity,{})
                if self.matches(saved) and self.alive(saved):child.transport=saved['transport_port'];child.record=saved
                else:child.transport=self.choose(identity+'|transport',port+1)
                self.children[identity]=child
                threading.Thread(target=server.serve_forever,daemon=True).start()
            if child.remote_port!=remote_port:self.stop_transport(child);child.record={};child.remote_port=remote_port
            child.stopped=False;child.state='reconnecting';self.start_transport(child)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if self.healthy(child):child.state='connected';self.save();return f'http://127.0.0.1:{child.port}/?run='+quote(child.run_id)
            time.sleep(.15)
        raise RuntimeError('测评连接尚未准备好，请稍后重试；未返回不可达的地址。')
    def restore(self):
        for record in list(self.saved.values())[:16]:
            if not self.matches(record):continue
            try:self.ensure(record,record['remote_port'],record['local_port'])
            except (OSError,ValueError,RuntimeError):pass
    def tick(self):
        if time.monotonic()-self.last_tick<5:return
        self.last_tick=time.monotonic()
        for child in list(self.children.values()):
            if child.stopped:continue
            try:
                with self.lock:
                    self.start_transport(child)
                    child.state='connected' if self.healthy(child) else 'reconnecting'
            except Exception:child.state='reconnecting'
    def stop_all_transports(self):
        for child in list(self.children.values()):self.stop_transport(child);child.state='stopped'
