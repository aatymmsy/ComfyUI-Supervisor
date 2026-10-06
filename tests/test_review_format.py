import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from PIL import Image

from supervisor.models import Evaluation, PromptPlan, StyleCard, TaskSettings
from supervisor.db import uid, now
from supervisor.wire import ReviewReply, expand_reply
from supervisor.providers import strict_schema
from supervisor.studio import task_progress
from test_live_readiness import configure_cloud


def review():
    return {'scores':{'alignment':90,'aesthetics':90,'composition':90,'anatomy':90,'structure':90,
        'hands':None,'text':None,'artifacts':90,'style':90,'safety':95,'nsfw':None},
        'decision':'keep','confidence':.95,'problems':[],'advice':''}


async def test_observed_dotted_check_evidence_and_empty_single_problem_need_no_retry(service):
    from test_visual_checks import checks
    data=review()
    data['checks']=checks().model_dump()
    data['checks'].update(disconnected_parts=True,evidence='')
    data['checks.evidence']='Left shoulder visibly ends without connection to the raised upper arm.'
    data['problems']=[dict(category='anatomy_error',severity='major',evidence='')]
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':20}})
    configure_cloud(service,httpx.MockTransport(handler))
    settings=TaskSettings(goal='portrait',demo=False,reference_only=True,autonomous=True,direct_prompt='portrait',content_label='sfw')
    task=service.create_task(settings,start=False)
    result,_=await service.cloud.request(task,settings,'review',Evaluation,{'asset_id':'a'})
    assert len(calls)==1 and result.issues[0].evidence==data['checks.evidence']
    assert result.visual_checks.disconnected_parts is True and result.anatomy==90
    assert service.evaluate_decision(result,settings)==('QUARANTINE','BASIC_STRUCTURE_FAILED')
    assert 'never output a literal dotted key' in calls[0]['messages'][0]['content']


@pytest.mark.parametrize('fault',['conflict','nontext','oversized','unrelated','multiple','no_defect','empty','unknown_dotted'])
def test_dotted_evidence_does_not_hide_conflicts_or_invent_observations(fault):
    from test_visual_checks import checks
    data=review();data['checks']=checks().model_dump()
    data['checks'].update(disconnected_parts=True,evidence='')
    data['checks.evidence']='Left shoulder is disconnected.'
    data['problems']=[dict(category='anatomy_error',severity='major',evidence='')]
    if fault=='conflict':data['checks']['evidence']='Different finding.'
    elif fault=='nontext':data['checks.evidence']=90
    elif fault=='oversized':data['checks.evidence']='a'*501
    elif fault=='unrelated':data['problems'][0]['category']='style_drift'
    elif fault=='multiple':data['problems'].append(dict(category='artifacts',severity='minor',evidence='Visible noise.'))
    elif fault=='no_defect':data['checks']['disconnected_parts']=False
    elif fault=='empty':data['checks.evidence']=''
    elif fault=='unknown_dotted':data['checks.anatomy']=100
    with pytest.raises(ValidationError):ReviewReply.model_validate(data)


def test_observed_evidence2_is_bounded_explanation_not_score():
    data=review()
    data['problems']=[dict(category='artifacts',severity='minor',evidence='Visible noise.',evidence2='Upper left background.')]
    assert 'Upper left' in ReviewReply.model_validate(data).problems[0].evidence
    data['problems'][0]['evidence2']=100
    with pytest.raises(ValidationError):ReviewReply.model_validate(data)


def test_flat_scores_equal_nested_scores_without_fabrication():
    nested = review()
    flat = {**nested.pop('scores'), **nested}
    expected = expand_reply(Evaluation, ReviewReply.model_validate(review()), {'asset_id':'a','stage':'final'})
    actual = expand_reply(Evaluation, ReviewReply.model_validate(flat), {'asset_id':'a','stage':'final'})
    assert actual == expected


@pytest.mark.parametrize('fault', ['missing','out_of_range','unknown','conflicting'])
def test_format_compatibility_preserves_strict_quality_validation(fault):
    data = review()
    flat = {**data.pop('scores'), **data}
    if fault == 'missing':
        del flat['safety']
    elif fault == 'out_of_range':
        flat['alignment'] = 120
    elif fault == 'unknown':
        flat['unverified_score'] = 100
    else:
        flat['scores'] = {'alignment':10}
    with pytest.raises(ValidationError):
        ReviewReply.model_validate(flat)


