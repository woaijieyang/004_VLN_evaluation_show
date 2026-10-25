"""One local-only entry shared by Linux, macOS and Windows launchers."""
import json,os,subprocess,sys,webbrowser,urllib.request
from pathlib import Path
from urllib.parse import quote
plugin=Path(__file__).resolve().parents[1];project=plugin.parent
NO_BROWSER='--no-browser' in sys.argv
sys.path.insert(0,str(plugin))
from local_project import ensure_local_project

def local_command(action,selection=None):
    argv=[sys.executable,'-B',str(plugin/'tools/runs.py'),action]
    if selection:argv.append(selection)
    if action=='list' and sys.platform.startswith('linux') and (project/'scripts/with_runtime.sh').is_file() and (project/'runtime/awarevln/bin/python').is_file():
        argv=[str(project/'scripts/with_runtime.sh'),str(project/'runtime/awarevln/bin/python'),'-B',str(plugin/'tools/runs.py'),action]
    return json.loads(subprocess.check_output(argv,cwd=project,text=True,encoding='utf-8'))

def service(action):
    private=plugin/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    python=str(private) if private.is_file() else sys.executable
    return subprocess.check_output([python,'-B',str(plugin/'tools/service.py'),action],cwd=project,text=True,encoding='utf-8')

def open_picker():
    private=plugin/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    runtime=project/'runtime/awarevln/bin/python'
    python=str(runtime) if sys.platform.startswith('linux') and runtime.is_file() else str(private) if private.is_file() else sys.executable
    check=subprocess.run([python,'-c',"import PIL,shutil,importlib.util; assert shutil.which('ffmpeg') or importlib.util.find_spec('imageio_ffmpeg')"],capture_output=True)
    if check.returncode:
        print('首次使用：正在准备本目录专用依赖，不修改系统或模型环境。')
        subprocess.check_call([sys.executable,'-B',str(plugin/'tools/bootstrap.py')],cwd=project)
    service('start')
    running=json.loads(service('status'));url=running['process']['url']+'/?choose=1'
    if not NO_BROWSER and (os.name=='nt' or sys.platform=='darwin' or os.environ.get('DISPLAY')):webbrowser.open(url)
    print('当前根目录：'+str(project));print('服务所在机器的选择页：'+url)
    if sys.platform.startswith('linux') and not os.environ.get('DISPLAY'):print('此 127.0.0.1 指运行服务的 Linux 主机，不是另一台电脑。')


def choose_run():
    while True:
        result=local_command('list');runs=result['runs']
        print('\n日期（新加坡时间） | 模型概况 | 数据集 | 完成条数 | 状态')
        for n,r in enumerate(runs,1):
            print(f"{n:>3}. {r['date']} | {r['model']} [{r['model_sha'] or '权重未知'}] | {r['dataset']} | {r['completed']}/{r['expected'] if r['expected'] is not None else '?'} | {r['status']}"+(' · 有保留样本' if r['retained'] else ''))
            print('     '+r['run_id'])
        print(f"已剔除过期运行 {result['expired_runs']} 个。r 刷新；q 返回。")
        answer=input('选择运行编号：').strip().lower()
        if answer=='q':return
        if answer=='r':continue
        if not answer.isdigit() or not 1<=int(answer)<=len(runs):print('请输入列表中的编号。');continue
        chosen=runs[int(answer)-1]
        service('start')
        running=json.loads(service('status'));base=running['process']['url']
        request=urllib.request.Request(base+'/api/runs/select',data=json.dumps({'id':chosen['id']}).encode(),headers={'Content-Type':'application/json'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request,timeout=20) as response:selection=json.load(response)
        url=selection['url']
        if not NO_BROWSER and (os.name=='nt' or sys.platform=='darwin' or os.environ.get('DISPLAY')):webbrowser.open(url)
        print('已选择：'+chosen['run_id']);print('服务所在机器的回放地址：'+url);return

def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    ensure_local_project(plugin)
    args=[arg for arg in sys.argv[1:] if arg!='--no-browser'];action=args[0] if args else 'menu'
    if action=='list':print(json.dumps(local_command('list'),ensure_ascii=False));return
    if action=='start':open_picker();return
    if action=='select':choose_run();return
    if action in ('stop','status'):print(service(action));return
    open_picker()
    while True:
        print('\nprocess-show · '+project.name+'\n作用范围：'+str(project)+'\n1) 打开测评选择弹窗\n2) 停止本目录的回放服务\n3) 查看状态\n4) 准备本目录专用依赖（首次使用）\nq) 退出菜单（后台继续运行）')
        answer=input('请选择 [1]：').strip().lower() or '1'
        if answer=='q':return
        if answer=='1':open_picker()
        elif answer=='2':print(service('stop'))
        elif answer=='3':print(service('status'))
        elif answer=='4':subprocess.check_call([sys.executable,'-B',str(plugin/'tools/bootstrap.py')],cwd=project)

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:print('操作失败：'+str(exc));sys.exit(1)
    except (EOFError,KeyboardInterrupt):print('\n已退出，后台状态不变。')
