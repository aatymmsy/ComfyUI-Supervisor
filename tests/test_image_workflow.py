import json

import gradio as gr
import pytest
from PIL import Image, PngImagePlugin

from conftest import ui_callbacks
from test_workflow_editor import configure_editor
from supervisor.image_workflow import image_workflow
from supervisor.models import TaskSettings
from supervisor.setup import save_workflow_graph
from supervisor.ui import build_ui


def original_image(tmp_path,graph,extra=None,name='original.png'):
    info=PngImagePlugin.PngInfo();info.add_text('prompt',json.dumps(graph))
    for key,value in (extra or {}).items():
        info.add_text(key,json.dumps(value))
    path=tmp_path/name;Image.new('RGB',(64,64),'steelblue').save(path,pnginfo=info)
    return path


def test_png_prefers_execution_graph_and_preserves_original_file(service,tmp_path):
    graph=configure_editor(service)
    image=original_image(tmp_path,graph,{'workflow':{'nodes':[{'id':4,'type':'CheckpointLoaderSimple'}]}})
    before=image.read_bytes()
    assert image_workflow(image)==graph
    config=json.loads(save_workflow_graph(service,image_workflow(image),'http://127.0.0.1:8188'))
    assert config['bindings']['positive'][0]['node_id']=='6'
    assert json.loads((service.project_root/config['workflow_api_json']).read_bytes())==graph
    assert image.read_bytes()==before and not service.db.rows('SELECT * FROM generations')


@pytest.mark.parametrize('fmt,extension',[('WEBP','.webp'),('JPEG','.jpg')])
def test_exif_execution_graph_is_read(service,tmp_path,fmt,extension):
    graph=configure_editor(service)
    exif=Image.Exif();exif[0x010f]='prompt:'+json.dumps(graph);exif[0x0110]='workflow:'+json.dumps({'nodes':[]})
    image=tmp_path/('comfy'+extension)
    Image.new('RGB',(64,64)).save(image,format=fmt,exif=exif)
    assert image_workflow(image)==graph


@pytest.mark.parametrize('raw,expected',[(None,'没有 ComfyUI'),('{invalid','元数据无效'),(json.dumps({'nodes':[]}),'缺少可执行')])
def test_missing_malformed_or_layout_only_metadata_has_clear_error(service,tmp_path,raw,expected):
    image=tmp_path/'invalid.png';info=PngImagePlugin.PngInfo()
    if raw is not None:info.add_text('workflow',raw)
    Image.new('RGB',(64,64)).save(image,pnginfo=info)
    before=service.workflow
    with pytest.raises(ValueError,match=expected):image_workflow(image)
    assert service.workflow is before and not service.workflow_path.exists()


def test_prompt_studio_import_fills_prompts_parameters_without_caption_or_generation(service,tmp_path,monkeypatch):
    graph=configure_editor(service)
    graph['6']['inputs']['text']='mountains, morning light'
    graph['7']['inputs']['text']='blur, merged ridges'
    graph['3']['inputs']['seed']=1234
    graph['5']['inputs']['width']=640
    graph['4']['inputs']['ckpt_name']='custom-model.safetensors'
    image=original_image(tmp_path,graph)
    monkeypatch.setattr(gr,'Info',lambda *a,**kw:None)
    monkeypatch.setattr(gr,'Warning',lambda *a,**kw:None)
    service.enqueue_caption=lambda *a:pytest.fail('Metadata mode must not enqueue captioning')
    app=build_ui(service)
    callbacks=ui_callbacks(app)
    result=list(callbacks['import_prompt_workflow_image'](str(image),'http://127.0.0.1:8188','zh'))
    assert len(result)==2 and '正在读取' in result[0][4]
    last=result[-1]
    assert last[5:7]==('mountains, morning light','blur, merged ridges')
    assert last[7]['value']==last[14]['value']==640
    assert last[11]['value']==last[18]['value']==1234
    assert '填入正负提示词' in last[4]
    assert not service.db.rows('SELECT * FROM tasks') and not service.db.rows('SELECT * FROM api_calls')
    mode=callbacks['refresh_caption_input'](True,True)
    assert mode[0]['visible'] is False and mode[1]['visible'] is True
    assert mode[2] is False  # Works even with no standalone caption branch.
    mode=callbacks['refresh_caption_input'](False,True)
    assert mode[0]['visible'] is True and mode[1]['visible'] is False


def test_configuration_import_preserves_prompt_and_failure_preserves_all_parameters(service,tmp_path,monkeypatch):
    graph=configure_editor(service);image=original_image(tmp_path,graph)
    monkeypatch.setattr(gr,'Info',lambda *a,**kw:None);monkeypatch.setattr(gr,'Warning',lambda *a,**kw:None)
    callback=ui_callbacks(build_ui(service))['import_workflow_image']
    result=list(callback(str(image),'http://127.0.0.1:8188','zh'))[-1]
    assert 'value' not in result[5] and 'value' not in result[6]
    before=service.workflow_path.read_bytes()
    missing=tmp_path/'missing.png';Image.new('RGB',(64,64)).save(missing)
    result=list(callback(str(missing),'http://127.0.0.1:8188','zh'))[-1]
    assert '没有 ComfyUI' in result[3]
    assert all('value' not in update for update in result[5:])
    assert service.workflow_path.read_bytes()==before
    task=service.create_task(TaskSettings(goal='fixture',demo=False),start=False)
    service.db.execute("UPDATE tasks SET state='RUNNING' WHERE id=?",(task,))
    result=list(callback(str(image),'http://127.0.0.1:8188','zh'))[-1]
    assert '暂停' in result[3] or 'PAUSE_TASKS' in result[3]
    assert service.workflow_path.read_bytes()==before
