import json
from pathlib import Path
from unittest.mock import AsyncMock

import gradio as gr
import pytest

from supervisor.models import TaskSettings
from supervisor.ui import build_ui
from supervisor.studio_features import result_review_html


async def completed(service,tmp_path):
    task=service.create_task(TaskSettings(goal='test',direct_prompt='test',autonomous=True,demo=True,review_enabled=False,groups=1,per_group=2,max_rounds=2,export_folder=str(tmp_path/'exports'),params={'width':128,'height':128}),start=True)
    await service.run(task)
    rows=service.db.rows("SELECT * FROM assets WHERE task_id=? AND source_kind='generated' ORDER BY created_at",(task,))
    assert len(rows)==2
    return task,rows


async def test_delete_result_removes_original_export_preview_and_manifest_entry(service,tmp_path):
    task,rows=await completed(service,tmp_path)
    deleted,kept=rows
    source=service.files.path(deleted['path'])
    cached=service.files.path('ui-cache/test/'+source.name);cached.parent.mkdir(parents=True);cached.write_bytes(source.read_bytes())
    original_other=service.files.path(kept['path']).read_bytes()
    export=tmp_path/'exports'/f'task_{task}'
    kept_export=next(export.glob('group_*/*'+kept['id']+'.png'))
    service.cloud.request=AsyncMock(side_effect=AssertionError('Deleting must not call cloud'))
    service.delete_generated(task,deleted['id'])
    assert not source.exists() and not cached.exists() and not service.files.path(deleted['thumb_path']).exists()
    assert not list(export.glob('group_*/*'+deleted['id']+'.png'))
    assert not list(service.files.path(f'tasks/{task}/delivery').glob('group_*/*'+deleted['id']+'.png'))
    assert kept_export.read_bytes()==original_other==service.files.path(kept['path']).read_bytes()
    assert len(service.db.accepted(task))==1
    assert service.db.one('SELECT state FROM assets WHERE id=?',(deleted['id'],))['state']=='DELETED'
    for manifest in [export/'manifest.json',service.files.path(f'tasks/{task}/delivery/manifest.json')]:
        body=json.loads(manifest.read_bytes())
        assert body['groups'][0]['saved_count']==1 and body['groups'][0]['shortfall']==1
        assert body['groups'][0]['items'][0]['asset_id']==kept['id']
    assert deleted['id'] not in result_review_html(service,task)
    assert service.cloud.request.await_count==0
    service.delete_generated(task,deleted['id'])  # repeated clicks are idempotent
    assert service.db.one("SELECT COUNT(*) n FROM decisions WHERE asset_id=? AND action='USER_DELETED'",(deleted['id'],))['n']==1


async def test_delete_rejects_modified_export_and_wrong_task(service,tmp_path):
    task,rows=await completed(service,tmp_path)
    image=rows[0];source=service.files.path(image['path'])
    with pytest.raises(ValueError):service.delete_generated('another-task',image['id'])
    export=next((tmp_path/'exports'/f'task_{task}').glob('group_*/*'+image['id']+'.png'))
    export.write_bytes(b'user changed file')
    with pytest.raises(ValueError,match='已被修改'):service.delete_generated(task,image['id'])
    assert source.exists() and export.read_bytes()==b'user changed file'
    assert service.db.one('SELECT state FROM assets WHERE id=?',(image['id'],))['state']=='AVAILABLE'
    assert not service.db.one('SELECT * FROM user_deletions WHERE asset_id=?',(image['id'],))


async def test_delete_recovers_after_interrupted_unlink_and_keeps_reference_sources(service,tmp_path,monkeypatch):
    task,rows=await completed(service,tmp_path)
    image=rows[0];source=service.files.path(image['path'])
    cached=service.files.path('ui-cache/reference/'+source.name);cached.parent.mkdir(parents=True);cached.write_bytes(source.read_bytes())
    service.files.import_image(task,cached,'sfw')
    original_unlink=Path.unlink
    failed=False
    def unlink(path,*a,**k):
        nonlocal failed
        if path==source and not failed:
            failed=True
            raise PermissionError('test locked image')
        return original_unlink(path,*a,**k)
    monkeypatch.setattr(Path,'unlink',unlink)
    with pytest.raises(PermissionError):service.delete_generated(task,image['id'])
    assert service.db.one('SELECT state FROM user_deletions WHERE asset_id=?',(image['id'],))['state']=='pending'
    monkeypatch.setattr(Path,'unlink',original_unlink)
    service.files.recover()
    assert not source.exists() and cached.exists()
    assert service.db.one('SELECT state FROM user_deletions WHERE asset_id=?',(image['id'],))['state']=='committed'


async def test_result_delete_callback_targets_id_and_refresh_clears_deleted_image(service,tmp_path,monkeypatch):
    monkeypatch.setattr(gr,'Warning',lambda *a,**k:None)
    task,rows=await completed(service,tmp_path)
    settings=service.settings(task).model_copy(update={'demo':False})
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?',(settings.model_dump_json(),task))
    app=build_ui(service)
    refresh=next(f for f in app.fns.values() if f.fn and f.fn.__name__=='refresh_all')
    before=refresh.fn(task,'zh')
    mapping=json.loads(before[25]); assert len(mapping['images'])==2
    callback=next(f.fn for f in app.fns.values() if f.fn and f.fn.__name__=='delete_result_image')
    image=rows[0]
    assert callback(json.dumps({'task_id':task,'asset_id':image['id']}),'zh')=='图片已删除。'
    after=refresh.fn(task,'zh')
    assert len(after)==len(refresh.outputs) and len(json.loads(after[25])['images'])==1
    assert len(after[19]['value'])==1 and image['id'] not in after[19]['value'][0][1]
    assert len(refresh.fn(None,'zh'))==len(refresh.outputs)


async def test_pending_paused_image_can_be_force_deleted(service,tmp_path):
    task,rows=await completed(service,tmp_path)
    image=rows[0]
    service.db.transition(task,'PAUSED','RECOVERABLE_ERROR','INVALID_RESPONSE_REVIEW_REQUIRED')
    service.db.execute("UPDATE generations SET state='EVALUATE' WHERE id=?",(image['generation_id'],))
    assert service.delete_generated(task,image['id'])=='deleted'
    assert not service.files.path(image['path']).exists()
    assert image['id'] not in result_review_html(service,task)


async def test_busy_delete_is_queued_idempotently_and_recovery_waits_for_lease(service,tmp_path):
    task,rows=await completed(service,tmp_path)
    image=rows[0]
    import time
    service.active.add(task)
    service.db.execute('UPDATE tasks SET lease_until=? WHERE id=?',(time.time()+60,task))
    assert service.delete_generated(task,image['id'])=='queued'
    assert service.delete_generated(task,image['id'])=='queued'
    service.files.recover()
    assert service.files.path(image['path']).exists()
    assert image['id'] not in result_review_html(service,task)
    service.active.remove(task)
    service.db.execute('UPDATE tasks SET lease_until=NULL WHERE id=?',(task,))
    service.files.finish_user_deletions(task)
    assert not service.files.path(image['path']).exists()
    assert service.delete_generated(task,image['id'])=='deleted'
