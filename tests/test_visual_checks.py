import json

import pytest
from pydantic import ValidationError

from supervisor.models import Evaluation, TaskSettings, VisualChecks
from supervisor.wire import ReviewReply, SHAPES, expand_reply, request_payload
from test_basic_pass_and_results import normal_character
from test_review_format import review


def checks(**changes):
    return VisualChecks(extra_limbs=False,missing_parts=False,fused_bodies=False,
        disconnected_parts=False,duplicated_body=False,content_violation=False,evidence='',**changes)


@pytest.mark.parametrize('flag',['extra_limbs','missing_parts','fused_bodies','disconnected_parts','duplicated_body'])
def test_visible_structural_defect_cannot_hide_behind_high_scores_or_minor_severity(service,flag):
    audit=checks().model_dump();audit.update({flag:True,'evidence':'Right person: torso and arm cannot connect normally.'})
    score=normal_character(anatomy=95,structure=95,hands=95,unassessable_fields=['nsfw_target'],
        visual_checks=audit,issues=[],decision='keep')
    assert service.evaluate_decision(score,TaskSettings(goal='two people',autonomous=True,quality_threshold=42))==('QUARANTINE','BASIC_STRUCTURE_FAILED')
    assert score.anatomy==95  # Preserve model scores; the visible defect has its own gate.


def test_hidden_parts_do_not_become_missing_anatomy_and_uncertainty_is_not_zero(service):
    audit=checks().model_dump();audit.update(missing_parts=None,extra_limbs=None,disconnected_parts=None)
    score=normal_character(visual_checks=audit,issues=[],hands=None)
    assert service.evaluate_decision(score,TaskSettings(goal='partially occluded figures',autonomous=True))[0]=='ACCEPTED'
    score.anatomy=None;score.unassessable_fields.append('anatomy')
    service.validate_face_review(score,True)
    assert score.anatomy is None and score.issues==[] and score.decision=='review'
    assert service.evaluate_decision(score,TaskSettings(goal='portrait',autonomous=True),True)==('RECHECK','SCORE_FIELDS_MISSING')


@pytest.mark.parametrize('recommendation',['keep','review'])
def test_anatomical_doubts_cannot_be_offset_by_high_cosmetic_scores(service,recommendation):
    audit=checks().model_dump();audit.update(missing_parts=None,fused_bodies=None)
    score=normal_character(overall=76.5,prompt_alignment=95,aesthetics=95,composition=95,
        artifacts=95,style_match=95,anatomy=58,structure=60,hands=45,
        decision=recommendation,visual_checks=audit,unassessable_fields=['nsfw_target'],
        issues=[dict(category='anatomy_error',severity='minor',region='left arm',
            evidence='The visible arm connection cannot be confidently traced.')])
    before=score.model_dump()
    assert score.effective_score()>77
    assert service.evaluate_decision(score,TaskSettings(goal='two people',autonomous=True))==('RECHECK','STRUCTURE_REVIEW_REQUIRED')
    assert score.model_dump()==before


def test_requested_anatomy_review_is_held_even_with_high_structure_scores(service):
    score=normal_character(anatomy=90,structure=90,hands=90,decision='review',visual_checks=checks(),
        unassessable_fields=['nsfw_target'],issues=[dict(category='anatomy_error',severity='minor',region=None,
            evidence='Unresolved joint ownership between two figures.')])
    assert service.evaluate_decision(score,TaskSettings(goal='two people',autonomous=True))[1]=='STRUCTURE_REVIEW_REQUIRED'
    score.decision='keep'
    assert service.evaluate_decision(score,TaskSettings(goal='two people',autonomous=True))[0]=='ACCEPTED'
    score.decision='review';score.issues=[];score.hands=45
    assert service.evaluate_decision(score,TaskSettings(goal='two people',autonomous=True))[0]=='ACCEPTED'


