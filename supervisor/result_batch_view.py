"""Compact per-image batch controls, including the gallery preview/fullscreen."""

RESULT_BATCH_CSS = """
.result-batch-panel {padding:10px 12px; border-top:1px solid var(--border-color-primary); text-align:left;}
.result-batch-controls {display:flex; flex-wrap:wrap; align-items:center; gap:12px;}
.result-batch-controls label {display:flex; align-items:center; gap:7px; font-size:14px;}
.result-batch-count {width:70px !important; min-width:0; padding:5px 8px !important; border:1px solid var(--border-color-primary); border-radius:6px; background:var(--input-background-fill); color:var(--body-text-color);}
.result-batch-review {appearance:auto !important; width:16px; height:16px; accent-color:var(--button-primary-background-fill);}
.result-batch-view {font-size:13px; padding:6px; color:var(--body-text-color); background:transparent; border:0; text-decoration:underline; cursor:pointer;}
.result-batch-start {padding:7px 14px; border:1px solid var(--button-primary-border-color); border-radius:7px; background:var(--button-primary-background-fill); color:var(--button-primary-text-color); cursor:pointer;}
.result-batch-start:disabled {opacity:.5; cursor:wait;}
.result-prompt-open,.result-discussion-open {display:inline-flex; align-items:center; justify-content:center; box-sizing:border-box; width:180px; min-width:180px; height:42px; padding:7px 14px; border:1px solid var(--border-color-primary); border-radius:7px; background:var(--button-secondary-background-fill); color:var(--button-secondary-text-color); cursor:pointer;}
.result-prompt-open:disabled,.result-discussion-open:disabled {opacity:.5; cursor:wait;}
.result-batch-panel p {font-size:12px; margin:5px 0 0; line-height:1.5;}
.result-batch-note {color:var(--body-text-color-subdued);}
.result-batch-panel input[aria-invalid=true] {border:2px solid #dc2626;}
.result-batch-feedback.error {color:#dc2626;}
#result-gallery .preview:has(.result-preview-footer) {display:flex !important; flex-direction:column !important;}
#result-gallery:has(.result-preview-footer):not(.fullscreen) {height:min(820px, calc(100dvh - 24px)) !important;}
#result-gallery .preview:has(.result-preview-footer) > .media-button {flex:1 1 0 !important; overflow:hidden;}
#result-gallery .preview:has(.result-preview-footer) > .caption {flex:0 0 auto !important;}
#result-gallery .preview:has(.result-preview-footer) > .thumbnails {position:static !important; flex:0 0 56px !important; width:100%;}
#result-gallery .preview > .thumbnails {justify-content:flex-start !important; overflow-x:auto !important; overflow-y:hidden !important;}
#result-gallery .preview > .thumbnails > .thumbnail-item {flex-shrink:0 !important;}
#result-gallery .result-preview-footer {display:flex; flex-direction:column; width:100%; flex:0 0 auto; max-height:52%; overflow:hidden; background:var(--background-fill-primary); color:var(--body-text-color); border-top:1px solid var(--border-color-primary); z-index:13;}
#result-gallery .result-preview-footer > .result-selected-details {padding:10px 14px; text-align:left; min-height:0; overflow:auto; flex:1 1 auto;}
#result-gallery .result-selected-details h3 {font-size:14px; margin:0 0 8px;}
#result-gallery .result-selected-details h4 {font-size:13px; margin:12px 0 6px;}
#result-gallery .result-selected-details .review-score-grid {display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:5px 12px; font-size:13px;}
#result-gallery .result-preview-footer > .result-batch-panel {flex:0 0 auto;}
#result-gallery .result-preview-footer summary {cursor:pointer; font-size:13px;}
#result-gallery .result-preview-footer pre {white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px;}
#result-gallery .preview:has(.result-preview-footer) > :not(.result-preview-footer) {min-height:0;}
"""

