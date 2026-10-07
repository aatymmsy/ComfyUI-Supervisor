import json
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from supervisor.db import now, uid
from supervisor.models import Evaluation, PromptPlan, StyleCard, TaskSettings
from supervisor.studio_features import result_review_html
from supervisor.wire import ReviewReply
from test_live_readiness import configure_cloud
from test_review_format import review


def response(data):
    return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':data if isinstance(data,str) else json.dumps(data)}}],
        'usage':{'prompt_tokens':10,'completion_tokens':10}})


def prepared(service, tmp_path):
    settings=TaskSettings(goal='abstract landscape',direct_prompt='abstract landscape',autonomous=True,demo=False,
        content_label='sfw',groups=1,per_group=2,max_rounds=1,prescreen=False,export_folder=str(tmp_path/'export'))
    task=service.create_task(settings)
    group=service.db.one('SELECT * FROM groups WHERE task_id=?',(task,))
    variant,generation=uid(),uid()
    plan=PromptPlan(group_id=group['id'],positive='abstract landscape',reason='test')
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',(uid(),task,StyleCard(subject=['landscape']).model_dump_json(),now()))
    service.db.execute('INSERT INTO prompt_variants VALUES(?,?,?,?,?,?,?,?)',(variant,task,group['id'],None,0,plan.model_dump_json(),'USED',now()))
    service.db.execute("INSERT INTO generations(id,task_id,group_id,variant_id,submission_token,client_id,state,graph,graph_hash,outputs,created_at,updated_at) VALUES(?,?,?,?,?,?,'EVALUATE','{}','test','[]',?,?)",
        (generation,task,group['id'],variant,uid(),uid(),now(),now()))
    assets=[]
    for index,color in enumerate(['green','blue']):
        image=tmp_path/f'{color}.png';Image.new('RGB',(64,64),color).save(image)
        asset=service.files.import_image(task,image,'sfw',group['id'],generation)
        service.db.execute('INSERT INTO generation_outputs VALUES(?,?,?,?,?,?,?)',(generation,asset['id'],'9',index,plan.params.seed,None,color))
        assets.append(asset)
    service.comfy.run=AsyncMock(side_effect=AssertionError('No regeneration'))
    service.comfy.download=AsyncMock(side_effect=AssertionError('No redownload'))
    return task,settings,generation,assets


@pytest.mark.parametrize('value,expected',[('87/100',87),('87分',87),('87%',87),('N/A',None),('无法评估',None)])
def test_explicit_score_notation_preserves_value(value,expected):
    data=review();data['scores']['style']=value
    assert ReviewReply.model_validate(data).scores.style==expected


@pytest.mark.parametrize('value',[True,False,'watercolor','good, 87 points','101/100','-3分'])
def test_style_names_and_invalid_scores_are_never_guessed(value):
    data=review();data['scores']['style']=value
    with pytest.raises(ValidationError):ReviewReply.model_validate(data)


def test_observed_annotations_keep_numeric_scores_and_evidence():
    data=review();data['problems']=[{'category':'style_drift','severity':'minor','evidence':'Flat background.',
        'category_hint':'Background detail','score_impact':-3}]
    result=ReviewReply.model_validate(data)
    assert result.scores.style==90 and result.problems[0].category=='style_drift'
    assert 'Flat background.' in result.problems[0].evidence and 'Background detail' in result.problems[0].evidence


@pytest.mark.parametrize('impact',[True,101,float('inf'),{'style':-4}])
def test_invalid_annotation_cannot_bypass_review_validation(impact):
    data=review();data['problems']=[{'category':'style_drift','severity':'minor','evidence':'Flat background.','score_impact':impact}]
    with pytest.raises(ValidationError):ReviewReply.model_validate(data)


async def test_numeric_repair_is_specific_and_keeps_budget_ledger(service):
    calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        data=review()
        if len(calls)==1:data['scores']['style']='watercolor'
        return response(data)
    configure_cloud(service,httpx.MockTransport(handler))
    settings=TaskSettings(goal='test',reference_only=True,demo=False,content_label='sfw')
    task=service.create_task(settings,start=False)
    result,_=await service.cloud.request(task,settings,'review',Evaluation,{'asset_id':'a'})
    assert result.style_match==90 and len(calls)==2
    repair=json.loads(calls[1]['messages'][-1]['content'][0]['text'])['repair']
    error=next(e for e in repair['errors'] if e['loc']==['scores','style'])
    assert '0 to 100' in error['expected'] and 'style name' in error['expected']
    assert [r['status'] for r in service.db.rows('SELECT status FROM api_calls ORDER BY rowid')]==['INVALID_RESPONSE','SUCCESS']
    assert not service.db.one("SELECT id FROM api_calls WHERE status='RESERVED'")


async def test_exhausted_format_route_uses_configured_verified_backup(service):
    calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body['model'])
        data=review()
        if body['model']=='m':data['scores']['style']='watercolor'
        return response(data)
    configure_cloud(service,httpx.MockTransport(handler))
    provider=service.config.providers[0];provider.retries=0
    provider.models.append(provider.models[0].model_copy(update={'id':'backup'}))
    service.config.routes['review']=['p/m','p/backup']
    settings=TaskSettings(goal='test',reference_only=True,demo=False,content_label='sfw')
    task=service.create_task(settings,start=False)
    result,_=await service.cloud.request(task,settings,'review',Evaluation,{'asset_id':'a'})
    assert result.style_match==90 and calls==['m','backup']


