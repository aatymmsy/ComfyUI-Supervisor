from conftest import ui_callbacks
from unittest.mock import AsyncMock

import gradio as gr
import pytest
from PIL import Image
from pydantic import ValidationError

from supervisor.control_words import parse_control_words, enforce_control_words
from supervisor.db import uid, now
from supervisor.models import ControlWord, PromptPlan, StyleCard, TaskSettings
from supervisor.ui import build_ui
from supervisor.wire import request_payload
from test_workflow_editor import configure_editor


@pytest.mark.parametrize('weight',[0,3.1,float('nan'),float('inf')])
def test_control_weight_must_be_finite_and_nonzero(weight):
    with pytest.raises(ValidationError):
        ControlWord(word='red coat',weight=weight)


def test_control_terms_keep_user_weights_without_duplicate_attention_or_negative_conflict():
    words = parse_control_words([['red coat',1.3],['soft lighting',None],['',None]])
    plan = PromptPlan(group_id='g',positive='portrait, (red coat:0.5), soft lighting, (trees, river:1.2)',negative='blur, red coat',reason='test')
    controlled = enforce_control_words(plan,words)
    assert controlled.positive == '(red coat:1.3), (soft lighting:1), portrait, (trees, river:1.2)'
    assert controlled.negative == 'blur'
    assert enforce_control_words(controlled,words) == controlled
    assert plan.positive.startswith('portrait')
    assert enforce_control_words(plan,[]) == plan
    with pytest.raises(ValueError):parse_control_words([['red coat',1],['RED COAT',2]])
    with pytest.raises(ValueError):parse_control_words([['(red coat:2)',1]])


async def test_cloud_omission_and_iteration_cannot_remove_controls_from_actual_workflow(service,tmp_path):
    configure_editor(service)
    image = tmp_path/'reference.png';Image.new('RGB',(64,64)).save(image)
    settings = TaskSettings(goal='landscape',groups=2,per_group=1,target_styles=['ink','photo'],autonomous=True,
        control_words=[{'word':'sunset','weight':1.4},{'word':'mountain','weight':1}],content_label='sfw')
    task = service.create_task(settings,[image],start=False)
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',(uid(),task,StyleCard(subject=['landscape']).model_dump_json(),now()))
    requests=[]
    async def cloud(task_id, settings, purpose, contract, payload):
        requests.append(payload)
        return PromptPlan(group_id=payload['group_id'],positive='river, (sunset:0.2)',negative='sunset, blur',reason='mock omission'),None
    service.cloud.request = cloud
    groups=service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(task,))
    for group in groups:
        for round_index in range(2):
            group['round_index']=round_index
            row=await service.plan(task,settings,group,first=round_index==0)
            plan=PromptPlan.model_validate_json(row['body'])
            actual=service.comfy.graph(plan,'test')
            assert '(sunset:1.4)' in actual['6']['inputs']['text']
            assert '(mountain:1)' in actual['6']['inputs']['text']
            assert '0.2' not in actual['6']['inputs']['text']
            assert 'sunset' not in actual['7']['inputs']['text']
            assert plan.positive.count('(sunset:') == 1
    assert len(requests) == 4 and requests[1]['current']
    payload=request_payload(PromptPlan,requests[0])
    assert payload['control_words'] == [w.model_dump() for w in settings.control_words]
    assert service.settings(task).control_words == settings.control_words
    assert not service.db.rows('SELECT id FROM api_calls')


async def test_control_grid_is_saved_to_task_and_invalid_weight_blocks_before_cloud(service,tmp_path,monkeypatch):
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    monkeypatch.setattr(gr,'Warning',lambda *a,**k:None)
    ready=AsyncMock();monkeypatch.setattr('supervisor.ui.check_live_ready',ready)
    service.enqueue=lambda task:None
    app=build_ui(service)
    callback=ui_callbacks(app)['studio_create']
    image=tmp_path/'reference.png';Image.new('RGB',(64,64)).save(image)
    async def start(rows):
        return await callback([str(image)],[],'',15,'landscape','ink','',1,'Live',str(service.project_root/'export'),512,512,20,7,42,
            'sfw',60,1,6,2,'zh','euler','normal',None,50000,1,*[value for row in rows for value in row])
    result=await start([['1','sunset, mountain，soft lighting','1.4']] + [['1','','']]*29)
    assert service.settings(result[0]['value']).control_words == [
        ControlWord(word=term,weight=1.4,group=1,weight_group=1) for term in ['sunset','mountain','soft lighting']]
    ready.reset_mock()
    result=await start([['1','sunset',0]])
    assert '控制词格式无效' in result[2] and 'studio-control-words' in result[3]
    assert ready.await_count == 0 and len(service.db.rows('SELECT id FROM tasks')) == 1


