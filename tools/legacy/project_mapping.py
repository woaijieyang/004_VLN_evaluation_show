"""Portable directory mapping. No current machine paths are embedded here."""
import hashlib,json,re,sys
from pathlib import Path,PurePosixPath

def validate_remote(value):
    path=PurePosixPath(value)
    if not path.is_absolute() or '..' in path.parts or str(path)=='/':
        raise ValueError('请填写绝对 Linux 项目目录，不要填写 process-show 子目录。')
    return str(path)

def project_identity(remote_root):
    return 'process-show-'+hashlib.sha256(validate_remote(remote_root).encode()).hexdigest()[:16]

def default_ports(project_id,local_root):
    def number(value):return 20000+2*(int(hashlib.sha256(value.encode()).hexdigest()[:8],16)%10000)
    return number(project_id+'|'+str(Path(local_root).resolve())),number(project_id+'|remote')

def local_config(plugin,remote_root,alias,remote):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',alias):raise ValueError('SSH 别名格式不正确')
    remote_root=validate_remote(remote_root)
    if remote['project_root']!=remote_root:raise ValueError('远端返回了其他项目身份')
    local_root=str(Path(plugin).resolve().parent)
    client='macos' if sys.platform=='darwin' else 'windows'
    local_port,_=default_ports(remote['project_id'],local_root)
    return {'project_id':remote['project_id'],'project_roots':{client:local_root,'posix':remote_root},
            'ssh_alias':alias,'local_port':local_port,'remote_port':remote['remote_port'],
            'enabled':True,'auto_start_on_evaluation':True,'output':'outputs/process-show',
            'watch_config':'process-show/config/sources.json','stage':'evaluation'}

def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(path)
