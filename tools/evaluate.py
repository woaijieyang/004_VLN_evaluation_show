"""Run a current-project Python evaluation with default local service startup."""
import subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from process_show.scope import Scope
from process_show.autostart import ensure_service


def main():
    scope=Scope.current()
    if len(sys.argv)<2:raise SystemExit('用法: python process-show/tools/evaluate.py 当前项目内的测评脚本.py [参数]')
    entry=scope.source(scope.project/sys.argv[1])
    if not entry.is_file() or entry.suffix!='.py':raise SystemExit('必须指定当前项目内的 Python 测评入口')
    ensure_service()
    raise SystemExit(subprocess.call([sys.executable,str(entry),*sys.argv[2:]],cwd=scope.project))


if __name__=='__main__':main()
