"""Incremental read-only adapters, restricted to explicitly selected input roots."""
import hashlib
import json
from pathlib import Path
import re
import time
from datetime import datetime, timezone

from process_show.storage.core import confined, dumps
from process_show.capture.engine import Engine, open_asset


def generic_metadata(path):
    """Inspect one bounded start record; partial writes are not complete episodes."""
    try:
        with path.open('rb') as stream:line=stream.readline(256*1024)
        if not line.endswith(b'\n'):return None
        event=json.loads(line)
        if event.get('schema_version')==1 and event.get('type')=='start':
            meta=event.get('metadata')
            if isinstance(meta,dict) and all(isinstance(meta.get(k),str) and meta[k] for k in ('run_id','model')):return meta
    except (OSError,ValueError,AttributeError):pass
    return None


def instruction_from_prompt(prompt):
    # Case1 official prompt contains Navigation Instruction: ... followed by context.
    patterns = [r'Your assigned task is: "(.*?)"\. (?:The reasoning from|Analyze this series)', r"(?:Navigation instruction|Navigation Instruction|Instruction|instruction):\s*(.*?)(?=\n(?:Reasoning|History|Current|Previous|ASSISTANT|###)|</s>|$)", r"instruction\s*[\"']([^\"']+)[\"']"]
    for pattern in patterns:
        match = re.search(pattern,prompt,re.S)
        if match:
            return match.group(1).strip(), "extracted_prompt"
    return prompt, "full_prompt_fallback"


