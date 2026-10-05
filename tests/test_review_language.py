import json

import httpx
import pytest

from supervisor.models import Evaluation,TaskSettings
from supervisor.studio_features import result_review_html
from test_live_readiness import configure_cloud
from test_review_format import review
from test_result_batch import source_task


@pytest.mark.parametrize('language,economical',[('zh',True),('en',True),('zh',False),('en',False)])
async def test_review_language_reaches_both_provider_formats(service,language,economical):
    bodies=[]
    settings=TaskSettings(goal='landscape',demo=False,reference_only=economical,content_label='sfw')
    task=service.create_task(settings,start=False)
    payload={'asset_id':'image','stage':'final'}
    from supervisor.providers import Cloud
    reply=review() if economical else Cloud.demo(Evaluation,'review',{**payload,'round_index':2}).model_dump()
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(reply)}}],
            'usage':{'prompt_tokens':30,'completion_tokens':30}})
    configure_cloud(service,httpx.MockTransport(handler))
    service.db.execute("INSERT OR REPLACE INTO runtime_state VALUES('review_language',?)",(language,))
    await service.cloud.request(task,settings,'review',Evaluation,payload)
    role=bodies[0]['messages'][0]['content']
    assert ('Simplified Chinese' if language=='zh' else 'English') in role
    assert 'enum values' in role and len(bodies)==1


async def test_localized_review_preserves_original_scores_and_prompt(service,tmp_path):
    old,assets=await source_task(service,tmp_path)
    from supervisor.providers import Cloud
    evaluation=Cloud.demo(Evaluation,'review',{'asset_id':assets[0]['id'],'stage':'final','round_index':2})
    row=service.save_evaluation(assets[0],evaluation,None,'final')
    original=service.db.one('SELECT body FROM evaluations WHERE id=?',(row['id'],))['body']
    translated={'issues':[{'evidence':'远山轮廓不清晰'}],
        'prompt_suggestions':[{'suggestion':'加强远山轮廓，保持当前光照'}]}
    service.db.execute('INSERT INTO review_localizations VALUES(?,?,?)',(row['id'],'zh',json.dumps(translated,ensure_ascii=False)))
    html=result_review_html(service,old,'zh')
    assert '远山轮廓不清晰' in html and '加强远山轮廓' in html
    assert '远山轮廓不清晰' not in result_review_html(service,old,'en')
    assert 'mountains, (light:1.2)' in html
    assert service.db.one('SELECT body FROM evaluations WHERE id=?',(row['id'],))['body']==original


async def test_progress_refresh_preserves_unchanged_gallery_and_review_dom(service,tmp_path):
    from supervisor.ui import build_ui
    old,assets=await source_task(service,tmp_path)
    app=build_ui(service)
    refresh=next(f.fn for f in app.fns.values() if getattr(f.fn,'__name__','')=='refresh_all')
    initial=refresh(old,'zh')
    unchanged=refresh(old,'zh',True,assets[0]['id'],initial[-1])
    assert all(unchanged[index]=={'__type__':'update'} for index in (19,23,25))
    service.db.execute("UPDATE tasks SET updated_at='2099-01-01T00:00:00' WHERE id=?",(old,))
    progress_only=refresh(old,'zh',True,None,initial[-1])
    assert all(progress_only[index]=={'__type__':'update'} for index in (19,23,25))
    service.delete_generated(old,assets[0]['id'])
    changed=refresh(old,'zh',True,None,initial[-1])
    assert len(changed[19]['value'])==1 and changed[19]['selected_index'] is None
    assert assets[0]['id'] not in json.loads(changed[25])['images'][0]['asset_id']
