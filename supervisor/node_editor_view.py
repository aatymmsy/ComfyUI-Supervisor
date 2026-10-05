"""Grouped workflow parameter navigation; editable values remain server validated."""

from html import escape
import json

MODEL_INPUTS = {'ckpt_name', 'unet_name', 'lora_name', 'vae_name', 'clip_name',
                'clip_name1', 'clip_name2', 'clip_name3', 'model_name'}


def node_name(parameters):
    models = [str(row['value']) for row in parameters if row['input'] in MODEL_INPUTS]
    return ' · '.join(models) if models else ' · '.join(filter(None,(parameters[0].get('title'),parameters[0]['class_type'])))


def parameter_button(row, selected):
    key = escape(row['key'], quote=True)
    name = escape(row['input'])
    active = ' active' if row['key'] == selected else ''
    disabled = ' disabled' if row.get('type')=='DISPLAY' else ''
    return f'<button type="button" class="node-parameter{active}" data-editor-key="{key}"{disabled} aria-pressed="{str(bool(active)).lower()}">{name}</button>'


def node_table(rows, selected=None):
    if not rows:
        return '<p class="workflow-empty">请读取工作流参数。点击表格左侧节点进入编辑区。</p>'
    groups = {}
    for row in rows:
        groups.setdefault(row['node_id'], []).append(row)
    body = []
    for node_id, parameters in groups.items():
        current = next((r for r in parameters if r['key'] == selected), parameters[0])
        active = any(r['key'] == selected for r in parameters)
        buttons = ''.join(f'<div>{parameter_button(r, selected)}<span>{escape(str(r["value"]))}</span></div>' for r in parameters)
        enabled = parameters[0].get('bypassed', False)
        supported = bool(parameters[0].get('bypass_routes')) or enabled
        reason = parameters[0].get('bypass_reason', '')
        bypass = (f'<label class="node-bypass" title="{escape(reason, quote=True) if not supported else "记录绕过选择，提交生图时接通同类型输入输出"}"><input type="checkbox" data-bypass-node="{escape(str(node_id), quote=True)}" '
                  f'{"checked" if enabled else ""} {"disabled" if not supported else ""}><span>{"已选绕过" if enabled else "绕过"}</span></label>'
                  + (f'<small class="bypass-reason">{escape(reason)}</small>' if not supported else ''))
        info = parameters[0].get('switch')
        switch = '<span class="node-no-switch">—</span>'
        if info:
            reason = info['reason']
            value = info['value']
            switch_value = str(value).lower() if value is not None else ''
            switch = (f'<select class="node-switch" data-switch-node="{escape(str(node_id), quote=True)}" data-switch-value="{switch_value}" aria-label="切换节点 {escape(str(node_id), quote=True)}" {"disabled" if reason else ""}>'
                      + ('<option value="" selected>由上游控制</option>' if value is None else '')
                      + f'<option value="true" {"selected" if value else ""}>True · 真</option><option value="false" {"selected" if value is False else ""}>False · 假</option></select>'
                      f'<div class="switch-branches"><div class="{"selected-branch" if value else ""}"><strong>True →</strong> {escape(info["true"])}</div><div class="{"selected-branch" if value is False else ""}"><strong>False →</strong> {escape(info["false"])}</div></div>'
                      + (f'<small class="bypass-reason">{escape(reason)}</small>' if reason else ''))
        body.append(f'<tr class="{"selected-node" if active else ""}"><td><button type="button" class="node-select" data-editor-key="{escape(current["key"], quote=True)}" aria-label="编辑节点 {escape(str(node_id), quote=True)}">{escape(str(node_id))} ↘</button></td>'
                    f'<td>{escape(node_name(parameters))}</td><td><details><summary>{len(parameters)} 个参数 · 展开查看</summary><div class="node-detail">{buttons}</div></details></td><td>{bypass}</td><td>{switch}</td></tr>')
    return '<table class="node-table"><thead><tr><th>节点 / 编辑</th><th>节点 / 当前模型</th><th>参数（按节点折叠）</th><th>绕过此节点</th><th>分支切换（开始任务时生效）</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table>'


