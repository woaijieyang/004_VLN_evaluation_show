"""Discover immediate project output runs; selecting a catalog ID never accepts a path."""
import argparse,hashlib,heapq,json,sqlite3,sys,time,os
from datetime import datetime,timezone,timedelta
from pathlib import Path
from contextlib import closing
plugin=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(plugin))
from process_show.core import confined,atomic_json
from process_show.scope import Scope
from process_show.capture.adapters import generic_metadata
from process_show.service.task_groups import aggregate as aggregate_tasks

def read_json(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}


def started_epoch(value,fallback):
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:return fallback
        return parsed.timestamp()
    except (AttributeError,TypeError,ValueError):return fallback


def display_time(epoch):return datetime.fromtimestamp(epoch,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def quick_roots(project,output,known,seen,limit=256):
    """Refresh known evaluation families without waiting for unrelated history."""
    project=confined(project,project);output=confined(project,output)
    excluded={'.git','.shared-ro','.venv','.rebuild','.cache','__pycache__','node_modules','runtime','data','datasets','models','checkpoints','backup','docs','shards','images','rgb','previews','_qa','_maps','src','tests','scripts','build','dist','assets','frames','observations','episodes','model_calls'}
    roots={confined(project,Path(p).absolute()) for p in known if Path(p).absolute()!=output}
    ancestors={project}
    for p in roots:
        ancestors.update(a for a in p.parents if a==project or project in a.parents)
    pending=[]
    for p in ancestors:
        try:heapq.heappush(pending,(-p.stat().st_mtime,str(p)))
        except OSError:pass
    processed=0;queued={p for _,p in pending}
    while pending and processed<limit:
        _,name=heapq.heappop(pending);p=confined(project,Path(name));processed+=1
        if p==output or p==project/'process-show':continue
        try:
            stamp=p.stat().st_mtime_ns
            if p not in ancestors and seen.get(name)==stamp:continue
            seen[name]=stamp;children=list(p.iterdir())
            if (p/'run_manifest.json').is_file() or any(c.name.endswith('.jsonl') and c.is_file() and not c.is_symlink() and generic_metadata(c) for c in children):roots.add(p)
            for c in children:
                if c.name in excluded or c.is_symlink() or not c.is_dir() or c==output or c==project/'process-show':continue
                try:
                    confined(project,c);mtime=c.stat().st_mtime
                    if str(c) not in queued:heapq.heappush(pending,(-mtime,str(c)));queued.add(str(c))
                except (OSError,ValueError):pass
        except OSError:continue
    return sorted(roots)

def run_folders(project,output):
    project=confined(project,project);output=confined(project,output)
    excluded={'.git','.shared-ro','.venv','.rebuild','.cache','__pycache__','node_modules','runtime','data','datasets','models','checkpoints','upstream','backup','docs','shards','images','rgb','previews','_qa','_maps','desktop','src','tests','scripts','build','dist','configs','patches','results-show','logs','assets','frames','observations','episodes','model_calls'}
    def inside(candidate):
        try:confined(project,candidate);return True
        except ValueError:return False
    for folder,dirs,files in os.walk(project,followlinks=False):
        path=Path(folder)
        dirs[:]=[name for name in dirs if name not in excluded and inside(path/name)
                 and path/name!=output and path/name!=project/'process-show']
        def freshness(name):
            try:return (path/name).stat().st_mtime
            except OSError:return 0
        dirs.sort(key=freshness,reverse=True)
        if 'run_manifest.json' in files:
            yield path
        elif any(name.endswith('.jsonl') and generic_metadata(path/name) for name in files):
            yield path


def generic_runs(root,manifest,ttl,retained,now):
    groups={}
    for path in root.glob('*.jsonl'):
        if path.is_symlink():continue
        confined(root,path)
        meta=generic_metadata(path)
        if not meta:continue
        ended=False
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-65536));tail=stream.read()
        if tail.endswith(b'\n'):
            try:
                last=json.loads(tail.splitlines()[-1]);ended=last.get('type')=='end' and last.get('schema_version')==1
            except (ValueError,IndexError,AttributeError):pass
        groups.setdefault((meta['run_id'],meta['model']),[]).append((meta,path.stat().st_mtime,ended))
    entries=[];expired=0
    for (run_id,model),records in groups.items():
        meta=records[0][0];completed=sum(r[2] for r in records)
        expires=max(r[1] for r in records)+ttl if completed==len(records) else None
        keep=str(root) in retained
        if expires and expires<=now and not keep:expired+=1;continue
        started=meta.get('started_at',manifest.get('created_at'))
        try:date=datetime.fromisoformat(started.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
        except (AttributeError,ValueError,TypeError):date=datetime.fromtimestamp(min(r[1] for r in records),timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
        epoch=started_epoch(started,min(r[1] for r in records))
        entries.append({'id':hashlib.sha256((str(root)+'|generic:'+run_id+'|'+model).encode()).hexdigest()[:20],
            'run_id':run_id,'run_root':str(root),'date':date,'model':model,'source_model':model,
            'model_sha':str(manifest.get('model_sha256',''))[:10],
            'dataset':','.join(sorted({r[0].get('dataset','未知') for r in records})),
            'completed':completed,'expected':manifest.get('expected_tasks') if isinstance(manifest.get('expected_tasks'),int) else None,
            'status':'已记录结束（总任务数未知）' if completed==len(records) else '采集中记录（进程状态未知）',
            'expires_at':expires,'retained':keep,'stage':meta.get('stage','evaluation'),'adapter':'generic','sort_time':epoch})
    return entries,expired,set(groups)

def discover(project,output,now=None,on_update=None,roots=None):
    project=Path(project).absolute();output=Path(output).absolute();now=time.time() if now is None else now
    ttl=3600;retained=set();source_models={};source_stages={};source_ids={};source_kinds={};collected={}
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
                    source_ids.setdefault(value['source_run'],value['run_id'])
                    source_kinds.setdefault(value['source_run'],set()).add(value.get('source_kind'))
            for row in db.execute('SELECT meta,status,result,ready,created,pinned,selected,deleted,updated FROM episodes'):
                value=json.loads(row[0])
                collected.setdefault((value.get('source_run'),value['run_id'],value['model']),[]).append((value,*row[1:]))
    entries=[];expired=0;errors=[];trace_groups=set()
    confined(project,project)
    for folder in (run_folders(project,output) if roots is None else roots):
        if folder==output or folder.is_symlink() or not folder.is_dir():continue
        try:
            root=confined(project,folder);manifest_path=confined(project,root/'run_manifest.json')
            manifest=read_json(manifest_path)
            # Only the supported trace format can be selected. Scores alone are not replay.
            trace=next(root.glob('shards/shard-*/attempt-*/episodes/*/trace.jsonl'),None)
            layout='shards'
            if trace is None:
                trace=next(root.glob('episodes/*/trace.jsonl'),None);layout='episodes'
                if trace and 'live_sensor' in source_kinds.get(str(root),set()) and 'case1_trace' not in source_kinds.get(str(root),set()):continue
            if trace is None:
                generic,count,groups=generic_runs(root,manifest,ttl,retained,now)
                entries.extend(generic);expired+=count
                trace_groups.update((str(root),run_id,model) for run_id,model in groups)
                if generic and on_update:on_update({'runs':aggregate_tasks(project,list(entries),now),'expired_runs':expired,'ttl_seconds':ttl,'errors':list(errors),'scanned_at':now})
                continue
            confined(root,trace)
            receipt_path=confined(project,root/'model-receipt.json')
            receipt=read_json(receipt_path)
            model=manifest.get('model') or receipt.get('model_class') or '未提供模型名称'
            if isinstance(model,dict):model=model.get('name') or model.get('id') or '模型名称未知'
            run_id=source_ids.get(str(root),manifest.get('run_id',root.name))
            trace_groups.add((str(root),run_id,source_models.get(str(root),str(model))))
            progress_path=confined(project,root/'progress.json');progress=read_json(progress_path)
            completed=progress.get('completed',0);expected=progress.get('expected',manifest.get('expected_tasks'))
            if layout=='episodes' and not progress_path.is_file():completed=sum((p.parent/'result.json').is_file() for p in root.glob('episodes/*/trace.jsonl'))
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
            datasets=manifest.get('datasets',manifest.get('dataset',[]))
            if isinstance(datasets,dict):datasets=list(datasets)
            if isinstance(datasets,str):datasets=[datasets]
            if not isinstance(datasets,list):datasets=[]
            label=','.join(str(d.get('name',d.get('id','未知'))) if isinstance(d,dict) else str(d) for d in datasets) or '数据集未标注'
            epoch=started_epoch(created,manifest_path.stat().st_mtime)
            entries.append({'id':hashlib.sha256(str(root).encode()).hexdigest()[:20],'run_id':run_id,'run_root':str(root),
                            'date':date,'model':str(model),'model_sha':str(manifest.get('model_sha256',''))[:10],
                            'dataset':label,'source_model':source_models.get(str(root),str(model)),'completed':completed,'expected':expected,
                            'status':'已完成' if finished else '运行中（进度记录）' if active else '状态未知',
                            'expires_at':expires,'retained':keep,'stage':source_stages.get(str(root),manifest.get('stage','evaluation')),
                            'sort_time':epoch,'trace_layout':layout})
            if on_update:on_update({'runs':aggregate_tasks(project,list(entries),now),'expired_runs':expired,'ttl_seconds':ttl,'errors':list(errors),'scanned_at':now})
        except (OSError,ValueError) as exc:errors.append({'run':folder.name,'error':str(exc)})
    for (source_run,run_id,model),rows in collected.items():
        if source_run:
            try:confined(project,Path(source_run).absolute())
            except ValueError:continue
            if (source_run,run_id,model) in trace_groups:continue
        alive=[r for r in rows if r[7] is None]
        if not alive:expired+=1;continue
        keep=any(r[5] or r[6] for r in alive)
        active=any(r[1]=='live' for r in alive)
        unfinished=any(r[3] is None for r in alive)
        expires=None if active or unfinished else max(r[3] for r in alive)+ttl
        if expires is not None and expires<=now and not keep:expired+=1;continue
        meta=alive[0][0]
        epoch=min(started_epoch(r[0].get('started_at'),r[4]) for r in rows)
        identity=str(output)+'|catalog:'+run_id+'|'+model
        if source_run:identity+='|source:'+source_run
        entries.append({'id':hashlib.sha256(identity.encode()).hexdigest()[:20],'run_id':run_id,'run_root':source_run or str(output),
                        'date':display_time(epoch),'model':meta['model'],'model_sha':str(meta.get('model_sha256',''))[:10],
                        'dataset':','.join(sorted({r[0]['dataset'] for r in alive})),'source_model':meta['model'],
                        'completed':sum(r[2] is not None for r in alive),'expected':None,
                        'status':'存在采集中记录' if active else '视频处理中' if unfinished else '已采集记录（总任务数未知）',
                        'expires_at':expires,'retained':keep,'stage':meta['stage'],'catalog_only':True,
                        'source_run':source_run,'last_update':max(r[8] for r in alive),'sort_time':epoch})
    for entry in entries:entry['display_name']='/'.join(Path(entry['run_root']).parts[-3:])
    return {'runs':aggregate_tasks(project,entries,now),'expired_runs':expired,'ttl_seconds':ttl,'errors':errors,'scanned_at':now}

def select(scope,selection,persist_watch=True,catalog=None):
    catalog=read_json(scope.output/'run-catalog.json') if catalog is None else catalog
    if time.time()-catalog.get('scanned_at',0)>600:raise ValueError('运行列表已过期，请刷新列表')
    chosen=next((r for r in catalog.get('runs',[]) if r['id']==selection),None)
    if chosen is None:raise ValueError('请从当前项目的可选运行中选择')
    if chosen.get('expires_at') and chosen['expires_at']<=time.time() and not chosen.get('retained'):raise ValueError('运行已过缓存期限，请刷新列表')
    scope.source(chosen['run_root'])
    if not persist_watch:return chosen
    source={k:chosen[k] for k in ('run_root','stage','model')};source['model']=chosen.get('source_model',chosen['model']);source['include_history']=True
    source['adapter']=chosen.get('adapter','case1')
    config=scope.source(scope.project/scope.config['watch_config'])
    config.parent.mkdir(parents=True,exist_ok=True)
    existing=read_json(config)
    sources=existing.get('sources',[])
    if chosen.get('task_group'):
        for member in chosen['members']:
            if member.get('catalog_only'):continue
            watch={k:member[k] for k in ('run_root','stage','model')}
            watch.update(model=member.get('source_model',member['model']),adapter=member.get('adapter','case1'),include_history=True)
            sources=[s for s in sources if s.get('run_root')!=watch['run_root']]+[watch]
    elif not chosen.get('catalog_only'):
        sources=[s for s in sources if s.get('run_root')!=source['run_root']]+[source]
    atomic_json(config,dict(existing,sources=sources,selection={k:chosen[k] for k in ('id','run_id','date','model','dataset','completed','expected')}))
    return chosen

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['list','select']);parser.add_argument('id',nargs='?')
    args=parser.parse_args();scope=Scope.current()
    if args.action=='list':
        result=discover(scope.project,scope.output);scope.output.mkdir(parents=True,exist_ok=True)
        atomic_json(scope.output/'run-catalog.json',result)
    else:result=select(scope,args.id)
    print(json.dumps(result,ensure_ascii=False))
