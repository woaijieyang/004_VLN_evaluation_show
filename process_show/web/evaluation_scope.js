(function(root){
 'use strict';
 function resolve(health,runs,query){
  if(health.evaluation_id)return {mode:'bound',evaluation:health.evaluation};
  const id=query.get('evaluation'),name=query.get('run'),model=query.get('model');
  if(!id&&!name)return {mode:'choose'};
  const matches=runs.filter(r=>id?r.id===id:r.run_id===name&&(!model||r.model===model||r.source_model===model));
  return matches.length===1?{mode:'select',evaluation:matches[0]}:{mode:'choose',reason:matches.length?'同名测评有多个，请按来源目录选择。':'指定测评尚未发现或已过期，请重新选择。'};
 }
 function accepts(episode,id){return !!id&&episode.evaluation_id===id;}
 function scoped(pathname,path){
  const match=pathname.match(/^\/evaluations\/[A-Za-z0-9_-]{1,64}\//);
  return match&&path.startsWith('/api/')?match[0].slice(0,-1)+path:path;
 }
 function initialEpisode(rows){return rows.find(e=>e.recorded_steps>0&&!e.error)||rows.find(e=>e.recorded_steps>0)||rows[0];}
 function status(e,labels){return e.status==='live'&&e.error?'采集异常，运行状态未知':labels[e.status]||e.status;}
 const api={resolve,accepts,scoped,initialEpisode,status};
 if(typeof module==='object'&&module.exports)module.exports=api;
 else root.EvaluationScope=api;
})(typeof globalThis==='object'?globalThis:this);
