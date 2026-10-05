import json
from unittest.mock import AsyncMock

import gradio as gr
import pytest

from supervisor.comfy import Comfy
from supervisor.models import PromptPlan, WorkflowConfig
from supervisor.providers import CloudError
from supervisor.result_batch import result_recipe
from supervisor.result_retry import retry_from_result, retry_from_group
from supervisor.ui import build_ui
from supervisor.wire import request_payload
from test_result_batch import source_task


async def test_retry_selected_prompt_controls_and_recipe_preserved_until_worker(service,tmp_path,monkeypatch):
    old,assets=await source_task(service,tmp_path,character='mountain spirit',lora_trigger_words='ink style',
        control_words=[{'word':'sunrise','weight':1.3,'group':1}])
    source_plan=PromptPlan.model_validate_json(service.db.one('''SELECT p.body FROM prompt_variants p
        JOIN generations n ON n.variant_id=p.id WHERE n.id=?''',(assets[0]['generation_id'],))['body'])
    ready=AsyncMock();monkeypatch.setattr('supervisor.result_retry.check_live_ready',ready)
    service.cloud.request=AsyncMock(side_effect=AssertionError('No paid calls while queuing'))
    new=await retry_from_result(service,old,assets[0]['id'],'retry_selected_0001','改善远山轮廓')
    service.cloud.request.assert_not_called()
    settings=service.settings(new)
    assert settings.character=='mountain spirit' and settings.control_words[0].group==1
    assert settings.lora_trigger_words=='ink style' and settings.direct_prompt==source_plan.positive
    assert settings.params.seed==source_plan.params.seed+1 and settings.params.cfg==source_plan.params.cfg
    assert settings.result_retry.source_asset==assets[0]['id'] and settings.per_group==1
    assert ready.call_args.kwargs['purposes']==(('prompt_generation','提示词修订',0),)
    calls=[]
    async def revise(task,settings,purpose,contract,payload,images=None):
        calls.append(payload)
        assert purpose=='prompt_generation' and payload['current']['positive']==source_plan.positive
        assert payload['feedback']['human']=='改善远山轮廓' and payload['manual_retry']
        return PromptPlan(group_id=payload['group_id'],positive='layered mountains, distinct distant silhouettes',
            negative='blur, merged ridges',params={'seed':999,'cfg':18},reason='clarify distant contours'),None
    service.cloud.request=AsyncMock(side_effect=revise)
    snapshot=WorkflowConfig.model_validate_json(service.db.one('SELECT workflow_json FROM task_configs WHERE task_id=?',(new,))['workflow_json'])
    service.comfy=Comfy(snapshot,service.project_root,service.db)
    service.comfy.run=AsyncMock(return_value=[{'node_id':'9','output_index':0,'identity':'mock','demo':True}])
    await service.run(new)
    plan=PromptPlan.model_validate_json(service.db.one('SELECT body FROM prompt_variants WHERE task_id=?',(new,))['body'])
    assert len(calls)==1 and '(sunrise:1.3)' in plan.positive and 'ink style' in plan.positive
    assert plan.params.seed==settings.params.seed and plan.params.cfg==4
    assert len(service.db.accepted(new))==1 and service.files.path(assets[0]['path']).is_file()
    assert service.db.one("SELECT kind FROM human_feedback WHERE asset_id=?",(assets[0]['id'],))['kind']=='RETRY'
    assert result_recipe(service,new,service.db.accepted(new)[0]['id'],1,False)[0].result_retry is None


async def test_retry_duplicate_and_cross_operation_request_ids(service,tmp_path,monkeypatch):
    old,assets=await source_task(service,tmp_path)
    monkeypatch.setattr('supervisor.result_retry.check_live_ready',AsyncMock())
    new=await retry_from_result(service,old,assets[0]['id'],'retry_duplicate_01')
    assert await retry_from_result(service,old,assets[0]['id'],'retry_duplicate_01')==new
    assert len(service.db.rows('SELECT * FROM human_feedback'))==1
    with pytest.raises(ValueError,match='已使用'):
        await retry_from_result(service,old,assets[1]['id'],'retry_duplicate_01')
    with pytest.raises(ValueError,match='来源任务'):
        await retry_from_result(service,'missing-task',assets[0]['id'],'retry_missing_0001')
    service.delete_generated(old,assets[1]['id'])
    with pytest.raises(ValueError):
        await retry_from_result(service,old,assets[1]['id'],'retry_deleted_0001')


async def test_retry_unchanged_prompt_stops_before_generation(service,tmp_path,monkeypatch):
    old,assets=await source_task(service,tmp_path)
    monkeypatch.setattr('supervisor.result_retry.check_live_ready',AsyncMock())
    new=await retry_from_result(service,old,assets[0]['id'],'retry_unchanged_01')
    async def unchanged(task,settings,purpose,contract,payload,images=None):
        return PromptPlan(group_id=payload['group_id'],**payload['current'],params=payload['params'],reason='unchanged'),None
    service.cloud.request=AsyncMock(side_effect=unchanged)
    service.comfy.run.reset_mock()
    await service.run(new)
    service.comfy.run.assert_not_called()
    assert not service.db.rows('SELECT id FROM generations WHERE task_id=?',(new,))
    assert service.db.one('SELECT reason FROM tasks WHERE id=?',(new,))['reason']=='RESULT_RETRY_PROMPT_UNCHANGED'


