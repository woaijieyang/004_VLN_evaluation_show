'use strict';
const $=id=>document.getElementById(id);
let selectedRun=new URLSearchParams(location.search).get('run');
let selectedModel=new URLSearchParams(location.search).get('model');
const state={evaluationId:null,listLimit:150,episodes:[],episode:null,steps:[],step:0,follow:false,playing:false,speed:1,map:null,mapImage:null,mapKey:'',mapZoom:1,pan:[0,0],fullMap:false,viewSpan:null,requestedStep:0,anchors:null,mapHits:[]};
let refreshBusy=false,refreshAgain=false,replayBusy=false,replayController=null,paintHandle=0,imageTimer=null,loadedObservation=null,policy=null,lastPlayback=0;
let selectionVersion=0,imageVersion=0,draggingMap=null,mapLoading=false;
const cards=new Map(),imageCache=new Map(),mapCache=new Map(),actionNodes=new Map();
let activeAction=null;
const statusLabels={live:'采集中记录',ready:'视频就绪',pending_encode:'等待视频编码',encoding:'视频编码中',encode_error:'编码失败'};
const text=(id,value)=>{$(id).textContent=value??'—';};
async function api(path,body,signal){
 const controller=new AbortController();let timedOut=false;
 const abort=()=>controller.abort();if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
 const timer=setTimeout(()=>{timedOut=true;controller.abort();},path.includes('/map?')?45000:20000);
 try{const r=await fetch(path,{...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),signal:controller.signal});const data=await r.json();if(!r.ok)throw Error(data.error||r.statusText);return data;}
 catch(error){if(timedOut)throw Error('请求超时，请点击“刷新 / 补载历史”重试');throw error;}
 finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}