async def test_flat_review_uses_one_request_and_keeps_all_metrics(service):
    bodies=[]
    data=review()
    flat={**data.pop('scores'), **data}
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(flat)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':100}})
    configure_cloud(service,httpx.MockTransport(handler))
    settings=TaskSettings(goal='portrait',demo=False,reference_only=True,content_label='sfw')
    task=service.create_task(settings,start=False)
    result,_=await service.cloud.request(task,settings,'review',Evaluation,{'asset_id':'a','stage':'final'})
    assert len(bodies)==1 and result.safety_score==95 and result.anatomy==90
    assert result.hands is None and result.text_quality is None
    assert service.db.one('SELECT status FROM api_calls')['status']=='SUCCESS'


async def test_observed_review_summary_translation_and_suggestions_need_no_retry(service):
    bodies=[]
    data=review()
    data.update(problems=[dict(category='anatomy_error',severity='major',evidence='Visible missing connection.',
        evidence_summary='Left figure, shoulder joint.',evidence_zh='左侧人物的肩部连接缺失。',evidence_lang='zh')],
        advice='Clarify the shoulder connection.',advice_zh='明确肩部连接。',advice_lang='zh',
        suggestions=[dict(field='detail',suggestion='Keep each arm attached to its own torso.',expected_effect='Clear limb ownership.')],
        checks=dict(extra_limbs=False,missing_parts=True,fused_bodies=False,disconnected_parts=False,
            duplicated_body=False,content_violation=False,evidence='Visible shoulder has no arm connection.',
            evidence_zh='可见肩部没有手臂连接。'))
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':20}})
    configure_cloud(service,httpx.MockTransport(handler))
    settings=TaskSettings(goal='two clothed people',direct_prompt='two clothed people',autonomous=True,demo=False,content_label='sfw')
    task=service.create_task(settings,start=False)
    result,_=await service.cloud.request(task,settings,'review',Evaluation,{'asset_id':'a','stage':'final'})
    assert len(bodies)==1 and result.anatomy==90 and result.safety_score==95
    assert 'Left figure' in result.issues[0].evidence and '肩部连接缺失' in result.issues[0].evidence
    assert result.visual_checks.missing_parts is True and '手臂连接' in result.visual_checks.evidence
    assert '明确肩部' in result.prompt_suggestions[0].suggestion
    assert 'Keep each arm' in result.prompt_suggestions[0].suggestion
    assert service.evaluate_decision(result,settings)==('QUARANTINE','BASIC_STRUCTURE_FAILED')
    role=bodies[0]['messages'][0]['content']
    assert 'evidence and advice in their existing output fields in Simplified Chinese' in role
    assert 'Do not add translation copies' in role
    assert service.db.token_usage(task)==30


@pytest.mark.parametrize('fault',['missing_score','changed_score','invalid_check','empty_evidence',
    'oversized_evidence','numeric_annotation','unknown_problem_field','invalid_suggestion','oversized_advice'])
def test_explanatory_compatibility_never_repairs_or_overwrites_quality_fields(fault):
    data=review()
    data['problems']=[dict(category='anatomy_error',severity='major',evidence='Visible extra arm.',evidence_summary='Left figure.')]
    data['suggestions']=['Use clear arm ownership.']
    if fault=='missing_score':del data['scores']['safety']
    elif fault=='changed_score':data['scores']['anatomy']=120
    elif fault=='invalid_check':
        data['checks']=dict(extra_limbs='unclear',missing_parts=False,fused_bodies=False,disconnected_parts=False,
            duplicated_body=False,content_violation=False,evidence='No visible defect.',evidence_summary='Checked joints.')
    elif fault=='empty_evidence':data['problems'][0]['evidence']=''
    elif fault=='oversized_evidence':data['problems'][0]['evidence']='a'*501
    elif fault=='numeric_annotation':data['problems'][0]['evidence_score']=100
    elif fault=='unknown_problem_field':data['problems'][0]['missing_parts']=False
    elif fault=='invalid_suggestion':data['suggestions']=[dict(field='detail',suggestion='Clear arms.',expected_effect='',anatomy=100)]
    elif fault=='oversized_advice':data.update(advice='a'*201,advice_zh='修正手部。')
    with pytest.raises(ValidationError):ReviewReply.model_validate(data)


