import asyncio
import json
from unittest.mock import AsyncMock

import gradio as gr
import pytest
from PIL import Image
from pydantic import ValidationError

from supervisor.db import uid, now
from supervisor.group_queue import group_queue_html
from supervisor.models import Evaluation, PromptPlan, StyleCard, TaskSettings
from supervisor.providers import CloudError
from supervisor.ui import build_ui
from supervisor.wire import ReviewReply, expand_reply
from test_review_format import review
from test_live_readiness import configure_cloud
import httpx
from supervisor.comfy import Comfy, GenerationError
from test_workflow_editor import configure_editor


def direct(groups=2, **extra):
    return TaskSettings(**dict(dict(goal='mountains',direct_prompt='mountains',autonomous=True,
        review_enabled=False,demo=True,groups=groups,per_group=1,max_rounds=1,
        params={'width':64,'height':64}),**extra))


@pytest.mark.parametrize('annotation', [
    {'location':'left figure, right arm'}, {'evidence_dup':'fused at wrist','category_dup':'anatomy_error'},
    {'evidence_scope':'inside the frame','evidence_note':'not an occlusion','severity_note':'visible defect','evidence_placeholder':'right arm'},
])
def test_real_review_problem_annotations_keep_scores_and_evidence(annotation):
    body=review()
    body['problems']=[dict(category='anatomy_error',severity='major',evidence='Two arms share one wrist',**annotation)]
    result=expand_reply(Evaluation,ReviewReply.model_validate(body),{'asset_id':'asset'})
    assert result.anatomy==90 and result.safety_score==95
    assert result.issues[0].evidence.startswith('Two arms share one wrist')
    assert result.issues[0].region==annotation.get('location')


@pytest.mark.parametrize('annotation', [{'category_dup':'blur'},{'location':42},{'evidence_note':{'score':100}},{'extra_limbs':True}])
def test_problem_compatibility_does_not_ignore_conflicting_or_structural_fields(annotation):
    body=review();body['problems']=[dict(category='anatomy_error',severity='major',evidence='Fused wrist',**annotation)]
    with pytest.raises(ValidationError):ReviewReply.model_validate(body)


async def test_observed_location_reply_succeeds_without_an_extra_paid_retry(service):
    body=review();body['problems']=[{'category':'anatomy_error','severity':'major','evidence':'Fused wrist','location':'left figure'}]
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(body)}}], 'usage':{'prompt_tokens':10,'completion_tokens':20}})
    configure_cloud(service,httpx.MockTransport(handler))
    settings=TaskSettings(goal='QA',demo=False,reference_only=True,content_label='sfw')
    task=service.create_task(settings,start=False)
    result,call=await service.cloud.request(task,settings,'review',Evaluation,{'asset_id':'a','stage':'final'})
    assert len(calls)==1 and result.issues[0].region=='left figure'
    assert service.db.one('SELECT status FROM api_calls WHERE id=?',(call,))['status']=='SUCCESS'
    assert service.db.token_usage(task)==30


async def test_append_running_round_before_reference_analysis_copies_inputs(service,tmp_path):
    service._dispatch_next=lambda:None
    image=tmp_path/'ref.png';Image.new('RGB',(64,64),'green').save(image)
    old=service.create_task(TaskSettings(goal='forest',autonomous=True,demo=True,groups=2,target_styles=['forest','beach']),[image])
    new=service.restart_task(old)
    original=service.db.one('SELECT * FROM assets WHERE task_id=?',(old,))
    copied=service.db.one('SELECT * FROM assets WHERE task_id=?',(new,))
    assert copied['sha256']==original['sha256'] and copied['id']!=original['id']
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(old,))['state']=='RUNNING'
    assert not service.db.rows('SELECT * FROM prompt_variants WHERE task_id=?',(new,))


