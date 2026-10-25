"""Clipped start/goal/arrival annotations for standalone video exports."""
import math
from PIL import Image,ImageDraw


def annotate(image,bbox,project,history,step,anchors,font):
    overlay=Image.new('RGBA',image.size)
    d=ImageDraw.Draw(overlay)
    for a,b in zip(history,history[1:]):
        if b['step']==a['step']+1:d.line((*project(a['pose']['position']),*project(b['pose']['position'])),fill='#3158df',width=5)
    def tag(text,x,y):
        f=font(22);width=f.getlength(text)+10
        x=max(bbox[0]+3,min(bbox[2]-width-3,x));y=max(bbox[1]+3,min(bbox[3]-30,y))
        d.rectangle((x,y,x+width,y+28),fill='#ffffffee');d.text((x+5,y),text,font=f,fill='#000')
    groups={}
    for s in history:
        p=s['pose']['position'];key=tuple(round(v,3) for v in p)
        groups.setdefault(key,[]).append(s['step'])
    for p,ids in groups.items():
        x,y=project(p)
        if not (bbox[0]+8<x<bbox[2]-8 and bbox[1]+8<y<bbox[3]-8):continue
        d.ellipse((x-2,y-2,x+2,y+2),fill='white',outline='black',width=1)
    def marker(p,label,color,goal=False):
        if p is None:return
        x,y=project(p);outside=not (bbox[0]+12<x<bbox[2]-12 and bbox[1]+12<y<bbox[3]-12)
        x=max(bbox[0]+12,min(bbox[2]-12,x));y=max(bbox[1]+12,min(bbox[3]-12,y))
        if goal:d.polygon([(x,y-10),(x+10,y),(x,y+10),(x-10,y)],fill=color,outline='white')
        else:d.ellipse((x-8,y-8,x+8,y+8),fill=color,outline='white',width=2)
        tag(label+('·视野外' if outside else ''),x+12,y-30)
    marker(anchors.get('start'),'起点','#198263')
    for i,g in enumerate(anchors.get('goals',[])):marker(g['position'],'目标'+(str(i+1) if len(anchors['goals'])>1 else ''),'#c5413b',True)
    pose=step.get('pose')
    if pose:
        x,y=project(pose['position']);q=pose.get('rotation_xyzw',[0,0,0,1]);norm=math.sqrt(sum(v*v for v in q));qx,qy,qz,qw=[v/norm for v in q]
        angle=math.atan2(-(1-2*(qx*qx+qy*qy)),-2*(qx*qz+qw*qy))
        points=[(x+24*math.cos(angle),y+24*math.sin(angle)),(x+15*math.cos(angle+2.4),y+15*math.sin(angle+2.4)),(x+15*math.cos(angle-2.4),y+15*math.sin(angle-2.4))]
        d.polygon(points,fill='#ee9a36',outline='#111');tag('当前位置',x+20,y+13)
    crop=overlay.crop(bbox);image.paste(crop,(bbox[0],bbox[1]),crop)
