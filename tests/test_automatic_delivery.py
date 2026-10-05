import json
from unittest.mock import AsyncMock

import pytest

from conftest import ui_callbacks
from supervisor.db import now, uid
from supervisor.models import PromptPlan, StyleCard, TaskSettings, VisualChecks
from supervisor.studio_features import result_review_html
from supervisor.ui import build_ui
from supervisor.wire import request_payload
from test_basic_pass_and_results import normal_character
from test_result_batch import source_task
from test_visual_checks import checks


def scope_unknown():
    audit = checks().model_dump()
    audit['content_violation'] = None
    return VisualChecks(**audit)


@pytest.mark.parametrize('nsfw', [None, 10, 74])
def test_permitted_content_is_not_held_for_adult_target_fit(service, nsfw):
    score = normal_character(nsfw_target=nsfw, visual_checks=checks(), issues=[],
        unassessable_fields=['hands', 'nsfw_target'] if nsfw is None else ['hands'])
    before = score.model_dump()
    settings = TaskSettings(goal='illustration', autonomous=True,
                            content_label='adult_allowed', quality_threshold=60)
    assert service.evaluate_decision(score, settings) == ('ACCEPTED', 'FINAL_QUALITY_PASSED')
    assert not service.needs_structure_confirmation(score, False, settings)
    assert score.model_dump() == before


@pytest.mark.parametrize('unresolved', [False, True])
async def test_content_scope_is_rechecked_then_delivered_or_iterated_without_approval(service, tmp_path, unresolved):
    calls = []

    async def respond(task, settings, purpose, contract, payload, images=None):
        calls.append((payload, images))
        audit = scope_unknown() if len(calls) == 1 or (unresolved and len(calls) == 2) else checks()
        return normal_character(asset_id=payload['asset_id'], visual_checks=audit, issues=[]), None

    service.cloud.request = AsyncMock(side_effect=respond)
    task = service.create_task(TaskSettings(goal='portrait', direct_prompt='portrait', autonomous=True,
        content_label='adult_allowed', quality_threshold=60, groups=1, per_group=1,
        max_rounds=2, prescreen=False, export_folder=str(tmp_path / 'saved')))
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?', (task,))['state'] == 'COMPLETED'
    assert len(calls) == (3 if unresolved else 2)
    assert calls[0][1] == calls[1][1]
    assert calls[1][0]['confirmation_reason'] == 'CONTENT_SCOPE_UNCERTAIN'
    decisions = service.db.rows('SELECT action FROM decisions ORDER BY rowid')
    assert [row['action'] for row in decisions] == (['QUARANTINE', 'ACCEPTED'] if unresolved else ['ACCEPTED'])
    assert len(list((tmp_path / 'saved').rglob('*.png'))) == 1


@pytest.mark.parametrize('action,reason,uncertain', [
    ('REVIEW', 'UNCERTAIN_OR_BELOW_THRESHOLD', True),
    ('REVIEW', 'CONTENT_SCOPE_UNCERTAIN', True),
    ('RECHECK', 'STRUCTURE_REVIEW_REQUIRED', True),
    ('CANDIDATE', 'FINAL_QUALITY_PASSED', False),
    ('REVIEW', 'BASIC_STRUCTURE_UNCERTAIN', False),
])
async def test_old_waiting_images_resolve_on_resume_using_the_original(service, tmp_path, action, reason, uncertain):
    task, assets = await source_task(service, tmp_path)
    asset = assets[0]
    score = normal_character(asset_id=asset['id'], issues=[], visual_checks=scope_unknown() if uncertain else checks())
    settings = service.settings(task).model_copy(update={
        'review_enabled':True, 'content_label':'adult_allowed', 'quality_threshold':60})
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?', (settings.model_dump_json(), task))
    row = service.save_evaluation(asset, score, None, 'final')
    service.decision(asset['id'], row['id'], action, reason)
    service.db.transition(task, 'WAITING_APPROVAL', 'FINAL_REVIEW', 'CONFIRM_QUALIFIED_CANDIDATES')
    original = service.files.path(asset['path']).read_bytes()
    count = len(service.db.rows('SELECT id FROM generations WHERE task_id=?', (task,)))

    async def respond(task, settings, purpose, contract, payload, images=None):
        assert images == [service.files.path(asset['path'])]
        return normal_character(asset_id=asset['id'], issues=[], visual_checks=checks()), None

    service.cloud.request = AsyncMock(side_effect=respond)
    service.comfy.run.reset_mock()
    assert '待人工检查' not in result_review_html(service, task)
    service.resume(task)
    await service.run(task)
    assert service.cloud.request.await_count == int(uncertain)
    service.comfy.run.assert_not_awaited()
    assert service.files.path(asset['path']).read_bytes() == original
    assert len(service.db.rows('SELECT id FROM generations WHERE task_id=?', (task,))) == count
    assert service.db.one('SELECT state FROM tasks WHERE id=?', (task,))['state'] == 'COMPLETED'
    decision = service.db.one('SELECT action,approved_by FROM decisions WHERE asset_id=? ORDER BY rowid DESC LIMIT 1', (asset['id'],))
    assert decision['action'] == 'ACCEPTED' and decision['approved_by'] != 'user'


