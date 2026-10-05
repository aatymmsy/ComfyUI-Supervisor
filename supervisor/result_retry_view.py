RESULT_RETRY_CSS = """
#result-gallery .result-retry-icon {position:absolute; right:44px; top:8px; z-index:12; width:28px; height:28px; padding:5px; display:flex; align-items:center; justify-content:center; border:0; border-radius:7px; color:#fff; background:#334155; cursor:pointer; box-shadow:0 1px 5px #0003;}
#result-gallery .result-retry-icon:hover {background:#2563eb;}
#result-gallery .result-retry-icon:focus-visible {outline:2px solid #fff; outline-offset:2px;}
#result-gallery .result-retry-icon:disabled {opacity:.55; cursor:wait;}
#result-gallery .result-retry-icon svg {width:18px; height:18px; pointer-events:none;}
#result-gallery .result-retry-icon.pending svg {animation:result-retry-spin 1s linear infinite;}
#result-gallery .preview .media-button > .result-retry-icon {top:92px !important; right:82px !important; width:48px !important; height:48px !important; padding:12px !important; border-radius:9px !important;}
#result-gallery .preview .media-button > .result-retry-icon svg {width:24px !important; height:24px !important;}
.result-retry-notice {position:fixed; top:72px; right:24px; z-index:10020; max-width:min(520px,85vw); padding:12px 16px; border-radius:8px; color:#fff; background:#334155; box-shadow:0 3px 16px #0004; pointer-events:none;}
@keyframes result-retry-spin {to {transform:rotate(360deg);}}
"""

RESULT_RETRY_JS = r"""() => {
 if(window.supervisorResultRetryInstalled) return;
 window.supervisorResultRetryInstalled=true;
 const icon='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 10a9 9 0 1 1 1 7M3 3v7h7"/></svg>';
 let queued=false,pending=null,hideTimer=null;
 const failedIds=new Map();
 const notice=text=>{
   let el=document.querySelector('.result-retry-notice');
   if(!el){el=document.createElement('div');el.className='result-retry-notice';el.setAttribute('role','status');document.body.appendChild(el);}
   if(el.textContent!==text) el.textContent=text;
   clearTimeout(hideTimer);
   if(!pending) hideTimer=setTimeout(()=>el.remove(),8000);
 };
 const render=()=>{
   queued=false;
   let data,response;
   try {
     data=JSON.parse(document.querySelector('#result-image-map textarea, #result-image-map input')?.value || '{}');
     response=JSON.parse(document.querySelector('#result-retry-response textarea, #result-retry-response input')?.value || '{}');
   } catch {return;}
   if(pending && response.request_id===pending.request_id) {
     const key=pending.task_id+':'+pending.asset_id;
     if(response.ok) failedIds.delete(key); else failedIds.set(key,pending.request_id);
     pending=null; notice(response.text || (response.ok?'已加入重画队列。':'重画失败，请重试。'));
   }
   const gallery=document.getElementById('result-gallery');
   if(!gallery) return;
   for(const img of document.querySelectorAll('#result-gallery img')) {
     const url=decodeURIComponent(img.currentSrc || img.src || '');
     const record=(data.images || []).find(r=>url.includes(r.filename));
     const host=img.closest('.thumbnail-item, .gallery-item, .image-container, .image-frame') || img.parentElement;
     if(!host) continue;
     const bounds=host.getBoundingClientRect();
     if(!record || bounds.width<80 || bounds.height<80) {host.querySelector(':scope > .result-retry-icon')?.remove();continue;}
     host.classList.add('result-delete-host');
     let button=host.querySelector(':scope > .result-retry-icon');
     if(!button) {
       button=document.createElement('button');button.type='button';button.className='result-retry-icon';button.innerHTML=icon;
       button.addEventListener('click',event=>{
         event.preventDefault();event.stopPropagation();
         if(pending || button.disabled) return;
         let live;
         try{live=JSON.parse(document.querySelector('#result-image-map textarea, #result-image-map input')?.value || '{}');}catch{return;}
         const image=host.querySelector('img'),url=decodeURIComponent(image?.currentSrc || image?.src || '');
         const selected=(live.images || []).find(r=>url.includes(r.filename));
         const input=document.querySelector('#result-retry-request textarea, #result-retry-request input');
         if(!selected || !input) return;
         const task=selected.task_id || live.task_id, key=task+':'+selected.asset_id;
         pending={task_id:task,asset_id:selected.asset_id,request_id:failedIds.get(key) || crypto.randomUUID()};
         notice(window.supervisorLanguage==='en'?'Preparing prompt revision…':'正在提交重画：检查原图和工作流，随后加入队列…');
         document.querySelectorAll('#result-gallery .result-retry-icon').forEach(b=>{b.disabled=true;b.classList.add('pending');});
         const prototype=input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
         Object.getOwnPropertyDescriptor(prototype,'value').set.call(input,JSON.stringify(pending));
         input.dispatchEvent(new Event('input',{bubbles:true}));
         document.getElementById('result-retry-trigger')?.click();
       });
       host.appendChild(button);
     }
     button.disabled=!!pending;
     button.classList.toggle('pending',!!pending && pending.asset_id===record.asset_id);
     button.title=window.supervisorLanguage==='en'?'Revise this image’s prompt and regenerate':'人工审查不达标：修订此图提示词并重画';
     button.setAttribute('aria-label',button.title+' '+record.asset_id.slice(0,8));
   }
 };
 const schedule=()=>{if(!queued){queued=true;requestAnimationFrame(render);}};
 new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});
 document.addEventListener('input',schedule);document.addEventListener('load',schedule,true);
 setInterval(schedule,1000);schedule();
}"""
