import json
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from supervisor.context import compact_card, ensure_face, needs_face
from supervisor.db import now, uid
from supervisor.models import Evaluation, PromptPlan, StyleCard, TaskSettings
from supervisor.providers import Cloud, CloudError
from supervisor.studio import task_progress
from supervisor.wire import TagsReply, expand_reply, request_payload


async def test_themes_do_not_inherit_original_scene_or_other_group_history(service, tmp_path):
    image = tmp_path / 'reference.png'
    Image.new('RGB', (64, 64), 'blue').save(image)
    settings = TaskSettings(goal='learn reference appearance', autonomous=True, groups=2,
        target_styles=['woman librarian reading a book', 'woman sailor aboard a ship'])
    task = service.create_task(settings, [image])
    asset = service.db.one('SELECT * FROM assets WHERE task_id=?', (task,))
    service.db.execute('UPDATE assets SET metadata=? WHERE id=?', (json.dumps({'extracted':{'positive':{'value':'woman at river, releasing a lantern'}}}), asset['id']))
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)', (uid(), task, StyleCard(subject=['woman at river'], writing_style=['comma-separated tags']).model_dump_json(), now()))
    groups = service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal', (task,))
    async def respond(task, settings, purpose, contract, payload):
        # Copying a base scene would reintroduce the river into both new themes.
        text = payload.get('base_prompt', {}).get('positive') or payload['target_style']
        return PromptPlan(group_id=payload['group_id'], positive=text, reason='test'), None
    service.cloud.request = AsyncMock(side_effect=respond)
    first = await service.plan(task, settings, groups[0], first=True)
    second = await service.plan(task, settings, groups[1], first=True)
    assert 'librarian' in json.loads(first['body'])['positive']
    assert 'sailor' in json.loads(second['body'])['positive']
    assert 'librarian' not in json.loads(second['body'])['positive']
    payload = service.cloud.request.call_args.args[-1]
    assert payload['current'] is None and not payload['feedback']
    wire = request_payload(PromptPlan, payload)
    assert wire['target_style'] == settings.target_styles[1] and 'base_prompt' not in wire
    assert 'overrides conflicting' in wire['instruction']
    await service.plan(task, settings, groups[1])
    current = service.cloud.request.call_args.args[-1]['current']['positive']
    assert 'sailor' in current and 'librarian' not in current


def test_face_tags_survive_short_context():
    tags = TagsReply(subject=['woman'],style=['photo'],composition='portrait',lighting='daylight',
        detail=['fabric','jewelry','hair','background'],negative=[],face=['green eyes','gentle facial expression'])
    card = expand_reply(StyleCard, tags, {}).model_dump()
    card['detail'] = ['fabric','jewelry','hair','background'] + card['detail']
    short = compact_card(card)
    assert 'green eyes' in short['detail'] and 'gentle facial expression' in short['detail']


def test_sparse_person_prompt_gets_face_and_open_eyes_without_paid_retry():
    plan = PromptPlan(group_id='g',positive='1girl, long black hair, closed eyes',negative='blur',reason='test')
    ensure_face(plan, 'natural expression', 'woman reading')
    assert 'closed eyes' not in plan.positive and 'open eyes' in plan.positive
    assert 'facial features' in plan.positive and 'deformed face' in plan.negative
    original = plan.model_dump()
    ensure_face(plan, 'natural expression', 'woman reading')
    assert plan.model_dump() == original


@pytest.mark.parametrize('positive,target', [('mountain landscape','ink wash'),('1girl, closed eyes','woman sleeping with closed eyes'),('man, back view','a man from behind'),('female lion','animal painting'),('mountain rock face','landscape'),('forest, no people','forest')])
def test_face_defaults_respect_non_people_and_explicit_user_composition(positive, target):
    plan = PromptPlan(group_id='g',positive=positive,reason='test')
    ensure_face(plan, '', target)
    assert plan.positive == positive and plan.negative == ''
    assert not needs_face(plan.positive, '', target)


def test_face_quality_cannot_be_hidden_by_high_other_scores(service):
    settings = TaskSettings(goal='woman portrait',autonomous=True)
    evaluation = Cloud.demo(Evaluation, 'review', {'asset_id':'test','stage':'final','round_index':2})
    for field in ('overall','prompt_alignment','aesthetics','composition','artifacts','style_match','safety_score'):
        setattr(evaluation, field, 100)
    evaluation.unassessable_fields = ['nsfw_target']
    evaluation.anatomy = 20
    evaluation.decision = 'keep'
    assert service.evaluate_decision(evaluation, settings)[0] == 'QUARANTINE'
    assert service.evaluate_decision(evaluation, settings, face_required=True)[0] != 'ACCEPTED'