@pytest.mark.parametrize('budget_error', [False, True])
async def test_limits_end_automatic_work_without_candidate_approval(service, tmp_path, budget_error):
    from supervisor.db import BudgetExceeded
    task, assets = await source_task(service, tmp_path)
    asset = assets[0]
    score = normal_character(asset_id=asset['id'], aesthetics=None,
        unassessable_fields=['aesthetics', 'hands', 'nsfw_target'], visual_checks=checks(), issues=[])
    settings = service.settings(task).model_copy(update={'review_enabled':True, 'max_generations':2 if not budget_error else 10})
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?', (settings.model_dump_json(), task))
    row = service.save_evaluation(asset, score, None, 'final')
    service.decision(asset['id'], row['id'], 'CANDIDATE', 'FINAL_QUALITY_PASSED')
    service.db.transition(task, 'PAUSED', 'FINAL_REVIEW', 'USER_PAUSE')
    service.cloud.request = AsyncMock(side_effect=BudgetExceeded() if budget_error else AssertionError('Limit must prevent cloud calls'))
    service.resume(task)
    await service.run(task)
    assert service.db.one('SELECT state,reason FROM tasks WHERE id=?', (task,)) == {
        'state':'PARTIAL', 'reason':'BUDGET_EXHAUSTED' if budget_error else 'GENERATION_LIMIT'}
    assert service.cloud.request.await_count == int(budget_error)
    assert service.files.path(asset['path']).is_file()


async def test_missing_scores_after_one_recheck_iterate_without_manual_gate(service, tmp_path):
    calls = []

    async def respond(task, settings, purpose, contract, payload, images=None):
        calls.append(payload)
        return normal_character(asset_id=payload['asset_id'], aesthetics=None,
            unassessable_fields=['aesthetics', 'hands', 'nsfw_target'], visual_checks=checks(), issues=[]), None

    service.cloud.request = AsyncMock(side_effect=respond)
    task = service.create_task(TaskSettings(goal='portrait', direct_prompt='portrait', autonomous=True,
        groups=1, per_group=1, max_rounds=2, prescreen=False, export_folder=str(tmp_path / 'saved')))
    await service.run(task)
    assert len(calls) == 4
    assert len({p['asset_id'] for p in calls}) == 2
    assert service.db.one('SELECT state FROM tasks WHERE id=?', (task,))['state'] == 'PARTIAL'
    assert {d['action'] for d in service.db.rows('SELECT action FROM decisions')} == {'QUARANTINE'}


async def test_automatic_draft_and_delivery_never_require_human_approval(service, tmp_path):
    task = service.create_task(TaskSettings(goal='landscape', direct_prompt='landscape', autonomous=True,
        groups=1, per_group=1, max_rounds=1, prescreen=False, export_folder=str(tmp_path / 'saved')))
    group = service.db.one('SELECT * FROM groups WHERE task_id=?', (task,))
    plan = PromptPlan(group_id=group['id'], positive='landscape', reason='old draft')
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)', (uid(),task,StyleCard().model_dump_json(),now()))
    service.db.execute('INSERT INTO prompt_variants VALUES(?,?,?,?,?,?,?,?)',
        (uid(),task,group['id'],None,0,plan.model_dump_json(),'DRAFT',now()))
    service.db.transition(task,'WAITING_APPROVAL','PROMPT','CONFIRM_STYLE_AND_PROMPTS')
    service.resume(task)

    async def respond(task, settings, purpose, contract, payload, images=None):
        return normal_character(asset_id=payload['asset_id'], visual_checks=checks(), issues=[]), None

    service.cloud.request = AsyncMock(side_effect=respond)
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?', (task,))['state'] == 'COMPLETED'
    delivery = service.db.one('SELECT manifest_path FROM deliveries WHERE task_id=?', (task,))
    manifest = json.loads(service.files.path(delivery['manifest_path']).read_bytes())
    assert manifest['groups'][0]['items'][0]['approval'] == 'automatic'


