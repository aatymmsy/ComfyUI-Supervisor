from conftest import ui_callbacks
import json
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from supervisor.models import Evaluation, TaskSettings
from supervisor.wire import request_payload


def normal_character(**changes):
    data = dict(asset_id='image',stage='final',overall=65,prompt_alignment=70,aesthetics=40,
        composition=65,anatomy=68,structure=70,hands=None,artifacts=70,style_match=65,
        nsfw_target=None,safety_score=90,decision='retry',delete_reason=[],prompt_suggestions=[],
        confidence=.9,issues=[dict(category='style_drift',severity='major',region=None,evidence='Style is too plain')],
        unassessable_fields=['hands','nsfw_target'])
    data.update(changes)
    return Evaluation(**data)


def test_new_task_and_preset_defaults_use_42():
    from supervisor.studio_features import StudioPreset
    assert TaskSettings(goal='portrait').quality_threshold == 42
    assert TaskSettings(goal='portrait').basic_pass
    assert StudioPreset(goal='portrait',groups=1).quality == 42
    assert TaskSettings.model_json_schema()['properties']['quality_threshold']['default'] == 42
    assert TaskSettings(goal='portrait',quality_threshold=73).quality_threshold == 73


@pytest.mark.parametrize('score,accepted',[(41.9,False),(42,True),(50,True),(60,True)])
def test_default_quality_gate_accepts_42_and_preserves_actual_scores(service,score,accepted):
    evaluation=normal_character(overall=score,prompt_alignment=score,aesthetics=score,
        composition=score,artifacts=score,style_match=score,anatomy=None,structure=None,
        safety_score=None,issues=[],unassessable_fields=['anatomy','structure','hands','nsfw_target','safety_score'])
    before=evaluation.model_dump()
    action,reason=service.evaluate_decision(evaluation,TaskSettings(goal='landscape',autonomous=True,content_label='sfw'))
    assert (action=='ACCEPTED') is accepted
    assert reason == ('BASIC_STRUCTURE_PASSED' if accepted else 'QUALITY_BELOW_THRESHOLD')
    assert evaluation.effective_score()==score and evaluation.model_dump()==before


@pytest.mark.parametrize('threshold',[0,41,42,59,60])
def test_basic_pass_accepts_normal_character_despite_low_cosmetic_scores(service,threshold):
    evaluation=normal_character()
    before=evaluation.model_dump()
    action,reason=service.evaluate_decision(evaluation,TaskSettings(goal='woman',autonomous=True,quality_threshold=threshold),face_required=True)
    assert action=='ACCEPTED' and reason==('BASIC_STRUCTURE_PASSED' if threshold<=42 else 'FINAL_QUALITY_PASSED')
    assert evaluation.model_dump()==before  # Real scores are retained.


@pytest.mark.parametrize('changes',[
    {'anatomy':41}, {'structure':41}, {'hands':35,'unassessable_fields':['nsfw_target']},
    {'issues':[dict(category='anatomy_error',severity='major',region='arms',evidence='Extra arm')]},
    {'anatomy':None,'unassessable_fields':['anatomy','hands','nsfw_target']},
 {'safety_score':30}, {'traditional':{'duplicate':True}},
])
def test_basic_pass_does_not_accept_deformed_uncertain_or_blocked_character(service,changes):
    settings=TaskSettings(goal='woman',autonomous=True,quality_threshold=60,content_label='sfw')
    assert service.evaluate_decision(normal_character(**changes),settings,face_required=True)[0]!='ACCEPTED'


def test_above_pass_line_retains_strict_quality_and_non_character_nulls(service):
    assert service.evaluate_decision(normal_character(),TaskSettings(goal='woman',autonomous=True,quality_threshold=85),True)[0]!='ACCEPTED'
    landscape=normal_character(anatomy=None,structure=None,issues=[],unassessable_fields=['anatomy','structure','hands','nsfw_target'])
    assert service.evaluate_decision(landscape,TaskSettings(goal='landscape',autonomous=True,quality_threshold=60))[0]=='ACCEPTED'
    wire=request_payload(Evaluation,{'asset_id':'image','acceptance_mode':'basic_structure','face_required':True})
    assert 'no obvious deformation' in wire['instruction'] and 'inflating' in wire['instruction']
    assert wire['acceptance_mode']=='basic_structure'


async def test_basic_pass_saves_first_normal_image_without_unneeded_iteration(service,tmp_path):
    async def respond(task,settings,purpose,contract,payload,images=None):
        assert purpose=='review' and payload['acceptance_mode']=='basic_structure'
        return normal_character(asset_id=payload['asset_id']),None
    service.cloud.request=AsyncMock(side_effect=respond)
    task=service.create_task(TaskSettings(goal='woman portrait',direct_prompt='woman portrait',autonomous=True,
        groups=1,per_group=1,max_rounds=3,prescreen=False,quality_threshold=42,export_folder=str(tmp_path/'saved')))
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    assert len(service.db.rows('SELECT id FROM generations'))==1
    assert len(list((tmp_path/'saved').rglob('*.png')))==1
    assert not service.db.rows("SELECT id FROM file_operations WHERE kind='direct'")
    score=json.loads(service.db.one('SELECT body FROM evaluations')['body'])
    assert score['aesthetics']==40 and score['anatomy']==68


