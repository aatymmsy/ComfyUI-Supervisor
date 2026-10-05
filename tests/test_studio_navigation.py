from conftest import ui_callbacks
from unittest.mock import AsyncMock

import gradio as gr
import pytest
from PIL import Image

from supervisor.models import TaskSettings
from supervisor.node_editor_view import node_table, parameter_tabs
from supervisor.ui import build_ui
from test_workflow_editor import configure_editor


def tabs(app):
    return [c for c in app.blocks.values() if isinstance(c,gr.Tab) and c.visible]


def test_fresh_start_requires_setup_but_cached_task_opens_studio(service):
    app = build_ui(service)
    assert [c.id for c in tabs(app)] == ['setup','workflow-editor','studio','prompt-studio','intervention','discussion']
    views = next(c for c in app.blocks.values() if isinstance(c,gr.Tabs))
    assert views.selected == 'setup'
    service.create_task(TaskSettings(goal='past task'),start=False)
    app = build_ui(service)
    assert next(c for c in app.blocks.values() if isinstance(c,gr.Tabs)).selected == 'studio'


def test_saved_workflow_without_tasks_opens_studio(service):
    configure_editor(service)
    app = build_ui(service)
    assert next(c for c in app.blocks.values() if isinstance(c,gr.Tabs)).selected == 'studio'


async def test_requested_groups_use_separate_themes_and_one_delivery_each(service,tmp_path,monkeypatch):
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    monkeypatch.setattr(gr,'Warning',lambda *a,**k:None)
    readiness = AsyncMock()
    monkeypatch.setattr('supervisor.ui.check_live_ready',readiness)
    service.enqueue = lambda task:None
    app = build_ui(service)
    callbacks =ui_callbacks(app)
    image = tmp_path/'reference.png'
    Image.new('RGB',(64,64)).save(image)
    async def start(groups,styles,per_group=1):
        return await callbacks['studio_create']([str(image)],[],'',15,'landscape',styles,'',groups,'Live',str(service.project_root/'export'),
            512,512,20,7,42,'sfw',60,1,12,2,'zh','euler','normal',per_group_target=per_group)
    result = await start(3,'ink\nphoto\nwatercolor')
    task = result[0]['value']
    settings = service.settings(task)
    assert settings.groups == 3 and settings.per_group == 1 and settings.max_rounds == 12
    assert settings.target_styles == ['ink','photo','watercolor']
    assert len(service.db.rows('SELECT id FROM groups WHERE task_id=?',(task,))) == 3
    readiness.reset_mock()
    result = await start(3,'ink\nphoto')
    assert '模型随机补选主题' in result[2]
    assert result[4]['selected'] == 'studio'
    assert readiness.await_count == 1 and len(service.db.rows('SELECT id FROM tasks')) == 2
    readiness.reset_mock()
    for invalid in (None,0,51,1.5,True,float('nan')):
        failure = await start(3,'ink\nphoto\nwatercolor',invalid)
        assert '每组产出张数须为' in failure[2]
        assert 'studio-per-group' in failure[3]
    assert readiness.await_count == 0 and len(service.db.rows('SELECT id FROM tasks')) == 2
    assert not service.db.rows('SELECT id FROM api_calls')


def test_model_name_replaces_loader_type_in_table_and_editor():
    rows = [dict(node_id='1',class_type='DualCLIPLoader',input='clip_name1',key='1:clip_name1',value='first.safetensors'),
            dict(node_id='1',class_type='DualCLIPLoader',input='clip_name2',key='1:clip_name2',value='second.safetensors'),
            dict(node_id='2',class_type='KSampler',input='steps',key='2:steps',value=20)]
    table = node_table(rows,'1:clip_name2')
    editor = parameter_tabs(rows,'1:clip_name2')
    assert 'DualCLIPLoader' not in table+editor
    assert 'first.safetensors · second.safetensors' in table and 'KSampler' in table
    rows[1]['value'] = '<custom>.safetensors'
    assert '&lt;custom&gt;.safetensors' in parameter_tabs(rows,'1:clip_name2')
