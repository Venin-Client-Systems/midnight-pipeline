/* Native counters and explicit host activity; no invented inference metrics. */
(()=>{
  'use strict';
  const el=id=>document.getElementById(id);
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const finite=n=>typeof n==='number'&&Number.isFinite(n);
  const bytes=n=>!finite(n)?'Unavailable':n>=2**30?(n/2**30).toFixed(1)+' GiB':n>=2**20?(n/2**20).toFixed(1)+' MiB':n>=1024?(n/1024).toFixed(1)+' KiB':Math.round(n)+' B';
  const percent=n=>finite(n)?n.toFixed(1)+'%':'Unavailable';
  const celsius=n=>finite(n)?n.toFixed(1)+' °C':'Unavailable';
  const rpm=n=>finite(n)?Math.round(n).toLocaleString()+' RPM':'Unavailable';
  const duration=n=>!finite(n)?'':n>=3600?Math.floor(n/3600)+'h '+Math.floor(n%3600/60)+'m':n>=60?Math.floor(n/60)+'m '+Math.floor(n%60)+'s':Math.floor(n)+'s';
  const clock=v=>{const d=new Date(v);return v&&!Number.isNaN(d.getTime())?new Intl.DateTimeFormat('en-AU',{weekday:'short',day:'numeric',month:'short',hour:'numeric',minute:'2-digit',timeZone:'Australia/Adelaide'}).format(d)+' Adelaide':'Not available';};
  let metrics=null,state=null,lastTelemetry=0;
  const charts=[['chart-cpu','cpu_percent',100],['chart-gpu','gpu_percent',100],['chart-memory','memory_percent',100],['chart-swap','swap_bytes',null],['chart-temperature','cpu_celsius',110],['chart-fan','fan_rpm',null]];

  function paintOperation(){
    if(!state)return;
    const op=state.operation||{}, old=Date.now()-new Date(state.checked_at).getTime()>15000;
    el('running-answer').textContent=old?'UNKNOWN':op.answer||'CHECK';
    el('running-answer').className='running-answer '+(old?'attention':op.state||'');
    el('headline').textContent=old?'Status needs a fresh reading':op.headline||'Checking Midnight';
    el('running-detail').textContent=old?'The last scheduler observation is stale.':op.state==='running'?'Scheduler on · '+(op.local_worker_active?'Local coding worker active':'No local coding worker active'):op.happening||'';
    let happening=op.happening||'Waiting for an observation.';
    if(!old&&op.work_active&&metrics&&!metrics.stale){
      const active=(metrics.projects||[]).find(p=>p.process_running)||(metrics.projects||[]).find(p=>p.task);
      if(active)happening=active.stage+(active.name?' · '+active.name:'')+(active.task?' · '+active.task:'');
    }
    el('happening').textContent=old?'Telemetry may still be available below; the scheduler status is not current.':happening;
    el('next-run-label').textContent=op.next_run_label||'Next scheduled run';
    el('next-run-value').textContent=state.run_mode==='continuous'?'Always eligible':clock(op.next_run_at);
    const days=state.schedule.weekdays,dayLabel=!days||days.length===7?'Every day':days.join(', ').replace(/\b\w/g,c=>c.toUpperCase());
    el('next-run-detail').textContent=op.next_run_blocked?'Work remains subject to the displayed hold and admission controls.':
      state.commissioning?.active?'One retained '+state.commissioning.project+' action · '+state.commissioning.state+' · original deadline; no automatic second action.':
      (op.next_work_at?op.next_work_label+' '+clock(op.next_work_at)+' · ':'')+(state.run_mode==='continuous'?'24/7 with no expiry. Saved schedule '+dayLabel+' '+state.schedule.start+'–'+state.schedule.end+' Adelaide; returns when you switch mode.':dayLabel+' '+state.schedule.start+'–'+state.schedule.end+' · Next scheduled start '+clock(state.next_start));
    const readiness=state.readiness||{}, memory=readiness.memory||{};
    const age=(Date.now()-new Date(readiness.observed_at).getTime())/1000;
    const outdated=old||!finite(age)||age<0||age>15;
    el('readiness-title').textContent=outdated?'Launch observation needs refreshing':readiness.state==='blocked_now'?'A launch prerequisite is blocked now':'Launch admission remains to be checked';
    el('readiness-state').textContent=outdated?'Unknown / stale':words(readiness.state);
    el('readiness-state').className='pill'+(outdated||readiness.state==='blocked_now'?' warn':' neutral');
    el('readiness-summary').textContent=outdated?'A fresh reading is required before describing current launch conditions.':readiness.summary||'No launch observation is available.';
    el('readiness-detail').textContent=(readiness.model?'Selected model: '+readiness.model+' · ':'')+'Observed '+clock(readiness.observed_at)+(finite(memory.deficit_bytes)?' · Memory shortfall at observation: '+(memory.deficit_bytes/1e9).toFixed(1)+' GB':'');
    el('readiness-note').textContent=(readiness.notes||[]).join(' ');
  }

  function drawCharts(){
    const history=metrics?.history||[];
    for(const [id,key,maximum] of charts){
      const canvas=el(id), width=canvas.getBoundingClientRect().width;
      if(!width)continue;
      const ratio=Math.min(window.devicePixelRatio||1,1.5),height=66;
      canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
      const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);ctx.clearRect(0,0,width,height);
      ctx.strokeStyle='#303e4b';ctx.lineWidth=1;
      for(const y of [14,38,62]){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke();}
      const valid=history.map(p=>p[key]).filter(finite);
      if(valid.length<2){ctx.fillStyle='#a7b4c6';ctx.font='11px -apple-system,sans-serif';ctx.fillText('Building live history…',0,32);continue;}
      const max=maximum||Math.max(...valid)*1.12||1;
      const end=history[history.length-1].at,begin=Math.max(history[0].at,end-600),span=Math.max(end-begin,20);
      ctx.strokeStyle=key==='memory_percent'&&metrics.memory?.pressure!=='normal'?'#f0ba7b':'#99e7ce';ctx.lineWidth=1.7;
      ctx.beginPath();let moving=false;
      for(const p of history){if(!finite(p[key])){moving=false;continue;}const x=Math.max(0,Math.min(width,(p.at-begin)/span*width)),y=62-Math.min(1,p[key]/max)*55;if(moving)ctx.lineTo(x,y);else ctx.moveTo(x,y);moving=true;}
      ctx.stroke();
      canvas.onpointermove=event=>{const at=begin+(event.offsetX/width)*span;const p=history.reduce((a,b)=>Math.abs(b.at-at)<Math.abs(a.at-at)?b:a);canvas.title=clock(p.at*1000)+' · '+(key==='swap_bytes'?bytes(p[key]):key.endsWith('celsius')?celsius(p[key]):key==='fan_rpm'?rpm(p[key]):percent(p[key]));};
    }
  }

  function vital(label,value){const row=node('div',undefined,'vital-row');row.append(node('span',label),node('strong',value));el('telemetry-vitals').append(row);}
  function paintHardware(){
    const hardware=metrics?.hardware||{},temperature=hardware.temperature||{},age=(Date.now()-new Date(hardware.sampled_at).getTime())/1000;
    const fresh=hardware.available===true&&finite(age)&&age>=-5&&age<=25&&!metrics.stale;
    const fans=Array.isArray(hardware.fans)?hardware.fans:[],first=fans.find(f=>finite(f.rpm));
    el('telemetry-temperature').textContent=fresh?celsius(temperature.cpu_celsius):'Unavailable';
    el('telemetry-temperature-detail').textContent=fresh?'CPU sensor average · GPU '+celsius(temperature.gpu_celsius):hardware.reason||temperature.reason||'Temperature reading unavailable / stale';
    el('telemetry-fan').textContent=fresh&&first?rpm(first.rpm):'Unavailable';
    el('telemetry-fan-detail').textContent=fresh&&fans.length?fans.map(f=>f.name+': '+rpm(f.rpm)).join(' · '):hardware.reason||hardware.fan_reason||'Fan reading unavailable / stale';
    el('telemetry-hardware-detail').textContent='Temperature and fan sensors update every 10 seconds. '+(hardware.sampled_at?'Last sensor reading '+clock(hardware.sampled_at)+'. ':'')+(hardware.sensor_group_note||'');
    const sensors=el('telemetry-sensors');sensors.replaceChildren();
    if(fresh)for(const sensor of temperature.sensors||[])sensors.append(node('span',sensor.key+' · '+celsius(sensor.celsius),'sensor-reading'));
  }
  function paint(value){
    metrics=value;lastTelemetry=Date.now();
    const cpu=value.cpu||{},gpu=value.gpu||{},memory=value.memory||{},models=value.models;
    const selected=(state?.models?.selected_next||state?.models?.active)?.model;
    const model=Array.isArray(models)?models.find(m=>m.name===selected)||models[0]:null;
    const native=value.native_models,session=(native?.sessions||[]).find(s=>s.birth_verified),progress=session?.progress;
    el('telemetry-model').textContent=session?session.model+(session.native_descendants?' · native process active':' · gateway active'):model?.name||(models===null?'Ollama is unavailable':native?.state==='unavailable'?'Native model observation unavailable':Array.isArray(models)&&!models.length?'No Ollama model is loaded':'Observing the local model');
    const details=['Selected for next action: '+(selected||'Not yet observed')];
    if(session){
      details.push('Native identity observed '+clock(new Date(native.observed_at*1000).toISOString()));
      if(progress?.prefill)details.push('Last logged prefill chunk layer '+progress.prefill.last_logged_layer+'/'+progress.prefill.layers+(finite(progress.prefill.chunk_tokens)?' · '+progress.prefill.chunk_tokens+' chunk tokens':'')+' (checkpoint time unavailable)'+(progress.prefill.log_stale?' · native log stale':''));
      if(progress?.generated_output_observed)details.push('Output observed in bounded stream tail; wall time unavailable'+(finite(progress.output_seconds_behind_latest_record)?' · '+duration(progress.output_seconds_behind_latest_record)+' before latest stream record':''));
      else details.push('No generated output observed in bounded stream tail');
      if(progress?.stream_stale)details.push('Stream observation stale');
    }else if(model)details.push(model.parameters,model.quantisation,finite(model.context_tokens)?model.context_tokens.toLocaleString()+' token context':'Context not reported','Ollama model resident');
    if(native?.available===false||native?.truncated)details.push('Native observations incomplete');
    el('telemetry-model-detail').textContent=details.filter(Boolean).join(' · ');
    el('telemetry-cpu').textContent=percent(cpu.utilisation_percent);
    el('telemetry-cpu-detail').textContent=(cpu.logical_cores?cpu.logical_cores+' logical cores':'Core count unavailable')+' · Thermal '+(cpu.thermal_state||'unavailable');
    el('telemetry-gpu').textContent=percent(gpu.utilisation_percent);
    el('telemetry-gpu-detail').textContent=gpu.name?gpu.name+' · '+bytes(gpu.in_use_bytes)+' GPU memory in use':'GPU driver counters unavailable';
    el('telemetry-pressure').textContent=(memory.pressure||'unavailable').replace(/^./,x=>x.toUpperCase());
    el('telemetry-pressure').className='pressure-'+memory.pressure;
    el('telemetry-memory-detail').textContent=bytes(memory.occupied_bytes)+' / '+bytes(memory.total_bytes)+' occupied · '+bytes(memory.compressed_bytes)+' compressed';
    el('telemetry-swap').textContent=bytes(memory.swap_used_bytes);
    el('telemetry-swap-detail').textContent='In '+bytes(memory.swap_in_bytes_per_second)+'/s · Out '+bytes(memory.swap_out_bytes_per_second)+'/s';
    el('telemetry-vitals').replaceChildren();
    vital('Ollama model resident allocation',model?bytes(model.resident_bytes):'Not loaded / unavailable');
    if(session)vital('Colibri sampled charged footprint',session.memory?.available?bytes(session.memory.charged_bytes):'Unavailable / stale');
    vital('Model GPU / unified allocation',model?bytes(model.gpu_bytes):'Unavailable');
    vital('Wired memory',bytes(memory.wired_bytes));
    const usage=(value.projects||[]).find(p=>p.usage)?.usage;
    vital('Last completed worker turn',usage?(finite(usage.input_tokens)?usage.input_tokens.toLocaleString():'—')+' input / '+(finite(usage.output_tokens)?usage.output_tokens.toLocaleString():'—')+' output tokens':'No completed turn usage reported yet');
    el('telemetry-vitals').append(node('p','Live token/s is not reported by this client. Token counts above are completed-turn usage, not context occupancy.','meta'));
    const work=el('telemetry-work');work.replaceChildren();
    if(!(value.projects||[]).length)work.append(node('p','No registered project activity.','muted'));
    for(const p of value.projects||[]){
      const section=node('div',undefined,'worker-observation');
      section.append(node('p',p.name+' · '+p.stage,'worker-stage'));
      if(p.task)section.append(node('p',p.task,'worker-task'));
      else if(p.last_task)section.append(node('p','Last task: '+p.last_task,'worker-task'));
      const detail=[];
      if(p.process_running)detail.push('Current process active'+(finite(p.phase_elapsed_seconds)?' for '+duration(p.phase_elapsed_seconds):''));
      if(finite(p.last_worker_event_age_seconds))detail.push('Local worker event '+duration(p.last_worker_event_age_seconds)+' ago');
      if(p.control?.state!=='active'&&p.control?.explanation)detail.push(p.control.explanation);
      if(p.control?.next_retry_at)detail.push('Recheck '+clock(p.control.next_retry_at));
      if(p.control?.recovery_owner&&p.control.state==='blocked')detail.push('Recovery: '+p.control.recovery_owner);
      section.append(node('p',detail.join(' · ')||'No project phase is active.','meta'));
      if(p.events?.length){const list=node('ol',undefined,'worker-events');for(const event of p.events.slice(-4).reverse())list.append(node('li',event.label+' '+event.state,event.state==='failed'?'event-failed':''));section.append(list);}
      work.append(section);
    }
    const processes=el('telemetry-processes');processes.replaceChildren();
    for(const p of value.processes||[]){const tr=node('tr');for(const text of [p.role+' · '+p.name+' · PID '+p.pid,percent(p.cpu_percent),bytes(p.footprint_bytes),bytes(p.read_bytes_per_second)+'/s / '+bytes(p.write_bytes_per_second)+'/s'])tr.append(node('td',text));processes.append(tr);}
    el('telemetry-measurement').textContent='Observed '+clock(value.sampled_at)+' · '+(finite(value.sample_cost_ms)?'Sample took '+value.sample_cost_ms.toFixed(0)+' ms. ':'')+'Native macOS counters; Ollama residency and separately identified Colibri process/footprint observations.';
    el('telemetry-errors').textContent=(value.errors||[]).length?'Some readings are unavailable: '+value.errors.map(e=>String(e).split(':')[0]).join(', '):'';
    drawCharts();freshness();paintOperation();
  }

  function freshness(){
    if(!metrics)return;
    const age=metrics.sampled_at?(Date.now()-new Date(metrics.sampled_at).getTime())/1000:Infinity;
    const stale=!metrics.available||!finite(age)||age>8||Date.now()-lastTelemetry>10000;
    metrics.stale=stale;
    el('telemetry').classList.toggle('telemetry-stale',stale);
    el('telemetry-status').textContent=stale?'Telemetry unavailable / stale':'Live · '+(metrics.interval_seconds||2)+'-second samples';
    el('telemetry-status').className='pill'+(stale?' warn':'');
    if(stale&&metrics.native_models)el('telemetry-model').textContent='Native model observation unavailable / stale';
    paintHardware();
  }
  document.addEventListener('midnight-state',event=>{state=event.detail;paintOperation();});
  if(window.midnightData){state=window.midnightData;paintOperation();}
  let pending=false;
  async function refresh(){if(pending)return;pending=true;try{const response=await fetch('/api/telemetry',{cache:'no-store',credentials:'same-origin'});if(response.ok)paint(await response.json());}catch(_error){freshness();}finally{pending=false;}}
  refresh();setInterval(refresh,20000);setInterval(()=>{freshness();paintOperation();},1000);
  const feed=new EventSource('/api/telemetry/events');
  feed.addEventListener('telemetry',event=>{try{paint(JSON.parse(event.data));}catch(_error){freshness();}});
  feed.onerror=()=>{el('telemetry-status').textContent='Reconnecting telemetry…';el('telemetry-status').className='pill warn';};
  let resizePending=false;window.addEventListener('resize',()=>{if(!resizePending){resizePending=true;requestAnimationFrame(()=>{drawCharts();resizePending=false;});}});
})();
