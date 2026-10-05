from conftest import ui_callbacks
import json
import time
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from pydantic import ValidationError

from supervisor.models import TaskSettings


def paused_task(service):
    settings = TaskSettings(goal='landscape', groups=1, per_group=1, max_rounds=6, patience=5, quality_threshold=80)
    task = service.create_task(settings, start=False)
    service.db.execute('UPDATE groups SET round_index=6,stale_rounds=5 WHERE task_id=?', (task,))
    service.db.transition(task, 'PARTIAL', 'DELIVER', 'ROUND_OR_PATIENCE_LIMIT')
    return task


def test_edit_round_limited_task_preserves_progress_budget_and_pauses_before_resume(service):
    task = paused_task(service)
    call = service.db.reserve(task, 'review', 'p', 'm', 'k', {}, 100, 1000000)
    service.db.settle(call, 'SUCCESS', cost=15, usage={'input_tokens':100,'output_tokens':50})
    before = service.db.one('SELECT created_at FROM tasks WHERE id=?', (task,))
    service.update_task_limits(task, 12, 70)
    settings = service.settings(task)
    assert settings.max_rounds == 12 and settings.quality_threshold == 70
    assert settings.patience == 5 and settings.token_budget == 50000
    group = service.db.one('SELECT round_index,stale_rounds FROM groups WHERE task_id=?', (task,))
    assert group == {'round_index':6,'stale_rounds':0}
    assert service.db.token_usage(task) == 150 and service.db.budget(task)['spent'] == 15
    current = service.db.one('SELECT state,phase,created_at FROM tasks WHERE id=?', (task,))
    assert current == {'state':'PAUSED','phase':'LIMITS_UPDATED','created_at':before['created_at']}
    event = json.loads(service.db.one("SELECT body FROM events WHERE kind='TASK_LIMITS_UPDATED'")['body'])
    assert event['before'] == {'max_rounds':6,'quality_threshold':80}
    assert event['after'] == {'max_rounds':12,'quality_threshold':70}
    service.resume(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?', (task,))['state'] == 'RUNNING'
    assert service.next_group(task, settings)['round_index'] == 6


def test_used_rounds_cannot_be_erased_by_editing_the_limit(service):
    task = paused_task(service)
    service.update_task_limits(task, 4, 65)
    assert service.settings(task).max_rounds == 4
    assert service.db.one('SELECT state FROM tasks WHERE id=?', (task,))['state'] == 'PARTIAL'
    assert service.db.one('SELECT round_index FROM groups WHERE task_id=?', (task,))['round_index'] == 6


@pytest.mark.parametrize('rounds,score', [(0,80),(101,80),(6.5,80),(6,-1),(6,101),(6,float('nan'))])
def test_invalid_limits_do_not_partially_update_the_task(service, rounds, score):
    task = paused_task(service)
    before = service.db.one('SELECT * FROM tasks WHERE id=?', (task,))
    with pytest.raises(ValidationError):
        service.update_task_limits(task, rounds, score)
    assert service.db.one('SELECT * FROM tasks WHERE id=?', (task,)) == before
    assert not service.db.rows("SELECT id FROM events WHERE kind='TASK_LIMITS_UPDATED'")


@pytest.mark.parametrize('state,reason', [('RUNNING',None),('STOPPING',None),('COMPLETED','TARGET_REACHED'),('CANCELLED','USER_STOP'),('PARTIAL','BUDGET_EXHAUSTED')])
def test_active_or_other_terminal_tasks_are_not_reopened(service, state, reason):
    task = paused_task(service)
    service.db.transition(task, state, 'DELIVER', reason)
    with pytest.raises(ValueError, match='TASK_LIMITS_NOT_EDITABLE'):
        service.update_task_limits(task, 12, 70)
    assert service.settings(task).max_rounds == 6


def test_paused_task_with_a_worker_lease_is_not_edited(service):
    task = paused_task(service)
    service.db.execute('UPDATE tasks SET lease_until=? WHERE id=?', (time.time()+60,task))
    with pytest.raises(ValueError, match='PAUSE_TASK_BEFORE_LIMITS'):
        service.update_task_limits(task, 12, 70)
    assert service.settings(task).max_rounds == 6


async def test_studio_controls_feed_new_tasks_and_apply_to_existing_tasks(service, tmp_path, monkeypatch):
    import gradio as gr
    from supervisor.ui import build_ui
    monkeypatch.setattr(gr, 'Info', lambda *args, **kwargs: None)
    monkeypatch.setattr(gr, 'Warning', lambda *args, **kwargs: None)
    monkeypatch.setattr('supervisor.ui.check_live_ready', AsyncMock())
    service.enqueue = lambda task: None
    app = build_ui(service)
    controls = {getattr(c,'elem_id',None):c for c in app.blocks.values() if getattr(c,'elem_id',None)}
    assert controls['studio-rounds'].minimum == 1 and controls['studio-rounds'].maximum == 100
    assert controls['studio-quality'].minimum == 0 and controls['studio-quality'].maximum == 100
    assert controls['studio-per-group'].minimum == 1 and controls['studio-per-group'].maximum == 50
    callbacks =ui_callbacks(app)
    image = tmp_path/'reference.png'
    Image.new('RGB',(64,64),'green').save(image)
    await callbacks['studio_create']([str(image)],[], '',15,'landscape','ink','',1,'Live',str(service.project_root/'export'),
                                    512,512,20,7,42,'sfw',68,1,15,2,'zh','euler','normal',per_group_target=3)
    task = service.db.one("SELECT id FROM tasks WHERE state='RUNNING'")['id']
    assert service.settings(task).max_rounds == 15 and service.settings(task).quality_threshold == 68
    assert service.settings(task).per_group == 3
    service.db.transition(task,'PAUSED','USER_PAUSE')
    result = callbacks['studio_update_limits'](task,20,72,'zh',5)
    assert '设置已保存' in result[0]
    assert service.settings(task).max_rounds == 20 and service.settings(task).quality_threshold == 72
    assert callbacks['load_task_limits'](task,'zh')[:2] == (20,72)
    assert callbacks['load_task_limits'](task,'zh')[3] == 5
    assert '1 组 × 5 张 = 5 张' in result[1]
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state'] == 'PAUSED'
    assert not service.db.rows('SELECT id FROM api_calls')


def test_image_target_change_reopens_round_limited_task_without_erasing_attempts(service):
    task = paused_task(service)
    service.update_task_limits(task,12,80,5)
    assert service.settings(task).per_group == 5
    assert service.db.one('SELECT round_index,stale_rounds FROM groups WHERE task_id=?',(task,)) == {'round_index':6,'stale_rounds':0}
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state'] == 'PAUSED'
    event = json.loads(service.db.one("SELECT body FROM events WHERE kind='TASK_LIMITS_UPDATED'")['body'])
    assert event['before']['per_group'] == 1 and event['after']['per_group'] == 5


@pytest.mark.parametrize('target',[0,51,2.5,float('nan'),True])
def test_invalid_image_target_leaves_existing_task_unchanged(service,target):
    task = paused_task(service)
    before = service.db.one('SELECT * FROM tasks WHERE id=?',(task,))
    with pytest.raises((ValidationError,ValueError)):
        service.update_task_limits(task,12,60,target)
    assert service.db.one('SELECT * FROM tasks WHERE id=?',(task,)) == before
