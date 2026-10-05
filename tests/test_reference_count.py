from conftest import ui_callbacks
import json
from unittest.mock import AsyncMock

import gradio as gr
import pytest
from PIL import Image

from supervisor.files import sha256, studio_reference_paths
from supervisor.models import TaskSettings, StyleCard
from supervisor.ui import build_ui


@pytest.mark.parametrize('count',[1,50,65,1000])
def test_uploaded_and_folder_samples_obey_count_and_reuse_seed(tmp_path,count):
    paths=[]
    for index in range(80):
        path=tmp_path/f'{index:03}.png';path.touch();paths.append(path)
    uploaded,total=studio_reference_paths(paths,{'source':'images'},'',42,count)
    folder,folder_total=studio_reference_paths([],{'source':'folder'},str(tmp_path),42,count)
    assert total==folder_total==80
    assert len(uploaded)==min(count,80) and len(set(uploaded))==len(uploaded)
    assert uploaded==folder==studio_reference_paths(paths,{'source':'images'},'',42,count)[0]


@pytest.mark.parametrize('count',[None,True,0,-1,1.5,'bad',float('nan'),float('inf')])
def test_invalid_sample_count_is_rejected_before_scanning(tmp_path,count):
    with pytest.raises(ValueError,match='参考图数量'):
        studio_reference_paths([],{'source':'folder'},str(tmp_path/'missing'),42,count)


def test_number_input_accepts_more_than_slider_range_and_model_has_no_upper_limit(service):
    app=build_ui(service)
    number=next(c for c in app.blocks.values() if getattr(c,'elem_id',None)=='reference-count-input')
    slider=next(c for c in app.blocks.values() if getattr(c,'elem_id',None)=='reference-count-slider')
    assert number.value==slider.value==TaskSettings(goal='test').sample_count==15
    assert number.preprocess(1000)==1000 and number.maximum is None
    assert slider.minimum==1 and slider.maximum==50
    assert TaskSettings(goal='test',sample_count=1_000_000).sample_count==1_000_000


@pytest.mark.parametrize('source',['images','folder'])
@pytest.mark.parametrize('count',[3,65,1000])
async def test_preview_tagging_and_generation_use_same_sample_and_record_count(service,tmp_path,monkeypatch,source,count):
    reference_folder=tmp_path/'references';reference_folder.mkdir()
    paths=[]
    for index in range(70):
        path=reference_folder/f'{index:03}.png'
        Image.new('RGB',(64,64),(index,100,150)).save(path)
        paths.append(path)
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    monkeypatch.setattr(gr,'Warning',lambda *a,**k:None)
    ready=AsyncMock();monkeypatch.setattr('supervisor.ui.check_live_ready',ready)
    enqueue=service.enqueue
    service.enqueue=lambda task:enqueue(task) if service.settings(task).reference_only else None
    service.cloud.candidates=lambda *a,**k:[(None,None,'test')]
    service.cloud.credential_key=lambda credential:'test'
    async def tag_reference(task,settings,asset):
        card=StyleCard(reference_images=[asset['id']])
        metadata=json.loads(asset['metadata']);metadata['model_tags']=card.model_dump()
        service.db.execute('UPDATE assets SET metadata=? WHERE id=?',(json.dumps(metadata),asset['id']))
        return card
    service.tag_reference=AsyncMock(side_effect=tag_reference)
    app=build_ui(service)
    callbacks=ui_callbacks(app)
    files=paths if source=='images' else []
    folder=str(reference_folder) if source=='folder' else ''
    directory={'source':source}
    preview,message,seed,directory=callbacks['preview_references'](files,directory,folder,count)
    selected,total=studio_reference_paths(files,directory,folder,seed,count)
    assert f'已随机抽样 {len(selected)} 张' in message
    expected={sha256(path) for path in selected}
    updates=[update async for update in callbacks['auto_tag_references'](
        files,directory,folder,seed,'landscape','','sfw',1,50000,count)]
    assert updates[-1]==f'候选 70 张 · 已抽取 {min(count,70)} 张 · 已打标 {min(count,70)}/{min(count,70)} · 开始生图时复用结果'
    assert {call.args[2]['sha256'] for call in service.tag_reference.await_args_list}==expected
    analysis=service.db.one('SELECT id FROM tasks')
    assert service.settings(analysis['id']).sample_count==count
    result=await callbacks['studio_create'](files,directory,folder,count,'landscape','ink','',1,'Live',str(service.project_root/'export'),
        512,512,20,7,42,'sfw',60,1,6,2,'zh','euler','normal',seed)
    task=result[0]['value']
    assert service.settings(task).sample_count==count and ready.await_count==1
    refs=service.db.rows("SELECT sha256 FROM assets WHERE task_id=? AND source_kind='reference'",(task,))
    assert len(refs)==len(selected) and {asset['sha256'] for asset in refs}==expected
    event=json.loads(service.db.one("SELECT body FROM events WHERE task_id=? AND kind='SAMPLE'",(task,))['body'])
    assert event=={'seed':seed,'candidate_count':total,'selected_count':len(selected),'requested_count':count}
    assert not service.db.rows('SELECT id FROM api_calls')
