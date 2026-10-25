"""Isolated local-service integration receipt; synthetic trace, no model or GPU."""
import hashlib,json,os,shutil,subprocess,sys,tempfile,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path
plugin=Path(__file__).resolve().parents[2];project=plugin.parent
sys.path.insert(0,str(plugin))
from process_show.core import confined
qa=confined(project,project/'outputs/process-show/_qa');qa.mkdir(parents=True,exist_ok=True)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with tempfile.TemporaryDirectory(prefix='local-service-',dir=qa) as temporary:
    root=Path(temporary).resolve();assert qa.resolve() in root.parents
    target=root/'process-show';target.mkdir()
    for name in ('tools','process_show'):
        shutil.copytree(plugin/name,target/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc','legacy-launchers'))
    (target/'config').mkdir()
    cfg={'project_id':'isolated-local-smoke','project_roots':{'windows':str(root),'posix':str(root),'macos':str(root)},'output':'outputs/process-show','watch_config':'process-show/config/sources.json','service_port':28771,'enabled':True,'auto_start_on_evaluation':True}
    (target/'config/service.json').write_text(json.dumps(cfg));(target/'config/sources.json').write_text('{"sources":[]}')
    run=root/'outputs/synthetic-local-run';trace=run/'shards/shard-0/attempt-1/episodes/one/trace.jsonl';trace.parent.mkdir(parents=True)
    profile=run/'nav.json';profile.write_text('{"assets":{}}')
    manifest={'created_at':datetime.now(timezone.utc).isoformat(),'model':'SYNTHETIC LOCAL ENGINEERING TEST','datasets':['R2R'],'expected_tasks':1,'navmesh_profile':str(profile),'navmesh_sha256':hashlib.sha256(profile.read_bytes()).hexdigest()}
    (run/'run_manifest.json').write_text(json.dumps(manifest));(run/'progress.json').write_text('{"status":"COMPLETED","completed":1,"expected":1}')
    pose={'position':[0,0,0],'rotation_xyzw':[0,0,0,1]}
    events=[{'kind':'reset','task_key':'r2r|test|en|mp3d/fixture/fixture.glb|1|-','episode_id':'one','state_evaluator_only':pose},
            {'kind':'model_input','call_id':1,'prompt':'Instruction: Synthetic fixture only.','images':[]},
            {'kind':'primitive_action','step':1,'action':0,'last_call_id':1,'after_evaluator_only':pose},
            {'kind':'terminal','steps':1,'native_metrics':{'success':0},'termination_reason':'stop'}]
    trace.write_text(''.join(json.dumps(e)+'\n' for e in events))
    controller=target/'tools/service.py'
    def service(action):return subprocess.check_output([sys.executable,'-B',str(controller),action],cwd=root,text=True,encoding='utf-8')
    try:
        started=json.loads(service('start'));base=started['url']
        def get(path):return json.load(opener.open(base+path,timeout=15))
        health=get('/api/health');assert health['project_root']==str(root)
        runs=get('/api/runs');deadline=time.monotonic()+15
        while runs.get('pending') and time.monotonic()<deadline:time.sleep(.1);runs=get('/api/runs')
        assert len(runs['runs'])==1,runs
        req=urllib.request.Request(base+'/api/runs/select',data=json.dumps({'id':runs['runs'][0]['id']}).encode(),headers={'Content-Type':'application/json'})
        selected=json.load(opener.open(req,timeout=15));assert selected['selected']['run_id']=='synthetic-local-run'
        deadline=time.monotonic()+15;rows=[]
        while time.monotonic()<deadline:
            rows=get('/api/episodes')
            if rows and rows[0]['status'] in ('ready','encode_error'):break
            time.sleep(.2)
        assert len(rows)==1,rows
        if rows[0]['status']!='ready':
            print(json.dumps({'status':rows[0]['status'],'error':rows[0].get('error'),'sync':get('/api/sync'),'service_log':(root/'outputs/process-show/service.log').read_text(errors='replace')[-6000:]}),flush=True)
        assert rows[0]['status']=='ready',rows[0].get('error')
        media=opener.open(base+'/api/episodes/'+rows[0]['id']+'/video',timeout=15).read()
        assert b'ftyp' in media[:32]
        before=started['pid'];service('stop');assert not json.loads(service('status'))['running']
        restarted=json.loads(service('start'));assert restarted['pid']!=before
        receipt={'platform':sys.platform,'synthetic_only':True,'directory_scope':True,'popup_catalog_count':1,'selected_run_only':True,'mp4_bytes':len(media),'stop_verified':True,'restart_verified':True,'service_port':restarted['remote_port']}
        (qa/('local-service-'+sys.platform+'.json')).write_text(json.dumps(receipt,indent=2),encoding='utf-8')
        print(json.dumps(receipt))
    except Exception:
        log=root/'outputs/process-show/service.log'
        if log.is_file():print(log.read_text(errors='replace')[-6000:],flush=True)
        raise
    finally:service('stop')