async def test_retry_ui_keeps_current_task_and_wire_does_not_invent_theme(service,tmp_path,monkeypatch):
    old,assets=await source_task(service,tmp_path)
    monkeypatch.setattr('supervisor.result_retry.check_live_ready',AsyncMock())
    monkeypatch.setattr(gr,'Info',lambda *a,**kw:None)
    monkeypatch.setattr(gr,'Warning',lambda *a,**kw:None)
    app=build_ui(service)
    callback=next(f.fn for f in app.fns.values() if getattr(f.fn,'__name__','')=='create_result_retry')
    choice,text,response=await callback(json.dumps({'task_id':old,'asset_id':assets[0]['id'],'request_id':'retry_ui_queue001'}),'zh')
    assert 'value' not in choice and json.loads(response)['ok'] and '原图保留' in text
    assert json.loads((await callback('[]','zh'))[2])['ok'] is False
    payload=request_payload(PromptPlan,{'manual_retry':True,'current':{'positive':'mountains'},'feedback':{'human':'fix contours'}})
    assert 'Invent' not in payload['instruction'] and 'Preserve' in payload['instruction']


async def test_group_redraw_reuses_latest_own_prompt_and_target_and_is_idempotent(service,tmp_path,monkeypatch):
    old,assets=await source_task(service,tmp_path,character='mountain spirit',
        control_words=[{'word':'sunrise','weight':1.3,'group':1}])
    monkeypatch.setattr('supervisor.result_retry.check_live_ready',AsyncMock())
    latest=assets[-1]
    variant=service.db.one('SELECT variant_id FROM generations WHERE id=?',(latest['generation_id'],))['variant_id']
    body=json.loads(service.db.one('SELECT body FROM prompt_variants WHERE id=?',(variant,))['body'])
    body['positive']='latest mountains, clear ridges'
    service.db.execute('UPDATE prompt_variants SET body=? WHERE id=?',(json.dumps(body),variant))
    before=[service.files.path(asset['path']).read_bytes() for asset in assets]
    new=await retry_from_group(service,old,latest['group_id'],'group_redraw_request001')
    settings=service.settings(new)
    assert settings.direct_prompt==body['positive'] and settings.result_retry.source_asset==latest['id']
    assert settings.per_group==2 and settings.groups==1 and settings.character=='mountain spirit'
    assert settings.control_words[0].word=='sunrise' and settings.params.cfg==4
    assert await retry_from_group(service,old,latest['group_id'],'group_redraw_request001')==new
    assert len(service.db.rows("SELECT * FROM events WHERE kind='GROUP_RETRY_CREATED'"))==1
    assert before==[service.files.path(asset['path']).read_bytes() for asset in assets]
    with pytest.raises(ValueError,match='已使用'):
        await retry_from_result(service,old,latest['id'],'group_redraw_request001')


async def test_group_redraw_rejects_running_unstarted_or_wrong_owner_groups(service,tmp_path,monkeypatch):
    from supervisor.models import TaskSettings
    old,assets=await source_task(service,tmp_path)
    ready=AsyncMock();monkeypatch.setattr('supervisor.result_retry.check_live_ready',ready)
    unstarted=service.create_task(TaskSettings(goal='forest',direct_prompt='forest',groups=1))
    group=service.db.one('SELECT id FROM groups WHERE task_id=?',(unstarted,))['id']
    with pytest.raises(ValueError,match='尚无'):
        await retry_from_group(service,unstarted,group,'group_not_run_request1')
    with pytest.raises(ValueError,match='不存在'):
        await retry_from_group(service,old,group,'group_wrong_owner001')
    service.db.execute("UPDATE generations SET state='EVALUATE' WHERE id=?",(assets[-1]['generation_id'],))
    with pytest.raises(ValueError,match='仍在运行'):
        await retry_from_group(service,old,assets[0]['group_id'],'group_still_running001')
    ready.assert_not_called()


async def test_retry_keeps_scoped_source_character_and_batch_does_not_inherit_role_rows(service,tmp_path,monkeypatch):
    old,assets=await source_task(service,tmp_path,character='旧角色',character_controls=[{'name':'山林守卫','groups':[1]}])
    monkeypatch.setattr('supervisor.result_retry.check_live_ready',AsyncMock())
    new=await retry_from_group(service,old,assets[0]['group_id'],'group_scoped_role_001')
    settings=service.settings(new)
    assert settings.character=='山林守卫' and settings.character_controls==[]
    assert result_recipe(service,old,assets[0]['id'],1,False)[0].character_controls==[]
