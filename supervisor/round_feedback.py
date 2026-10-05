"""Carry general corrective directions across one task, without copying scenes."""
import json
import re

DIRECTIONS = {
 'face':'When faces are meant to be visible, specify clear facial landmarks, iris/pupil detail and readable eyes. Respect intentionally hidden faces or closed eyes.',
 'hands':'For visible hands, specify clear separated fingers, coherent wrists and readable contact/grip relationships. Avoid overlapping or fused fingers.',
 'geometry':'Keep coherent body/object proportions, complete connected limbs and joints, and clear spatial relationships. For multiple people, specify who owns each visible arm, hand, leg and contact point; separate bodies and avoid fused, duplicated, extra or genuinely missing parts. Respect intentional occlusion and framing.',
 'clarity':'Describe a clear focal subject, distinct contours and readable fine detail; avoid unintended blur or smudged focal features.',
 'artifacts':'Separate overlapping objects and materials; avoid merged edges, stray fragments and incoherent textures.',
 'composition':'Make this group’s intended framing and spatial relationships explicit. Avoid unintended cropping and obstructed focal details.',
 'alignment':'Recheck this group’s user controls and theme; express their visible requirements clearly and resolve contradictions.',
 'style':'Keep this group’s requested style explicit and consistent; do not inherit another group’s style.',
 'text':'If readable text is requested, make lettering and its placement explicit; avoid unwanted or garbled text.'
}


def round_feedback(service,task_id):
    cache=getattr(service,'_round_feedback_cache',None)
    if cache is None:
        cache={};service._round_feedback_cache=cache
    state=cache.setdefault(task_id,{'rowid':0,'counts':{},'latest_at':''})
    rows=service.db.rows("SELECT e.rowid,e.body,e.created_at FROM evaluations e JOIN assets a ON a.id=e.asset_id WHERE a.task_id=? AND e.stage='final' AND e.rowid>? ORDER BY e.rowid",(task_id,state['rowid']))
    for row in rows:
        evaluation=json.loads(row['body'])
        focuses=set()
        checks=evaluation.get('visual_checks') or {}
        if any(checks.get(key) is True for key in ('extra_limbs','missing_parts','fused_bodies','disconnected_parts','duplicated_body')):
            focuses.add('geometry')
        for issue in evaluation.get('issues',[]):
            text=str(issue.get('evidence',''))+' '+str(issue.get('region',''))
            category=issue.get('category')
            if re.search(r'face|facial|eyes?|pupils?|iris|面部|五官|瞳|眼睛',text,re.I): focuses.add('face')
            if re.search(r'hands?|fingers?|wrists?|手部|手指|手腕',text,re.I): focuses.add('hands')
            focus={'anatomy_error':'geometry','blur':'clarity','artifacts':'artifacts',
                'composition_failure':'composition','target_mismatch':'alignment','style_drift':'style'}.get(category)
            if focus: focuses.add(focus)
            if re.search(r'lettering|illegible|garbled text|文字|字形|乱码',text,re.I): focuses.add('text')
        if not focuses and evaluation.get('prompt_suggestions'):
            focuses.add('clarity')
        new_focus=focuses-set(state['counts'])
        for focus in focuses:
            state['counts'][focus]=state['counts'].get(focus,0)+1
        if new_focus:
            state['latest_at']=row['created_at']
        state['rowid']=row['rowid']
    return {'latest_at':state['latest_at'], 'directions':[{'focus':key,'observations':count,
        'direction':DIRECTIONS[key]} for key,count in sorted(state['counts'].items(),key=lambda item:(-item[1],item[0]))]}