async def test_old_hold_without_final_scores_gets_original_model_review(service, tmp_path):
    task, assets = await source_task(service, tmp_path, images_only_delivery=True)
    asset = assets[0]
    settings = service.settings(task).model_copy(update={'review_enabled':True})
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?',(settings.model_dump_json(),task))
    service.decision(asset['id'],None,'REVIEW','FINAL_SCORE_REQUIRED')
    service.db.transition(task,'PAUSED','FINAL_REVIEW','USER_PAUSE')

    async def respond(task, settings, purpose, contract, payload, images=None):
        assert images == [service.files.path(asset['path'])]
        return normal_character(asset_id=asset['id'],visual_checks=checks(),issues=[]),None

    service.cloud.request=AsyncMock(side_effect=respond)
    service.comfy.run.reset_mock()
    service.resume(task)
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    assert service.cloud.request.await_count==1
    service.comfy.run.assert_not_awaited()


async def test_protected_failed_image_is_retained_without_manual_approval(service, tmp_path):
    task, assets = await source_task(service,tmp_path,images_only_delivery=True)
    asset=assets[0]
    settings=service.settings(task).model_copy(update={'review_enabled':True})
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?',(settings.model_dump_json(),task))
    service.db.execute('UPDATE assets SET protected=1 WHERE id=?',(asset['id'],))
    service.db.transition(task,'PAUSED','FINAL_REVIEW','USER_PAUSE')
    audit=checks().model_dump()
    audit.update(extra_limbs=True,evidence='Visible third arm attached to the shoulder.')

    async def respond(task, settings, purpose, contract, payload, images=None):
        return normal_character(asset_id=asset['id'],visual_checks=audit,issues=[]),None

    service.cloud.request=AsyncMock(side_effect=respond)
    await service._review_asset(task,asset['id'])
    decision=service.db.one('SELECT action,reason FROM decisions WHERE asset_id=? ORDER BY rowid DESC LIMIT 1',(asset['id'],))
    assert decision=={'action':'REJECTED','reason':'PROTECTED_ASSET'}
    assert service.files.path(asset['path']).is_file()
    assert asset['id'] not in {row['id'] for row in service.db.accepted(task)}


async def test_resumed_delivery_does_not_overwrite_user_edited_metadata(service, tmp_path):
    from supervisor.providers import CloudError
    task, assets = await source_task(service,tmp_path)
    destination=tmp_path/'export'/f'task_{task}'/'manifest.json'
    destination.write_bytes(b'user edited metadata')
    service.db.transition(task,'RUNNING','DELIVER')
    with pytest.raises(CloudError,match='OUTPUT_FILE_CHANGED'):
        service.deliver(task,'COMPLETED','TARGET_REACHED')
    assert destination.read_bytes()==b'user edited metadata'


def test_automatic_tasks_hide_manual_acceptance_controls(service):
    app = build_ui(service)
    controls = ui_callbacks(app)['review_controls']
    task = service.create_task(TaskSettings(goal='portrait', direct_prompt='portrait', autonomous=True), start=False)
    assert all(update['visible'] is False for update in controls(task))
    legacy = service.create_task(TaskSettings(goal='landscape'), start=False)
    assert all(update['visible'] is True for update in controls(legacy))
    assert all(update['visible'] is False for update in controls(None))
    app.close()


def test_final_recheck_requests_resolution_instead_of_human_approval():
    from supervisor.models import Evaluation
    from supervisor.wire import SHAPES
    payload = request_payload(Evaluation, {'asset_id':'image', 'stage':'final',
        'structure_confirmation':{'decision':'review'}, 'confirmation_reason':'SCORE_FIELDS_MISSING'})
    assert payload['confirmation_reason'] == 'SCORE_FIELDS_MISSING'
    assert 'not a request for human approval' in payload['instruction']
    assert 'connection audit' in SHAPES[Evaluation]['checks']['evidence']


def test_high_scores_and_empty_connection_basis_do_not_hide_an_arm_concern(service):
    score=normal_character(anatomy=88,structure=86,hands=72,decision='keep',
        unassessable_fields=['nsfw_target'],visual_checks=checks(),
        issues=[{'category':'anatomy_error','severity':'minor','region':'raised arm',
                 'evidence':'Hand detail is simplified; arm is assumed coherent.'}])
    settings=TaskSettings(goal='portrait',autonomous=True,quality_threshold=75)
    assert service.needs_structure_confirmation(score,True,settings)
    score.visual_checks.evidence='Raised arm: visible shoulder and upper arm reach the bent elbow; forearm continues to the gripping wrist. Fingers overlap naturally.'
    assert not service.needs_structure_confirmation(score,True,settings)


