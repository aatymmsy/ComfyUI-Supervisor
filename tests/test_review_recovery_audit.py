from unittest.mock import AsyncMock

import pytest

from test_basic_pass_and_results import normal_character
from test_result_batch import source_task
from test_visual_checks import checks


@pytest.mark.parametrize('old_stage', ['final', 'prescreen', None])
@pytest.mark.parametrize('defective', [False, True])
async def test_recovery_uses_persisted_recheck_even_when_decision_is_stale(service, tmp_path, old_stage, defective):
    task, assets = await source_task(service, tmp_path, images_only_delivery=True)
    asset = assets[0]
    settings = service.settings(task).model_copy(update={'review_enabled': True, 'quality_threshold': 60})
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?', (settings.model_dump_json(), task))
    service.db.transition(task, 'PAUSED', 'FINAL_REVIEW', 'USER_PAUSE')
    old_checks = checks().model_dump()
    old_checks.update(content_violation=not defective, evidence='Original finding.')
    old = service.save_evaluation(asset, normal_character(asset_id=asset['id'],
        stage=old_stage, visual_checks=old_checks, issues=[]), None, old_stage) if old_stage else None
    service.decision(asset['id'], old['id'] if old else None, 'REVIEW', 'CONTENT_SCOPE_UNCERTAIN')
    latest_checks = checks().model_dump()
    latest_checks.update(content_violation=defective, evidence='Independent original review.')
    latest = service.save_evaluation(asset, normal_character(asset_id=asset['id'],
        visual_checks=latest_checks, issues=[], traditional={'structure_confirmation': True}), None, 'final')
    # Recovery must be deterministic even when imported timestamps tie.
    service.db.execute('UPDATE evaluations SET created_at=? WHERE asset_id=?', ('2000-01-01T00:00:00+00:00', asset['id']))
    service.cloud.request = AsyncMock(side_effect=AssertionError('Persisted review needs no new model call'))
    saved = service.reconcile_scored_results(task)
    decision = service.db.one('SELECT action,evaluation_id FROM decisions WHERE asset_id=? ORDER BY rowid DESC LIMIT 1', (asset['id'],))
    assert saved == int(not defective)
    assert decision == {'action': 'REJECTED' if defective else 'ACCEPTED', 'evaluation_id': latest['id']}
    service.cloud.request.assert_not_awaited()


@pytest.mark.parametrize('existing_quarantine', [False, True])
async def test_generation_recovery_keeps_protected_defect_without_stalling(service, tmp_path, existing_quarantine):
    task, assets = await source_task(service, tmp_path, images_only_delivery=True)
    asset = assets[0]
    settings = service.settings(task).model_copy(update={'review_enabled': True})
    service.db.execute('UPDATE tasks SET settings=? WHERE id=?', (settings.model_dump_json(), task))
    service.db.execute('DELETE FROM decisions WHERE asset_id=?', (asset['id'],))
    service.db.execute('UPDATE assets SET protected=1 WHERE id=?', (asset['id'],))
    service.db.execute("UPDATE generations SET state='EVALUATE' WHERE id=?", (asset['generation_id'],))
    service.db.transition(task, 'RUNNING', 'EVALUATE')
    service.save_evaluation(asset, normal_character(asset_id=asset['id'], visual_checks=checks(), issues=[]), None, 'final')
    audit = checks().model_dump()
    audit.update(disconnected_parts=True, evidence='Visible forearm disconnected from the elbow.')
    row = service.save_evaluation(asset, normal_character(asset_id=asset['id'], visual_checks=audit,
        issues=[], traditional={'structure_confirmation': True}), None, 'final')
    service.db.execute('UPDATE evaluations SET created_at=? WHERE asset_id=?', ('2000-01-01T00:00:00+00:00', asset['id']))
    if existing_quarantine:
        service.decision(asset['id'], row['id'], 'QUARANTINE', 'BASIC_STRUCTURE_FAILED')
    original = service.files.path(asset['path']).read_bytes()
    service.cloud.request = AsyncMock(side_effect=AssertionError('Valid confirmation must be reused'))
    generation = service.db.one('SELECT * FROM generations WHERE id=?', (asset['generation_id'],))
    await service.process_generation(task, settings, generation)
    decision = service.db.one('SELECT action,reason FROM decisions WHERE asset_id=? ORDER BY rowid DESC LIMIT 1', (asset['id'],))
    assert decision == {'action': 'REJECTED', 'reason': 'PROTECTED_ASSET'}
    assert service.db.one('SELECT state FROM generations WHERE id=?', (asset['generation_id'],))['state'] == 'DECIDED'
    assert service.files.path(asset['path']).read_bytes() == original
    service.cloud.request.assert_not_awaited()


@pytest.mark.parametrize('protected', [False, True])
async def test_duplicate_quarantine_recovers_without_an_evaluation(service, tmp_path, protected):
    task, assets = await source_task(service, tmp_path, images_only_delivery=True)
    asset = assets[0]
    settings = service.settings(task).model_copy(update={'review_enabled': True})
    service.db.execute('UPDATE assets SET protected=? WHERE id=?', (int(protected), asset['id']))
    service.db.execute("UPDATE generations SET state='EVALUATE' WHERE id=?", (asset['generation_id'],))
    service.decision(asset['id'], None, 'QUARANTINE', 'EXACT_DUPLICATE')
    service.db.transition(task, 'RUNNING', 'EVALUATE')
    service.cloud.request = AsyncMock(side_effect=AssertionError('Duplicates need no paid evaluation'))
    generation = service.db.one('SELECT * FROM generations WHERE id=?', (asset['generation_id'],))
    await service.process_generation(task, settings, generation)
    assert service.db.one('SELECT state FROM generations WHERE id=?', (asset['generation_id'],))['state'] == 'DECIDED'
    assert service.db.one('SELECT state FROM assets WHERE id=?', (asset['id'],))['state'] == ('AVAILABLE' if protected else 'QUARANTINED')
    service.cloud.request.assert_not_awaited()
