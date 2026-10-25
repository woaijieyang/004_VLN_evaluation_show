"""Viewer-only start/goal anchors; never feed evaluator truth into a model."""
import gzip,hashlib,json,math
from pathlib import Path
from functools import lru_cache
from process_show.storage.core import confined


def position(value):
    if isinstance(value,(list,tuple)) and len(value)==3 and all(isinstance(x,(int,float)) and math.isfinite(x) for x in value):
        return list(value)
    return None


@lru_cache(maxsize=8)
def annotation_index(path,expected_sha,signature=None):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha:raise ValueError('Annotation identity changed')
    data=json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)
    index={}
    for episode in data['episodes']:
        index.setdefault((str(episode['episode_id']),Path(episode['scene_id']).stem),[]).append(episode)
    return index


def resolve_anchors(meta,steps,case_root=None):
    start=next((position(s.get('pose',{}).get('position')) for s in steps if s['step']==0),None)
    start=start or position(meta.get('start_position'))
    result={'start':start,'goals':[],'goal_source':None,'goal_status':'未提供目标位置'}
    if 'goals' in meta or 'goal_position' in meta:
        entries=meta.get('goals',[{'position':meta.get('goal_position')}]) or []
        for goal in entries:
            if not isinstance(goal,dict):continue
            p=position(goal.get('position'))
            if p is not None:result['goals'].append({'position':p,'radius':goal.get('radius')})
        result.update(goal_source='adapter_metadata',goal_status='已提供' if result['goals'] else '未提供目标位置')
        return result
    if meta.get('source_kind')!='case1_trace':return result
    from process_show.storage.scope import Scope
    case=Path(case_root) if case_root else Scope.current().project
    try:
        spec_path=confined(case,Path(meta['source_run'])/'dataset-spec.json')
        spec=json.loads(spec_path.read_text(encoding='utf-8'))
        original=Path(spec['annotation'])
        if original.is_relative_to(case):candidate=original
        else:candidate=case/'data/.shared-ro'/original.relative_to('/data/zy/vln/data')
        path=confined(case,candidate.resolve(strict=True))
        stat=path.stat()
        records=annotation_index(str(path),spec['annotation_sha256'],(stat.st_size,stat.st_mtime_ns)).get((str(meta['episode_id']),meta['scene_id']),[])
        instruction_id=meta.get('instruction_id')
        if instruction_id is not None:
            records=[r for r in records if str(r.get('instruction',{}).get('instruction_id'))==str(instruction_id)]
        if len(records)!=1:raise ValueError('Episode/scene/instruction identity is not unique')
        record=records[0]
        actual_text=' '.join(meta.get('instruction','').split())
        expected_text=' '.join(record.get('instruction',{}).get('instruction_text','').split())
        if actual_text and actual_text!=expected_text:raise ValueError('Instruction identity mismatch')
        declared_start=position(record.get('start_position'))
        if start and declared_start and max(abs(a-b) for a,b in zip(start,declared_start))>1e-3:
            raise ValueError('Reset position differs from annotation')
        for goal in record.get('goals',[]):
            p=position(goal.get('position'))
            if p is not None:result['goals'].append({'position':p,'radius':goal.get('radius')})
        result.update(goal_source='verified_evaluation_annotation',annotation_sha256=spec['annotation_sha256'],goal_status='已核验' if result['goals'] else '标注中没有目标')
    except (OSError,ValueError,KeyError,TypeError) as exc:
        result.update(goals=[],goal_status='目标未核验：'+str(exc))
    return result