@pytest.mark.parametrize('fault,label',[
    ('extra','回复含不支持的字段：problems.0.unrecognized'),
    ('empty','必需说明为空：problems.0.evidence'),
    ('json','回复不是完整、可解析的 JSON'),
])
async def test_format_stop_names_the_actual_problem_without_network_guidance(service,fault,label):
    data=review()
    data['problems']=[dict(category='anatomy_error',severity='minor',evidence='private explanation')]
    if fault=='extra':data['problems'][0]['unrecognized']='private annotation'
    elif fault=='empty':data['problems'][0]['evidence']=''
    reply='{broken' if fault=='json' else json.dumps(data)
    configure_cloud(service,httpx.MockTransport(lambda request:httpx.Response(200,json={
        'choices':[{'finish_reason':'stop','message':{'content':reply}}],
        'usage':{'prompt_tokens':1,'completion_tokens':1}})))
    service.config.providers[0].retries=0
    task=service.create_task(TaskSettings(goal='test',demo=False,reference_only=True,content_label='sfw'),start=False)
    from supervisor.providers import CloudError
    with pytest.raises(CloudError,match='INVALID_RESPONSE_REVIEW_REQUIRED'):
        await service.cloud.request(task,service.settings(task),'review',Evaluation,{'asset_id':'a'})
    service.db.transition(task,'PAUSED','RECOVERABLE_ERROR','INVALID_RESPONSE_REVIEW_REQUIRED')
    visible=task_progress(service,task)
    assert label in visible and '云端回复格式未通过校验' in visible
    assert '检查连接与模型' not in visible and 'private' not in visible


@pytest.mark.parametrize('flat', [False, True])
async def test_missing_supplemental_structure_is_unknown_without_retry_or_fabrication(service, flat):
    calls = []
    data = review()
    del data['scores']['structure']
    if flat:
        data = {**data.pop('scores'), **data}
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':100}})
    configure_cloud(service, httpx.MockTransport(handler))
    service.config.providers[0].models[0].json_schema = False
    settings = TaskSettings(goal='portrait', demo=False, reference_only=True, content_label='sfw')
    task = service.create_task(settings, start=False)
    result, call = await service.cloud.request(task, settings, 'review', Evaluation, {'asset_id':'a','stage':'final'})
    expected = expand_reply(Evaluation, ReviewReply.model_validate(review()), {'asset_id':'a','stage':'final'})
    assert len(calls) == 1 and result.structure is None
    assert 'structure' in result.unassessable_fields
    for field in ('anatomy','safety_score','prompt_alignment','aesthetics','composition','artifacts','style_match'):
        assert getattr(result, field) == getattr(expected, field)
    assert result.overall == (90*6+95)/7
    event = json.loads(service.db.one("SELECT body FROM events WHERE kind='CLOUD_REPLY_OPTIONAL_SCORE_MISSING'")['body'])
    assert event == {'call_id':call,'purpose':'review','fields':['structure'],'value':None}
    request_data = json.loads(calls[0]['messages'][-1]['content'][0]['text'])
    assert 'structure' in request_data['output']['scores']
    assert 'including structure' in request_data['data']['instruction']
    assert service.db.token_usage(task) == 200


@pytest.mark.parametrize('field', ['alignment','aesthetics','composition','anatomy','artifacts','style','safety','nsfw'])
def test_structure_compatibility_does_not_hide_missing_core_scores(field):
    data = review()
    del data['scores']['structure']
    del data['scores'][field]
    with pytest.raises(ValidationError):
        ReviewReply.model_validate(data)


def test_cloud_schema_still_requests_a_structure_score_explicitly():
    schema = strict_schema(ReviewReply)['$defs']['Scores']
    assert 'structure' in schema['required']
    assert 'default' not in schema['properties']['structure']


