"""Bind the current launcher directory locally. No SSH or global installation."""
import hashlib,json,os,sys
from pathlib import Path
plugin=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(plugin))
from process_show.core import confined,atomic_json

def ensure_local_project(plugin):
    plugin=Path(plugin).absolute();project=plugin.parent
    confined(project,plugin)
    key='windows' if os.name=='nt' else 'macos' if sys.platform=='darwin' else 'posix'
    path=confined(project,plugin/'config/service.json')
    cfg=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    declared=cfg.get('project_roots',{}).get(key)
    changed=not declared or Path(declared).resolve()!=project.resolve()
    if changed:
        identity='process-show-'+hashlib.sha256(str(project).encode()).hexdigest()[:16]
        port=20000+2*(int(hashlib.sha256(identity.encode()).hexdigest()[:8],16)%10000)
        cfg={'project_id':identity,'project_roots':{key:str(project)},'service_port':port,
             'enabled':True,'auto_start_on_evaluation':True,'output':'outputs/process-show',
             'watch_config':'process-show/config/sources.json','stage':'evaluation'}
        path.parent.mkdir(parents=True,exist_ok=True);atomic_json(path,cfg)
    watch=confined(project,project/cfg.get('watch_config','process-show/config/sources.json'))
    original=json.loads(watch.read_text(encoding='utf-8')) if watch.is_file() else {'sources':[]}
    sources=[]
    for source in original.get('sources',[]):
        try:confined(project,Path(source['run_root']).absolute())
        except (ValueError,KeyError):continue
        sources.append(source)
    if len(sources)!=len(original.get('sources',[])) or not watch.is_file():
        watch.parent.mkdir(parents=True,exist_ok=True);atomic_json(watch,{'sources':sources})
    return cfg
