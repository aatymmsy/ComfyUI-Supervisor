"""Display measured ComfyUI node progress; unknown work has no invented percentage."""
import html
import json
import math

PROGRESS_CSS = """
.generation-meter { padding:10px 0; }
.generation-meter .meter-label { display:flex; justify-content:space-between; gap:12px; font-size:14px; margin-bottom:8px; }
.generation-meter .meter-track { height:9px; border-radius:6px; overflow:hidden; background:var(--background-fill-secondary); }
.generation-meter .meter-fill { height:100%; background:var(--color-accent); border-radius:6px; transition:width .25s ease; }
.generation-meter .meter-running { width:28%; animation:supervisor-progress 1.5s ease-in-out infinite alternate; }
@keyframes supervisor-progress { from {transform:translateX(0)} to {transform:translateX(255%)} }
@media(prefers-reduced-motion:reduce) { .generation-meter .meter-running {animation:none;} }
"""


def generation_progress_html(service, task_id):
    task = service.db.one('SELECT state,phase FROM tasks WHERE id=?', (task_id,)) if task_id else None
    if not task:
        return ''
    generation = service.db.one('SELECT id,state FROM generations WHERE task_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (task_id,))
    if task['state'] not in ('RUNNING','STOPPING'):
        return ''
    value = None
    label = '等待 ComfyUI 开始生图'
    suffix = ''
    if generation and generation['state'] == 'MONITOR':
        label = 'ComfyUI 生图中'
        events = service.db.rows("SELECT kind,body FROM events WHERE task_id=? AND kind IN ('COMFY_progress','COMFY_executing','COMFY_WS_DISCONNECTED','COMFY_WS_FALLBACK') ORDER BY id DESC LIMIT 200", (task_id,))
        for event in events:
            data = json.loads(event['body'])
            if data.get('generation_id') != generation['id']:
                continue
            if data.get('node'):
                label += ' · 节点 ' + str(data['node'])
            if event['kind'] == 'COMFY_progress':
                current, maximum = data.get('value'), data.get('max')
                if type(current) in (int,float) and type(maximum) in (int,float) and math.isfinite(current) and math.isfinite(maximum) and maximum > 0:
                    value = min(100, max(0, current / maximum * 100))
                    suffix = f'{current:g}/{maximum:g} · {value:.0f}%（当前节点）'
            elif 'WS_' in event['kind']:
                label = 'ComfyUI 生图中 · 等待进度回报'
            break
    elif generation and generation['state'] in ('COLLECT','EVALUATE','DECIDED') and task['phase'] in ('EVALUATE','PRESCREEN','REVIEW','COLLECT'):
        value = 100
        label = '本张生图完成 · 正在收集/评分'
        suffix = '100%'
    elif task['phase'] == 'QUEUED':
        label = '已排队 · 等待前面的任务'
    elif task['phase'] in ('ANALYZE','CAPTION','THEMES','PROMPT','INGEST','PREPARE','DELIVER'):
        label={'ANALYZE':'正在打标与分析参考图片','CAPTION':'正在反推参考图片的提示词',
            'THEMES':'正在确定各组主题','PROMPT':'大模型正在生成/完善提示词',
            'INGEST':'正在整理参考图片','PREPARE':'正在准备生成任务','DELIVER':'正在保存生成结果'}[task['phase']]
        suffix='尚未开始生图' if task['phase']!='DELIVER' else ''
    elif task['phase']=='SUBMIT':
        label='正在向 ComfyUI 提交工作流'
    elif task['phase'] not in ('MONITOR','GENERATE','COLLECT','EVALUATE','PRESCREEN','REVIEW'):
        label='正在准备生图任务'
    attrs = f'aria-valuenow="{value:.0f}"' if value is not None else 'aria-valuetext="进度待回报"'
    fill = f'<div class="meter-fill" style="width:{value:.2f}%"></div>' if value is not None else '<div class="meter-fill meter-running"></div>'
    return f'<div class="generation-meter"><div class="meter-label"><span>{html.escape(label)}</span><span>{html.escape(suffix)}</span></div><div class="meter-track" role="progressbar" aria-label="生图进度" aria-valuemin="0" aria-valuemax="100" {attrs}>{fill}</div></div>'
