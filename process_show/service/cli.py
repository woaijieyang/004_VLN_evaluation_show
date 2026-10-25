import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

from process_show.storage.core import Store, dumps, atomic_json
from process_show.capture.adapters import Importer
from process_show.media.render import encode, ffmpeg_exe, RenderDeferred


def work_loop(store, stop):
    # Reclaim an interrupted encode only while no encoder owns the OS lock.
    from .filelock import lock as lock_file
    with (store.root/"encoder.lock").open("a+b") as lock:
        try:
            lock_file(lock,blocking=False)
            with store.db() as db:
                db.execute("UPDATE episodes SET status='pending_encode',error='Recovering interrupted encoder' WHERE status='encoding'")
        except OSError:
            pass
    while not stop.is_set():
        with store.db() as db:
            pending = [r[0] for r in db.execute("SELECT e.id FROM episodes e LEFT JOIN render_requests r ON r.episode=e.id WHERE e.status='pending_encode' AND e.deleted IS NULL ORDER BY COALESCE(r.requested,0) DESC,e.created LIMIT 1")]
        for eid in pending:
            if stop.is_set():
                break
            try:
                encode(store,eid)
            except RenderDeferred:
                continue
            except Exception as exc:
                with store.db() as db:
                    db.execute("UPDATE episodes SET status='encode_error',error=? WHERE id=?",(str(exc),eid))
                print("Encoding error:",eid,str(exc),file=sys.stderr,flush=True)
        stop.wait(1)


def cleanup_loop(store, stop):
    while not stop.is_set():
        try:
            store.cleanup()
        except Exception as exc:
            print("Cleanup blocked:",str(exc),file=sys.stderr,flush=True)
        stop.wait(60)



def main(argv=None):
    parser = argparse.ArgumentParser(description="Step-indexed VLN replay. Reads only explicit input roots.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cpu-limit",type=int,default=2)
    sub = parser.add_subparsers(dest="command",required=True)
    for command in ("convert","watch"):
        p=sub.add_parser(command)
        p.add_argument("--run-root",type=Path,required=True)
        p.add_argument("--stage",required=True)
        p.add_argument("--model",required=True)
        p.add_argument("--adapter",choices=("case1","generic"),default="case1")
        p.add_argument("--started-at",help="Run start ISO timestamp with timezone; prefer manifest time")
        p.add_argument("--trace",action="append",type=Path,default=[],help="Explicit trace path relative to run-root; repeatable")
        p.add_argument("--max-episodes",type=int,default=0,help="0=all discovered; positive=bounded initial conversion")
        if command=="convert":
            p.add_argument("--defer-encode",action="store_true")
        else:
            p.add_argument("--port",type=int,default=8765)
    p=sub.add_parser("serve");p.add_argument("--port",type=int,default=8765);p.add_argument("--config",type=Path)
    p=sub.add_parser("cleanup");p.add_argument("--dry-run",action="store_true")
    sub.add_parser("doctor")
    sub.add_parser("list")
    p=sub.add_parser("encode");p.add_argument("id")
    p=sub.add_parser("pin");p.add_argument("id");p.add_argument("--off",action="store_true")
    p=sub.add_parser("policy");p.add_argument("--json",help="JSON policy patch")
    args=parser.parse_args(argv)
    from process_show.storage.scope import Scope
    scope=Scope.current()
    if args.output is None:args.output=scope.output
    scope.output_path(args.output)
    if getattr(args,'config',None):scope.source(args.config)
    if args.cpu_limit<1:parser.error("cpu-limit must be positive")
    if hasattr(os,"sched_setaffinity"):
        os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:args.cpu_limit])
    store=Store(args.output)
    if args.command=="doctor":
        import PIL,subprocess
        f=ffmpeg_exe()
        data={"python":sys.version,"pillow":PIL.__version__,"ffmpeg":f,"ffmpeg_version":subprocess.check_output([f,"-version"],text=True).splitlines()[0],"output":str(store.root),"cpu_encoder_threads":2,"gpu":False}
        print(dumps(data));return
    if args.command=="list":
        print(dumps(store.list()));return
    if args.command=="cleanup":
        print(dumps(store.cleanup(args.dry_run)));return
    if args.command=="pin":
        store.pin(args.id,not args.off);return
    if args.command=="policy":
        print(dumps(store.set_policy(json.loads(args.json)) if args.json else store.setting("policy")));return
    if args.command=="encode":
        encode(store,args.id);return
    if args.command in ("convert","watch"):
        importer=Importer(store,args.run_root,args.stage,args.model,args.adapter,args.started_at,lazy=args.command=="watch")
        traces=[importer.root/p for p in args.trace]
        if args.command=="convert":
            paths=traces or importer.discover()
            if args.max_episodes:
                paths=paths[:args.max_episodes]
            for path in paths:
                eid=importer.poll(path,encode_now=not args.defer_encode)
                print(dumps({"trace":str(path),"episode":eid}),flush=True)
            return
    stop=threading.Event()
    service_lock=(store.root/"service.lock").open("a+b")
    from .filelock import lock as lock_file
    lock_file(service_lock,blocking=False)
    from process_show.service.sync import SyncManager
    sync=SyncManager(store)
    sync.service_stop=stop
    threads=[threading.Thread(target=work_loop,args=(store,stop),daemon=True),threading.Thread(target=cleanup_loop,args=(store,stop),daemon=True)]
    if args.command=="watch":
        source_sync=sync.add(importer,True,traces,args.max_episodes)
        threads.append(threading.Thread(target=source_sync.run,args=(stop,),daemon=True))
    elif args.command=="serve" and args.config:
        config=json.loads(args.config.read_text(encoding="utf-8"))
        sources=config.get("sources",[])
        selected_path=store.root/'selected-run.json'
        if selected_path.is_file():
            selected=json.loads(selected_path.read_text(encoding='utf-8'))
            scope.source(selected['run_root'])
            if not selected.get('expires_at') or selected['expires_at']>time.time() or selected.get('retained'):
                sources=[] if selected.get('catalog_only') else [dict(run_root=selected['run_root'],stage=selected['stage'],model=selected.get('source_model',selected['model']),include_history=True)]
        for source in sources:
            importer=Importer(store,source["run_root"],source["stage"],source["model"],source.get("adapter","case1"),source.get("started_at"),lazy=True)
            source_sync=sync.add(importer,source.get("include_history",False),limit=source.get("max_episodes",0))
            threads.append(threading.Thread(target=source_sync.run,args=(stop,),daemon=True))
    from process_show.service.server import make_server
    server=make_server(store,port=args.port)
    server.sync_manager=sync
    from process_show.service.run_sessions import RunSessions
    server.run_sessions=RunSessions(store,sync,server.catalog_provider)
    for t in threads:t.start()
    server.run_sessions.restore()
    print(f"process-show listening on http://127.0.0.1:{server.server_port}",flush=True)
    def shutdown(*_):
        stop.set()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown)
    server.stop_callback=shutdown
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        shutdown()
    finally:
        stop.set();server.run_sessions.close_all();server.server_close()


if __name__=="__main__":main()
