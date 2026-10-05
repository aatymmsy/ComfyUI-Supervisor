import json
from unittest.mock import AsyncMock

import gradio as gr
import httpx
import pytest
from PIL import Image

from supervisor.models import Evaluation, TaskSettings, PromptPlan
from supervisor.setup import ConnectionFailure
from supervisor.workflow_bypass import apply_bypasses
from supervisor.workflow_editor import read_workflow_editor, set_workflow_bypass, save_workflow_parameter
from supervisor.generation_progress import generation_progress_html
from supervisor.node_editor_view import node_table
from test_workflow_editor import configure_editor
from test_basic_pass_and_results import normal_character


def configure_bypass(service):
    graph = configure_editor(service)
    graph['10'] = {'class_type':'LoraLoader','inputs':{'model':['4',0],'clip':['4',1],'lora_name':'test.safetensors','strength_model':1.,'strength_clip':1.}}
    graph['11'] = {'class_type':'LoraLoader','inputs':{'model':['10',0],'clip':['10',1],'lora_name':'test2.safetensors','strength_model':.7,'strength_clip':.7}}
    graph['3']['inputs']['model'] = ['11',0]
    graph['6']['inputs']['clip'] = ['11',1]
    graph['7']['inputs']['clip'] = ['11',1]
    (service.project_root/service.workflow.workflow_api_json).write_text(json.dumps(graph))
    definitions = {
      'LoraLoader':{'input':{'required':{'model':['MODEL'],'clip':['CLIP'],'lora_name':[['test.safetensors','test2.safetensors']], 'strength_model':['FLOAT'],'strength_clip':['FLOAT']}},'output':['MODEL','CLIP']},
      'KSampler':{'input':{'required':{'steps':['INT'],'model':['MODEL'],'latent_image':['LATENT']}},'output':['LATENT']},
      'CheckpointLoaderSimple':{'input':{'required':{'ckpt_name':[['REPLACE_WITH_INSTALLED_CHECKPOINT.safetensors']]}},'output':['MODEL','CLIP','VAE']}
    }
    service.comfy.transport = httpx.MockTransport(lambda r:httpx.Response(200,json=definitions))
    return graph


async def test_bypass_rewires_both_model_and_clip_chains_and_restores_source(service):
    original = configure_bypass(service)
    _,_,rows = await read_workflow_editor(service)
    assert next(r for r in rows if r['node_id']=='10')['bypass_routes']=={'0':['4',0],'1':['4',1]}
    assert '绕过此节点' in node_table(rows)
    await set_workflow_bypass(service,'10',True)
    await set_workflow_bypass(service,'11',True)
    plan = PromptPlan(group_id='g',positive='landscape',reason='test')
    compiled = service.comfy.graph(plan,'test')
    assert '10' not in compiled and '11' not in compiled
    assert compiled['3']['inputs']['model']==['4',0]
    assert compiled['6']['inputs']['clip']==['4',1]
    assert json.loads((service.project_root/service.workflow.workflow_api_json).read_text())==original
    await save_workflow_parameter(service,'10:strength_model',.4)
    assert set(service.workflow.bypass_nodes)=={'10','11'}
    await read_workflow_editor(service)
    await set_workflow_bypass(service,'10',False)
    await set_workflow_bypass(service,'11',False)
    restored = service.comfy.graph(plan,'test')
    assert restored['3']['inputs']['model']==['11',0]
    assert restored['10']['inputs']['strength_model']==.4


async def test_unsupported_bypass_and_active_worker_snapshot_is_preserved(service):
    from supervisor.comfy import Comfy
    from supervisor.models import WorkflowConfig
    configure_bypass(service)
    await read_workflow_editor(service)
    before=service.workflow.model_dump()
    with pytest.raises(ConnectionFailure,match='同类型'):
        await set_workflow_bypass(service,'4',True)
    assert service.workflow.model_dump()==before
    current=service.create_task(TaskSettings(goal='landscape',direct_prompt='landscape'))
    executing=service.comfy
    service.active.add(current);service.executing_task=current
    try:
        await set_workflow_bypass(service,'10',True)
        assert service.comfy is executing and executing.config.bypass_nodes=={}
        current_config=json.loads(service.db.one('SELECT workflow_json FROM task_configs WHERE task_id=?',(current,))['workflow_json'])
        assert current_config['bypass_nodes']=={}
        new=service.create_task(TaskSettings(goal='landscape',direct_prompt='landscape'),start=False)
        new_config=WorkflowConfig.model_validate_json(service.db.one('SELECT workflow_json FROM task_configs WHERE task_id=?',(new,))['workflow_json'])
        assert '10' in new_config.bypass_nodes
        graph=Comfy(new_config,service.project_root,service.db).graph(PromptPlan(group_id='g',positive='landscape',reason='test'),'token')
        assert '10' not in graph
    finally:
        service.active.discard(current);service.executing_task=None


