import json
from unittest.mock import AsyncMock

import gradio as gr
import pytest

from supervisor.comfy import Comfy
from supervisor.models import PromptPlan, TaskSettings, WorkflowConfig
from supervisor.result_batch import batch_from_result, result_recipe
from supervisor.studio_features import result_review_html
from supervisor.ui import build_ui
from test_workflow_editor import configure_editor


async def source_task(service,tmp_path,**extra):
    configure_editor(service)
    service.comfy.run=AsyncMock(return_value=[{'node_id':'9','output_index':0,'identity':'mock','demo':True}])
    task=service.create_task(TaskSettings(goal='mountains',direct_prompt='mountains, (light:1.2)',direct_negative='blur',
        review_enabled=False,autonomous=True,demo=False,groups=1,per_group=2,max_rounds=4,
        params={'width':512,'height':512,'seed':77,'cfg':4},export_folder=str(tmp_path/'export'),**extra))
    await service.run(task)
    assets=service.db.rows('SELECT * FROM assets WHERE task_id=? ORDER BY created_at',(task,))
    service.enqueue=lambda t:None
    return task,assets


async def test_batch_uses_selected_recipe_and_actual_graph_not_current_workflow(service,tmp_path,monkeypatch):
    old,assets=await source_task(service,tmp_path)
    first,last=assets
    first_gen=service.db.one('SELECT * FROM generations WHERE id=?',(first['generation_id'],))
    # A later prompt/graph and the global workflow are different from the selected image.
    last_gen=service.db.one('SELECT * FROM generations WHERE id=?',(last['generation_id'],))
    body=json.loads(service.db.one('SELECT body FROM prompt_variants WHERE id=?',(last_gen['variant_id'],))['body'])
    body['positive']='different latest prompt'
    service.db.execute('UPDATE prompt_variants SET body=? WHERE id=?',(json.dumps(body),last_gen['variant_id']))
    changed=json.loads((service.project_root/service.workflow.workflow_api_json).read_bytes())
    changed['4']['inputs']['ckpt_name']='different.safetensors'
    (service.project_root/service.workflow.workflow_api_json).write_text(json.dumps(changed),encoding='utf-8')
    ready=AsyncMock();monkeypatch.setattr('supervisor.result_batch.check_live_ready',ready)
    service.cloud.request=AsyncMock(side_effect=AssertionError('No cloud requests'))
    before={p:p.read_bytes() for p in (tmp_path/'export'/f'task_{old}').rglob('*.png')}
    new=await batch_from_result(service,old,first['id'],3,False,'request_selected_01')
    settings=service.settings(new)
    assert settings.direct_prompt=='mountains, (light:1.2)' and settings.direct_negative=='blur'
    assert settings.params.seed==78 and settings.params.cfg==4
    assert settings.per_group==settings.max_rounds==3 and not settings.review_enabled
    assert not settings.control_words and not settings.lora_trigger_words and settings.input_folder is None
    snapshot=WorkflowConfig.model_validate_json(service.db.one('SELECT workflow_json FROM task_configs WHERE task_id=?',(new,))['workflow_json'])
    graph=json.loads((service.project_root/snapshot.workflow_api_json).read_bytes())
    assert graph==json.loads(first_gen['graph']) and graph['4']['inputs']['ckpt_name']!='different.safetensors'
    assert not snapshot.switch_nodes and not snapshot.bypass_nodes
    assert ready.call_args.kwargs['workflow_config']==snapshot and ready.call_args.kwargs['purposes']==()
    service.comfy=Comfy(snapshot,service.project_root,service.db)
    service.comfy.run=AsyncMock(return_value=[{'node_id':'9','output_index':0,'identity':'mock','demo':True}])
    await service.run(new)
    plans=[PromptPlan.model_validate_json(r['body']) for r in service.db.rows('SELECT body FROM prompt_variants WHERE task_id=? ORDER BY round_index',(new,))]
    assert [p.params.seed for p in plans]==[78,79,80]
    assert all(p.positive==settings.direct_prompt and p.negative=='blur' for p in plans)
    assert len(service.db.accepted(new))==3 and len(list((tmp_path/'export').glob(f'{new[:8]}_group_*.png')))==3
    assert before and all(p.read_bytes()==content for p,content in before.items())
    assert service.db.token_usage(new)==0
    service.cloud.request.assert_not_called()


async def test_reviewed_batch_calls_only_review_and_inherits_limits(service,tmp_path,monkeypatch):
    from supervisor.models import Evaluation
    from supervisor.providers import Cloud
    old,assets=await source_task(service,tmp_path,quality_threshold=73,budget_micro=234000,token_budget=12345)
    monkeypatch.setattr('supervisor.result_batch.check_live_ready',AsyncMock())
    calls=[]
    async def review(task,settings,purpose,contract,payload,images=None):
        assert purpose=='review' and contract is Evaluation
        calls.append(purpose)
        return Cloud.demo(Evaluation,purpose,{**payload,'round_index':2}),None
    service.cloud.request=AsyncMock(side_effect=review)
    new=await batch_from_result(service,old,assets[1]['id'],2,True,'request_review_001')
    settings=service.settings(new)
    assert settings.max_rounds==4 and settings.budget_micro==234000 and settings.token_budget==12345
    assert settings.quality_threshold==73 and settings.review_enabled
    snapshot=WorkflowConfig.model_validate_json(service.db.one('SELECT workflow_json FROM task_configs WHERE task_id=?',(new,))['workflow_json'])
    service.comfy=Comfy(snapshot,service.project_root,service.db)
    service.comfy.run=AsyncMock(return_value=[{'node_id':'9','output_index':0,'identity':'mock','demo':True}])
    await service.run(new)
    assert calls==['review','review'] and len(service.db.accepted(new))==2


