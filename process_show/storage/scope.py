"""Production service scope is bound to the folder containing this plugin."""
import hashlib,json,os,sys
from pathlib import Path
from process_show.storage.core import confined


class Scope:
    def __init__(self,project,config):
        confined(Path(project).absolute(),Path(project).absolute())
        self.project=Path(project).resolve()
        self.config=config
        key='windows' if os.name=='nt' else 'macos' if sys.platform=='darwin' else 'posix'
        declared=Path(config['project_roots'][key]).resolve()
        if declared!=self.project:raise ValueError('配置属于其他项目目录；请为当前目录配置独立的 process-show。')
        self.output=confined(self.project,self.project/config.get('output','outputs/process-show'))
        self.scope_id=hashlib.sha256((config['project_id']+'|'+str(self.project)).encode()).hexdigest()[:24]
    @classmethod
    def current(cls):
        plugin=Path(__file__).absolute().parents[2]
        confined(plugin.parent,plugin)
        if not (plugin/'config/service.json').is_file():plugin=Path.cwd()/'process-show'
        return cls(plugin.parent,json.loads((plugin/'config/service.json').read_text(encoding='utf-8')))
    def source(self,path):
        path=confined(self.project,Path(path).absolute())
        if '.shared-ro' in path.parts:raise ValueError('共享资源只可作为场景/数据资产，不能连接其中其他项目的测评输出。')
        return path
    def output_path(self,path):
        return confined(self.output,Path(path).absolute())
    @property
    def stopped_file(self):return self.output/'service-stop.json'
    def stop(self):
        from process_show.storage.core import atomic_json
        import time
        self.output.mkdir(parents=True,exist_ok=True)
        atomic_json(self.stopped_file,{'scope_id':self.scope_id,'stopped_by_user':True,'at':time.time()})