def test_invalid_bypass_cycle_and_missing_route_rejected():
    graph={'a':{'inputs':{'model':['b',0]}},'b':{'inputs':{'model':['a',0]}},'out':{'inputs':{'model':['a',0]}}}
    with pytest.raises(ValueError,match='循环'):
        apply_bypasses(graph,{'a':{'0':['b',0]},'b':{'0':['a',0]}})
    with pytest.raises(ValueError,match='兼容输入'):
        apply_bypasses(graph,{'a':{}})


@pytest.mark.parametrize('threshold,score,expected',[(60,60,'ACCEPTED'),(60,59.9,'QUARANTINE'),(70,72,'ACCEPTED'),(80,79.9,'QUARANTINE')])
def test_automatic_numeric_threshold_ignores_hidden_confidence_and_recommendation(service,threshold,score,expected):
    evaluation=normal_character(prompt_alignment=score,aesthetics=score,composition=score,anatomy=None,structure=None,artifacts=score,style_match=score,safety_score=None,issues=[],confidence=.6,decision='review',unassessable_fields=['anatomy','structure','hands','safety_score','nsfw_target'])
    assert service.evaluate_decision(evaluation,TaskSettings(goal='landscape',autonomous=True,quality_threshold=threshold))[0]==expected
    assert service.evaluate_decision(normal_character(confidence=.6,decision='review'),TaskSettings(goal='woman',autonomous=True,quality_threshold=60),True)[0]=='ACCEPTED'


def test_missing_scores_safety_and_deformation_remain_explained(service):
    settings=TaskSettings(goal='woman',autonomous=True,quality_threshold=60,content_label='sfw')
    evaluation=normal_character(aesthetics=None,unassessable_fields=['aesthetics','hands','nsfw_target'])
    assert service.evaluate_decision(evaluation,settings,True)==('RECHECK','SCORE_FIELDS_MISSING')
    assert service.evaluate_decision(normal_character(safety_score=50),settings,True)==('QUARANTINE','SAFETY_CHECK_FAILED')
    assert service.evaluate_decision(normal_character(anatomy=41),settings,True)==('QUARANTINE','BASIC_STRUCTURE_FAILED')


async def test_old_scored_review_reused_once_and_exported_without_cloud(service,tmp_path):
    task=service.create_task(TaskSettings(goal='landscape',direct_prompt='landscape',autonomous=True,review_enabled=False,quality_threshold=60,groups=1,per_group=1,export_folder=str(tmp_path/'exports')))
    await service.run(task)
    settings=service.settings(task).model_copy(update={'review_enabled':True,'per_group':2})
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?',(settings.model_dump_json(),task))
    asset=service.db.one("SELECT * FROM assets WHERE task_id=? AND source_kind='generated'",(task,))
    evaluation=normal_character(asset_id=asset['id'],anatomy=None,structure=None,confidence=.6,issues=[],unassessable_fields=['anatomy','structure','hands','nsfw_target'])
    row=service.save_evaluation(asset,evaluation,None,'final')
    service.decision(asset['id'],row['id'],'REVIEW','BASIC_STRUCTURE_UNCERTAIN')
    service.db.transition(task,'PARTIAL','DELIVER','ROUND_OR_PATIENCE_LIMIT')
    service.deliver(task,'PARTIAL','ROUND_OR_PATIENCE_LIMIT',rebuild=True)
    service.cloud.request=AsyncMock(side_effect=AssertionError('No new cloud request'))
    assert service.reconcile_scored_results(task)==1
    assert service.reconcile_scored_results(task)==0
    assert len(list((tmp_path/'exports').rglob('*.png')))==1
    manifest=json.loads(service.files.path(f'tasks/{task}/delivery/manifest.json').read_text())
    assert manifest['groups'][0]['qualified_count']==1
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='PARTIAL'
    service.cloud.request.assert_not_called()


async def test_progress_uses_latest_generation_and_current_node(service,monkeypatch):
    task=service.create_task(TaskSettings(goal='landscape',direct_prompt='landscape',autonomous=True,review_enabled=False,groups=1,per_group=1))
    await service.run(task)
    generation=service.db.one('SELECT * FROM generations WHERE task_id=?',(task,))
    service.db.transition(task,'RUNNING','MONITOR')
    service.db.execute("UPDATE generations SET state='MONITOR' WHERE id=?",(generation['id'],))
    service.db.event(task,'COMFY_progress',{'generation_id':'old','value':20,'max':20})
    assert 'meter-running' in generation_progress_html(service,task)
    service.db.event(task,'COMFY_progress',{'generation_id':generation['id'],'node':'3','value':7,'max':20})
    html=generation_progress_html(service,task)
    assert '35%' in html and '7/20' in html and '节点 3' in html
    service.db.event(task,'COMFY_executing',{'generation_id':generation['id'],'node':'8'})
    html=generation_progress_html(service,task)
    assert 'meter-running' in html and '35%' not in html
    service.db.event(task,'COMFY_progress',{'generation_id':generation['id'],'node':'8','value':3,'max':0})
    assert 'meter-running' in generation_progress_html(service,task)
    service.db.transition(task,'PARTIAL','DELIVER')
    assert generation_progress_html(service,task)==''


