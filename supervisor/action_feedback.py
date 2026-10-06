"""Immediate, accessible feedback for server-backed button actions."""
import json

import gradio as gr
from gradio.events import Dependency

ACTION_CSS='''
.action-feedback-button[data-action-working=true] {cursor:wait !important; opacity:.8;}
.action-feedback-button[data-action-working=true]::after {content:' ⟳'; display:inline-block; animation:action-spin 1s linear infinite;}
@keyframes action-spin {to {transform:rotate(360deg);}}
@media(prefers-reduced-motion:reduce) {.action-feedback-button[data-action-working=true]::after {animation:none;}}
.action-feedback-status {position:fixed; bottom:18px; left:50%; transform:translateX(-50%); z-index:20000; max-width:90vw; padding:9px 16px; border-radius:9px; background:var(--background-fill-primary); color:var(--body-text-color); border:1px solid var(--border-color-primary); box-shadow:0 3px 16px #0003; font-size:13px; pointer-events:none;}
'''
ACTION_JS=r'''() => {
 if(window.supervisorActionFeedback) return;
 window.supervisorActionFeedback=true;
 let status,clearTimer;
 const labels=new Map();
 const show=text=>{
   clearTimeout(clearTimer);
   if(!status){status=document.createElement('div');status.className='action-feedback-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');document.body.appendChild(status);}
   status.textContent=text;status.hidden=false;
 };
 document.addEventListener('click',event=>{
   const button=event.target.closest?.('.action-feedback-button');
   if(!button || button.disabled || button.querySelector('button:disabled')) return;
   if(button.dataset.actionWorking==='true'){event.preventDefault();event.stopImmediatePropagation();return;}
   labels.set(button.id,button.dataset.actionLabel || button.textContent.trim());
   button.dataset.actionWorking='true';button.setAttribute('aria-busy','true');
   show('正在执行：'+labels.get(button.id)+'…');
 },true);
 window.supervisorActionDone=id=>{
   const button=document.getElementById(id);
   if(!labels.has(id)) return;
   if(button){button.dataset.actionWorking='false';button.removeAttribute('aria-busy');}
   const label=labels.get(id);labels.delete(id);
   if(labels.size) {
     show('仍在执行：'+[...labels.values()].join('、')+'…');
   } else {
     show('已响应：'+label+'，请查看操作结果。');
     clearTimer=setTimeout(()=>{if(status)status.hidden=true;},6000);
   }
 };
}'''


def install_action_feedback(app):
    dependencies=list(app.get_config_file()['dependencies'])
    clickable={block_id for dep in dependencies for block_id,event in dep.get('targets',[]) if event=='click'}
    for component in app.blocks.values():
        if component._id in clickable and isinstance(component,gr.Button) and component.elem_id not in ('studio-start','direct-start') and 'result-delete-bridge' not in (component.elem_classes or []):
            component.elem_id=component.elem_id or f'action-{component._id}'
            component.elem_classes=[*(component.elem_classes or []),'action-feedback-button']
    for dependency in dependencies:
        for block_id,event in dependency.get('targets',[]):
            button=app.blocks.get(block_id)
            if event=='click' and isinstance(button,gr.Button) and 'action-feedback-button' in (button.elem_classes or []):
                Dependency(None,dependency,dependency['id'],None).then(fn=None,queue=False,
                    js='() => {window.supervisorActionDone?.('+json.dumps(button.elem_id)+');return [];}')
    app.load(fn=None,js=ACTION_JS)
