'use strict';
const ReplayNavigation=(()=>{
 function bounds(position,steps,goals,global,span=8){
   if(!global&&position){return [position[0]-span/2,position[2]-span/2,position[0]+span/2,position[2]+span/2];}
   const points=steps.filter(s=>s?.pose).map(s=>s.pose.position).concat((goals||[]).map(g=>g.position));
   if(!points.length)return [-4,-4,4,4];
   return [Math.min(...points.map(p=>p[0]))-2,Math.min(...points.map(p=>p[2]))-2,Math.max(...points.map(p=>p[0]))+2,Math.max(...points.map(p=>p[2]))+2];
 }
 function arrivals(steps,upto,all=false){
   const groups=new Map();
   for(const s of steps){if(!s?.pose||(!all&&s.step>upto))continue;const p=s.pose.position,key=p.map(v=>Math.round(v*1000)).join(',');if(!groups.has(key))groups.set(key,{position:p,steps:[]});groups.get(key).steps.push(s.step);}
   return [...groups.values()];
 }
 function label(ids){if(!ids.length)return '';const contiguous=ids.every((n,i)=>!i||n===ids[i-1]+1);if(ids.length>1&&contiguous)return ids[0]+'–'+ids[ids.length-1];return ids.join(',');}
 return {bounds,arrivals,label};
})();
if(typeof module!=='undefined')module.exports=ReplayNavigation;
