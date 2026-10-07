"""Layout and small-range pointer snapping for the generation workspace."""

from .models import PASS_SCORE

STUDIO_VIEW_CSS = """
.gradio-container { max-width: 1600px !important; padding: 24px 4vw !important; }
#editor-page, #results-page { gap: 20px !important; }
#editor-page .row { gap: 20px !important; }
#editor-page textarea { line-height: 1.65 !important; }
#editor-page .block:not(.gr-accordion) { padding: 16px !important; }
#top-toolbar { flex-wrap: wrap; }
#open-results { margin-left: auto !important; }
.results-nav { align-items: center !important; justify-content: space-between; }
#results-title { text-align: right; }
#result-gallery { padding:0 !important; width:100% !important; min-width:0 !important; box-sizing:border-box !important; }
#result-gallery .gallery-container {width:100% !important; height:100% !important; min-height:0 !important; overflow:hidden; isolation:isolate; border-radius:inherit;}
#result-gallery .grid-wrap {width:100% !important; height:100% !important; min-height:0 !important; box-sizing:border-box;}
#result-gallery .preview {position:absolute !important; inset:0 !important; width:100% !important; height:100% !important; max-width:none !important; box-sizing:border-box; background:var(--background-fill-primary) !important;}
#result-gallery .preview > .icon-button-wrapper.top-panel {top:16px !important; right:16px !important; gap:12px !important; padding:6px !important; border-radius:12px !important; background:var(--background-fill-primary) !important; box-shadow:0 2px 10px #0002; opacity:1 !important;}
#result-gallery .preview > .icon-button-wrapper.top-panel .icon-button {width:48px !important; height:48px !important; min-width:48px !important; padding:12px !important; flex:0 0 48px !important; border-radius:8px !important; box-sizing:border-box !important;}
#result-gallery .preview > .icon-button-wrapper.top-panel .icon-button svg {width:24px !important; height:24px !important;}
#result-gallery .preview > .icon-button-wrapper.top-panel .icon-button > div {width:24px !important; height:24px !important; min-width:24px !important; flex:0 0 24px !important;}
#result-gallery .preview .media-button > .result-delete-icon {top:92px !important; right:22px !important; width:48px !important; height:48px !important; padding:12px !important; border-radius:9px !important;}
#result-gallery .preview .media-button > .result-delete-icon svg {width:24px !important; height:24px !important;}
#result-gallery .preview ~ .grid-wrap {visibility:hidden !important; pointer-events:none !important;}
#result-gallery.fullscreen {inset:0 !important; width:100vw !important; height:100dvh !important; max-width:none !important; border:0 !important; border-radius:0 !important; z-index:10000 !important; background:var(--background-fill-primary) !important;}
#result-gallery.fullscreen .gallery-container {border-radius:0 !important;}
body:has(#result-gallery.fullscreen) {overflow:hidden;}
#results-page:has(> #result-gallery.fullscreen) > :not(#result-gallery) {visibility:hidden !important; pointer-events:none !important;}
.supervisor-image-lightbox {position:fixed; inset:0; z-index:11000; display:flex; align-items:center; justify-content:center; padding:5vh 5vw; background:rgba(15,23,42,.48); backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);}
.supervisor-image-panel {position:relative; display:flex; flex-direction:column; width:90vw; height:90dvh; border-radius:18px; border:1px solid #ffffff38; box-shadow:0 24px 90px #0008; overflow:hidden; background:var(--background-fill-primary);}
.supervisor-image-toolbar {display:flex; align-items:center; justify-content:space-between; flex:0 0 56px; padding:4px 12px 4px 20px; gap:12px; color:var(--body-text-color); font-size:13px;}
.supervisor-image-close {width:48px; height:48px; border:0; border-radius:8px; font-size:28px; color:var(--body-text-color); background:var(--button-secondary-background-fill); cursor:pointer;}
.supervisor-image-canvas > img {display:block; width:100%; min-height:0; flex:1 1 0; object-fit:contain; padding:0 12px 12px; box-sizing:border-box;}
.supervisor-image-canvas {position:relative; display:flex; flex:1 1 0; min-height:0; overflow:hidden;}
.supervisor-image-canvas > img {padding:0 64px 12px;}
.supervisor-image-prev,.supervisor-image-next {position:absolute; top:50%; transform:translateY(-50%); z-index:1; width:48px; height:64px; border:1px solid var(--border-color-primary); border-radius:10px; background:var(--button-secondary-background-fill); color:var(--body-text-color); font-size:36px; cursor:pointer;}
.supervisor-image-prev {left:8px;} .supervisor-image-next {right:8px;}
.supervisor-image-prev:disabled,.supervisor-image-next:disabled {opacity:.3; cursor:default;}
.supervisor-image-caption {overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
#result-gallery img {cursor:zoom-in;}
body:has(.supervisor-image-lightbox) {overflow:hidden;}
.pass-slider input[type=range] { margin-top: 24px !important; }
.pass-marker { position: absolute; width: 0; height: 16px; border-left: 2px dashed #92989d; pointer-events: none; z-index: 2; }
.pass-marker span { position: absolute; top: -21px; left: 0; transform: translateX(-50%); white-space: nowrap; font-size: 12px; color: #7e858b; }
@media(max-width:700px) {
 .gradio-container { padding: 16px !important; }
 #editor-page .block:not(.gr-accordion) { padding: 12px !important; }
 #result-gallery:not(.fullscreen) { height: 70vh !important; }
}
"""

