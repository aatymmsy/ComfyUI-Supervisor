import json
import hashlib

import httpx
import pytest
from PIL import Image

from supervisor.models import Evaluation,PromptPlan,StyleCard,TaskSettings,dump_json
from supervisor.providers import CloudError
from supervisor.wire import PromptReply,ReviewReply,expand_reply
from test_live_readiness import configure_cloud


def test_small_replies_fill_local_ids_and_all_scoring_metrics():
    prompt=expand_reply(PromptPlan,PromptReply.model_validate({'prompt':'mountains'}),{'group_id':'local','params':{'seed':42}})
    assert prompt.group_id=='local' and prompt.params.seed==42 and prompt.negative==''
    brief=ReviewReply.model_validate({'scores':{'alignment':90,'aesthetics':90,'composition':90,'anatomy':None,
        'structure':88,'hands':None,'text':None,'artifacts':90,'style':90,'safety':95,'nsfw':None},
        'decision':'keep','confidence':.95,'problems':[],'advice':''})
    score=expand_reply(Evaluation,brief,{'asset_id':'local','stage':'final'})
    assert score.asset_id=='local' and score.stage=='final'
    assert score.hands is None and 'hands' in score.unassessable_fields
    assert score.structure==88 and score.prompt_alignment==90 and score.safety_score==95
    assert score.effective_score()>85


async def test_deepseek_disables_thinking_and_uses_small_json_shape(service):
    bodies=[]
    def handler(request):
        body=json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'camera':['wide shot'],'pose':[],'composition':'layers','lighting':'daylight','detail':[]})}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'completion_tokens_details':{'reasoning_tokens':0}}})
    configure_cloud(service,httpx.MockTransport(handler))
    provider=service.config.providers[0]
    provider.base_url='https://api.deepseek.com'
    provider.models[0].id='deepseek-flash'
    provider.models[0].json_schema=False
    service.config.routes['tagging']=['p/deepseek-flash']
    settings=TaskSettings(goal='landscape',demo=False,reference_only=True,content_label='sfw')
    task=service.create_task(settings)
    tags,_=await service.cloud.request(task,settings,'tagging',StyleCard,{'goal':'do not resend goal','original_prompt':{}})
    body=bodies[0]
    assert body['thinking']=={'type':'disabled'} and body['max_tokens']==384
    text=json.loads(body['messages'][-1]['content'][0]['text'])
    assert 'output' in text and 'schema' not in text and 'goal' not in text['data']
    assert tags.camera==['wide shot'] and tags.subject==[]
    usage=json.loads(service.db.one('SELECT usage FROM api_calls')['usage'])
    assert usage['reasoning_tokens']==0


async def test_token_budget_stops_before_next_api_request(service):
    configure_cloud(service,httpx.MockTransport(lambda request:pytest.fail('Budget must stop network call')))
    settings=TaskSettings(goal='test',demo=False,content_label='sfw',token_budget=1000)
    task=service.create_task(settings)
    call=service.db.reserve(task,'tagging','p','m','k',{},100,settings.budget_micro)
    service.db.settle(call,'SUCCESS',cost=1,usage={'input_tokens':900,'output_tokens':100})
    with pytest.raises(CloudError,match='TOKEN_BUDGET_LIMIT'):
        await service.cloud.request(task,settings,'tagging',StyleCard,{})
    assert service.limit_reason(task,settings)=='TOKEN_BUDGET_LIMIT'


async def test_legacy_tag_cache_survives_goal_and_examples_changes(service,tmp_path):
    calls=[]
    def handler(request):
        calls.append(1)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':StyleCard(subject=['mountains']).model_dump_json()}}],'usage':{'prompt_tokens':1,'completion_tokens':1}})
    configure_cloud(service,httpx.MockTransport(handler))
    image=tmp_path/'reference.png'
    Image.new('RGB',(64,64),'green').save(image)
    settings=TaskSettings(goal='old goal',demo=False,reference_only=True,content_label='sfw')
    old=service.create_task(settings,[image],start=False)
    asset=service.db.one('SELECT * FROM assets WHERE task_id=?',(old,))
    await service.tag_reference(old,settings,asset)
    legacy={'goal':settings.goal,'examples':settings.prompt_examples,'scope':settings.content_label,'routes':service.config.routes['tagging'],
        'providers':[{'base':p.base_url,'models':[m.id for m in p.models]} for p in service.config.providers]}
    metadata=json.loads(service.db.one('SELECT metadata FROM assets WHERE id=?',(asset['id'],))['metadata'])
    metadata['tag_context']=hashlib.sha256(dump_json(legacy).encode()).hexdigest()
    service.db.execute('UPDATE assets SET metadata=? WHERE id=?',(dump_json(metadata),asset['id']))
    revised=settings.model_copy(update={'goal':'new goal','prompt_examples':'new examples'})
    new=service.create_task(revised,[image],start=False)
    await service.tag_reference(new,revised,service.db.one('SELECT * FROM assets WHERE task_id=?',(new,)))
    assert len(calls)==1


def test_manual_uploads_are_also_sampled_to_fifteen(tmp_path):
    from supervisor.files import studio_reference_paths
    files=[tmp_path/f'{index}.png' for index in range(40)]
    selected,count=studio_reference_paths(files,[],None,42)
    assert len(selected)==15 and count==40