async def test_batch_deduplicates_request_and_keeps_queue_order(service,tmp_path,monkeypatch):
    old,assets=await source_task(service,tmp_path)
    monkeypatch.setattr('supervisor.result_batch.check_live_ready',AsyncMock())
    queued=service.create_task(service.settings(old))
    new=await batch_from_result(service,old,assets[0]['id'],2,False,'request_duplicate1')
    assert await batch_from_result(service,old,assets[0]['id'],2,False,'request_duplicate1')==new
    assert service.db.one('SELECT COUNT(*) n FROM result_batch_requests')['n']==1
    assert [r['id'] for r in service.db.rows("SELECT id FROM tasks WHERE state='RUNNING' ORDER BY created_at,rowid")]==[queued,new]
    with pytest.raises(ValueError,match='已使用'):
        await batch_from_result(service,old,assets[0]['id'],3,False,'request_duplicate1')
    service.end_round()
    paused=await batch_from_result(service,old,assets[1]['id'],1,False,'request_roundended')
    assert service.db.one('SELECT state,reason FROM tasks WHERE id=?',(paused,))=={'state':'RUNNING','reason':None}


@pytest.mark.parametrize('count,review',[(0,False),(51,False),(1.5,False),(True,False),(2,'false')])
async def test_batch_rejects_invalid_options_before_preflight(service,tmp_path,count,review,monkeypatch):
    old,assets=await source_task(service,tmp_path)
    ready=AsyncMock();monkeypatch.setattr('supervisor.result_batch.check_live_ready',ready)
    with pytest.raises(ValueError):
        await batch_from_result(service,old,assets[0]['id'],count,review,'request_invalid01')
    ready.assert_not_called()
    assert not service.db.rows('SELECT * FROM result_batch_requests')


async def test_missing_graph_file_ownership_and_legacy_recovery(service,tmp_path,monkeypatch):
    old,assets=await source_task(service,tmp_path)
    ready=AsyncMock();monkeypatch.setattr('supervisor.result_batch.check_live_ready',ready)
    with pytest.raises(ValueError):
        await batch_from_result(service,'wrong-task',assets[0]['id'],1,False,'request_wrongtask')
    path=service.files.path(assets[0]['path']);content=path.read_bytes();path.unlink()
    with pytest.raises(ValueError,match='原文件'):
        await batch_from_result(service,old,assets[0]['id'],1,False,'request_missing01')
    path.write_bytes(content)
    service.db.execute('DELETE FROM task_configs WHERE task_id=?',(old,))
    settings,workflow=result_recipe(service,old,assets[0]['id'],1,False)
    assert workflow and settings.direct_prompt=='mountains, (light:1.2)'
    service.db.execute("UPDATE generations SET graph='{}' WHERE id=?",(assets[0]['generation_id'],))
    with pytest.raises(ValueError,match='工作流记录'):
        await batch_from_result(service,old,assets[0]['id'],1,False,'request_badgraph1')
    ready.assert_not_called()


async def test_ui_batch_callback_and_card_keep_original_selection(service,tmp_path,monkeypatch):
    old,assets=await source_task(service,tmp_path)
    monkeypatch.setattr('supervisor.result_batch.check_live_ready',AsyncMock())
    monkeypatch.setattr(gr,'Info',lambda *a,**kw:None)
    monkeypatch.setattr(gr,'Warning',lambda *a,**kw:None)
    app=build_ui(service)
    callback=next(f.fn for f in app.fns.values() if getattr(f.fn,'__name__','')=='create_result_batch')
    selection,text,response=await callback(json.dumps({'task_id':old,'asset_id':assets[0]['id'],'count':2,'review':False,'request_id':'request_ui_batch1'}),'zh')
    result=json.loads(response)
    assert result['ok'] and '顺序队列' in text and 'value' not in selection
    assert result['task_id']!=old
    viewer=next(f.fn for f in app.fns.values() if getattr(f.fn,'__name__','')=='view_result_batch')
    assert viewer(json.dumps({'request_id':'request_ui_batch1','task_id':old,'asset_id':assets[0]['id']}),'zh')['value']==result['task_id']
    assert 'value' not in viewer(json.dumps({'request_id':'request_ui_batch1','task_id':'wrong','asset_id':assets[0]['id']}),'zh')
    html=result_review_html(service,old)
    assert 'result-batch-count' in html and 'result-batch-review' in html and '批量产出' in html
    assert all(a['id'] in html for a in assets)
    _,_,response=await callback('[]','zh')
    assert json.loads(response)['ok'] is False
