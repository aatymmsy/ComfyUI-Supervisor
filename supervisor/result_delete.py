RESULT_DELETE_CSS = """
.result-delete-bridge { display:none !important; }
#result-gallery .result-delete-host { position:relative; }
#result-gallery .result-delete-icon { position:absolute; right:8px; top:8px; z-index:12; width:28px; height:28px; padding:5px; display:flex; align-items:center; justify-content:center; border:0; border-radius:7px; color:#fff; background:#dc2626; box-shadow:0 1px 5px #0003; cursor:pointer; opacity:.94; }
#result-gallery .result-delete-icon:hover { background:#b91c1c; opacity:1; }
#result-gallery .result-delete-icon:focus-visible { outline:2px solid #fff; outline-offset:2px; }
#result-gallery .result-delete-icon:disabled { opacity:.45; cursor:wait; }
#result-gallery .result-delete-icon svg { width:18px; height:18px; pointer-events:none; }
"""

RESULT_DELETE_JS = r"""() => {
  if (window.supervisorDeleteIcons) return;
  window.supervisorDeleteIcons = true;
  const icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg>';
  let queued = false;
  const render = () => {
    queued = false;
    const gallery = document.getElementById('result-gallery');
    if (!gallery) return;
    let data;
    try { data = JSON.parse(document.querySelector('#result-image-map textarea, #result-image-map input')?.value || '{}'); } catch { return; }
    const images = data.images || [];
    for (const img of document.querySelectorAll('#result-gallery img')) {
      const url = decodeURIComponent(img.currentSrc || img.src || '');
      const record = images.find(item => url.includes(item.filename));
      const host = img.closest('.thumbnail-item, .gallery-item, .image-container, .image-frame') || img.parentElement;
      if (!host) continue;
      const bounds=host.getBoundingClientRect();
      if (!record || bounds.width<80 || bounds.height<80) {
        host.querySelector(':scope > .result-delete-icon')?.remove();
        continue;
      }
      host.classList.add('result-delete-host');
      let button = host.querySelector(':scope > .result-delete-icon');
      if (!button) {
        button = document.createElement('button');
        button.type = 'button';
        button.className = 'result-delete-icon';
        button.innerHTML = icon;
        button.addEventListener('click', event => {
          event.preventDefault(); event.stopPropagation();
          if (button.disabled || window.supervisorDeleteBusy) return;
          const input = document.querySelector('#result-delete-request textarea, #result-delete-request input');
          if (!input) return;
          let live;
          try { live=JSON.parse(document.querySelector('#result-image-map textarea, #result-image-map input')?.value || '{}'); } catch { return; }
          const liveUrl=decodeURIComponent(host.querySelector('img')?.src || '');
          const selected=(live.images || []).find(item => liveUrl.includes(item.filename));
          if (!selected) return;
          window.supervisorDeleteBusy = true;
          document.querySelectorAll('#result-gallery .result-delete-icon').forEach(b => b.disabled=true);
          const request = JSON.stringify({task_id:selected.task_id || live.task_id,asset_id:selected.asset_id,nonce:Date.now()});
          const prototype = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          Object.getOwnPropertyDescriptor(prototype,'value').set.call(input,request);
          input.dispatchEvent(new Event('input',{bubbles:true}));
          document.getElementById('result-delete-trigger')?.click();

        });
        host.appendChild(button);
      }
      button.disabled = !!window.supervisorDeleteBusy;
      button.dataset.taskId = record.task_id || data.task_id;
      button.dataset.assetId = record.asset_id;
      button.title = '删除图片';
      button.setAttribute('aria-label','删除图片 ' + record.asset_id.slice(0,8));
    }
  };
  const schedule = () => { if (!queued) { queued=true; requestAnimationFrame(render); } };
  new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});
  document.addEventListener('input',schedule);
  document.addEventListener('load',schedule,true);
  setInterval(schedule,1000);
  schedule();
}"""
