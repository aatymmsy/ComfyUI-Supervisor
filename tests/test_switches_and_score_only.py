import json
from unittest.mock import patch

import httpx
import pytest

from supervisor.comfy import Comfy, GenerationError
from supervisor.models import Evaluation, PromptPlan, TaskSettings, WorkflowConfig
from supervisor.node_editor_view import node_table
from supervisor.setup import ConnectionFailure
from supervisor.wire import ReviewReply, SHAPES, expand_reply
from supervisor.workflow_editor import read_workflow_editor, set_workflow_switch, set_workflow_bypass, save_workflow_parameter
from test_bypass_progress_scores import configure_bypass
from test_basic_pass_and_results import normal_character


def configure_switch(service, linked=False):
    graph = configure_bypass(service)
    graph['12'] = {'class_type':'EmptyLatentImage','_meta':{'title':'竖图'},'inputs':{'width':512,'height':768,'batch_size':1}}
    graph['125'] = {'class_type':'EmptyLatentImage','_meta':{'title':'横图'},'inputs':{'width':768,'height':512,'batch_size':1}}
    graph['126'] = {'class_type':'ComfySwitchNode','_meta':{'title':'横向'},'inputs':{'switch':['129',0] if linked else True,'on_true':['125',0],'on_false':['12',0]}}
    graph['3']['inputs']['latent_image'] = ['126',0]
    if linked:
        graph['129'] = {'class_type':'BooleanValue','inputs':{'value':True}}
    (service.project_root/service.workflow.workflow_api_json).write_text(json.dumps(graph))
    definitions = {
        'ComfySwitchNode':{'input':{'required':{'switch':['BOOLEAN',{}]},'optional':{'on_true':['LATENT',{'lazy':True}],'on_false':['LATENT',{'lazy':True}]}},'output':['LATENT']},
        'LoraLoader':{'input':{'required':{'model':['MODEL'],'clip':['CLIP'],'lora_name':[['test.safetensors','test2.safetensors']], 'strength_model':['FLOAT'],'strength_clip':['FLOAT']}},'output':['MODEL','CLIP']},
        'KSampler':{'input':{'required':{'steps':['INT'],'model':['MODEL'],'latent_image':['LATENT']}},'output':['LATENT']},
        'EmptyLatentImage':{'input':{'required':{'width':['INT'],'height':['INT'],'batch_size':['INT']}},'output':['LATENT']},
    }
    service.comfy.transport = httpx.MockTransport(lambda r:httpx.Response(200,json=definitions))
    return graph


async def test_switch_is_recorded_without_requests_and_only_changes_submitted_graph(service):
    source = configure_switch(service)
    _,_,rows = await read_workflow_editor(service)
    row = next(r for r in rows if r['node_id']=='126')
    assert row['switch']['true'].startswith('节点 125 · 横图')
    assert row['switch']['false'].startswith('节点 12 · 竖图')
    html = node_table(rows)
    assert 'data-switch-node="126"' in html and 'True →' in html and 'False →' in html
    original_actor = service.comfy
    original_config = service.workflow
    service.active.add('existing')
    service.comfy.transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError('No network on selection')))
    with patch('supervisor.workflow_editor.read_workflow_editor',side_effect=AssertionError('No refresh on selection')), patch('supervisor.comfy.Comfy.graph',side_effect=AssertionError('No compilation on selection')):
        await set_workflow_switch(service,'126',False)
    assert service.comfy is original_actor and service.comfy.config is original_config
    assert service.workflow.switch_nodes['126'].value is False
    assert json.loads((service.project_root/service.workflow.workflow_api_json).read_text()) == source
    plan = PromptPlan(group_id='g',positive='landscape',reason='test')
    assert original_actor.graph(plan,'old')['126']['inputs']['switch'] is True
    revised = Comfy(service.workflow,service.project_root,service.db)
    assert revised.graph(plan,'new')['126']['inputs']['switch'] is False
    assert revised.graph(plan,'new')['126']['inputs']['on_true']==['125',0]
    assert revised.graph(plan,'new')['126']['inputs']['on_false']==['12',0]
    service.active.clear()
    restored=WorkflowConfig.model_validate_json(service.workflow.model_dump_json())
    assert restored.switch_nodes['126'].value is False


