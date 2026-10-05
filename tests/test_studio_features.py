from conftest import ui_callbacks
import json

import pytest
import httpx
from PIL import Image

from supervisor.control_words import parse_control_words
from supervisor.db import now,uid
from supervisor.files import update_reference_sample,studio_reference_paths
from supervisor.models import Evaluation,PromptPlan,TaskSettings,StyleCard
from supervisor.studio import task_progress
from supervisor.studio_features import PresetStore,StudioPreset,control_preview_html,result_review_html,task_control_preview
from supervisor.ui import build_ui
from test_live_readiness import configure_cloud


def test_sample_growth_shrink_regrowth_and_explicit_reshuffle_preserve_pool(tmp_path,monkeypatch):
    paths=[]
    for index in range(50):
        path=tmp_path/f'{index:02}.png';path.touch();paths.append(path)
    def no_decode(*a,**k):
        raise AssertionError('Sampling must not decode images')
    monkeypatch.setattr(Image,'open',no_decode)
    directory={'source':'images'}
    original,total,state=update_reference_sample(paths,directory,'',7,15)
    directory['_sample']=state
    grown,_,state=update_reference_sample(paths,directory,'',7,20)
    assert grown[:15]==original and len(grown)==20 and total==50
    directory['_sample']=state
    reduced,_,state=update_reference_sample(paths,directory,'',7,10)
    assert reduced==original[:10]
    directory['_sample']=state
    restored,_,state=update_reference_sample(paths,directory,'',7,15)
    assert restored==original
    directory['_sample']=state
    reshuffled,_,state=update_reference_sample(paths,directory,'',7,15,force=True)
    assert reshuffled!=original and len(set(reshuffled))==15
    directory['_sample']=state
    assert studio_reference_paths(paths,directory,'',7,15)[0]==reshuffled
    changed,_,_=update_reference_sample(paths[:5],directory,'',7,15)
    assert set(changed)==set(paths[:5])


def test_reference_thumbnails_are_lazy_paginated_and_cache_only_selected_images(service,tmp_path,monkeypatch):
    paths=[]
    for index in range(40):
        path=tmp_path/f'{index:02}.png';Image.new('RGB',(64,64),(index,0,0)).save(path);paths.append(path)
    app=build_ui(service)
    callbacks=ui_callbacks(app)
    _,_,_,directory=callbacks['preview_references'](paths,{'source':'images'},'',20)
    assert not list(service.data_root.rglob('reference-thumbnails/*.jpg'))
    reads=[]
    original=Image.open
    def record(path,*a,**k):
        reads.append(str(path));return original(path,*a,**k)
    monkeypatch.setattr(Image,'open',record)
    callbacks['refresh_open_reference_preview'](directory,1,False)
    assert not reads
    page,status,number=callbacks['reference_preview_page'](directory,1)
    assert page['visible'] and len(page['value'])==12 and number==1 and '1/2' in status
    assert reads==directory['_sample']['selected'][:12]
    callbacks['reference_preview_page'](directory,1)
    assert len(reads)==12
    page,status,number=callbacks['reference_preview_page'](directory,99)
    assert len(page['value'])==8 and number==2 and '2/2' in status
    assert len(reads)==20
    page,status,number=callbacks['refresh_open_reference_preview'](directory,1,True)
    assert len(page['value'])==12 and number==1


def test_preset_store_persists_and_overwrites_only_valid_recipes(tmp_path):
    path=tmp_path/'presets.json'
    preset=StudioPreset(goal='landscape',themes='ink\nphoto',groups=2,sample_count=1000,
        rows=[['1','sunset, mountain','1.4'],['2','clouds','']],visible_rows=2)
    store=PresetStore(path)
    store.save('风景方案',preset)
    assert PresetStore(path).read()['风景方案']==preset
    revised=preset.model_copy(update={'sample_count':20})
    store.save('风景方案',revised)
    assert len(store.read())==1 and store.read()['风景方案'].sample_count==20
    prior=path.read_bytes()
    for bad in ({**preset.model_dump(),'apikey':'secret'}, {**preset.model_dump(),'groups':0}):
        with pytest.raises(ValueError):
            store.save('invalid',bad)
        assert path.read_bytes()==prior
    for themes in ('only one',''):
        store.save('random',preset.model_copy(update={'themes':themes}))
        assert store.read()['random'].themes==themes


