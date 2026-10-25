"""Discover immediate project output runs; selecting a catalog ID never accepts a path."""
import argparse,hashlib,json,sqlite3,sys,time,os
from datetime import datetime,timezone,timedelta
from pathlib import Path
from contextlib import closing
plugin=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(plugin))
from process_show.core import confined,atomic_json
from process_show.scope import Scope

def read_json(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}

def run_folders(project,output):
    project=confined(project,project);output=confined(project,output)
    excluded={'.git','.shared-ro','.venv','.rebuild','.cache','__pycache__','node_modules','runtime','data','datasets','models','checkpoints','upstream','backup','docs','shards','images','rgb','previews','_qa','_maps','desktop','src','tests','scripts','build','dist','configs','oracleAdapter','patches','results-show','logs','assets','frames','observations','episodes','model_calls'}
    def inside(candidate):
        try:confined(project,candidate);return True
        except ValueError:return False
    for folder,dirs,files in os.walk(project,followlinks=False):
        path=Path(folder)
        dirs[:]=[name for name in dirs if name not in excluded and inside(path/name)
                 and path/name!=output and path/name!=project/'process-show']
        if 'run_manifest.json' in files:
            dirs[:]=[]
            yield path

def discover(project,output,now=None):
    project=Path(project).absolute();output=Path(output).absolute();now=time.time() if now is None else now
    ttl=3600;retained=set();source_models={};source_stages={};collected={}
    catalog=output/'catalog.sqlite3'
    if catalog.is_file():
        with closing(sqlite3.connect(str(catalog))) as db:
            db.execute('PRAGMA query_only=ON')
            row=db.execute("SELECT value FROM settings WHERE key='policy'").fetchone()
            if row:ttl=json.loads(row[0]).get('ttl_seconds',3600)
            for meta in db.execute('SELECT meta FROM episodes WHERE deleted IS NULL AND (pinned=1 OR selected=1)'):
                retained.add(json.loads(meta[0]).get('source_run'))
            for meta in db.execute('SELECT meta FROM episodes'):
                value=json.loads(meta[0])
                if value.get('source_run'):
                    source_models.setdefault(value['source_run'],value['model'])
                    source_stages.setdefault(value['source_run'],value['stage'])
            for row in db.execute('SELECT meta,status,result,ready,created,pinned,selected,deleted FROM episodes'):
                value=json.loads(row[0])
                if not value.get('source_run'):
                    collected.setdefault((value['run_id'],value['model']),[]).append((value,*row[1:]))
    entries=[];expired=0;errors=[]
    confined(project,project)
    for folder in run_folders(project,output):
        if folder==output or folder.is_symlink() or not folder.is_dir():continue
        try:
            root=confined(project,folder);manifest_path=confined(project,root/'run_manifest.json')
            if not manifest_path.is_file():continue
            manifest=read_json(manifest_path)
            # Only the supported trace format can be selected. Scores alone are not replay.
            trace=next(root.glob('shards/shard-*/attempt-*/episodes/*/trace.jsonl'),None)
            if trace is None:continue
            confined(root,trace)
            progress_path=confined(project,root/'progress.json');progress=read_json(progress_path)
            completed=progress.get('completed',0);expected=progress.get('expected',manifest.get('expected_tasks'))
            if isinstance(expected,list):expected=len(expected)
            if not isinstance(completed,int):completed=0
            if not isinstance(expected,int):expected=None
            native_status=str(progress.get('status',manifest.get('status','UNKNOWN'))).upper()
            finished=native_status in ('COMPLETED','COMPLETE','FINISHED','DONE','SUCCESS') or bool(expected and completed>=expected)
            active=native_status in ('RUNNING','ACTIVE','IN_PROGRESS') and not finished
            updated=(progress_path if progress_path.is_file() else manifest_path).stat().st_mtime
            expires=updated+ttl if not active else None
            keep=str(root) in retained
            if expires is not None and expires<=now and not keep:expired+=1;continue
            created=manifest.get('created_at')
            try:date=datetime.fromisoformat(created.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
            except (ValueError,TypeError,AttributeError):date=datetime.fromtimestamp(manifest_path.stat().st_mtime,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
            model=manifest.get('model','未提供模型名称')
            if isinstance(model,dict):model=model.get('name') or model.get('id') or '模型名称未知'
            datasets=manifest.get('datasets',manifest.get('dataset',[]))
            if isinstance(datasets,dict):datasets=list(datasets)
            if isinstance(datasets,str):datasets=[datasets]
            if not isinstance(datasets,list):datasets=[]
            label=','.join(str(d.get('name',d.get('id','未知'))) if isinstance(d,dict) else str(d) for d in datasets) or '数据集未标注'
            entries.append({'id':hashlib.sha256(str(root).encode()).hexdigest()[:20],'run_id':root.name,'run_root':str(root),
                            'date':date,'model':str(model),'model_sha':str(manifest.get('model_sha256',''))[:10],
                            'dataset':label,'source_model':source_models.get(str(root),str(model)),'completed':completed,'expected':expected,
                            'status':'已完成' if finished else '运行中（进度记录）' if active else '状态未知',
                            'expires_at':expires,'retained':keep,'stage':source_stages.get(str(root),manifest.get('stage','evaluation'))})
        except (OSError,ValueError) as exc:errors.append({'run':folder.name,'error':str(exc)})
    for (run_id,model),rows in collected.items():
        alive=[r for r in rows if r[7] is None]
        if not alive:expired+=1;continue
        keep=any(r[5] or r[6] for r in alive)
        active=any(r[1]=='live' for r in alive)
        unfinished=any(r[3] is None for r in alive)
        expires=None if active or unfinished else max(r[3] for r in alive)+ttl
        if expires is not None and expires<=now and not keep:expired+=1;continue
        meta=alive[0][0]
        entries.append({'id':hashlib.sha256((str(output)+'|catalog:'+run_id+'|'+model).encode()).hexdigest()[:20],'run_id':run_id,'run_root':str(output),
                        'date':meta['date'],'model':meta['model'],'model_sha':str(meta.get('model_sha256',''))[:10],
                        'dataset':','.join(sorted({r[0]['dataset'] for r in alive})),'source_model':meta['model'],
                        'completed':sum(r[2] is not None for r in alive),'expected':None,
                        'status':'存在采集中记录' if active else '视频处理中' if unfinished else '已采集记录（总任务数未知）',
                        'expires_at':expires,'retained':keep,'stage':meta['stage'],'catalog_only':True})
    return {'runs':sorted(entries,key=lambda r:r['date'],reverse=True),'expired_runs':expired,'ttl_seconds':ttl,'errors':errors,'scanned_at':now}

def select(scope,selection,persist_watch=True):
    catalog=read_json(scope.output/'run-catalog.json')
    if time.time()-catalog.get('scanned_at',0)>600:raise ValueError('运行列表已过期，请刷新列表')
    chosen=next((r for r in catalog.get('runs',[]) if r['id']==selection),None)
    if chosen is None:raise ValueError('请从当前项目的可选运行中选择')
    if chosen.get('expires_at') and chosen['expires_at']<=time.time() and not chosen.get('retained'):raise ValueError('运行已过缓存期限，请刷新列表')
    scope.source(chosen['run_root'])
    if not persist_watch:return chosen
    source={k:chosen[k] for k in ('run_root','stage','model')};source['model']=chosen.get('source_model',chosen['model']);source['include_history']=True
    config=scope.source(scope.project/scope.config['watch_config'])
    config.parent.mkdir(parents=True,exist_ok=True)
    atomic_json(config,{'sources':[source],'selection':{k:chosen[k] for k in ('id','run_id','date','model','dataset','completed','expected')}})
    return chosen

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['list','select']);parser.add_argument('id',nargs='?')
    args=parser.parse_args();scope=Scope.current()
    if args.action=='list':
        result=discover(scope.project,scope.output);scope.output.mkdir(parents=True,exist_ok=True)
        atomic_json(scope.output/'run-catalog.json',result)
    else:result=select(scope,args.id)
    print(json.dumps(result,ensure_ascii=False))
