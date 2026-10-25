"""Beginner-friendly local control of this plugin's service and owned SSH tunnel."""
import argparse,ctypes,json,os,re,shlex,shutil,socket,subprocess,sys,threading,time,urllib.request,webbrowser
from pathlib import Path,PurePosixPath

PLUGIN=Path(__file__).resolve().parents[2]
OUTPUT=PLUGIN.parent/'outputs/process-show/desktop'
RECEIPT=OUTPUT/'session.json'
HOST='codex-server';PORT=18765;REMOTE_PORT=8765
TRANSPORT_OVERRIDE=None
REMOTE_OVERRIDE=None
URL=f'http://127.0.0.1:{PORT}/'
FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0


def configuration():
    global HOST,PORT,REMOTE_PORT,URL
    profile=PLUGIN/'config/legacy/connection.json'
    cfg=json.loads((profile if profile.is_file() else PLUGIN/'config/service.json').read_text(encoding='utf-8'))
    key='windows' if os.name=='nt' else 'macos' if sys.platform=='darwin' else 'posix'
    if Path(cfg['project_roots'][key]).resolve()!=PLUGIN.parent.resolve():raise RuntimeError('此配置属于其他目录，禁止连接。请为当前项目准备独立配置。')
    HOST=cfg.get('ssh_alias','codex-server');PORT=TRANSPORT_OVERRIDE or int(cfg.get('local_port',18765));REMOTE_PORT=int(cfg.get('remote_port',8765));URL=f'http://127.0.0.1:{PORT}/'
    binding=OUTPUT/'network.json'
    if binding.is_file():
        data=json.loads(binding.read_text(encoding='utf-8'))
        if data.get('project_root')==str(PLUGIN.parent.resolve()) and data.get('remote_root')==cfg['project_roots']['posix'] and data.get('project_id')==cfg['project_id']:
            PORT=TRANSPORT_OVERRIDE or data['local_port'];URL=f'http://127.0.0.1:{PORT}/'
    if REMOTE_OVERRIDE:REMOTE_PORT=REMOTE_OVERRIDE
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',HOST):raise ValueError('Invalid SSH alias')
    return cfg


def ensure_local_binding():
    cfg=configuration()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(URL+'api/connection',timeout=.5) as response:current=json.load(response)
        if current.get('project_id')==cfg['project_id'] and current.get('project_root')==str(PLUGIN.parent.resolve()):return cfg
    except Exception:pass
    guardian=OUTPUT/'guardian.json'
    if guardian.is_file():
        record=json.loads(guardian.read_text(encoding='utf-8'))
        if record.get('project_root')==str(PLUGIN.parent.resolve()) and record.get('project_id')==cfg['project_id'] and process_identity(record.get('pid',0))==record.get('identity'):
            binding={'project_root':str(PLUGIN.parent.resolve()),'remote_root':cfg['project_roots']['posix'],'project_id':cfg['project_id'],'local_port':record['local_port'],'transport_port':record['local_port']+1}
            (OUTPUT/'network.json').write_text(json.dumps(binding,indent=2),encoding='utf-8')
            return configuration()
    # Reclaim only this project's old transport before reserving a new port pair.
    close_tunnel()
    sys.path.insert(0,str(PLUGIN))
    from process_show.ports import choose
    port=choose(cfg['project_id']+'|'+str(PLUGIN.parent.resolve())+'|'+cfg['project_roots']['posix'],PORT,pair=True)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    data={'project_root':str(PLUGIN.parent.resolve()),'remote_root':cfg['project_roots']['posix'],'project_id':cfg['project_id'],'local_port':port,'transport_port':port+1}
    temp=OUTPUT/'network.tmp';temp.write_text(json.dumps(data,indent=2));temp.replace(OUTPUT/'network.json')
    return configuration()


def run(argv,timeout=25):
    result=subprocess.run(argv,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,creationflags=FLAGS)
    if result.returncode:raise RuntimeError((result.stderr or result.stdout or 'Command failed').strip()[:1000])
    return result.stdout