function filtered(){const query=$('search').value.trim().toLowerCase(),filter=$('state').value;return state.episodes.filter(e=>{const m=e.meta;return EvaluationScope.accepts(e,state.evaluationId)&&(!query||[m.episode_id,m.scene_id,m.model].some(x=>String(x).toLowerCase().includes(query)))&&(!$('dataset').value||m.dataset===$('dataset').value)&&(!$('date').value||m.date===$('date').value)&&(!$('stage').value||m.stage.includes($('stage').value))&&(!filter||(filter==='kept'?(e.pinned||e.selected):['success','failure'].includes(filter)?e.outcome===filter:e.status===filter));});}
function renderList(){const box=$('episodes'),scroll=box.scrollTop,matches=filtered(),items=matches.slice(0,state.listLimit),visible=new Set(items.map(x=>x.id));text('count',state.episodes.length);text('list-summary',`显示 ${items.length} / 匹配 ${matches.length} 条 · 索引共 ${state.episodes.length} 条`);$('load-more').hidden=items.length>=matches.length;for(const e of items){let node=cards.get(e.id);if(!node){node=document.createElement('button');node.className='episode';node.dataset.episode=e.id;node.innerHTML='<div class="episode-top"><span></span><span></span></div><div class="episode-title"></div><div class="episode-scene"></div><div class="episode-status"></div>';node.onclick=()=>choose(e.id);cards.set(e.id,node);}node.classList.toggle('selected',state.episode?.id===e.id);const [dataset,kept]=node.querySelectorAll('.episode-top span');dataset.textContent=e.meta.dataset;kept.textContent=e.pinned?'★ 固定':e.selected?'保留':'';node.querySelector('.episode-title').textContent='Episode '+e.meta.episode_id;node.querySelector('.episode-scene').textContent=e.meta.scene_id;const label=node.querySelector('.episode-status');label.textContent=`${e.catching_up?e.max_step+'/'+(e.meta.source_total_steps??'?'):e.max_step} 步 · ${e.catching_up?'同步历史':e.outcome==='success'?'成功':e.outcome==='failure'?'失败':statusLabels[e.status]||e.status}`;label.className='episode-status '+(e.outcome||'');box.append(node);}for(const node of [...box.children]){if(!visible.has(node.dataset.episode))node.remove();}box.scrollTop=scroll;}
async function refresh(){if(state.serviceStopped||!state.evaluationId)return;if(refreshBusy){refreshAgain=true;return;}refreshBusy=true;try{state.episodes=await api('/api/episodes');const value=$('dataset').value,options=[...new Set(state.episodes.map(e=>e.meta.dataset))];if([...$('dataset').options].slice(1).map(o=>o.value).join('|')!==options.join('|')){$('dataset').replaceChildren(new Option('全部数据集',''),...options.map(x=>new Option(x,x)));$('dataset').value=value;}renderList();syncKeepDatasets();if(state.awaitingRun&&!state.episode&&filtered().length){state.awaitingRun=false;await choose(filtered()[0].id);}if(state.episode){const summary=state.episodes.find(e=>e.id===state.episode.id);if(!summary){stop();state.episode=null;$('viewer').hidden=true;$('empty').hidden=false;}else if(summary.updated!==state.episode.updated||summary.status!==state.episode.status){await loadReplay(false);}else{state.episode.pinned=summary.pinned;state.episode.selected=summary.selected;header();}}}catch(error){text('connection',error.message);}finally{refreshBusy=false;if(refreshAgain){refreshAgain=false;refresh();}}}
async function choose(id){stop();selectionVersion++;imageVersion++;replayController?.abort();replayBusy=false;state.episode={...state.episodes.find(e=>e.id===id)};state.steps=[];text('raw','正在加载当前任务…');actionNodes.clear();activeAction=null;$('action-sequence').replaceChildren();state.step=0;state.requestedStep=0;state.anchors=null;state.follow=state.episode.status==='live'&&!state.episode.catching_up;state.map=null;state.mapImage=null;state.mapKey='';state.mapZoom=1;state.pan=[0,0];state.fullMap=false;state.viewSpan=null;text('fit-map','全局路线');loadedObservation=null;$('rgb').hidden=true;$('rgb-empty').hidden=false;$('rgb-empty').textContent='正在加载已记录观测';$('viewer').hidden=false;$('empty').hidden=true;renderList();await loadReplay(true);}
async function loadReplay(full=false){if(!state.episode||replayBusy)return;replayBusy=true;const id=state.episode.id,version=selectionVersion;replayController=new AbortController();const after=full||!state.steps.length?'':`?after=${Math.max(0,state.steps.length-2)}`;try{const payload=await api(`/api/episodes/${id}/replay${after}`,undefined,replayController.signal);if(version!==selectionVersion)return;state.episode=payload.episode;state.anchors=payload.anchors;if(full)state.steps=[];for(const s of payload.steps)state.steps[s.step]=s;if(full)state.steps.length=payload.last_step+1;state.lastStep=payload.last_step;if(state.follow)state.requestedStep=payload.last_step;$('timeline').max=payload.last_step;$('step-number').max=payload.last_step;header();renderActionSequence();schedulePaint();requestMap();prefetch();}catch(error){if(error.name!=='AbortError')text('error',error.message);}finally{if(version===selectionVersion)replayBusy=false;}}
function header(){
 const e=state.episode;if(!e)return;const m=e.meta;
 text('eyebrow',`${m.dataset} / ${m.split} / ${m.stage}`);text('title',`Episode ${m.episode_id}`);
 text('identity',`${m.scene_id}   ·   ${m.model}   ·   ${m.date}`);
 text('status',e.catching_up?'正在同步历史':statusLabels[e.status]||e.status);
 text('instruction',m.instruction||'未记录导航指令');$('expand').hidden=(m.instruction||'').length<150;
 text('pin',e.pinned?'★ 已固定':'☆ 保留');$('download').href=`/api/episodes/${e.id}/video?download=1`;
 $('download').classList.toggle('disabled',e.status!=='ready');
 text('retention-note',e.pinned?'已保留到运行目录，不自动清除':e.selected?'当前已入选保留样本':`视频生成后缓存 ${cacheLabel()}，未保留项到期清除`);text('error',e.error||'');
 if(e.status==='ready'&&(m.video_version||1)<4){
   text('download','生成新版 MP4 ↓');
   $('download').onclick=async event=>{event.preventDefault();try{await api(`/api/episodes/${e.id}/render`,{});await loadReplay(true);}catch(error){text('error',error.message);}};
 }else{text('download',e.status==='ready'?'下载 MP4 ↓':'视频生成中');$('download').onclick=null;}
}
function stepRecord(){return state.steps[state.step]||{step:state.step,action:'待同步'};}
function setStep(n,manual=true){
 if(!state.episode)return;
 const next=Math.max(0,Math.min(Number(n),state.lastStep||0));
 if(next!==state.requestedStep&&!state.fullMap)state.pan=[0,0];
 state.requestedStep=next;
 if(manual){stop();state.follow=false;}
 $('timeline').value=next;$('step-number').value=next;
 schedulePaint();
}
function schedulePaint(){if(!paintHandle)paintHandle=requestAnimationFrame(paint);}
function paint(){paintHandle=0;if(!state.episode)return;const start=performance.now();if(!showObservation()){drawTimeline();return;}const s=stepRecord();updateAction(s);text('step-label',state.step);text('total-label',`/ ${state.lastStep||0} ${state.episode.catching_up?'已载入':'步'}`);text('action',s.action);$('timeline').value=state.step;$('step-number').value=state.step;$('follow').classList.toggle('active',state.follow);text('end-label',state.episode.catching_up?`历史同步 ${state.lastStep||0} / ${state.episode.meta.source_total_steps??'?'} 步`:(state.episode.status==='live'?'已接收 ':'共 ')+(state.lastStep||0)+' 步');text('step-info',`动作 ${s.action||'—'} · 模型调用 ${s.call_id??'—'} · 碰撞 ${s.collided===true?'是':s.collided===false?'否':'未知'}`);const p=s.pose?.position;text('coordinates',p?`x ${p[0].toFixed(2)}  y ${p[1].toFixed(2)}  z ${p[2].toFixed(2)} m`:'位姿未记录');requestMap();drawMap();drawTimeline();const perf=$('performance');perf.dataset.paints=Number(perf.dataset.paints)+1;perf.dataset.lastPaintMs=(performance.now()-start).toFixed(2);perf.dataset.step=state.step;}
function observationNumber(){const s=state.steps[state.requestedStep]||{};return $('strict').checked?(s.rgb_available?s.step:null):s.observation_step;}
function cachedImage(number){return imageCache.get(state.episode.id+':'+number);}
function loadImage(number){const eid=state.episode.id,key=eid+':'+number;let entry=imageCache.get(key);if(entry)return entry;const im=new Image();entry={image:im,ready:false,promise:null};entry.promise=new Promise((resolve,reject)=>{const timer=setTimeout(()=>{imageCache.delete(key);reject(Error('图片加载超时，请刷新重试'));},20000);im.onload=()=>{clearTimeout(timer);im.decode().catch(()=>{}).then(()=>{entry.ready=true;resolve(im);});};im.onerror=()=>{clearTimeout(timer);imageCache.delete(key);reject(Error('观测图像读取失败'));};});im.decoding='async';im.src=`/api/episodes/${eid}/observation?step=${number}&strict=1`;imageCache.set(key,entry);$('performance').dataset.observationRequests=Number($('performance').dataset.observationRequests)+1;if(imageCache.size>128)imageCache.delete(imageCache.keys().next().value);return entry;}
function applyObservation(entry,number){if(!entry.ready)return;const rgb=$('rgb');if(rgb.src!==entry.image.src)rgb.src=entry.image.src;rgb.hidden=false;$('rgb-empty').hidden=true;$('image-loading').hidden=true;loadedObservation=number;const exact=number===state.step;const message=exact?`第 ${number} 步的真实画面`:`第 ${state.step} 步未保存图片，显示第 ${number} 步画面`;text('observation-badge',message);$('observation-badge').hidden=false;rgb.dataset.sourceStep=number;rgb.dataset.requestedStep=state.step;rgb.dataset.displayStep=state.step;text('observation-label',exact?'当前步原始观测':`第 ${number} 步的历史画面`);text('rgb-note',exact?'该图像在此步骤真实记录':'BEV 为当前步位置；图片来自此前步骤');}
function showObservation(){
 const number=observationNumber();clearTimeout(imageTimer);
 if(number===null||number===undefined){
   imageVersion++;state.step=state.requestedStep;loadedObservation=null;
   $('rgb').hidden=true;$('rgb').dataset.displayStep=state.step;$('image-loading').hidden=true;
   $('rgb-empty').hidden=false;$('rgb-empty').textContent='此步骤没有记录图像';$('observation-badge').hidden=true;
   text('observation-label','无原始帧');text('rgb-note','可取消严格模式查看最近观测');return true;
 }
 const cached=cachedImage(number);
 if(cached?.ready){imageVersion++;state.step=state.requestedStep;applyObservation(cached,number);return true;}
 const request=++imageVersion,version=selectionVersion,requested=state.requestedStep;
 text('image-loading',`定位 STEP ${requested} · 加载观测 #${number}…`);$('image-loading').hidden=false;
 $('performance').dataset.pendingStep=requested;
 imageTimer=setTimeout(()=>{
   loadImage(number).promise.then(()=>{if(request===imageVersion&&version===selectionVersion&&requested===state.requestedStep)schedulePaint();})
    .catch(error=>{if(request===imageVersion)text('image-loading',error.message);});
 },state.playing?0:50);
 return false;
}
function prefetch(){const source=state.steps.filter(s=>s?.rgb_available).map(s=>s.step),current=state.step;const ordered=[...source].sort((a,b)=>Math.abs(a-current)-Math.abs(b-current)).slice(0,24),version=selectionVersion;let next=0;async function worker(){while(next<ordered.length&&version===selectionVersion){const n=ordered[next++];try{await loadImage(n).promise;}catch(_){}}}for(let i=0;i<3;i++)worker();}
let mapController=null,mapRequestId=0;
function waitForMap(delay,signal){return new Promise((resolve,reject)=>{
 const cancel=()=>{clearTimeout(timer);reject(new DOMException('Cancelled','AbortError'));};
 const timer=setTimeout(()=>{signal.removeEventListener('abort',cancel);resolve();},delay);
 if(signal.aborted)cancel();else signal.addEventListener('abort',cancel,{once:true});
});}
function requestMap(){
 if(!state.episode)return;const position=stepRecord().pose?.position;if(!position)return;
 const plane=Math.round(position[1]*4)/4,mode=$('map-mode').value,id=state.episode.id;
 const key=id+':'+plane+':'+mode+':'+(stepRecord().map_asset||'');
 if(key===state.mapKey)return;
 state.mapKey=key;mapController?.abort();mapController=new AbortController();
 const controller=mapController,request=++mapRequestId,version=selectionVersion,step=state.step;
 const current=()=>request===mapRequestId&&version===selectionVersion&&state.mapKey===key;
 state.map=null;state.mapImage=null;text('map-note','');text('map-message','正在读取场景地图…');$('map-message').hidden=false;
 function apply(info,image){if(!current())return;state.map=info;state.mapImage=image;
   $('map-message').hidden=!!info.available;
   if(!info.available)text('map-message',(info.reason||'未提供地图')+' · 可点击“重试地图”');
   text('map-note',info.available?(info.description||(mode==='scene'?'物体环境':'可通行区域'))+' · y='+plane.toFixed(2)+'m':'场景底图暂不可用，当前仅显示真实轨迹');
   schedulePaint();
 }
 const cached=mapCache.get(key);if(cached){apply(cached.info,cached.image);return;}
 (async()=>{
   const started=performance.now();let info;
   while(current()){
     info=await api(`/api/episodes/${id}/map?height=${plane}&mode=${mode}&step=${step}`,undefined,controller.signal);
     if(!current())return;
     if(!info.pending)break;
     const elapsed=Math.round((performance.now()-started)/1000);
     text('map-message',`${info.reason} · 已等待 ${elapsed} 秒`);
     if(elapsed>=120)throw Error('地图仍在后台生成，请稍后点击“重试地图”');
     await waitForMap(Math.min(5000,Math.max(500,(info.retry_after||1.5)*1000)),controller.signal);
   }
   if(!current())return;if(!info.available){apply(info,null);return;}
   const image=await new Promise((resolve,reject)=>{
     const im=new Image(),timer=setTimeout(()=>reject(Error('地图图片读取超时')),20000);
     im.onload=()=>{clearTimeout(timer);resolve(im);};im.onerror=()=>{clearTimeout(timer);reject(Error('地图图片读取失败'));};im.src=info.image_url;
   });
   if(!current())return;mapCache.set(key,{info,image});if(mapCache.size>24)mapCache.delete(mapCache.keys().next().value);apply(info,image);
 })().catch(error=>{if(current()&&error.name!=='AbortError'){text('map-message',error.message+' · 点击“重试地图”');$('map-message').hidden=false;text('map-note','地图加载失败，轨迹与步骤仍可查看');}});
}
$('retry-map').onclick=()=>{mapCache.delete(state.mapKey);state.mapKey='';requestMap();};
$('open-refresh').onclick=()=>{refreshSyncStatus();$('refresh-dialog').showModal();};
$('close-refresh').onclick=()=>$('refresh-dialog').close();