async def test_nested_problem_advice_is_preserved_without_retry_or_weaker_review(service):
    data = review()
    data['advice'] = 'A' * 200
    data['output_language'] = 'zh'
    data['problems'] = [
        dict(category='anatomy_error', severity='major', evidence='Visible disconnected arm.',
            severity_reason='The visible joint is disconnected.', advice='Clarify the shoulder connection.'),
        dict(category='anatomy_error', severity='major', evidence='Visible duplicate leg.',
            advice='Keep two legs attached to one torso.'),
    ]
    data['checks'] = dict(extra_limbs=' true ', missing_parts='False', fused_bodies='null',
        disconnected_parts='TRUE', duplicated_body=False, content_violation=False,
        evidence='Left person has a disconnected arm and a duplicate leg.')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop',
            'message':{'content':json.dumps(data)}}], 'usage':{'prompt_tokens':10,'completion_tokens':20}})
    configure_cloud(service, httpx.MockTransport(handler))
    settings = TaskSettings(goal='two clothed people', demo=False, autonomous=True, direct_prompt='two clothed people', content_label='sfw')
    task = service.create_task(settings, start=False)
    result, _ = await service.cloud.request(task, settings, 'review', Evaluation, {'asset_id':'a'})
    assert len(calls) == 1 and result.anatomy == 90 and result.safety_score == 95
    assert [issue.severity for issue in result.issues] == ['major','major']
    assert 'visible joint' in result.issues[0].evidence
    advice = result.prompt_suggestions[0].suggestion
    assert advice.startswith('A' * 200) and 'shoulder connection' in advice and 'two legs' in advice
    assert len(advice) > 200
    assert result.visual_checks.extra_limbs is True and result.visual_checks.disconnected_parts is True
    assert result.visual_checks.missing_parts is False and result.visual_checks.fused_bodies is None
    assert service.evaluate_decision(result, settings) == ('QUARANTINE','BASIC_STRUCTURE_FAILED')
    schema = strict_schema(ReviewReply)
    assert 'advice' not in schema['$defs']['Problem']['properties']
    assert '_supplemental_advice' not in schema['properties']


@pytest.mark.parametrize('value', [None, ''])
def test_empty_optional_nested_advice_keeps_canonical_advice_required(value):
    data = review()
    data['problems'] = [dict(category='style_drift',severity='minor',evidence='Simple background.',advice=value)]
    result = ReviewReply.model_validate(data)
    assert result._supplemental_advice == []
    assert '_supplemental_advice' not in result.model_dump()
    del data['advice']
    with pytest.raises(ValidationError):
        ReviewReply.model_validate(data)


@pytest.mark.parametrize('value', [42, {}, ['edit'], 'x'*201])
def test_invalid_nested_advice_is_rejected(value):
    data = review()
    data['problems'] = [dict(category='style_drift',severity='minor',evidence='Simple background.',advice=value)]
    with pytest.raises(ValidationError):
        ReviewReply.model_validate(data)


@pytest.mark.parametrize('value', ['clear', 'false because hidden', 0, 1, {}])
def test_wire_check_conversion_does_not_guess_defects(value):
    data = review()
    data['checks'] = dict(extra_limbs=value,missing_parts=False,fused_bodies=False,
        disconnected_parts=False,duplicated_body=False,content_violation=False,evidence='Checked visible limbs.')
    with pytest.raises(ValidationError):
        ReviewReply.model_validate(data)


def test_quoted_true_still_requires_defect_evidence():
    data = review()
    data['checks'] = dict(extra_limbs='true',missing_parts=False,fused_bodies=False,
        disconnected_parts=False,duplicated_body=False,content_violation=False,evidence='')
    with pytest.raises(ValidationError):
        ReviewReply.model_validate(data)


@pytest.mark.parametrize('value', [42, {}, 'x'*41])
def test_output_language_annotation_has_no_scoring_authority(value):
    data = review()
    data['output_language'] = value
    with pytest.raises(ValidationError):
        ReviewReply.model_validate(data)


