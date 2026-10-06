import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image, PngImagePlugin

from conftest import ui_callbacks
from supervisor.image_prompt import embedded_prompts, enqueue_image_prompt
from supervisor.models import PromptPlan
from test_live_readiness import configure_cloud


def image_with_metadata(tmp_path, **fields):
    path=tmp_path/'original.png'
    info=PngImagePlugin.PngInfo()
    for key,value in fields.items():
        info.add_text(key,value)
    Image.new('RGB',(64,64),'green').save(path,pnginfo=info)
    return path


def graph(positive='mountains',negative='blur'):
    return {'1':{'class_type':'CLIPTextEncode','inputs':{'text':['6',0]}},
        '2':{'class_type':'CLIPTextEncode','inputs':{'text':negative}},
        '3':{'class_type':'KSampler','inputs':{'positive':['1',0],'negative':['2',0]}},
        '4':{'class_type':'VAEDecode','inputs':{'samples':['3',0]}},
        '5':{'class_type':'SaveImage','inputs':{'images':['4',0]}},
        '6':{'class_type':'StringConcatenate','inputs':{'string_a':positive,'string_b':'soft light','delimiter':', '}},
        '99':{'class_type':'CLIPTextEncode','inputs':{'text':'unconnected text'}}}


def test_api_metadata_uses_connected_prompts_and_resolves_strings(tmp_path):
    image=image_with_metadata(tmp_path,prompt=json.dumps(graph()))
    assert embedded_prompts(image)==('mountains, soft light','blur')


def test_multiple_distinct_output_prompts_are_not_arbitrarily_selected(tmp_path):
    data=graph()
    data.update({'7':{'class_type':'CLIPTextEncode','inputs':{'text':'sea'}},
        '8':{'class_type':'KSampler','inputs':{'positive':['7',0],'negative':['2',0]}},
        '9':{'class_type':'SaveImage','inputs':{'images':['8',0]}}})
    assert embedded_prompts(image_with_metadata(tmp_path,prompt=json.dumps(data))) is None


@pytest.mark.parametrize('fields,expected',[
    ({'parameters':'mountains, soft light\nNegative prompt: blur\nSteps: 20, Sampler: Euler, Seed: 42'},('mountains, soft light','blur')),
    ({'parameters':'mountains\nSteps: 20, Seed: 42'},('mountains','')),
    ({'prompt':'mountains','negative_prompt':'blur'},('mountains','blur')),
    ({'prompt':'{"positive":"mountains","negative":"blur"}'},('mountains','blur')),
    ({'positive':'mountains','negative':'blur'},('mountains','blur')),
    ({'prompt':'{broken graph'},None),
    ({},None),
])
def test_text_metadata_formats(tmp_path,fields,expected):
    assert embedded_prompts(image_with_metadata(tmp_path,**fields))==expected


@pytest.mark.parametrize('fmt',['JPEG','WEBP'])
def test_exif_parameters_are_read_without_model(tmp_path,fmt):
    path=tmp_path/('original.jpg' if fmt=='JPEG' else 'original.webp')
    exif=Image.Exif()
    exif[0x9286]=b'ASCII\x00\x00\x00mountains\nNegative prompt: blur\nSteps: 20, Seed: 42'
    Image.new('RGB',(64,64),'green').save(path,format=fmt,exif=exif)
    assert embedded_prompts(path)==('mountains','blur')


async def test_ui_metadata_read_is_free_and_does_not_change_workflow(service,tmp_path):
    from supervisor.ui import build_ui
    from test_switches_and_score_only import configure_switch
    configure_switch(service)
    callback=ui_callbacks(build_ui(service))['prompt_image_uploaded']
    image=image_with_metadata(tmp_path,prompt=json.dumps(graph()))
    service.cloud.request=AsyncMock(side_effect=AssertionError('No paid call'))
    before=service.workflow.model_dump_json()
    result=[item async for item in callback(str(image),'sfw',0,1000)][-1]
    assert result[:2]==('mountains, soft light','blur') and '未调用模型' in result[2]
    assert service.workflow.model_dump_json()==before
    assert not service.db.rows('SELECT id FROM tasks')


async def test_ui_missing_metadata_queues_one_vision_call_without_generation(service,tmp_path,monkeypatch):
    from supervisor.ui import build_ui
    image=image_with_metadata(tmp_path)
    bodies=[]
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{
            'content':json.dumps({'positive':'mountains, soft light','negative':'blur'})}}],
            'usage':{'prompt_tokens':10,'completion_tokens':20}})
    configure_cloud(service,httpx.MockTransport(handler))
    monkeypatch.setattr(service,'enqueue',lambda task:asyncio.create_task(service.run(task)))
    service.comfy.generate=AsyncMock(side_effect=AssertionError('No generation'))
    callback=ui_callbacks(build_ui(service))['prompt_image_uploaded']
    results=[item async for item in callback(str(image),'sfw',.1,5000)]
    assert results[-1][:2]==('mountains, soft light','blur') and '并非原始' in results[-1][2]
    assert len(bodies)==1
    user=bodies[0]['messages'][-1]['content']
    payload=json.loads(user[0]['text'])
    assert len(user)==2 and 'image_url' in user[1]
    assert 'Describe the supplied image' in payload['data']['instruction']
    assert 'fresh group' not in payload['data']['instruction']
    assert service.db.token_usage(service.db.one('SELECT id FROM tasks')['id'])==30
    assert not service.db.rows('SELECT id FROM generations')


async def test_saved_image_prompt_restores_completion_without_duplicate_call(service,tmp_path,monkeypatch):
    configure_cloud(service,httpx.MockTransport(lambda request:(_ for _ in ()).throw(AssertionError('No network'))))
    monkeypatch.setattr(service,'enqueue',lambda task:None)
    task=enqueue_image_prompt(service,image_with_metadata(tmp_path),'sfw',.1,5000)
    service.db.execute('UPDATE image_prompt_jobs SET result=? WHERE task_id=?',(json.dumps({'positive':'mountains','negative':''}),task))
    service.cloud.request=AsyncMock(side_effect=AssertionError('No second call'))
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    service.cloud.request.assert_not_called()


async def test_ui_failure_preserves_both_prompts(service,tmp_path):
    from supervisor.ui import build_ui
    callback=ui_callbacks(build_ui(service))['prompt_image_uploaded']
    result=[item async for item in callback(str(image_with_metadata(tmp_path)),'sfw',0,1000)][-1]
    assert result[0]==result[1]=={'__type__':'update'}
    assert '原提示词已保留' in result[2] and not service.db.rows('SELECT id FROM api_calls')


async def test_zero_model_budget_stops_before_network_or_generation(service,tmp_path,monkeypatch):
    configure_cloud(service,httpx.MockTransport(lambda request:(_ for _ in ()).throw(AssertionError('No charged request'))))
    monkeypatch.setattr(service,'enqueue',lambda task:None)
    task=enqueue_image_prompt(service,image_with_metadata(tmp_path),'sfw',0,5000)
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']!='COMPLETED'
    assert not service.db.rows('SELECT id FROM api_calls') and not service.db.rows('SELECT id FROM generations')
