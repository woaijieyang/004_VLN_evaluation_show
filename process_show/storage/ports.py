"""Project-derived port candidates with collision checks; no global registry."""
import hashlib,os,socket

def candidates(identity,preferred):
    yield preferred
    start=int(hashlib.sha256(identity.encode()).hexdigest()[:8],16)%10000
    for offset in range(10000):
        port=20000+2*((start+offset)%10000)
        if port!=preferred:yield port

def free(port):
    with socket.socket() as probe:
        probe.settimeout(.15)
        if probe.connect_ex(('127.0.0.1',port))==0:return False
    with socket.socket() as sock:
        if os.name=='nt':sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        else:sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:sock.bind(('127.0.0.1',port));return True
        except OSError:return False

def choose(identity,preferred,pair=False):
    if type(preferred) is not int or not 1024<=preferred<=65534:
        raise ValueError('Port must be an integer in 1024..65534')
    for port in candidates(identity,preferred):
        if free(port) and (not pair or free(port+1)):return port
    raise OSError('No available loopback port for this project')