async def test_whole_json_fence_is_transport_format_only(service):
    configure_cloud(service,httpx.MockTransport(lambda r:response('```json\n'+json.dumps(review())+'\n```')))
    settings=TaskSettings(goal='test',reference_only=True,demo=False,content_label='sfw')
    task=service.create_task(settings,start=False)
    result,_=await service.cloud.request(task,settings,'review',Evaluation,{'asset_id':'a'})
    assert result.style_match==90


async def test_backup_route_cannot_overrun_existing_budget(service):
    from supervisor.db import BudgetExceeded
    calls=[]
    def handler(request):
        calls.append(request)
        data=review();data['scores']['style']='watercolor'
        return response(data)
    configure_cloud(service,httpx.MockTransport(handler))
    provider=service.config.providers[0];provider.retries=0
    provider.models.append(provider.models[0].model_copy(update={'id':'backup'}))
    service.config.routes['review']=['p/m','p/backup']
    settings=TaskSettings(goal='test',reference_only=True,demo=False,content_label='sfw',budget_micro=100)
    task=service.create_task(settings,start=False)
    with pytest.raises(BudgetExceeded):
        await service.cloud.request(task,settings,'review',Evaluation,{'asset_id':'a'})
    assert len(calls)==1 and not service.db.one("SELECT id FROM api_calls WHERE status='RESERVED'")


@pytest.mark.parametrize('all_invalid',[False,True])
async def test_bad_image_review_continues_other_outputs_and_stops_at_round_limit(service,tmp_path,all_invalid):
    calls=[]
    def handler(request):
        calls.append(request)
        data=review()
        if all_invalid or len(calls)==1:data['scores']['style']='watercolor'
        return response(data)
    configure_cloud(service,httpx.MockTransport(handler));service.config.providers[0].retries=0
    task,settings,generation,assets=prepared(service,tmp_path)
    await service.run(task)
    assert len(calls)==2
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='PARTIAL'
    assert service.db.one('SELECT state FROM generations WHERE id=?',(generation,))['state']=='DECIDED'
    assert len(service.db.accepted(task))==(0 if all_invalid else 1)
    first=service.db.one('SELECT * FROM decisions WHERE asset_id=? ORDER BY rowid DESC',(assets[0]['id'],))
    assert first['action']=='REVIEW_FAILED' and first['evaluation_id'] is None
    assert '无有效评分' in result_review_html(service,task)
    assert '待人工' not in result_review_html(service,task)
    assert service.files.path(assets[0]['path']).exists()


async def test_force_delete_during_review_prevents_resurrection_and_cleans_at_safe_point(service,tmp_path):
    task,settings,generation,assets=prepared(service,tmp_path)
    calls=[]
    async def handler(request):
        calls.append(request)
        if len(calls)==1:
            assert service.delete_generated(task,assets[0]['id'])=='queued'
            assert service.files.path(assets[0]['path']).exists()
            assert assets[0]['id'] not in result_review_html(service,task)
        return response(review())
    configure_cloud(service,httpx.MockTransport(handler));service.config.providers[0].retries=0
    await service.run(task)
    assert len(calls)==2 and len(service.db.accepted(task))==1
    assert service.db.one('SELECT state FROM assets WHERE id=?',(assets[0]['id'],))['state']=='DELETED'
    assert not service.files.path(assets[0]['path']).exists()
    assert not service.db.one('SELECT id FROM evaluations WHERE asset_id=?',(assets[0]['id'],))
    assert service.db.one('SELECT action FROM decisions WHERE asset_id=?',(assets[0]['id'],))['action']=='USER_DELETED'
    service.db.execute("UPDATE generations SET state='EVALUATE' WHERE id=?",(generation,))
    await service.process_generation(task,settings,service.db.one('SELECT * FROM generations WHERE id=?',(generation,)))
    assert len(calls)==2


async def test_bad_thumbnail_format_still_reviews_original_before_rejecting(service,tmp_path):
    task,settings,generation,assets=prepared(service,tmp_path)
    settings.prescreen=True
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?',(settings.model_dump_json(),task))
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        data=review()
        if len(calls)==1:data['scores']['style']='watercolor'
        return response(data)
    configure_cloud(service,httpx.MockTransport(handler));service.config.providers[0].retries=0
    await service.run(task)
    assert len(calls)==4 and len(service.db.accepted(task))==2
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    assert service.db.one("SELECT COUNT(*) n FROM events WHERE kind='PRESCREEN_REVIEW_UNAVAILABLE'")['n']==1
    assert not service.db.one("SELECT id FROM decisions WHERE action='REVIEW_FAILED'")


async def test_failed_confirmation_does_not_display_initial_score_as_valid(service,tmp_path):
    task,settings,generation,assets=prepared(service,tmp_path)
    initial=Evaluation.model_validate({'asset_id':assets[0]['id'],'stage':'final','overall':90,'prompt_alignment':90,
        'aesthetics':90,'composition':90,'anatomy':90,'artifacts':90,'style_match':90,'nsfw_target':None,
        'decision':'keep','unassessable_fields':['nsfw_target'],'delete_reason':[],'prompt_suggestions':[],'issues':[]})
    service.save_evaluation(assets[0],initial,None,'final')
    service.decision(assets[0]['id'],None,'REVIEW_FAILED','INVALID_RESPONSE_REVIEW_REQUIRED')
    html=result_review_html(service,task)
    card=html.split('data-asset-id="'+assets[0]['id']+'"',1)[1].split('</details>',1)[0]
    assert '无有效评分' in card and '综合分 90' not in card and 'review-score-grid' not in card