function canvasContext(canvas){const r=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);if(canvas.width!==Math.round(r.width*dpr)||canvas.height!==Math.round(r.height*dpr)){canvas.width=Math.round(r.width*dpr);canvas.height=Math.round(r.height*dpr);}const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);return {ctx,w:r.width,h:r.height};}
function viewTransform(w,h){
 const route=ReplayNavigation.bounds(null,state.steps,state.anchors?.goals,true);
 if(state.viewSpan===null)state.viewSpan=Math.max(20,route[2]-route[0],route[3]-route[1]);
 const bounds=ReplayNavigation.bounds(stepRecord().pose?.position,state.steps,state.anchors?.goals,state.fullMap,state.viewSpan);
 const scale=Math.min((w-48)/(bounds[2]-bounds[0]),(h-48)/(bounds[3]-bounds[1]))*state.mapZoom;
 const cx=(bounds[0]+bounds[2])/2,cz=(bounds[1]+bounds[3])/2;
 return {scale,project:p=>[w/2+(p[0]-cx)*scale+state.pan[0],h/2+(p[2]-cz)*scale+state.pan[1]]};
}
function drawMap(){
 if(!state.episode)return;const {ctx,w,h}=canvasContext($('bev'));if(!w)return;
 ctx.clearRect(0,0,w,h);ctx.fillStyle='#f3f5f7';ctx.fillRect(0,0,w,h);
 const view=viewTransform(w,h),project=view.project;
 if(state.mapImage&&state.map?.available){
   const t=state.map.map_transform;
   if(t){const [[a,b,c],[d,e,f]]=t,det=a*e-b*d;
     const world=(u,v)=>[(e*(u-c)-b*(v-f))/det,0,(-d*(u-c)+a*(v-f))/det];
     const o=project(world(0,0)),u=project(world(1,0)),v=project(world(0,1));
     ctx.save();ctx.transform(u[0]-o[0],u[1]-o[1],v[0]-o[0],v[1]-o[1],o[0],o[1]);ctx.drawImage(state.mapImage,0,0);ctx.restore();
   }
 }
 state.mapHits=drawRouteOverlay(ctx,w,h,view,state.steps,stepRecord(),state.anchors,loadedObservation,state.fullMap);
 $('bev').dataset.scale=view.scale;$('bev').dataset.zoom=state.mapZoom;$('bev').dataset.step=state.step;$('bev').dataset.view=state.fullMap?'global':'follow';
 const xy=stepRecord().pose?project(stepRecord().pose.position):[0,0];
 $('bev').dataset.agentX=xy[0];$('bev').dataset.agentY=xy[1];$('bev').dataset.width=w;$('bev').dataset.height=h;
 text('anchor-status',state.anchors?.goals?.length?(state.anchors.goal_source==='verified_evaluation_annotation'?'目标已核验':'目标由接入方提供')+' · 每一步均有点标记，悬停查看步号':state.anchors?.goal_status||'未提供目标位置');
}