STUDIO_VIEW_JS = """() => {
 const passScore = __PASS_SCORE__;
 const mark = () => {
   document.querySelectorAll('.pass-slider input[type=range]').forEach(input => {
     const parent = input.parentElement;
     let marker = parent.querySelector('.pass-marker');
     if (!marker) {
       parent.style.position = 'relative';
       marker = document.createElement('div'); marker.className = 'pass-marker';
       marker.appendChild(document.createElement('span')); parent.appendChild(marker);
     }
     const label = window.supervisorLanguage === 'en' ? `Pass · ${passScore}` : `及格线 · ${passScore}`;
     if (marker.firstChild.textContent !== label) marker.firstChild.textContent = label;
     // Allow for the native thumb radius, so the mark matches the pass score.
     marker.style.left = `${input.offsetLeft + 8 + (input.clientWidth - 16) * passScore / 100}px`;
     marker.style.top = `${input.offsetTop + input.clientHeight / 2 - 8}px`;
   });
 };
 if (!window.supervisorPassInstalled) {
   window.supervisorPassInstalled = true;
   let pointerSlider = null;
   document.addEventListener('click', e => {
     const tab = e.target.closest?.('#editor-page [role=tab]');
     if (tab) window.supervisorReturnTab = tab.dataset.tabId;
   }, true);
   document.addEventListener('pointerdown', e => {
     pointerSlider = e.target.matches?.('.pass-slider input[type=range]') ? e.target : null;
   }, true);
   document.addEventListener('input', e => {
     // Capture before the component handler; only dragging snaps, typing and keys stay precise.
     if (e.target === pointerSlider && Math.abs(Number(e.target.value) - passScore) <= 1) e.target.value = String(passScore);
   }, true);
   document.addEventListener('pointerup', () => { pointerSlider = null; }, true);
   document.addEventListener('pointercancel', () => { pointerSlider = null; }, true);
   new MutationObserver(mark).observe(document.body, {childList:true, subtree:true});
   window.addEventListener('resize',mark);
 }
 mark();
 return [];
}""".replace('__PASS_SCORE__', str(PASS_SCORE))