async def test_cancel_queued_group_only_and_duplicate_rounds_remain_independent(service):
    service._dispatch_next=lambda:None
    first=service.create_task(direct())
    await service.prepare(first,service.settings(first))
    second=service.restart_task(first)
    first_groups=service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(first,))
    second_groups=service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(second,))
    html=group_queue_html(service,second)
    assert all(g['id'] in html for g in first_groups+second_groups)
    assert html.count('class="group-queue-row"')==4
    assert html.count('· 第 1 组 ·')==2
    service.cancel_group(first,first_groups[0]['id'])
    service.cancel_group(first,first_groups[0]['id'])  # idempotent
    assert service.group_cancelled(first_groups[0]['id'])
    assert all(not service.group_cancelled(g['id']) for g in second_groups)
    await service.run(first)
    await service.run(second)
    assert [a['group_id'] for a in service.db.accepted(first)]==[first_groups[1]['id']]
    assert len(service.db.accepted(second))==2
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(first,))['state']=='COMPLETED'


@pytest.mark.parametrize('state',['COMPLETED','PARTIAL','CANCELLED','FAILED','WAITING_APPROVAL','PAUSED'])
def test_group_queue_does_not_include_selected_history_or_other_inactive_tasks(service,state):
    service._dispatch_next=lambda:None
    old=service.create_task(direct())
    active=service.create_task(direct())
    service.db.transition(old,state,'DELIVER','USER_STOP')
    html=group_queue_html(service,old)
    assert old[:8] not in html and active[:8] in html
    service.db.transition(active,'COMPLETED','DELIVER','TARGET_REACHED')
    assert '当前没有执行或排队' in group_queue_html(service,old)


def test_round_paused_queue_remains_visible_but_is_not_marked_executing(service):
    service._dispatch_next=lambda:None
    task=service.create_task(direct())
    service.db.transition(task,'PAUSED','QUEUED','ROUND_ENDED')
    html=group_queue_html(service)
    assert task[:8] in html and '已暂停' in html
    assert 'data-current-group' not in html


async def test_only_current_group_has_theme_line_and_queued_tasks_are_not_processing(service):
    from supervisor.group_queue import GROUP_QUEUE_CSS
    service._dispatch_next=lambda:None
    current,queued=[service.create_task(direct()) for _ in range(2)]
    await service.prepare(current,service.settings(current))
    groups=service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(current,))
    service.executing_task=current
    service.db.transition(current,'RUNNING','PROMPT')
    html=group_queue_html(service,queued)
    assert html.count('data-current-group="true"')==1 and html.count('正在处理')==1
    assert f'data-group-id="{groups[0]["id"]}" data-current-group="true"' in html
    assert html.count('待执行')==3
    service.db.execute('UPDATE groups SET round_index=1 WHERE id=?',(groups[0]['id'],))
    html=group_queue_html(service,queued)
    assert f'data-group-id="{groups[1]["id"]}" data-current-group="true"' in html
    assert 'var(--page-accent)' in GROUP_QUEUE_CSS


async def test_cancelling_current_generation_collects_image_skips_review_and_continues_other_group(service):
    service._dispatch_next=lambda:None
    task=service.create_task(direct(review_enabled=True))
    groups=service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(task,))
    original_run=service.comfy.run
    async def mocked_run(generation,*args,**kwargs):
        if generation['group_id']==groups[0]['id']:
            service.cancel_group(task,groups[0]['id'])
        return await original_run(generation,*args,**kwargs)
    service.comfy.run=mocked_run
    original_cloud=service.cloud.request
    async def cloud(task_id,settings,purpose,contract,payload,*args,**kwargs):
        assert purpose=='review' and payload['prompt']['positive']=='mountains'
        asset=service.db.one('SELECT group_id FROM assets WHERE id=?',(payload['asset_id'],))
        assert asset['group_id']==groups[1]['id']
        return await original_cloud(task_id,settings,purpose,contract,payload,*args,**kwargs)
    service.cloud.request=cloud
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    kept=service.db.one('SELECT * FROM assets WHERE group_id=?',(groups[0]['id'],))
    assert kept and service.files.path(kept['path']).is_file() and kept['state']=='AVAILABLE'
    assert not service.db.one('SELECT id FROM evaluations WHERE asset_id=?',(kept['id'],))
    assert len(service.db.accepted(task))==1