function drawTimeline(){const {ctx,w,h}=canvasContext($('coverage')),max=state.lastStep||1;ctx.clearRect(0,0,w,h);const x0=6,x1=w-6,y=18;ctx.lineCap='round';ctx.strokeStyle='#e6ebf2';ctx.lineWidth=4;ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(x1,y);ctx.stroke();ctx.strokeStyle='#627eec';ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(x0+(x1-x0)*state.step/max,y);ctx.stroke();let images=0;for(const s of state.steps){if(!s)continue;if(s.rgb_available){images++;const x=x0+s.step/max*(x1-x0);ctx.fillStyle='#bbc8f4';ctx.fillRect(x-1,5,2,5);}if(s.collided){ctx.fillStyle='#e6a071';ctx.fillRect(x0+s.step/max*(x1-x0)-1,25,2,3);}}const cx=x0+state.requestedStep/max*(x1-x0);ctx.beginPath();ctx.arc(cx,y,6,0,2*Math.PI);ctx.fillStyle='white';ctx.shadowColor='#3954a933';ctx.shadowBlur=6;ctx.fill();ctx.shadowBlur=0;ctx.strokeStyle='#4263eb';ctx.lineWidth=2;ctx.stroke();text('coverage-label',`${images} 张真实观测 · 上方刻度为图像记录位置`);}
function stop(){state.playing=false;lastPlayback=0;text('play','▶');}
function playback(now){if(!state.playing)return;if(state.requestedStep!==state.step){lastPlayback=now;requestAnimationFrame(playback);return;}if(!lastPlayback)lastPlayback=now;const duration=500/state.speed;if(now-lastPlayback>=duration){const advance=Math.floor((now-lastPlayback)/duration);lastPlayback+=advance*duration;if(state.step+advance>state.lastStep){stop();return;}setStep(state.step+advance,false);}requestAnimationFrame(playback);}
$('play').onclick=()=>{if(!state.episode)return;if(state.playing){stop();return;}state.follow=false;state.playing=true;if(state.step>=state.lastStep){state.step=0;state.requestedStep=0;}text('play','Ⅱ');prefetch();requestAnimationFrame(playback);};$('previous').onclick=()=>setStep(state.step-1);$('next').onclick=()=>setStep(state.step+1);$('timeline').oninput=e=>setStep(e.target.value);$('timeline').onchange=()=>prefetch();$('step-number').oninput=e=>setStep(e.target.value);$('speed').onchange=()=>state.speed=Number($('speed').value);$('follow').onclick=()=>{stop();state.follow=true;setStep(state.lastStep,false);};$('strict').onchange=()=>{imageVersion++;loadedObservation=null;schedulePaint();};$('map-mode').onchange=()=>{state.mapKey='';requestMap();};$('zoom-in').onclick=()=>{state.mapZoom=Math.min(8,state.mapZoom*1.3);schedulePaint();};$('zoom-out').onclick=()=>{state.mapZoom=Math.max(.5,state.mapZoom/1.3);schedulePaint();};$('fit-map').onclick=()=>{state.fullMap=!state.fullMap;state.pan=[0,0];text('fit-map',state.fullMap?'跟随智能体':'全局路线');schedulePaint();};$('bev').onpointerdown=e=>{draggingMap=[e.clientX,e.clientY,...state.pan];$('bev').setPointerCapture(e.pointerId);};$('bev').onpointermove=e=>{
 if(draggingMap){state.pan=[draggingMap[2]+e.clientX-draggingMap[0],draggingMap[3]+e.clientY-draggingMap[1]];$('map-tooltip').hidden=true;schedulePaint();return;}
 const r=$('bev').getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
 const hit=state.mapHits.find(p=>Math.hypot(p.x-x,p.y-y)<12);
 $('map-tooltip').hidden=!hit;if(hit){text('map-tooltip',hit.label);$('map-tooltip').style.left=Math.min(r.width-220,Math.max(8,x+12))+'px';$('map-tooltip').style.top=Math.max(8,y-42)+'px';}
};$('bev').onpointerleave=()=>$('map-tooltip').hidden=true;$('bev').onpointerup=()=>draggingMap=null;$('bev').onwheel=e=>{e.preventDefault();state.mapZoom=Math.max(.5,Math.min(8,state.mapZoom*(e.deltaY<0?1.12:.89)));schedulePaint();};
for(const id of ['search','dataset','state','date','stage'])$(id).oninput=()=>{state.listLimit=150;renderList();};
$('pin').onclick=async()=>{try{await api(`/api/episodes/${state.episode.id}/pin`,{pinned:!state.episode.pinned});await refresh();}catch(e){text('error',e.message);}};$('copy').onclick=async()=>{try{await navigator.clipboard.writeText(state.episode.meta.instruction);text('copy','已复制');setTimeout(()=>text('copy','复制'),1200);}catch(_){text('error','请选中指令文字复制');}};$('expand').onclick=()=>{$('instruction').classList.toggle('expanded');text('expand',$('instruction').classList.contains('expanded')?'收起':'展开完整指令');};$('raw').parentElement.ontoggle=()=>{if($('raw').parentElement.open)text('raw',JSON.stringify({identity:state.episode?.meta,step:stepRecord(),result:state.episode?.result},null,2));};
$('settings').onclick=async()=>{try{const data=await api('/api/policy');policy=data.policy;$('policy-count').value=policy.count;setCacheDuration(policy.ttl_seconds);$('dataset-count').value=state.episode?(policy.datasets[state.episode.meta.dataset]??''):'';$('disk-limit').value=policy.max_bytes/1024**3;$('rules').value=JSON.stringify(policy.rules,null,2);text('policy-usage',`当前 ${Math.round(data.usage_bytes/1024**2)} MiB。固定项超过额度仍会保留。`);text('policy-error','');$('policy-dialog').showModal();}catch(e){text('error',e.message);}};$('cancel-policy').onclick=()=>$('policy-dialog').close();$('policy-form').onsubmit=async e=>{e.preventDefault();try{const datasets={...policy.datasets},rules=JSON.parse($('rules').value);if(state.episode){const m=state.episode.meta;if($('dataset-count').value==='')delete datasets[m.dataset];else datasets[m.dataset]=Number($('dataset-count').value);const k=$('rule-kind').value;if(k)rules.push({dataset:m.dataset,[k]:m[k]});}await api('/api/policy',{count:Number($('policy-count').value),datasets,rules,ttl_seconds:cacheDuration(),max_bytes:Math.round(Number($('disk-limit').value)*1024**3)});$('policy-dialog').close();await loadKeepPolicy();await refresh();}catch(e){text('policy-error',e.message);}};
new ResizeObserver(()=>schedulePaint()).observe($('viewer'));window.addEventListener('resize',schedulePaint);document.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)||$('policy-dialog').open||!state.episode)return;if(e.key==='ArrowLeft'){e.preventDefault();setStep(state.step-1);}if(e.key==='ArrowRight'){e.preventDefault();setStep(state.step+1);}if(e.code==='Space'){e.preventDefault();$('play').click();}});
let events=new EventSource('/api/events');function bindEvents(){events.addEventListener('update',()=>{if(!connectionManaged){text('connection','实时连接');$('connection').classList.add('online');}refresh();});events.onerror=()=>{if(!connectionManaged){text('connection','连接中断，正在重连');$('connection').classList.remove('online');}};}bindEvents();refresh();
let serviceIdentity=null;
api('/api/health').then(info=>{serviceIdentity=info;document.querySelector('.app-tag').textContent='导航过程回放 · '+info.project_name;document.querySelector('.app-tag').title=info.project_root;text('stop-service',info.run_id?'停止此测评服务':'停止本目录服务');}).catch(()=>{});
$('stop-service').onclick=async()=>{
 try{
  if(!serviceIdentity)serviceIdentity=await api('/api/health');
  await api('/api/control/stop',{scope_id:serviceIdentity.scope_id});
  state.serviceStopped=true;stop();events.close();text('connection',serviceIdentity.run_id?'此测评服务已停止':'本目录服务已停止');$('connection').classList.remove('online');
  text('error',serviceIdentity.run_id?'此测评的回放端口已停止；其他测评与模型继续运行。可从测评选择页重新打开。':'本目录的回放服务已停止，模型继续运行。需要恢复时重新运行对应系统入口。');
  text('stop-service','已停止');$('stop-service').disabled=true;
 }catch(error){text('error',error.message);}
};

