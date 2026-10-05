from conftest import ui_callbacks
import io
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from supervisor.comfy import Comfy
from supervisor.models import Evaluation, PromptPlan, TaskSettings, WorkflowConfig, load_yaml
from supervisor.providers import Cloud, CloudError
from supervisor.studio import check_live_ready


async def test_direct_prompt_without_review_saves_every_image_and_has_no_cloud_dependency(service,tmp_path):
    service.cloud.request = AsyncMock(side_effect=AssertionError('No cloud request permitted'))
    prompt = 'layered mountains, (soft light:1.2)'
    task = service.create_task(TaskSettings(goal='mountains',direct_prompt=prompt,direct_negative='blur',review_enabled=False,
        autonomous=True,groups=1,per_group=5,max_rounds=5,patience=1,params={'seed':42},budget_micro=0,export_folder=str(tmp_path/'export')))
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    plans = [PromptPlan.model_validate_json(r['body']) for r in service.db.rows('SELECT body FROM prompt_variants WHERE task_id=? ORDER BY round_index',(task,))]
    assert len(plans)==5 and all(p.positive==prompt and p.negative=='blur' for p in plans)
    assert [p.params.seed for p in plans]==[42,43,44,45,46]
    assert len(list((tmp_path/'export').rglob('*.png')))==5
    assert not service.db.rows('SELECT id FROM api_calls') and not service.db.rows('SELECT id FROM evaluations')
    service.cloud.request.assert_not_called()
    manifest = json.loads(service.files.path(f'tasks/{task}/delivery/manifest.json').read_text())
    assert manifest['review_enabled'] is False
    assert manifest['groups'][0]['qualified_count']==0 and manifest['groups'][0]['unreviewed_count']==5
    assert all(item['scores'] is None and item['reviewed'] is False for item in manifest['groups'][0]['items'])


async def test_direct_prompt_review_uses_only_review_and_keeps_user_text(service):
    calls=[]
    async def respond(task,settings,purpose,contract,payload,images=None):
        assert purpose=='review' and contract is Evaluation
        calls.append(payload)
        return Cloud.demo(Evaluation,purpose,{**payload,'round_index':2}),None
    service.cloud.request = AsyncMock(side_effect=respond)
    task = service.create_task(TaskSettings(goal='mountains',direct_prompt='mountains',direct_negative='mist',autonomous=True,
        review_enabled=True,groups=1,per_group=2,max_rounds=3,auto_candidates=True,prescreen=False))
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    assert len(calls)==2 and len(service.db.accepted(task))==2
    assert all(PromptPlan.model_validate_json(r['body']).positive=='mountains' for r in service.db.rows('SELECT body FROM prompt_variants'))


async def test_no_review_export_failure_resumes_without_new_generation_or_fake_scores(service,tmp_path,monkeypatch):
    export = service.export_file
    failed=False
    def fail_once(task,path,source):
        nonlocal failed
        if not failed:
            failed=True
            raise CloudError('OUTPUT_FOLDER_UNWRITABLE')
        export(task,path,source)
    monkeypatch.setattr(service,'export_file',fail_once)
    task=service.create_task(TaskSettings(goal='mountains',direct_prompt='mountains',review_enabled=False,autonomous=True,
        groups=1,per_group=1,max_rounds=1,export_folder=str(tmp_path/'export')))
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='PAUSED'
    service.resume(task)
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    assert service.db.one('SELECT COUNT(*) n FROM generations')['n']==1
    assert service.db.one('SELECT COUNT(*) n FROM decisions')['n']==1
    assert not service.db.rows('SELECT id FROM evaluations')


def test_review_bypass_is_scoped_to_valid_direct_prompts():
    with pytest.raises(ValidationError):
        TaskSettings(goal='test',review_enabled=False)
    with pytest.raises(ValidationError):
        TaskSettings(goal='test',direct_prompt='  ',review_enabled=False)


