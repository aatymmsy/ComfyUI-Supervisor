import json
from PIL import Image
from supervisor.context import creative_brief, generation_reference_context, prompt_techniques, reference_study
from supervisor.models import StyleCard, TaskSettings, PromptPlan
from supervisor.db import uid, now
from supervisor.wire import ReferenceReply, expand_reply, request_payload


def test_weight_syntax_and_detail_methods_learned_without_example_content():
    example = '1girl, white hair, 1.5::a huge white lily fills the left foreground::, (glass bottle:1.2), lace textures, holding bottle'
    methods = prompt_techniques(example)
    assert any('weight::phrase::' in value for value in methods)
    assert any('(term:weight)' in value for value in methods)
    assert len(methods)==6
    assert all(word not in json.dumps(methods) for word in ('white hair','lily','bottle','1girl'))


def test_reference_reply_does_not_request_identity():
    assert set(ReferenceReply.model_fields)=={'camera','pose','composition','lighting','detail'}
    card=expand_reply(StyleCard,ReferenceReply(camera=['close-up'],pose=['standing'],composition='diagonal',lighting='soft',detail=['glass reflections']),{})
    assert card.camera==['close-up'] and card.pose==['standing'] and not card.subject


def test_old_reference_anchors_become_abstract_methods():
    card=StyleCard(subject=['Alice, standing'],camera=['extreme close-up'],pose=['holding a lily'],composition=['face in upper right'],
        lighting=['blue moonlight'],detail=['white hair','blue eyes','lily bottle condensation'],writing_style=['Alice, blue hair'])
    result=generation_reference_context(card.model_dump())
    assert result['writing_methods']
    assert all(word not in json.dumps(result) for word in ('Alice','lily','blue','close-up','holding','upper right','white hair'))
    assert not reference_study(card).subject


def test_twenty_local_creative_directions_are_unique_and_stable():
    first=[creative_brief('task-one',ordinal) for ordinal in range(20)]
    assert first==[creative_brief('task-one',ordinal) for ordinal in range(20)]
    assert len({(value['shot'],value['composition'],value['action']) for value in first})==20
    assert len({value['shot'] for value in first[:8]})==8
    assert first!=[creative_brief('task-two',ordinal) for ordinal in range(20)]


async def test_cached_reference_learns_metadata_weight_writing_locally(service,tmp_path):
    image=tmp_path/'ref.png';Image.new('RGB',(32,32)).save(image)
    settings=TaskSettings(goal='new story',autonomous=True,groups=1,target_styles=['new story'])
    task=service.create_task(settings,[image],start=False)
    asset=service.db.one('SELECT * FROM assets WHERE task_id=?',(task,))
    metadata=json.loads(asset['metadata'])
    metadata.update(model_tags=StyleCard(subject=['ALICE'],camera=['close-up']).model_dump(),
        extracted={'positive':{'value':'ALICE, 1.2::a lily in the foreground::, glass reflections'}})
    service.db.execute('UPDATE assets SET metadata=? WHERE id=?',(json.dumps(metadata),asset['id']))
    async def reject(*args,**kwargs):
        raise AssertionError('A cached reference must not add a paid tagging request')
    service.cloud.request=reject
    tagged=await service.tag_reference(task,settings,service.db.one('SELECT * FROM assets WHERE id=?',(asset['id'],)))
    assert not tagged.subject
    assert any('weight::phrase::' in item for item in tagged.writing_style)
    assert 'ALICE' not in json.dumps(generation_reference_context(tagged.model_dump()))


async def test_group_planning_does_not_share_anchors_or_other_group_context(service,tmp_path):
    settings=TaskSettings(goal='invent creative scenes',autonomous=True,groups=8,target_styles=[f'theme-{i}' for i in range(8)],prompt_examples='1.2::ALICE and a lily foreground::, glass textures')
    image=tmp_path/'reference.png';Image.new('RGB',(32,32)).save(image)
    task=service.create_task(settings,[image],start=False)
    card=StyleCard(subject=['ALICE'],camera=['extreme close-up'],pose=['holding lily'],detail=['blue eyes','glass reflections'])
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',(uid(),task,card.model_dump_json(),now()))
    requests=[]
    async def cloud(task_id,settings,purpose,contract,payload):
        requests.append(request_payload(contract,payload))
        return PromptPlan(group_id=payload['group_id'],positive=f"INDEPENDENT_{len(requests)}",reason='mock'),None
    service.cloud.request=cloud
    groups=service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(task,))
    for group in groups:
        await service.plan(task,settings,group,first=True)
    assert len(requests)==8
    assert len({item['creative_brief']['shot'] for item in requests})==8
    assert all(not item.get('current') for item in requests)
    assert all(word not in json.dumps(requests) for word in ('ALICE','lily','blue eyes','INDEPENDENT_'))
    assert 'extreme close-up' not in json.dumps([item['style_card'] for item in requests])
    await service.plan(task,settings,groups[0])
    assert requests[-1]['current']['positive']=='INDEPENDENT_1'
    assert 'INDEPENDENT_2' not in json.dumps(requests[-1])
