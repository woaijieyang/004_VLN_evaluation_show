import hashlib,json,os
from pathlib import Path
root=Path(__file__).resolve().parents[2]
result={}
for directory,dirs,files in os.walk(root,followlinks=False):
    dirs[:]=[d for d in dirs if d not in {'.assets','.venv','__pycache__','build','dist'} and not d.endswith('.egg-info')]
    for name in files:
        path=Path(directory)/name
        if path.is_symlink():raise RuntimeError('Unexpected source symlink')
        if name.endswith('.pyc'):continue
        result[path.relative_to(root).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps(result,sort_keys=True,indent=2))
