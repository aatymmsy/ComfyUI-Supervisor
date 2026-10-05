"""Queue a prompt revision using the selected image's own recipe and feedback."""
import json
import re

from .context import compact_review
from .control_words import controls_for_group, enforce_control_words, enforce_lora_triggers, character_for_group
from .db import now, uid
from .models import PromptPlan, ResultRetry, TaskSettings, dump_json
from .providers import CloudError
from .result_batch import result_recipe
from .studio import check_live_ready
from .round_feedback import round_feedback


def retry_recipe(service, task_id, asset_id, feedback, count=1):
    if not service.db.one('SELECT id FROM tasks WHERE id=?',(task_id,)):
        raise ValueError('找不到图片来源任务，请刷新后重试。')
    old = service.settings(task_id)
    settings, workflow = result_recipe(service, task_id, asset_id, count, old.review_enabled)
    source = service.db.one('''SELECT g.ordinal FROM assets a JOIN groups g ON g.id=a.group_id
        WHERE a.id=? AND a.task_id=?''', (asset_id, task_id))
    evaluation = service.db.one("SELECT body FROM evaluations WHERE asset_id=? AND stage='final' ORDER BY rowid DESC LIMIT 1", (asset_id,))
    controls = [word.model_copy(update={'group':1 if word.group is not None else None})
                for word in controls_for_group(old, source['ordinal'])]
    settings = TaskSettings.model_validate({**settings.model_dump(), 'goal':old.goal,
        'character':character_for_group(old,source['ordinal']), 'character_controls':[], 'lora_trigger_words':old.lora_trigger_words,
        'control_words':[word.model_dump() for word in controls],
        'target_styles':[old.target_styles[source['ordinal']]] if old.target_styles else [],
        'result_retry':ResultRetry(source_task=task_id, source_asset=asset_id, feedback=feedback,
            review=compact_review(json.loads(evaluation['body'])) if evaluation else {}).model_dump()})
    return settings, workflow


async def retry_from_result(service, task_id, asset_id, request_id, feedback=None):
    return await _queue_retry(service,task_id,asset_id,request_id,feedback)


async def retry_from_group(service, task_id, group_id, request_id):
    return await _queue_retry(service,task_id,group_id,request_id,source_group=True)