def process_identity(pid):
    if os.name=='nt':
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return None
        try:
            exit_code=wintypes.DWORD()
            kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(exit_code)) or exit_code.value!=259:return None
            created,exited,kernel_time,user_time=(wintypes.FILETIME() for _ in range(4))
            kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
            if not kernel.GetProcessTimes(handle,*map(ctypes.byref,(created,exited,kernel_time,user_time))):return None
            return str((created.dwHighDateTime<<32)|created.dwLowDateTime)
        finally:
            kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(handle)
    if sys.platform=='darwin':
        result=subprocess.run(['ps','-p',str(pid),'-o','lstart=','-o','stat='],capture_output=True,text=True)
        value=result.stdout.strip()
        return value if result.returncode==0 and value and not value.split()[-1].startswith('Z') else None
    path=Path(f'/proc/{pid}/stat')
    if not path.exists():return None
    fields=path.read_text(encoding='utf-8').rsplit(')',1)[1].split()
    return None if fields[0]=='Z' else fields[19]


def health():
    try:
        cfg=configuration()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(URL+'api/health',timeout=2) as response:data=json.load(response)
        return data if data.get('app')=='process-show' and data.get('project_root')==cfg['project_roots']['posix'] and data.get('project_id')==cfg['project_id'] else None
    except Exception:return None


def ssh_command(action):
    cfg=configuration()
    if action not in ('start','auto-start','stop','status'):raise ValueError('Unsupported service action')
    remote=PurePosixPath(cfg['project_roots']['posix'])
    command=f'cd {shlex.quote(str(remote.parent))} && cd {shlex.quote(remote.name)} && /usr/bin/python3 -B process-show/tools/service.py '+action
    if action in ('start','auto-start'):command+=' '+shlex.quote(cfg['watch_config'])
    ssh=shutil.which('ssh')
    if not ssh:raise RuntimeError('没有找到 Windows OpenSSH 客户端。请在 Windows 可选功能中启用 OpenSSH 客户端。')
    return [ssh,'-o','BatchMode=yes','-o','ConnectTimeout=10',HOST,command]


def save_receipt(pid,adopted=False):
    OUTPUT.mkdir(parents=True,exist_ok=True)
    identity=process_identity(pid)
    if identity is None:raise RuntimeError('SSH 连接进程已退出，请查看 tunnel.log。')
    cfg=configuration()
    data={'pid':pid,'identity':identity,'host':HOST,'local_port':PORT,'remote_port':REMOTE_PORT,'adopted_from_task_receipt':adopted,'project_root':str(PLUGIN.parent.resolve()),'remote_project_root':cfg['project_roots']['posix'],'project_id':cfg['project_id']}
    temporary=RECEIPT.with_suffix('.tmp');temporary.write_text(json.dumps(data,indent=2));temporary.replace(RECEIPT)
    return data


def adopt_task_tunnel():
    if RECEIPT.exists():return
    old=OUTPUT.parent/'_qa/tunnel-pid.txt'
    if os.name!='nt' or not old.is_file():return
    pid=int(old.read_text(encoding='utf-8-sig').strip())
    command=run(['powershell','-NoProfile','-Command',f'Get-CimInstance Win32_Process -Filter "ProcessId={pid}" | Select-Object Name,CommandLine | ConvertTo-Json -Compress'])
    data=json.loads(command) if command.strip() else {}
    line=data.get('CommandLine','')
    if data.get('Name','').lower()=='ssh.exe' and HOST in line and f'{PORT}:127.0.0.1:{REMOTE_PORT}' in line:
        save_receipt(pid,adopted=True)