def test_preset_ui_roundtrip_restores_rows_parameters_and_unrestricted_count(service):
    app=build_ui(service)
    callbacks=ui_callbacks(app)
    fields=[65,'landscape','ink\nphoto','subject, light',2,3,512,768,20,7,42,'euler','normal',70,12,2,50000,3,'sfw']
    rows=[['1','sunset, mountain','1.4'],['2','clouds','']]+[['1','','']]*28
    choice,status=callbacks['save_studio_preset']('常用风景',*fields,2,*[v for row in rows for v in row])
    assert choice['value']=='常用风景' and '已保存' in status
    restored=callbacks['load_studio_preset']('常用风景')
    assert list(restored[:19])==fields
    assert list(restored[21:111])==[v for row in rows for v in row]
    assert restored[111]==2 and restored[112]['visible'] and restored[113]['visible']
    assert restored[114]['visible'] is False and restored[-2]==50 and '已载入' in restored[-1]
    assert all('value' not in value for value in callbacks['load_studio_preset']('missing')[:-1])
    rebuilt=build_ui(service)
    refresh=next(f.fn for f in rebuilt.fns.values() if getattr(f.fn,'__name__','')=='refresh_presets')
    assert refresh()['choices']==['常用风景']


def make_scored_round(service,tmp_path,task,group,round_index,positive,weight=85):
    variant,generation=uid(),uid()
    plan=PromptPlan(group_id=group,positive=positive,negative=f'negative round {round_index}',reason='design',params={'seed':42+round_index})
    service.db.execute('INSERT INTO prompt_variants VALUES(?,?,?,?,?,?,?,?)',(variant,task,group,None,round_index,plan.model_dump_json(),'USED',now()))
    service.db.execute('INSERT INTO generations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (generation,task,group,variant,uid(),None,uid(),'DECIDED','{}','test','[]',now(),now()))
    path=tmp_path/f'{round_index}.png';Image.new('RGB',(64,64),(round_index,0,0)).save(path)
    asset=service.files.import_image(task,path,'sfw',group_id=group,generation_id=generation)
    evaluation=Evaluation(asset_id=asset['id'],stage='final',overall=weight,prompt_alignment=weight,
        aesthetics=weight,composition=weight,anatomy=weight,artifacts=weight,style_match=weight,nsfw_target=weight,
        decision='keep',delete_reason=[],prompt_suggestions=[],confidence=.9,issues=[],unassessable_fields=[])
    saved=service.save_evaluation(asset,evaluation,None,'final')
    service.decision(asset['id'],saved['id'],'ACCEPTED','test')
    return asset


def test_result_score_and_folded_prompt_use_actual_variant_not_latest_group_prompt(service,tmp_path):
    task=service.create_task(TaskSettings(goal='test',groups=1,per_group=2,demo=False),start=False)
    group=service.db.one('SELECT id FROM groups WHERE task_id=?',(task,))['id']
    first=make_scored_round(service,tmp_path,task,group,0,'first round, <script>untrusted</script>',80)
    second=make_scored_round(service,tmp_path,task,group,1,'second round, soft light',90)
    later=PromptPlan(group_id=group,positive='unused later prompt',reason='test')
    service.db.execute('INSERT INTO prompt_variants VALUES(?,?,?,?,?,?,?,?)',(uid(),task,group,None,2,later.model_dump_json(),'APPROVED',now()))
    rendered=result_review_html(service,task)
    cards=rendered.split('<details class="result-review-card"')[1:]
    assert len(cards)==2
    assert second['id'] in cards[0] and '综合分 90.0' in cards[0] and '第 2 轮' in cards[0]
    assert 'second round, soft light' in cards[0] and 'negative round 1' in cards[0] and 'first round' not in cards[0]
    assert first['id'] in cards[1] and '综合分 80.0' in cards[1] and '第 1 轮' in cards[1]
    assert 'first round' in cards[1] and '&lt;script&gt;' in cards[1] and '<script>' not in rendered
    assert 'unused later prompt' not in rendered and rendered.count('此轮提示词')==2


def test_control_weight_preview_shows_model_shared_weight_and_locked_weight(service):
    words=parse_control_words([[1,'sunset, mountain',None],[1,'soft lighting',1.4]],1)
    task=service.create_task(TaskSettings(goal='test',groups=1,control_words=words),start=False)
    group=service.db.one('SELECT id FROM groups WHERE task_id=?',(task,))['id']
    plan=PromptPlan(group_id=group,positive='(sunset:1.8), (mountain:0.2), (soft lighting:0.5)',reason='test')
    service.db.execute('INSERT INTO prompt_variants VALUES(?,?,?,?,?,?,?,?)',(uid(),task,group,None,0,plan.model_dump_json(),'USED',now()))
    actual=task_control_preview(service,task)
    assert '(sunset:1.8), (mountain:1.8), (soft lighting:1.4)' in actual and '本轮实际权重' in actual
    configured=control_preview_html(words,1)
    assert '模型统一选择' in configured and '(soft lighting:1.4)' in configured


def test_progress_reports_group_shortfalls_tokens_cost_and_actionable_error(service,tmp_path):
    task=service.create_task(TaskSettings(goal='test',groups=2,per_group=2,target_styles=['ink','photo'],budget_micro=1_000_000),start=False)
    group=service.db.one('SELECT id FROM groups WHERE task_id=? AND ordinal=0',(task,))['id']
    make_scored_round(service,tmp_path,task,group,0,'mountain')
    call=service.db.reserve(task,'tagging','p','m','c',{},1000,1_000_000)
    service.db.settle(call,'SUCCESS',500,{'input_tokens':10,'output_tokens':5})
    service.db.transition(task,'PAUSED','ANALYZE','MISSING_API_KEY')
    progress=task_progress(service,task)
    assert '合格 1/2 · 剩余 1' in progress and '合格 0/2 · 剩余 2' in progress
    assert '剩余目标 3 张' in progress and 'token 15/' in progress and '费用 0.0005 / 1 USD' in progress
    assert '下一步' in progress and '检查 API 连接' in progress


async def test_increasing_sample_tags_only_new_images_and_reduction_uses_cache(service,tmp_path):
    paths=[]
    for index in range(20):
        path=tmp_path/f'{index:02}.png';Image.new('RGB',(64,64),(index,100,150)).save(path);paths.append(path)
    calls=[]
    def respond(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':StyleCard(subject=['landscape']).model_dump_json()}}],
            'usage':{'prompt_tokens':1,'completion_tokens':1}})
    configure_cloud(service,httpx.MockTransport(respond))
    app=build_ui(service)
    callbacks=ui_callbacks(app)
    directory={'source':'images'}
    selections=[]
    for count,expected_calls in [(5,5),(8,8),(3,8),(8,8)]:
        _,_,seed,directory=callbacks['preview_references'](paths,directory,'',count)
        selections.append(list(directory['_sample']['selected']))
        messages=[text async for text in callbacks['auto_tag_references'](paths,directory,'',seed,'landscape','','sfw',1,50000,count)]
        assert len(calls)==expected_calls
        assert f'已打标 {count}/{count}' in messages[-1]
    assert selections[1][:5]==selections[0] and selections[2]==selections[0][:3] and selections[3]==selections[1]
    assert len(service.db.rows('SELECT id FROM tasks'))==2
    assert len(service.db.rows("SELECT id FROM assets WHERE source_kind='reference'"))==8
