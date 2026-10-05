from conftest import ui_callbacks
import hashlib
import json

import httpx
import pytest

from supervisor.comfy import Comfy
from supervisor.models import PromptPlan, TaskSettings, WorkflowConfig, load_yaml
from supervisor.setup import ConnectionFailure
from supervisor.workflow_editor import read_workflow_editor, save_workflow_parameter


def configure_editor(service):
    config=load_yaml(service.project_root/'config/workflow.example.yaml',WorkflowConfig)
    graph=json.loads((service.project_root/config.workflow_api_json).read_text())
    definitions={}
    for node in graph.values():
        declared={}
        for name,value in node['inputs'].items():
            if isinstance(value,list):declared[name]=['MODEL']
            elif name=='ckpt_name':declared[name]=[[value,'custom-model.safetensors']]
            elif name=='sampler_name':declared[name]=[[value,'dpmpp_2m']]
            elif isinstance(value,int):declared[name]=['INT',{'min':0,'max':4096}]
            elif isinstance(value,float):declared[name]=['FLOAT',{'min':0,'max':100}]
            else:declared[name]=['STRING']
        definitions[node['class_type']]={'input':{'required':declared}}
    service.workflow=config
    service.comfy=Comfy(config,service.project_root,service.db,httpx.MockTransport(lambda r:httpx.Response(200,json=definitions)))
    return graph


async def test_custom_model_persists_and_retains_all_connections_and_original_workflow(service):
    original=configure_editor(service)
    _,_,rows=await read_workflow_editor(service)
    assert any(r['key']=='4:ckpt_name' and 'custom-model.safetensors' in r['choices'] for r in rows)
    assert not any(r['input'] in ('text','seed','filename_prefix','batch_size') for r in rows)
    await save_workflow_parameter(service,'4:ckpt_name','custom-model.safetensors')
    saved=json.loads((service.project_root/service.workflow.workflow_api_json).read_text())
    expected=json.loads(json.dumps(original));expected['4']['inputs']['ckpt_name']='custom-model.safetensors'
    assert saved==expected
    assert json.loads((service.project_root/'workflows/example-api.json').read_text())==original
    assert service.workflow.workflow_hash==hashlib.sha256((service.project_root/service.workflow.workflow_api_json).read_bytes()).hexdigest()
    service.reload_config()
    result=service.comfy.graph(PromptPlan(group_id='g',positive='user prompt',reason='test'),'token')
    assert result['4']['inputs']['ckpt_name']=='custom-model.safetensors'
    assert result['6']['inputs']['text']=='user prompt'


async def test_changed_sampler_updates_the_binding_allowlist(service):
    configure_editor(service)
    await save_workflow_parameter(service,'3:sampler_name','dpmpp_2m')
    plan=PromptPlan(group_id='g',positive='mountains',reason='test',params={'sampler':'dpmpp_2m'})
    assert service.comfy.graph(plan,'token')['3']['inputs']['sampler_name']=='dpmpp_2m'


@pytest.mark.parametrize('key,value', [('4:ckpt_name','missing-model.safetensors'),('3:steps',61),('3:steps',2.5),('5:width',600),('6:text','hidden rewrite'),('3:model','replace link')])
async def test_invalid_or_linked_parameters_leave_workflow_unchanged(service,key,value):
    configure_editor(service)
    before=service.workflow.model_dump()
    with pytest.raises(ConnectionFailure):
        await save_workflow_parameter(service,key,value)
    assert service.workflow.model_dump()==before
    assert not service.workflow_path.exists()
    assert not list((service.project_root/'workflows').glob('customized-*.json'))


async def test_workflow_cannot_be_edited_while_tasks_are_running(service):
    configure_editor(service)
    service.create_task(TaskSettings(goal='mountains'))
    with pytest.raises(ConnectionFailure,match='停止'):
        await save_workflow_parameter(service,'4:ckpt_name','custom-model.safetensors')
    assert not service.workflow_path.exists()


async def test_numeric_node_edit_and_page_reload_keep_customized_defaults(service,monkeypatch):
    import gradio as gr
    from supervisor.ui import build_ui
    configure_editor(service)
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    monkeypatch.setattr(gr,'Warning',lambda *a,**k:None)
    await save_workflow_parameter(service,'3:steps',25)
    app=build_ui(service)
    callbacks=ui_callbacks(app)
    workflow,_table,_source,_status=await callbacks['load_saved_workflow']('http://ignored.invalid','zh')
    assert json.loads(gr.Code(language='json').postprocess(workflow))['workflow_api_json'].startswith('workflows/customized-')
    _,_,rows,_=await callbacks['read_editor']()
    result=await callbacks['save_editor']('3:cfg',None,3.5,False,'',rows)
    assert result[0].startswith('已保存')
    assert json.loads(gr.Code(language='json').postprocess(result[5]))['workflow_api_json'].startswith('workflows/customized-')
    saved=json.loads((service.project_root/service.workflow.workflow_api_json).read_text())
    assert saved['3']['inputs']['cfg']==3.5 and saved['3']['inputs']['steps']==25


async def test_replacing_workflow_removes_stale_model_rows_and_refreshes_all_imports(service,tmp_path,monkeypatch):
    import gradio as gr
    from supervisor.ui import build_ui
    from supervisor.workflow_editor import cached_editor_rows
    graph=configure_editor(service)
    await save_workflow_parameter(service,'4:ckpt_name','custom-model.safetensors')
    await read_workflow_editor(service)
    assert any(row['value']=='custom-model.safetensors' for row in cached_editor_rows(service))
    app=build_ui(service)
    callbacks=ui_callbacks(app)
    upload=tmp_path/'replacement.json';upload.write_text(json.dumps(graph),encoding='utf-8')
    monkeypatch.setattr(gr,'Info',lambda *a,**kw:None)
    monkeypatch.setattr(gr,'Warning',lambda *a,**kw:None)
    callbacks['import_workflow'](str(upload),'http://127.0.0.1:8188','zh')
    with pytest.raises(ConnectionFailure,match='变化'):
        cached_editor_rows(service)
    _,_,rows,status=await callbacks['read_editor']()
    assert not any(row['value']=='custom-model.safetensors' for row in rows)
    assert 'desktop-api.json' in status and '0 个 LoRA' in status
    # Every successful workflow replacement must lead to a fresh node table.
    for name in ('import_workflow','identify_workflow','import_workflow_image','import_prompt_workflow_image'):
        event=next(event for event in app.fns.values() if event.fn and event.fn.__name__==name)
        assert any(dep.get('trigger_after')==event._id and app.fns[dep['id']].fn is callbacks['read_editor']
                   for dep in app.get_config_file()['dependencies'])


def test_accordion_toggles_never_write_stale_open_state_back_to_themselves(service):
    import gradio as gr
    from supervisor.ui import build_ui
    app=build_ui(service);config=app.get_config_file()
    accordions={key for key,block in app.blocks.items() if isinstance(block,gr.Accordion)}
    for dep in config['dependencies']:
        for key,event in dep.get('targets',[]):
            if key in accordions and event in ('expand','collapse'):
                assert key not in dep['outputs']