// Explicit retention is scoped to a dataset; keep drafts intact during SSE refreshes.
let keepPolicy=null,keepDirty=false;
function syncKeepDatasets(){
 const select=$('keep-dataset'),old=select.value;
 const names=[...new Set([...state.episodes.map(e=>e.meta.dataset),...Object.keys(keepPolicy?.selections||{})])].sort();
 if(names.join('\n')!==[...select.options].map(o=>o.value).join('\n')){
   select.replaceChildren(...names.map(n=>new Option(n,n)));if(names.includes(old))select.value=old;
 }
 if(!keepDirty)showKeepSelection();
 $('save-keep').disabled=!select.value;
}
function showKeepSelection(){
 if(!keepPolicy)return;document.querySelector('.keep-heading span').textContent='缓存 '+cacheLabel();const dataset=$('keep-dataset').value;
 $('keep-ids').value=(keepPolicy.selections?.[dataset]||[]).join(', ');
 const count=keepPolicy.datasets[dataset]??keepPolicy.count;
 text('keep-hint',`每数据集 ${count} 条；指定优先，不足随机补足，超额全留。`);
}
async function loadKeepPolicy(){try{keepPolicy=(await api('/api/policy')).policy;syncKeepDatasets();}catch(e){text('keep-feedback',e.message);}}
$('keep-ids').oninput=()=>{keepDirty=true;text('keep-feedback','尚未保存');};
$('keep-dataset').onchange=()=>{keepDirty=false;showKeepSelection();text('keep-feedback','');};
$('keep-form').onsubmit=async event=>{
 event.preventDefault();const dataset=$('keep-dataset').value;if(!dataset)return;
 const ids=[...new Set($('keep-ids').value.split(/[,，、;；\s]+/u).filter(Boolean))];
 $('save-keep').disabled=true;
 try{
   const latest=(await api('/api/policy')).policy;
   keepPolicy=await api('/api/policy',{selections:{...(latest.selections||{}),[dataset]:ids}});
   keepDirty=false;showKeepSelection();await refresh();
   const available=state.episodes.filter(e=>e.meta.dataset===dataset),matched=available.filter(e=>ids.includes(String(e.meta.episode_id)));
   const missing=ids.filter(id=>!matched.some(e=>String(e.meta.episode_id)===id));
   const count=keepPolicy.datasets[dataset]??keepPolicy.count;
   const kept=available.filter(e=>e.selected||e.pinned).length;
   text('keep-feedback',`已保存 ${ids.length} 个 ID，当前保留 ${kept} 条。`+(missing.length?`尚无缓存：${missing.join('、')}；后续到达时保留，已删除内容不会重建。`:'')+(kept<count?'可用样本不足，后续完成时补足。':'')+(matched.length>ids.length?'同 ID 的不同运行均保留。':''));
 }catch(e){text('keep-feedback',e.message);}finally{$('save-keep').disabled=false;}
};
loadKeepPolicy();

