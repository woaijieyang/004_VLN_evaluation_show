'use strict';
function drawRouteOverlay(ctx,w,h,view,steps,current,anchors,observation,global){
 const project=view.project,hits=[],boxes=[];
 const inside=(x,y,pad=10)=>x>=pad&&x<=w-pad&&y>=pad&&y<=h-pad;
 function label(value,x,y,force=false){
   ctx.font='600 12px "Segoe UI","Microsoft YaHei",sans-serif';
   const tw=ctx.measureText(value).width+8,th=19;
   x=Math.max(4,Math.min(w-tw-4,x));y=Math.max(4,Math.min(h-th-35,y));
   const box=[x,y,x+tw,y+th];
   if(!force&&boxes.some(b=>box[0]<b[2]&&box[2]>b[0]&&box[1]<b[3]&&box[3]>b[1]))return;
   ctx.fillStyle='rgba(255,255,255,.94)';ctx.fillRect(x,y,tw,th);ctx.fillStyle='#000';ctx.fillText(value,x+4,y+14);boxes.push(box);
 }
 function path(records,color,width,dash=[]){ctx.strokeStyle=color;ctx.lineWidth=width;ctx.lineJoin='round';ctx.lineCap='round';ctx.setLineDash(dash);ctx.beginPath();records.forEach((s,i)=>{const p=project(s.pose.position);if(!i||s.step!==records[i-1].step+1)ctx.moveTo(...p);else ctx.lineTo(...p);});ctx.stroke();ctx.setLineDash([]);}
 const all=steps.filter(s=>s?.pose),past=all.filter(s=>s.step<=current.step);
 if(global)path(all,'#929aa7',2,[4,5]);
 path(past,'#fff',4);path(past,'#3158df',2);
 const groups=ReplayNavigation.arrivals(steps,current.step,global);
 for(const group of groups){
   const [x,y]=project(group.position);if(!inside(x,y))continue;
   const reached=group.steps.some(n=>n<=current.step);
   ctx.beginPath();ctx.arc(x,y,reached?1.7:1.3,0,2*Math.PI);ctx.fillStyle=reached?'#fff':'#e4e7ec';ctx.fill();ctx.strokeStyle=reached?'#111':'#8a929d';ctx.lineWidth=.7;ctx.stroke();
   hits.push({x,y,label:'到达步骤 '+group.steps.join(', ')+' · ['+group.position.map(v=>v.toFixed(2)).join(', ')+'] m'});
 }
 function anchor(p,name,color,goal=false){
   if(!p)return;const [px,py]=project(p),outside=!inside(px,py,18);
   const x=Math.max(18,Math.min(w-18,px)),y=Math.max(26,Math.min(h-55,py));
   ctx.fillStyle=color;ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.beginPath();
   if(goal){ctx.moveTo(x,y-6);ctx.lineTo(x+5.5,y);ctx.lineTo(x,y+6);ctx.lineTo(x-5.5,y);ctx.closePath();}else ctx.arc(x,y,4.5,0,Math.PI*2);
   ctx.fill();ctx.stroke();
   if(outside){const angle=Math.atan2(py-y,px-x);ctx.strokeStyle=color;ctx.lineWidth=2.5;ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x+13*Math.cos(angle),y+13*Math.sin(angle));ctx.stroke();}
   const dy=current.pose? p[1]-current.pose.position[1]:0;
   const altitude=Math.abs(dy)>1?' · '+(dy>0?'高':'低')+Math.abs(dy).toFixed(1)+'m':'';
   label(name+(outside?' · 视野外':'')+altitude,x+11,y-24,true);
   hits.push({x,y,label:name+' · '+p.map(v=>v.toFixed(3)).join(', ')+' m'+(outside?'（真实位置在视野外）':'')});
 }
 anchor(anchors?.start,'起点','#198263');
 (anchors?.goals||[]).forEach((g,i)=>anchor(g.position,'目标'+((anchors.goals.length>1)?' '+(i+1):''),'#c5413b',true));
 if(current.pose){
   const [x,y]=project(current.pose.position),q=current.pose.rotation_xyzw||[0,0,0,1],norm=Math.hypot(...q),[qx,qy,qz,qw]=q.map(v=>v/norm),angle=Math.atan2(-(1-2*(qx*qx+qy*qy)),-2*(qx*qz+qw*qy));
   if(observation!==null&&observation!==current.step&&steps[observation]?.pose){const [ox,oy]=project(steps[observation].pose.position);ctx.strokeStyle='#2356d7';ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(ox,oy,4.5,0,2*Math.PI);ctx.stroke();}
   ctx.save();ctx.translate(x,y);ctx.rotate(angle);ctx.beginPath();ctx.moveTo(10,0);ctx.lineTo(-6,-6);ctx.lineTo(-3,0);ctx.lineTo(-6,6);ctx.closePath();ctx.fillStyle='#ee9a36';ctx.shadowColor='#0005';ctx.shadowBlur=0;ctx.fill();ctx.shadowBlur=0;ctx.strokeStyle='#111';ctx.lineWidth=1.2;ctx.stroke();ctx.restore();
   label('当前位置',x+10,y+9,true);
 }
 const meters=view.scale>70?1:view.scale>25?2:5,bar=meters*view.scale;
 ctx.fillStyle='#fffffff2';ctx.fillRect(w-bar-27,7,bar+19,37);ctx.fillStyle='#000';ctx.font='12px Segoe UI';ctx.fillText(meters+' m',w-bar-18,23);ctx.strokeStyle='#000';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(w-bar-18,32);ctx.lineTo(w-18,32);ctx.stroke();
 return hits;
}
