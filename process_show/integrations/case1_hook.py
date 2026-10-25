"""Future-run opt-in observer. Never import into the active frozen evaluator."""
import os
import functools
import warnings
from pathlib import Path
from datetime import datetime,timezone
from process_show.capture.sdk import Recorder


def observational(method):
    @functools.wraps(method)
    def wrapped(self,*args,**kwargs):
        try:
            return method(self,*args,**kwargs)
        except Exception as exc:
            warnings.warn("process-show capture disabled: "+str(exc),RuntimeWarning)
            recorder=self.recorder
            self.recorder=None
            if recorder and recorder.eid:
                try:
                    recorder.store.error(recorder.eid,"Observer disabled: "+str(exc))
                    records=recorder.store.steps(recorder.eid)
                    recorder.end_episode({"status":"error","total_steps":max((s['step'] for s in records),default=0),"termination_reason":"visualization_error"})
                except Exception:
                    pass  # original warning remains in evaluator log; never mask navigation
    return wrapped


class Case1Observer:
    def __init__(self):
        root=os.environ.get("PROCESS_SHOW_OUTPUT")
        self.recorder=None
        try:
            from process_show.storage.scope import Scope
            self.scope=Scope.current()
            if not root and self.scope.config.get('enabled') and self.scope.config.get('auto_start_on_evaluation') and not self.scope.stopped_file.exists():root=str(self.scope.output)
            self.recorder=Recorder(root) if root else None
        except Exception as exc:
            warnings.warn("process-show unavailable: "+str(exc),RuntimeWarning)
        self.key=None

    @observational
    def before(self,ep,state,observation):
        if not self.recorder:return
        key=(str(ep.scene_id),str(ep.episode_id),getattr(ep.instruction,"instruction_id",None))
        if key!=self.key:
            if self.key is not None:raise RuntimeError("Missing terminal event")
            self.key=key
            run_root=os.environ.get('CASE1_RUN_ROOT')
            if not run_root and os.environ.get('CASE1_TRACE_ROOT'):run_root=str(Path(os.environ['CASE1_TRACE_ROOT']).parents[2])
            if not run_root:raise ValueError('Missing current evaluation run identity')
            self.scope.source(run_root)
            self.recorder.start_episode({"run_id":os.environ.get("PROCESS_SHOW_RUN_ID",Path(run_root).name),"attempt":os.environ.get("PROCESS_SHOW_ATTEMPT",os.environ.get("CASE1_TRACE_ROOT","attempt-01")),"model":os.environ.get("PROCESS_SHOW_MODEL","AwareVLN"),"stage":os.environ.get("PROCESS_SHOW_STAGE",self.scope.config.get('stage','evaluation')),"dataset":os.environ["CASE1_DATASET"].upper(),"split":"val_unseen","scene_id":Path(ep.scene_id).stem,"episode_id":str(ep.episode_id),"instruction_id":key[2],"instruction":ep.instruction.instruction_text,"started_at":os.environ.get("PROCESS_SHOW_STARTED_AT",datetime.now(timezone.utc).isoformat()),"source_kind":"live_sensor",'source_run':run_root,'goals':[{'position':list(g.position),'radius':getattr(g,'radius',None)} for g in ep.goals]})
            self.recorder.record_step({"step":0,"action":"RESET","pose":state,"collided":False},observation.get("rgb"))

    @observational
    def after(self,action,state,outcome,call_id):
        if not self.recorder:return
        observation,_,done,info=outcome
        self.recorder.record_step({"step":int(state["elapsed_steps"]),"action":{0:"STOP",1:"FORWARD",2:"LEFT",3:"RIGHT"}.get(action,str(action)),"pose":state,"collided":state.get("collided"),"call_id":call_id},observation.get("rgb"))
        if done:
            success=info.get("success")
            self.recorder.end_episode({"total_steps":int(state["elapsed_steps"]),"status":"completed","success":bool(success) if success in (0,1,False,True) else None,"metrics":{k:v for k,v in info.items() if isinstance(v,(bool,int,float))},"termination_reason":"stop" if action==0 else "environment_terminal"})
            self.key=None

    @observational
    def close(self):
        if self.recorder:self.recorder.close()
