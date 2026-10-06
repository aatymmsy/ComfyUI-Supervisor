"""Consistent upload affordances and whole-frame file drop handling.

Files still pass through Gradio's input/upload pipeline and server validation.
"""

UPLOAD_CSS = r"""
.supervisor-upload {border-style:dashed !important; transition:border-color .15s,background-color .15s;}
.supervisor-upload [data-testid=block-label] {font-size:13px !important;}
.supervisor-image-upload [data-testid=block-label] > span {display:none;}
.supervisor-image-upload [data-testid=block-label]::before,
.supervisor-image-upload .empty::before,
.supervisor-upload [data-testid=upload-text]::before {
  content:''; display:block; width:26px; height:26px; background:currentColor;
  mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='3' width='18' height='18' rx='2'/%3E%3Ccircle cx='8.5' cy='8.5' r='1.5'/%3E%3Cpath d='m21 15-5-5L5 21'/%3E%3C/svg%3E") center/contain no-repeat;
}
.supervisor-image-upload [data-testid=block-label]::before {display:inline-block; width:15px; height:15px; margin-right:6px; vertical-align:-2px;}
.supervisor-image-upload .empty {display:flex !important; flex-direction:column; justify-content:center; align-items:center; gap:8px; padding:20px 8px 8px !important; box-sizing:border-box;}
.supervisor-image-upload .empty .icon {display:none !important;}
.supervisor-image-upload .empty::after {content:'当前入口未启用'; font-size:13px; line-height:20px;}
.supervisor-image-upload[data-upload-lang=en] .empty::after {content:'This upload is disabled'; font-size:12px;}
.supervisor-upload [data-testid=upload-text] {display:flex !important; flex-direction:column; align-items:center; justify-content:center; gap:8px; font-size:0 !important; line-height:0 !important; min-width:0; width:100%;}
.supervisor-upload [data-testid=upload-text] > * {display:none !important;}
.supervisor-upload [data-testid=upload-text]::after {content:'拖入图片或点击上传'; font-size:13px; font-weight:400; line-height:20px; white-space:nowrap;}
#workflow-api-upload [data-testid=upload-text]::after {content:'拖入 JSON 或点击上传';}
#workflow-api-upload [data-testid=upload-text]::before {mask-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6'/%3E%3C/svg%3E");}
.supervisor-upload[data-upload-lang=en] [data-testid=upload-text]::after {content:'Drop images or click to upload'; font-size:12px;}
#workflow-api-upload[data-upload-lang=en] [data-testid=upload-text]::after {content:'Drop JSON or click to upload';}
.supervisor-upload .upload-container {padding:20px 8px 8px !important; box-sizing:border-box !important;}
.supervisor-upload.upload-drag-active {border-color:var(--color-accent,#287b62) !important; background:color-mix(in srgb,var(--color-accent,#287b62) 12%,transparent) !important; outline:2px solid var(--color-accent,#287b62); outline-offset:-2px;}
.supervisor-upload.upload-drag-invalid {border-color:#d15d55 !important; outline:2px solid #d15d55; outline-offset:-2px;}
#supervisor-upload-feedback {position:fixed; bottom:24px; left:50%; transform:translateX(-50%); z-index:10000; max-width:calc(100vw - 32px); padding:10px 16px; border:1px solid var(--border-color-primary,#777); border-radius:8px; background:var(--background-fill-primary,#fff); color:var(--body-text-color,#222); box-shadow:0 4px 20px #0002; font-size:13px; pointer-events:none;}
#supervisor-upload-feedback[hidden] {display:none;}
"""