async def test_structure_doubt_is_automatically_rechecked_and_saved_without_human_review(service,tmp_path):
    from unittest.mock import AsyncMock
    calls=[]
    async def respond(task,settings,purpose,contract,payload,images=None):
        calls.append(payload['asset_id'])
        if len(calls)==1:
            return normal_character(asset_id=payload['asset_id'],anatomy=58,structure=60,hands=45,
                decision='review',visual_checks=checks(),unassessable_fields=['nsfw_target'],
                issues=[dict(category='anatomy_error',severity='minor',region=None,evidence='Unclear arm attachment.')]),None
        return normal_character(asset_id=payload['asset_id'],visual_checks=checks(),issues=[]),None
    service.cloud.request=AsyncMock(side_effect=respond)
    task=service.create_task(TaskSettings(goal='two clothed people',direct_prompt='two clothed people',
        autonomous=True,groups=2,per_group=1,max_rounds=1,prescreen=False,export_folder=str(tmp_path/'saved')))
    await service.run(task)
    held=service.db.one('SELECT * FROM assets WHERE id=?',(calls[0],))
    assert held['state']=='AVAILABLE' and service.files.path(held['path']).exists()
    decision=service.db.one('SELECT * FROM decisions WHERE asset_id=? ORDER BY rowid DESC LIMIT 1',(held['id'],))
    assert (decision['action'],decision['reason'])==('ACCEPTED','BASIC_STRUCTURE_PASSED')
    assert len(calls)==3 and calls[0]==calls[1]
    assert {a['id'] for a in service.db.accepted(task)}=={calls[0],calls[2]}
    assert len(list((tmp_path/'saved').rglob('*.png')))==2
    assert decision['approved_by'] is None


@pytest.mark.parametrize('changes',[
    {'anatomy':41},
    {'visual_checks':dict(extra_limbs=True,missing_parts=False,fused_bodies=False,
        disconnected_parts=False,duplicated_body=False,content_violation=False,evidence='Visible extra arm.')},
    {'prompt_alignment':0,'aesthetics':0,'composition':0,'artifacts':0,'style_match':0},
])
async def test_human_structure_confirmation_cannot_bypass_confirmed_defects_or_threshold(service,tmp_path,changes):
    from test_result_batch import source_task
    task,assets=await source_task(service,tmp_path)
    asset=assets[0]
    score=normal_character(asset_id=asset['id'],anatomy=58,structure=60,hands=45,decision='review',
        visual_checks=checks(),unassessable_fields=['nsfw_target'],issues=[dict(category='anatomy_error',
            severity='minor',region=None,evidence='Unresolved arm attachment.')])
    data=score.model_dump();data.update(changes)
    stored=service.save_evaluation(asset,Evaluation.model_validate(data),None,'final')
    service.decision(asset['id'],stored['id'],'REVIEW','STRUCTURE_REVIEW_REQUIRED')
    service.db.transition(task,'PAUSED','FINAL_REVIEW','USER_PAUSE')
    with pytest.raises(ValueError,match='quality rules'):
        service.candidate_action(task,asset['id'],True)


@pytest.mark.parametrize('recommendation',['keep','retry','review'])
def test_major_problem_contradicting_clear_checklist_is_held_not_discarded_or_passed(service,recommendation):
    score=normal_character(visual_checks=checks(),decision=recommendation,
        issues=[dict(category='anatomy_error',severity='major',region='arm',evidence='Reported arm defect.')])
    before=score.model_dump()
    assert service.evaluate_decision(score,TaskSettings(goal='clothed portrait',autonomous=True))==('RECHECK','STRUCTURE_REVIEW_REQUIRED')
    assert score.model_dump()==before


def test_minor_hand_detail_with_explicitly_coherent_structure_can_pass(service):
    score=normal_character(hands=58,unassessable_fields=['nsfw_target'],visual_checks=checks(),decision='keep',
        issues=[dict(category='anatomy_error',severity='minor',region='hand',evidence='Small hand has simplified drawing detail.')])
    assert service.evaluate_decision(score,TaskSettings(goal='clothed portrait',autonomous=True))[0]=='ACCEPTED'


