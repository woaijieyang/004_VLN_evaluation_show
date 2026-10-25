"""Prepare dependencies exclusively inside this plugin's private environment."""
import os,subprocess,sys,venv
from pathlib import Path
plugin=Path(__file__).resolve().parents[1];project=plugin.parent
sys.path.insert(0,str(plugin))
from process_show.core import confined
environment=confined(project,plugin/'.venv')
cache=confined(project,project/'outputs/process-show/install-cache');cache.mkdir(parents=True,exist_ok=True)
temporary=cache/'tmp';temporary.mkdir(exist_ok=True)
for name in ('TMP','TEMP','TMPDIR'):os.environ[name]=str(temporary)
venv.EnvBuilder(with_pip=True).create(environment)
python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
env=dict(os.environ,TMP=str(temporary),TEMP=str(temporary),TMPDIR=str(temporary),PIP_CACHE_DIR=str(cache))
subprocess.check_call([str(python),'-m','pip','--isolated','--disable-pip-version-check','--cache-dir',str(cache),'install','--requirement',str(plugin/'config/requirements.lock')],env=env)
print('本目录依赖已准备：'+str(environment))