def test_unset_weight_can_change_but_explicit_weight_is_locked():
    words = parse_control_words([['sunset',None],['mountain',1.4]])
    plan = PromptPlan(group_id='g',positive='(sunset:1.8), (mountain:0.2), river',reason='design')
    controlled = enforce_control_words(plan,words)
    assert controlled.positive == '(sunset:1.8), (mountain:1.4), river'
    revised = plan.model_copy(update={'positive':'(sunset:0.8), river'})
    assert enforce_control_words(revised,words).positive == '(sunset:0.8), (mountain:1.4), river'
    for invalid in ['0','4','nan','inf','bad']:
        malformed = plan.model_copy(update={'positive':f'(sunset:{invalid}), river'})
        assert enforce_control_words(malformed,words).positive == '(sunset:1), (mountain:1.4), river'
    payload = request_payload(PromptPlan,{'control_words':[w.model_dump() for w in words]})
    assert payload['control_words'][0]['weight'] is None
    assert 'non-null weight is locked' in payload['instruction']
    assert parse_control_words([['sunset',' '],['mountain','1.4']]) == words


@pytest.mark.parametrize('group',['',0,-1,1.5,'bad',True,3])
def test_group_controls_reject_invalid_or_out_of_range_group(group):
    with pytest.raises(ValueError):
        parse_control_words([[group,'sunset','']],group_count=2)


def test_same_term_can_have_separate_weights_in_different_groups():
    words=parse_control_words([['1','sunset','1.4'],['2','sunset','0.8']],2)
    assert [word.group for word in words]==[1,2]
    with pytest.raises(ValueError):
        parse_control_words([['1','sunset',1],['1','SUNSET',2]],2)
    with pytest.raises(ValueError):
        TaskSettings(goal='landscape',groups=1,control_words=words)


@pytest.mark.parametrize('row_weight,expected_weight',[(0.8,0.8),(None,0.2)])
async def test_group_controls_scope_cloud_payload_and_actual_workflow(service,tmp_path,row_weight,expected_weight):
    from supervisor.control_words import controlled_goal
    configure_editor(service)
    image=tmp_path/'reference.png';Image.new('RGB',(64,64)).save(image)
    settings=TaskSettings(goal='landscape',groups=2,per_group=1,target_styles=['ink','photo'],autonomous=True,
        control_words=parse_control_words([[1,'sunset, soft lighting',1.4],[2,'sunset, mountain',row_weight]],2))
    task=service.create_task(settings,[image],start=False)
    service.db.execute('INSERT INTO style_cards VALUES(?,?,1,?,?)',(uid(),task,StyleCard(subject=['landscape']).model_dump_json(),now()))
    requests=[]
    async def cloud(task_id, settings, purpose, contract, payload):
        requests.append(payload)
        return PromptPlan(group_id=payload['group_id'],positive='river, (sunset:0.2), (mountain:1.8)' if any(word['word']=='mountain' for word in payload['control_words']) else 'river',reason='mock'),None
    service.cloud.request=cloud
    groups=service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(task,))
    for group in groups:
        for round_index in range(2):
            group['round_index']=round_index
            row=await service.plan(task,settings,group,first=round_index==0)
            actual=service.comfy.graph(PromptPlan.model_validate_json(row['body']),'test')['6']['inputs']['text']
            if group['ordinal']==0:
                assert '(sunset:1.4)' in actual and '(soft lighting:1.4)' in actual and 'mountain' not in actual
            else:
                assert f'(sunset:{expected_weight:g})' in actual and f'(mountain:{expected_weight:g})' in actual and 'soft lighting' not in actual
            assert all(word['group']==group['ordinal']+1 for word in requests[-1]['control_words'])
    assert controlled_goal(settings,0)=='landscape sunset soft lighting'
    assert controlled_goal(settings,1)=='landscape sunset mountain'
    assert service.settings(task).control_words==settings.control_words
    assert not service.db.rows('SELECT id FROM api_calls')