def parameter_tabs(rows, selected):
    current = next((r for r in rows if r['key'] == selected), None)
    if not current:
        return ''
    parameters = [r for r in rows if r['node_id'] == current['node_id']]
    title = f'节点 {escape(str(current["node_id"]))} · {escape(node_name(parameters))}'
    buttons = ''.join(parameter_button(r, selected) for r in parameters)
    return f'<div class="node-editor-heading">{title}</div><div class="node-tabs">{buttons}</div><p>当前参数：<strong>{escape(current["input"])}</strong></p>'


def parameter_form(rows, selected, revision):
    current=next((row for row in rows if row['key']==selected),None)
    if not current:
        return '<p>点击表格左侧节点，逐行编辑它的全部参数。</p>'
    parameters=[row for row in rows if row['node_id']==current['node_id']]
    body=[]
    for row in parameters:
        kind=row.get('type','STRING')
        value=row['value']
        attrs=f'data-parameter-key="{escape(row["key"],quote=True)}" data-value-type="{kind}" data-original="{escape(json.dumps(value,ensure_ascii=False),quote=True)}" aria-label="{escape(row["input"],quote=True)}"'
        hint=''
        if row.get('switch') or kind=='DISPLAY':
            control=f'<span>{escape(str(value))}</span>'
            hint='在上方节点表中切换，开始新任务时生效。'
        elif kind=='enum':
            choices=list(row['choices'])
            if value not in choices:
                choices.insert(0,value)
            control='<select '+attrs+'>'+''.join(f'<option value="{escape(str(choice),quote=True)}" {"selected" if choice==value else ""}>{escape(str(choice))}</option>' for choice in choices)+'</select>'
        elif kind in ('INT','FLOAT'):
            limits=' '.join(f'{name}="{row[name]}"' for name in ('min','max') if row.get(name) is not None)
            step=row.get('multiple_of') or (1 if kind=='INT' else 'any')
            control=f'<input type="number" {attrs} value="{value}" {limits} step="{step}">'
            hint=' · '.join(f'{label} {row[name]}' for name,label in (('min','最小'),('max','最大'),('multiple_of','步进')) if row.get(name) is not None)
        elif kind=='BOOLEAN':
            control=f'<input type="checkbox" {attrs} {"checked" if value else ""}>'
        else:
            control=f'<textarea {attrs} rows="1" maxlength="1000">{escape(str(value))}</textarea>'
        body.append(f'<label class="parameter-line"><strong>{escape(row["input"])}</strong><div>{control}<small>{escape(hint)}</small></div></label>')
    return (f'<div class="parameter-form" data-revision="{escape(revision,quote=True)}"><div class="node-editor-heading">节点 {escape(str(current["node_id"]))} · {escape(node_name(parameters))}</div>'
            +'<p class="parameter-help">全部可编辑参数逐行列出。可连续修改多个节点，最后统一保存；种子、提示词和批次由生图表单设置。</p><div class="parameter-lines">'+''.join(body)
            +'</div><button type="button" class="parameter-save">保存修改的参数</button><span class="parameter-dirty" role="status"></span></div>')


def lora_form(options):
    loaders=options['loaders']
    if not loaders or not options['sources']['MODEL']:
        return '<div class="lora-add-panel"><strong>添加 LoRA 节点</strong><p>请先读取工作流，并在 ComfyUI 中安装 LoRA；需要可用的 LoRA 加载器和 MODEL 输出。</p></div>'
    kind='LoraLoader' if 'LoraLoader' in loaders and options['sources']['CLIP'] else next(iter(loaders))
    def select(name,choices,value):
        return f'<select data-lora-field="{name}" aria-label="{name}">'+''.join(f'<option value="{escape(key,quote=True)}" {"selected" if key==value else ""}>{escape(label)}</option>' for key,label in choices)+'</select>'
    parts=[('<strong>添加 LoRA 节点</strong>')]
    parts.append('<label>加载方式'+select('loader',[(key,'模型 + CLIP' if key=='LoraLoader' else '仅模型') for key in loaders],kind)+'</label>')
    parts.append('<label>可添加的本机 LoRA（尚未加载）'+select('lora_name',[(name,name) for name in loaders[kind]],loaders[kind][0])+'</label>')
    for channel,label in (('MODEL','模型接入点'),('CLIP','CLIP 接入点')):
        parts.append(f'<label class="lora-{channel.lower()}">{label}'+select(channel.lower(),options['sources'][channel],options['defaults'][channel])+'</label>')
    for name,label in (('strength_model','模型强度'),('strength_clip','CLIP 强度')):
        parts.append(f'<label class="lora-{name}">{label}<input type="number" value="1" step="any" data-lora-field="{name}" aria-label="{label}"></label>')
    parts.append('<button type="button" class="lora-add-button">添加并保存</button>')
    return (f'<div class="lora-add-panel" data-revision="{escape(options["revision"],quote=True)}" data-loaders="{escape(json.dumps(loaders,ensure_ascii=False),quote=True)}"><div class="lora-add-controls">'
            +''.join(parts)+'</div><p>插入选定接入点与其下游之间；已有 LoRA 可继续串联。新增节点保存后立即显示在表中。</p></div>')


