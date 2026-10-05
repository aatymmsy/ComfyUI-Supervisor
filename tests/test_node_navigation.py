from conftest import ui_callbacks
import json

import httpx
import pytest
from pydantic import ValidationError

from supervisor.models import StyleCard, TaskSettings
from supervisor.node_editor_view import node_table, parameter_tabs
from supervisor.wire import ReferenceReply, request_payload
from test_live_readiness import configure_cloud
from test_workflow_editor import configure_editor


@pytest.mark.parametrize('count', [0, 3, 4])
async def test_reference_detail_tags_succeed_without_retry_or_loss(service, count):
    details = ['glass reflection', 'fabric texture', 'wet stone', 'soft highlights'][:count]
    requests = []
    def handler(request):
        requests.append(request)
        data = dict(camera=['wide shot'], pose=['standing'], composition='layered', lighting='daylight', detail=details)
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}], 'usage':{'prompt_tokens':100,'completion_tokens':60}})
    configure_cloud(service, httpx.MockTransport(handler))
    settings = TaskSettings(goal='portrait', demo=False, reference_only=True, content_label='sfw')
    task = service.create_task(settings, start=False)
    card, _ = await service.cloud.request(task,settings,'tagging',StyleCard,{'asset_ids':['reference']})
    assert len(requests) == 1
    assert card.detail == details and card.subject == []
    assert card.reference_images == ['reference']
    assert service.db.one('SELECT status FROM api_calls')['status'] == 'SUCCESS'
    assert service.db.token_usage(task) == 160


def test_reference_detail_limit_is_explicit_and_invalid_values_still_rejected():
    data = dict(camera=[],pose=[],composition='layers',lighting='daylight',detail=[])
    assert 'At most 4' in request_payload(StyleCard,{})['instruction']
    for details in (['tag']*5, [{'not':'a tag'}], ['x'*101]):
        with pytest.raises(ValidationError):
            ReferenceReply.model_validate({**data, 'detail':details})


async def test_grouped_navigation_selects_one_field_and_escapes_workflow_text(service):
    from supervisor.ui import build_ui
    configure_editor(service)
    app = build_ui(service)
    callbacks =ui_callbacks(app)
    table, _, rows, _ = await callbacks['read_editor']()
    assert table.count('<tr class=') == len({r['node_id'] for r in rows})
    assert table.count('<details>') == len({r['node_id'] for r in rows})
    selected = callbacks['select_editor']('3:cfg',rows)
    assert selected[1]['visible'] and selected[1]['value'] == 7
    assert not selected[0]['visible'] and not selected[2]['visible'] and not selected[3]['visible']
    assert '3:steps' in selected[4] and '4:ckpt_name' not in selected[4]
    unsafe = [{'node_id':'<bad>', 'class_type':'<script>x</script>', 'input':'<input>', 'key':'n:"key', 'value':'<img onerror=alert(1)>'}]
    assert '<script>' not in node_table(unsafe) and '<img' not in node_table(unsafe)
    assert 'data-editor-key="n:&quot;key"' in parameter_tabs(unsafe,'n:"key')