function actionInfo(step){
 const raw=step?.action??'未记录';
 const name={RESET:['○','初始化'],FORWARD:['↑','前进'],MOVE_FORWARD:['↑','前进'],LEFT:['↶','左转'],TURN_LEFT:['↶','左转'],RIGHT:['↷','右转'],TURN_RIGHT:['↷','右转'],STOP:['■','停止']}[String(raw).toUpperCase()];
 return name||['·',String(raw)];
}
function renderActionSequence(){
 const sequence=$('action-sequence');
 for(const s of state.steps){
   if(!s)continue;let button=actionNodes.get(s.step);
   if(!button){button=document.createElement('button');button.type='button';button.dataset.step=s.step;button.onclick=()=>setStep(s.step);sequence.append(button);actionNodes.set(s.step,button);}
   const [icon,name]=actionInfo(s);button.textContent=`${s.step} · ${icon} ${name}`;
   button.setAttribute('aria-label',`第 ${s.step} 步：${name}`);
   button.title=s.call_id==null?'没有关联模型调用':`来自模型调用 ${s.call_id}`;
 }
}
function updateAction(s){
 if($('raw').parentElement.open)text('raw',JSON.stringify({identity:state.episode.meta,step:s,result:state.episode.result},null,2));
 const [icon,name]=actionInfo(s);text('action-current',`${icon} ${name}`);
 const origin=s.step===0?'环境初始化':s.call_id==null?'未记录关联模型调用':`关联模型调用 #${s.call_id}`;
 text('action-origin',`${s.step===0?'第 0 步':`第 ${s.step-1} → ${s.step} 步`} · ${origin}`+(s.collided?' · 发生碰撞':'')+(s.stop_source?` · 停止来源：${s.stop_source}`:''));
 const button=actionNodes.get(s.step);
 if(button&&button!==activeAction){
   activeAction?.removeAttribute('aria-current');button.setAttribute('aria-current','step');activeAction=button;
   const box=$('action-sequence'),b=button.getBoundingClientRect(),r=box.getBoundingClientRect();
   if(b.left<r.left||b.right>r.right)box.scrollLeft+=b.left-r.left-(r.width-b.width)/2;
 }
}

let refreshTimer=null,syncBusy=false,manualRefreshing=false;
function readRefreshPreferences(){try{return JSON.parse(localStorage.getItem('process-show-refresh')||'{}');}catch(_){return {};}}
const refreshPreferences=readRefreshPreferences();
$('refresh-history').checked=refreshPreferences.history!==false;
$('refresh-interval').value=['0','5','15','30'].includes(String(refreshPreferences.interval))?String(refreshPreferences.interval):'15';
function configureRefresh(){
 clearInterval(refreshTimer);const interval=Number($('refresh-interval').value);
 try{localStorage.setItem('process-show-refresh',JSON.stringify({history:$('refresh-history').checked,interval}));}catch(_){}
 if(interval)refreshTimer=setInterval(()=>{if(!state.serviceStopped){refresh();refreshSyncStatus();}},interval*1000);
}
function presentSync(info){
 const sources=info.sources||[],pending=sources.reduce((n,s)=>n+s.pending,0),excluded=sources.reduce((n,s)=>n+s.excluded_history,0),errors=sources.reduce((n,s)=>n+s.errors.length,0);
 const scanned=sources.every(s=>s.last_scan);
 text('refresh-status',info.selection_required?'请先选择一次测评；这里不会合并不同测评的任务':info.capture_mode==='indexed_producer'?'当前测评由 SDK / 适配器直接写入；步骤到达后实时更新':!sources.length?'当前测评没有配置原始数据源':!scanned?'正在扫描配置的数据源…':pending?`正在补载：${pending} 条记录待同步`:(errors?`同步有 ${errors} 项错误，可点击刷新重试`:excluded?`${excluded} 条历史尚未导入，点击补载`:'已检查原始记录 · 当前无待导入内容'));
 const box=$('sync-details');box.replaceChildren();
 if(info.last_index_update){const pulse=document.createElement('p');pulse.textContent=`当前测评最近索引更新：${new Date(info.last_index_update*1000).toLocaleString()} · ${Math.round(info.record_age_seconds||0)} 秒前。`+(info.live_records&&info.record_age_seconds>30?'已有采集记录停更；源进程是否仍在运行未知。':'');box.append(pulse);}
 for(const message of info.capture_errors||[]){const item=document.createElement('p');item.textContent='采集/处理错误：'+message;box.append(item);}
 for(const source of sources){const item=document.createElement('p');item.textContent=`${source.source}：发现 ${source.discovered} 条，已索引 ${source.indexed} 条，待同步 ${source.pending} 条。历史补载${source.history_enabled?'已开启':'未开启'}。`+(source.last_scan?` 上次扫描 ${new Date(source.last_scan*1000).toLocaleTimeString()}`:'');box.append(item);for(const error of source.errors){const el=document.createElement('p');el.textContent=`${error.path}：${error.message}`;box.append(el);}}
 const note=document.createElement('p');note.textContent=`已到期清除 ${info.expired_episodes||0} 条。刷新不会重建已删除视频；新增记录进入原有编码队列。`;box.append(note);
}
async function refreshSyncStatus(){if(syncBusy||state.serviceStopped)return;syncBusy=true;try{presentSync(await api('/api/sync'));}catch(e){text('refresh-status',e.message);}finally{syncBusy=false;}}
$('refresh-data').onclick=async()=>{
 if(!state.evaluationId){openRunDialog();return;}
 if(manualRefreshing||state.serviceStopped)return;manualRefreshing=true;$('refresh-data').disabled=true;text('refresh-status','正在重新扫描并重试当前画面…');
 try{
   presentSync(await api('/api/refresh',{history:$('refresh-history').checked}));
   if(state.episode){imageVersion++;loadedObservation=null;for(const key of imageCache.keys())if(key.startsWith(state.episode.id+':'))imageCache.delete(key);mapCache.clear();state.mapKey='';}
   await refresh();if(state.episode){selectionVersion++;replayController?.abort();replayBusy=false;await loadReplay(true);schedulePaint();}
   await refreshSyncStatus();
 }catch(e){text('refresh-status',e.message);}finally{manualRefreshing=false;$('refresh-data').disabled=false;}
};
$('refresh-history').onchange=configureRefresh;$('refresh-interval').onchange=configureRefresh;
$('clear-filters').onclick=()=>{for(const id of ['search','dataset','state','date','stage'])$(id).value='';state.listLimit=150;renderList();};
$('load-more').onclick=()=>{state.listLimit+=150;renderList();};
configureRefresh();refreshSyncStatus();