NODE_EDITOR_CSS = """
#editor-field, #editor-bypass-request, #editor-bypass-trigger, #editor-bulk-request, #editor-bulk-trigger { display: none !important; }
#editor-page #editor-table {padding:0 !important;}
#editor-page #editor-lora {padding:0 !important; margin-top:-16px;}
#editor-page :is(#editor-table,#editor-lora) .html-container {padding:0 !important;}
.lora-add-panel {border:1px solid var(--border-color-primary); border-radius:0 0 8px 8px; padding:12px 14px; background:var(--background-fill-secondary);}
.lora-add-controls {display:flex; align-items:end; gap:10px; flex-wrap:wrap;}
.lora-add-controls > strong {align-self:center;}
.lora-add-controls label {display:flex; flex-direction:column; gap:5px; flex:1 1 170px; font-size:13px; min-width:0;}
.lora-add-controls label:has(input) {flex:0 1 110px;}
.lora-add-panel p,.parameter-help {font-size:12px; color:var(--body-text-color-subdued); margin:8px 0;}
.lora-add-controls select,.lora-add-controls input,.parameter-line select,.parameter-line input,.parameter-line textarea {width:100%; min-width:0; padding:8px 10px; border:1px solid var(--border-color-primary); border-radius:6px; background:var(--input-background-fill); color:var(--body-text-color); font:inherit;}
.lora-add-button,.parameter-save {padding:10px 18px; background:var(--button-primary-background-fill); color:var(--button-primary-text-color); border:1px solid var(--button-primary-border-color); border-radius:7px; cursor:pointer;}
.lora-add-button:disabled,.parameter-save:disabled {opacity:.5; cursor:wait;}
.parameter-lines {max-height:540px; overflow:auto; margin:14px 0; padding-right:8px;}
.parameter-line {display:grid; grid-template-columns:minmax(140px,28%) minmax(0,1fr); gap:16px; align-items:center; padding:10px 0; border-bottom:1px solid var(--border-color-primary);}
.parameter-line small {display:block; color:var(--body-text-color-subdued); font-size:12px; margin-top:4px;}
.parameter-line input[type=checkbox] {appearance:auto !important; width:22px; height:22px; accent-color:var(--color-accent);}
.parameter-line :is(input,select,textarea)[data-dirty=true] {border-color:var(--color-accent); box-shadow:0 0 0 1px var(--color-accent);}
.parameter-dirty {margin-left:12px; font-size:13px; color:var(--body-text-color-subdued);}
@media(max-width:600px) {.parameter-line {grid-template-columns:1fr; gap:6px;} }
.node-table th:nth-child(4) {width:125px;}
.node-table th:last-child {width:280px;}
.node-switch {width:100%; max-width:180px; padding:6px 8px; border-radius:6px; color:var(--body-text-color); background:var(--input-background-fill); border:1px solid var(--border-color-primary);}
.node-switch:disabled {opacity:.5;}
.switch-branches {max-height:116px; overflow:auto; font-size:12px; line-height:1.6; margin-top:6px; color:var(--body-text-color-subdued);}
.switch-branches > div {padding:3px 6px; border-left:2px solid transparent; overflow-wrap:anywhere;}
.switch-branches .selected-branch {color:var(--body-text-color); border-left-color:var(--color-accent); background:#42a77a10;}
#editor-page #caption-panel {max-width:720px; gap:12px !important; align-items:center; margin-bottom:8px;}
#editor-page #caption-uploader {flex:0 0 260px !important; min-width:0 !important; width:260px !important; padding:0 !important;}
#editor-page #caption-input {height:112px !important; min-height:112px !important; padding:0 !important;}
#caption-input .upload-container, #caption-input .upload-wrapper {min-height:0 !important; height:100% !important;}
#caption-status {padding:4px 0 !important; font-size:12px; min-width:180px !important;}
@media(max-width:550px) {#editor-page #caption-uploader {flex-basis:220px !important; width:220px !important;} #caption-status {min-width:0 !important;}}
.node-bypass {display:flex; align-items:center; gap:8px; cursor:pointer;}
.node-bypass:has(input:disabled) {opacity:.55; cursor:default;}
.node-bypass input {width:18px; height:18px; accent-color:var(--color-accent);}
.bypass-reason {display:block; margin-top:8px; color:var(--body-text-color-subdued); font-size:12px;}
#editor-page #studio-theme-columns { display:grid !important; grid-template-columns:repeat(3,minmax(0,1fr)); gap:20px !important; align-items:start; }
#studio-theme-columns > .column { min-width:0 !important; width:100% !important; max-width:100% !important; }
#studio-control-words { gap:8px !important; }
#studio-control-words .control-word-line textarea { min-width:0 !important; width:100% !important; }
#studio-control-words .control-word-line .block { min-width:0 !important; }
#editor-page #studio-control-words .control-word-line:not(.hide):not(.hidden):not([hidden]) {display:grid; grid-template-columns:minmax(0,1.3fr) minmax(0,2.2fr) minmax(0,.85fr) minmax(36px,.55fr); align-items:end; gap:8px !important;}
#editor-page #studio-control-words .control-word-line .form {display:contents !important;}
#editor-page #studio-control-words .control-word-line .block {width:100% !important; min-width:0 !important; box-sizing:border-box;}
#editor-page #studio-control-words .control-word-line label > span {height:24px; line-height:24px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-bottom:6px !important;}
#editor-page #studio-control-words .control-word-line textarea {height:64px !important; min-height:64px !important; max-height:64px !important; resize:none !important; overflow-y:auto !important;}
#editor-page #studio-control-words .control-word-delete {align-self:end; width:100% !important; min-width:0 !important; height:64px !important; padding:0 !important; border:1px solid var(--border-color-primary) !important; background:var(--background-fill-secondary) !important; border-radius:10px !important; color:#d65353; font-size:20px; line-height:1;}
#editor-page #studio-lora-triggers {padding:10px 12px !important;}
#studio-lora-triggers textarea {min-height:34px !important;}
#editor-page #studio-character {padding:8px 12px !important;}
#studio-character textarea {min-height:34px !important; max-height:72px !important;}
#studio-control-words .control-word-line {animation:supervisor-row-in .16s ease-out;}
@keyframes supervisor-row-in {from{opacity:0; transform:translateY(-4px)} to{opacity:1; transform:translateY(0)}}
.gradio-container button {transition:background-color .14s ease,border-color .14s ease,box-shadow .14s ease,transform .1s ease;}
.gradio-container button:active {transform:scale(.98);}
.node-bypass input {transition:box-shadow .14s ease;}
.node-bypass input:focus-visible {box-shadow:0 0 0 3px #42a77a33;}
#editor-page,#results-page {animation:supervisor-page-in .18s ease-out;}
@keyframes supervisor-page-in {from{opacity:.6; transform:translateY(4px)} to{opacity:1; transform:translateY(0)}}
.result-review-card[open],.node-detail {animation:supervisor-page-in .14s ease-out;}
@media(prefers-reduced-motion:reduce) {.gradio-container *, .gradio-container *::before, .gradio-container *::after {animation:none !important; transition:none !important;}}
#editor-page #studio-control-words .row { gap:8px !important; flex-wrap:nowrap !important; }
#editor-page #studio-control-words .block { padding:8px !important; }
#control-word-add { align-self:flex-start; width:32px !important; min-width:32px !important; flex:0 0 auto !important; font-size:18px; }
@media(max-width:850px) { #editor-page #studio-theme-columns { grid-template-columns:1fr; } }
.node-table { width:100%; border-collapse:collapse; table-layout:fixed; font-size:14px; }
.node-table th, .node-table td { padding:12px 14px; text-align:left; border-bottom:1px solid var(--border-color-primary); vertical-align:top; overflow-wrap:anywhere; }
.node-table th:first-child { width:110px; }
.node-table th:nth-child(2) { width:28%; }
.node-table .selected-node { background:var(--background-fill-secondary); }
.node-select, .node-parameter { cursor:pointer; padding:7px 12px; border:1px solid var(--border-color-primary); background:var(--button-secondary-background-fill); color:var(--body-text-color); font:inherit; }
.node-select { width:100%; text-align:left; }
.node-table summary { cursor:pointer; color:var(--body-text-color-subdued); }
.node-detail { padding-top:12px; }
.node-detail > div { display:flex; align-items:baseline; gap:12px; margin:6px 0; }
.node-detail span { flex:1; overflow-wrap:anywhere; color:var(--body-text-color-subdued); }
.node-tabs { display:flex; gap:8px; flex-wrap:wrap; margin:14px 0; }
.node-parameter.active { border-color:var(--color-accent); box-shadow:inset 0 0 0 1px var(--color-accent); }
.node-editor-heading { font-size:17px; font-weight:600; }
#editor-controls { scroll-margin-top:20px; padding:20px; border:1px solid var(--border-color-primary); border-radius:8px; }
#editor-page #top-toolbar { gap:12px !important; }
#editor-page #language-switch { padding:0 !important; flex:0 0 180px !important; min-width:180px !important; width:180px !important; }
#editor-page #appearance-controls { flex:0 0 350px !important; width:350px !important; gap:8px !important; flex-wrap:nowrap !important; align-items:center; min-width:350px !important; }
#editor-page #appearance-switch { flex:0 0 266px !important; width:266px !important; min-width:0 !important; padding:0 !important; }
#editor-page #top-toolbar > button { min-width:120px !important; }
#editor-page #appearance-compact { flex:0 0 76px !important; min-width:76px !important; width:76px !important; padding:0 !important; position:relative; overflow:visible !important; }
#appearance-compact > button { padding:8px !important; }
#appearance-compact > button + div { position:absolute; top:100%; left:0; width:300px; max-height:420px; overflow:auto; z-index:30; background:var(--background-fill-primary); border:1px solid var(--border-color-primary); border-radius:8px; box-shadow:0 8px 24px #0002; }
@media(max-width:700px) { .node-table th:first-child {width:70px;} .node-table th, .node-table td {padding:8px;} #appearance-compact > button + div {left:auto; right:0;} }
"""

