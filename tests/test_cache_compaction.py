import json
import os
import time
from unittest.mock import patch

import gradio as gr
from PIL import Image

from supervisor.models import TaskSettings,StyleCard
from supervisor.db import uid,now
from supervisor.ui import launch


def test_compaction_removes_analyzed_reference_originals_but_keeps_unanalyzed_and_previews(service,tmp_path):
    source=tmp_path/'input.png';Image.new('RGB',(256,256),'blue').save(source)
    completed=service.create_task(TaskSettings(goal='analysis',reference_only=True),[source],start=False)
    service.db.transition(completed,'COMPLETED','ANALYZE')
    paused=service.create_task(TaskSettings(goal='paused'),[source],start=False)
    unfinished=service.create_task(TaskSettings(goal='not tagged'),[source],start=False)
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',(uid(),paused,StyleCard(style=['blue']).model_dump_json(),now()))
    analyzed=service.db.one("SELECT * FROM assets WHERE task_id=?",(paused,))
    service.db.execute('UPDATE assets SET metadata=? WHERE id=?',(json.dumps({'model_tags':{'style':['blue']}}),analyzed['id']))
    assert service.cleanup_data_if_due()==2
    for task in (completed,paused):
        asset=service.db.one('SELECT * FROM assets WHERE task_id=?',(task,))
        assert not service.files.path(asset['path']).exists() and service.files.path(asset['thumb_path']).exists()
    untouched=service.db.one('SELECT * FROM assets WHERE task_id=?',(unfinished,))
    assert service.files.path(untouched['path']).exists() and source.exists()


def test_cache_expiration_keeps_recent_uploads_and_unfinished_sources(service,tmp_path):
    cache=service.files.path('ui-cache');cache.mkdir()
    stale=cache/'old-upload.png';fresh=cache/'new-upload.png';pinned=cache/'pending.png'
    for path in (stale,fresh,pinned):Image.new('RGB',(64,64)).save(path)
    old=time.time()-90000
    os.utime(stale,(old,old));os.utime(pinned,(old,old))
    service.create_task(TaskSettings(goal='pending'),[pinned],start=False)
    assert service.cleanup_data_if_due()==1
    assert not stale.exists() and fresh.exists() and pinned.exists()
    assert service.cleanup_data_if_due()==0


def test_launch_serves_task_images_without_display_cache_copies(service):
    with patch.object(gr.Blocks,'launch',return_value='launched'):
        assert launch(service,7863)=='launched'
    path=service.files.path('tasks/example/images/test.png');path.parent.mkdir(parents=True);Image.new('RGB',(64,64)).save(path)
    from gradio.utils import is_static_file
    assert is_static_file(path)
    assert not is_static_file(service.data_root/'supervisor.db')
