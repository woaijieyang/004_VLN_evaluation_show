"""Advisory locks stored only inside the owning project's output directory."""
import os

def lock(file,blocking=True):
    if os.name=='nt':
        import msvcrt
        file.seek(0,os.SEEK_END)
        if file.tell()==0:file.write(b'\0');file.flush()
        file.seek(0)
        msvcrt.locking(file.fileno(),msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(file.fileno(),fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
