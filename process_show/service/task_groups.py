"""Logical evaluation tasks own ports; shards are internal source members."""
import hashlib,json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from process_show.storage.core import confined


def read(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def boundary(project,source):
    project=confined(Path(project).absolute(),Path(project).absolute()).resolve();source=confined(project,Path(source).absolute())
    for folder in (source,*source.parents):
        if folder==project:break
        if project not in folder.resolve().parents:break
        marker=confined(project,folder/'process-show-task.json')
        scope=confined(project,folder/'scope-manifest.json')
        registry=confined(project,folder/'job-registry.json')
        if marker.is_file() or (scope.is_file() and registry.is_file()):
            return folder,read(marker) if marker.is_file() else read(scope)
    return None,None


def aggregate(project,entries,now):
    grouped={};plain=[]
    def leaves(rows):
        for row in rows:
            if row.get('task_group'):yield from leaves(row['members'])
            else:yield row
    for row in leaves(entries):
        folder,metadata=boundary(project,row['run_root'])
        if folder is None:plain.append(row);continue
        grouped.setdefault(str(folder),(metadata,[]))[1].append(row)
    for name,(metadata,members) in grouped.items():
        folder=Path(name);progress=read(confined(project,folder/'progress.json'))
        explicit=metadata.get('id',metadata.get('task_id',metadata.get('run_id',folder.name)))
        identity=hashlib.sha256((str(Path(project).resolve())+'|task|'+str(folder)+'|'+str(explicit)).encode()).hexdigest()[:20]
        expected=progress.get('expected',metadata.get('expected',metadata.get('expected_tasks')))
        if not isinstance(expected,int):expected=None
        available=sum(r.get('completed',0) for r in members)
        reported=progress.get('completed',progress.get('raw_execution_completed'))
        completed=reported if isinstance(reported,int) else available
        native=str(progress.get('status','UNKNOWN')).upper()
        done=progress.get('full_dataset_complete') is True or native in ('COMPLETED','COMPLETE','DONE','FINISHED') or (not progress and expected is not None and completed>=expected)
        status='任务侧报告完成' if done else '任务侧报告进行中' if ('RUNNING' in native or 'IN_PROGRESS' in native or progress.get('running_shards')) else '任务状态待确认'
        keep=any(r.get('retained') for r in members)
        starts=[r.get('sort_time',0) for r in members]
        marker=folder/('process-show-task.json' if (folder/'process-show-task.json').is_file() else 'scope-manifest.json')
        epoch=marker.stat().st_mtime if marker.is_file() else min(starts,default=now)
        models=sorted({r['model'] for r in members})
        plain.append({'id':identity,'run_id':str(explicit),'display_name':str(metadata.get('name',folder.name)),
            'run_root':name,'task_root':name,'task_group':True,'members':members,
            'date':datetime.fromtimestamp(epoch,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'),'sort_time':epoch,'model':', '.join(models),'model_sha':'',
            'dataset':','.join(sorted({d for r in members for d in r.get('dataset','').split(',') if d})),
            'completed':completed,'expected':expected,'completion_source':'task_progress' if isinstance(reported,int) else 'source_records',
            'available_completed_records':available,'audited_completed':progress.get('independently_audited_tasks'),
            'task_progress_updated':(folder/'progress.json').stat().st_mtime if (folder/'progress.json').is_file() else None,
            'status':status,'stage':str(metadata.get('stage',members[0].get('stage','evaluation'))),
            'expires_at':max(r['expires_at'] for r in members) if done and all(r.get('expires_at') is not None for r in members) else None,
            'retained':keep,'last_update':max((r.get('last_update',r.get('sort_time',0)) for r in members),default=now)})
    return sorted(plain,key=lambda r:(r.get('sort_time',0),r['id']),reverse=True)