def test_contradictory_anatomy_review_cannot_bypass_quality_gate(service):
    score=normal_character(visual_checks=checks(),decision='review',
        issues=[dict(category='anatomy_error',severity='major',region='arm',evidence='Reported arm defect.')])
    settings=TaskSettings(goal='clothed portrait',autonomous=True,quality_threshold=95)
    assert service.evaluate_decision(score,settings)==('QUARANTINE','QUALITY_BELOW_THRESHOLD')


def test_unresolved_anatomy_cannot_pass_even_with_high_structural_scores(service):
    audit=checks().model_dump();audit['fused_bodies']=None
    score=normal_character(visual_checks=audit,decision='keep',
        issues=[dict(category='anatomy_error',severity='minor',region='shoulder',evidence='Visible connection is unresolved.')])
    assert service.evaluate_decision(score,TaskSettings(goal='two clothed people',autonomous=True))==('RECHECK','STRUCTURE_REVIEW_REQUIRED')


def test_low_structure_still_blocks_despite_clear_checklist(service):
    score=normal_character(hands=41,unassessable_fields=['nsfw_target'],visual_checks=checks(),decision='keep',issues=[])
    assert service.evaluate_decision(score,TaskSettings(goal='clothed portrait',autonomous=True))==('QUARANTINE','BASIC_STRUCTURE_FAILED')


def test_allowed_scope_is_not_rejected_by_unexplained_low_safety_but_visible_violation_is(service):
    settings=TaskSettings(goal='adult illustration',autonomous=True,content_label='adult_allowed')
    score=normal_character(safety_score=60,visual_checks=checks(),issues=[])
    assert service.evaluate_decision(score,settings)[0]=='ACCEPTED'
    audit=checks().model_dump();audit.update(content_violation=True,evidence='Visible content violates the confirmed scope.')
    score.visual_checks=VisualChecks(**audit);score.safety_score=100
    assert service.evaluate_decision(score,settings)==('QUARANTINE','SAFETY_CHECK_FAILED')
    score.visual_checks=None;score.safety_score=60
    assert service.evaluate_decision(score,settings)[1]=='SAFETY_CHECK_FAILED'  # Legacy records remain conservative.


def test_checklist_needs_visible_evidence_and_strict_booleans():
    for value in (True,'true'):
        data=checks().model_dump();data['missing_parts']=value
        with pytest.raises(ValidationError):VisualChecks(**data)


def test_compact_cloud_reply_roundtrips_checks_and_requests_all_people_and_missing_parts():
    data=review();audit=checks().model_dump();audit.update(missing_parts=True,evidence='Left figure: visible shoulder ends without an arm inside the frame.')
    data['checks']=audit
    score=expand_reply(Evaluation,ReviewReply.model_validate(data),{'asset_id':'image','stage':'final'})
    assert Evaluation.model_validate_json(score.model_dump_json()).visual_checks.missing_parts is True
    instruction=request_payload(Evaluation,{'acceptance_mode':'basic_structure','content_scope':'adult_allowed','face_required':True})['instruction']
    assert 'EACH visible person' in instruction and 'missing_parts' in instruction and 'not a limb hidden' in instruction
    assert 'permitted adult content alone is not a violation' in instruction
    assert 'missing_parts' in SHAPES[Evaluation]['checks']


async def test_structural_checks_feed_iteration_and_later_groups(service,tmp_path):
    from test_result_batch import source_task
    from supervisor.round_feedback import round_feedback
    task,assets=await source_task(service,tmp_path)
    audit=checks().model_dump();audit.update(fused_bodies=True,evidence='Left and right figures share an impossible torso connection.')
    score=normal_character(asset_id=assets[0]['id'],visual_checks=audit,issues=[])
    stored=service.save_evaluation(assets[0],score,None,'final')
    body=json.loads(stored['body'])
    assert body['issues'][0]['category']=='anatomy_error' and body['issues'][0]['severity']=='major'
    assert stored['rubric_version']=='2' and body['anatomy']==68
    shared=round_feedback(service,task)['directions']
    assert any(item['focus']=='geometry' and 'genuinely missing' in item['direction'] for item in shared)