// The stable local entry remains reachable while SSH reconnects.
let connectionManaged=false,lastTransportState=null;
async function checkTransport(){
 try{
  const response=await fetch('/api/connection',{cache:'no-store',signal:AbortSignal.timeout(3500)});
  if(!response.ok)return;const info=await response.json();if(!info.auto_reconnect)return;
  connectionManaged=true;
  const connected=info.state==='connected';
  text('connection',connected?'已连接 · 自动重连已开启':info.state==='stopped'?'已手动停止':`正在自动重连${info.retry_in?' · '+info.retry_in+'秒后重试':''}`);
  $('connection').classList.toggle('online',connected);
  $('connection').title=connected?'网络断开后会自动恢复连接':'网络恢复后自动继续，无需重新打开插件';
  if(connected&&lastTransportState&&lastTransportState!=='connected'&&!state.serviceStopped){
    refresh();if(state.episode)loadReplay(true);events.close();events=new EventSource('/api/events');bindEvents();
  }
  lastTransportState=info.state;
 }catch(_){if(connectionManaged&&!state.serviceStopped){text('connection','本机连接守护暂不可用');$('connection').classList.remove('online');}}
}
checkTransport();setInterval(checkTransport,2000);

function cacheLabel(){const seconds=keepPolicy?.ttl_seconds||3600;return seconds%86400===0?seconds/86400+' 天':seconds%3600===0?seconds/3600+' 小时':Math.round(seconds/60)+' 分钟';}
function setCacheDuration(seconds){
 const select=$('cache-duration');select.value=[...select.options].some(o=>o.value===String(seconds))?String(seconds):'custom';
 $('cache-minutes').value=seconds/60;$('custom-cache-label').hidden=select.value!=='custom';
}
function cacheDuration(){const seconds=$('cache-duration').value==='custom'?Number($('cache-minutes').value)*60:Number($('cache-duration').value);if(!Number.isInteger(seconds)||seconds<60||seconds>2592000)throw Error('缓存时间应为 1 分钟至 30 天');return seconds;}
$('cache-duration').onchange=()=>$('custom-cache-label').hidden=$('cache-duration').value!=='custom';
$('save-options').onclick=()=>{
 if(!state.episode)return;
 text('save-identity',`${state.episode.meta.dataset} · Episode ${state.episode.meta.episode_id}`);
 text('save-availability',state.episode.status==='ready'?'完整 MP4 已就绪。':'本机保存需等待 MP4 生成完成；现在可以先保留到运行目录。');
 text('save-feedback','');$('save-dialog').showModal();
};
$('cancel-save').onclick=()=>$('save-dialog').close();
$('save-form').onsubmit=async event=>{
 event.preventDefault();const episode=state.episode;if(!episode)return;
 const local=document.querySelector('input[name="save-destination"]:checked').value==='local';
 if(local&&episode.status!=='ready'){text('save-feedback','视频尚未生成完成。请等待“下载 MP4”就绪后再保存到本机。');return;}
 $('confirm-save').disabled=true;
 let writable=null;
 try{
  if(!local){await api(`/api/episodes/${episode.id}/pin`,{pinned:true});await refresh();text('save-feedback','已固定到本项目运行目录，不会因缓存到期自动清除。');return;}
  const filename=(episode.directory?.split(/[\\/]/).pop()||`${episode.meta.dataset}-${episode.meta.scene_id}-ep${episode.meta.episode_id}-${episode.id.slice(0,12)}`)+'.mp4';
  const url=`/api/episodes/${episode.id}/video?download=1`;
  if(typeof window.showSaveFilePicker==='function'){
   const handle=await window.showSaveFilePicker({suggestedName:filename,types:[{description:'MP4 视频',accept:{'video/mp4':['.mp4']}}]});
   const response=await fetch(url);if(!response.ok)throw Error('视频读取失败：'+response.status);
   writable=await handle.createWritable();const reader=response.body.getReader(),total=Number(response.headers.get('Content-Length'));let bytes=0;
   for(;;){const {done,value}=await reader.read();if(done)break;await writable.write(value);bytes+=value.byteLength;text('save-feedback',total?`正在保存到本机 · ${Math.round(bytes/total*100)}%`:`正在保存到本机 · ${Math.round(bytes/1024/1024)} MiB`);}
   await writable.close();writable=null;text('save-feedback','已保存到你选择的本机位置。服务器保留策略不变。');
  }else{
   const link=document.createElement('a');link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();
   text('save-feedback','已发起本机下载。此浏览器未提供目录选择接口，请在下载列表查看位置；如需每次自选目录，可在系统浏览器中打开页面并使用“另存为”。');
  }
 }catch(error){if(writable)await writable.abort().catch(()=>{});text('save-feedback',error.name==='AbortError'?'已取消保存。':error.message);}
 finally{$('confirm-save').disabled=false;}
};