UPLOAD_JS = r"""() => {
  if (window.supervisorUploadInstalled) return;
  window.supervisorUploadInstalled = true;
  const selector = '.supervisor-upload';
  const busy = new WeakSet();
  let feedbackTimer;
  const english = () => window.supervisorLanguage === 'en';
  const message = (zh, en) => {
    let box = document.getElementById('supervisor-upload-feedback');
    if (!box) {
      box = document.createElement('div'); box.id = 'supervisor-upload-feedback';
      box.setAttribute('role', 'status'); box.setAttribute('aria-live', 'polite');
      document.body.appendChild(box);
    }
    box.textContent = english() ? en : zh; box.hidden = false;
    clearTimeout(feedbackTimer); feedbackTimer = setTimeout(() => { box.hidden = true; }, 4500);
  };
  const disabled = root => root.classList.contains('caption-disabled') || Boolean(root.querySelector('input[type=file]')?.disabled);
  const findRoot = event => {
    const root = event.target?.closest?.(selector);
    if (root) return root;
    // A disabled control uses pointer-events:none; its containing column gets the event.
    return Array.from(document.querySelectorAll(selector)).find(node => {
      const r = node.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && event.clientX >= r.left && event.clientX <= r.right && event.clientY >= r.top && event.clientY <= r.bottom;
    });
  };
  const fileDrag = event => Array.from(event.dataTransfer?.types || []).includes('Files') || Boolean(event.dataTransfer?.files?.length);
  const reset = () => document.querySelectorAll('.upload-drag-active,.upload-drag-invalid').forEach(root => root.classList.remove('upload-drag-active', 'upload-drag-invalid'));
  const decorate = () => {
    for (const root of document.querySelectorAll(selector)) {
      const lang = english() ? 'en' : 'zh';
      if (root.dataset.uploadLang !== lang) root.dataset.uploadLang = lang;
      const button = root.querySelector('.upload-container');
      const label = root.id === 'workflow-api-upload' ? (english() ? 'Drop JSON or click to upload' : '拖入 JSON 或点击上传') : (english() ? 'Drop images or click to upload' : '拖入图片或点击上传');
      if (button && button.getAttribute('aria-label') !== label) button.setAttribute('aria-label', label);
    }
  };
  decorate();
  new MutationObserver(decorate).observe(document.body, {childList:true, characterData:true, subtree:true});
  const inputAfterClear = root => new Promise((resolve, reject) => {
    const clear = root.querySelector('button[aria-label="Clear"],button[aria-label="清除"],button[title="Clear"],button[title="清除"]');
    if (!clear) { reject(new Error('missing input')); return; }
    let timer;
    const observer = new MutationObserver(() => {
      const input = root.querySelector('input[type=file]');
      if (input) { observer.disconnect(); clearTimeout(timer); resolve(input); }
    });
    observer.observe(root, {childList:true, subtree:true});
    timer = setTimeout(() => { observer.disconnect(); reject(new Error('input timeout')); }, 5000);
    clear.click();
  });
  const receive = async (root, files) => {
    if (disabled(root)) { message('此上传入口尚未启用，请使用其他入口。', 'This upload is disabled. Please use another entry.'); return; }
    if (busy.has(root)) { message('正在切换上传文件，请稍后再拖入。', 'The upload is being replaced. Please try again shortly.'); return; }
    const multiple = ['studio-references', 'manual-references'].includes(root.id);
    const json = root.id === 'workflow-api-upload';
    if (!multiple && files.length !== 1) { message('此处每次只能上传一个文件。', 'Drop one file at a time here.'); return; }
    const valid = file => json ? /\.json$/i.test(file.name) : multiple ? (/^image\//i.test(file.type) || /\.(png|jpe?g|webp|gif|bmp|tiff?|avif|ico)$/i.test(file.name)) : /\.(png|jpe?g|webp)$/i.test(file.name);
    if (files.some(file => !valid(file))) { message(json ? '请上传 JSON 工作流文件。' : '文件格式不支持，请上传此入口支持的图片。', json ? 'Please upload a JSON workflow.' : 'Please upload images supported by this entry.'); return; }
    if (files.some(file => file.size > 100 * 1024 * 1024)) { message('单个文件不能超过 100 MB。', 'Each file must be at most 100 MB.'); return; }
    busy.add(root);
    try {
      const input = root.querySelector('input[type=file]') || await inputAfterClear(root);
      if (!root.isConnected || disabled(root)) throw new Error('inactive input');
      const transfer = new DataTransfer(); files.forEach(file => transfer.items.add(file));
      input.value = ''; input.files = transfer.files;
      input.dispatchEvent(new Event('change', {bubbles:true}));
      message(`已接收 ${files.length} 个文件，正在上传。`, `Received ${files.length} file(s). Uploading.`);
    } catch {
      message('未能启动上传，请点击上传框重试。', 'Could not start the upload. Please click the upload box to retry.');
    } finally { busy.delete(root); }
  };
  for (const name of ['dragenter', 'dragover']) document.addEventListener(name, event => {
    if (!fileDrag(event)) return;
    event.preventDefault();
    const root = findRoot(event); reset();
    if (root) { const off = disabled(root); root.classList.add(off ? 'upload-drag-invalid' : 'upload-drag-active'); event.dataTransfer.dropEffect = off ? 'none' : 'copy'; }
    else event.dataTransfer.dropEffect = 'none';
  }, true);
  document.addEventListener('dragleave', event => {
    const root = event.target?.closest?.(selector);
    if (!event.relatedTarget || (root && !root.contains(event.relatedTarget))) reset();
  }, true);
  document.addEventListener('dragend', reset, true);
  document.addEventListener('drop', event => {
    if (!fileDrag(event)) return;
    const root = findRoot(event); const files = Array.from(event.dataTransfer.files || []);
    event.preventDefault(); event.stopImmediatePropagation(); reset();
    if (!root) { message('请将文件拖到上传框内。', 'Please drop files inside an upload box.'); return; }
    if (!files.length) { message('没有收到文件，请从文件夹拖入或点击上传。', 'No file received. Drag from a folder or click to upload.'); return; }
    void receive(root, files);
  }, true);
}"""