NODE_EDITOR_JS = r"""() => {
 if (!window.supervisorNodeEditorInstalled) {
   window.supervisorNodeEditorInstalled = true;
   const drafts=new Map();
   const valueOf=input=>input.dataset.valueType==='BOOLEAN' ? input.checked : ['INT','FLOAT'].includes(input.dataset.valueType) ? (input.value===''?null:Number(input.value)) : input.value;
   const remember=input=>{
     const form=input.closest('.parameter-form');
     if(!form) return;
     const id=form.dataset.revision+':'+input.dataset.parameterKey;
     const value=valueOf(input), original=JSON.parse(input.dataset.original);
     if(JSON.stringify(value)===JSON.stringify(original)) drafts.delete(id);
     else drafts.set(id,{revision:form.dataset.revision,key:input.dataset.parameterKey,value});
   };
   const renderParameters=()=>{
     const form=document.querySelector('#editor-parameters .parameter-form');
     if(form) {
       form.querySelectorAll('[data-parameter-key]').forEach(input=>{
         const draft=drafts.get(form.dataset.revision+':'+input.dataset.parameterKey);
         if(draft) {if(input.type==='checkbox') input.checked=draft.value; else input.value=draft.value===null?'':draft.value;}
         input.dataset.dirty=String(!!draft);
       });
       const count=Array.from(drafts.values()).filter(d=>d.revision===form.dataset.revision).length;
       const line=form.querySelector('.parameter-dirty'), text=count?`${count} 个参数尚未保存`:'';
       if(line.textContent!==text) line.textContent=text;
     }
     document.querySelectorAll('.lora-add-panel[data-loaders]').forEach(panel=>{
       const clip=panel.querySelector('[data-lora-field=loader]').value==='LoraLoader';
       panel.querySelectorAll('.lora-clip,.lora-strength_clip').forEach(el=>el.style.display=clip?'flex':'none');
     });
     document.querySelectorAll('.parameter-save,.lora-add-button').forEach(button=>button.disabled=!!window.supervisorWorkflowEditBusy);
   };
   window.supervisorRenderParameters=renderParameters;
   new MutationObserver(renderParameters).observe(document.body,{childList:true,subtree:true});
   document.addEventListener('input',e=>{
     if(e.target.matches?.('#editor-parameters [data-parameter-key]')) {remember(e.target);renderParameters();}
   });
   document.addEventListener('change',e=>{
     if(e.target.matches?.('[data-lora-field=loader]')) {
       const panel=e.target.closest('.lora-add-panel'), models=JSON.parse(panel.dataset.loaders)[e.target.value];
       const select=panel.querySelector('[data-lora-field=lora_name]'), old=select.value;
       select.replaceChildren(...models.map(name=>new Option(name,name,false,name===old)));
       renderParameters();
     }
   });
   document.addEventListener('click',e=>{
     if(e.target.closest?.('#editor-refresh')) drafts.clear();
     const save=e.target.closest?.('.parameter-save'), add=e.target.closest?.('.lora-add-button');
     if(!save && !add || window.supervisorWorkflowEditBusy) return;
     const panel=(save||add).closest('.parameter-form,.lora-add-panel');
     if(Array.from(panel.querySelectorAll('input,select,textarea')).some(input=>!input.reportValidity())) return;
     let body;
     if(save) {
       panel.querySelectorAll('[data-parameter-key]').forEach(remember);
       const changes=Object.fromEntries(Array.from(drafts.values()).filter(d=>d.revision===panel.dataset.revision).map(d=>[d.key,d.value]));
       body={kind:'parameters',revision:panel.dataset.revision,changes};
     } else {
       if(Array.from(drafts.values()).some(d=>d.revision===panel.dataset.revision)) {
         const status=document.querySelector('#editor-status .prose');
         if(status) status.textContent='请先保存修改的参数，再添加 LoRA。';
         document.querySelector('.parameter-save')?.scrollIntoView({block:'center',behavior:'smooth'});return;
       }
       body={kind:'lora',revision:panel.dataset.revision};
       panel.querySelectorAll('[data-lora-field]').forEach(input=>body[input.dataset.loraField]=input.type==='number'?(input.value===''?null:Number(input.value)):input.value);
     }
     const input=document.querySelector('#editor-bulk-request textarea, #editor-bulk-request input');
     const trigger=document.querySelector('#editor-bulk-trigger');
     if(!input || !trigger) return;
     window.supervisorWorkflowEditBusy=true;renderParameters();
     const proto=input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
     Object.getOwnPropertyDescriptor(proto,'value').set.call(input,JSON.stringify(body));
     input.dispatchEvent(new Event('input',{bubbles:true}));trigger.click();
   });
   const expandedDetails = new Map();
   const restoredDetails = new WeakSet();
   document.addEventListener('click',e=>{
     const summary=e.target.closest?.('summary');
     const detail=summary?.parentElement;
     if(detail?.matches('details[data-persist-key]')) expandedDetails.set(detail.dataset.persistKey,!detail.open);
   },true);
   document.addEventListener('toggle',e=>{
     const key=e.target?.dataset?.persistKey;
     if(key && e.target.isConnected) expandedDetails.set(key,e.target.open);
   },true);
   new MutationObserver(()=>{
     document.querySelectorAll('details[data-persist-key]').forEach(detail=>{
       if(restoredDetails.has(detail)) return;
       restoredDetails.add(detail);
       const remembered=expandedDetails.get(detail.dataset.persistKey);
       if(remembered!==undefined && detail.open!==remembered) detail.open=remembered;
     });
   }).observe(document.body,{childList:true,subtree:true});
   const updateThemes = () => {
     const count = Number(document.querySelector('#studio-groups input')?.value);
     const perGroup = Number(document.querySelector('#studio-per-group input')?.value);
     const styles = document.querySelector('#studio-styles textarea')?.value || '';
     const filled = styles.split(/\r?\n/).filter(line=>line.trim()).length;
     const valid = Number.isInteger(count) && count>=1 && count<=20;
     const validTarget = Number.isInteger(perGroup) && perGroup>=1 && perGroup<=50;
     const target = valid && validTarget ? ` · ${count} 组 × ${perGroup} 张 = 目标 ${count*perGroup} 张合格图` : ' · 请填写有效的组数和每组张数';
     const random = !valid || filled===count ? '' : filled<count ? ` · 模型将随机补足 ${count-filled} 组主题` : ` · 模型将随机选取 ${count} 组主题`;
     const text = `已填写 ${filled} / ${valid ? count : '?'} 组主题` + target + random;
     const line = document.querySelector('#studio-group-status .prose p');
     if (line && line.textContent!==text) line.textContent=text;
   };
   document.addEventListener('input',e=>{
     if(e.target.closest?.('#studio-groups, #studio-per-group, #studio-styles')) requestAnimationFrame(updateThemes);
   });
   window.supervisorUpdateThemes = updateThemes;
   // Preset/import callbacks replace values without dispatching DOM input events.
   setInterval(updateThemes,500);
   document.addEventListener('change', e => {
     const choice = e.target.closest?.('#editor-table [data-bypass-node], #editor-table [data-switch-node]');
     if (!choice || choice.disabled || window.supervisorBypassBusy) return;
     const input = document.querySelector('#editor-bypass-request textarea, #editor-bypass-request input');
     const trigger = document.querySelector('#editor-bypass-trigger');
     const isSwitch = !!choice.dataset.switchNode;
     if(!input || !trigger) { if(isSwitch) choice.value=choice.dataset.switchValue; else choice.checked=!choice.checked; return; }
     const body=isSwitch?{kind:'switch',node_id:choice.dataset.switchNode,value:choice.value==='true'}:{kind:'bypass',node_id:choice.dataset.bypassNode,enabled:choice.checked};
     window.supervisorBypassBusy=true;
     const label=!isSwitch && choice.closest("label")?.querySelector("span");
     if(label) label.textContent=choice.checked?"已选绕过":"绕过";
     document.querySelectorAll('#editor-table [data-bypass-node], #editor-table [data-switch-node]').forEach(c=>c.disabled=true);
     const proto=input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
     Object.getOwnPropertyDescriptor(proto,'value').set.call(input,JSON.stringify(body));
     input.dispatchEvent(new Event('input',{bubbles:true}));
     trigger.click();
   });
   document.addEventListener('click', e => {
     const button = e.target.closest?.('#editor-table [data-editor-key], #editor-parameters [data-editor-key]');
     if (!button) return;
     const input = document.querySelector('#editor-field input, #editor-field textarea');
     if (!input) return;
     if (input.value === button.dataset.editorKey) {
       document.querySelector('#editor-controls')?.scrollIntoView({behavior:'smooth',block:'start'});
       return;
     }
     window.supervisorScrollEditor = true;
     const prototype = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
     Object.getOwnPropertyDescriptor(prototype,'value').set.call(input,button.dataset.editorKey);
     input.dispatchEvent(new Event('input',{bubbles:true}));
   });
 }
 document.querySelectorAll(".control-word-delete").forEach(b=>{b.title="删除此行控制词";b.setAttribute("aria-label","删除此行控制词");});
 window.supervisorUpdateThemes?.();
 return [];
}"""

