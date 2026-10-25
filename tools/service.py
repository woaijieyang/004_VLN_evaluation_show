"""Start/stop ONLY this plugin's own recorded process identity."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

case=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(case/'process-show'))
from process_show.scope import Scope
scope=Scope.current()
out=scope.output;out.mkdir(parents=True,exist_ok=True)
receipt=out/"service-process.json"
from process_show.service.filelock import lock
launch_lock=(out/'service-launch.lock').open('a+b');lock(launch_lock)

from process_control import identity,owns_entry

command=sys.argv[1]
old=json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else None
alive=old and identity(old["pid"])==old["start_ticks"]
owned_entries={case/'process-show/tools/run.py',case/'process-show/run.py'}
if alive:
    alive=owns_entry(old['pid'],owned_entries)
if command=="stop":
    scope.stop()
    if alive:
        if not owns_entry(old['pid'],owned_entries):raise RuntimeError('Owner command mismatch')
        os.kill(old["pid"],signal.SIGTERM)
        deadline=time.monotonic()+5
        while identity(old['pid'])==old['start_ticks'] and time.monotonic()<deadline:
            time.sleep(.1)
        if identity(old['pid'])==old['start_ticks']:raise RuntimeError('Owned service is still stopping; wait before restart')
        print("Stopped owned process",old["pid"])
    else:print("No active owned process")
elif command in ('start','auto-start'):
    if command=='auto-start' and (not scope.config.get('enabled') or not scope.config.get('auto_start_on_evaluation') or scope.stopped_file.exists()):
        print(json.dumps({'running':bool(alive),'paused_by_user':scope.stopped_file.exists()}));sys.exit(0)
    if command=='start':
        if alive and scope.stopped_file.exists():
            deadline=time.monotonic()+5
            while identity(old['pid'])==old['start_ticks'] and time.monotonic()<deadline:time.sleep(.1)
            alive=identity(old['pid'])==old['start_ticks']
        scope.stopped_file.unlink(missing_ok=True)
    if alive:
        print(json.dumps(old));sys.exit(0)
    config=Path(sys.argv[2] if len(sys.argv)>2 else scope.project/scope.config['watch_config']).resolve()
    if case/"process-show" not in config.parents:raise ValueError("Config outside plugin")
    from process_show.ports import choose
    port=choose(scope.scope_id,int(scope.config.get('service_port',scope.config.get('remote_port',8765))))
    python=case/'runtime/awarevln/bin/python' if os.name!='nt' else case/'process-show/.venv/Scripts/python.exe'
    if not python.is_file() and (case/'process-show/.venv/bin/python').is_file():python=case/'process-show/.venv/bin/python'
    argv=[str(python) if python.exists() else sys.executable,"-B",str(case/"process-show/tools/run.py"),"--output",str(out),"serve","--config",str(config),'--port',str(port)]
    if sys.platform.startswith('linux') and not os.environ.get('CASE1_PROTECTION') and (case/'scripts/with_runtime.sh').exists():argv.insert(0,str(case/"scripts/with_runtime.sh"))
    with (out/"service.log").open("ab") as log:
        process=subprocess.Popen(argv,cwd=case,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    time.sleep(1)
    if process.poll() is not None:raise RuntimeError("Service exited; see service.log")
    data={"pid":process.pid,"start_ticks":identity(process.pid),"config":str(config),"url":f"http://127.0.0.1:{port}","remote_port":port,"project_id":scope.config['project_id'],"started_at":time.time(),'scope_id':scope.scope_id,'project_root':str(scope.project),'local_service':True}
    receipt.write_text(json.dumps(data,indent=2))
    print(json.dumps(data))
elif command=='status':print(json.dumps({'running':bool(alive),'process':old if alive else None}))
else:raise ValueError("Use start CONFIG, stop or status")