async def test_old_failed_scores_no_longer_claim_manual_review(service,tmp_path):
    task=service.create_task(TaskSettings(goal='landscape',direct_prompt='landscape',autonomous=True,review_enabled=False,groups=1,per_group=1))
    await service.run(task)
    settings=service.settings(task).model_copy(update={'review_enabled':True,'quality_threshold':60,'per_group':2})
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?',(settings.model_dump_json(),task))
    asset=service.db.one("SELECT * FROM assets WHERE task_id=? AND source_kind='generated'",(task,))
    evaluation=normal_character(asset_id=asset['id'],safety_score=30,confidence=.6)
    row=service.save_evaluation(asset,evaluation,None,'final')
    service.decision(asset['id'],row['id'],'REVIEW','BASIC_STRUCTURE_UNCERTAIN')
    service.db.transition(task,'PARTIAL','DELIVER')
    assert service.reconcile_scored_results(task)==0
    decision=service.db.one('SELECT action,reason FROM decisions WHERE asset_id=? ORDER BY rowid DESC LIMIT 1',(asset['id'],))
    assert decision=={'action':'REJECTED','reason':'SAFETY_CHECK_FAILED'}
    assert service.files.path(asset['path']).is_file()


async def test_rebuild_removes_only_verified_duplicate_delivery_copies(service,tmp_path):
    export=tmp_path/'exports'
    task=service.create_task(TaskSettings(goal='landscape',direct_prompt='landscape',autonomous=True,review_enabled=False,groups=1,per_group=1,export_folder=str(export)))
    await service.run(task)
    asset=service.db.one("SELECT * FROM assets WHERE task_id=? AND source_kind='generated'",(task,))
    duplicate_name=f"000_{asset['id']}.png"
    roots=[service.files.path(f'tasks/{task}/delivery/group_01'),export/f'task_{task}/group_01']
    source=service.files.path(asset['path'])
    for root in roots:
        (root/duplicate_name).write_bytes(source.read_bytes())
        (root/f"999_{asset['id']}.png").write_bytes(b'user edited copy')
    outside=tmp_path/duplicate_name;outside.write_bytes(source.read_bytes())
    service.deliver(task,'COMPLETED','TARGET_REACHED',rebuild=True)
    for root in roots:
        assert not (root/duplicate_name).exists()
        assert (root/f"999_{asset['id']}.png").read_bytes()==b'user edited copy'
        assert (root/f"001_{asset['id']}.png").is_file()
    assert outside.is_file() and source.is_file()


async def test_toggle_callback_uses_cached_definitions_without_read_or_compiling(service,monkeypatch):
    from unittest.mock import Mock
    from supervisor.ui import build_ui
    from supervisor.comfy import apply_bypasses
    configure_bypass(service)
    await read_workflow_editor(service)
    app=build_ui(service)
    calls=Mock(side_effect=AssertionError('No object_info requests on toggles'))
    service.comfy.transport=httpx.MockTransport(calls)
    monkeypatch.setattr(service,'reload_config',Mock(side_effect=AssertionError('No config reload on toggles')))
    compiler=Mock(wraps=apply_bypasses)
    monkeypatch.setattr('supervisor.comfy.apply_bypasses',compiler)
    callback=next(f.fn for f in app.fns.values() if f.fn and f.fn.__name__=='toggle_editor_bypass')
    for enabled in (True,False,True):
        result=await callback(json.dumps({'node_id':'10','enabled':enabled}),'11:strength_model')
        assert '已记录' in result[3]
        assert result[1]['value']=='11:strength_model'
        assert next(r for r in result[2] if r['node_id']=='10')['bypassed']==enabled
    calls.assert_not_called();compiler.assert_not_called();service.reload_config.assert_not_called()
    graph=service.comfy.graph(PromptPlan(group_id='g',positive='landscape',reason='test'),'token')
    assert '10' not in graph
    compiler.assert_called_once()


async def test_workflow_change_requires_explicit_read_instead_of_slow_toggle_fetch(service):
    configure_bypass(service)
    await read_workflow_editor(service)
    path=service.project_root/service.workflow.workflow_api_json
    path.write_bytes(path.read_bytes()+b'\n')
    calls=[]
    service.comfy.transport=httpx.MockTransport(lambda r:calls.append(r))
    with pytest.raises(ConnectionFailure,match='请先读取'):
        await set_workflow_bypass(service,'10',True)
    assert not calls and not service.workflow.bypass_nodes
