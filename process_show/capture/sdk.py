"""One recorder per evaluation worker. Nonblocking steps; no simulator imports."""
import multiprocessing as mp
import queue
import time

from process_show.storage.core import Store,validate_step


def _consumer(root, events, control):
    from PIL import Image
    from process_show.capture.engine import Engine
    engine = Engine(Store(root))
    processed = 0
    terminals = []
    closing = False
    while True:
        while control.poll():
            message = control.recv()
            if message[0] == "end":
                terminals.append(message[1:])
            elif message[0] == "close":
                closing = True
        try:
            eid, step, rgb, bev = events.get(timeout=.05)
            try:
                paused=(engine.store.root/'service-stop.json').exists()
                if paused:
                    processed+=1
                    continue
                if rgb is not None and not isinstance(rgb, Image.Image):
                    rgb = Image.fromarray(rgb)
                if bev is not None and not isinstance(bev, Image.Image):
                    bev = Image.fromarray(bev)
                engine.step(eid,step,rgb,bev)
            except Exception as exc:
                engine.store.error(eid, "capture: "+str(exc))
            processed += 1
        except queue.Empty:
            pass
        for terminal in list(terminals):
            target, eid, result = terminal
            if processed >= target:
                try:
                    engine.end(eid,result,encode_now=False)
                except Exception as exc:
                    engine.store.error(eid,"terminal: "+str(exc))
                terminals.remove(terminal)
        if closing and not terminals:
            break


class Recorder:
    def __init__(self, output_root, queue_size=128):
        from process_show.storage.scope import Scope
        Scope.current().output_path(output_root)
        from process_show.capture.autostart import ensure_service
        ensure_service(output_root)
        self.store = Store(output_root)
        ctx = mp.get_context("spawn")
        self.queue = ctx.Queue(maxsize=queue_size)
        parent, child = ctx.Pipe()
        self.control = parent
        self.process = ctx.Process(target=_consumer,args=(str(self.store.root), self.queue, child),daemon=True)
        self.process.start()
        self.eid = None
        self.accepted = 0
        self.dropped = []
        self.paused = False

    def start_episode(self, metadata):
        if self.eid is not None:
            raise ValueError("End previous episode first")
        self.eid = self.store.create(metadata)
        self.dropped = []
        self.paused = False
        return self.eid

    def record_step(self, step, rgb=None, bev=None):
        if self.eid is None:
            raise ValueError("Call start_episode first")
        if (self.store.root/'service-stop.json').exists():
            self.paused=True
            return False
        if not self.process.is_alive():
            self.store.error(self.eid,"capture worker unavailable")
            return False
        step=validate_step(step)
        try:
            # Copy before returning: environments may reuse/mutate sensor arrays.
            self.queue.put_nowait((self.eid,dict(step),rgb.copy() if rgb is not None else None,bev.copy() if bev is not None else None))
            self.accepted += 1
            return True
        except queue.Full:
            self.dropped.append(step.get("step"))
            return False

    def end_episode(self, result):
        if self.eid is None:
            raise ValueError("No episode")
        value = dict(result, dropped_steps=self.dropped)
        if self.paused:value.update(status='interrupted',capture_paused=True)
        self.control.send(("end",self.accepted,self.eid,value))
        eid,self.eid = self.eid,None
        return eid

    def close(self, timeout=60):
        if self.eid is not None:
            raise ValueError("end_episode required, including interrupted episodes")
        self.control.send(("close",))
        self.process.join(timeout)
        if self.process.is_alive():
            raise TimeoutError("Capture still draining; not killed. Pending evidence retained")
        if self.process.exitcode:
            raise RuntimeError("Capture worker failed")
        self.queue.close()
