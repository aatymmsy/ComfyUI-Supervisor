import json
from unittest.mock import AsyncMock

import gradio as gr
import pytest
from PIL import Image

from supervisor.control_words import parse_control_words, control_word_rows, controls_for_group
from supervisor.models import TaskSettings, StyleCard, PromptPlan
from supervisor.db import uid, now
from supervisor.studio_features import StudioPreset, PresetStore
from supervisor.ui import build_ui
from test_workflow_editor import configure_editor


@pytest.mark.parametrize('groups',['1,3','1，3',' 1 ， 3,1 '])
def test_multi_groups_expand_once_and_restore_same_row(groups):
    words=parse_control_words([[groups,'sunset，mountain',1.4]],3)
    assert [(w.group,w.word,w.weight,w.weight_group) for w in words]==[
        (1,'sunset',1.4,1),(1,'mountain',1.4,1),(3,'sunset',1.4,1),(3,'mountain',1.4,1)]
    assert control_word_rows(words)==[['1,3','sunset, mountain','1.4']]
    settings=TaskSettings(goal='landscape',groups=3,control_words=words)
    assert len(controls_for_group(settings,0))==2 and controls_for_group(settings,1)==[]
    assert len(controls_for_group(settings,2))==2
    assert TaskSettings.model_validate_json(settings.model_dump_json())==settings


@pytest.mark.parametrize('groups',['1,bad','1,0','1,4','1,2.5','， ,'])
def test_invalid_multi_group_row_is_rejected(groups):
    with pytest.raises(ValueError):
        parse_control_words([[groups,'sunset',1]],3)


def test_duplicate_control_in_overlapping_groups_is_rejected():
    with pytest.raises(ValueError):
        parse_control_words([['1,2','sunset',1],['2,3','SUNSET',1.4]],3)


def test_auto_weights_and_single_tags_keep_multi_group_row_origin():
    words=parse_control_words([['2，1','sunset',''],[3,'mountain',1.2]],3)
    assert control_word_rows(words)==[['2,1','sunset',''],['3','mountain','1.2']]
    assert parse_control_words(control_word_rows(words),3)==words


def test_presets_preserve_multi_group_text(service):
    preset=StudioPreset(goal='landscape',groups=3,rows=[['1，3','sunset, mountain','1.4']])
    store=PresetStore(service.project_root/'config/test-presets.json')
    store.save('multi',preset)
    restored=store.read()['multi']
    assert restored.rows==preset.rows
    assert parse_control_words(restored.rows,3)==parse_control_words(preset.rows,3)


def test_import_previous_configuration_keeps_multi_group_row(service,monkeypatch):
    configure_editor(service)
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    settings=TaskSettings(goal='landscape',groups=3,control_words=parse_control_words([['1，3','sunset, mountain',1.4],[2,'soft light','']],3))
    service.remember_last_run(settings)
    app=build_ui(service)
    callback=next(f.fn for f in app.fns.values() if getattr(f.fn,'__name__','')=='import_last_configuration')
    result=callback()
    assert list(result[21:27])==['1,3','sunset, mountain','1.4','2','soft light','']


async def test_multi_group_controls_reach_only_selected_actual_workflows(service,tmp_path):
    configure_editor(service)
    image=tmp_path/'reference.png';Image.new('RGB',(64,64)).save(image)
    settings=TaskSettings(goal='landscape',autonomous=True,groups=3,target_styles=['ink','film','photo'],
        control_words=parse_control_words([['1，3','sunset, mountain',1.4]],3))
    task=service.create_task(settings,[image],start=False)
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',(uid(),task,StyleCard(subject=['river']).model_dump_json(),now()))
    async def cloud(task_id,settings,purpose,contract,payload):
        return PromptPlan(group_id=payload['group_id'],positive='river',reason='mock'),None
    service.cloud.request=cloud
    for group in service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(task,)):
        row=await service.plan(task,settings,group,first=True)
        actual=service.comfy.graph(PromptPlan.model_validate_json(row['body']),'test')['6']['inputs']['text']
        if group['ordinal'] in (0,2):
            assert '(sunset:1.4)' in actual and '(mountain:1.4)' in actual
        else:
            assert 'sunset' not in actual and 'mountain' not in actual