SCROLL_EDITOR_JS = """() => {
 if(window.supervisorScrollEditor) {
   window.supervisorScrollEditor = false;
   document.querySelector('#editor-controls')?.scrollIntoView({behavior:'smooth',block:'start'});
 }
 return [];
}"""

CHARACTER_ROWS_CSS = """
#studio-character-rows .character-rows-editor {padding:8px 12px;}
.character-rows-editor > p {font-size:12px; opacity:.75; margin:4px 0 8px;}
.character-row {display:grid; grid-template-columns:minmax(80px,1fr) minmax(180px,3fr) 38px; align-items:end; gap:8px; margin-bottom:8px;}
.character-row label {display:flex; flex-direction:column; gap:5px; min-width:0; font-size:13px;}
.character-row input {width:100%; height:44px; box-sizing:border-box; padding:8px 10px; border:1px solid var(--border-color-primary); border-radius:9px; color:var(--body-text-color); background:var(--input-background-fill);}
.character-row button,.character-add {height:44px; padding:0 10px; border:1px solid var(--border-color-primary); border-radius:9px; background:var(--background-fill-secondary); color:var(--body-text-color); cursor:pointer;}
.character-row button {color:#d65353;}
.character-add {width:44px; font-size:20px;}
.character-add:disabled {opacity:.45; cursor:default;}
.character-rows-status {margin-left:10px; font-size:12px; opacity:.75;}
"""

