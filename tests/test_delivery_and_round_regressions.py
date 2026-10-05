import json
import os
from pathlib import Path
import zipfile
from unittest.mock import AsyncMock

import pytest

from conftest import ui_callbacks
from supervisor.control_words import parse_control_words, control_word_rows, enforce_control_words
from supervisor.delivery import delivered_image_count, export_image_zip
from supervisor.models import Evaluation, PromptPlan, TaskSettings
from supervisor.result_prompt import prompt_from_result
from supervisor.studio import task_progress
from supervisor.ui import build_ui


def direct(tmp_path,**extra):
    return TaskSettings(goal='mountains',direct_prompt='mountains, warm light',direct_negative='blur',
        autonomous=True,images_only_delivery=True,prescreen=False,review_enabled=False,groups=1,per_group=2,max_rounds=2,demo=True,
        export_folder=str(tmp_path/'exports'),delivery_layout='flat',params={'width':128,'height':128},**extra)


@pytest.mark.parametrize('field',['anatomy','structure','hands'])
@pytest.mark.parametrize('score',[41.9,42,50,59.9])
def test_deformation_boundary_is_42_and_preserves_actual_scores(service,field,score):
    values=dict(asset_id='asset',stage='final',overall=90,prompt_alignment=90,aesthetics=90,
        composition=90,anatomy=90,structure=90,hands=90,artifacts=90,style_match=90,
        safety_score=100,nsfw_target=None,decision='keep',delete_reason=[],prompt_suggestions=[],
        issues=[],unassessable_fields=['nsfw_target'])
    values[field]=score
    evaluation=Evaluation(**values)
    original=evaluation.model_dump()
    action,reason=service.evaluate_decision(evaluation,TaskSettings(goal='portrait',autonomous=True,quality_threshold=60),True)
    assert action==('QUARANTINE' if score<42 else 'ACCEPTED')
    if score<42:
        assert reason=='BASIC_STRUCTURE_FAILED'
    assert evaluation.model_dump()==original


@pytest.mark.parametrize('mode',['files','zip'])
async def test_delivery_export_and_delete_keep_counts_and_unrelated_files(service,tmp_path,mode):
    task=service.create_task(direct(tmp_path,delivery_format=mode))
    await service.run(task)
    root=tmp_path/'exports'
    unrelated=root/'unrelated.png';unrelated.write_bytes(b'keep user file')
    assets=service.db.accepted(task)
    assert len(assets)==delivered_image_count(service,task)==2
    assert not (root/f'task_{task}').exists()
    if mode=='files':
        assert len(list(root.glob(f'{task[:8]}_group_*.png')))==2
    else:
        assert list(root.glob('*.png'))==[unrelated]
    archive=Path(export_image_zip(service,task))
    with zipfile.ZipFile(archive) as stream:
        assert len(stream.namelist())==2
        assert all(stream.read(name) for name in stream.namelist())
    service.delete_generated(task,assets[0]['id'])
    assert delivered_image_count(service,task)==1
    with zipfile.ZipFile(archive) as stream:
        assert len(stream.namelist())==1
        assert assets[0]['id'] not in stream.namelist()[0]
        assert assets[1]['id'] in stream.namelist()[0]
    assert unrelated.read_bytes()==b'keep user file'


async def test_zip_write_failure_is_not_reported_as_successful_delivery(service,tmp_path,monkeypatch):
    task=service.create_task(direct(tmp_path,delivery_format='zip'))
    replace=os.replace
    def fail(source,destination):
        if str(destination).endswith('.zip'):
            raise OSError('locked output')
        return replace(source,destination)
    monkeypatch.setattr('supervisor.delivery.os.replace',fail)
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']!='COMPLETED'
    assert delivered_image_count(service,task)==0
    assert '已保存 0/2' in task_progress(service,task)
    assert len(service.db.accepted(task))==2
    assert not list((tmp_path/'exports').glob('.supervisor-zip-*'))
    assert service.db.one('SELECT state,reason FROM tasks WHERE id=?',(task,))=={'state':'PAUSED','reason':'OUTPUT_FOLDER_UNWRITABLE'}
    generations=service.db.one('SELECT COUNT(*) n FROM generations WHERE task_id=?',(task,))['n']
    monkeypatch.setattr('supervisor.delivery.os.replace',replace)
    service._dispatch_next=lambda:None
    service.resume(task)
    await service.run(task)
    assert delivered_image_count(service,task)==2
    assert service.db.one('SELECT COUNT(*) n FROM generations WHERE task_id=?',(task,))['n']==generations


async def test_current_review_finishes_after_end_round_and_waiting_work_is_cancelled(service,tmp_path):
    settings=direct(tmp_path).model_copy(update={'review_enabled':True,'per_group':1,'max_rounds':3})
    first=service.create_task(settings)
    queued=service.create_task(settings)
    request=service.cloud.request
    reviews=[]
    async def end_before_review(task,settings,purpose,contract,payload,images=None):
        if purpose=='review':
            reviews.append(payload['asset_id'])
            service.end_round()
        return await request(task,settings,purpose,contract,payload,images)
    service.cloud.request=end_before_review
    await service.run(first)
    assert len(reviews)==1
    assert service.db.one('SELECT id FROM evaluations WHERE asset_id=?',(reviews[0],))
    for task in (first,queued):
        assert service.db.one('SELECT state,reason FROM tasks WHERE id=?',(task,))=={'state':'CANCELLED','reason':'USER_ROUND_ENDED'}
    assert not service.db.one('SELECT id FROM generations WHERE task_id=?',(queued,))
    new=service.create_task(direct(tmp_path))
    await service.run(new)
    assert delivered_image_count(service,new)==2


