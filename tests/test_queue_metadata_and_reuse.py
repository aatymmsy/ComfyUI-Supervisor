from conftest import ui_callbacks
import asyncio
import io
import json
import threading
import shutil
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import gradio as gr
import httpx
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from supervisor.caption import caption_branch, run_caption
from supervisor.comfy import Comfy
from supervisor.models import TaskSettings, PromptPlan, StyleCard
from supervisor.ui import build_ui
from supervisor.workflow_metadata import editor_workflow, png_with_graph
from test_workflow_editor import configure_editor


def direct(**extra):
    return TaskSettings(goal='mountains',direct_prompt='mountains',review_enabled=False,autonomous=True,
                        groups=1,per_group=2,max_rounds=2,**extra)


async def test_completed_resume_creates_fresh_round_and_keeps_previous_outputs(service,tmp_path):
    configure_editor(service)
    old=service.create_task(direct(export_folder=str(tmp_path/'exports'),direct_negative='blur',params={'width':128,'height':128,'seed':77}))
    await service.run(old)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(old,))['state']=='COMPLETED'
    previous_images={path:path.read_bytes() for path in (tmp_path/'exports'/f'task_{old}').rglob('*.png')}
    service.db.execute('INSERT INTO task_runtime VALUES(?,999,NULL)',(old,))
    snapshot=service.db.one('SELECT workflow_json FROM task_configs WHERE task_id=?',(old,))
    path=service.project_root/service.workflow.workflow_api_json
    modified=json.loads(path.read_bytes());modified['4']['inputs']['ckpt_name']='different.safetensors'
    path.write_text(json.dumps(modified),encoding='utf-8')
    new=service.resume(old)
    assert new!=old and service.settings(new)==service.settings(old).model_copy(update={'delivery_layout':'flat'})
    assert service.db.one('SELECT workflow_json FROM task_configs WHERE task_id=?',(new,))==snapshot
    assert service.db.one('SELECT round_index,stale_rounds FROM groups WHERE task_id=?',(new,))=={'round_index':0,'stale_rounds':0}
    assert not service.db.accepted(new) and not service.db.one('SELECT * FROM task_runtime WHERE task_id=?',(new,))
    assert service.db.budget(new)=={'spent':0,'reserved':0,'uncertain':0}
    latest=service.db.one('SELECT body FROM prompt_variants WHERE task_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(old,))
    cloned=service.db.one('SELECT body,status,round_index FROM prompt_variants WHERE task_id=?',(new,))
    a,b=PromptPlan.model_validate_json(latest['body']),PromptPlan.model_validate_json(cloned['body'])
    assert (a.positive,a.negative,a.params)==(b.positive,b.negative,b.params)
    assert cloned['status']=='APPROVED' and cloned['round_index']==0
    await service.run(new)
    assert len(service.db.accepted(old))==len(service.db.accepted(new))==2
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(new,))['state']=='COMPLETED'
    assert previous_images and all(path.read_bytes()==content for path,content in previous_images.items())
    assert len(list((tmp_path/'exports').glob(f'{new[:8]}_group_*.png')))==2