RESULT_PREVIEW_JS = """() => {
 if(window.supervisorResultPreviewInstalled) return;
 window.supervisorResultPreviewInstalled=true;
 window.supervisorPreviewClosed=false;
 let opened=false,previousY=0,origin=null,queued=false,lightbox=null,enlargeOrigin=null,currentId=null,navigating=false;
 const images=()=>{try{return JSON.parse(document.querySelector('#result-image-map textarea, #result-image-map input')?.value || '{}').images || [];}catch{return [];}};
 const recordFor=image=>{
   let url=image?.currentSrc || image?.src || '';
   try{url=decodeURIComponent(url);}catch{}
   return images().find(item=>url.includes(item.filename));
 };
 const thumbnailFor=record=>Array.from(document.querySelectorAll('#result-gallery .grid-wrap button[aria-label^="Thumbnail"]')).find(button=>recordFor(button.querySelector('img'))?.asset_id===record.asset_id);
 const closeLightbox=()=>{
   lightbox?.remove();lightbox=null;currentId=null;
   if(enlargeOrigin?.isConnected) enlargeOrigin.focus({preventScroll:true});
 };
 const show=record=>{
   const thumbnail=thumbnailFor(record);
   const image=thumbnail?.querySelector('img') || document.querySelector('#result-gallery .preview > .media-button img');
   if(!image || recordFor(image)?.asset_id!==record.asset_id) return false;
   currentId=record.asset_id;
   const copy=lightbox.querySelector('.supervisor-image-canvas img');
   const src=image.currentSrc || image.src;
   if(copy.src!==src) copy.src=src;
   copy.alt=record.caption || '';
   const list=images(),index=list.findIndex(item=>item.asset_id===currentId);
   const caption=lightbox.querySelector('.supervisor-image-caption');
   const text=`${index+1} / ${list.length} · ${record.caption || ''}`;
   if(caption.textContent!==text) caption.textContent=text;
   lightbox.querySelector('.supervisor-image-prev').disabled=index<=0;
   lightbox.querySelector('.supervisor-image-next').disabled=index>=list.length-1;
   return true;
 };
 const move=direction=>{
   const list=images(),index=list.findIndex(item=>item.asset_id===currentId),record=list[index+direction];
   const focused=document.activeElement;
   if(index<0 || !record || !show(record)) return;
   const thumbnail=thumbnailFor(record);
   if(thumbnail){navigating=true;thumbnail.click();navigating=false;}
   requestAnimationFrame(()=>{
     if(!lightbox) return;
     (lightbox.contains(focused) && !focused.disabled ? focused : lightbox.querySelector('.supervisor-image-close')).focus({preventScroll:true});
   });
 };
 const enlarge=(image,button)=>{
   const record=recordFor(image);
   if(!record) return;
   if(lightbox){show(record);return;}
   enlargeOrigin=button;
   const en=window.supervisorLanguage==='en';
   lightbox=document.createElement('div');lightbox.className='supervisor-image-lightbox';
   lightbox.setAttribute('role','dialog');lightbox.setAttribute('aria-modal','true');lightbox.setAttribute('aria-label',en?'Enlarged image':'放大图片');
   const panel=document.createElement('div');panel.className='supervisor-image-panel';
   const toolbar=document.createElement('div');toolbar.className='supervisor-image-toolbar';
   const caption=document.createElement('span');caption.className='supervisor-image-caption';caption.setAttribute('aria-live','polite');
   const close=document.createElement('button');close.type='button';close.className='supervisor-image-close';close.textContent='×';close.setAttribute('aria-label',en?'Close enlarged image':'关闭放大图片');close.addEventListener('click',closeLightbox);
   toolbar.append(caption,close);
   const canvas=document.createElement('div');canvas.className='supervisor-image-canvas';
   const copy=document.createElement('img');
   const nav=(direction,label,symbol)=>{
     const button=document.createElement('button');button.type='button';button.className=direction<0?'supervisor-image-prev':'supervisor-image-next';button.textContent=symbol;button.setAttribute('aria-label',label);button.addEventListener('click',()=>move(direction));return button;
   };
   canvas.append(nav(-1,en?'Previous image':'上一张','‹'),copy,nav(1,en?'Next image':'下一张','›'));
   panel.append(toolbar,canvas);lightbox.append(panel);
   lightbox.addEventListener('click',event=>{if(event.target===lightbox) closeLightbox();});
   document.body.append(lightbox);show(record);close.focus({preventScroll:true});
 };
 const update=()=>{
   queued=false;
   const gallery=document.getElementById('result-gallery');
   const visible=!!gallery && gallery.getBoundingClientRect().height>0;
   const preview=visible && gallery.querySelector('.preview');
   if(preview && window.supervisorPreviewClosed){preview.style.visibility='hidden';preview.querySelector('button[aria-label="Close"]')?.click();return;}
   if(preview) preview.style.visibility='';
   if(lightbox){
     const record=images().find(item=>item.asset_id===currentId);
     if(!visible || !record) closeLightbox(); else show(record);
   }
   if(preview && !opened){
     opened=true;
     if(!lightbox){gallery.scrollIntoView({behavior:'auto',block:'start'});(preview.querySelector('button[aria-label="Close"]') || preview.querySelector('.media-button'))?.focus({preventScroll:true});}
     else lightbox.querySelector('.supervisor-image-close').focus({preventScroll:true});
   }else if(!preview && opened){
     opened=false;
     if(visible && !lightbox){window.scrollTo({top:previousY,behavior:'auto'});if(origin?.isConnected) origin.focus({preventScroll:true});}
   }
 };
 const schedule=()=>{if(!queued){queued=true;requestAnimationFrame(update);}};
 document.addEventListener('click',event=>{
   const previewImage=event.target.matches?.('#result-gallery .preview > .media-button img') && event.target;
   if(previewImage){event.preventDefault();event.stopImmediatePropagation();enlarge(previewImage,previewImage.closest('button'));return;}
   const close=event.target.closest?.('#result-gallery .preview button[aria-label="Close"]');
   if(close){window.supervisorPreviewClosed=true;closeLightbox();}
   const thumbnail=event.target.closest?.('#result-gallery .grid-wrap button[aria-label^="Thumbnail"]');
   if(thumbnail && !event.target.closest?.('.result-delete-icon, .result-retry-icon')){
     window.supervisorPreviewClosed=false;
     if(!opened && (event.isTrusted || !origin?.isConnected)){previousY=window.scrollY;origin=thumbnail;}
     if(!navigating) enlarge(thumbnail.querySelector('img'),thumbnail);
   }
 },true);
 document.addEventListener('keydown',event=>{
   if(lightbox){
     if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();closeLightbox();}
     else if(event.key==='ArrowLeft' || event.key==='ArrowRight'){event.preventDefault();event.stopImmediatePropagation();move(event.key==='ArrowLeft'?-1:1);}
     else if(event.key==='Tab'){
       const controls=Array.from(lightbox.querySelectorAll('button:not([disabled])')),index=controls.indexOf(document.activeElement);
       event.preventDefault();event.stopImmediatePropagation();controls[(index+(event.shiftKey?-1:1)+controls.length)%controls.length].focus();
     }
     return;
   }
   const gallery=document.getElementById('result-gallery');
   if(event.key==='Escape' && gallery?.querySelector('.preview')){event.preventDefault();event.stopImmediatePropagation();gallery.querySelector('button[aria-label="Close"]')?.click();}
 },true);
 document.addEventListener('focusin',event=>{
   // Native gallery updates can focus its thumbnail after our image switch.
   if(lightbox && !lightbox.contains(event.target)) lightbox.querySelector('.supervisor-image-close').focus({preventScroll:true});
 },true);
 new MutationObserver(()=>{
   const preview=document.querySelector('#result-gallery .preview');
   if(preview && window.supervisorPreviewClosed) preview.style.visibility='hidden';
   schedule();
 }).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['class','src']});
 document.addEventListener('input',event=>{if(event.target.closest?.('#result-image-map')) schedule();});
 schedule();
}
"""