async def test_prompt_handoff_uses_selected_history_image_even_without_score(service,tmp_path):
    old=service.create_task(direct(tmp_path))
    await service.run(old)
    asset=service.db.accepted(old)[0]
    other=service.create_task(direct(tmp_path).model_copy(update={'direct_prompt':'forest'}))
    service.db.execute("UPDATE generations SET state='EVALUATE' WHERE id=?",(asset['generation_id'],))
    request=json.dumps({'task_id':old,'asset_id':asset['id'],'request_id':'selected-prompt-1'})
    body,plan,group,attempt=prompt_from_result(service,request)
    assert (plan.positive,plan.negative)==('mountains, warm light','blur')
    assert group==1 and attempt==1
    assert len(service.db.rows('SELECT id FROM tasks'))==2
    with pytest.raises(ValueError):
        prompt_from_result(service,json.dumps({**body,'task_id':other}))


def test_all_group_control_terms_roundtrip_and_reach_each_group():
    words=parse_control_words([['全部','soft light, warm colors','1.2']],3)
    assert control_word_rows(words)==[['全部','soft light, warm colors','1.2']]
    for group in range(1,4):
        plan=enforce_control_words(PromptPlan(group_id='group',positive='mountains',negative='',params={},reason='scene'),
            [word for word in words if word.group in (None,group)])
        assert '(soft light:1.2)' in plan.positive and '(warm colors:1.2)' in plan.positive


@pytest.mark.parametrize('rows',[
    [['全部','warm light','1.2'],['1','warm light','1.5']],
    [['1','Warm Light','1.5'],['全部','warm light','1.2']],
])
def test_global_and_scoped_duplicate_controls_fail_before_submission(rows):
    with pytest.raises(ValueError,match='重复'):
        parse_control_words(rows,3)


async def test_zip_uses_same_duplicate_filter_as_delivery_manifest(service,tmp_path):
    settings=direct(tmp_path).model_copy(update={'groups':2,'per_group':1,'max_rounds':3,'review_enabled':True})
    task=service.create_task(settings)
    await service.run(task)
    first,second=service.db.accepted(task)
    service.files.path(second['path']).write_bytes(service.files.path(first['path']).read_bytes())
    service.db.execute('UPDATE assets SET sha256=? WHERE id=?',(first['sha256'],second['id']))
    service.deliver(task,'COMPLETED','TARGET_REACHED',rebuild=True)
    manifest=json.loads(service.files.path(f'tasks/{task}/delivery/manifest.json').read_bytes())
    expected={item['relative_path'] for group in manifest['groups'] for item in group['items']}
    with zipfile.ZipFile(export_image_zip(service,task)) as stream:
        assert set(stream.namelist())==expected
    assert len(expected)==delivered_image_count(service,task)==1


async def test_progress_stream_submits_format_choice_and_character_snapshot(service,tmp_path,monkeypatch):
    import gradio as gr
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    monkeypatch.setattr('supervisor.ui.check_live_ready',AsyncMock())
    service.enqueue=lambda _:None
    callbacks=ui_callbacks(build_ui(service))
    updates=[value async for value in callbacks['direct_submit']('mountains','blur',1,False,
        str(tmp_path/'export'),128,128,4,1,42,'sfw',60,4,1,50000,2,'euler','normal','zh',True)]
    assert 'working' in updates[0][-1]
    task=updates[-1][0]['value']
    settings=service.settings(task)
    assert settings.delivery_format=='zip' and settings.delivery_layout=='flat'
    assert settings.direct_prompt=='mountains' and settings.direct_negative=='blur'
    assert 'done' in updates[-1][-1]
    from PIL import Image
    reference=tmp_path/'reference.png';Image.new('RGB',(64,64),'blue').save(reference)
    rows=['全部','warm light','1.2']+['1','','']*29
    updates=[value async for value in callbacks['studio_submit']([str(reference)],{},'',15,'portrait',
        'ink\nfilm\nphoto','',3,'Live',str(tmp_path/'studio-export'),128,128,4,1,42,'sfw',60,1,4,2,
        'zh','euler','normal',42,50000,1,*rows,'character_tag','申鹤',False)]
    assert 'value' in updates[-1][0],updates[-1][1]
    settings=service.settings(updates[-1][0]['value'])
    assert settings.character=='申鹤' and settings.lora_trigger_words=='character_tag'
    assert settings.delivery_format=='files' and settings.delivery_layout=='flat'
    assert len(settings.control_words)==1 and settings.control_words[0].group is None


async def test_task_choice_label_tracks_completion_without_changing_selection(service,tmp_path):
    task=service.create_task(direct(tmp_path))
    callback=ui_callbacks(build_ui(service))['refresh_task_labels']
    before,signature=callback('zh',None)
    assert any(task==value and '等待队列' in label for label,value in before['choices'])
    await service.run(task)
    after,signature=callback('zh',signature)
    assert any(task==value and '已完成' in label for label,value in after['choices'])
    assert 'value' not in after
    unchanged,_=callback('zh',signature)
    assert 'choices' not in unchanged