async def test_cancel_during_prompt_failure_does_not_pause_other_groups(service,tmp_path):
    service._dispatch_next=lambda:None
    image=tmp_path/'ref.png';Image.new('RGB',(64,64),'green').save(image)
    task=service.create_task(TaskSettings(goal='landscape',demo=True,autonomous=True,
        groups=2,per_group=1,max_rounds=1,target_styles=['forest','beach'],params={'width':64,'height':64}),[image])
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',(uid(),task,StyleCard().model_dump_json(),now()))
    groups=service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(task,))
    async def cloud(task_id,settings,purpose,contract,payload,*args):
        if purpose=='review':
            return expand_reply(Evaluation,ReviewReply.model_validate(review()),payload),None
        if payload['group_id']==groups[0]['id']:
            service.cancel_group(task,groups[0]['id'])
            raise CloudError('INVALID_RESPONSE_REVIEW_REQUIRED')
        return PromptPlan(group_id=payload['group_id'],positive='beach',reason='QA'),None
    service.cloud.request=cloud
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    assert not service.db.one('SELECT id FROM generations WHERE group_id=?',(groups[0]['id'],))
    assert len(service.db.accepted(task))==1


def test_cancel_all_queued_groups_releases_task_without_touching_next_task(service):
    service._dispatch_next=lambda:None
    first,second=[service.create_task(direct()) for _ in range(2)]
    for g in service.db.rows('SELECT id FROM groups WHERE task_id=?',(first,)):
        service.cancel_group(first,g['id'])
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(first,))['state']=='CANCELLED'
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(second,))['state']=='RUNNING'
    assert first[:8] not in group_queue_html(service)
    with pytest.raises(ValueError):service.cancel_group(first,service.db.one('SELECT id FROM groups WHERE task_id=?',(second,))['id'])


async def test_cancel_all_groups_on_reopened_task_keeps_cancelled_state_and_delivered_images(service):
    service._dispatch_next=lambda:None
    task=service.create_task(direct(groups=1))
    await service.run(task)
    images=[(service.files.path(a['path']),service.files.path(a['path']).read_bytes()) for a in service.db.accepted(task)]
    service.db.transition(task,'PAUSED','LIMITS_UPDATED')
    group=service.db.one('SELECT id FROM groups WHERE task_id=?',(task,))['id']
    service.cancel_group(task,group)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='CANCELLED'
    assert all(path.read_bytes()==original for path,original in images)


