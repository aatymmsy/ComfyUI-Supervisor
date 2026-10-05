from unittest.mock import AsyncMock

import pytest
from PIL import Image

from supervisor.db import uid, now
from supervisor.models import Change, PromptPlan, StyleCard, TaskSettings
from supervisor.providers import CloudError
from supervisor.studio import task_progress


def iteration_task(service,tmp_path):
    image=tmp_path/'ref.png'
    Image.new('RGB',(64,64),'green').save(image)
    settings=TaskSettings(goal='keep mountains',autonomous=True,groups=1,target_styles=['ink'],params={'seed':42})
    task=service.create_task(settings,[image])
    group=service.db.one('SELECT * FROM groups WHERE task_id=?',(task,))
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',(uid(),task,StyleCard(subject=['mountains'],locked_attributes=['subject']).model_dump_json(),now()))
    initial=PromptPlan(group_id=group['id'],positive='mountains, ink',reason='initial',params=settings.params)
    service.db.execute('INSERT INTO prompt_variants VALUES(?,?,?,?,?,?,?,?)',(uid(),task,group['id'],None,0,initial.model_dump_json(),'USED',now()))
    return task,settings,group


@pytest.mark.parametrize('field,canonical',[('positive.style_tags','style'),('negative_prompt','negative'),('positive_prompt','detail')])
async def test_normal_iteration_aliases_are_approved(service,tmp_path,field,canonical):
    task,settings,group=iteration_task(service,tmp_path)
    proposed=PromptPlan(group_id=group['id'],positive='mountains, balanced ink',negative='blur',reason='improve balance',
        changes=[Change(field=field,before='old',after='new',hypothesis='quality')],params={'seed':999})
    service.cloud.request=AsyncMock(return_value=(proposed,None))
    row=await service.plan(task,settings,group)
    saved=PromptPlan.model_validate_json(row['body'])
    assert row['status']=='APPROVED' and saved.changes[0].field==canonical
    assert saved.params.seed==42
    payload=service.cloud.request.call_args.args[-1]
    assert 'style' in payload['allowed_change_fields'] and 'dotted paths' in payload['iteration_rule']


async def test_autonomous_text_rewrite_without_change_report_does_not_pause(service,tmp_path):
    task,settings,group=iteration_task(service,tmp_path)
    service.cloud.request=AsyncMock(return_value=(PromptPlan(group_id=group['id'],positive='mountains, refined ink',reason='improve clarity'),None))
    row=await service.plan(task,settings,group)
    assert row['status']=='APPROVED'
    assert PromptPlan.model_validate_json(row['body']).changes[0].field=='detail'


async def test_disallowed_parameter_change_gets_one_correction_and_stays_bounded(service,tmp_path):
    task,settings,group=iteration_task(service,tmp_path)
    proposed=PromptPlan(group_id=group['id'],positive='mountains',reason='change seed',changes=[Change(field='seed',before='42',after='999',hypothesis='test')])
    service.cloud.request=AsyncMock(return_value=(proposed,None))
    with pytest.raises(CloudError,match='ITERATION_CHANGE_SCOPE_INVALID'):
        await service.plan(task,settings,group)
    assert service.cloud.request.await_count==2
    service.db.transition(task,'PAUSED','RECOVERABLE_ERROR','ITERATION_CHANGE_SCOPE_INVALID')
    progress=task_progress(service,task)
    assert '提示词迭代' in progress and '工作流执行失败' not in progress


async def test_accepted_prompt_is_reused_without_cloud_rewrite(service,tmp_path):
    task,settings,group=iteration_task(service,tmp_path)
    variant=service.db.one('SELECT id FROM prompt_variants WHERE task_id=?',(task,))
    generation=uid()
    service.db.execute('INSERT INTO generations(id,task_id,group_id,variant_id,submission_token,client_id,state,graph,graph_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (generation,task,group['id'],variant['id'],uid(),uid(),'DECIDED','{}','test',now(),now()))
    asset=service.db.one('SELECT id FROM assets WHERE task_id=?',(task,))
    service.db.execute("UPDATE assets SET source_kind='generated',generation_id=?,group_id=? WHERE id=?",(generation,group['id'],asset['id']))
    service.decision(asset['id'],None,'ACCEPTED','test')
    service.cloud.request=AsyncMock(side_effect=AssertionError('No paid rewrite for an accepted prompt'))
    group['round_index']=1
    row=await service.plan(task,settings,group)
    saved=PromptPlan.model_validate_json(row['body'])
    assert row['status']=='APPROVED' and saved.positive=='mountains, ink' and saved.params.seed==43
    assert service.cloud.request.await_count==0
