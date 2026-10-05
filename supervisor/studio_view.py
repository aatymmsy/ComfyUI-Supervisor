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
.supervisor-image-panel > img {display:block; width:100%; min-height:0; flex:1 1 0; object-fit:contain; padding:0 12px 12px; box-sizing:border-box;}
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
 let opened=false, previousY=0, origin=null, queued=false, lightbox=null, enlargeOrigin=null;
 const closeLightbox=()=>{
   lightbox?.remove();lightbox=null;
   if(enlargeOrigin?.isConnected) enlargeOrigin.focus({preventScroll:true});
 };
 const enlarge=button=>{
   const image=document.querySelector('#result-gallery .preview > .media-button img');
   if(!image) return;
   closeLightbox();enlargeOrigin=button;
   const en=window.supervisorLanguage==='en';
   lightbox=document.createElement('div');lightbox.className='supervisor-image-lightbox';
   lightbox.setAttribute('role','dialog');lightbox.setAttribute('aria-modal','true');lightbox.setAttribute('aria-label',en?'Enlarged image':'放大图片');
   const panel=document.createElement('div');panel.className='supervisor-image-panel';
   const toolbar=document.createElement('div');toolbar.className='supervisor-image-toolbar';
   const text=document.createElement('span');text.textContent=en?'Enlarged image · Click the background or press Esc to return to details':'放大查看 · 点击背景或按 Esc 返回评分详情';
   const close=document.createElement('button');close.type='button';close.className='supervisor-image-close';close.textContent='×';close.setAttribute('aria-label',en?'Close enlarged image':'关闭放大图片');
   close.addEventListener('click',closeLightbox);
   toolbar.append(text,close);
   const copy=document.createElement('img');copy.src=image.currentSrc || image.src;copy.alt=image.alt || text.textContent;
   panel.append(toolbar,copy);lightbox.append(panel);
   lightbox.addEventListener('click',event=>{if(event.target===lightbox) closeLightbox();});
   document.body.append(lightbox);close.focus({preventScroll:true});
 };
 const update=()=>{
   queued=false;
   const gallery=document.getElementById('result-gallery');
   const visible=!!gallery && gallery.getBoundingClientRect().height>0;
   const preview=visible && gallery.querySelector('.preview');
   // A response sent before Close may still contain the old selected_index.
   // Hide it before paint and close it; only an explicit thumbnail click reopens.
   if(preview && window.supervisorPreviewClosed) {
     preview.style.visibility='hidden';
     preview.querySelector('button[aria-label="Close"]')?.click();
     return;
   }
   if(preview) preview.style.visibility='';
   const enlargeButton=preview && preview.querySelector('button[aria-label="Fullscreen"],button[data-supervisor-enlarge]');
   if(enlargeButton) {
     enlargeButton.dataset.supervisorEnlarge='true';
     const label=window.supervisorLanguage==='en'?'Enlarge image':'页面内放大图片';
     if(enlargeButton.getAttribute('aria-label')!==label) enlargeButton.setAttribute('aria-label',label);
     if(enlargeButton.title!==label) enlargeButton.title=label;
   }
   if(!preview && lightbox) closeLightbox();
   if(preview && !opened) {
     opened=true;
     if(!gallery.classList.contains('fullscreen')) gallery.scrollIntoView({behavior:'auto',block:'start'});
     (preview.querySelector('button[aria-label="Close"]') || preview.querySelector('.media-button'))?.focus({preventScroll:true});
   } else if(!preview && opened) {
     opened=false;
     if(visible) {
       window.scrollTo({top:previousY,behavior:'auto'});
       if(origin?.isConnected) origin.focus({preventScroll:true});
     }
   }
 };
 const schedule=()=>{if(!queued){queued=true;requestAnimationFrame(update);}};
 document.addEventListener('click',event=>{
   const expand=event.target.closest?.('#result-gallery .preview button[aria-label="Fullscreen"],#result-gallery .preview button[data-supervisor-enlarge]');
   if(expand){event.preventDefault();event.stopImmediatePropagation();enlarge(expand);return;}
   const close=event.target.closest?.('#result-gallery .preview button[aria-label="Close"]');
   if(close) {
     window.supervisorPreviewClosed=true;
     const gallery=document.getElementById('result-gallery');
     if(gallery?.classList.contains('fullscreen')) gallery.querySelector('button[aria-label="Exit fullscreen mode"]')?.click();
   }
   const thumbnail=event.target.closest?.('#result-gallery .grid-wrap button[aria-label^="Thumbnail"]');
   if(thumbnail && !event.target.closest?.('.result-delete-icon, .result-retry-icon')) {
     window.supervisorPreviewClosed=false;
     if(!opened && (event.isTrusted || !origin?.isConnected)) {previousY=window.scrollY;origin=thumbnail;}
   }
 },true);
 document.addEventListener('keydown',event=>{
   if(lightbox) {
     if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();closeLightbox();}
     if(event.key==='Tab'){event.preventDefault();event.stopImmediatePropagation();lightbox.querySelector('button').focus();}
     return;
   }
   const gallery=document.getElementById('result-gallery');
   if(event.key==='Escape' && gallery?.querySelector('.preview')) {
     event.preventDefault();event.stopImmediatePropagation();
     gallery.querySelector('button[aria-label="Close"]')?.click();return;
   }
   if(event.key!=='Tab' || !gallery?.classList.contains('fullscreen')) return;
   const controls=Array.from(gallery.querySelectorAll('button:not([disabled]),input:not([disabled]),summary,a[href],[tabindex="0"]')).filter(el=>el.getBoundingClientRect().width>0 && getComputedStyle(el).visibility!=='hidden');
   if(!controls.length) return;
   const first=controls[0],last=controls.at(-1),current=document.activeElement;
   if(!gallery.contains(current) || (event.shiftKey && current===first) || (!event.shiftKey && current===last)) {
     event.preventDefault();(event.shiftKey?last:first).focus();
   }
 },true);
 new MutationObserver(()=>{
   const preview=document.querySelector('#result-gallery .preview');
   if(preview && window.supervisorPreviewClosed) preview.style.visibility='hidden';
   schedule();
 }).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['class']});
 schedule();
}"""