let availableRuns=[],runDialogBusy=false,runCatalogPending=false;
function renderRuns(){
 const query=$('run-search').value.trim().toLowerCase(),list=$('run-list');list.replaceChildren();
 const matches=availableRuns.filter(r=>[r.date,r.model,r.model_sha,r.dataset,r.run_id].join(' ').toLowerCase().includes(query));
 for(const run of matches){
  const card=document.createElement('article');card.className='run-card';
  const title=document.createElement('h3');title.textContent=run.date+' · '+run.model;
  const identity=document.createElement('p');identity.textContent=run.dataset.toUpperCase()+' · '+run.stage+' · 权重 '+(run.model_sha||'未记录')+' · '+run.run_id;
  const source=document.createElement('p');source.textContent='来源：'+run.run_root;source.className='run-source';
  const details=document.createElement('div');details.className='run-card-details';
  for(const textValue of [`完成 ${run.completed} / ${run.expected??'?'} 条`,run.status,run.retained?'有保留样本':run.expires_at?'缓存至 '+new Date(run.expires_at*1000).toLocaleString():'尚未进入到期计时']){const badge=document.createElement('span');badge.textContent=textValue;details.append(badge);}
  const button=document.createElement('button');button.className='primary';button.textContent=state.evaluationId===run.id?'查看当前测评':'选择并查看';
  button.onclick=async()=>{
   button.disabled=true;text('run-error','');
   try{
    await selectEvaluation(run);
   }catch(error){text('run-error',error.message);button.disabled=false;}
  };
  card.append(title,identity,source,details,button);list.append(card);
 }
 if(!matches.length){const empty=document.createElement('p');empty.textContent=runCatalogPending?'正在扫描测评，列表会自动更新…':'没有符合条件且尚未过期的测评。请检查本目录是否已有受支持的测评记录，或点击刷新。';list.append(empty);}
}
async function openRunDialog(){
 if(!$('run-dialog').open)$('run-dialog').showModal();if(runDialogBusy)return;runDialogBusy=true;text('run-error','');text('run-summary','正在读取当前根目录内的测评记录…');
 try{
  const [data,health]=await Promise.all([api('/api/runs'),api('/api/health')]);availableRuns=data.runs;runCatalogPending=!!data.pending;
  text('run-root','作用范围：'+health.project_root+'（仅本机、本目录及其子目录）');
  text('run-summary',data.pending?'正在扫描本目录测评，已有结果会先展示…':`可选 ${availableRuns.length} 个测评 · 已剔除 ${data.expired_runs} 个过期测评 · 缓存 ${Math.round(data.ttl_seconds/60)} 分钟`);renderRuns();
  if(data.pending)setTimeout(()=>{if($('run-dialog').open)openRunDialog();},1200);
 }catch(error){text('run-error',error.message);}finally{runDialogBusy=false;}
}
async function selectEvaluation(run){
    const result=await api('/api/runs/select',{id:run.id});
    if(result.probe_token){
     const probe=new URL('/api/health',result.url);probe.searchParams.set('probe',result.probe_token);
     let healthy;
     try{const reply=await fetch(probe,{signal:AbortSignal.timeout(5000),credentials:'omit',cache:'no-store'});if(!reply.ok)throw Error('服务尚未就绪');healthy=await reply.json();}
     catch(_){throw Error(`测评已在运行主机的 ${result.port} 端口启动，但当前浏览器无法访问。已保留选择页；请在服务所在机器访问，或使用已有的远程访问通道。`);}
     if(healthy.scope_id!==result.scope_id||healthy.evaluation_id!==run.id)throw Error('端口返回的目录或测评身份不匹配，已阻止跳转。');
     location.assign(result.url);return;
    }
    throw Error('服务版本未提供测评可达性校验，请刷新后重试。');

}
async function initializeEvaluation(){
 try{
  const health=await api('/api/health');
  if(health.evaluation_id){
   const run=health.evaluation;state.evaluationId=health.evaluation_id;
   selectedRun=health.run_id;selectedModel=run.catalog_only?run.source_model||run.model:null;
   text('active-run',`${run.date||''} · ${run.model} · ${run.dataset||''} · ${run.run_id}`);
   $('active-run').title=run.run_root;
   state.awaitingRun=true;await refresh();await refreshSyncStatus();
   if(new URLSearchParams(location.search).get('choose')==='1')openRunDialog();
   return;
  }
  text('active-run','尚未选择测评 · 每次测评使用独立端口');
  state.episodes=[];renderList();
  const query=new URLSearchParams(location.search);
  if(query.get('run')||query.get('evaluation')){
   let data=await api('/api/runs');
   for(let tries=0;data.pending&&!data.runs.length&&tries<20;tries++){
    await new Promise(resolve=>setTimeout(resolve,500));data=await api('/api/runs');
   }
   const route=EvaluationScope.resolve(health,data.runs,query);
   if(route.mode==='select'){await selectEvaluation(route.evaluation);return;}
   await openRunDialog();text('run-error',route.reason||'请选择一次测评');return;
  }
  await openRunDialog();
 }catch(error){text('connection',error.message);text('active-run','无法确认当前测评身份，请重试选择');await openRunDialog();}
}
$('choose-run').onclick=openRunDialog;$('refresh-runs').onclick=openRunDialog;$('run-search').oninput=renderRuns;
$('close-runs').onclick=()=>$('run-dialog').close();
initializeEvaluation();
setInterval(()=>{if($('run-dialog').open&&!runDialogBusy)openRunDialog();},5000);