async def _queue_retry(service, task_id, source_id, request_id, feedback=None, *, source_group=False):
    if any(not isinstance(v,str) or not 1 <= len(v) <= 80 for v in (task_id,source_id)):
        raise ValueError('图片归属无效，请刷新后重试。')
    if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}',request_id):
        raise ValueError('重画请求无效，请刷新后重试。')
    if feedback is None:
        feedback = ResultRetry.model_fields['feedback'].default
    if not isinstance(feedback,str) or not feedback.strip() or len(feedback)>1000:
        raise ValueError('重画意见须为 1–1000 字。')
    fingerprint = dump_json(['retry_group' if source_group else 'retry',task_id,source_id,feedback])

    def source_recipe():
        asset_id=source_id
        count=1
        if source_group:
            group=service.db.one('SELECT * FROM groups WHERE id=? AND task_id=?',(source_id,task_id))
            if not group or group['state']=='CANCELLED':
                raise ValueError('该组不存在或已取消，请刷新结果页。')
            pending=service.db.one("SELECT id FROM generations WHERE group_id=? AND state NOT IN ('DECIDED','FAILED')",(source_id,))
            planning=service.next_group(task_id,service.settings(task_id)) if service.executing_task==task_id else None
            if pending or (planning and planning['id']==source_id and service.db.one('SELECT phase FROM tasks WHERE id=?',(task_id,))['phase']=='PROMPT'):
                raise ValueError('该组仍在运行，请等待本组处理结束后再重绘。')
            asset=service.db.one('''SELECT a.id FROM assets a JOIN generations n ON n.id=a.generation_id
                JOIN prompt_variants p ON p.id=n.variant_id WHERE a.task_id=? AND a.group_id=?
                AND a.source_kind='generated' AND a.state IN ('AVAILABLE','QUARANTINED') AND n.state='DECIDED'
                ORDER BY p.round_index DESC,n.created_at DESC,a.rowid DESC LIMIT 1''',(task_id,source_id))
            if not asset:
                raise ValueError('该组尚无已完成的可用图片，暂时无法重绘。')
            asset_id=asset['id']
            count=service.settings(task_id).per_group
        settings,workflow=retry_recipe(service,task_id,asset_id,feedback,count)
        return asset_id,settings,workflow

    def previous_request():
        row = service.db.one('SELECT * FROM result_batch_requests WHERE request_id=?',(request_id,))
        if row and row['fingerprint'] != fingerprint:
            raise ValueError('请求已使用，请重新点击重画。')
        return row['task_id'] if row else None

    with service.active_lock:
        previous = previous_request()
        if previous:
            return previous
        asset_id, settings, workflow = source_recipe()
    if not settings.demo:
        purposes = [('prompt_generation','提示词修订',0)]
        if settings.review_enabled:
            purposes.append(('review','质量评审',1))
        await check_live_ready(service,settings.content_label,settings.params.model_dump(),
            purposes=tuple(purposes),workflow_config=workflow)
    with service.active_lock:
        previous = previous_request()
        if previous:
            return previous
        asset_id, settings, workflow = source_recipe()
        new_id = service.create_task(settings,workflow_config=workflow,
            batch_request=(request_id,task_id,asset_id,fingerprint))
        service.db.execute('INSERT INTO human_feedback VALUES(?,?,?,?,?)',
            (uid(),asset_id,'RETRY',dump_json({'task_id':new_id,'feedback':feedback}),now()))
        service.db.event(new_id,'RESULT_RETRY_CREATED',{'source_task':task_id,'source_asset':asset_id})
        if source_group:
            service.db.event(new_id,'GROUP_RETRY_CREATED',{'source_task':task_id,'source_group':source_id,
                'source_asset':asset_id,'target_images':settings.per_group})
    service.enqueue(new_id)
    return new_id


async def revise_result_prompt(service, task_id, settings, group, params, current):
    previous = PromptPlan.model_validate_json(current['body']) if current else PromptPlan(
        group_id=group['id'],positive=settings.direct_prompt,negative=settings.direct_negative,params=params,reason='所选原图的提示词')
    review = service.db.one('''SELECT e.body FROM evaluations e JOIN assets a ON a.id=e.asset_id
        WHERE a.group_id=? AND e.stage='final' ORDER BY e.rowid DESC LIMIT 1''',(group['id'],))
    payload = {'goal':settings.goal,'group_id':group['id'],'params':params,
        'current':{'positive':previous.positive,'negative':previous.negative}, 'manual_retry':True,
        'feedback':{'human':settings.result_retry.feedback,
            'review':compact_review(json.loads(review['body'])) if review else settings.result_retry.review},
        'character':character_for_group(settings,0),
        'control_words':[word.model_dump() for word in controls_for_group(settings,0)],
        'target_style':settings.target_styles[0] if settings.target_styles else '',
        'iteration_rule':'Revise this exact image prompt using its own review and human feedback. Preserve its subject, character identity, user controls, scene and intended composition. Fix only supported defects; do not invent a new theme or copy another image. Return a revised prompt, not merely a new seed.'}
    payload['round_improvements']=round_feedback(service,settings.result_retry.source_task)['directions']
    plan, _ = await service.cloud.request(task_id,settings,'prompt_generation',PromptPlan,payload)
    if plan.group_id != group['id']:
        raise CloudError('PROMPT_GROUP_ID_MISMATCH')
    plan.params = settings.params.model_copy(update={'seed':params['seed']})
    plan = enforce_lora_triggers(enforce_control_words(plan,controls_for_group(settings,0)),settings.lora_trigger_words)
    if plan.positive == previous.positive and plan.negative == previous.negative:
        raise CloudError('RESULT_RETRY_PROMPT_UNCHANGED')
    return plan