async def test_restart_reuses_each_groups_prompts_and_analysis_after_reference_cleanup(service,tmp_path):
    from supervisor.db import uid,now
    image=tmp_path/'ref.png';Image.new('RGB',(64,64)).save(image)
    settings=TaskSettings(goal='two themes',autonomous=True,demo=True,groups=2,per_group=2,target_styles=['forest','beach'],input_folder=str(tmp_path))
    old=service.create_task(settings,start=False)
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',(uid(),old,StyleCard(subject=['person'],style=['watercolor']).model_dump_json(),now()))
    for group in service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(old,)):
        plan=PromptPlan(group_id=group['id'],positive=f'theme {group["ordinal"]}, open eyes, clear face',negative='blur',params={'width':128,'height':128,'seed':100+group['ordinal']},reason='last approved prompt')
        service.db.execute('INSERT INTO prompt_variants VALUES(?,?,?,?,?,?,?,?)',(uid(),old,group['id'],None,3,plan.model_dump_json(),'USED',now()))
        service.db.execute('UPDATE groups SET round_index=4 WHERE id=?',(group['id'],))
    service.db.transition(old,'COMPLETED','DELIVER')
    image.unlink()
    for asset in service.db.rows('SELECT path FROM assets WHERE task_id=?',(old,)):
        service.files.path(asset['path']).unlink()
    service.cloud.request=AsyncMock(side_effect=AssertionError('Restart must not retag or request new themes/prompts'))
    new=service.resume(old)
    assert not service.db.rows('SELECT id FROM assets WHERE task_id=?',(new,))
    await service.prepare(new,service.settings(new))
    rows=service.db.rows('SELECT g.ordinal,p.body FROM groups g JOIN prompt_variants p ON p.group_id=g.id WHERE g.task_id=? ORDER BY g.ordinal',(new,))
    assert [json.loads(row['body'])['positive'] for row in rows]==['theme 0, open eyes, clear face','theme 1, open eyes, clear face']
    assert service.cloud.request.await_count==0
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(old,))['state']=='COMPLETED'


async def test_resume_paused_task_keeps_existing_progress_and_ui_selects_new_round(service,monkeypatch):
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    old=service.create_task(direct(params={'width':128,'height':128}))
    await service.prepare(old,service.settings(old))
    service.db.execute('UPDATE groups SET round_index=1 WHERE task_id=?',(old,))
    service.db.transition(old,'PAUSED','SUBMIT')
    assert service.resume(old)==old
    assert service.db.one('SELECT round_index FROM groups WHERE task_id=?',(old,))['round_index']==1
    service.db.transition(old,'COMPLETED','DELIVER')
    service.enqueue=lambda task:None
    service._dispatch_next=lambda:None
    app=build_ui(service)
    callbacks=[f for f in app.fns.values() if f.fn and f.fn.__name__=='continue_task']
    assert len(callbacks)==4 and all(len(f.outputs)==2 for f in callbacks)
    selection,message=callbacks[0].fn(old,'zh')
    assert selection['value']!=old and '新一轮' in message
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(selection['value'],))['state']=='RUNNING'


def caption_graph():
    return {'1':{'class_type':'LoadImage','inputs':{'image':'old.png'}},
            '2':{'class_type':'WD14Tagger|pysssss','inputs':{'image':['1',0],'model':'local-model'}},
            '3':{'class_type':'KSampler','inputs':{'positive':['2',0]}}}


def test_fifo_dispatch_preserves_earlier_task_even_when_latest_is_enqueued_first(service):
    first,second,third=[service.create_task(direct()) for _ in range(3)]
    started=threading.Event(); release=threading.Event(); done=threading.Event(); order=[]
    async def run(task):
        order.append(task)
        if task==first:
            started.set()
            await asyncio.to_thread(release.wait,5)
        service.db.transition(task,'COMPLETED','DELIVER')
        if task==third:done.set()
    service.run=run
    service.enqueue(third)
    assert started.wait(3)
    service.enqueue(second)
    assert order==[first] and second[:8] in service.queue_status()
    release.set()
    assert done.wait(5)
    assert order==[first,second,third]


async def test_end_round_finishes_current_image_cancels_waiting_and_allows_new_tasks(service):
    first=service.create_task(direct())
    second=service.create_task(direct())
    download=service.comfy.download
    async def finish_image(*args,**kwargs):
        service.end_round()
        await download(*args,**kwargs)
    service.comfy.download=finish_image
    await service.run(first)
    assert len(service.db.accepted(first))==1
    assert service.db.one('SELECT state,reason FROM tasks WHERE id=?',(first,))=={'state':'CANCELLED','reason':'USER_ROUND_ENDED'}
    assert service.db.one('SELECT state,reason FROM tasks WHERE id=?',(second,))=={'state':'CANCELLED','reason':'USER_ROUND_ENDED'}
    assert service.files.path(f'tasks/{first}/delivery/group_01').is_dir()
    assert not service.db.one('SELECT id FROM generations WHERE task_id=?',(second,))
    service.comfy.download=download
    service._dispatch_next=lambda:None
    service.resume_round()
    await service.run(first)
    await service.run(second)
    assert len(service.db.accepted(first))==1 and not service.db.accepted(second)
    new=service.create_task(direct())
    await service.run(new)
    assert len(service.db.accepted(new))==2


