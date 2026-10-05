import json
from PIL import Image

from supervisor.db import uid, now
from supervisor.models import TaskSettings, StyleCard, PromptPlan, Evaluation, Issue
from supervisor.providers import Cloud
from supervisor.result_history import result_history
from supervisor.round_feedback import round_feedback
from supervisor.wire import request_payload


def seed_groups(service, image, count, each=2):
    task=service.create_task(TaskSettings(goal='mountain landscape',groups=count,demo=False),start=False)
    groups=service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(task,))
    for group in groups:
        for _ in range(each):
            service.files.import_image(task,image,'sfw',group['id'],'fixture-generation')
    return task,groups


def test_history_220_images_caps_original_gallery_without_deleting_assets(service,tmp_path):
    image=tmp_path/'mountain.png';Image.new('RGB',(64,64),'steelblue').save(image)
    for _ in range(4):
        seed_groups(service,image,20)
    task,groups=seed_groups(service,image,3,each=20)
    gallery,review,mapping=result_history(service,task)
    assert len(gallery)==len(mapping['images'])==50
    assert 'groups' not in mapping and mapping['image_limit']==50
    assert service.db.one("SELECT COUNT(*) n FROM assets WHERE source_kind='generated'")['n']==220
    assert all(r['task_id']==task for r in mapping['images'])
    newest,_=seed_groups(service,image,1)
    gallery,_,mapping=result_history(service,task)
    assert len(gallery)==50 and all(r['task_id']==newest for r in mapping['images'][:2])
    assert len({r['asset_id'] for r in mapping['images']})==50
    gallery,_,mapping=result_history(service,task,include_history=False)
    assert len(gallery)==50 and all(r['task_id']==task for r in mapping['images'])
    newest_rows=service.db.rows('SELECT id FROM assets WHERE task_id=?',(newest,))
    assert [r['asset_id'] for r in result_history(service,newest,include_history=False)[2]['images']]==[r['id'] for r in reversed(newest_rows)]


async def test_later_group_planning_learns_review_directions_preserving_own_controls(service,tmp_path):
    settings=TaskSettings(goal='independent landscapes',autonomous=True,demo=False,groups=2,
        target_styles=['lake sunrise','pine forest'],control_words=[{'word':'mist','group':2}])
    image=tmp_path/'landscape.png';Image.new('RGB',(64,64)).save(image)
    task=service.create_task(settings,[image],start=False)
    reference=service.db.one("SELECT * FROM assets WHERE task_id=? AND source_kind='reference'",(task,))
    metadata=json.loads(reference['metadata']);metadata['model_tags']=StyleCard().model_dump()
    service.db.execute('UPDATE assets SET metadata=? WHERE id=?',(json.dumps(metadata),reference['id']))
    requests=[]
    async def cloud(task_id,settings,purpose,contract,payload,images=None):
        requests.append(payload)
        return PromptPlan(group_id=payload['group_id'],positive=payload['target_style'],reason='mock'),None
    service.cloud.request=cloud
    await service.prepare(task,settings)
    groups=service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(task,))
    assert len(requests)==1 and not requests[0]['round_improvements']
    assert not service.db.one('SELECT id FROM prompt_variants WHERE group_id=?',(groups[1]['id'],))
    image=tmp_path/'landscape.png';Image.new('RGB',(64,64)).save(image)
    asset=service.files.import_image(task,image,'sfw',groups[0]['id'],'fixture-generation')
    evaluation=Cloud.demo(Evaluation,'review',{'asset_id':asset['id'],'round_index':2})
    evaluation.issues=[Issue(category='blur',severity='minor',region=None,evidence='UNIQUE_LAKE_SCENE has smudged focal contours')]
    service.save_evaluation(asset,evaluation,None,'final')
    await service.plan(task,settings,groups[1],first=True)
    last=requests[-1]
    assert last['target_style']=='pine forest' and last['control_words'][0]['word']=='mist'
    assert last['round_improvements'][0]['focus']=='clarity'
    assert 'UNIQUE_LAKE_SCENE' not in json.dumps(last) and not last['latest_reviews']
    wire=request_payload(PromptPlan,last)
    assert wire['round_improvements']==last['round_improvements']
    expected=round_feedback(service,task)
    del service._round_feedback_cache
    assert round_feedback(service,task)==expected
    other=service.create_task(settings,[image],start=False)
    assert not round_feedback(service,other)['directions']


def test_feedback_aggregates_final_reviews_once_and_recognizes_chinese_details(service,tmp_path):
    image=tmp_path/'face.png';Image.new('RGB',(64,64)).save(image)
    task,groups=seed_groups(service,image,1,1)
    asset=service.db.one('SELECT * FROM assets WHERE task_id=?',(task,))
    evaluation=Cloud.demo(Evaluation,'review',{'asset_id':asset['id'],'round_index':2})
    evaluation.issues=[Issue(category='anatomy_error',severity='major',region=None,evidence='五官与眼睛模糊，手指融合')]
    evaluation.stage='prescreen'
    service.save_evaluation(asset,evaluation,None,'prescreen')
    assert not round_feedback(service,task)['directions']
    evaluation.stage='final'
    service.save_evaluation(asset,evaluation,None,'final')
    shared=round_feedback(service,task)
    assert {r['focus'] for r in shared['directions']}=={'face','hands','geometry'}
    assert round_feedback(service,task)==shared
    assert all(r['observations']==1 for r in shared['directions'])
    service.save_evaluation(asset,evaluation,None,'final')
    repeated=round_feedback(service,task)
    assert repeated['latest_at']==shared['latest_at']
    assert all(r['observations']==2 for r in repeated['directions'])