CHARACTER_ROWS_JS = r"""() => {
 if(window.supervisorCharacterRowsInstalled)return;
 window.supervisorCharacterRowsInstalled=true;
 let rows=[{groups:'全部',name:''}],lastRaw=null,lastHost=null,lastLanguage=null;
 const field=()=>document.querySelector('#studio-character textarea, #studio-character input');
 const publish=()=>{
   const input=field();if(!input)return;
   const raw=JSON.stringify(rows);lastRaw=raw;
   const prototype=input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
   Object.getOwnPropertyDescriptor(prototype,'value').set.call(input,raw);
   input.dispatchEvent(new Event('input',{bubbles:true}));
 };
 const mount=()=>{
   const host=document.querySelector('#studio-character-rows .character-rows-editor'),input=field();
   if(!host || !input)return;
   const english=window.supervisorLanguage==='en',raw=input.value || '';
   if(raw===lastRaw && host===lastHost && english===lastLanguage)return;
   if(raw!==lastRaw){
     try {rows=raw.trim().startsWith('[')?JSON.parse(raw):[{groups:'全部',name:raw}];}
     catch {host.querySelector('.character-rows-status').textContent='角色配置无效，请重新输入。';return;}
     if(!Array.isArray(rows))return;
     rows=rows.length?rows:[{groups:'全部',name:''}];
   }
   lastRaw=raw;lastHost=host;lastLanguage=english;
   const list=host.querySelector('.character-rows');list.replaceChildren();
   rows.forEach((row,index)=>{
     const line=document.createElement('div');line.className='character-row';
     for(const [key,label,placeholder] of [
       ['groups',english?'Groups':'组号',english?'All or 1,2':'全部 或 1,2'],
       ['name',english?'Character / work / features':'角色 / 作品 / 特征',english?'Character name and optional notes':'角色名，可补充作品或外观特征']]){
       const wrap=document.createElement('label');wrap.append(document.createTextNode(label));
       const box=document.createElement('input');box.type='text';box.value=row[key] || '';box.placeholder=placeholder;
       box.maxLength=key==='name'?600:80;
       box.addEventListener('input',()=>{rows[index][key]=box.value;publish();});
       wrap.append(box);line.append(wrap);
     }
     const remove=document.createElement('button');remove.type='button';remove.textContent='×';
     remove.setAttribute('aria-label',english?'Remove character row':'删除角色行');
     remove.onclick=()=>{rows.splice(index,1);if(!rows.length)rows=[{groups:'全部',name:''}];publish();lastHost=null;mount();host.querySelector('.character-rows-status').textContent=english?'Row removed':'已删除角色行';};
     line.append(remove);list.append(line);
   });
   const add=host.querySelector('.character-add');add.disabled=rows.length>=30;
   add.onclick=()=>{if(rows.length>=30)return;rows.push({groups:rows[rows.length-1]?.groups || '全部',name:''});publish();lastHost=null;mount();host.querySelector('.character-rows-status').textContent=english?'Row added':'已添加角色行';};
 };
 setInterval(mount,250);mount();
}"""
