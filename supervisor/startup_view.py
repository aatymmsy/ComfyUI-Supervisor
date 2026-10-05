"""Show actual submission work before a task reaches the generation queue."""
import asyncio
import html
import time

import gradio as gr


def startup_html(label='',started=None,state='idle'):
    if not label:
        return '<div class="startup-card" data-state="idle" hidden></div>'
    elapsed=max(0,time.time()-(started or time.time()))
    waiting=state=='working'
    return (f'<div class="startup-card" data-state="{state}" data-started="{int((started or time.time())*1000)}" role="status" aria-live="polite">'
            f'<strong>{html.escape(label)}</strong>'
            +(f'<span>已等待 {elapsed:.0f} 秒 · 尚未开始生图</span><div class="startup-track"><i></i></div>' if waiting else '')
            +'</div>')


async def startup_progress(callback,args,width):
    started=time.time()
    updates=asyncio.Queue()
    loop=asyncio.get_running_loop()
    def report(label):
        loop.call_soon_threadsafe(updates.put_nowait,label)
    future=asyncio.create_task(callback(*args,on_progress=report))
    label='正在检查输入'
    try:
        initial=[gr.update() for _ in range(width)]
        initial[1]='正在准备生图，请稍候…'
        initial[2]='正在准备生图，请稍候…'
        yield (*initial,startup_html(label,started,'working'))
        while not future.done():
            try:
                label=await asyncio.wait_for(updates.get(),timeout=.5)
                while not updates.empty():
                    label=updates.get_nowait()
            except asyncio.TimeoutError:
                pass
            if not future.done():
                yield (*[gr.update() for _ in range(width)],startup_html(label,started,'working'))
        result=await future
        success=str(result[1]).startswith(('任务已开始：','提示词生图已开始：'))
        yield (*result,startup_html('任务已加入顺序队列' if success else str(result[1]),started,'done' if success else 'error'))
    finally:
        # Finish an already accepted submission even if the page disconnects.
        if not future.done():
            await asyncio.shield(future)


STARTUP_CSS="""
.startup-card {padding:10px 12px; border:1px solid var(--border-color-primary); border-radius:8px; font-size:13px;}
.startup-card[hidden] {display:none !important;}
.startup-card strong,.startup-card span {display:block;}
.startup-card span {margin-top:5px; color:var(--body-text-color-subdued);}
.startup-card[data-state=error] {border-color:#dc2626; color:#dc2626;}
.startup-track {margin-top:9px; height:5px; overflow:hidden; border-radius:4px; background:var(--background-fill-secondary);}
.startup-track i {display:block; width:25%; height:100%; background:var(--color-accent); animation:supervisor-progress 1.5s ease-in-out infinite alternate;}
.task-action-help {font-size:13px; color:var(--body-text-color-subdued);}
#studio-start[data-startup-pending],#direct-start[data-startup-pending] {font-size:0 !important;}
#studio-start[data-startup-pending]::after,#direct-start[data-startup-pending]::after {content:'正在准备生图…';font-size:14px;}
@media(prefers-reduced-motion:reduce) {.startup-track i {animation:none;}}
"""

STARTUP_JS=r"""() => {
 if(window.supervisorStartupInstalled) return;
 window.supervisorStartupInstalled=true;
 const pending=new Map();
 const feedback=(host)=>{
   let local=host.querySelector(':scope > .startup-local-feedback');
   if(!local) {
     local=document.createElement('div');local.className='startup-local-feedback';
     local.innerHTML='<div class="startup-card" role="status" aria-live="polite"><strong>已收到，正在提交生图请求…</strong><span>尚未开始生图</span><div class="startup-track"><i></i></div></div>';
     // Leave Gradio's HTML container and its rendering anchors intact.
     host.appendChild(local);
   }
   return local;
 };
 const update=()=>{
   pending.forEach((state,id)=>{
     const host=document.getElementById(`${id}-status`),button=document.getElementById(id);
     const card=host?.querySelector('.html-container .startup-card');
     if(!button && !host) {pending.delete(id);return;}
     const current=card?.dataset.started && card.dataset.started!==state.previous;
     if(card && current && ['done','error'].includes(card.dataset.state)) {
       host.querySelector(':scope > .startup-local-feedback')?.remove();
       pending.delete(id);if(button) {button.disabled=false;button.removeAttribute('data-startup-pending');}return;
     }
     if(button) {
       button.disabled=true;
       button.setAttribute('data-startup-pending','');
     }
     if(host && current && card.dataset.state==='working') {
       host.querySelector(':scope > .startup-local-feedback')?.remove();
     } else if(host) {
       const local=feedback(host);
       const seconds=Math.floor((Date.now()-state.started)/1000);
       const span=local.querySelector('span'),text=`已等待 ${seconds} 秒 · 请求正在进入处理，尚未开始生图`;
       if(span && span.textContent!==text) span.textContent=text;
     }
   });
 };
 document.addEventListener('click',event=>{
   const button=event.target.closest?.('#studio-start,#direct-start');
   if(!button) return;
   if(pending.has(button.id)) {event.preventDefault();event.stopImmediatePropagation();return;}
   const host=document.getElementById(`${button.id}-status`);
   if(!host) return;
   pending.set(button.id,{started:Date.now(),previous:host.querySelector('.html-container .startup-card')?.dataset.started});
   feedback(host);
   requestAnimationFrame(update);
 },true);
 setInterval(update,250);
}"""
