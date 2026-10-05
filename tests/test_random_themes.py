import json
from unittest.mock import AsyncMock
from conftest import ui_callbacks

import gradio as gr
import httpx
import pytest
from PIL import Image

from supervisor.models import PromptPlan, StyleCard, TaskSettings, ThemePlan
from supervisor.ui import build_ui
from test_live_readiness import configure_cloud


@pytest.mark.parametrize('provided,count,chosen,expected', [
    (['ink'],3,['library portrait','harbor scene'],['ink','library portrait','harbor scene']),
    ([],2,['library portrait','harbor scene'],['library portrait','harbor scene']),
    (['ink','photo','watercolor'],2,['watercolor','ink'],['watercolor','ink']),
    (['ink','ink','ink'],2,['harbor scene'],['ink','harbor scene']),
    ([f'theme {index}' for index in range(21)],20,[f'theme {index}' for index in range(20)],[f'theme {index}' for index in range(20)]),
])
async def test_random_themes_are_resolved_once_then_used_by_each_group(service,tmp_path,provided,count,chosen,expected):
    reference=tmp_path/'ref.png'; Image.new('RGB',(64,64),'green').save(reference)
    settings=TaskSettings(goal='learn reference appearance',autonomous=True,groups=count,target_styles=provided)
    task=service.create_task(settings,[reference])
    seen=[]
    async def respond(task_id,settings,purpose,contract,payload,images=None):
        seen.append((contract,payload))
        if contract is StyleCard:
            return StyleCard(subject=['landscape']),None
        if contract is ThemePlan:
            assert payload['count']==len(chosen)
            return ThemePlan(themes=chosen),None
        return PromptPlan(group_id=payload['group_id'],positive=payload['target_style'],reason='test'),None
    service.cloud.request=AsyncMock(side_effect=respond)
    await service.prepare(task,settings)
    assert service.settings(task).target_styles==expected
    assert [payload['target_style'] for contract,payload in seen if contract is PromptPlan]==expected[:1]
    first_count=len(seen)
    await service.prepare(task,service.settings(task))
    assert len(seen)==first_count
    assert len(service.db.rows("SELECT * FROM events WHERE kind='THEMES_RESOLVED'"))==1
    for group in service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(task,))[1:]:
        await service.plan(task,settings,group,first=True)
    assert [payload['target_style'] for contract,payload in seen if contract is PromptPlan]==expected


async def test_equal_theme_count_does_not_spend_a_theme_request(service,tmp_path):
    reference=tmp_path/'ref.png'; Image.new('RGB',(64,64)).save(reference)
    settings=TaskSettings(goal='landscape',autonomous=True,groups=2,target_styles=['ink','photo'])
    task=service.create_task(settings,[reference])
    service.cloud.request=AsyncMock(side_effect=AssertionError('No theme API call when count matches'))
    await service.resolve_themes(task,settings)
    assert service.settings(task).target_styles==['ink','photo']


@pytest.mark.parametrize('bad', [['one'],['one','one'],['ink','new'],['','new']])
async def test_wrong_or_duplicate_theme_reply_is_repaired_before_success(service,bad):
    bodies=[]
    def handler(request):
        body=json.loads(request.content); bodies.append(body)
        answer=bad if len(bodies)==1 else ['library portrait','harbor scene']
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'themes':answer})}}],
            'usage':{'prompt_tokens':20,'completion_tokens':20}})
    configure_cloud(service,httpx.MockTransport(handler))
    service.config.providers[0].retries=1
    settings=TaskSettings(goal='landscape',demo=False,reference_only=True,content_label='sfw')
    task=service.create_task(settings,start=False)
    payload={'count':2,'mode':'generate','existing_themes':['ink'],'goal':'landscape','content_scope':'sfw','random_seed':42}
    result,_=await service.cloud.request(task,settings,'prompt_generation',ThemePlan,payload)
    assert result.themes==['library portrait','harbor scene'] and len(bodies)==2
    assert [row['status'] for row in service.db.rows('SELECT status FROM api_calls')]==['INVALID_RESPONSE','SUCCESS']


async def test_select_does_not_accept_a_new_theme(service):
    configure_cloud(service,httpx.MockTransport(lambda request:httpx.Response(200,json={
        'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'themes':['new theme']})}}],
        'usage':{'prompt_tokens':1,'completion_tokens':1}})))
    service.config.providers[0].retries=0
    settings=TaskSettings(goal='landscape',demo=False,reference_only=True,content_label='sfw')
    task=service.create_task(settings,start=False)
    from supervisor.providers import CloudError
    with pytest.raises(CloudError,match='INVALID_RESPONSE_PROMPT_REQUIRED'):
        await service.cloud.request(task,settings,'prompt_generation',ThemePlan,
            {'count':1,'mode':'select','existing_themes':['ink','photo'],'random_seed':42})


@pytest.mark.parametrize('themes', ['', 'ink', 'ink\nphoto\nwatercolor'])
async def test_studio_accepts_empty_fewer_or_extra_themes(service,tmp_path,monkeypatch,themes):
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None); monkeypatch.setattr(gr,'Warning',lambda *a,**k:None)
    monkeypatch.setattr('supervisor.ui.check_live_ready',AsyncMock())
    service.enqueue=lambda task:None
    app=build_ui(service)
    start=ui_callbacks(app)['studio_create']
    image=tmp_path/'ref.png'; Image.new('RGB',(64,64)).save(image)
    result=await start([str(image)],[],'',15,'landscape',themes,'',2,'Live',str(service.project_root/'export'),512,512,20,7,42,
        'sfw',60,1,6,2,'zh','euler','normal',per_group_target=1)
    assert '模型随机补选主题' in result[2]
    assert service.settings(result[0]['value']).groups==2
    assert not service.db.rows('SELECT * FROM api_calls')
