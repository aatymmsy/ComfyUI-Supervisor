"""Create an independent queued task from one image's actual execution recipe."""

import hashlib
import json
import re

from .comfy import Comfy
from .files import atomic_write, sha256
from .models import PromptPlan, TaskSettings, WorkflowConfig, dump_json
from .setup import infer_workflow
from .studio import check_live_ready


def result_recipe(service, task_id, asset_id, count, review):
    if type(count) is not int or not 1 <= count <= 50 or type(review) is not bool:
        raise ValueError('产出张数须为 1–50 的整数，审查选项无效。')
    row = service.db.one('''SELECT a.*,n.graph,n.graph_hash,p.body prompt FROM assets a
        JOIN generations n ON n.id=a.generation_id AND n.task_id=a.task_id AND n.group_id=a.group_id
        JOIN prompt_variants p ON p.id=n.variant_id AND p.task_id=a.task_id AND p.group_id=a.group_id
        WHERE a.id=? AND a.task_id=? AND a.source_kind='generated' AND n.state='DECIDED' ''',(asset_id,task_id))
    if not row or row['state'] not in ('AVAILABLE','QUARANTINED'):
        raise ValueError('这张图片已删除或尚未完成生成，请刷新结果后重试。')
    path = service.files.path(row['path'])
    if not path.is_file() or sha256(path) != row['sha256']:
        raise ValueError('图片原文件缺失或已被修改，无法批量产出。')
    old = service.settings(task_id)
    plan = PromptPlan.model_validate_json(row['prompt'])
    settings = TaskSettings.model_validate({**old.model_dump(),
        'goal':plan.positive[:3000], 'direct_prompt':plan.positive, 'direct_negative':plan.negative,
        'review_enabled':review, 'groups':1, 'per_group':count,
        'max_rounds':max(old.max_rounds,count) if review else count,
        'autonomous':True, 'reference_only':False, 'images_only_delivery':True,
        'delivery_layout':'flat','delivery_format':'files',
        'auto_candidates':True, 'auto_iterations':True, 'prescreen':False,
        'input_folder':None, 'target_styles':[], 'control_words':[], 'lora_trigger_words':'', 'character':'', 'character_controls':[],
        'prompt_examples':'', 'result_retry':None, 'content_label':row['content_label'],
        'params':{**plan.params.model_dump(),'seed':(plan.params.seed+1) % (2**63)}})
    if old.demo:
        return settings, None
    if not row['graph'] or hashlib.sha256(row['graph'].encode('utf-8')).hexdigest() != row['graph_hash']:
        raise ValueError('该图的实际工作流记录缺失或损坏，无法批量产出。')
    graph = json.loads(row['graph'])
    record = service.db.one('SELECT workflow_json FROM task_configs WHERE task_id=?',(task_id,))
    if record:
        workflow = WorkflowConfig.model_validate_json(record['workflow_json'])
    elif service.workflow:
        # Legacy tasks have no binding snapshot. Infer only from this executed graph.
        try:
            workflow = infer_workflow(graph,'',service.workflow.base_url)
        except ValueError:
            raise ValueError('旧图片的工作流无法可靠绑定，请重新生成后再批量产出。') from None
    else:
        raise ValueError('该图缺少工作流绑定与 ComfyUI 地址，无法批量产出。')
    raw = dump_json(graph).encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    relative = f'workflows/snapshots/{digest}.json'
    atomic_write(service.project_root/relative,raw)
    # The stored graph has already applied switches and bypasses.
    workflow = workflow.model_copy(update={'workflow_api_json':relative,'workflow_hash':digest,
        'switch_nodes':{},'bypass_nodes':{}})
    try:
        Comfy(workflow,service.project_root,service.db).graph(
            plan.model_copy(update={'params':settings.params}),'batch-preflight')
    except Exception:
        raise ValueError('该图的工作流或参数无法复用，请检查生成记录。') from None
    return settings, workflow


async def batch_from_result(service, task_id, asset_id, count, review, request_id):
    if any(not isinstance(value,str) or not 1 <= len(value) <= 80 for value in (task_id,asset_id)):
        raise ValueError('图片归属无效，请刷新结果后重试。')
    if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}',request_id):
        raise ValueError('批量请求无效，请刷新结果后重试。')
    fingerprint = dump_json([task_id,asset_id,count,review])

    def previous_request():
        previous = service.db.one('SELECT * FROM result_batch_requests WHERE request_id=?',(request_id,))
        if previous and previous['fingerprint'] != fingerprint:
            raise ValueError('批量请求已使用，请重新点击执行。')
        return previous['task_id'] if previous else None

    with service.active_lock:
        previous = previous_request()
        if previous:
            return previous
        settings, workflow = result_recipe(service,task_id,asset_id,count,review)
    if not settings.demo:
        await check_live_ready(service,settings.content_label,settings.params.model_dump(),
            purposes=(("review","质量评审",1),) if review else (),workflow_config=workflow)
    with service.active_lock:
        previous = previous_request()
        if previous:
            return previous
        # Recheck ownership/file availability after network preflight.
        settings, workflow = result_recipe(service,task_id,asset_id,count,review)
        new_id = service.create_task(settings,workflow_config=workflow,
            batch_request=(request_id,task_id,asset_id,fingerprint))
        service.db.event(new_id,'RESULT_BATCH_CREATED',{'source_task':task_id,'source_asset':asset_id,
            'count':count,'review_enabled':review})
    service.enqueue(new_id)
    return new_id
