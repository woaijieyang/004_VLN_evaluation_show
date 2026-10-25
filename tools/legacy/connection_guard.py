"""Project-owned stable local proxy and reconnecting SSH transport."""
import http.client,json,threading,time,socket,os,sys
from urllib.parse import urlparse,parse_qs
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import desktop_launcher as launcher

class Guardian:
    def __init__(self):
        cfg=launcher.configuration();self.port=launcher.PORT;self.transport=self.port+1
        self.cfg=cfg;self.state={'state':'reconnecting','message':'正在建立连接','attempt':0,'retry_in':0}
        self.lock=threading.Lock();self.stop=threading.Event();self.last_connected=None;self.forwards=None
    def status(self):
        with self.lock:return dict(self.state,project_id=self.cfg['project_id'],project_root=str(launcher.PLUGIN.parent.resolve()),remote_root=self.cfg['project_roots']['posix'],local_port=self.port,transport_port=self.transport,remote_port=launcher.REMOTE_PORT,last_connected=self.last_connected,auto_reconnect=True)
    def get_forwarder(self):
        if self.forwards is None:
            from run_forward import RunForwarder
            self.forwards=RunForwarder(launcher,self,handler_for)
        return self.forwards
    def update(self,**values):
        with self.lock:self.state.update(values)
    def loop(self):
        launcher.TRANSPORT_OVERRIDE=self.transport;launcher.PORT=self.transport;launcher.URL=f'http://127.0.0.1:{self.transport}/'
        failures=0
        while not self.stop.is_set():
            if (launcher.OUTPUT/'connection-stop.json').exists():
                launcher.close_tunnel();
                if self.forwards:self.forwards.stop_all_transports()
                self.update(state='stopped',message='已手动停止',retry_in=0);self.stop.wait(1);continue
            if launcher.health():
                failures=0;self.last_connected=time.time();self.update(state='connected',message='已连接',attempt=0,retry_in=0)
                if self.forwards:self.forwards.tick()
                self.stop.wait(2);continue
            failures+=1;self.update(state='reconnecting',message='连接中断，正在自动重连',attempt=failures)
            try:
                launcher.close_tunnel()
                launcher.connect_once(False,lambda _:None,automatic=True)
                continue
            except Exception as exc:
                if 'PAUSED_BY_USER' in str(exc):
                    (launcher.OUTPUT/'connection-stop.json').write_text('{}');continue
                self.update(message='网络暂不可用，正在自动重连',error=str(exc)[:300])
            delay=min(30,2**min(failures,5))
            for left in range(delay,0,-1):
                self.update(retry_in=left)
                if self.stop.wait(1) or (launcher.OUTPUT/'connection-stop.json').exists():break

FALLBACK='<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>process-show · 正在重连</title><style>body{font:17px/1.8 "Segoe UI","Microsoft YaHei",sans-serif;background:#f7f9fc;color:#111;margin:10vh auto;max-width:600px;padding:28px}main{padding:32px;background:white;border:1px solid #e2e7ee;border-radius:16px}h1{font-size:27px}#status{font-weight:600}</style><main><h1>process-show</h1><p id="status">正在恢复服务器连接…</p><p>网络恢复后会自动连接并返回回放页面，无需重新双击打开。</p></main><script>async function check(){try{const r=await fetch(\'/api/connection\');const s=await r.json();document.getElementById(\'status\').textContent=s.message+(s.retry_in?\' · \'+s.retry_in+\' 秒后重试\':\'\');if(s.state===\'connected\')location.reload();if(s.state===\'stopped\')document.querySelector(\'main p:last-child\').textContent=\'服务已停止，请从测评选择页重新打开。\';}catch(e){} }setInterval(check,2000);check();</script>'

def handler_for(guardian):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,data,kind='application/json; charset=utf-8'):
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
        def do_GET(self):self.forward()
        def do_POST(self):self.forward()
        def forward(self):
            host=self.headers.get('Host','');origin=self.headers.get('Origin')
            parsed=urlparse(self.path)
            health_probe=bool(self.command=='GET' and parsed.path=='/api/health' and parse_qs(parsed.query).get('probe') and origin and urlparse(origin).scheme=='http' and urlparse(origin).hostname in ('127.0.0.1','localhost'))
            if host not in (f'127.0.0.1:{guardian.port}',f'localhost:{guardian.port}') or (origin and origin not in ('http://'+host,) and not health_probe):
                return self.send(403,b'{"error":"Origin rejected"}')
            if self.path=='/api/connection':return self.send(200,json.dumps(guardian.status()).encode())
            length=int(self.headers.get('Content-Length','0'))
            if length>65536:return self.send(413,b'{}')
            body=self.rfile.read(length) if length else None
            headers={k:v for k,v in self.headers.items() if k.lower() not in ('host','connection','accept-encoding')}
            headers['Host']=host
            if getattr(guardian,'run_id',None) and guardian.status()['state']!='connected':
                if self.path.split('?',1)[0]=='/':return self.send(200,FALLBACK.encode(),'text/html; charset=utf-8')
                return self.send(503,b'{"error":"This evaluation is reconnecting or stopped"}')
            conn=http.client.HTTPConnection('127.0.0.1',guardian.transport,timeout=20);started=False
            try:
                conn.request(self.command,self.path,body,headers);response=conn.getresponse()
                if self.path=='/api/runs/select' and response.status==200:
                    value=json.loads(response.read())
                    reachable=guardian.get_forwarder().ensure(value['selected'],value['port'])
                    value['service_url']=value['url'];value['url']=reachable;value['port']=int(reachable.split(':')[2].split('/')[0]);value['entry_port']=guardian.port
                    return self.send(200,json.dumps(value).encode())
                if self.path=='/api/control/stop' and response.status==200:
                    if getattr(guardian,'run_id',None):guardian.stopped=True
                    else:(launcher.OUTPUT/'connection-stop.json').write_text('{}')
                started=True;self.send_response(response.status)
                for key,value in response.getheaders():
                    if key.lower() not in ('connection','transfer-encoding'):self.send_header(key,value)
                self.send_header('Connection','close');self.end_headers();self.close_connection=True
                while True:
                    chunk=response.read1(65536)
                    if not chunk:break
                    self.wfile.write(chunk);self.wfile.flush()
            except (OSError,http.client.HTTPException,ValueError,RuntimeError):
                if not started:
                    try:
                        if self.path.split('?',1)[0]=='/':self.send(200,FALLBACK.encode(),'text/html; charset=utf-8')
                        else:self.send(503,b'{"error":"Connection interrupted; reconnecting automatically"}')
                    except OSError:pass
            finally:conn.close()
    return Handler

def serve():
    guardian=Guardian()
    sys.path.insert(0,str(launcher.PLUGIN))
    from process_show.service.filelock import lock
    singleton=(launcher.OUTPUT/'guardian.lock').open('a+b');lock(singleton,blocking=False)
    server=ThreadingHTTPServer(('127.0.0.1',guardian.port),handler_for(guardian));server.daemon_threads=True
    (launcher.OUTPUT/'guardian.json').write_text(json.dumps({'pid':os.getpid(),'identity':launcher.process_identity(os.getpid()),'project_root':str(launcher.PLUGIN.parent.resolve()),'project_id':guardian.cfg['project_id'],'local_port':guardian.port}),encoding='utf-8')
    guardian.get_forwarder()
    threading.Thread(target=guardian.loop,daemon=True).start()
    threading.Thread(target=guardian.forwards.restore,daemon=True).start()
    try:server.serve_forever()
    finally:guardian.stop.set();server.server_close()

if __name__=='__main__':serve()
