"""Configure ONLY the project containing this script; called by the mapping UI."""
import argparse,json,sys
from pathlib import Path,PureWindowsPath,PurePosixPath
from project_mapping import project_identity,default_ports,write_json

def configure(plugin,windows_root,client_platform='windows'):
    plugin=Path(plugin).resolve();project=plugin.parent
    if client_platform not in ('windows','macos','linux'):raise ValueError('Unsupported client platform')
    if client_platform=='windows' and not PureWindowsPath(windows_root).is_absolute():raise ValueError('Absolute Windows project path required')
    if client_platform=='macos' and (not PurePosixPath(windows_root).is_absolute() or '..' in PurePosixPath(windows_root).parts):raise ValueError('Absolute macOS project path required')
    if client_platform=='linux' and Path(windows_root).resolve()!=project:raise ValueError('Linux service must belong to this project')
    path=plugin/'config/service.json'
    old=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    bound=old.get('project_roots',{}).get('posix')==str(project)
    identity=old.get('project_id') if bound else None
    identity=identity or project_identity(str(project))
    local,remote=default_ports(identity,windows_root)
    config=dict(old) if bound else {}
    config.update(project_id=identity,project_roots={**config.get('project_roots',{}),'posix':str(project),client_platform:windows_root},
                  enabled=config.get('enabled',True),auto_start_on_evaluation=config.get('auto_start_on_evaluation',True),
                  output='outputs/process-show',watch_config='process-show/config/sources.json',
                  remote_port=config.get('remote_port',remote),local_port=local)
    watch=plugin/'config/sources.json'
    sources=json.loads(watch.read_text(encoding='utf-8')).get('sources',[]) if watch.is_file() else []
    safe=[]
    for source in sources:
        source_path=PurePosixPath(source.get('run_root',''))
        if PurePosixPath(str(project)) in source_path.parents and '..' not in source_path.parts:safe.append(source)
    if len(safe)!=len(sources) or not watch.exists():write_json(watch,{'sources':safe})
    write_json(path,config)
    return {'project_root':str(project),'project_id':identity,'remote_port':config['remote_port'],
            'sources_removed':len(sources)-len(safe),'mapped':True}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--windows-root');parser.add_argument('--client-root');parser.add_argument('--client-platform',choices=['windows','macos','linux'],default='windows')
    args=parser.parse_args();print(json.dumps(configure(Path(__file__).resolve().parents[2],args.client_root or args.windows_root or '',args.client_platform)))