class Importer:
    def __init__(self, store, run_root, stage, model, adapter="case1", started_at=None, lazy=False):
        self.store = store
        self.engine = Engine(store)
        self.root = Path(run_root).absolute()
        from process_show.storage.scope import Scope
        Scope.current().source(self.root)
        self.root=confined(self.root,self.root)
        if not self.root.is_dir():
            raise ValueError("Input root missing")
        self.stage,self.model,self.adapter = stage,model,adapter
        manifest_path=confined(self.root,self.root/"run_manifest.json")
        self.manifest=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.is_file() else {}
        self.started_at = started_at or self.manifest.get("created_at")
        self.lazy=lazy
        self.evaluation=None
        self.run_id=self.manifest.get('run_id',self.root.name)

    def _step(self,eid,step,rgb_ref=None):
        if self.lazy:
            step=dict(step)
            step["previews"]=[]
            if rgb_ref is not None:
                step["rgb_source"]=rgb_ref
                image_path=confined(self.root,self.root/rgb_ref["path"])
                step["rgb_available"]=image_path.is_file()
            else:
                step.setdefault("rgb_available",False)
            step["lazy_source"]=True
            self.store.put_step(eid,step)
        else:
            self.engine.step(eid,step,open_asset(self.root,rgb_ref["path"]) if rgb_ref else None)

    def discover(self):
        patterns = ("shards/shard-*/attempt-*/episodes/*/trace.jsonl","episodes/*/trace.jsonl") if self.adapter == "case1" else ("*.jsonl",)
        paths=sorted({p for pattern in patterns for p in self.root.glob(pattern) if not p.is_symlink()})
        if self.adapter!='case1' and self.evaluation:
            paths=[p for p in paths if (lambda m:m and m['run_id']==self.evaluation['run_id'] and m['model']==self.evaluation['model'])(generic_metadata(p))]
        return paths

    def poll(self, path, encode_now=False, max_events=None, max_seconds=None):
        path = confined(self.root,path)
        key = str(path)
        with self.store.db() as db:
            saved = db.execute("SELECT * FROM imports WHERE path=?",(key,)).fetchone()
        state = json.loads(saved["state"]) if saved else {"step":0,"calls":{},"pending":[],"reset":None}
        if state.get("expired"):
            return state["eid"]
        offset = saved["offset"] if saved else 0
        if path.stat().st_size < offset:
            raise ValueError("Source truncated; select a new attempt identity")
        stat = path.stat()
        signature = [stat.st_dev,stat.st_ino]
        if state.get("file_identity") not in (None,signature):
            raise ValueError("Source replaced; refusing cursor reuse")
        state["file_identity"] = signature
        with path.open("rb") as f:
            f.seek(offset)
            processed=0
            began=time.monotonic()
            while True:
                if (max_events and processed>=max_events) or (max_seconds and processed and time.monotonic()-began>=max_seconds):
                    break
                start = f.tell()
                line = f.readline()
                if not line or not line.endswith(b"\n"):
                    break
                try:
                    event = json.loads(line)
                except (ValueError,UnicodeError) as exc:
                    raise ValueError(f"Invalid complete JSON line at byte {start}") from exc
                if self.adapter == "case1":
                    self._case1(state,event,path,encode_now)
                else:
                    self._generic(state,event,encode_now,path)
                offset = f.tell()
                with self.store.db() as db:
                    db.execute("INSERT INTO imports VALUES (?,?,?) ON CONFLICT(path) DO UPDATE SET offset=excluded.offset,state=excluded.state",(key,offset,dumps(state)))
                processed+=1
        if state.get("eid") and self.adapter=="case1":
            result_path=confined(self.root,path.parent/"result.json")
            if result_path.is_file():
                native=json.loads(result_path.read_text(encoding='utf-8'))
                row=self.store.get(state["eid"])
                if not row["meta"].get("source_completed"):
                    meta=dict(row["meta"],source_completed=True,source_total_steps=native.get("primitive_steps"))
                    with self.store.db() as db:
                        db.execute("UPDATE episodes SET meta=?,updated=? WHERE id=?",(dumps(meta),self.store.clock(),state["eid"]))
        return state.get("eid")

    def _ensure(self,state,path,prompt=""):
        if state.get("eid"):
            return
        reset = state["reset"]
        key = reset["task_key"].split("|")
        instruction,source = instruction_from_prompt(prompt) if prompt else ("", "not_recorded")
        # Bind attempt and shard so retries never collide.
        rel = path.relative_to(self.root)
        attempt = "/".join(rel.parts[:3])
        meta = {"schema_version":1,"run_id":self.run_id,"attempt":attempt,"model":self.model,"stage":self.stage,"dataset":key[0].upper(),"split":key[1],"language":key[2],"scene_id":Path(key[3]).stem,"episode_id":reset["episode_id"],"instruction_id":reset.get("instruction_id"),"instruction":instruction,"instruction_source":source,"started_at":self.started_at or datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),"source_trace":str(rel),"source_run":str(self.root),"source_kind":"case1_trace"}
        meta["timestamp_source"]="run_manifest_or_explicit" if self.started_at else "trace_mtime_fallback"
        meta["provenance"]={k:self.manifest[k] for k in ("model_sha256","source_sha256","config_sha256","benchmark_sha256","navmesh_sha256") if k in self.manifest}
        state["eid"] = self.engine.start(meta)
        for item in state["pending"]:
            self._step(state["eid"],item)
        state["pending"] = []

    def _case1(self,state,event,path,encode_now):
        kind = event["kind"]
        if kind == "reset":
            state["reset"] = event
            state["pending"] = [{"step":0,"action":"RESET","pose":event["state_evaluator_only"],"collided":False}]
        elif kind in ("model_input","model_call"):
            self._ensure(state,path,event.get("prompt",""))
            if self.store.get(state["eid"])["status"] != "live":
                return
            cid = str(event["call_id"])
            state["calls"][cid] = {"call_id":event["call_id"],"raw_output":event.get("raw_decoded_output",""),"model_frame":event.get("frame")}
            # The latest image in this input is the observation BEFORE the next action.
            if kind == "model_input":
                steps = self.store.steps(state["eid"])
                s = next((v for v in steps if v["step"] == state["step"]), None)
                if s is not None:
                    # This call OBSERVES state n; it did not execute action n.
                    s["observation_call_id"]=event["call_id"]
                    s["model_frame"]=event.get("frame")
                    refs = []
                    for original in event.get('images',[]):
                        ref=dict(original)
                        image=confined(self.root,self.root/ref['path'])
                        if not image.is_file():
                            local=confined(self.root,path.parent/ref['path'])
                            if local.is_file():ref=dict(ref,path=str(local.relative_to(self.root)),original_path=original['path'])
                        refs.append(ref)
                    s["rgb_source"] = refs[-1] if refs else None
                    self._step(state["eid"],s,refs[-1] if refs else None)
        elif kind == "primitive_action":
            n = int(event["step"])
            if n <= state["step"]:
                raise ValueError("Non-monotonic primitive step")
            state["step"] = n
            s = {"step":n,"action":{0:"STOP",1:"FORWARD",2:"LEFT",3:"RIGHT"}.get(event["action"],str(event["action"])),"pose":event["after_evaluator_only"],"collided":event["after_evaluator_only"].get("collided"),"call_id":event.get("last_call_id"),"stop_source":event.get("stop_source"),"timestamp_source":"import_time"}
            s.update(state["calls"].get(str(event.get("last_call_id")),{}))
            if state.get("eid"):
                if self.store.get(state["eid"])["status"] == "live":
                    self._step(state["eid"],s)
            else:
                state["pending"].append(s)
        elif kind == "terminal":
            self._ensure(state,path)
            native = event.get("native_metrics",{})
            success = native.get("success")
            result = {"status":"completed","total_steps":int(event["steps"]),"termination_reason":event.get("termination_reason"),"success":bool(success) if success in (0,1,False,True) else None,"metrics":native,"model_calls":event.get("model_calls"),"metrics_source":"existing terminal trace; not independently re-audited"}
            self.engine.end(state["eid"],result,encode_now)

    def _generic(self,state,event,encode_now,path=None):
        if event.get("schema_version") != 1:
            raise ValueError("Generic event requires schema_version=1")
        kind = event["type"]
        if kind == "start":
            metadata=dict(event['metadata'],source_run=str(self.root),source_kind='generic_jsonl')
            if path:metadata['source_trace']=str(path.relative_to(self.root))
            state["eid"] = self.engine.start(metadata)
        elif kind == "step":
            s = dict(event["data"])
            self.engine.step(state["eid"],s,open_asset(self.root,s.get("rgb_path")),open_asset(self.root,s.get("map_path")),render_preview=not self.lazy)
        elif kind == "end":
            self.engine.end(state["eid"],event["result"],encode_now)
        else:
            raise ValueError("Unknown generic event type")
