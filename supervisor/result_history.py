"""Browse the latest fifty images using the original gallery."""
import time
from .control_words import character_for_group

from .studio_features import result_review_html, unreviewed_status

IMAGE_LIMIT = 50


def result_history(service,task_id,lang='zh',include_history=True):
    rows=service.db.rows('''SELECT a.*,g.ordinal,g.state group_state,p.round_index,n.state generation_state,d.action,
        d.evaluation_id,e.effective_score,t.updated_at task_updated,t.state task_state,t.reason task_reason,
        COALESCE(json_extract(t.settings,'$.review_enabled'),1) review_enabled,
        json_extract(t.settings,'$.character') character FROM assets a
        JOIN tasks t ON t.id=a.task_id LEFT JOIN groups g ON g.id=a.group_id
        LEFT JOIN generations n ON n.id=a.generation_id LEFT JOIN prompt_variants p ON p.id=n.variant_id
        LEFT JOIN decisions d ON d.asset_id=a.id AND d.rowid=(SELECT MAX(rowid) FROM decisions WHERE asset_id=a.id)
        LEFT JOIN evaluations e ON e.id=d.evaluation_id
        WHERE a.source_kind='generated' AND a.state IN ('AVAILABLE','QUARANTINED')
        AND json_extract(t.settings,'$.demo')=0 AND (? OR a.task_id=?)
        ORDER BY a.created_at DESC,a.rowid DESC LIMIT ?''',(bool(include_history),task_id,IMAGE_LIMIT))
    signature=(task_id,lang,bool(include_history),
        tuple((row['id'],row['state'],row['action'],row['evaluation_id'],row['generation_state'],row['task_updated'],row['group_state']) for row in rows))
    cache=getattr(service,'_result_history_cache',None)
    if cache and cache['signature']==signature and time.monotonic()-cache['time']<10:
        return cache['value']
    from .appearance import translate
    images,records,owners=[],[],[]
    settings_by_task={}
    for row in rows:
        path=service.files.path(row['path'])
        if not path.is_file():
            continue
        if row['task_id'] not in owners:
            owners.append(row['task_id'])
        group=row['ordinal']+1 if row['ordinal'] is not None else '?'
        attempt=row['round_index']+1 if row['round_index'] is not None else '?'
        caption=(f"任务 {row['task_id'][:8]} · 第 {group} 组 · 第 {attempt} 轮" if lang=='zh' else f"Task {row['task_id'][:8]} · Group {group} · Attempt {attempt}")
        if row['task_id'] not in settings_by_task:
            settings_by_task[row['task_id']]=service.settings(row['task_id'])
        character=character_for_group(settings_by_task[row['task_id']],row['ordinal'] or 0)
        if character:
            caption+=' · '+character
        pending_label,_=unreviewed_status({'state':row['task_state'],'reason':row['task_reason']},row['generation_state'],row['review_enabled'],row['group_state']=='CANCELLED')
        action_label=translate(row['action'] or 'pending',lang)
        if settings_by_task[row['task_id']].autonomous and row['action'] in ('REVIEW','RECHECK','CANDIDATE'):
            action_label=('待自动判定' if row['action']=='CANDIDATE' else '待自动复核') if lang=='zh' else 'Automatic review pending'
        caption+=' · '+(pending_label if not row['evaluation_id'] and not row['action'] and pending_label.startswith('未评审') else action_label)
        if row['effective_score'] is not None:
            caption+=f" · {row['effective_score']:.1f}"
        images.append((str(path),caption))
        records.append({'asset_id':row['id'],'task_id':row['task_id'],'group_id':row['group_id'],
            'filename':path.name,'caption':caption})
    review=result_review_html(service,task_id,lang,task_ids=owners,asset_ids=[row['asset_id'] for row in records]) if owners else '<p>尚无生成图片。</p>'
    value=(images,review,{'task_id':task_id,'images':records,'review_html':review,'image_limit':IMAGE_LIMIT})
    service._result_history_cache={'signature':signature,'time':time.monotonic(),'value':value}
    return value
