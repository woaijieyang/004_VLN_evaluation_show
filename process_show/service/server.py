import hashlib
import io
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import os
import threading
import time
from urllib.parse import urlparse, parse_qs

from process_show.storage.core import dumps
from process_show.media.render import extract_frame,ensure_preview
from process_show.playback.presentation import replay_payload,observation_image,provided_map
from process_show.maps.scene_map import maps_for


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_close(self):
        if hasattr(self,"map_jobs"):self.map_jobs.close()
        super().server_close()


def make_server(store, host="127.0.0.1", port=8765):
    from process_show.storage.scope import Scope
    scope=Scope.current()
    scope.output_path(store.root)
    static = Path(__file__).resolve().parents[1] / "web"
    decode_lock = threading.Semaphore(1)
    cache = {}
    observations=OrderedDict()
    observation_lock=threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self,*args):
            pass

        def response_headers(self, code, content_type, length=None,cache_control='no-store'):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", cache_control)
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self' http://127.0.0.1:* http://localhost:*; img-src 'self' blob:; media-src 'self' blob:; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
            if getattr(self,'probe_origin',None):
                self.send_header('Access-Control-Allow-Origin',self.probe_origin)
                self.send_header('Vary','Origin')
            if length is not None:
                self.send_header("Content-Length", str(length))

        def send_bytes(self,data,ctype="application/json; charset=utf-8",code=200,cacheable=False):
            self.response_headers(code,ctype,len(data),'private, max-age=3600' if cacheable else 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def json(self,value,code=200):
            self.send_bytes(dumps(value).encode(),code=code)

        def safe_request(self,allow_probe=False):
            host_header = self.headers.get("Host","")
            host_name = host_header.split(":")[0]
            if host_name not in ("localhost","127.0.0.1",host):
                raise ValueError("Host not allowed")
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != host_header:
                if allow_probe and urlparse(origin).scheme=='http' and urlparse(origin).hostname in ('127.0.0.1','localhost'):self.probe_origin=origin
                else:raise ValueError("Cross-origin request rejected")

        def allowed_episode(self,row):
            bound=getattr(self.server,'bound_run',None)
            if not bound:return True
            return row['meta']['run_id']==bound['run_id'] and (row['meta']['model']==bound.get('source_model',bound['model']) if bound.get('catalog_only') else row['meta'].get('source_run')==bound['run_root'])

        def do_GET(self):
            try:
                self.probe_origin=None
                parsed = urlparse(self.path)
                path,query = parsed.path,parse_qs(parsed.query)
                token=getattr(self.server,'probe_token',None)
                self.safe_request(allow_probe=bool(path=='/api/health' and token and query.get('probe',[None])[0]==token))
                if path in ("/","/app.js","/style.css","/viewer.js","/viewer.css","/navigation.js","/route_overlay.js"):
                    file = static / ("viewer.html" if path == "/" else path[1:])
                    return self.send_bytes(file.read_bytes(),mimetypes.guess_type(file.name)[0] or "text/plain")
                if path == '/api/runs':
                    value=self.server.catalog_provider.get()
                    selected_path=store.root/'selected-run.json'
                    value['selected']=json.loads(selected_path.read_text(encoding='utf-8')) if selected_path.is_file() else None
                    return self.json(value)
                if path == "/api/episodes":
                    rows=store.list()
                    safe_rows=[]
                    for r in rows:
                        if not self.allowed_episode(r):continue
                        try:
                            if r['meta'].get('source_run'):scope.source(r['meta']['source_run'])
                        except ValueError:continue
                        r['meta']={k:v for k,v in r['meta'].items() if k in ('run_id','dataset','split','date','stage','scene_id','episode_id','model','instruction_id','source_completed','source_total_steps','video_version')}
                        r.pop('result',None);r.pop('directory',None)
                        safe_rows.append(r)
                    return self.json(safe_rows)
                if path == '/api/health':
                    return self.json({'app':'process-show','running':True,'pid':os.getpid(),'api_version':1,'project_root':str(scope.project),'scope_id':scope.scope_id,'project_name':scope.project.name,'project_id':scope.config['project_id'],'service_port':self.server.server_port,'run_id':getattr(self.server,'bound_run',{}).get('run_id'),'evaluation_id':getattr(self.server,'bound_run',{}).get('id')})
                if path == '/api/sync':
                    manager=getattr(self.server,'sync_manager',None)
                    return self.json(manager.snapshot() if manager else {'sources':[],'configured_sources':0,'expired_episodes':0})
                if path == "/api/policy":
                    return self.json({"policy":store.setting("policy"),"usage_bytes":store.usage()})
                if path == "/api/events":
                    self.response_headers(200,"text/event-stream")
                    self.send_header("Connection","close")
                    self.end_headers()
                    self.close_connection = True
                    last = None
                    while True:
                        with store.db() as db:
                            signature=dumps(list(db.execute('SELECT COUNT(*),MAX(updated),SUM(pinned),SUM(selected) FROM episodes WHERE deleted IS NULL').fetchone()))
                        data = "event: update\ndata: "+dumps({"revision":signature})+"\n\n" if signature != last else ": heartbeat\n\n"
                        self.wfile.write(data.encode())
                        self.wfile.flush()
                        last = signature
                        time.sleep(1)
                map_match=re.fullmatch(r'/api/maps/([0-9a-f]{64})\.webp',path)
                if map_match:
                    from process_show.storage.core import confined
                    map_path=confined(store.root,store.root/'_maps'/(map_match[1]+'.webp'))
                    return self.send_bytes(map_path.read_bytes(),'image/webp',cacheable=True)
                match = re.fullmatch(r"/api/episodes/([0-9a-f]{64})(?:/(steps|frame|video|thumbnail|replay|observation|map|map-image))?",path)
                if not match:
                    return self.json({"error":"Not found"},404)
                eid,kind = match.groups()
                row = store.get(eid)
                if not self.allowed_episode(row):return self.json({'error':'此端口只允许访问它对应的测评'},403)
                if row['meta'].get('source_run'):scope.source(row['meta']['source_run'])
                if row["deleted"] is not None:
                    return self.json({"error":"Video expired"},410)
                if kind is None:
                    return self.json(row)
                if kind=='replay':
                    with store.lease(eid):
                        payload=replay_payload(store,eid)
                        if 'after' in query:payload['steps']=[s for s in payload['steps'] if s['step']>=int(query['after'][0])]
                        return self.json(payload)
                if kind=='observation':
                    n=int(query.get('step',['0'])[0]);strict=query.get('strict',['0'])[0]=='1'
                    key=(eid,n,strict)
                    with store.lease(eid),observation_lock:
                        if key not in observations:
                            im,source_step=observation_image(store,eid,n,strict)
                            if im is None:return self.json({'error':'No recorded observation','requested_step':n},404)
                            output=io.BytesIO();im.thumbnail((960,960));im.save(output,'WEBP',quality=88,method=1)
                            observations[key]=(output.getvalue(),source_step)
                            if len(observations)>128:observations.popitem(last=False)
                        data,source_step=observations[key]
                    self.response_headers(200,'image/webp',len(data),'private, max-age=3600')
                    self.send_header('X-Observation-Step',str(source_step))
                    self.send_header('X-Requested-Step',str(n))
                    self.end_headers();self.wfile.write(data);return
                if kind=='map':
                    height=float(query.get('height',['0'])[0]);mode=query.get('mode',['scene'])[0]
                    try:
                        provided=provided_map(store,eid,int(query.get('step',['2147483647'])[0]))
                        if provided:return self.json(provided)
                        provider=maps_for(store)
                        key=(row['meta'].get('source_run'),row['meta'].get('scene_id'),round(height*4)/4,mode)
                        info=provider.get(row['meta'],height,cached_only=True) if mode=='nav' else provider.scene(row['meta'],height,cached_only=True)
                        if info.get('uncached'):info=self.server.map_jobs.request(key,lambda: provider.get(row['meta'],height) if mode=='nav' else provider.scene(row['meta'],height))
                    except (ImportError,ValueError,OSError) as exc:
                        info={'available':False,'reason':str(exc)}
                    return self.json(info)
                if kind=='map-image':
                    n=int(query.get('step',['0'])[0])
                    step=next(s for s in store.steps(eid) if s['step']==n)
                    with store.lease(eid):
                        return self.send_bytes(store.path(eid,step['map_asset']).read_bytes(),'image/webp',cacheable=True)
                if kind == "steps":
                    index_path = store.path(eid,"steps.json")
                    with store.lease(eid):
                        if index_path.is_file():
                            return self.send_bytes(index_path.read_bytes())
                        return self.json({"steps":store.steps(eid),"index":[],"fps":2})
                if kind == "frame":
                    n = int(query.get("step",["0"])[0])
                    page = int(query.get("page",["0"])[0])
                    with store.lease(eid):
                        if row["status"] not in ("ready","expired"):
                            ensure_preview(store,eid,n)
                        preview = store.path(eid,f"previews/{n:07d}-{page:03d}.webp")
                        if preview.is_file():
                            return self.send_bytes(preview.read_bytes(),"image/webp")
                        if row["status"] != "ready":
                            return self.json({"error":"Frame not yet available"},404)
                        key = (eid,n)
                        with decode_lock:
                            if key not in cache:
                                data = extract_frame(store,eid,n)
                                if len(cache) >= 12:
                                    cache.pop(next(iter(cache)))
                                cache[key] = data
                            return self.send_bytes(cache[key],"image/jpeg")
                file = store.path(eid,"thumbnail.webp" if kind == "thumbnail" else Path(row["directory"]).name+".mp4")
                if kind=='video' and row['status']!='ready':return self.json({'error':'Video encoding is pending'},409)
                if not file.is_file():
                    return self.json({"error":"Video is not finalized"},409)
                with store.lease(eid) as lease:
                    size = file.stat().st_size
                    begin,end = 0,size-1
                    range_header = self.headers.get("Range")
                    code = 200
                    if range_header:
                        m = re.fullmatch(r"bytes=(\d*)-(\d*)",range_header)
                        if not m or not any(m.groups()):
                            return self.json({"error":"Unsupported range"},416)
                        a,b = m.groups()
                        if a:
                            begin,end = int(a),min(int(b),size-1) if b else size-1
                        else:
                            begin = max(0,size-int(b))
                        if begin > end or begin >= size:
                            self.response_headers(416,"text/plain",0)
                            self.send_header("Content-Range",f"bytes */{size}")
                            self.end_headers()
                            return
                        code = 206
                    self.response_headers(code,"image/webp" if kind == "thumbnail" else "video/mp4",end-begin+1)
                    self.send_header("Accept-Ranges","bytes")
                    if code == 206:
                        self.send_header("Content-Range",f"bytes {begin}-{end}/{size}")
                    if "download" in query:
                        from urllib.parse import quote
                        self.send_header("Content-Disposition","attachment; filename*=UTF-8''"+quote(file.name))
                    self.end_headers()
                    with file.open("rb") as f:
                        f.seek(begin)
                        remaining = end-begin+1
                        while remaining:
                            block = f.read(min(256*1024,remaining))
                            if not block:
                                break
                            self.wfile.write(block)
                            remaining -= len(block)
                            store.renew(lease)
            except (BrokenPipeError,ConnectionResetError):
                pass
            except KeyError:
                self.json({"error":"Not found"},404)
            except (ValueError,OSError) as exc:
                self.json({"error":str(exc)},400)

        def do_POST(self):
            try:
                self.probe_origin=None
                self.safe_request()
                if not self.headers.get("Content-Type","").startswith("application/json"):
                    raise ValueError("JSON Content-Type required")
                size = int(self.headers.get("Content-Length","0"))
                if not 0 < size <= 65536:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError("JSON object required")
                if self.path=='/api/control/stop':
                    if body.get('scope_id')!=scope.scope_id:raise ValueError('Project scope mismatch')
                    if not getattr(self.server,'bound_run',None):scope.stop()
                    self.json({'stopped':True,'scope_id':scope.scope_id})
                    callback=getattr(self.server,'stop_callback',self.server.shutdown)
                    threading.Thread(target=callback,daemon=True).start()
                    return
                if self.path == '/api/refresh':
                    manager=getattr(self.server,'sync_manager',None)
                    if not manager:return self.json({'error':'No configured source watcher; start with the project watch config'},409)
                    if set(body)-{'history'}:raise ValueError('Only configured sources may be refreshed')
                    return self.json(manager.request(body.get('history',True)),202)
                if self.path == '/api/runs/select':
                    if set(body)!={'id'} or not isinstance(body['id'],str):raise ValueError('请指定列表中的测评 ID')
                    import sys
                    sys.path.insert(0,str(scope.project/'process-show/tools'))
                    from runs import select
                    manager=getattr(self.server,'sync_manager',None)
                    if manager is None:return self.json({'error':'此服务未启用运行选择'},409)
                    chosen=select(scope,body['id'],persist_watch=False)
                    sessions=getattr(self.server,'run_sessions',None)
                    if sessions is None:return self.json({'error':'测评端口管理器尚未启动'},409)
                    return self.json(dict(sessions.open(chosen),entry_port=self.server.server_port,scope_id=scope.scope_id))
                if self.path == "/api/policy":
                    return self.json(store.set_policy(body))
                render_match=re.fullmatch(r'/api/episodes/([0-9a-f]{64})/render',self.path)
                if render_match:
                    eid=render_match[1];row=store.get(eid)
                    if not self.allowed_episode(row):return self.json({'error':'此端口不对应这次测评'},403)
                    if row['deleted'] is not None:raise ValueError('Episode expired')
                    if row['result'] is None:raise ValueError('Episode is still running')
                    with store.db() as db:
                        if row['status']!='encoding':db.execute("UPDATE episodes SET status='pending_encode',updated=? WHERE id=?",(store.clock(),eid))
                        db.execute('INSERT INTO render_requests VALUES (?,?) ON CONFLICT(episode) DO UPDATE SET requested=excluded.requested',(eid,store.clock()))
                    return self.json({'queued':True})
                match = re.fullmatch(r"/api/episodes/([0-9a-f]{64})/pin",self.path)
                if match:
                    if not self.allowed_episode(store.get(match[1])):return self.json({'error':'此端口不对应这次测评'},403)
                    if type(body.get("pinned")) is not bool:
                        raise ValueError("pinned must be boolean")
                    store.pin(match[1],body["pinned"])
                    return self.json({"ok":True})
                self.json({"error":"Not found"},404)
            except (ValueError,KeyError,OSError) as exc:
                self.json({"error":str(exc)},400)

    server=Server((host,port),Handler)
    from .catalog import RunCatalog
    server.catalog_provider=RunCatalog(scope,store)
    from process_show.maps.map_jobs import MapJobs
    server.map_jobs=MapJobs()
    return server