async def test_cancel_while_preparing_comfy_submission_never_posts_prompt(service,monkeypatch):
    configure_editor(service)
    service._dispatch_next=lambda:None
    task=service.create_task(direct(groups=2,demo=False))
    await service.prepare(task,service.settings(task))
    group=service.db.one('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(task,))
    variant=service.db.one('SELECT * FROM prompt_variants WHERE group_id=?',(group['id'],))
    generation=uid()
    service.db.execute("INSERT INTO generations(id,task_id,group_id,variant_id,submission_token,client_id,state,graph,graph_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,'PREPARED','{}','hash',?,?)",(generation,task,group['id'],variant['id'],uid(),uid(),now(),now()))
    async def socket(*args,**kwargs):raise OSError('mock only')
    monkeypatch.setattr('supervisor.comfy.websockets.connect',socket)
    requests=[]
    def handler(request):
        requests.append(request)
        raise AssertionError('No submission is allowed after cancellation')
    comfy=Comfy(service.workflow,service.project_root,service.db,httpx.MockTransport(handler))
    async def metadata(*args):
        service.cancel_group(task,group['id'])
        return None
    comfy.workflow_metadata=metadata
    with pytest.raises(GenerationError,match='GROUP_CANCELLED'):
        await comfy.run(service.db.one('SELECT * FROM generations WHERE id=?',(generation,)),lambda *args:None)
    assert not requests
    assert service.db.one('SELECT state FROM generations WHERE id=?',(generation,))['state']=='FAILED'
    assert service.next_group(task,service.settings(task))['ordinal']==1


def test_result_queue_ui_callback_and_feedback(service,monkeypatch):
    service._dispatch_next=lambda:None;service.enqueue=lambda _:None
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    task=service.create_task(direct())
    app=build_ui(service)
    callbacks={f.fn.__name__:f for f in app.fns.values() if f.fn}
    refresh=callbacks['refresh_all']
    before=refresh.fn(task,'zh')
    assert len(before)==len(refresh.outputs) and task[:8] in before[-2]
    assert '各组进度' not in before[21]
    group=service.db.one('SELECT id FROM groups WHERE task_id=? ORDER BY ordinal',(task,))['id']
    text,response=callbacks['cancel_result_group'].fn(json.dumps({'task_id':task,'group_id':group,'request_id':'req'}),'zh')
    assert '已有图片保留' in text and json.loads(response)=={'request_id':'req','ok':True,'text':text}
    assert group not in refresh.fn(task,'zh')[-2]
    choice,message=callbacks['continue_task'].fn(task,'zh')
    assert choice['value']!=task and '新一轮' in message
    assert '第 1 组' in refresh.fn(choice['value'],'zh')[-2]


async def test_group_buttons_are_exclusive_for_finished_running_and_unstarted_groups(service,tmp_path):
    from test_result_batch import source_task
    old,assets=await source_task(service,tmp_path)
    completed_group=assets[0]['group_id']
    service.db.transition(old,'RUNNING','GENERATE')
    queued=service.create_task(direct(groups=2))
    await service.prepare(queued,service.settings(queued))
    groups=service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(queued,))
    service.executing_task=queued
    service.db.transition(queued,'RUNNING','PROMPT')
    html=group_queue_html(service)
    import re
    def row(group):
        return re.search(r'<div class="group-queue-row" data-group-id="'+group+r'".*?</div>',html).group(0)
    assert 'group-retry-button' in row(completed_group) and 'group-cancel-button' not in row(completed_group)
    assert 'group-cancel-button' in row(groups[0]['id']) and 'group-retry-button' not in row(groups[0]['id'])
    assert 'group-cancel-button' in row(groups[1]['id']) and 'group-retry-button' not in row(groups[1]['id'])
    # Even a group with previous output must offer delete while it is running.
    service.executing_task=old
    service.db.execute("UPDATE generations SET state='EVALUATE' WHERE id=?",(assets[-1]['generation_id'],))
    html=group_queue_html(service)
    assert 'group-cancel-button' in row(completed_group) and 'group-retry-button' not in row(completed_group)


async def test_group_redraw_ui_has_feedback_without_switching_selected_task(service,tmp_path,monkeypatch):
    from test_result_batch import source_task
    old,assets=await source_task(service,tmp_path)
    monkeypatch.setattr('supervisor.result_retry.check_live_ready',AsyncMock())
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    monkeypatch.setattr(gr,'Warning',lambda *a,**k:None)
    app=build_ui(service)
    callback=next(f.fn for f in app.fns.values() if getattr(f.fn,'__name__','')=='retry_result_group')
    request=json.dumps({'task_id':old,'group_id':assets[0]['group_id'],'request_id':'group_ui_redraw_001'})
    text,response=await callback(request,'zh')
    assert '本组重绘队列' in text and '原图保留' in text and json.loads(response)['ok']
    assert len(next(f for f in app.fns.values() if f.fn==callback).outputs)==2
    assert not json.loads((await callback('[]','zh'))[1])['ok']
