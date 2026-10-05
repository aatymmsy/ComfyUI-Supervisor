import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import gradio as gr
import httpx
import pytest
from pydantic import ValidationError

from conftest import ui_callbacks
from test_result_batch import source_task
from test_supervisor import cloud_config
from supervisor.db import BudgetExceeded
from supervisor.discussion import Discussion, DiscussionReply
from supervisor.providers import Cloud, CloudError
from supervisor.ui import build_ui


def configure_discussion(service, replies):
    calls=[]
    def respond(request):
        assert request.url.path == '/v1/chat/completions'
        body=json.loads(request.content)
        calls.append(body)
        reply=replies[min(len(calls)-1,len(replies)-1)]
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(reply)}}],
                                      'usage':{'prompt_tokens':10,'completion_tokens':15}})
    service.config=cloud_config()
    service.cloud=Cloud(service.config,service.db,httpx.MockTransport(respond),{'TEST_SUPERVISOR_KEY':'fixture'})
    return calls


READY={'answer':'已整理为完整的山景提示词。','prompt_ready':True,
       'positive':'layered mountain landscape, warm morning light','negative':'blur, merged ridges'}


async def test_discussion_has_context_deduplicates_and_never_executes(service,monkeypatch):
    calls=configure_discussion(service,[{'answer':'你更喜欢清晨还是傍晚？','prompt_ready':False,'positive':None,'negative':''},READY])
    service.enqueue=lambda *a:pytest.fail('Discussion must not enqueue work')
    service.create_task=lambda *a,**kw:pytest.fail('Discussion must not create generation tasks')
    chat=Discussion(service)
    session,first,_=await chat.send(None,'想要山景提示词','request-one')
    same=await chat.send(None,'想要山景提示词','request-one')
    assert same[:2]==(session,first) and len(calls)==1
    session,turn,reply=await chat.send(session,'选清晨暖光','request-two',language='zh')
    payload=json.loads(calls[-1]['messages'][1]['content'][0]['text'])['data']
    assert len(payload['conversation'])==3 and payload['conversation'][0]['content']=='想要山景提示词'
    assert payload['response_language']=='Simplified Chinese'
    assert 'tools' not in calls[-1] and 'only text conversation' in calls[-1]['messages'][0]['content']
    assert chat.prompt(session,turn)==(READY['positive'],READY['negative'])
    assert len(chat.history(session))==4 and len(chat.prompts(session))==1
    assert service.db.budget(session)['spent']==80
    assert not service.db.rows('SELECT * FROM generations') and not service.db.rows('SELECT * FROM groups')
    assert not service.db.rows('SELECT * FROM task_configs') and not service.db.rows('SELECT * FROM assets')
    assert not service.active and '等待 0' in service.queue_status()
    with pytest.raises(ValueError,match='只能发送消息'):
        service.resume(session)
    await service.run(session)
    assert not service.db.rows('SELECT * FROM generations')
    app=build_ui(service)
    task=next(block for block in app.blocks.values() if isinstance(block,gr.Dropdown) and block.label in ('Current task','当前任务'))
    assert not task.choices


async def test_failure_keeps_message_and_budget_refuses_before_network(service):
    calls=configure_discussion(service,[READY])
    chat=Discussion(service)
    with pytest.raises(BudgetExceeded):
        await chat.send(None,'山景','small-budget',budget=.01)
    turn=service.db.one('SELECT * FROM discussion_turns')
    assert turn['state']=='FAILED' and turn['message']=='山景' and calls==[]
    assert chat.history(turn['session_id'])[0]['content']=='山景'
    assert service.db.budget(turn['session_id'])['spent']==0
    with pytest.raises(ValueError,match='已经提交'):
        await chat.send(turn['session_id'],'山景','small-budget')


async def test_invalid_reply_cannot_enable_application_or_execute(service):
    configure_discussion(service,[{**READY,'tool_call':{'name':'generate'}}])
    chat=Discussion(service)
    with pytest.raises(CloudError,match='INVALID_RESPONSE_DISCUSSION'):
        await chat.send(None,'讨论提示词','invalid-reply')
    turn=service.db.one('SELECT * FROM discussion_turns')
    assert chat.prompts(turn['session_id'])==[] and not service.db.rows('SELECT * FROM generations')
    with pytest.raises(ValueError):chat.prompt(turn['session_id'],turn['id'])


@pytest.mark.parametrize('reply',[{**READY,'positive':' '},{**READY,'prompt_ready':False},
                                  {**READY,'prompt_ready':'true'}])
def test_ready_prompt_must_be_consistent_and_typed(reply):
    with pytest.raises(ValidationError):DiscussionReply.model_validate(reply)


