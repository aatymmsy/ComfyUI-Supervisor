import json
from pathlib import Path

import httpx
import pytest

from supervisor.setup import ConnectionFailure, validate_connection_form, test_connection as probe_connection, detect_workflow, infer_workflow, workflow_summary


def test_missing_fields_are_reported_together(service):
    with pytest.raises(ConnectionFailure) as error:
        validate_connection_form(service, "", "", "", False, False, "")
    assert set(error.value.fields) == {"key", "model", "endpoint", "vision_confirmed", "scope_confirmed"}


async def test_probe_checks_selected_models_and_vision(service):
    calls = []
    def handler(request):
        assert str(request.url) == "https://example.test/v1/chat/completions"
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})
    key = await probe_connection(service, "secret-test", "text", "https://example.test/v1", "vision", transport=httpx.MockTransport(handler))
    assert key == "secret-test"
    assert [c["model"] for c in calls] == ["text", "vision"]
    assert len(calls[0]["messages"][0]["content"]) == 1
    assert calls[1]["messages"][0]["content"][1]["type"] == "image_url"


async def test_probe_rejects_invalid_key_without_disclosing_response(service):
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "secret-test"}))
    with pytest.raises(ConnectionFailure) as error:
        await probe_connection(service, "secret-test", "model", "https://example.test/v1", "", transport=transport)
    assert error.value.fields == ["key"]
    assert "401" in str(error.value)
    assert "secret-test" not in str(error.value)


async def test_probe_does_not_accept_http_200_with_invalid_output(service):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "plain text"}}]}))
    with pytest.raises(ConnectionFailure, match="JSON"):
        await probe_connection(service, "key", "model", "https://example.test/v1", "", transport=transport)


async def test_save_feedback_and_atomic_failure(service, tmp_path, monkeypatch):
    from supervisor.ui import build_ui
    import gradio as gr
    service.provider_path = tmp_path / "providers.yaml"
    notifications = []
    monkeypatch.setattr(gr, "Info", lambda message, **kwargs: notifications.append(("success", message)))
    monkeypatch.setattr(gr, "Warning", lambda message, **kwargs: notifications.append(("failure", message)))
    app = build_ui(service)
    callback = next(f.fn for f in app.fns.values() if getattr(f.fn, "__name__", "") == "save_api_form")
    args = ["custom", "private-connection-token", "model", "https://example.test/v1", "", True, False, 0, 0, .05, "sfw", True, "", False, "zh"]
    service.cloud.transport = httpx.MockTransport(lambda r: httpx.Response(401))
    failed = await callback(*args)
    assert "连接失败" in failed[2]
    assert json.loads(failed[3])["fields"] == ["key"]
    assert not service.provider_path.exists()
    service.cloud.transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]}))
    saved = await callback(*args)
    assert "连接成功" in saved[2]
    assert saved[1] == "" and saved[3] == "[]"
    assert service.provider_path.exists()
    assert "private-connection-token" not in service.provider_path.read_text()
    assert [n[0] for n in notifications] == ["failure", "success"]


async def test_unsupported_bindings_still_show_model_parameters(service):
    graph = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "actual-model.safetensors", "weight_dtype": "default"}},
             "2": {"class_type": "LoraLoader", "inputs": {"lora_name": "detail.safetensors", "strength_model": .6, "strength_clip": 1., "model": ["1", 0]}},
             "3": {"class_type": "VAELoader", "inputs": {"vae_name": "actual-vae.safetensors"}}}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"queue_running": [[1, "prompt", graph]], "queue_pending": []}))
    config, rows, status = await detect_workflow(service, "http://127.0.0.1:8188", transport)
    assert config is None
    assert len(rows) == 3
    assert "actual-model.safetensors" in rows[0][2]
    assert '"strength_model": 0.6' in rows[1][3]
    assert "已识别" in status


def test_latent_switch_binds_only_selected_branch():
    graph = json.loads((Path(__file__).resolve().parents[1] / "workflows/example-api.json").read_text())
    graph["other"] = {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 512, "batch_size": 1}}
    graph["switch"] = {"class_type": "ComfySwitchNode", "inputs": {"switch": True, "on_true": ["other", 0], "on_false": ["5", 0]}}
    graph["3"]["inputs"]["latent_image"] = ["switch", 0]
    config = infer_workflow(graph, "test.json", "http://127.0.0.1:8188")
    assert config.bindings["width"][0].node_id == "other"


def test_linked_model_filename_is_resolved():
    graph = {"1": {"class_type": "PrimitiveString", "inputs": {"value": "model.safetensors"}},
             "2": {"class_type": "UNETLoader", "inputs": {"unet_name": ["1", 0], "weight_dtype": "default"}}}
    assert "model.safetensors" in workflow_summary(graph)[0][2]
