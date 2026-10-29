from pathlib import Path
import json
import time

from PIL import Image

from process_show.storage.core import Store, dumps, confined
from process_show.media.render import compose, instruction_pages, encode


class Engine:
    def __init__(self, store):
        self.store = store

    def start(self, meta):
        return self.store.create(meta)

    def step(self, eid, step, rgb=None, map_image=None, render_preview=True):
        store = self.store
        store.capacity()
        start = time.monotonic()
        step = dict(step)
        step["rgb_available"] = rgb is not None
        step["map_available"] = map_image is not None
        row = store.get(eid)
        if row["status"] != "live":
            return
        if rgb is not None:
            directory=store.path(eid,'observations');directory.mkdir(exist_ok=True)
            name=f"observations/{step['step']:07d}.webp"
            path=store.path(eid,name);tmp=path.with_suffix('.tmp')
            store.own(eid,path);store.own(eid,tmp)
            rgb.convert('RGB').save(tmp,'WEBP',quality=88,method=2);tmp.replace(path)
            step['observation_asset']=name
        if map_image is not None:
            import hashlib
            name='maps/'+hashlib.sha256(map_image.tobytes()).hexdigest()+'.webp'
            path=store.path(eid,name);path.parent.mkdir(exist_ok=True)
            store.own(eid,path)
            if not path.exists():map_image.convert('RGB').save(path,'WEBP',lossless=True)
            step['map_asset']=name
        previous = store.steps(eid)
        existing = next((s for s in previous if s["step"] == step["step"]), None)
        if existing and existing.get("rgb_available") and rgb is None:
            # Text-only model_call update must not overwrite already recorded RGB.
            existing.update({k:v for k,v in step.items() if k not in ("rgb_available", "map_available", "previews")})
            store.put_step(eid,existing)
            return
        step['published_at']=time.time()
        store.put_step(eid,step)
        if not render_preview:
            return  # RGB + telemetry are already available; exports compose on demand.
        folder = store.path(eid,"previews")
        folder.mkdir(exist_ok=True)
        history = [s for s in previous if s["step"] != step["step"]] + [step]
        names = []
        for page in range(len(instruction_pages(row["meta"]["instruction"]))):
            name = f"previews/{step['step']:07d}-{page:03d}.webp"
            path = store.path(eid,name)
            temp = path.with_suffix(".tmp")
            store.own(eid,path)
            store.own(eid,temp)
            image = compose(row["meta"],step,history,rgb,map_image,page)
            image.save(temp,"WEBP",quality=85,method=2)
            temp.replace(path)
            names.append(name)
        step["previews"] = names
        step["render_ms"] = (time.monotonic()-start)*1000
        step["published_at"] = time.time()
        store.put_step(eid,step)

    def end(self, eid, result, encode_now=True):
        steps = self.store.steps(eid)
        row = self.store.get(eid)
        if row["status"] == "ready" or row["deleted"] is not None:
            return
        result = dict(result)
        total = result.get("total_steps")
        if type(total) is not int or total < 0:
            raise ValueError("Result requires total_steps")
        ids = [s["step"] for s in steps]
        result["missing_steps"] = sorted(set(range(total+1))-set(ids))
        result["missing_rgb_steps"] = [s["step"] for s in steps if not s.get("rgb_available")]
        result["complete_telemetry"] = not result["missing_steps"] and not result["missing_rgb_steps"]
        if ids and max(ids) > total:
            raise ValueError("Result total smaller than observed step")
        # Missing SDK events are explicit timeline frames, never interpolated data.
        for n in result["missing_steps"]:
            if not result.get('capture_paused'):self.step(eid,{"step":n,"action":"UNRECORDED","gap":True})
        success = result.get("success")
        outcome = "success" if success is True else "failure" if success is False else "unknown"
        if result.get("status") != "completed":
            outcome = "unknown"
        self.store.save_json(eid,"result.json",result)
        with self.store.db() as db:
            db.execute("UPDATE episodes SET result=?,outcome=?,status='pending_encode',updated=? WHERE id=?", (dumps(result),outcome,self.store.clock(),eid))
        if encode_now:
            encode(self.store,eid)


def open_asset(root, value):
    if not value:
        return None
    p = confined(root, Path(root) / value)
    if not p.is_file():
        return None
    with Image.open(p) as im:
        return im.convert("RGB").copy()
