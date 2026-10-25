"""Cross-platform process identity; queries only the recorded project process."""
import os,sys,subprocess,ctypes,base64,shlex
from pathlib import Path

def identity(pid):
    if os.name=='nt':
        from ctypes import wintypes
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.OpenProcess.restype=wintypes.HANDLE;k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        handle=k.OpenProcess(0x1000,False,int(pid))
        if not handle:return None
        try:
            code=wintypes.DWORD();k.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
            if not k.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259:return None
            values=[wintypes.FILETIME() for _ in range(4)]
            k.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
            if not k.GetProcessTimes(handle,*map(ctypes.byref,values)):return None
            return str((values[0].dwHighDateTime<<32)|values[0].dwLowDateTime)
        finally:k.CloseHandle.argtypes=[wintypes.HANDLE];k.CloseHandle(handle)
    if sys.platform=='darwin':
        value=subprocess.run(['ps','-p',str(int(pid)),'-o','lstart='],capture_output=True,text=True)
        return value.stdout.strip() if value.returncode==0 else None
    path=Path(f'/proc/{int(pid)}/stat')
    if not path.is_file():return None
    fields=path.read_text(encoding='utf-8').rsplit(')',1)[1].split()
    return None if fields[0]=='Z' else fields[19]

def owns_entry(pid,entries):
    if os.name=='nt':
        command=f"[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' | Select-Object -ExpandProperty CommandLine"
        encoded=base64.b64encode(command.encode('utf-16le')).decode()
        result=subprocess.run(['powershell','-NoProfile','-EncodedCommand',encoded],capture_output=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
        if result.returncode:return False
        count=ctypes.c_int();shell=ctypes.WinDLL('shell32');shell.CommandLineToArgvW.restype=ctypes.POINTER(ctypes.c_wchar_p)
        shell.CommandLineToArgvW.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_int)]
        argv=shell.CommandLineToArgvW(result.stdout.strip(),ctypes.byref(count))
        if not argv:return False
        try:return any(str(entry) in [argv[i] for i in range(count.value)] for entry in entries)
        finally:
            kernel=ctypes.WinDLL('kernel32');kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree(ctypes.cast(argv,ctypes.c_void_p))
    if sys.platform=='darwin':
        result=subprocess.run(['ps','-p',str(int(pid)),'-o','command='],capture_output=True,text=True)
        return any(str(entry) in result.stdout for entry in entries) if result.returncode==0 else False
    argv=Path(f'/proc/{int(pid)}/cmdline').read_bytes().split(b'\0')
    return any(str(entry).encode() in argv for entry in entries)