RESULT_BATCH_JS = r"""() => {
 if(window.supervisorResultBatchInstalled) return;
 window.supervisorResultBatchInstalled=true;
 const drafts=new Map();
 let pending=null, pendingPrompt=null, queued=false, footerKey='', footerMarkup='';
 let mapRaw='', imageMap=null, reviewDocument=null;
 const key=panel=>panel.dataset.taskId+':'+panel.dataset.assetId;
 const stateFor=panel=>{
   const id=key(panel);
   if(!drafts.has(id)) drafts.set(id,{count:panel.querySelector('.result-batch-count').value,review:panel.querySelector('.result-batch-review').checked,text:'',error:false});
   return drafts.get(id);
 };
 const setText=(element,text)=>{if(element && element.textContent!==text) element.textContent=text;};
 const draw=panel=>{
   const draft=stateFor(panel), en=panel.dataset.lang==='en';
   const count=panel.querySelector('.result-batch-count'), review=panel.querySelector('.result-batch-review');
   if(count.value!==draft.count) count.value=draft.count;
   review.checked=draft.review;
   const n=Number(draft.count), rounds=Math.max(Number(panel.dataset.rounds),n || 1);
   const note=draft.review
     ? (en ? `Target ${n || 1} approved images · score ≥ ${panel.dataset.threshold} · up to ${rounds} attempts, within the original budget.` : `目标 ${n || 1} 张合格图 · 分数线 ${panel.dataset.threshold} · 最多尝试 ${rounds} 次，仍受原预算限制。`)
     : (en ? `Generate ${n || 1} images and save directly, without cloud review.` : `生成 ${n || 1} 张后直接保存，不调用云端审查。`);
   setText(panel.querySelector('.result-batch-note'),note+(en ? ' Same prompt, model and output folder; new seeds.' : ' 沿用此图提示词、模型和输出目录，逐张换种子。'));
   const feedback=panel.querySelector('.result-batch-feedback');
   setText(feedback,draft.text);
   feedback.classList.toggle('error',draft.error);
   count.setAttribute('aria-invalid',String(draft.error && (!Number.isInteger(n) || n<1 || n>50)));
   const button=panel.querySelector('.result-batch-start');
   if(!button.dataset.unavailable) button.dataset.unavailable=String(button.disabled);
   button.disabled=button.dataset.unavailable==='true' || !!pending;
   setText(button,pending?.key===key(panel) ? (en?'Adding…':'正在加入队列…') : (en?'Generate batch':'批量产出'));
   const edit=panel.querySelector('.result-prompt-open');
   if(edit) {
     edit.disabled=!!pendingPrompt;
     setText(edit,pendingPrompt?.key===key(panel) && pendingPrompt.kind==='prompt' ? (en?'Loading prompts…':'正在载入提示词…') : (en?'Edit in prompt studio':'转到提示词生图'));
   }
   const discuss=panel.querySelector('.result-discussion-open');
   if(discuss) {
     discuss.disabled=!!pendingPrompt;
     setText(discuss,pendingPrompt?.key===key(panel) && pendingPrompt.kind==='discussion' ? (en?'Loading prompts…':'正在载入提示词…') : (en?'Discuss this prompt':'转到创作讨论'));
   }
   if(draft.taskId) {
     let view=panel.querySelector('.result-batch-view');
     if(!view) {view=document.createElement('button');view.type='button';view.className='result-batch-view';panel.querySelector('.result-batch-controls').appendChild(view);}
     setText(view,en?'View batch results':'查看本次产出');
   }
 };
 const preview=()=>{
   const gallery=document.getElementById('result-gallery'), host=gallery?.querySelector('.preview');
   if(!host) {footerKey='';footerMarkup='';return;}
   const raw=document.querySelector('#result-image-map textarea, #result-image-map input')?.value || '{}';
   if(raw!==mapRaw) {
     try {imageMap=JSON.parse(raw);reviewDocument=new DOMParser().parseFromString(imageMap.review_html || '', 'text/html');mapRaw=raw;} catch {return;}
   }
   const map=imageMap;
   if(!map || !reviewDocument) return;
   const img=host.querySelector('img'), url=decodeURIComponent(img?.currentSrc || img?.src || '');
   const record=(map.images || []).find(item=>url.includes(item.filename));
   const card=record && reviewDocument.querySelector(`.result-review-card[data-asset-id="${record.asset_id}"]`);
   let footer=host.querySelector(':scope > .result-preview-footer');
   if(!card) {footer?.remove();footerKey='';footerMarkup='';return;}
   const clone=card.cloneNode(true), panel=clone.querySelector('.result-batch-panel');
   panel?.remove();
   const details=document.createElement('section');details.className='result-selected-details';
   const heading=document.createElement('h3');heading.textContent=clone.querySelector('summary').textContent;
   details.appendChild(heading);
   clone.querySelector(':scope > summary').remove();
   const prompt=clone.querySelector('.round-prompt');
   if(prompt) {
     const title=document.createElement('h4');title.textContent=prompt.querySelector('summary').textContent;
     prompt.querySelector('summary').remove();
     prompt.replaceWith(title,...prompt.childNodes);
   }
   details.append(...clone.childNodes);
   const markup=details.outerHTML;
   const id=(record.task_id || map.task_id)+':'+record.asset_id;
   if(footer && footerKey===id && footerMarkup===markup) return;
   const scroll=footerKey===id ? footer?.querySelector('.result-selected-details')?.scrollTop || 0 : 0;
   footer?.remove();
   footer=document.createElement('div'); footer.className='result-preview-footer';
   footer.appendChild(details);
   if(panel) footer.appendChild(panel);
   host.appendChild(footer); footerKey=id;footerMarkup=markup;
   details.scrollTop=scroll;
 };
 const render=()=>{
   queued=false;
   let response;
   try {response=JSON.parse(document.querySelector('#result-batch-response textarea, #result-batch-response input')?.value || '{}');} catch {}
   if(pending && response?.request_id===pending.id) {
     const draft=drafts.get(pending.key);
     draft.text=response.text;draft.error=!response.ok;draft.retry=null;
     if(response.ok) {draft.taskId=response.task_id;draft.requestId=response.request_id;}
     pending=null;
   }
   let promptResponse;
   try {promptResponse=JSON.parse(document.querySelector(pendingPrompt?.kind==='discussion' ? '#result-discussion-response textarea, #result-discussion-response input' : '#result-prompt-response textarea, #result-prompt-response input')?.value || '{}');} catch {}
   if(pendingPrompt && promptResponse?.request_id===pendingPrompt.id) {
     const draft=drafts.get(pendingPrompt.key);
     draft.text=promptResponse.ok?'':promptResponse.text;draft.error=!promptResponse.ok;
     pendingPrompt=null;
   }
   preview();
   document.querySelectorAll('.result-batch-panel').forEach(draw);
 };
 const schedule=()=>{if(!queued) {queued=true;requestAnimationFrame(render);}};
 document.addEventListener('input',event=>{
   const panel=event.target.closest?.('.result-batch-panel');
   if(!panel) return;
   const draft=stateFor(panel);
   if(event.target.matches('.result-batch-count')) draft.count=event.target.value;
   if(event.target.matches('.result-batch-review')) draft.review=event.target.checked;
   draft.text='';draft.error=false;schedule();
 });
 document.addEventListener('click',event=>{
   const edit=event.target.closest?.('.result-prompt-open,.result-discussion-open');
   if(edit) {
     event.preventDefault();event.stopPropagation();
     if(edit.disabled || pendingPrompt) return;
     const isDiscussion=edit.classList.contains('result-discussion-open');
     const panel=edit.closest('.result-batch-panel'),input=document.querySelector(isDiscussion ? '#result-discussion-request textarea, #result-discussion-request input' : '#result-prompt-request textarea, #result-prompt-request input');
     if(!input) return;
     const id=crypto.randomUUID(),draft=stateFor(panel);
     pendingPrompt={id,key:key(panel),kind:isDiscussion?'discussion':'prompt'};draft.text='';draft.error=false;
     const gallery=document.getElementById('result-gallery');
     if(gallery?.classList.contains('fullscreen')) gallery.querySelector('button[aria-label="Exit fullscreen mode"]')?.click();
     const prototype=input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
     Object.getOwnPropertyDescriptor(prototype,'value').set.call(input,JSON.stringify({task_id:panel.dataset.taskId,asset_id:panel.dataset.assetId,request_id:id}));
     input.dispatchEvent(new Event('input',{bubbles:true}));
     document.getElementById(isDiscussion?'result-discussion-trigger':'result-prompt-trigger')?.click();schedule();
     setTimeout(()=>{
       if(pendingPrompt?.id!==id) return;
       pendingPrompt=null;draft.text='提示词载入超时，请重试。';draft.error=true;schedule();
     },30000);
     return;
   }
   const view=event.target.closest?.('.result-batch-view');
   if(view) {
     event.preventDefault();event.stopPropagation();
     const panel=view.closest('.result-batch-panel'), draft=stateFor(panel);
     const input=document.querySelector('#result-batch-view-request textarea, #result-batch-view-request input');
     if(!input || !draft.requestId) return;
     const prototype=input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
     Object.getOwnPropertyDescriptor(prototype,'value').set.call(input,JSON.stringify({task_id:panel.dataset.taskId,asset_id:panel.dataset.assetId,request_id:draft.requestId}));
     input.dispatchEvent(new Event('input',{bubbles:true}));
     document.getElementById('result-batch-view-trigger')?.click();return;
   }
   const button=event.target.closest?.('.result-batch-start');
   if(!button) return;
   event.preventDefault();event.stopPropagation();
   if(button.disabled || pending) return;
   const panel=button.closest('.result-batch-panel'), draft=stateFor(panel), n=Number(draft.count);
   if(!Number.isInteger(n) || n<1 || n>50) {
     draft.text=panel.dataset.lang==='en'?'Enter a whole number from 1 to 50.':'请输入 1–50 的整数张数。';draft.error=true;
     draw(panel);panel.querySelector('.result-batch-count').focus();return;
   }
   const input=document.querySelector('#result-batch-request textarea, #result-batch-request input');
   if(!input) return;
   const signature=JSON.stringify([n,draft.review]);
   const id=draft.retry?.signature===signature ? draft.retry.id : crypto.randomUUID();
   draft.retry={id,signature};pending={id,key:key(panel)};draft.text='';draft.error=false;
   const request=JSON.stringify({task_id:panel.dataset.taskId,asset_id:panel.dataset.assetId,count:n,review:draft.review,request_id:id});
   const prototype=input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
   Object.getOwnPropertyDescriptor(prototype,'value').set.call(input,request);
   input.dispatchEvent(new Event('input',{bubbles:true}));
   document.getElementById('result-batch-trigger')?.click();
   schedule();
   setTimeout(()=>{
     if(pending?.id!==id) return;
     draft.text='响应超时，可重试同一请求；不会重复创建任务。';draft.error=true;pending=null;schedule();
   },90000);
 },true);
 new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});
 document.addEventListener('load',schedule,true);
 setInterval(schedule,700);
 schedule();
}"""