async def test_missing_required_score_is_named_in_visible_failure(service):
    data = review()
    del data['scores']['safety']
    configure_cloud(service, httpx.MockTransport(lambda request: httpx.Response(200, json={
        'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}],
        'usage':{'prompt_tokens':1,'completion_tokens':1}})))
    service.config.providers[0].retries = 0
    settings = TaskSettings(goal='test', demo=False, reference_only=True, content_label='sfw')
    task = service.create_task(settings, start=False)
    from supervisor.providers import CloudError
    with pytest.raises(CloudError, match='INVALID_RESPONSE_REVIEW_REQUIRED'):
        await service.cloud.request(task, settings, 'review', Evaluation, {})
    service.db.transition(task, 'PAUSED', 'RECOVERABLE_ERROR', 'INVALID_RESPONSE_REVIEW_REQUIRED')
    assert '缺少字段：scores.safety' in task_progress(service, task)
    call = service.db.reserve(task, 'review', 'p', 'm', 'k', {}, 100, settings.budget_micro)
    service.db.settle(call, 'INVALID_RESPONSE', cost=1)
    assert '缺少字段' not in task_progress(service, task)


async def test_invalid_review_logs_fields_without_leaking_reply_contents(service):
    private='private reply text'
    def handler(request):
        data=review(); data['scores']['safety']=private
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}],
            'usage':{'prompt_tokens':1,'completion_tokens':1}})
    configure_cloud(service,httpx.MockTransport(handler))
    service.config.providers[0].retries=0
    settings=TaskSettings(goal='test',demo=False,reference_only=True,content_label='sfw')
    task=service.create_task(settings,start=False)
    from supervisor.providers import CloudError
    with pytest.raises(CloudError,match='INVALID_RESPONSE_REVIEW_REQUIRED'):
        await service.cloud.request(task,settings,'review',Evaluation,{'asset_id':'a','stage':'final'})
    events=json.dumps(service.db.rows("SELECT body FROM events WHERE kind='CLOUD_REPLY_VALIDATION_FAILED'"))
    assert 'safety' in events and private not in events and 'float_parsing' in events


async def test_resume_reviews_existing_image_without_retagging_or_regeneration(service, tmp_path):
    image=tmp_path/'already-generated.png'
    Image.new('RGB',(64,64),'green').save(image)
    settings=TaskSettings(goal='woman portrait',demo=False,autonomous=True,content_label='sfw',groups=1,per_group=1,
        prescreen=False,target_styles=['woman reading'],export_folder=str(tmp_path/'export'))
    calls=[]
    valid=False
    def handler(request):
        body=json.loads(request.content)
        data=json.loads(body['messages'][-1]['content'][0]['text'])
        calls.append(data['purpose'])
        result=review()
        if valid:
            del result['scores']['structure']
            result['problems']=[dict(category='style_drift',severity='minor',evidence='Simple background.',
                evidence_summary='Visible background only.',evidence_zh='背景略简单。',advice='Add a background prop.')]
            result['suggestions']=['Add a small background detail.']
            result={**result.pop('scores'),**result}
        else:
            result['scores']['safety']=120
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':10}})
    configure_cloud(service,httpx.MockTransport(handler))
    service.config.providers[0].retries=0
    task=service.create_task(settings,[image])
    group=service.db.one('SELECT * FROM groups WHERE task_id=?',(task,))
    asset=service.db.one('SELECT * FROM assets WHERE task_id=?',(task,))
    variant,generation=uid(),uid()
    plan=PromptPlan(group_id=group['id'],positive='woman reading, open eyes',reason='test')
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',(uid(),task,StyleCard(subject=['woman']).model_dump_json(),now()))
    service.db.execute('INSERT INTO prompt_variants VALUES(?,?,?,?,?,?,?,?)',(variant,task,group['id'],None,0,plan.model_dump_json(),'USED',now()))
    service.db.execute("INSERT INTO generations(id,task_id,group_id,variant_id,submission_token,client_id,state,graph,graph_hash,outputs,created_at,updated_at) VALUES(?,?,?,?,?,?,'EVALUATE','{}','test','[]',?,?)",
        (generation,task,group['id'],variant,uid(),uid(),now(),now()))
    service.db.execute("UPDATE assets SET source_kind='generated',group_id=?,generation_id=? WHERE id=?",(group['id'],generation,asset['id']))
    service.db.execute('INSERT INTO generation_outputs VALUES(?,?,?,?,?,?,?)',(generation,asset['id'],'9',0,plan.params.seed,None,'already-generated'))
    service.comfy.run=AsyncMock(side_effect=AssertionError('Never regenerate an existing image'))
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='PAUSED'
    assert service.db.one('SELECT state FROM generations WHERE id=?',(generation,))['state']=='EVALUATE'
    valid=True
    service.resume(task)
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    assert calls==['review','review']
    evaluation = Evaluation.model_validate_json(service.db.one('SELECT body FROM evaluations')['body'])
    assert evaluation.structure is None and 'structure' in evaluation.unassessable_fields
    assert 'Add a background prop.' in evaluation.prompt_suggestions[0].suggestion
    assert service.db.one('SELECT COUNT(*) n FROM generations WHERE task_id=?',(task,))['n']==1
    assert len(list((tmp_path/'export'/('task_'+task)).glob('group_*/*.png')))==1