def test_add_control_row_inherits_previous_group_without_resetting_other_rows(service):
    app=build_ui(service)
    callback=next(f.fn for f in app.fns.values() if getattr(f.fn,'__name__','')=='add_control_row')
    result=callback(2,'1','3',*(['1']*28))
    assert result[0]==3 and result[3]['visible'] is True
    updates=result[31:61]
    assert updates[2]['value']=='3'
    assert all('value' not in update for index,update in enumerate(updates) if index!=2)
    full=callback(30,*(['2']*30))
    assert full[0]==30 and full[-1]['interactive'] is False
    assert all('value' not in update for update in full[31:61])


def test_multiple_tags_share_row_group_weight_and_keep_phrase_spaces():
    words=parse_control_words([[1,' sunset, mountain，soft lighting, , ','1.4'],
        [2,'sunset, mountain','0.8'],[1,'， ,','']],2)
    assert [(word.word,word.group,word.weight,word.weight_group) for word in words] == [
        ('sunset',1,1.4,1),('mountain',1,1.4,1),('soft lighting',1,1.4,1),
        ('sunset',2,0.8,2),('mountain',2,0.8,2)]
    settings=TaskSettings(goal='landscape',groups=2,control_words=words)
    assert TaskSettings.model_validate_json(settings.model_dump_json())==settings
    plan=PromptPlan(group_id='g',positive='(sunset:0.2), mountain, river',negative='sunset, mountain, blur',reason='test')
    controlled=enforce_control_words(plan,words[:3])
    assert controlled.positive=='(sunset:1.4), (mountain:1.4), (soft lighting:1.4), river'
    assert controlled.negative=='blur'
    assert enforce_control_words(controlled,words[:3])==controlled


@pytest.mark.parametrize('positive,weight',[
    ('(sunset:1.8), (mountain:0.4)',1.8),
    ('river, (mountain:1.6)',1.6),
    ('(sunset:1), (mountain:1.6)',1),
    ('(sunset:nan), (mountain:1.5)',1.5),
    ('(sunset:4), (mountain:0)',1),
    ('river',1),
])
def test_auto_weight_is_shared_even_if_model_omits_or_disagrees(positive,weight):
    words=parse_control_words([[1,'sunset, mountain',None],[1,'soft lighting',None]],1)
    plan=PromptPlan(group_id='g',positive=positive+', (soft lighting:0.8)',reason='test')
    controlled=enforce_control_words(plan,words)
    assert controlled.positive.startswith(f'(sunset:{weight:g}), (mountain:{weight:g}), (soft lighting:0.8)')
    assert enforce_control_words(controlled,words)==controlled
    revised=plan.model_copy(update={'positive':'(mountain:1.2), (soft lighting:0.6)'})
    assert enforce_control_words(revised,words).positive=='(sunset:1.2), (mountain:1.2), (soft lighting:0.6)'
    payload=request_payload(PromptPlan,{'control_words':[word.model_dump() for word in words]})
    assert 'same non-null weight_group' in payload['instruction']


@pytest.mark.parametrize('rows',[
    [[1,'sunset, SUNSET',1]],
    [[1,'sunset, mountain',1],[1,'MOUNTAIN',1]],
    [[1,'sunset, (mountain:1.4)',1]],
    [[1,'sunset, mountain',0]],
])
def test_multiple_tags_reject_duplicates_syntax_and_invalid_weights(rows):
    with pytest.raises(ValueError):
        parse_control_words(rows,1)


def test_multiple_tag_limit_counts_tags_and_settings_require_consistent_row_weights():
    words=parse_control_words([[1,','.join(f'tag {index}' for index in range(300)),1]],1)
    assert len(TaskSettings(goal='test',groups=1,control_words=words).control_words)==300
    with pytest.raises(ValueError):
        parse_control_words([[1,','.join(f'tag {index}' for index in range(301)),1]],1)
    with pytest.raises(ValueError):
        TaskSettings(goal='test',groups=1,control_words=[
            ControlWord(word='sunset',group=1,weight_group=1,weight=1.4),
            ControlWord(word='mountain',group=1,weight_group=1)])