async def test_thumbnail_review_uncertainty_continues_to_original_image_final_review(service,tmp_path):
    from test_visual_checks import checks
    calls=[]
    async def respond(task,settings,purpose,contract,payload,images=None):
        assert purpose=='review'
        calls.append((payload['stage'],images[0]))
        return normal_character(asset_id=payload['asset_id'],stage=payload['stage'],
            decision='review' if payload['stage']=='prescreen' else 'keep',
            visual_checks=checks(),issues=[]),None
    service.cloud.request=AsyncMock(side_effect=respond)
    task=service.create_task(TaskSettings(goal='clothed portrait',direct_prompt='clothed portrait',autonomous=True,
        groups=1,per_group=1,max_rounds=2,prescreen=True,export_folder=str(tmp_path/'saved')))
    await service.run(task)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(task,))['state']=='COMPLETED'
    assert [stage for stage,_ in calls]==['prescreen','final']
    assert calls[0][1]!=calls[1][1]
    assert service.db.one('SELECT COUNT(*) n FROM generations')['n']==1
    assert service.db.one("SELECT COUNT(*) n FROM decisions WHERE reason='PRESCREEN_NEEDS_REVIEW'")['n']==0
    assert len(list((tmp_path/'saved').rglob('*.png')))==1


async def test_result_page_navigation_preserves_form_and_selected_task_and_start_errors(service,tmp_path,monkeypatch):
    import gradio as gr
    from supervisor.ui import build_ui
    monkeypatch.setattr(gr,'Warning',lambda *a,**k:None)
    monkeypatch.setattr(gr,'Info',lambda *a,**k:None)
    monkeypatch.setattr('supervisor.ui.check_live_ready',AsyncMock())
    service.enqueue=lambda task:None
    app=build_ui(service)
    controls={getattr(c,'elem_id',None):c for c in app.blocks.values() if getattr(c,'elem_id',None)}
    callbacks=ui_callbacks(app)
    assert controls['studio-quality'].value==42 and controls['direct-quality'].value==42
    refresh_outputs=next(f.outputs for f in app.fns.values() if f.fn and f.fn.__name__=='refresh_all')
    assert len(callbacks['refresh_all'](None,'zh'))==len(refresh_outputs)
    failure=await callbacks['direct_create']('','',1,False,str(service.project_root/'export'),512,512,4,1,42,'sfw',60,4,1,50000,2,'euler','normal','zh')
    assert callbacks['open_results_if_started'](None,failure[1])==({'__type__':'update'},{'__type__':'update'})
    success=await callbacks['direct_create']('my unchanged prompt','blur',1,False,str(service.project_root/'export'),512,512,4,1,42,'sfw',60,4,1,50000,2,'euler','normal','zh')
    task=success[0]['value']
    opened=callbacks['open_results_if_started'](task,success[1])
    assert opened[0]['visible'] is False and opened[1]['visible'] is True
    assert callbacks['show_editor']()[0]['visible'] is True
    assert callbacks['show_editor']('prompt-studio')[2]['selected']=='prompt-studio'
    assert service.settings(task).direct_prompt=='my unchanged prompt'
    assert len(service.db.rows('SELECT id FROM tasks'))==1
    # Only generated files appear in the result page; reference images stay off that page.
    path=tmp_path/'reference.png';Image.new('RGB',(64,64),'green').save(path)
    service.files.import_image(task,path,'sfw')
    path=tmp_path/'generated.png';Image.new('RGB',(64,64),'blue').save(path)
    group=service.db.one('SELECT id FROM groups WHERE task_id=?',(task,))['id']
    asset=service.files.import_image(task,path,'sfw',group_id=group,generation_id='test-generation')
    values=callbacks['refresh_all'](task,'zh')
    assert len(values)==len(refresh_outputs) and len(values[19]['value'])==1
    assert values[19]['value'][0][0]==str(service.files.path(asset['path']))


def test_pass_marker_stays_stable_and_pointer_snap_is_narrow_without_trapping_keyboard(tmp_path):
    import shutil
    import subprocess
    from supervisor.studio_view import STUDIO_VIEW_JS
    node=shutil.which('node')
    if not node:
        pytest.skip('Node is only needed for frontend behavior verification')
    script = r'''
const assert = require('node:assert/strict');
const handlers={}; let mutations=0, redraw;
function element() {
 return {style:{},appendChild(child){this.firstChild=child;},
  get textContent(){return this.text||'';},set textContent(value){mutations++;this.text=value;}};
}
const parent={style:{},querySelector(){return this.marker||null;},appendChild(marker){this.marker=marker;}};
const input={value:'50',parentElement:parent,offsetLeft:20,offsetTop:25,clientWidth:200,clientHeight:16,
 matches(){return true;}};
global.document={body:{},querySelectorAll(){return [input];},createElement:element,
 addEventListener(name,fn){handlers[name]=fn;}};
global.window={supervisorLanguage:'zh',addEventListener(){}};
global.MutationObserver=class {constructor(fn){redraw=fn;}observe(){}};
const setup=eval(SOURCE); setup();
assert.equal(mutations,1); redraw();redraw();assert.equal(mutations,1);
assert.equal(parent.marker.firstChild.textContent,'及格线 · 42');
assert.equal(parent.marker.style.left,`${20 + 8 + (200 - 16) * .42}px`);
handlers.pointerdown({target:input});
for (const [original,expected] of [[40,40],[41,42],[42,42],[43,42],[44,44],[59,59],[60,60],[61,61]]) {
 input.value=String(original);handlers.input({target:input});assert.equal(Number(input.value),expected);
}
handlers.pointerup();input.value='41';handlers.input({target:input});assert.equal(input.value,'41');
window.supervisorLanguage='en';redraw();assert.equal(parent.marker.firstChild.textContent,'Pass · 42');
const changes=mutations;redraw();assert.equal(mutations,changes);
'''.replace('SOURCE',json.dumps(STUDIO_VIEW_JS))
    path=tmp_path/'pass-marker-test.cjs';path.write_text(script,encoding='utf-8')
    subprocess.run([node,str(path)],check=True,capture_output=True,text=True,timeout=10)

