"""Optional CPU-only navmesh rasterization. Never creates a Habitat Simulator."""
from collections import OrderedDict
import hashlib
import json
import math
from pathlib import Path
import threading
import time

from process_show.storage.core import confined, atomic_json


class SceneMaps:
    def __init__(self, output_root, case_root=None):
        self.root=Path(output_root)/"_maps"
        self.case=Path(case_root) if case_root else Path(__file__).resolve().parents[3]
        self.lock=threading.Lock()
        self.finders=OrderedDict()

    def source_path(self,value):
        # Existing case data views may name a case-owned read-only mount through
        # an alias. Access ONLY the canonical in-case target, never an external target.
        candidate=Path(value).absolute()
        candidate.relative_to(self.case)
        return confined(self.case,candidate.resolve(strict=True))

    def asset(self,meta):
        source=meta.get('source_run')
        if not source:return None
        manifest_path=confined(self.case,Path(source)/'run_manifest.json')
        if not manifest_path.is_file():return None
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        # A generic model manifest is not necessarily a Habitat map profile.
        # Missing optional map capabilities must not prevent replay or MP4 export.
        if not isinstance(manifest.get('navmesh_profile'),str) or not isinstance(manifest.get('navmesh_sha256'),str):return None
        profile_path=confined(self.case,Path(manifest['navmesh_profile']))
        if hashlib.sha256(profile_path.read_bytes()).hexdigest()!=manifest['navmesh_sha256']:
            raise ValueError('Navmesh profile identity changed')
        profile=json.loads(profile_path.read_text(encoding='utf-8'))
        key=f"mp3d/{meta['scene_id']}/{meta['scene_id']}.glb"
        asset=profile['assets'].get(key)
        if not asset:return None
        path=self.source_path(asset['navmesh_path'])
        return path,asset['navmesh_sha256']

    def get(self,meta,height,cached_only=False):
        asset=self.asset(meta)
        if asset is None:return {'available':False,'reason':'未提供可验证的场景地图'}
        path,sha=asset
        plane=round(float(height)*4)/4
        if plane==0:plane=0.0
        if not math.isfinite(plane):raise ValueError('Non-finite map height')
        key=hashlib.sha256(f'{sha}|{plane:.2f}|0.05|map-v2'.encode()).hexdigest()
        # Atomic cache files can be read without waiting for unrelated scene generation.
        info_path=confined(self.root,self.root/(key+'.json'))
        image_path=confined(self.root,self.root/(key+'.webp'))
        if info_path.exists() and image_path.exists():return json.loads(info_path.read_text(encoding='utf-8'))
        if cached_only:return {'available':False,'uncached':True}
        with self.lock:
            self.root.mkdir(parents=True,exist_ok=True)
            info_path=confined(self.root,self.root/(key+'.json'))
            image_path=confined(self.root,self.root/(key+'.webp'))
            if info_path.exists() and image_path.exists():
                return json.loads(info_path.read_text(encoding='utf-8'))
            if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
                raise ValueError('Navmesh file identity changed')
            import numpy as np
            import habitat_sim
            from PIL import Image
            if sha not in self.finders:
                finder=habitat_sim.PathFinder()
                if not finder.load_nav_mesh(str(path)):raise ValueError('Navmesh load failed')
                self.finders[sha]=finder
                if len(self.finders)>4:self.finders.popitem(last=False)
            finder=self.finders[sha]
            low,high=finder.get_bounds()
            mpp=max(.05,float(max(high[0]-low[0],high[2]-low[2]))/1200)
            grid=np.asarray(finder.get_topdown_view(mpp,plane),dtype=bool)
            yy,xx=np.where(grid)
            if not len(xx):return {'available':False,'reason':f'高度 {plane:.2f}m 没有可通行区域'}
            x0,x1=max(0,int(xx.min())-16),min(grid.shape[1],int(xx.max())+17)
            z0,z1=max(0,int(yy.min())-16),min(grid.shape[0],int(yy.max())+17)
            grid=grid[z0:z1,x0:x1]
            padded=np.pad(grid,1)
            edge=grid & ~(padded[1:-1,:-2]&padded[1:-1,2:]&padded[:-2,1:-1]&padded[2:,1:-1])
            pixels=np.empty((*grid.shape,3),dtype=np.uint8);pixels[:]=[230,236,241]
            pixels[grid]=[253,254,255];pixels[edge]=[158,179,194]
            temp=image_path.with_suffix('.tmp')
            Image.fromarray(pixels).save(temp,'WEBP',lossless=True)
            temp.replace(image_path)
            minx=float(low[0])+x0*mpp;minz=float(low[2])+z0*mpp
            info={'available':True,'key':key,'width':grid.shape[1],'height':grid.shape[0],
                  'bounds':[minx,minz,minx+grid.shape[1]*mpp,minz+grid.shape[0]*mpp],
                  'map_transform':[[1/mpp,0,-minx/mpp],[0,1/mpp,-minz/mpp]],
                  'meters_per_pixel':mpp,'plane_height':plane,'height_tolerance':.5,
                  'navmesh_sha256':sha,'source':'verified evaluation navmesh / CPU PathFinder',
                  'image_url':'/api/maps/'+key+'.webp'}
            atomic_json(info_path,info)
            return info

    def scene(self,meta,height,cached_only=False):
        nav=self.get(meta,height,cached_only=cached_only)
        if not nav.get('available'):return nav
        manifest=json.loads(confined(self.case,Path(meta['source_run'])/'run_manifest.json').read_text(encoding='utf-8'))
        profile=json.loads(confined(self.case,Path(manifest['navmesh_profile'])).read_text(encoding='utf-8'))
        asset=profile['assets'][f"mp3d/{meta['scene_id']}/{meta['scene_id']}.glb"]
        source=self.source_path(asset['scene_path'])
        sha=asset['scene_sha256']
        key=hashlib.sha256(f"{sha}|{nav['plane_height']}|scene-topdown-v3-mp3d-zup".encode()).hexdigest()
        info_path=confined(self.root,self.root/(key+'.json'))
        image_path=confined(self.root,self.root/(key+'.webp'))
        if info_path.exists() and image_path.exists():return json.loads(info_path.read_text(encoding='utf-8'))
        if cached_only:return {'available':False,'uncached':True}
        with self.lock:
            info_path=confined(self.root,self.root/(key+'.json'))
            image_path=confined(self.root,self.root/(key+'.webp'))
            if info_path.exists() and image_path.exists():return json.loads(info_path.read_text(encoding='utf-8'))
            if hashlib.sha256(source.read_bytes()).hexdigest()!=sha:raise ValueError('Scene GLB identity changed')
            from process_show.maps.scene_geometry import textured_topdown
            started=time.monotonic()
            transform=[[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]]
            image,info=textured_topdown(source,nav['bounds'],nav['plane_height'],world_transform=transform)
            if nav['navmesh_sha256'] not in self.finders:
                import habitat_sim
                nav_path,_=self.asset(meta)
                pf=habitat_sim.PathFinder()
                if not pf.load_nav_mesh(str(nav_path)):raise ValueError('Navmesh load failed')
                self.finders[nav['navmesh_sha256']]=pf
            pf=self.finders[nav['navmesh_sha256']]
            low,high=pf.get_bounds()
            for axis in (0,2):
                if abs(info['geometry_bounds'][0][axis]-float(low[axis]))>.15 or abs(info['geometry_bounds'][1][axis]-float(high[axis]))>.15:
                    raise ValueError('Scene/navmesh coordinate registration failed')
            temp=image_path.with_suffix('.tmp');image.save(temp,'WEBP',quality=90,method=3);temp.replace(image_path)
            info.update(available=True,key=key,plane_height=nav['plane_height'],scene_sha256=sha,
                        source='actual scene GLB geometry, CPU orthographic roof cut; color_mode recorded separately',
                        image_url='/api/maps/'+key+'.webp',generation_seconds=time.monotonic()-started,
                        navmesh_sha256=nav['navmesh_sha256'],mode='scene',navmap=nav)
            atomic_json(info_path,info)
            return info


_maps={}
_maps_lock=threading.Lock()
def maps_for(store):
    key=str(store.root)
    with _maps_lock:
        if key not in _maps:
            from process_show.storage.scope import Scope
            _maps[key]=SceneMaps(store.root,Scope.current().project)
        return _maps[key]