async def test_image_jump_prefills_selected_round_and_apply_only_fills_form(service,tmp_path,monkeypatch):
    task,assets=await source_task(service,tmp_path)
    source=service.db.one('''SELECT p.body FROM prompt_variants p JOIN generations n ON n.variant_id=p.id
                            WHERE n.id=?''',(assets[0]['generation_id'],))
    original=json.loads(source['body'])
    calls=configure_discussion(service,[READY])
    monkeypatch.setattr(gr,'Warning',lambda *a,**kw:None)
    callbacks=ui_callbacks(build_ui(service))
    before=len(service.db.rows('SELECT * FROM generations'))
    result=callbacks['discuss_result_prompt'](json.dumps({'task_id':task,'asset_id':assets[0]['id'],'request_id':'jump'}),'zh')
    assert original['positive'] in result[3] and original['negative'] in result[3]
    assert result[2]==[] and 'selected' not in result[15] and result[16]=='discussion' and json.loads(result[-1])['ok']
    assert calls==[] and not service.db.rows('SELECT * FROM discussion_sessions')
    outputs=[part async for part in callbacks['send_discussion'](None,'send-ui','需要清晨山景','sfw',1,'zh')]
    assert '等待模型回复' in outputs[0][9]
    last=outputs[-1]
    assert last[6:8]==(READY['positive'],READY['negative']) and last[8]['interactive']
    session,turn=last[0],last[5]['value']
    applied=callbacks['apply_discussion_prompt'](session,turn,'zh')
    assert applied[:2]==(READY['positive'],READY['negative']) and 'selected' not in applied[5] and applied[6]=='prompt-studio' and applied[-1] is True
    assert len(service.db.rows('SELECT * FROM generations'))==before and len(calls)==1
    invalid=callbacks['apply_discussion_prompt'](session,'missing-turn','zh')
    assert invalid[-1] is False and all('value' not in value for value in invalid[:-1])
    restored=callbacks['load_discussion'](session)
    assert len(restored[2])==2 and restored[6]==READY['positive']
    fresh=callbacks['new_discussion']()
    assert fresh[0] is None and fresh[2]==[] and len(Discussion(service).sessions())==1


async def test_unconfigured_discussion_and_empty_messages_do_not_create_accounts(service):
    chat=Discussion(service)
    with pytest.raises(ValueError,match='讨论内容'):
        await chat.send(None,' ','empty')
    with pytest.raises(ValueError,match='API 连接'):
        await chat.send(None,'山景','unconfigured')
    assert not service.db.rows('SELECT * FROM tasks')


async def test_new_discussion_inherits_saved_scope_and_configuration_changes(service):
    from supervisor.models import Policy
    calls=configure_discussion(service,[READY])
    provider=service.config.providers[0]
    provider.policy=Policy(nsfw_policy='allowed',allowed_content=['adult_allowed'],
        evidence='confirmed',review_due='2099-01-01T00:00:00+00:00')
    chat=Discussion(service)
    assert chat.configured_scope()=='adult_allowed' and '允许成人内容' in chat.cost(None)
    session,_,_=await chat.send(None,'讨论山景','inherited-adult')
    assert chat.session(session).content_label=='adult_allowed' and len(calls)==1
    with pytest.raises(ValueError,match='沿用 API 配置'):
        await chat.send(None,'讨论山景','wrong-scope',label='sfw')
    assert len(calls)==1 and len(chat.sessions())==1
    provider.policy=Policy(nsfw_policy='forbidden',allowed_content=['sfw'])
    next_session,_,_=await chat.send(None,'新的山景','inherited-sfw')
    assert chat.session(next_session).content_label=='sfw' and len(calls)==2
    # Never silently change the scope of an existing discussion.
    with pytest.raises(Exception):
        await chat.send(session,'继续旧讨论','old-scope')
    assert chat.session(session).content_label=='adult_allowed' and len(calls)==2


def test_discussion_ui_defaults_and_reset_follow_saved_api_configuration(service):
    configure_discussion(service,[READY])
    app=build_ui(service)
    event=next(event for event in app.fns.values() if event.fn and event.fn.__name__=='send_discussion')
    scope=event.inputs[3]
    assert scope.value=='configured' and ('沿用 API 配置','configured') in scope.choices
    fresh=ui_callbacks(app)['new_discussion']()
    assert fresh[10]['value']=='configured' and '普通内容' in fresh[9]


async def test_shared_provider_rate_slots_do_not_collide(service,monkeypatch):
    from supervisor import providers
    monkeypatch.setattr(providers,'time',SimpleNamespace(monotonic=lambda:100))
    sleeper=AsyncMock()
    monkeypatch.setattr(providers.asyncio,'sleep',sleeper)
    provider=SimpleNamespace(id='shared',rpm=60)
    await asyncio.gather(*(service.cloud.wait_turn(provider) for _ in range(3)))
    assert [call.args[0] for call in sleeper.await_args_list]==[1,2]