async def test_ui_switch_feedback_names_branch_and_bypass_feedback_remains_distinct(service,monkeypatch):
    import gradio as gr
    from conftest import ui_callbacks
    from supervisor.ui import build_ui
    configure_switch(service)
    await read_workflow_editor(service)
    notices=[]
    monkeypatch.setattr(gr,'Info',lambda text,**kwargs:notices.append(text))
    callback=ui_callbacks(build_ui(service))['toggle_editor_bypass']
    switched=await callback(json.dumps({'kind':'switch','node_id':'126','value':False}))
    assert '分支切换为 False' in switched[3] and '绕过' not in notices[-1]
    bypassed=await callback(json.dumps({'kind':'bypass','node_id':'10','enabled':True}))
    assert '已记录绕过' in bypassed[3] and '绕过' in notices[-1]
    assert service.workflow.switch_nodes['126'].value is False


async def test_switch_and_bypass_choices_survive_each_other_and_previous_config(service):
    configure_switch(service)
    await read_workflow_editor(service)
    await set_workflow_switch(service,'126',False)
    await set_workflow_bypass(service,'10',True)
    assert service.workflow.switch_nodes['126'].value is False
    compiled=service.comfy.graph(PromptPlan(group_id='g',positive='landscape',reason='test'),'token')
    assert '10' not in compiled and compiled['126']['inputs']['switch'] is False
    settings=TaskSettings(goal='landscape',groups=2,target_styles=['ink','film'])
    service.remember_last_run(settings)
    saved=json.loads((service.project_root/'config/last-run.local.json').read_text(encoding='utf-8'))
    assert saved['workflow']['switch_nodes']['126']['value'] is False
    await set_workflow_switch(service,'126',True)
    assert service.workflow.bypass_nodes['10']
    assert service.comfy.graph(PromptPlan(group_id='g',positive='landscape',reason='test'),'token')['126']['inputs']['switch'] is True


async def test_switch_saved_through_parameter_editor_is_staged_too(service):
    source=configure_switch(service)
    await read_workflow_editor(service)
    rows=await save_workflow_parameter(service,'126:switch',False)
    assert next(r for r in rows if r['key']=='126:switch')['value'] is False
    assert json.loads((service.project_root/service.workflow.workflow_api_json).read_text())==source


async def test_linked_switch_is_disabled_without_replacing_connection(service):
    source=configure_switch(service,linked=True)
    _,_,rows=await read_workflow_editor(service)
    # Linked selectors are not scalar parameters; add a disabled informational
    # row so users can see both branch sources even when they cannot edit it.
    row=next(r for r in rows if r['node_id']=='126')
    assert row['switch']['reason']
    assert 'disabled' in node_table([row])
    with pytest.raises(ConnectionFailure):
        await set_workflow_switch(service,'126',False)
    assert json.loads((service.project_root/service.workflow.workflow_api_json).read_text())==source


@pytest.mark.parametrize('value',[None,1,'false'])
async def test_invalid_switch_choices_leave_config_unchanged(service,value):
    configure_switch(service)
    await read_workflow_editor(service)
    before=service.workflow.model_dump_json()
    with pytest.raises(ConnectionFailure):
        await set_workflow_switch(service,'126',value)
    assert service.workflow.model_dump_json()==before


@pytest.mark.parametrize('autonomous,auto_candidates,action',[(True,False,'ACCEPTED'),(False,True,'ACCEPTED'),(False,False,'CANDIDATE')])
def test_score_threshold_has_no_confidence_or_model_recommendation_gate(service,autonomous,auto_candidates,action):
    settings=TaskSettings(goal='woman',autonomous=autonomous,auto_candidates=auto_candidates,quality_threshold=60)
    data=normal_character().model_dump()
    data['confidence']=0.01
    data['decision']='review'
    for key in ('overall','prompt_alignment','aesthetics','composition','anatomy','artifacts','style_match','structure','hands'):
        data[key]=65
    data['unassessable_fields']=['nsfw_target','text_quality']
    evaluation=Evaluation.model_validate(data)
    assert 'confidence' not in evaluation.model_dump()
    assert service.evaluate_decision(evaluation,settings,True)[0]==action
    settings.quality_threshold=evaluation.effective_score()+.01
    assert service.evaluate_decision(evaluation,settings,True)[0] not in ('ACCEPTED','CANDIDATE')


