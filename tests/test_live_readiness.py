from conftest import ui_callbacks
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from supervisor.comfy import Comfy, GenerationError
from supervisor.models import Params, PromptPlan, ProvidersConfig, StyleCard, TaskSettings
from supervisor.providers import Cloud
from supervisor.setup import infer_workflow, resolve_workflow_text
from supervisor.studio import StudioFailure, check_live_ready, task_progress


def configure_cloud(service, transport=None):
    service.config = ProvidersConfig.model_validate({"providers":[{"id":"p","name":"Test","base_url":"https://test.invalid/v1",
        "rpm":1000000,"policy":{"nsfw_policy":"forbidden","allowed_content":["sfw"]},
        "credentials":[{"id":"k","api_key_env":"TEST_KEY"}],
        "models":[{"id":"m","text":True,"vision":True,"json_schema":True,
            "pricing":{"input_per_million_micro":1,"output_per_million_micro":1,"max_call_micro":100,"source":"test","verified_at":"2026-10-01"}}]}],
        "routes":{"tagging":["p/m"],"review":["p/m"],"prompt_generation":["p/m"]}})
    service.session_keys["TEST_KEY"] = "private-test-key"
    service.cloud = Cloud(service.config,service.db,transport,service.session_keys)


def test_connected_prompts_can_be_bound_without_replacing_model_links(service):
    graph = json.loads((service.project_root / "workflows/example-api.json").read_text())
    graph["s1"] = {"class_type":"PrimitiveStringMultiline","inputs":{"value":"mountains"}}
    graph["s2"] = {"class_type":"PrimitiveString","inputs":{"value":"clear water"}}
    graph["concat"] = {"class_type":"StringConcatenate","inputs":{"string_a":["s1",0],"string_b":["s2",0],"delimiter":", "}}
    graph["6"]["inputs"]["text"] = ["concat",0]
    assert resolve_workflow_text(graph,["concat",0]) == "mountains, clear water"
    config = infer_workflow(graph,"workflows/linked.json","http://127.0.0.1:8188")
    (service.project_root / config.workflow_api_json).write_text(json.dumps(graph))
    plan = PromptPlan(group_id="g",positive="new landscape",params=Params(),reason="test")
    result = Comfy(config,service.project_root,service.db).graph(plan,"test")
    assert result["6"]["inputs"]["text"] == "new landscape"
    assert result["3"]["inputs"]["model"] == graph["3"]["inputs"]["model"]


async def test_missing_credentials_and_workflow_block_live_start(service):
    with pytest.raises(StudioFailure) as failure:
        await check_live_ready(service,"sfw",Params().model_dump())
    assert failure.value.tab == "setup"
    configure_cloud(service)
    with pytest.raises(StudioFailure,match="没有可执行工作流"):
        await check_live_ready(service,"sfw",Params().model_dump())
    service.session_keys.clear()
    with pytest.raises(StudioFailure,match="API Key"):
        await check_live_ready(service,"sfw",Params().model_dump())


async def test_studio_does_not_fall_back_to_demo(service,tmp_path,monkeypatch):
    import gradio as gr
    from supervisor.ui import build_ui
    monkeypatch.setattr(gr,"Warning",lambda *a,**kw:None)
    image = tmp_path / "reference.png"
    Image.new("RGB",(64,64)).save(image)
    app = build_ui(service)
    callback =ui_callbacks(app)['studio_create']
    result = await callback([str(image)],[],"",10,"landscape","ink","",1,"Demo",str(service.project_root/"export"),512,512,4,1,42,"sfw",80,1,2,1,"zh","euler","normal")
    assert result[4]["selected"] == "setup"
    assert "无法开始" in result[2]
    assert not service.db.rows("SELECT id FROM tasks")
    assert not list(service.data_root.rglob("*.png"))


async def test_unavailable_strict_schema_uses_json_mode_and_keeps_budget_tracking(service):
    formats=[]
    def handler(request):
        kind=json.loads(request.content)["response_format"]["type"]
        formats.append(kind)
        if kind=="json_schema":
            return httpx.Response(400,json={"error":{"message":"This response_format type is unavailable now"}})
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":StyleCard(subject=["mountains"]).model_dump_json()}}],"usage":{"prompt_tokens":1,"completion_tokens":1}})
    configure_cloud(service,httpx.MockTransport(handler))
    task=service.create_task(TaskSettings(goal="landscape",demo=False,content_label="sfw"))
    for _ in range(2):
        card,_=await service.cloud.request(task,service.settings(task),"tagging",StyleCard,{})
        assert card.subject==["mountains"]
    assert formats==["json_schema","json_object","json_object"]
    rows=service.db.rows("SELECT status,error_code FROM api_calls WHERE task_id=?",(task,))
    assert rows[0]["error_code"]=="JSON_SCHEMA_UNSUPPORTED"
    assert [r["status"] for r in rows]==["HTTP_ERROR","SUCCESS","SUCCESS"]


def test_task_error_is_in_visible_progress(service):
    task=service.create_task(TaskSettings(goal="test"))
    service.db.transition(task,"PAUSED","RECOVERABLE_ERROR","HTTP_400")
    assert "任务已停止" in task_progress(service,task)
    assert "HTTP_400" in task_progress(service,task)


async def test_schema_validation_repair_is_bounded_and_logged(service):
    requests=[]
    def handler(request):
        data=json.loads(request.content)
        payload=json.loads(data["messages"][-1]["content"][0]["text"])
        requests.append(payload)
        body={"subject":["mountains"],"unexpected":"value"} if len(requests)==1 else StyleCard(subject=["mountains"]).model_dump()
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":json.dumps(body)}}],"usage":{"prompt_tokens":1,"completion_tokens":1}})
    configure_cloud(service,httpx.MockTransport(handler))
    service.config.providers[0].retries=1
    service.config.providers[0].models[0].json_schema=False
    task=service.create_task(TaskSettings(goal="landscape",demo=False,content_label="sfw"))
    card,_=await service.cloud.request(task,service.settings(task),"tagging",StyleCard,{})
    assert card.subject==["mountains"]
    assert len(requests)==2 and "repair" in requests[1]
    assert all("input" not in error for error in requests[1]["repair"]["errors"])
    assert [r["status"] for r in service.db.rows("SELECT status FROM api_calls WHERE task_id=?",(task,))]==["INVALID_RESPONSE","SUCCESS"]
