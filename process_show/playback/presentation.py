"""Resolve recorded observations independently from the exported composite video."""
import io
from PIL import Image
from process_show.storage.core import confined
from process_show.capture.engine import open_asset


def observation_step(steps,number,strict=False):
    matches=[s for s in steps if s['step']<=number and s.get('rgb_available')]
    if strict:matches=[s for s in matches if s['step']==number]
    return max(matches,key=lambda s:s['step']) if matches else None


def observation_image(store,eid,number,strict=False):
    steps=store.steps(eid)
    source=observation_step(steps,number,strict)
    if source is None:return None,None
    row=store.get(eid)
    if source.get('observation_asset'):
        with Image.open(store.path(eid,source['observation_asset'])) as im:
            return im.convert('RGB').copy(),source['step']
    if source.get('rgb_source'):
        from process_show.storage.scope import Scope
        Scope.current().source(row['meta']['source_run'])
        return open_asset(row['meta']['source_run'],source['rgb_source']['path']),source['step']
    # Legacy SDK data: recover the RGB area from its recorded composite, once per cached observation.
    from process_show.media.render import extract_frame
    preview=store.path(eid,f"previews/{source['step']:07d}-000.webp")
    if preview.exists():
        with Image.open(preview) as im:canvas=im.convert('RGB').copy()
    else:canvas=Image.open(io.BytesIO(extract_frame(store,eid,source['step']))).convert('RGB')
    return canvas.crop((58,155,1152,710)),source['step']


def replay_payload(store,eid):
    row=store.get(eid)
    records=store.steps(eid)
    output=[];last=None;last_map=None
    for s in records:
        if s.get('rgb_available'):last=s['step']
        if s.get('map_asset'):last_map=s['map_asset']
        item={k:s.get(k) for k in ('step','action','pose','collided','call_id','observation_call_id','stop_source','floor_id','gap','rgb_available','raw_output')}
        item['observation_step']=last
        item['map_asset']=last_map
        output.append(item)
    from process_show.playback.anchors import resolve_anchors
    return {'episode':row,'steps':output,'last_step':max((s['step'] for s in records),default=0),'presentation_version':2,'anchors':resolve_anchors(row['meta'],records)}


def provided_map(store,eid,number):
    row=store.get(eid)
    candidates=[s for s in store.steps(eid) if s['step']<=number and s.get('map_asset')]
    if not candidates:return None
    step=max(candidates,key=lambda s:s['step'])
    transform=step.get('map_transform') or row['meta'].get('map_transform')
    if not transform:return {'available':False,'reason':'提供了地图，但缺少坐标变换'}
    (a,b,c),(d,e,f)=transform;det=a*e-b*d
    if abs(det)<1e-10:raise ValueError('Singular map transform')
    with Image.open(store.path(eid,step['map_asset'])) as im:w,h=im.size
    points=[((e*(u-c)-b*(v-f))/det,(-d*(u-c)+a*(v-f))/det) for u,v in ((0,0),(w,0),(0,h),(w,h))]
    return {'available':True,'width':w,'height':h,'map_transform':transform,
            'bounds':[min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)],
            'plane_height':step.get('pose',{}).get('position',[0,0,0])[1],
            'image_url':f'/api/episodes/{eid}/map-image?step={step["step"]}',
            'description':'接入方提供的地图','mode':'provided','source_step':step['step']}