def test_end_round_does_not_hold_new_tasks(service):
    service.end_round()
    new=service.create_task(direct())
    assert service.db.one('SELECT state,reason FROM tasks WHERE id=?',(new,))=={'state':'RUNNING','reason':None}
    assert service.db.one("SELECT value FROM runtime_state WHERE name='round_ended'")['value']=='0'
    assert '等待 1 个' in service.queue_status()


def test_queue_wait_and_pause_do_not_consume_runtime(service):
    task=service.create_task(direct(max_runtime_seconds=60))
    service.db.execute('UPDATE tasks SET created_at=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),task))
    service.db.execute('INSERT INTO task_runtime VALUES(?,5,NULL)',(task,))
    assert service.limit_reason(task,service.settings(task)) is None


def test_each_queued_task_keeps_workflow_snapshot(service):
    original=configure_editor(service)
    first=service.create_task(direct())
    path=service.project_root/service.workflow.workflow_api_json
    changed=json.loads(json.dumps(original));changed['4']['inputs']['ckpt_name']='different.safetensors'
    path.write_text(json.dumps(changed),encoding='utf-8')
    config=json.loads(service.db.one('SELECT workflow_json FROM task_configs WHERE task_id=?',(first,))['workflow_json'])
    assert json.loads((service.project_root/config['workflow_api_json']).read_bytes())==original


def test_png_contains_full_editor_workflow_exact_prompt_and_unchanged_pixels():
    graph=caption_graph()
    definitions={'LoadImage':{'input':{'required':{'image':['STRING']}},'output':['IMAGE','MASK']},
        'WD14Tagger|pysssss':{'input':{'required':{'image':['IMAGE'],'model':[['local-model']]}},'output':['STRING']},
        'KSampler':{'input':{'required':{'positive':['CONDITIONING'],'seed':['INT',{'default':0,'control_after_generate':True}],
                                        'steps':['INT',{'default':20}]}},'output':['LATENT']}}
    graph['3']['inputs'].update(seed=123,steps=7)
    workflow=editor_workflow(graph,definitions)
    assert workflow['nodes'][2]['widgets_values']==[123,'fixed',7]
    assert len(workflow['links'])==2
    info=PngInfo();info.add_text('workflow','{"nodes":[]}');info.add_text('custom','kept')
    original=Image.new('RGB',(64,64),'blue');stream=io.BytesIO();original.save(stream,format='PNG',pnginfo=info)
    result=png_with_graph(stream.getvalue(),graph,workflow)
    with Image.open(io.BytesIO(result)) as image:
        assert image.tobytes()==original.tobytes()
        assert image.info['custom']=='kept'
        assert json.loads(image.info['prompt'])==graph
        assert json.loads(image.info['workflow'])==workflow


def test_unknown_custom_node_uses_valid_api_fallback_and_discards_stale_workflow():
    graph=caption_graph()
    assert editor_workflow(graph,{}) is None
    info=PngInfo();info.add_text('workflow','{"nodes":[]}')
    stream=io.BytesIO();Image.new('RGB',(64,64)).save(stream,format='PNG',pnginfo=info)
    with Image.open(io.BytesIO(png_with_graph(stream.getvalue(),graph))) as image:
        assert 'workflow' not in image.info
        assert json.loads(image.info['prompt'])==graph


async def test_missing_output_is_rejected_before_paid_calls_and_targets_both_forms(service,monkeypatch):
    monkeypatch.setattr(gr,'Warning',lambda *a,**kw:None)
    ready=AsyncMock();monkeypatch.setattr('supervisor.ui.check_live_ready',ready)
    app=build_ui(service)
    calls=ui_callbacks(app)
    result=await calls['direct_create']('mountains','',1,False,'',512,512,4,1,42,'sfw',60,4,1,50000,2,'euler','normal','zh')
    assert '输出文件夹' in result[2] and 'direct-output' in result[4]
    assert result[3]['selected']=='prompt-studio'
    result=await calls['studio_create']([],{},'',15,'mountains','ink','',1,'Live','',512,512,4,1,42,'sfw',60,1,4,2,'zh','euler','normal')
    assert 'studio-output' in result[3] and 'studio-references' in result[3]
    ready.assert_not_called()
    assert not service.db.rows('SELECT id FROM tasks')


def test_last_configuration_is_outside_data_and_includes_prompts_workflow_and_folder(service,tmp_path):
    original=configure_editor(service)
    settings=direct(export_folder=str(tmp_path/'export'),params={'width':640,'height':896,'seed':77},direct_negative='blur')
    service.remember_last_run(settings,str(tmp_path/'references'))
    source=service.project_root/service.workflow.workflow_api_json
    source.unlink()
    loaded,folder=service.load_last_run()
    assert loaded==settings and folder==str(tmp_path/'references')
    assert service.last_run_prompts()==('mountains','blur')
    assert json.loads((service.project_root/service.workflow.workflow_api_json).read_bytes())==original
    service.remember_last_run(settings)
    assert service.load_last_run()[1] is None


def test_last_configuration_trigger_restores_values_and_never_uploaded_reference_paths(service,monkeypatch):
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    service.remember_last_run(direct(export_folder=str(service.project_root/'export')))
    app=build_ui(service)
    callback=next(f.fn for f in app.fns.values() if f.fn and f.fn.__name__=='import_last_configuration')
    f=next(f for f in app.fns.values() if f.fn==callback)
    result=callback()
    assert len(result)==len(f.outputs)
    values={getattr(component,'elem_id',None):value for component,value in zip(f.outputs,result)}
    assert values['direct-positive']=='mountains'
    assert values['studio-references'] is None and values['reference-source']=='images'
    assert values['direct-output']==str(service.project_root/'export')


async def test_caption_submits_only_branch_reads_text_and_never_starts_generation(service,tmp_path):
    configure_editor(service)
    graph=caption_graph();path=service.project_root/service.workflow.workflow_api_json
    path.write_text(json.dumps(graph),encoding='utf-8')
    service.enqueue=lambda task:None
    image=tmp_path/'reference.png';Image.new('RGB',(64,64)).save(image)
    task=service.enqueue_caption(image)
    submitted=[]
    def handle(request):
        if request.url.path=='/upload/image':return httpx.Response(200,json={'name':'new.png','subfolder':'supervisor-caption'})
        if request.url.path=='/object_info':return httpx.Response(200,json={'WD14Tagger|pysssss':{'output_node':True}})
        if request.url.path=='/prompt':
            submitted.append(json.loads(request.content)['prompt']);return httpx.Response(200,json={'prompt_id':'p'})
        if request.url.path=='/history/p':return httpx.Response(200,json={'p':{'status':{'completed':True},'outputs':{'2':{'tags':['mountains, river']}}}})
        raise AssertionError(request.url)
    service.comfy.transport=httpx.MockTransport(handle)
    await run_caption(service,task)
    assert list(submitted[0])==['1','2']
    assert submitted[0]['1']['inputs']['image']=='supervisor-caption/new.png'
    assert service.db.one('SELECT result FROM caption_jobs WHERE task_id=?',(task,))['result']=='mountains, river'
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    assert not service.db.rows('SELECT * FROM generations') and not service.db.rows('SELECT * FROM api_calls')


def test_caption_disabled_without_independent_caption_branch(service):
    assert caption_branch({'3':caption_graph()['3']}) is None
    unsafe=caption_graph();unsafe['2']['inputs']['image']=['3',0]
    assert caption_branch(unsafe) is None
    app=build_ui(service)
    component=next(c for c in app.blocks.values() if getattr(c,'elem_id',None)=='caption-input')
    assert component.interactive is False and 'caption-disabled' in component.elem_classes


async def test_deferred_cleanup_keeps_exports_running_tasks_and_generated_originals(service,tmp_path):
    image=tmp_path/'reference.png';Image.new('RGB',(64,64)).save(image)
    completed=service.create_task(direct(export_folder=str(tmp_path/'export')),[image])
    await service.run(completed)
    ref=service.db.one("SELECT * FROM assets WHERE task_id=? AND source_kind='reference'",(completed,))
    originals=[service.files.path(a['path']) for a in service.db.accepted(completed)]
    running=service.create_task(direct(),[image])
    old=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
    service.db.execute('UPDATE tasks SET updated_at=?',(old,))
    assert service.files.path(ref['path']).exists()  # Nothing is cleaned merely by creating Supervisor.
    assert service.cleanup_data_if_due()==3
    assert not service.files.path(ref['path']).exists()
    assert service.files.path(ref['thumb_path']).exists()
    assert all(p.is_file() for p in originals)
    assert len(list((tmp_path/'export'/f'task_{completed}').glob('group_*/*.png')))==2
    live=service.db.one("SELECT path FROM assets WHERE task_id=? AND source_kind='reference'",(running,))
    assert service.files.path(live['path']).is_file()
    assert service.cleanup_data_if_due()==0


def test_folder_restore_shows_folder_source_and_samples_again(service,tmp_path,monkeypatch):
    monkeypatch.setattr(gr,'Info',lambda *a,**kw:None)
    folder=tmp_path/'references';folder.mkdir()
    for index in range(4):Image.new('RGB',(64,64),(index,30,60)).save(folder/f'{index}.png')
    service.remember_last_run(TaskSettings(goal='mountains',autonomous=True,sample_count=2,groups=1,content_label='sfw'),str(folder))
    app=build_ui(service)
    callback=next(f for f in app.fns.values() if f.fn and f.fn.__name__=='import_last_configuration')
    result=callback.fn()
    assert len(result)==len(callback.outputs)
    by_id={getattr(c,'elem_id',None):value for c,value in zip(callback.outputs,result)}
    assert by_id['reference-source']=='folder' and by_id['reference-folder']['visible'] is True
    assert by_id['reference-images']['visible'] is False
    assert by_id['studio-folder']==str(folder)
    preview=next(f.fn for f in app.fns.values() if f.fn and f.fn.__name__=='preview_references')
    _,_,_,state=preview([],{'source':'folder'},str(folder),2)
    assert len(state['_sample']['selected'])==2
    assert all(str(folder) in name for name in state['_sample']['selected'])


async def test_cleanup_removes_display_copies_but_keeps_cache_used_by_unfinished_tasks(service,tmp_path):
    completed=service.create_task(direct(export_folder=str(tmp_path/'export')))
    await service.run(completed)
    source=service.files.path(service.db.accepted(completed)[0]['path'])
    used=service.files.path('ui-cache/used/'+source.name);used.parent.mkdir(parents=True)
    disposable=service.files.path('ui-cache/display/'+source.name);disposable.parent.mkdir(parents=True)
    shutil.copyfile(source,used);shutil.copyfile(source,disposable)
    import os,time
    for cached in (used,disposable):os.utime(cached,(time.time()-90000,time.time()-90000))
    service.create_task(direct(),[used])
    assert service.cleanup_data_if_due()==3
    assert used.is_file() and source.is_file() and not disposable.exists()