async def test_blind_connection_recheck_catches_defect_despite_high_first_scores(service, tmp_path):
    calls=[]

    async def respond(task,settings,purpose,contract,payload,images=None):
        calls.append(payload)
        if len(calls)==1:
            return normal_character(asset_id=payload['asset_id'],anatomy=88,structure=86,hands=72,
                prompt_alignment=88,aesthetics=88,composition=85,artifacts=94,style_match=90,
                unassessable_fields=['nsfw_target'],decision='keep',visual_checks=checks(),
                issues=[{'category':'anatomy_error','severity':'minor','region':'raised arm',
                         'evidence':'UNSUPPORTED_FIRST_ALLEGATION'}]),None
        if len(calls)==2:
            audit=checks().model_dump();audit.update(disconnected_parts=True,evidence='Raised forearm ends against a blanket fold without a coherent upper-arm connection.')
            return normal_character(asset_id=payload['asset_id'],anatomy=35,structure=38,
                visual_checks=audit,issues=[]),None
        return normal_character(asset_id=payload['asset_id'],visual_checks=checks(),issues=[]),None

    service.cloud.request=AsyncMock(side_effect=respond)
    task=service.create_task(TaskSettings(goal='portrait',direct_prompt='portrait',autonomous=True,
        groups=1,per_group=1,max_rounds=2,prescreen=False,export_folder=str(tmp_path/'saved')))
    await service.run(task)
    assert len(calls)==3
    assert calls[1]['structure_confirmation']=={'independent_original_review':True}
    assert calls[1]['confirmation_reason']=='CONNECTION_AUDIT_REQUIRED'
    assert 'UNSUPPORTED_FIRST_ALLEGATION' not in json.dumps(calls[1])
    rows=service.db.rows('SELECT body FROM evaluations WHERE asset_id=? ORDER BY rowid',(calls[0]['asset_id'],))
    assert json.loads(rows[0]['body'])['anatomy']==88
    assert json.loads(rows[1]['body'])['anatomy']==35
    assert [d['action'] for d in service.db.rows('SELECT action FROM decisions ORDER BY rowid')]==['QUARANTINE','ACCEPTED']
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'


async def test_old_duplicate_without_scores_is_resolved_locally(service,tmp_path):
    task,assets=await source_task(service,tmp_path,images_only_delivery=True)
    first,duplicate=assets
    service.files.path(duplicate['path']).write_bytes(service.files.path(first['path']).read_bytes())
    service.db.execute('UPDATE assets SET sha256=? WHERE id=?',(first['sha256'],duplicate['id']))
    service.decision(duplicate['id'],None,'REVIEW','EXACT_DUPLICATE')
    service.cloud.request=AsyncMock(side_effect=AssertionError('Exact duplicates need no cloud call'))
    await service.resolve_structure_hold(task,service.settings(task),duplicate['id'])
    assert service.db.one('SELECT action,reason FROM decisions WHERE asset_id=? ORDER BY rowid DESC LIMIT 1',(duplicate['id'],))=={'action':'QUARANTINE','reason':'EXACT_DUPLICATE'}
    service.cloud.request.assert_not_awaited()


@pytest.mark.parametrize('confirmed',[False,True])
async def test_partial_task_with_old_review_reuses_original_task_and_images(service,tmp_path,confirmed):
    task,assets=await source_task(service,tmp_path,images_only_delivery=True)
    asset=assets[0]
    settings=service.settings(task).model_copy(update={'review_enabled':True,'quality_threshold':60,'content_label':'adult_allowed'})
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?',(settings.model_dump_json(),task))
    score=normal_character(asset_id=asset['id'],visual_checks=checks() if confirmed else scope_unknown(),
        issues=[],traditional={'structure_confirmation':True} if confirmed else {})
    row=service.save_evaluation(asset,score,None,'final')
    service.decision(asset['id'],row['id'],'REVIEW','CONTENT_SCOPE_UNCERTAIN')
    service.db.transition(task,'PARTIAL','DELIVER','ROUND_OR_PATIENCE_LIMIT')
    tasks_before=service.db.one('SELECT COUNT(*) n FROM tasks')['n']

    async def respond(task,settings,purpose,contract,payload,images=None):
        assert images==[service.files.path(asset['path'])]
        return normal_character(asset_id=asset['id'],visual_checks=checks(),issues=[]),None

    service.cloud.request=AsyncMock(side_effect=respond)
    service.comfy.run.reset_mock()
    assert service.resume(task)==task
    await service.run(task)
    assert service.db.one('SELECT COUNT(*) n FROM tasks')['n']==tasks_before
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    assert service.cloud.request.await_count==int(not confirmed)
    service.comfy.run.assert_not_awaited()
