"""Live group queue and cancellation controls, keyed by task and group IDs."""
from html import escape


def group_queue_html(service, selected_task=None, language='zh'):
    english = language != 'zh'
    rows = []
    remaining = 0
    # Match the execution queue, independent of the history selected for viewing.
    tasks = service.db.rows("SELECT * FROM tasks WHERE phase<>'DISCUSSION' AND (state IN ('RUNNING','STOPPING') OR (state='PAUSED' AND reason='ROUND_ENDED')) ORDER BY created_at,rowid")
    for task in tasks:
        settings = service.settings(task['id'])
        if settings.reference_only:
            continue
        current_group = None
        if task['id'] == service.executing_task:
            pending = service.db.one("SELECT group_id FROM generations WHERE task_id=? AND state NOT IN ('DECIDED','FAILED') ORDER BY created_at,rowid LIMIT 1", (task['id'],))
            if pending:
                current_group = pending['group_id']
            elif getattr(service,'rechecking_group',None) and service.rechecking_group[0]==task['id']:
                current_group=service.rechecking_group[1]
            elif task['phase'] == 'PROMPT':
                planning = service.next_group(task['id'], settings)
                current_group = planning['id'] if planning else None
        for group in service.db.rows("SELECT * FROM groups WHERE task_id=? AND state<>'CANCELLED' ORDER BY ordinal", (task['id'],)):
            count = len(service.db.accepted(task['id'], group['id']))
            shortfall = max(0, settings.per_group - count)
            remaining += shortfall
            theme = settings.target_styles[group['ordinal']] if group['ordinal'] < len(settings.target_styles) else ''
            is_current = group['id'] == current_group
            pending_group = service.db.one("SELECT id FROM generations WHERE group_id=? AND state NOT IN ('DECIDED','FAILED') LIMIT 1",(group['id'],))
            has_run = group['round_index'] > 0 or bool(service.db.one("SELECT id FROM generations WHERE group_id=? AND state='DECIDED' LIMIT 1",(group['id'],)))
            retry = has_run and not is_current and not pending_group
            if shortfall == 0:
                state = '已完成'
            elif task['state'] == 'STOPPING':
                state = '正在停止'
            elif is_current:
                state = '正在处理'
            elif retry:
                state = '已运行结束'
            elif task['state'] == 'RUNNING':
                state = '待执行'
            else:
                state = {'PAUSED':'已暂停', 'WAITING_APPROVAL':'待确认', 'BLOCKED_POLICY':'连接受限', 'FAILED':'需恢复'}.get(task['state'], task['state'])
            if english:
                state = {'已完成':'Complete', '已结束':'Finished', '已运行结束':'Finished', '正在停止':'Stopping', '正在处理':'Processing', '待执行':'Queued', '已暂停':'Paused', '待确认':'Needs approval', '连接受限':'Connection blocked', '需恢复':'Needs recovery'}.get(state,state)
                text = f"Task {task['id'][:8]} · Group {group['ordinal']+1} · {theme} · {state} · Saved {count}/{settings.per_group} · Attempt {group['round_index']}/{settings.max_rounds}"
                title = f"Cancel group {group['ordinal']+1} of task {task['id'][:8]}"
            else:
                text = f"任务 {task['id'][:8]} · 第 {group['ordinal']+1} 组 · {theme} · {state} · 已保存 {count}/{settings.per_group} · 轮次 {group['round_index']}/{settings.max_rounds}"
                title = f"删除任务 {task['id'][:8]} 第 {group['ordinal']+1} 组的后续工作"
            if retry:
                title = f"Redraw group {group['ordinal']+1} of task {task['id'][:8]}" if english else f"重绘任务 {task['id'][:8]} 第 {group['ordinal']+1} 组"
                icon = 'M3 10a9 9 0 1 1 1 7M3 3v7h7'
                button_class = 'group-retry-button'
            else:
                icon = 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7'
                button_class = 'group-cancel-button'
            button = f'<button type="button" class="{button_class}" data-task-id="{escape(task["id"],quote=True)}" data-group-id="{escape(group["id"],quote=True)}" title="{escape(title,quote=True)}" aria-label="{escape(title,quote=True)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="{icon}"/></svg></button>'
            marker = ' data-current-group="true" aria-current="step"' if is_current else ''
            rows.append(f'<div class="group-queue-row" data-group-id="{escape(group["id"],quote=True)}"{marker}><span>{escape(text)}</span>{button}</div>')
    if not rows:
        return '<p class="group-queue-note">'+('No image tasks are running or queued.' if english else '当前没有执行或排队的生图任务。')+'</p>'
    title = f'Group progress · {len(rows)} groups · {remaining} images remaining' if english else f'各组进度 · {len(rows)} 组 · 剩余目标 {remaining} 张'
    note = 'Running or unstarted groups can be cancelled. Finished groups can be redrawn from their latest image prompt; original images are kept.' if english else '运行中及未运行的组可删除后续工作；已运行结束的组可根据最后一轮图片的提示词重绘，沿用该组张数，原图保留。'
    return '<details data-group-queue="all"><summary>'+escape(title)+'</summary><p class="group-queue-note">'+escape(note)+'</p>'+''.join(rows)+'</details>'