async def test_final_review_keeps_more_image_detail_without_an_extra_request(service,tmp_path):
    import base64
    import io
    import httpx
    from PIL import Image
    from supervisor.providers import Cloud
    from test_supervisor import cloud_config
    calls=[]
    data=review();data['checks']=checks().model_dump()
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}],
            'usage':{'prompt_tokens':50,'completion_tokens':70}})
    service.config=cloud_config();service.cloud=Cloud(service.config,service.db,httpx.MockTransport(respond),{'TEST_SUPERVISOR_KEY':'fixture'})
    image=tmp_path/'large.png';Image.new('RGB',(1536,2048),'gray').save(image)
    settings=TaskSettings(goal='two people',direct_prompt='two people',content_label='sfw',autonomous=True,demo=False)
    task=service.create_task(settings,start=False)
    result,_=await service.cloud.request(task,settings,'review',Evaluation,{'asset_id':'image','stage':'final','acceptance_mode':'basic_structure'},[image])
    url=calls[0]['messages'][1]['content'][1]['image_url']['url']
    with Image.open(io.BytesIO(base64.b64decode(url.split(',')[1]))) as uploaded:
        assert uploaded.size==(1152,1536)
    assert len(calls)==1 and result.visual_checks is not None


@pytest.mark.parametrize('confirmed_defect',[False,True])
async def test_automatic_recheck_resolves_contradictory_finding_then_delivers_or_iterates(service,tmp_path,confirmed_defect):
    from unittest.mock import AsyncMock
    calls=[]
    async def respond(task,settings,purpose,contract,payload,images=None):
        calls.append((payload,list(images or [])))
        if len(calls)==1:
            return normal_character(asset_id=payload['asset_id'],visual_checks=checks(),decision='review',
                issues=[dict(category='anatomy_error',severity='major',region='arm',evidence='Reported uncertain arm connection.')]),None
        if len(calls)==2 and confirmed_defect:
            audit=checks().model_dump();audit.update(disconnected_parts=True,evidence='Visible arm ends without a shoulder connection.')
            return normal_character(asset_id=payload['asset_id'],visual_checks=audit,decision='retry',issues=[]),None
        # A small visible hand is simplified but no actual structural defect is observed.
        return normal_character(asset_id=payload['asset_id'],hands=48,visual_checks=checks(),decision='keep',
            unassessable_fields=['nsfw_target'],issues=[]),None
    service.cloud.request=AsyncMock(side_effect=respond)
    task=service.create_task(TaskSettings(goal='clothed portrait',direct_prompt='clothed portrait',
        autonomous=True,groups=1,per_group=1,max_rounds=2,prescreen=False,export_folder=str(tmp_path/'saved')))
    await service.run(task)
    status=service.db.one('SELECT state,phase,reason FROM tasks WHERE id=?',(task,))
    assert status['state']=='COMPLETED',status
    assert len(calls)==(3 if confirmed_defect else 2)
    assert calls[0][1]==calls[1][1] and len(calls[1][1])==1
    assert 'structure_confirmation' in calls[1][0] and 'structure_confirmation' not in calls[0][0]
    decisions=service.db.rows('SELECT action FROM decisions ORDER BY rowid')
    assert [d['action'] for d in decisions]==(['QUARANTINE','ACCEPTED'] if confirmed_defect else ['ACCEPTED'])
    assert len(list((tmp_path/'saved').rglob('*.png')))==1


