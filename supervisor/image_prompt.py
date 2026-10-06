"""Recover embedded prompts first; queue a vision description only as fallback."""
import json
import math
import re
from pathlib import Path

from .image_workflow import image_metadata
from .models import PromptPlan, TaskSettings, dump_json
from .setup import resolve_workflow_text


def graph_prompts(graph):
    if not isinstance(graph, dict) or not graph or not all(isinstance(node, dict)
            and isinstance(node.get('inputs'), dict) for node in graph.values()):
        return None
    visited = set()

    def visit(key):
        key = str(key)
        if key in visited or key not in graph:
            return
        visited.add(key)
        for value in graph[key]['inputs'].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                visit(value[0])

    outputs = [key for key, node in graph.items() if node.get('class_type') in ('SaveImage', 'PreviewImage')]
    for key in outputs:
        visit(key)
    candidates = visited if outputs else graph.keys()

    def conditioning(value, seen=None):
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError('Unsupported conditioning')
        key = str(value[0])
        seen = set(seen or ())
        if key in seen:
            raise ValueError('Cyclic conditioning')
        seen.add(key)
        node = graph.get(key, {})
        inputs = node.get('inputs', {})
        kind = node.get('class_type', '')
        if kind.startswith('CLIPTextEncode'):
            texts = [resolve_workflow_text(graph, inputs[field]) for field in ('text', 'text_g', 'text_l') if field in inputs]
            if texts:
                return ', '.join(dict.fromkeys(text for text in texts if text.strip()))
        if kind == 'ComfySwitchNode' and isinstance(inputs.get('switch'), bool):
            return conditioning(inputs.get('on_true' if inputs['switch'] else 'on_false'), seen)
        if kind in ('FluxGuidance', 'ConditioningSetArea', 'ConditioningSetAreaPercentage', 'ConditioningSetMask'):
            return conditioning(inputs.get('conditioning'), seen)
        raise ValueError('Unsupported conditioning')

    prompts = []
    for key in candidates:
        node = graph[key]
        inputs = node['inputs']
        if node.get('class_type') not in ('KSampler', 'KSamplerAdvanced', 'BasicGuider', 'CFGGuider'):
            continue
        try:
            positive = conditioning(inputs.get('positive', inputs.get('conditioning')))
            negative = conditioning(inputs['negative']) if 'negative' in inputs else ''
        except (ValueError, TypeError, RecursionError):
            continue
        if positive.strip():
            pair = (positive, negative)
            if pair not in prompts:
                prompts.append(pair)
    # Never select an arbitrary branch when multiple outputs have different prompts.
    return prompts[0] if len(prompts) == 1 else None


def embedded_prompts(image):
    values = image_metadata(image)
    positive = values.get('positive') or values.get('positive_prompt')
    if positive and positive.strip():
        return positive, values.get('negative') or values.get('negative_prompt') or ''
    for key in ('prompt', 'workflow'):
        raw = values.get(key)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            # A plain prompt text is a common metadata convention.
            if key == 'prompt' and raw.strip() and not raw.lstrip().startswith(('{', '[')):
                return raw, values.get('negative') or values.get('negative_prompt') or ''
            continue
        if isinstance(data, dict):
            positive = data.get('positive') or data.get('positive_prompt')
            negative = data.get('negative', data.get('negative_prompt', ''))
            if isinstance(positive, str) and positive.strip() and isinstance(negative, str):
                return positive, negative
        pair = graph_prompts(data)
        if pair:
            return pair
    parameters = values.get('parameters')
    if parameters:
        body = re.split(r'\nSteps:\s*\d', parameters, maxsplit=1)[0]
        positive, separator, negative = body.partition('\nNegative prompt:')
        if positive.strip():
            return positive.strip(), negative.strip() if separator else ''
    return None


def enqueue_image_prompt(service, image, scope, usd, token_limit):
    if not math.isfinite(float(usd)) or float(usd) < 0:
        raise ValueError('模型反推预算须为非负数。')
    settings = TaskSettings(goal='输入图片识别', demo=False, reference_only=True,
        content_label=scope, groups=1, budget_micro=int(float(usd)*1_000_000), token_budget=token_limit)
    routes = service.cloud.candidates('image_prompt', scope, 1, 'USD')
    if not routes:
        raise ValueError('图片没有可读取的提示词；请先保存支持视觉能力的 API 连接。')
    if not any(service.cloud.credential_key(credential) for _, _, credential in routes):
        raise ValueError('未找到 API Key，请先保存 API 连接。')
    with service.active_lock:
        task_id = service.create_task(settings, [Path(image)], start=False)
        asset = service.db.one("SELECT id FROM assets WHERE task_id=? AND source_kind='reference'", (task_id,))
        if not asset:
            service.db.transition(task_id, 'FAILED', 'IMAGE_PROMPT', 'INVALID_IMAGE')
            raise ValueError('无法读取图片，请使用 PNG、JPEG 或 WebP。')
        service.db.execute('INSERT INTO image_prompt_jobs(task_id,asset_id) VALUES(?,?)', (task_id, asset['id']))
        service.db.transition(task_id, 'RUNNING', 'QUEUED')
    service.enqueue(task_id)
    return task_id


async def run_image_prompt(service, task_id, settings):
    job = service.db.one('SELECT * FROM image_prompt_jobs WHERE task_id=?', (task_id,))
    if not job['result']:
        service.check_round(task_id)
        reason = service.limit_reason(task_id, settings)
        if reason:
            service.db.transition(task_id, 'PAUSED', 'IMAGE_PROMPT', reason)
            return
        asset = service.db.one('SELECT path FROM assets WHERE id=? AND task_id=?', (job['asset_id'], task_id))
        group = service.db.one('SELECT id FROM groups WHERE task_id=? ORDER BY ordinal LIMIT 1', (task_id,))
        service.db.transition(task_id, 'RUNNING', 'IMAGE_PROMPT')
        plan, _ = await service.cloud.request(task_id, settings, 'image_prompt', PromptPlan,
            {'group_id': group['id'], 'image_caption': True, 'content_scope': settings.content_label},
            [service.files.path(asset['path'])])
        if not plan.positive.strip():
            from .providers import CloudError
            raise CloudError('INVALID_RESPONSE_PROMPT_REQUIRED')
        service.db.execute('UPDATE image_prompt_jobs SET result=? WHERE task_id=?',
            (dump_json({'positive': plan.positive, 'negative': plan.negative}), task_id))
    # Persist before the final transition, so restart never repeats a saved call.
    service.db.transition(task_id, 'COMPLETED', 'IMAGE_PROMPT', 'IMAGE_PROMPT_COMPLETE')