def test_character_rows_scope_and_legacy_roundtrip():
    from supervisor.control_words import parse_character_input,character_form_value,character_for_group
    text=json.dumps([{'groups':'1，3','name':'山林守卫'},{'groups':'2','name':'旅行者'}],ensure_ascii=False)
    legacy,roles=parse_character_input(text,3)
    settings=TaskSettings(goal='portrait',groups=3,character='旧角色',character_controls=roles)
    assert legacy=='' and [character_for_group(settings,i) for i in range(3)]==['山林守卫','旅行者','山林守卫']
    assert parse_character_input(character_form_value(settings),3)[1]==roles
    assert parse_character_input('旅行者',3)==('旅行者',[])
    assert character_for_group(TaskSettings(goal='portrait',groups=3,character='旅行者'),2)=='旅行者'
    only=TaskSettings(goal='portrait',groups=3,character='旧角色',character_controls=[{'name':'守卫','groups':[1]}])
    assert character_for_group(only,1)==''


@pytest.mark.parametrize('groups',['0','4','1,bad','1.5','全部,2','-1'])
def test_invalid_character_group_rows_are_rejected(groups):
    from supervisor.control_words import parse_character_input
    with pytest.raises(ValueError):parse_character_input(json.dumps([{'groups':groups,'name':'旅行者'}]),3)


def test_global_and_multi_character_rows_do_not_duplicate_overlapping_identity():
    from supervisor.control_words import parse_character_input,character_for_group
    _,roles=parse_character_input(json.dumps([{'groups':'','name':'守卫'},{'groups':'2,3','name':'旅行者'}]),3)
    settings=TaskSettings(goal='portrait',groups=3,character_controls=roles)
    assert character_for_group(settings,0)=='守卫' and character_for_group(settings,1)=='守卫；旅行者'
    with pytest.raises(ValueError):
        parse_character_input(json.dumps([{'groups':'全部','name':'守卫'},{'groups':'2','name':'守卫'}]),3)


async def test_group_character_identity_reaches_initial_planning_and_iteration_only_for_its_group(service,tmp_path):
    from supervisor.control_words import parse_character_input
    configure_editor(service)
    _,roles=parse_character_input(json.dumps([{'groups':'1,3','name':'山林守卫'},{'groups':'2','name':'旅行者'}]),3)
    settings=TaskSettings(goal='clothed portraits in a forest',autonomous=True,groups=3,
        target_styles=['ink','film','photo'],character_controls=roles)
    image=tmp_path/'reference.png';Image.new('RGB',(64,64)).save(image)
    task=service.create_task(settings,[image],start=False)
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',(uid(),task,StyleCard(subject=['forest']).model_dump_json(),now()))
    seen=[]
    async def cloud(task_id,settings,purpose,contract,payload):
        seen.append((payload['group_id'],payload.get('character')))
        return PromptPlan(group_id=payload['group_id'],positive='adult person, forest, clear face '+str(len(seen)),reason='mock'),None
    service.cloud.request=cloud
    groups=service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(task,))
    for group in groups:
        await service.plan(task,settings,group,first=True)
        await service.plan(task,settings,group)
    assert seen==[(groups[i]['id'],name) for i,name in enumerate(['山林守卫','旅行者','山林守卫']) for _ in range(2)]


def test_character_rows_are_preserved_in_preset_and_import(service,monkeypatch):
    from supervisor.control_words import parse_character_input,character_form_value
    _,roles=parse_character_input(json.dumps([{'groups':'1,3','name':'守卫'},{'groups':'2','name':'旅行者'}]),3)
    store=PresetStore(service.project_root/'config/role-presets.json')
    store.save('roles',StudioPreset(goal='portraits',groups=3,character_controls=roles))
    assert store.read()['roles'].character_controls==roles
    configure_editor(service)
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    settings=TaskSettings(goal='portraits',groups=3,character_controls=roles)
    service.remember_last_run(settings)
    app=build_ui(service)
    callback=next(f.fn for f in app.fns.values() if getattr(f.fn,'__name__','')=='import_last_configuration')
    assert callback()[20]==character_form_value(settings)
