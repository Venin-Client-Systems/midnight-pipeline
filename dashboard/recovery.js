"use strict";
(()=>{
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
const words=s=>String(s||'unknown').replaceAll('_',' ');
function paint(data){
 const target=document.getElementById('progress-recovery');if(!target)return;
 target.replaceChildren();
 for(const project of data.projects||[]){
  const card=node('article',undefined,'agent-message');card.append(node('h3',project.name||project.id));
  const recovery=project.repair_status;
  if(!recovery?.available){card.append(node('p','Recovery observations unavailable.','muted'));target.append(card);continue;}
  const scopes=recovery.scopes||[];
  if(!scopes.length)card.append(node('p','No retained recovery episode enrolled. Check the workbench for scope and scheduling.','muted'));
  for(const item of scopes.slice(0,3)){
   const a=item.admission||{},d=item.diagnosis||{};
   card.append(node('p',(item.task_id||'Retained task')+' · '+words(item.scope?.stage)+' · '+words(a.state),'pill '+(a.allowed?'':'neutral')));
   card.append(node('p',a.allowed?'Ready: '+words(a.reason):d.next_action||words(a.reason),'muted'));
   card.append(node('p','Failure: '+words(d.kind)+' · Prior launches at enrollment: '+(item.legacy_launches||0)+' · New charged launches: '+(item.historical_launches||0),'meta'));
   const detail=node('details');detail.append(node('summary','Scope, diagnosis and admission evidence'));
   detail.append(node('pre',JSON.stringify({scope:item.scope,checkpoint:item.checkpoint,admission:a,diagnosis:d,legacy_runs:item.legacy_runs},null,2)));card.append(detail);
  }
  target.append(card);
 }
 if(!target.children.length)target.append(node('p','No registered projects observed.','empty'));
}
document.addEventListener('midnight-state',e=>paint(e.detail));if(window.midnightData)paint(window.midnightData);
})();