GROUP_QUEUE_CSS = """
#result-group-progress details {border:1px solid #64748b55; border-radius:8px; padding:10px 12px;}
#result-group-progress summary {cursor:pointer; min-height:28px;}
.group-queue-note {font-size:12px; opacity:.7; margin:6px 0;}
.group-queue-row {display:flex; align-items:center; gap:12px; padding:8px 0; border-top:1px solid #64748b33;}
.group-queue-row[data-current-group="true"] {box-shadow:inset 0 -3px 0 var(--page-accent);}
.group-queue-row > span {flex:1; min-width:0; overflow-wrap:anywhere; font-size:14px;}
.group-cancel-button,.group-retry-button {flex:0 0 28px; width:28px; height:28px; padding:5px; border:0; border-radius:6px; color:#fff; background:#b91c1c; cursor:pointer;}
.group-retry-button {background:#334155;}
.group-retry-button:hover {background:var(--page-accent);}
#result-group-progress .group-cancel-button svg,#result-group-progress .group-retry-button svg {width:18px; height:18px; color:#fff !important; stroke:#fff; pointer-events:none;}
.group-cancel-button:disabled,.group-retry-button:disabled {opacity:.5; cursor:wait;}
.group-cancel-button:focus-visible,.group-retry-button:focus-visible {outline:2px solid #60a5fa; outline-offset:2px;}
.group-retry-button.pending svg {animation:result-retry-spin 1s linear infinite;}
.group-cancel-notice {position:fixed; top:72px; right:24px; z-index:10020; max-width:min(520px,85vw); padding:12px 16px; border-radius:8px; color:white; background:#334155; box-shadow:0 3px 16px #0004; pointer-events:none;}
"""

GROUP_QUEUE_JS = r"""() => {
 if(window.supervisorGroupCancelInstalled) return;
 window.supervisorGroupCancelInstalled=true;
 let pending=null,timer=null,expanded=null;
 const failedIds=new Map();
 // Remember user clicks, not toggle events emitted when Gradio replaces HTML.
 document.addEventListener('click',event=>{
   const summary=event.target.closest?.('#result-group-progress summary');
   if(summary) expanded=!summary.parentElement.open;
 },true);
 const restore=()=>{
   const detail=document.querySelector('#result-group-progress details');
   if(detail && expanded!==null && detail.open!==expanded) detail.open=expanded;
 };
 new MutationObserver(restore).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['open']});
 const notice=text=>{
   let el=document.querySelector('.group-cancel-notice');
   if(!el){el=document.createElement('div');el.className='group-cancel-notice';el.setAttribute('role','status');document.body.appendChild(el);}
   if(el.textContent!==text) el.textContent=text;
   clearTimeout(timer);
   if(!pending) timer=setTimeout(()=>el.remove(),8000);
 };
 document.addEventListener('click',event=>{
   const button=event.target.closest?.('#result-group-progress .group-cancel-button, #result-group-progress .group-retry-button');
   if(!button) return;
   event.preventDefault();event.stopPropagation();
   if(pending || button.disabled) return;
   const action=button.classList.contains('group-retry-button')?'retry':'cancel';
   const input=document.querySelector(`#group-${action}-request textarea, #group-${action}-request input`);
   if(!input) return;
   const key=button.dataset.taskId+':'+button.dataset.groupId+':'+action;
   pending={task_id:button.dataset.taskId,group_id:button.dataset.groupId,request_id:failedIds.get(key) || crypto.randomUUID(),action};
   notice(action==='retry'?(window.supervisorLanguage==='en'?'Preparing group redraw…':'正在准备本组重绘并加入队列…'):(window.supervisorLanguage==='en'?'Cancelling this group…':'正在取消该组后续工作…'));
   document.querySelectorAll('.group-cancel-button,.group-retry-button').forEach(b=>{b.disabled=true;if(b===button && action==='retry')b.classList.add('pending');});
   const prototype=input.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
   Object.getOwnPropertyDescriptor(prototype,'value').set.call(input,JSON.stringify(pending));
   input.dispatchEvent(new Event('input',{bubbles:true}));
   document.getElementById(`group-${action}-trigger`)?.click();
 });
 setInterval(()=>{
   let response;
   if(!pending) return;
   try{response=JSON.parse(document.querySelector(`#group-${pending.action}-response textarea, #group-${pending.action}-response input`)?.value || '{}');}catch{return;}
   if(pending && response.request_id===pending.request_id){
     const key=pending.task_id+':'+pending.group_id+':'+pending.action;
     if(response.ok)failedIds.delete(key);else failedIds.set(key,pending.request_id);
     pending=null;notice(response.text);
   }
   document.querySelectorAll('.group-cancel-button,.group-retry-button').forEach(b=>{if(b.disabled!==!!pending)b.disabled=!!pending;if(!pending)b.classList.remove('pending');});
 },250);
}"""