def connect_once(open_browser=True,report=print,automatic=False):
    global REMOTE_OVERRIDE
    configuration()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    if health():
        adopt_task_tunnel();report('插件已在运行，直接打开页面。')
        if open_browser:webbrowser.open(URL)
        return {'running':True,'url':URL,'reused':True}
    report('正在连接服务器并启动回放服务…')
    reply=run(ssh_command('auto-start' if automatic else 'start'))
    remote=json.loads(reply)
    if automatic and remote.get('paused_by_user'):raise RuntimeError('PAUSED_BY_USER')
    if remote.get('project_root')!=configuration()['project_roots']['posix'] or remote.get('project_id') not in (None,configuration()['project_id']):raise RuntimeError('Remote project identity mismatch')
    REMOTE_OVERRIDE=int(remote.get('remote_port') or remote['url'].rsplit(':',1)[1]);configuration()
    # Existing owned tunnels can reconnect to the just-started remote service.
    for _ in range(10):
        if health():
            adopt_task_tunnel()
            if open_browser:webbrowser.open(URL)
            return {'running':True,'url':URL,'reused':True}
        with socket.socket() as probe:
            probe.settimeout(1)
            occupied=probe.connect_ex(('127.0.0.1',PORT))==0
        if not occupied:break
        time.sleep(.2)
    if occupied:raise RuntimeError(f'本机端口 {PORT} 已占用，但未检测到 process-show。没有结束其他程序，请关闭旧连接后重试。')
    report('正在建立安全连接…')
    ssh=shutil.which('ssh')
    args=[ssh,'-N','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=2','-L',f'127.0.0.1:{PORT}:127.0.0.1:{REMOTE_PORT}',HOST]
    with (OUTPUT/'tunnel.log').open('ab') as log:
        process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=FLAGS,start_new_session=os.name!='nt')
    save_receipt(process.pid)
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError('SSH 连接失败。请检查网络/VPN；具体原因见 outputs/process-show/desktop/tunnel.log。')
        if health():
            report('已启动，可以在浏览器中使用。')
            if open_browser:webbrowser.open(URL)
            return {'running':True,'url':URL,'reused':False}
        time.sleep(.25)
    raise RuntimeError('连接等待超时。服务可能仍在启动，可点击“打开”重试。')


def close_tunnel():
    if not RECEIPT.exists():return False
    data=json.loads(RECEIPT.read_text(encoding='utf-8'));pid=data['pid']
    cfg=configuration()
    if data.get('project_root')!=str(PLUGIN.parent.resolve()) or data.get('project_id')!=cfg['project_id'] or data.get('remote_project_root')!=cfg['project_roots']['posix']:return False
    if process_identity(pid)!=data['identity']:return False
    if TRANSPORT_OVERRIDE is not None and data.get('local_port')!=TRANSPORT_OVERRIDE:return False
    if os.name=='nt':run(['taskkill','/PID',str(pid),'/F'],timeout=10)
    else:
        import signal
        os.kill(pid,signal.SIGTERM)
    return True


