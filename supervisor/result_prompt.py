"""Read the selected output's prompt for editing in the prompt studio."""
import json

from .models import PromptPlan


def prompt_from_result(service, request):
    body=json.loads(request)
    if not isinstance(body,dict) or any(not isinstance(body.get(key),str) or not 1 <= len(body[key]) <= 80
                                         for key in ('task_id','asset_id','request_id')):
        raise ValueError('图片选择无效，请刷新结果后重试。')
    row=service.db.one('''SELECT p.body,g.ordinal,p.round_index FROM assets a
        JOIN generations n ON n.id=a.generation_id AND n.task_id=a.task_id AND n.group_id=a.group_id
        JOIN prompt_variants p ON p.id=n.variant_id AND p.task_id=a.task_id AND p.group_id=a.group_id
        JOIN groups g ON g.id=a.group_id
        WHERE a.id=? AND a.task_id=? AND a.source_kind='generated'
        AND a.state IN ('AVAILABLE','QUARANTINED')''',(body['asset_id'],body['task_id']))
    if not row:
        raise ValueError('找不到这张图的生成提示词，请刷新结果后重试。')
    plan=PromptPlan.model_validate_json(row['body'])
    return body,plan,row['ordinal']+1,row['round_index']+1
