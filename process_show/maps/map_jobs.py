"""Bounded CPU map requests. HTTP handlers never wait on geometry generation."""
import threading,time,queue
from collections import OrderedDict

class MapJobs:
    def __init__(self):
        self.jobs=OrderedDict();self.lock=threading.Lock();self.queue=queue.Queue(maxsize=8)
        self.closed=threading.Event();self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def request(self,key,generate):
        with self.lock:
            entry=self.jobs.get(key)
            if entry:
                if 'result' in entry:
                    if entry['result'].get('available') or time.monotonic()-entry['finished']<5:return entry['result']
                    del self.jobs[key]
                else:return {'available':False,'pending':True,'state':entry['state'],'reason':'正在生成场景环境图' if entry['state']=='running' else '地图已排队，等待生成','retry_after':1.5}
            if self.queue.full():return {'available':False,'pending':True,'state':'busy','reason':'地图队列繁忙，正在等待空位','retry_after':3}
            self.jobs[key]={'state':'queued'};self.queue.put_nowait((key,generate))
            return {'available':False,'pending':True,'state':'queued','reason':'地图已排队，等待生成','retry_after':1}
    def run(self):
        while not self.closed.is_set():
            try:key,generate=self.queue.get(timeout=.2)
            except queue.Empty:continue
            with self.lock:self.jobs[key]['state']='running'
            try:result=generate()
            except Exception as exc:result={'available':False,'reason':str(exc)}
            with self.lock:
                self.jobs[key]={'result':result,'finished':time.monotonic()}
                completed=[k for k,v in self.jobs.items() if 'result' in v]
                for old in completed[:-32]:self.jobs.pop(old,None)
            self.queue.task_done()
    def close(self):self.closed.set()