async def test_round_limited_task_refreshes_delivery_after_limits_are_extended(service):
    task=service.create_task(TaskSettings(goal='mountains',direct_prompt='mountains',review_enabled=False,autonomous=True,
        groups=1,per_group=3,max_rounds=1))
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='PARTIAL'
    service.update_task_limits(task,3,80)
    service.resume(task)
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    manifest=json.loads(service.files.path(f'tasks/{task}/delivery/manifest.json').read_text())
    assert manifest['status']=='COMPLETED' and manifest['groups'][0]['saved_count']==3
    assert service.db.one('SELECT COUNT(*) n FROM delivery_items')['n']==3


async def test_real_comfy_protocol_without_api_or_reference_images(service,tmp_path,monkeypatch):
    config=load_yaml(service.project_root/'config/workflow.example.yaml',WorkflowConfig)
    original=json.loads((service.project_root/config.workflow_api_json).read_text())
    definitions={n['class_type']:{'input':{'required':{}}} for n in original.values()}
    rendered={}
    posted=[]
    def handler(request):
        if request.url.path=='/object_info':return httpx.Response(200,json=definitions)
        if request.url.path=='/prompt':
            graph=json.loads(request.content)['prompt'];posted.append(graph)
            assert graph['6']['inputs']['text']=='mountains, bright sky'
            stream=io.BytesIO();Image.new('RGB',(64,64),(len(posted)*30,20,10)).save(stream,format='PNG')
            key=str(len(posted));rendered[key]=stream.getvalue()
            return httpx.Response(200,json={'prompt_id':key})
        if request.url.path.startswith('/history/'):
            key=request.url.path.rsplit('/',1)[1]
            return httpx.Response(200,json={key:{'status':{'completed':True},'outputs':{'9':{'images':[{'filename':key+'.png','type':'output'}]}}}})
        if request.url.path=='/view':return httpx.Response(200,content=rendered[request.url.params['filename'].split('.')[0]])
        return httpx.Response(404)
    monkeypatch.setattr('supervisor.comfy.websockets.connect',AsyncMock(side_effect=OSError('no socket')))
    service.workflow=config;service.comfy=Comfy(config,service.project_root,service.db,httpx.MockTransport(handler))
    service.cloud.request=AsyncMock(side_effect=AssertionError('Cloud must not be called'))
    params={'width':512,'height':512,'steps':4,'cfg':1,'seed':42}
    await check_live_ready(service,'sfw',params,purposes=())
    task=service.create_task(TaskSettings(goal='mountains',direct_prompt='mountains, bright sky',review_enabled=False,demo=False,
        autonomous=True,groups=1,per_group=2,max_rounds=2,params=params,export_folder=str(tmp_path/'export')))
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    assert len(posted)==2 and len(list((tmp_path/'export').rglob('*.png')))==2
    assert service.db.token_usage(task)==0


async def test_direct_tab_toggle_and_start_bind_to_task_settings(service,monkeypatch):
    import gradio as gr
    from supervisor.ui import build_ui
    ready=AsyncMock()
    monkeypatch.setattr('supervisor.ui.check_live_ready',ready)
    monkeypatch.setattr(gr,'Info',lambda *a,**kw:None)
    monkeypatch.setattr(gr,'Warning',lambda *a,**kw:None)
    service.enqueue=lambda task:None
    app=build_ui(service)
    callback=ui_callbacks(app)['direct_create']
    result=await callback('mountains','blur',4,False,str(service.project_root/'export'),512,512,4,1,42,'sfw',75,10,1,50000,2,'euler','normal','zh')
    task=result[0]['value'];settings=service.settings(task)
    assert settings.direct_prompt=='mountains' and settings.direct_negative=='blur' and settings.review_enabled is False
    assert settings.per_group==4 and settings.max_rounds==4
    assert ready.call_args.kwargs['purposes']==()
    result=await callback('mountains','',1,True,str(service.project_root/'export'),512,512,4,1,42,'sfw',70,10,1,50000,2,'euler','normal','zh')
    assert service.settings(result[0]['value']).review_enabled is True
    assert ready.call_args.kwargs['purposes']==(('review','质量评审',1),)
