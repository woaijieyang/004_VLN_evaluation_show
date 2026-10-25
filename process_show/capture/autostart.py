"""Opt-in-by-local-config evaluation startup hook; never launches a model."""
import os,subprocess,sys
from pathlib import Path


def ensure_service(output_root=None):
    from process_show.storage.scope import Scope
    scope=Scope.current()
    if output_root is not None:
        scope.output_path(output_root)
        if Path(output_root).resolve()!=scope.output:return False
    if not scope.config.get('enabled') or not scope.config.get('auto_start_on_evaluation') or scope.stopped_file.exists():return False
    scope.output.mkdir(parents=True,exist_ok=True)
    with (scope.output/'autostart.log').open('ab') as log:
        subprocess.Popen([sys.executable,'-B',str(scope.project/'process-show/tools/service.py'),'auto-start',str(scope.project/scope.config['watch_config'])],cwd=scope.project,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    return True
