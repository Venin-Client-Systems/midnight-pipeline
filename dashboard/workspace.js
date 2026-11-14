"use strict";
(()=>{
const el=id=>document.getElementById(id);
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
let room=null,older=false,cursor=null,query='',busy=false,last=0,generation=0,messageBusy=null;
const stamp=value=>new Date(typeof value==='number'?value*1000:value).toLocaleString('en-AU',{timeZone:'Australia/Adelaide',hour12:false});
async function get(path){const r=await fetch(path,{cache:'no-store'});if(!r.ok)throw Error('Workspace unavailable');return r.json();}
function articles(messages){
 return messages.map(m=>{
  const article=node('article',undefined,'agent-message '+(m.kind==='decision'?'agent-decision':''));article.dataset.message=m.id;
  const head=node('div',undefined,'agent-message-heading');head.append(node('strong',m.author_name),node('span',m.kind==='decision'?'Principal decision':m.kind,'pill neutral'),node('time',stamp(m.at),'meta'));article.append(head);
  if(m.parent){const reply=node('button','View reply thread','agent-reply-link');reply.addEventListener('click',()=>thread(m.parent).catch(showError));article.append(reply);}
  if(m.kind==='decision')article.append(node('p',[m.context.scope,m.context.effect].filter(Boolean).join(' · '),'meta'));
  article.append(node('div',m.body,'agent-message-body'));
  if(m.context.model?.model)article.append(node('p',m.context.model.model+(m.context.mode==='advice_only'?' · Advice session':''),'meta'));
  return article;
 });
}
async function messages(before=null){
 if(!room)return;const gen=generation;if(messageBusy===gen)return;messageBusy=gen;el('agent-older').disabled=true;
 const selected=room,selectedQuery=query;const p={room,limit:'40'};if(before)p.before=String(before);if(query)p.q=query;
 try{
  const data=await get('/api/chat/messages?'+new URLSearchParams(p));
  if(room!==selected||query!==selectedQuery||generation!==gen)return;
  if(!data.available)throw Error('Workspace unavailable');
  const list=el('agent-messages');const existing=new Set([...list.children].map(e=>e.dataset.message));
  const items=articles(data.messages.filter(m=>!before||!existing.has(m.id)));
  if(before)list.prepend(...items);else list.replaceChildren(...items);
  if(!list.children.length)list.append(node('p',query?'No matching messages.':'No messages in this conversation yet.','empty'));
  cursor=data.next_cursor;el('agent-older').hidden=!cursor;el('agent-latest').hidden=!older;
 }finally{if(messageBusy===gen){messageBusy=null;el('agent-older').disabled=false;}}
}
async function thread(parent){
 const selected=room,gen=++generation;older=true;cursor=null;
 const data=await get('/api/chat/thread?'+new URLSearchParams({room:selected,message:parent}));
 if(room!==selected||generation!==gen)return;if(!data.available)throw Error('Workspace unavailable');
 el('agent-messages').replaceChildren(...articles(data.messages));
 if(data.truncated)el('agent-messages').append(node('p','Showing the parent and latest 40 direct replies. Search conversation history for earlier replies.','meta'));
 el('agent-older').hidden=true;el('agent-latest').hidden=false;
}
async function open(id,before=null){
 room=id;generation++;older=!!before;cursor=null;query='';el('agent-search').value='';el('agent-messages').replaceChildren();
 await refresh(true);await messages(before);
}
async function refresh(force=false){
 if(busy||(!force&&Date.now()-last<10000))return;busy=true;
 try{
  const [overview,rooms,inbox]=await Promise.all([get('/api/chat/overview'),get('/api/chat/rooms'),get('/api/chat/inbox')]);
  if(!overview.available||!rooms.available)throw Error('Workspace unavailable');
  el('agent-status').textContent=overview.messages+' messages · '+rooms.agents.length+' agents'+(overview.failed_local_replies?' · '+overview.failed_local_replies+' failed replies':'');
  el('agent-status').className='pill';el('agent-scrum').textContent='Daily scrum · Next '+stamp(overview.next_scrum_at);
  el('agent-scrum-detail').textContent=overview.meeting_today?'Today: '+overview.meeting_today.state+' · Opened '+stamp(overview.meeting_today.opened):'00:00 Adelaide each day · Local participants take turns; Codex advice is queued when needed.';
  if(!room&&rooms.rooms.length)room=rooms.rooms[0].id;
  const side=el('agent-rooms');side.replaceChildren();
  for(const r of rooms.rooms){const button=node('button',(r.kind==='direct'?'↔ ':r.kind==='group'?'Group · ':'# ')+r.name,'agent-room'+(r.id===room?' selected':''));button.addEventListener('click',()=>open(r.id).catch(showError));side.append(button);}
  const selected=rooms.rooms.find(r=>r.id===room);el('agent-room-title').textContent=selected?.name||'Choose a conversation';
  el('agent-members').textContent=selected?selected.members.map(a=>a.name).join(' · '):'';
  const pending=el('agent-inbox');pending.replaceChildren(node('p','Principal Architect inbox · '+inbox.questions.length,'eyebrow'));
  for(const question of inbox.questions.slice(0,8)){const b=node('button',question.author_name+': '+question.body.slice(0,150),'agent-inbox-item');b.addEventListener('click',()=>open(question.room,question.message_sequence+1).catch(showError));pending.append(b);}
  if(!inbox.questions.length)pending.append(node('p','No unanswered questions for Codex.','meta'));
  if(!older)await messages();last=Date.now();el('agent-error').hidden=true;
 }catch(e){showError(e);}finally{busy=false;}
}
function showError(){el('agent-status').textContent='Unavailable';el('agent-status').className='pill neutral';el('agent-error').hidden=false;el('agent-error').textContent='The saved agent workspace is unavailable. The dashboard has not changed anything.';}
el('agent-older').addEventListener('click',()=>{if(messageBusy===generation)return;older=true;messages(cursor).catch(showError);});
el('agent-latest').addEventListener('click',()=>{generation++;older=false;query='';el('agent-search').value='';refresh(true).then(()=>messages()).catch(showError);});
el('agent-search-form').addEventListener('submit',event=>{event.preventDefault();generation++;query=el('agent-search').value.trim();older=!!query;messages().catch(showError);});
document.addEventListener('midnight-state',()=>refresh());refresh(true);
})();