async def test_missing_face_assessment_does_not_export_a_portrait(service, tmp_path):
    image = tmp_path / 'reference.png'
    Image.new('RGB', (64, 64), 'green').save(image)
    output = tmp_path / 'export'
    settings = TaskSettings(goal='woman portrait',autonomous=True,groups=1,per_group=1,
        target_styles=['woman reading'],export_folder=str(output),prescreen=False,max_rounds=1)
    task = service.create_task(settings, [image])
    async def respond(task, settings, purpose, contract, payload, images=None):
        if contract is StyleCard:
            result = StyleCard(subject=['woman'])
        elif contract is PromptPlan:
            result = PromptPlan(group_id=payload['group_id'],positive='woman reading a book',reason='test')
        else:
            assert payload['face_required']
            assert request_payload(Evaluation, payload)['face_required']
            result = Cloud.demo(Evaluation, purpose, {**payload,'round_index':2})
            result.anatomy = None
            result.unassessable_fields = ['anatomy']
            result.decision = 'keep'
        return result, None
    service.cloud.request = AsyncMock(side_effect=respond)
    await service.run(task)
    assert not service.db.accepted(task)
    assert not list((output / ('task_' + task)).rglob('*.png'))
    score = Evaluation.model_validate_json(service.db.one('SELECT body FROM evaluations')['body'])
    assert score.anatomy is None and score.decision == 'review'
    assert not any(issue.evidence=='人物面部要求未得到有效评估，请检查五官和眼睛' for issue in score.issues)
    progress = task_progress(service, task)
    assert '已生成 1' in progress and '已保存 0/1' in progress and '尚无合格交付图' in progress


def test_output_folder_is_checked_and_created_before_any_calls(service, tmp_path):
    image = tmp_path / 'ref.png'
    Image.new('RGB', (64, 64)).save(image)
    blocked = tmp_path / 'file_not_folder'
    blocked.write_text('user data')
    with pytest.raises(ValueError, match='OUTPUT_FOLDER_UNWRITABLE'):
        service.create_task(TaskSettings(goal='test',export_folder=str(blocked)), [image])
    assert not service.db.rows('SELECT * FROM tasks')
    assert not service.db.rows('SELECT * FROM api_calls')
    output = tmp_path / 'export'
    task = service.create_task(TaskSettings(goal='test',export_folder=str(output)), [image])
    assert (output / ('task_' + task)).is_dir()
    assert not list(output.glob('.supervisor-write-*'))


async def test_resume_retries_failed_export_without_regeneration(service, tmp_path, monkeypatch):
    image = tmp_path / 'ref.png'
    Image.new('RGB', (64, 64), 'green').save(image)
    output = tmp_path / 'export'
    task = service.create_task(TaskSettings(goal='landscape', autonomous=True, groups=1,
        per_group=1, prescreen=False, export_folder=str(output)), [image])
    original = service.export_file
    failed = False
    def temporarily_unwritable(task, relative, source):
        nonlocal failed
        if not failed and relative.startswith('group_'):
            failed = True
            raise CloudError('OUTPUT_FOLDER_UNWRITABLE')
        original(task, relative, source)
    monkeypatch.setattr(service, 'export_file', temporarily_unwritable)
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?', (task,))['state'] == 'PAUSED'
    assert len(service.db.accepted(task)) == 1
    generation_count = service.db.one('SELECT COUNT(*) n FROM generations')['n']
    assert '已保存 0/1' in task_progress(service, task)
    service.resume(task)
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?', (task,))['state'] == 'COMPLETED'
    assert service.db.one('SELECT COUNT(*) n FROM generations')['n'] == generation_count
    assert len(list((output / ('task_' + task)).glob('group_*/*.png'))) == 1
    assert '已保存 1/1' in task_progress(service, task)


async def test_manual_recheck_uses_own_theme_and_exports_immediately(service, tmp_path):
    image = tmp_path / 'generated.png'
    Image.new('RGB', (64, 64), 'green').save(image)
    output = tmp_path / 'export'
    settings = TaskSettings(goal='natural expression',autonomous=True,groups=2,per_group=1,
        content_label='sfw',target_styles=['woman librarian','woman sailor'],export_folder=str(output))
    task = service.create_task(settings, [image], start=False)
    group = service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal', (task,))[1]
    variant, generation = uid(), uid()
    plan = PromptPlan(group_id=group['id'],positive='woman sailor, detailed open eyes',reason='test')
    service.db.execute('INSERT INTO prompt_variants VALUES(?,?,?,?,?,?,?,?)', (variant, task, group['id'], None, 0, plan.model_dump_json(), 'USED', now()))
    service.db.execute('INSERT INTO generations(id,task_id,group_id,variant_id,submission_token,client_id,state,graph,graph_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (generation, task, group['id'], variant, uid(), uid(), 'DECIDED', '{}', 'test', now(), now()))
    asset = service.db.one('SELECT * FROM assets WHERE task_id=?', (task,))
    service.db.execute("UPDATE assets SET source_kind='generated',group_id=?,generation_id=? WHERE id=?", (group['id'], generation, asset['id']))
    async def respond(task, settings, purpose, contract, payload, images):
        assert payload['target_style'] == 'woman sailor' and payload['face_required']
        assert payload['content_scope'] == 'sfw'
        result = Cloud.demo(Evaluation, purpose, {**payload, 'round_index':2})
        result.unassessable_fields = ['nsfw_target']
        result.anatomy = 94
        return result, None
    service.cloud.request = AsyncMock(side_effect=respond)
    await service._review_asset(task, asset['id'])
    assert len(service.db.accepted(task)) == 1
    assert len(list((output / ('task_' + task) / 'group_02').glob('*.png'))) == 1
