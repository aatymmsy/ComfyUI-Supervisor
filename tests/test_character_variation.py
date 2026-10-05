import json
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from supervisor.context import appearance_anchors, character_anchor_seed
from supervisor.db import now, uid
from supervisor.models import PromptPlan, StyleCard, TaskSettings
from supervisor.wire import request_payload


def planning_task(service, tmp_path, **overrides):
    settings = TaskSettings(**{
        'goal': 'adult portraits', 'autonomous': True, 'groups': 3,
        'target_styles': ['photography', 'ink', 'anime'], **overrides})
    reference = tmp_path / 'reference.png'
    Image.new('RGB', (32, 32)).save(reference)
    task = service.create_task(settings, [reference], start=False)
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',
                       (uid(), task, StyleCard().model_dump_json(), now()))
    groups = service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal', (task,))
    return task, settings, groups


def mock_planner(service, prompts):
    requests = []

    async def respond(task, settings, purpose, contract, payload):
        requests.append(request_payload(contract, payload))
        return PromptPlan(group_id=payload['group_id'], positive=next(prompts), reason='mock'), None

    service.cloud.request = AsyncMock(side_effect=respond)
    return requests


def test_twenty_groups_have_distinct_restart_stable_appearance_starting_points():
    designs = [character_anchor_seed('task', i) for i in range(20)]
    assert designs == [character_anchor_seed('task', i) for i in range(20)]
    assert len({tuple(design.values()) for design in designs}) == 20
    assert all(sum(left[key] != right[key] for key in left) >= 2
               for i, left in enumerate(designs) for right in designs[i + 1:])
    assert designs != [character_anchor_seed('another-task', i) for i in range(20)]


def test_appearance_comparison_excludes_identity_scene_and_is_bounded():
    prompt = 'ALICE, white hair, blue eyes, high ponytail, spaceship, red dress, mountains'
    assert appearance_anchors(prompt) == ['white hair', 'blue eyes', 'high ponytail']
    assert appearance_anchors('黑色头发，蓝瞳，双马尾，雪山') == ['黑色头发', '蓝瞳', '双马尾']
    assert len(appearance_anchors('black hair, white hair, red hair, green hair, blue eyes, '
                                  'hazel eyes, amber eyes, short hair, braids, bangs')) == 8


async def test_planning_compares_actual_other_group_appearance_without_copying_prompts(service, tmp_path):
    task, settings, groups = planning_task(service, tmp_path)
    requests = mock_planner(service, iter([
        '1girl, white hair, blue eyes, high ponytail, PRIVATE_SCENE_ONE',
        '1girl, black hair, brown eyes, pixie cut, PRIVATE_SCENE_TWO',
        '1girl, copper hair, green eyes, twin braids, PRIVATE_SCENE_THREE']))
    for group in groups:
        await service.plan(task, settings, group, first=True)
    assert len(requests) == 3 and service.cloud.request.await_count == 3
    assert len({json.dumps(r['character_variation']['suggested_anchors']) for r in requests}) == 3
    assert requests[0]['character_variation']['other_group_anchors'] == []
    assert requests[1]['character_variation']['other_group_anchors'] == [
        ['white hair', 'blue eyes', 'high ponytail']]
    assert 'PRIVATE_SCENE' not in json.dumps(requests)
    assert all('Character appearance MUST vary' in r['instruction'] for r in requests)
    assert 'realistic styles need plausible natural colors' in requests[0]['instruction']
    assert 'In monochrome work vary silhouette' in requests[1]['instruction']


async def test_scoped_character_locks_only_its_assigned_group(service, tmp_path):
    task, settings, groups = planning_task(service, tmp_path,
        character='legacy character', character_controls=[{'name':'forest guard', 'groups':[1]}])
    requests = mock_planner(service, iter(['1girl, guard', '1girl, city']))
    for group in groups[:2]:
        await service.plan(task, settings, group)
    assert requests[0]['character'] == 'forest guard'
    assert requests[0]['character_variation']['mode'] == 'preserve_declared'
    assert 'Character appearance MUST vary' not in requests[0]['instruction']
    assert requests[1]['character_variation']['mode'] == 'vary'
    assert 'character' not in requests[1] and 'legacy character' not in json.dumps(requests)


async def test_declared_trigger_is_kept_and_reaches_planner_without_random_identity(service, tmp_path):
    task, settings, groups = planning_task(service, tmp_path, lora_trigger_words='MY_IDENTITY_TRIGGER')
    requests = mock_planner(service, iter(['1girl, portrait']))
    row = await service.plan(task, settings, groups[0])
    variation = requests[0]['character_variation']
    assert variation == {'mode':'preserve_declared', 'lora_trigger_words':'MY_IDENTITY_TRIGGER'}
    assert 'Character appearance MUST vary' not in requests[0]['instruction']
    assert 'MY_IDENTITY_TRIGGER' in PromptPlan.model_validate_json(row['body']).positive


async def test_free_appearance_can_change_within_group_while_explicit_control_stays(service, tmp_path):
    task, settings, groups = planning_task(service, tmp_path,
        control_words=[{'word':'white hair', 'group':1, 'weight':1.3}])
    requests = mock_planner(service, iter([
        '1girl, white hair, blue eyes, high ponytail',
        '1girl, white hair, amber eyes, messy bun']))
    await service.plan(task, settings, groups[0])
    row = await service.plan(task, settings, groups[0])
    saved = PromptPlan.model_validate_json(row['body'])
    assert row['status'] == 'APPROVED' and '(white hair:1.3)' in saved.positive
    assert 'amber eyes' in saved.positive and 'high ponytail' not in saved.positive
    assert requests[1]['character_variation']['other_group_anchors'] == []
    assert 'NOT an identity lock' in requests[1]['instruction']
    assert 'preserve specified axes and vary the remaining free axes' in requests[1]['instruction']


@pytest.mark.parametrize('overrides', [{'direct_prompt':'my original prompt'}, {'autonomous':False}])
async def test_direct_and_manual_tasks_do_not_get_automatic_appearance_rules(service, tmp_path, overrides):
    task, settings, groups = planning_task(service, tmp_path, **overrides)
    requests = mock_planner(service, iter(['1girl, portrait']))
    row = await service.plan(task, settings, groups[0])
    if settings.direct_prompt is not None:
        service.cloud.request.assert_not_awaited()
        assert PromptPlan.model_validate_json(row['body']).positive == 'my original prompt'
    else:
        assert 'character_variation' not in requests[0]


def test_appearance_rules_do_not_change_review_or_manual_redraw():
    payload = {'asset_id':'asset', 'character_variation':{'mode':'vary'}}
    from supervisor.models import Evaluation
    assert 'character_variation' not in request_payload(Evaluation, payload)
    redraw = request_payload(PromptPlan, {'manual_retry':True, 'current':{'positive':'portrait'}})
    assert 'Preserve its subject, named character and defining features' in redraw['instruction']
    assert 'Character appearance MUST vary' not in redraw['instruction']
