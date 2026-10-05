from conftest import ui_callbacks
import json
from unittest.mock import AsyncMock

import gradio as gr
import pytest
from PIL import Image

from supervisor.control_words import enforce_control_words, enforce_lora_triggers, parse_control_words
from supervisor.models import PromptPlan,TaskSettings,StyleCard
from supervisor.db import uid,now
from supervisor.ui import build_ui
from test_workflow_editor import configure_editor


def test_optional_lora_triggers_preserve_weighted_tags_and_avoid_duplicate_negative():
    plan=PromptPlan(group_id='g',positive='(My_Style:1.2), river',negative='my_style, blurry',reason='test')
    assert enforce_lora_triggers(plan,'') is plan
    revised=enforce_lora_triggers(plan,'my_style，character_tag\ncharacter_tag')
    assert revised.positive=='character_tag, (My_Style:1.2), river'
    assert revised.negative=='blurry'
    assert plan.negative=='my_style, blurry'
    assert enforce_lora_triggers(revised,'my_style,character_tag')==revised


@pytest.mark.parametrize('direct',[False,True])
async def test_triggers_in_actual_prompt_on_every_group_and_iteration_and_last_config(service,tmp_path,direct):
    configure_editor(service)
    path=tmp_path/'ref.png'; Image.new('RGB',(64,64)).save(path)
    settings=TaskSettings(goal='landscape',groups=2,autonomous=True,lora_trigger_words='my_style, character_tag',
        direct_prompt='river' if direct else None,control_words=parse_control_words([[1,'sunset',1.4]],2))
    task=service.create_task(settings,[path],start=False)
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',(uid(),task,StyleCard(subject=['landscape']).model_dump_json(),now()))
    async def respond(task,settings,purpose,contract,payload):
        return PromptPlan(group_id=payload['group_id'],positive='river',reason='test'),None
    cloud=AsyncMock(side_effect=respond)
    service.cloud.request=cloud
    for group in service.db.rows('SELECT * FROM groups WHERE task_id=?',(task,)):
        for round_index in (0,1):
            group['round_index']=round_index
            row=await service.plan(task,settings,group,first=round_index==0)
            plan=PromptPlan.model_validate_json(row['body'])
            actual=service.comfy.graph(plan,'token')['6']['inputs']['text']
            assert actual.count('my_style')==1 and actual.count('character_tag')==1
            assert ('(sunset:1.4)' in actual)==(group['ordinal']==0)
    service.remember_last_run(settings,task_id=task)
    restored,_=service.load_last_run()
    assert restored.lora_trigger_words=='my_style, character_tag'
    if direct: cloud.assert_not_called()


def test_control_row_delete_shifts_values_clears_hidden_rows_and_keeps_one_empty_row(service):
    app=build_ui(service)
    controls={getattr(c,'elem_id',None):c for c in app.blocks.values() if getattr(c,'elem_id',None)}
    callback=next(f.fn for f in app.fns.values() if f.fn and f.inputs and f.outputs and
        getattr(f.fn,'__name__','')=='<lambda>' and f.outputs[0].__class__.__name__=='State' and len(f.outputs)==122)
    flat=[value for row in [[1,'sunset','1.2'],[2,'mountain','0.8']]+[[1,'','']]*28 for value in row]
    result=callback(2,*flat)
    assert result[0]==1 and result[1:4]==(2,'mountain','0.8')
    assert all(not result[2+3*i] and not result[3+3*i] for i in range(1,30))
    assert result[91]['visible'] is True and result[92]['visible'] is False
    only=callback(1,*result[1:91])
    assert only[0]==1 and only[2]=='' and only[3]=='' and only[-1]['interactive'] is True
    assert controls['studio-lora-triggers'].value in ('',None)


def test_preset_and_import_last_include_trigger_words(service,monkeypatch):
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    service.remember_last_run(TaskSettings(goal='landscape',lora_trigger_words='my_style'))
    app=build_ui(service)
    callbacks=ui_callbacks(app)
    importer=next(f for f in app.fns.values() if f.fn and f.fn.__name__=='import_last_configuration')
    values={getattr(c,'elem_id',None):v for c,v in zip(importer.outputs,importer.fn())}
    assert values['studio-lora-triggers']=='my_style'
    fields=[15,'landscape','ink\nphoto','',2,1,512,512,20,7,42,'euler','normal',60,6,1,50000,2,'sfw','character_tag']
    choice,_=callbacks['save_studio_preset']('lora',*fields,1,*([1,'','']*30))
    assert choice['value']=='lora'
    loaded=callbacks['load_studio_preset']('lora')
    assert loaded[19]=='character_tag'


async def test_direct_start_keeps_optional_trigger_configuration(service,monkeypatch):
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    monkeypatch.setattr('supervisor.ui.check_live_ready',AsyncMock())
    service.enqueue=lambda task:None
    app=build_ui(service)
    callback=ui_callbacks(app)['direct_create']
    result=await callback('river','blur',1,False,str(service.project_root/'export'),512,512,20,7,42,'sfw',60,6,1,50000,2,'euler','normal','zh','my_style')
    task=result[0]['value']
    assert service.settings(task).lora_trigger_words=='my_style'


def test_import_previous_settings_includes_groups_targets_themes_and_scoped_controls(service,monkeypatch):
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    settings=TaskSettings(goal='landscape',groups=3,per_group=4,target_styles=['ink','film','photo'],
        control_words=parse_control_words([[1,'sunset, mountain','1.4'],[3,'soft light','']],3),lora_trigger_words='my_style')
    service.remember_last_run(settings)
    app=build_ui(service)
    importer=next(f for f in app.fns.values() if f.fn and f.fn.__name__=='import_last_configuration')
    result=importer.fn()
    by_id={getattr(c,'elem_id',None):v for c,v in zip(importer.outputs,result)}
    assert by_id['studio-groups']==3 and by_id['studio-per-group']==4
    assert by_id['studio-styles']=='ink\nfilm\nphoto' and by_id['studio-lora-triggers']=='my_style'
    assert list(result[21:27])==['1','sunset, mountain','1.4','3','soft light','']
    assert (service.project_root/'config/last-run.local.json').is_file()


async def test_random_themes_saved_immediately_and_older_tasks_cannot_overwrite_latest(service,tmp_path):
    from supervisor.models import ThemePlan
    settings=TaskSettings(goal='landscape',autonomous=True,groups=3,target_styles=['ink'])
    reference=tmp_path/'reference.png'; Image.new('RGB',(64,64)).save(reference)
    task=service.create_task(settings,[reference],start=False)
    service.remember_last_run(settings,task_id=task)
    service.cloud.request=AsyncMock(return_value=(ThemePlan(themes=['film','photo']),None))
    await service.resolve_themes(task,settings)
    saved=json.loads((service.project_root/'config/last-run.local.json').read_text())
    assert saved['settings']['target_styles']==['ink','film','photo']
    newer=service.create_task(TaskSettings(goal='newer',groups=1,target_styles=['watercolor']),start=False)
    service.remember_last_run(service.settings(newer),task_id=newer)
    before=(service.project_root/'config/last-run.local.json').read_bytes()
    service.remember_task_settings(task)
    assert (service.project_root/'config/last-run.local.json').read_bytes()==before
