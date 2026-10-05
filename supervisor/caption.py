"""Execute only the caption branch of an existing ComfyUI workflow."""
from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path

import httpx


class CaptionError(ValueError):
    pass


def caption_branch(graph):
    """Require an identifiable captioner fed by LoadImage, never by generation."""
    for node_id, node in graph.items():
        kind = node['class_type'].lower()
        if not any(word in kind for word in ('tagger', 'caption', 'interrogate', 'florence', 'blip')) or 'loader' in kind:
            continue
        branch, visiting = {}, set()
        def visit(key):
            key = str(key)
            if key in visiting or key not in graph:
                raise CaptionError('反推分支连接无效。')
            if key in branch:
                return
            visiting.add(key)
            data = graph[key]
            if any(word in data['class_type'].lower() for word in ('sampler', 'saveimage', 'vaedecode')):
                raise CaptionError('反推分支依赖生图节点，无法单独反推。')
            for value in data.get('inputs', {}).values():
                if isinstance(value, list) and len(value) == 2 and str(value[0]) in graph and isinstance(value[1], int):
                    visit(value[0])
            visiting.remove(key)
            branch[key] = copy.deepcopy(data)
        try:
            visit(node_id)
        except CaptionError:
            continue
        loaders = [key for key, data in branch.items() if data['class_type'] == 'LoadImage']
        if loaders:
            return str(node_id), loaders, branch
    return None


def configured_branch(service):
    if not service.workflow:
        return None
    path = Path(service.workflow.workflow_api_json)
    graph = json.loads((path if path.is_absolute() else service.project_root/path).read_bytes())
    return caption_branch(graph)


def read_caption(outputs):
    for name in ('tags', 'text', 'string', 'caption', 'captions', 'prompt', 'result'):
        value = outputs.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            lines = [line.strip() for line in value if isinstance(line, str) and line.strip()]
            if lines:
                return '\n'.join(lines)
    raise CaptionError('反推节点没有返回可读取的文字，原提示词已保留。')


async def run_caption(service, task_id):
    job = service.db.one('SELECT * FROM caption_jobs WHERE task_id=?', (task_id,))
    branch = json.loads(job['graph'])
    config = service.comfy.config
    url = config.base_url.rstrip('/')
    async with httpx.AsyncClient(timeout=30, transport=service.comfy.transport, trust_env=False) as client:
        prompt_id = job['prompt_id']
        if not prompt_id:
            if job['state'] != 'PREPARED':
                prompt_id = await service.comfy.reconcile(client, {'submission_token':task_id})
                if not prompt_id:
                    raise CaptionError('反推提交状态不明，请检查 ComfyUI 历史记录，避免重复提交。')
            else:
                asset = service.db.one('SELECT path FROM assets WHERE id=?', (job['asset_id'],))
                source = service.files.path(asset['path'])
                with source.open('rb') as stream:
                    response = await client.post(url+'/upload/image',files={'image':('supervisor-caption-'+task_id+source.suffix,stream)},data={'type':'input','overwrite':'false','subfolder':'supervisor-caption'})
                response.raise_for_status()
                uploaded = response.json()
                name = uploaded['name']
                if uploaded.get('subfolder'):
                    name = uploaded['subfolder'].rstrip('/')+'/'+name
                for node in branch.values():
                    if node['class_type'] == 'LoadImage':
                        node['inputs']['image'] = name
                definitions = (await client.get(url+'/object_info')).json()
                if not definitions.get(branch[job['node_id']]['class_type'],{}).get('output_node'):
                    raise CaptionError('此反推节点不输出可读取的执行结果，请连接文字显示输出节点。')
                service.db.execute("UPDATE caption_jobs SET state='SUBMITTING',graph=? WHERE task_id=?",(json.dumps(branch),task_id))
                response = await client.post(url+'/prompt',json={'prompt':branch,'client_id':task_id,'extra_data':{'supervisor_token':task_id}})
                response.raise_for_status()
                prompt_id = response.json()['prompt_id']
            service.db.execute("UPDATE caption_jobs SET prompt_id=?,state='MONITOR' WHERE task_id=?", (prompt_id,task_id))
        service.db.transition(task_id,'RUNNING','CAPTION')
        deadline = time.monotonic()+config.timeout_seconds
        while time.monotonic() < deadline:
            response = await client.get(url+'/history/'+prompt_id)
            response.raise_for_status()
            item = response.json().get(prompt_id)
            if item:
                status = item.get('status',{})
                if status.get('status_str') == 'error':
                    service.db.execute("UPDATE caption_jobs SET state='FAILED' WHERE task_id=?",(task_id,))
                    raise CaptionError('ComfyUI 反推执行失败，请检查反推模型是否已下载。')
                if status.get('completed') or status.get('status_str') == 'success':
                    text = read_caption(item.get('outputs',{}).get(job['node_id'],{}))
                    service.db.execute("UPDATE caption_jobs SET state='COMPLETED',result=? WHERE task_id=?",(text,task_id))
                    service.db.transition(task_id,'COMPLETED','CAPTION','CAPTION_COMPLETE')
                    return
            await asyncio.sleep(min(2,config.reconcile_interval))
        raise CaptionError('反推等待超时，可继续该任务查看结果；不会启动生图。')