def test_cloud_review_schema_omits_confidence_and_accepts_legacy_replies():
    assert 'confidence' not in ReviewReply.model_json_schema()['properties']
    assert 'confidence' not in Evaluation.model_json_schema()['properties']
    assert 'confidence' not in SHAPES[Evaluation]
    assert 'delete_confidence' not in TaskSettings.model_json_schema()['properties']
    data={'scores':{'alignment':80,'aesthetics':80,'composition':80,'anatomy':None,'structure':None,'hands':None,'text':None,'artifacts':80,'style':80,'safety':90,'nsfw':None},'decision':'keep','problems':[],'advice':''}
    reply=ReviewReply.model_validate(data)
    result=expand_reply(Evaluation,reply,{'asset_id':'test'})
    assert 'confidence' not in result.model_dump()
    data['confidence']=.01
    assert ReviewReply.model_validate(data).model_dump()==reply.model_dump()
    assert 'delete_confidence' not in TaskSettings.model_validate({'goal':'test','delete_confidence':.99}).model_dump()


async def test_existing_and_new_queued_tasks_keep_separate_switch_snapshots(service):
    configure_switch(service)
    await read_workflow_editor(service)
    settings=TaskSettings(goal='landscape',direct_prompt='landscape',review_enabled=False,groups=1,per_group=1)
    first=service.create_task(settings,start=False)
    await set_workflow_switch(service,'126',False)
    second=service.create_task(settings,start=False)
    first_config=WorkflowConfig.model_validate_json(service.db.one('SELECT workflow_json FROM task_configs WHERE task_id=?',(first,))['workflow_json'])
    second_config=WorkflowConfig.model_validate_json(service.db.one('SELECT workflow_json FROM task_configs WHERE task_id=?',(second,))['workflow_json'])
    plan=PromptPlan(group_id='g',positive='landscape',reason='test')
    assert Comfy(first_config,service.project_root,service.db).graph(plan,'first')['126']['inputs']['switch'] is True
    assert Comfy(second_config,service.project_root,service.db).graph(plan,'second')['126']['inputs']['switch'] is False


async def test_legacy_completed_task_can_start_a_new_round_after_confidence_removed(service):
    settings=TaskSettings(goal='landscape',direct_prompt='landscape',review_enabled=False,groups=1,per_group=1)
    task=service.create_task(settings)
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    legacy=settings.model_dump();legacy['delete_confidence']=.95
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?',(json.dumps(legacy,ensure_ascii=False),task))
    next_task=service.create_task(settings,start=False,restart_from=task)
    assert next_task!=task and service.settings(next_task).model_dump()==settings.model_dump()


async def test_switch_callback_updates_selected_boolean_without_resetting_other_inputs(service):
    from supervisor.ui import build_ui
    configure_switch(service)
    await read_workflow_editor(service)
    app=build_ui(service)
    callback=next(f.fn for f in app.fns.values() if f.fn and f.fn.__name__=='toggle_editor_bypass')
    service.comfy.transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError('No network')))
    result=await callback(json.dumps({'kind':'switch','node_id':'126','value':False}),'126:switch')
    assert result[1]['value']=='126:switch' and result[6]['value'] is False
    result=await callback(json.dumps({'kind':'bypass','node_id':'10','enabled':True}),'11:strength_model')
    assert result[1]['value']=='11:strength_model' and 'value' not in result[6]


async def test_switch_parameter_without_preloaded_cache_still_preserves_source(service):
    source=configure_switch(service)
    await save_workflow_parameter(service,'126:switch',False)
    assert service.workflow.switch_nodes['126'].value is False
    assert json.loads((service.project_root/service.workflow.workflow_api_json).read_text())==source