def start_plugin(open_browser=True,report=print):
    configuration();OUTPUT.mkdir(parents=True,exist_ok=True)
    ensure_local_binding()
    (OUTPUT/'connection-stop.json').unlink(missing_ok=True)
    # Explicit Open authorizes resuming a remotely stopped service.
    try:run(ssh_command('start'))
    except Exception as exc:report('网络暂不可用，后台会自动重连：'+str(exc)[:120])
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(URL+'api/connection',timeout=1) as response:
            current=json.load(response)
        cfg=configuration()
        if current.get('project_id')!=cfg['project_id'] or current.get('project_root')!=str(PLUGIN.parent.resolve()):raise RuntimeError('Local connection belongs to another project')
    except (OSError,ValueError):
        # Migrate only the tunnel whose PID/start identity belongs to this plugin.
        close_tunnel()
        with (OUTPUT/'guardian.log').open('ab') as log:
            subprocess.Popen([sys.executable,'-B',str(PLUGIN/'tools/legacy/connection_guard.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=FLAGS,start_new_session=os.name!='nt')
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        try:
            with opener.open(URL+'api/connection',timeout=.5) as response:data=json.load(response)
            if data.get('project_id')==configuration()['project_id']:break
        except Exception:time.sleep(.1)
    else:raise RuntimeError('本地连接守护未能启动，请查看 desktop/guardian.log')
    report('连接守护已开启，网络恢复后会自动重连。')
    if open_browser:webbrowser.open(URL)
    return {'running':bool(health()),'reconnecting':not bool(health()),'url':URL,'auto_reconnect':True,'reused':False}


def stop_plugin(report=print):
    configuration()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    (OUTPUT/'connection-stop.json').write_text('{}')
    report('正在关闭 process-show 服务…')
    run(ssh_command('stop'))
    adopt_task_tunnel()
    closed=close_tunnel()
    report('已关闭插件。模型测评和原始数据不受影响。')
    return {'running':False,'owned_tunnel_closed':closed}


def gui(initial):
    import tkinter as tk
    from tkinter import ttk
    root=tk.Tk();root.title('process-show 控制台 · '+PLUGIN.parent.name);root.geometry('560x390');root.resizable(False,False);root.configure(bg='white')
    tk.Label(root,text='process-show',font=('Microsoft YaHei UI',22,'bold'),bg='white',fg='black').pack(anchor='w',padx=28,pady=(22,4))
    tk.Label(root,text='当前项目：'+PLUGIN.parent.name+' · 无需手动输入命令',font=('Microsoft YaHei UI',11),bg='white',fg='black',wraplength=460,justify='left').pack(anchor='w',padx=28)
    status=tk.StringVar(value='点击“打开插件”开始使用。')
    tk.Label(root,textvariable=status,wraplength=440,height=4,justify='left',font=('Microsoft YaHei UI',11),bg='#f2f5ff',fg='black',padx=14,pady=14).pack(fill='x',padx=28,pady=18)
    frame=tk.Frame(root,bg='white');frame.pack(fill='x',padx=28)
    buttons=[]
    def report(message):
        OUTPUT.mkdir(parents=True,exist_ok=True)
        with (OUTPUT/'control.log').open('a',encoding='utf-8') as log:log.write(message+'\n')
        root.after(0,status.set,message if len(message)<130 else message[:110]+'…\n完整原因见 desktop/control.log。')
    def action(which):
        for button in buttons:button.configure(state='disabled')
        def worker():
            try:
                if which=='open':start_plugin(report=report)
                elif which=='close':stop_plugin(report=report)
                else:report('正在运行：'+URL if health() else '本地连接未运行。点击“打开插件”可自动启动。')
            except Exception as exc:report('操作未完成：'+str(exc)+'\n请检查网络后重试。')
            finally:root.after(0,lambda:[b.configure(state='normal') for b in buttons])
        threading.Thread(target=worker,daemon=True).start()
    for title,which in [('启动 / 打开页面','open'),('停止服务','close'),('查看状态','status')]:
        button=ttk.Button(frame,text=title,command=lambda value=which:action(value));button.pack(side='left',expand=True,fill='x',padx=4);buttons.append(button)
    tk.Label(root,text='断线后自动重连 · 关闭窗口继续运行 · 点“停止服务”停止' ,font=('Microsoft YaHei UI',9),bg='white',fg='black').pack(pady=22)
    connection_label=tk.StringVar(value='正在检查连接…')
    tk.Label(root,textvariable=connection_label,font=('Microsoft YaHei UI',11),bg='white',fg='black').pack()
    def connection_poll():
        def worker():
            try:
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(URL+'api/connection',timeout=2) as response:info=json.load(response)
                message=info.get('message','连接未开启')
                if info.get('retry_in'):message+=f" · {info['retry_in']}秒后重试"
                message+='\n本机 '+str(info.get('local_port',PORT))+' → SSH '+HOST+' → 服务器 '+str(info.get('remote_port',REMOTE_PORT))
            except Exception:message='连接未开启，点击启动即可'
            try:root.after(0,connection_label.set,message)
            except RuntimeError:pass
        threading.Thread(target=worker,daemon=True).start();root.after(3000,connection_poll)
    def mapping_dialog():
        dialog=tk.Toplevel(root);dialog.title('配置当前目录的服务器映射');dialog.geometry('620x420');dialog.configure(bg='white');dialog.transient(root);dialog.grab_set()
        tk.Label(dialog,text='为这个目录建立独立映射',bg='white',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w',padx=22,pady=(16,8))
        tk.Label(dialog,text='本机项目：'+str(PLUGIN.parent.resolve()),bg='white',wraplength=570,justify='left').pack(anchor='w',padx=22)
        tk.Label(dialog,text='SSH 别名（与本机 SSH 配置一致）',bg='white').pack(anchor='w',padx=22,pady=(12,2))
        alias=tk.StringVar();ttk.Entry(dialog,textvariable=alias).pack(fill='x',padx=22)
        tk.Label(dialog,text='对应的 Linux 项目绝对目录',bg='white').pack(anchor='w',padx=22,pady=(12,2))
        remote=tk.StringVar();ttk.Entry(dialog,textvariable=remote).pack(fill='x',padx=22)
        message=tk.StringVar(value='远端目录中需已有完整 process-show 插件。端口自动分配，不复制旧项目的数据源。')
        tk.Label(dialog,textvariable=message,bg='white',wraplength=570,justify='left').pack(anchor='w',padx=22,pady=14)
        def apply_mapping():
            from project_mapping import validate_remote,local_config,write_json
            try:
                remote_root=validate_remote(remote.get().strip());host_alias=alias.get().strip()
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',host_alias):raise ValueError('请填写 SSH 别名')
            except Exception as exc:message.set(str(exc));return
            apply_button.configure(state='disabled');message.set('正在核验并保存此目录映射…')
            def worker():
                try:
                    target=PurePosixPath(remote_root)
                    cmd=f'cd {shlex.quote(str(target.parent))} && cd {shlex.quote(target.name)} && /usr/bin/python3 -B process-show/tools/configure_mapping.py --windows-root '+shlex.quote(str(PLUGIN.parent.resolve()))
                    reply=json.loads(run([shutil.which('ssh') or 'ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',host_alias,cmd]))
                    config=local_config(PLUGIN,remote_root,host_alias,reply)
                    write_json(PLUGIN/'config/service.json',config)
                    root.after(0,lambda:(dialog.destroy(),configuration(),action('open')))
                except Exception as exc:
                    error=str(exc);root.after(0,message.set,'映射未保存：'+error[:240]);root.after(0,lambda:apply_button.configure(state='normal'))
            threading.Thread(target=worker,daemon=True).start()
        apply_button=ttk.Button(dialog,text='保存映射并启动',command=apply_mapping);apply_button.pack(pady=8)
    mapping_button=ttk.Button(frame,text='目录映射',command=mapping_dialog)
    mapping_button.pack(side='left',expand=True,fill='x',padx=4);buttons.append(mapping_button)
    connection_poll()
    try:configuration();mapped=True
    except (OSError,ValueError,RuntimeError,KeyError):mapped=False
    if not mapped:root.after(150,mapping_dialog)
    elif initial in ('open','close'):root.after(150,lambda:action(initial))
    root.mainloop()


def main():
    for stream in (sys.stdout,sys.stderr):
        if stream is not None and hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['open','close','status','gui'],default='gui',nargs='?');parser.add_argument('--cli',action='store_true');parser.add_argument('--no-browser',action='store_true');args=parser.parse_args()
    if args.cli:
        if args.action=='open':result=start_plugin(not args.no_browser,report=lambda s:print(s,file=sys.stderr))
        elif args.action=='close':result=stop_plugin(report=lambda s:print(s,file=sys.stderr))
        else:result={'running':bool(health()),'url':URL}
        print(json.dumps(result,ensure_ascii=False));return
    gui(args.action)


if __name__=='__main__':main()
