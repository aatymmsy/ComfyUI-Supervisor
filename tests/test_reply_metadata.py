import json

import httpx
import pytest
from pydantic import ValidationError

from supervisor.models import Evaluation, PromptPlan, StyleCard, TaskSettings
from supervisor.providers import CloudError, strict_schema
from supervisor.studio import task_progress
from supervisor.wire import PromptReply, ReviewReply, TagsReply
from test_live_readiness import configure_cloud
from test_review_format import review


@pytest.mark.parametrize('contract,data', [
    (PromptReply, {'positive': 'woman reading, detailed open eyes', 'negative': 'blur'}),
    (TagsReply, {'subject': ['woman'], 'style': [], 'composition': 'portrait',
                 'lighting': 'daylight', 'detail': [], 'negative': []}),
    (ReviewReply, review()),
])
def test_textual_type_metadata_does_not_change_reply_or_requested_schema(contract, data):
    wrapped = {**data, 'type': 'model_reply'}
    assert contract.model_validate(wrapped) == contract.model_validate(data)
    assert wrapped['type'] == 'model_reply'
    assert 'type' not in strict_schema(contract)['properties']
    assert strict_schema(contract)['additionalProperties'] is False
    with pytest.raises(ValidationError):
        contract.model_validate({**wrapped, 'unexpected': 'must still fail'})
    with pytest.raises(ValidationError):
        contract.model_validate({**data, 'type': {'positive': 'hidden content'}})


async def test_prompt_with_type_metadata_succeeds_once_preserving_local_params(service):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
                'type': 'prompt', 'positive_prompt': 'woman reading, detailed open eyes',
                'negative_prompt': 'blur'})}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50}})
    configure_cloud(service, httpx.MockTransport(handler))
    service.config.providers[0].models[0].json_schema = False
    settings = TaskSettings(goal='portrait', demo=False, reference_only=True, content_label='sfw')
    task = service.create_task(settings, start=False)
    plan, _ = await service.cloud.request(task, settings, 'prompt_generation', PromptPlan,
                                        {'group_id': 'local', 'params': {'seed': 42, 'steps': 20}})
    assert len(requests) == 1
    assert plan.positive == 'woman reading, detailed open eyes' and plan.negative == 'blur'
    assert plan.group_id == 'local' and plan.params.seed == 42 and plan.params.steps == 20
    assert service.db.one('SELECT status FROM api_calls')['status'] == 'SUCCESS'
    assert service.db.token_usage(task) == 150


@pytest.mark.parametrize('purpose,contract,code,label', [
    ('prompt_generation', PromptPlan, 'INVALID_RESPONSE_PROMPT_REQUIRED', '提示词 JSON'),
    ('tagging', StyleCard, 'INVALID_RESPONSE_TAGGING_REQUIRED', '参考图标签 JSON'),
    ('review', Evaluation, 'INVALID_RESPONSE_REVIEW_REQUIRED', '评审 JSON'),
])
@pytest.mark.parametrize('content', ['{"unexpected":1}', 'not json'])
async def test_invalid_replies_report_the_failed_stage(service, purpose, contract, code, label, content):
    configure_cloud(service, httpx.MockTransport(lambda request: httpx.Response(200, json={
        'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
        'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})))
    service.config.providers[0].retries = 0
    settings = TaskSettings(goal='test', demo=False, reference_only=True, content_label='sfw')
    task = service.create_task(settings, start=False)
    with pytest.raises(CloudError, match=code):
        await service.cloud.request(task, settings, purpose, contract, {})
    service.db.transition(task, 'PAUSED', 'RECOVERABLE_ERROR', code)
    assert label in task_progress(service, task)
    # Existing paused tasks must also show the correct explanation.
    service.db.transition(task, 'PAUSED', 'RECOVERABLE_ERROR', 'INVALID_RESPONSE_REVIEW_REQUIRED')
    assert label in task_progress(service, task)