@pytest.mark.parametrize('confirmed_already',[False,True])
async def test_resume_old_structure_hold_uses_original_without_generating_or_rechecking_twice(service,tmp_path,confirmed_already):
    from unittest.mock import AsyncMock
    from test_result_batch import source_task
    task,assets=await source_task(service,tmp_path,images_only_delivery=True)
    asset=assets[0]
    score=normal_character(asset_id=asset['id'],visual_checks=checks(),decision='review',
        issues=[dict(category='anatomy_error',severity='minor',region='hand',evidence='Small hand is hard to assess.')])
    row=service.save_evaluation(asset,score,None,'final')
    service.decision(asset['id'],row['id'],'REVIEW','STRUCTURE_REVIEW_REQUIRED')
    if confirmed_already:
        # A restart between the successful recheck and its decision must reuse that result.
        score=normal_character(asset_id=asset['id'],visual_checks=checks(),issues=[],traditional={'structure_confirmation':True})
        service.save_evaluation(asset,score,None,'final')
    settings=service.settings(task).model_copy(update={'review_enabled':True})
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?',(settings.model_dump_json(),task))
    service.db.transition(task,'PAUSED','FINAL_REVIEW','USER_PAUSE')
    original=service.files.path(asset['path']).read_bytes()
    calls=[]
    async def respond(task,settings,purpose,contract,payload,images=None):
        assert images==[service.files.path(asset['path'])]
        calls.append(payload)
        return normal_character(asset_id=asset['id'],visual_checks=checks(),issues=[]),None
    service.cloud.request=AsyncMock(side_effect=respond)
    service.comfy.run.reset_mock()
    service.resume(task)
    await service.run(task)
    assert len(calls)==(0 if confirmed_already else 1)
    service.comfy.run.assert_not_called()
    assert service.files.path(asset['path']).read_bytes()==original
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    decision=service.db.one('SELECT * FROM decisions WHERE asset_id=? ORDER BY rowid DESC LIMIT 1',(asset['id'],))
    assert decision['action']=='ACCEPTED' and decision['approved_by']==('automatic_score_recheck' if confirmed_already else None)


async def test_cloud_cannot_claim_software_has_already_rechecked_an_image(service):
    import httpx
    from test_live_readiness import configure_cloud
    value=normal_character(traditional={'structure_confirmation':True}).model_dump()
    def respond(request):
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':10}})
    configure_cloud(service,httpx.MockTransport(respond))
    settings=TaskSettings(goal='clothed portrait',direct_prompt='clothed portrait',autonomous=True,demo=False,content_label='sfw')
    task=service.create_task(settings,start=False)
    result,_=await service.cloud.request(task,settings,'review',Evaluation,{'asset_id':'image','stage':'final'})
    assert 'structure_confirmation' not in result.traditional


async def test_failed_automatic_recheck_resumes_from_existing_image_and_first_review(service,tmp_path):
    from unittest.mock import AsyncMock
    from supervisor.providers import CloudError
    calls=[]
    async def respond(task,settings,purpose,contract,payload,images=None):
        calls.append(payload)
        if len(calls)==1:
            return normal_character(asset_id=payload['asset_id'],visual_checks=checks(),decision='review',
                issues=[dict(category='anatomy_error',severity='minor',region='hand',evidence='Hand detail is uncertain.')]),None
        if len(calls)==2:
            raise CloudError('INVALID_RESPONSE')
        return normal_character(asset_id=payload['asset_id'],visual_checks=checks(),issues=[]),None
    service.cloud.request=AsyncMock(side_effect=respond)
    service.enqueue=lambda _:None
    task=service.create_task(TaskSettings(goal='clothed portrait',direct_prompt='clothed portrait',
        autonomous=True,groups=1,per_group=1,max_rounds=1,prescreen=False,export_folder=str(tmp_path/'saved')))
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='PAUSED'
    assert len(service.db.rows('SELECT * FROM generations WHERE task_id=?',(task,)))==1
    service.resume(task)
    await service.run(task)
    assert len(calls)==3 and all(c['asset_id']==calls[0]['asset_id'] for c in calls)
    assert len(service.db.rows('SELECT * FROM generations WHERE task_id=?',(task,)))==1
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    assert len(service.db.accepted(task))==1