@pytest.mark.parametrize('overall',[0,99,None])
def test_supplemental_overall_does_not_override_individual_scores(overall):
    data=review();data['overall']=overall
    actual=expand_reply(Evaluation,ReviewReply.model_validate(data),{'asset_id':'a'})
    expected=expand_reply(Evaluation,ReviewReply.model_validate(review()),{'asset_id':'a'})
    assert actual==expected
    assert 'overall' not in strict_schema(ReviewReply)['properties']


@pytest.mark.parametrize('overall',[101,-1,True,'99',{},float('nan')])
def test_invalid_supplemental_overall_is_rejected(overall):
    data=review();data['overall']=overall
    with pytest.raises(ValidationError) as error:
        ReviewReply.model_validate(data)
    assert error.value.errors()[0]['type']=='review_overall'


@pytest.mark.parametrize('flag',[False,None])
def test_missing_checks_evidence_is_empty_only_when_no_defect_is_reported(flag):
    from test_visual_checks import checks
    data=review();data['checks']=checks().model_dump();del data['checks']['evidence']
    data['checks']['missing_parts']=flag
    assert ReviewReply.model_validate(data).checks.evidence==''


@pytest.mark.parametrize('source',['problem','annotation','absent','wrong_category'])
def test_missing_defect_evidence_reuses_only_supplied_matching_evidence(source):
    from test_visual_checks import checks
    data=review();data['checks']=checks().model_dump();del data['checks']['evidence']
    data['checks']['missing_parts']=True
    if source in ('problem','wrong_category'):
        data['problems']=[dict(category='anatomy_error' if source=='problem' else 'style_drift',
            severity='major',evidence='Left shoulder has a visible disconnected arm.')]
    if source=='annotation':data['checks']['evidence_summary']='Left shoulder has a visible disconnected arm.'
    if source in ('problem','annotation'):
        assert 'Left shoulder' in ReviewReply.model_validate(data).checks.evidence
    else:
        with pytest.raises(ValidationError):ReviewReply.model_validate(data)


async def test_root_validation_errors_have_safe_repair_reason_and_visible_feedback(service):
    bad=review();bad['advice_zh']={'private':'private model annotation'}
    bodies=[]
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(bad)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':20}})
    configure_cloud(service,httpx.MockTransport(handler))
    service.config.providers[0].retries=1
    settings=TaskSettings(goal='landscape',reference_only=True,demo=False,content_label='sfw')
    task=service.create_task(settings,start=False)
    from supervisor.providers import CloudError
    with pytest.raises(CloudError):
        await service.cloud.request(task,settings,'review',Evaluation,{'asset_id':'a'})
    assert len(bodies)==2
    repair=json.loads(bodies[1]['messages'][-1]['content'][0]['text'])['repair']
    assert repair['errors'][0]['type']=='review_advice_annotation'
    assert '200' in repair['errors'][0]['message']
    recorded=json.dumps(service.db.rows("SELECT body FROM events WHERE kind='CLOUD_REPLY_VALIDATION_FAILED'"))
    assert 'review_advice_annotation' in recorded and 'private' not in recorded
    service.db.transition(task,'PAUSED','RECOVERABLE_ERROR','INVALID_RESPONSE_REVIEW_REQUIRED')
    assert '建议附注必须为不超过 200 字的文本' in task_progress(service,task)
