import json
from pathlib import Path

import httpx
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from supervisor.comfy import Comfy, demo_image
from supervisor.db import uid
from supervisor.files import sha256
from supervisor.models import Evaluation, PromptPlan, StyleCard, TaskSettings, WorkflowConfig, load_yaml
from supervisor.providers import Cloud, CloudError
from supervisor.setup import endpoint_parts, infer_workflow, save_provider, save_workflow
from supervisor.wire import ReferenceReply, PromptReply, ReviewReply
from test_supervisor import cloud_config


@pytest.mark.parametrize("live", [False, True])
async def test_automatic_reference_to_reviewed_groups(service, tmp_path, monkeypatch, live):
    config = load_yaml(service.project_root / "config/workflow.example.yaml", WorkflowConfig)
    graph = json.loads((service.project_root / config.workflow_api_json).read_text())
    graph["6"]["inputs"]["text"] = "subject first, (soft lighting:1.2), fine details"
    reference = tmp_path / "reference.png"
    info = PngInfo()
    info.add_text("prompt", json.dumps(graph))
    Image.new("RGB", (64, 64), "green").save(reference, pnginfo=info)
    calls, rendered, posted = [], {}, []

    async def no_socket(*args, **kwargs):
        raise OSError("mock WebSocket unavailable")

    monkeypatch.setattr("supervisor.comfy.websockets.connect", no_socket)
    monkeypatch.setenv("TEST_SUPERVISOR_KEY", "studio-secret")

    def handler(request):
        if request.url.host == "test.invalid":
            body = json.loads(request.content)
            message = body["messages"][1]["content"]
            data = json.loads(message[0]["text"])
            calls.append(data)
            purpose, payload = data["purpose"], data["data"]
            contract = {"tagging":StyleCard, "prompt_generation":PromptPlan, "review":Evaluation}[purpose]
            result = Cloud.demo(contract, purpose, payload)
            if purpose == "prompt_generation" and payload.get("current"):
                result.positive = "redesigned composition, " + payload["target_style"]
                result.changes[0].field = "composition"
            if purpose == "tagging":
                result = ReferenceReply(camera=result.camera,pose=result.pose,composition=", ".join(result.composition),lighting=", ".join(result.lighting),detail=result.detail)
            elif purpose == "prompt_generation":
                result = PromptReply(positive=result.positive,negative=result.negative)
            else:
                result = ReviewReply(scores={"alignment":result.prompt_alignment,"aesthetics":result.aesthetics,
                    "composition":result.composition,"anatomy":result.anatomy,"structure":result.structure,
                    "hands":result.hands,"text":result.text_quality,"artifacts":result.artifacts,
                    "style":result.style_match,"safety":result.safety_score,"nsfw":result.nsfw_target},
                    decision=result.decision,
                    problems=[{"category":issue.category,"severity":issue.severity,"evidence":issue.evidence} for issue in result.issues],advice="Improve composition" if result.issues else "")
            return httpx.Response(200, json={"choices":[{"finish_reason":"stop", "message":{"content":result.model_dump_json()}}], "usage":{"prompt_tokens":10, "completion_tokens":10}})
        if request.url.path == "/prompt":
            graph = json.loads(request.content)["prompt"]
            posted.append(graph)
            seed = graph["3"]["inputs"]["seed"]
            ordinal, round_index = divmod(seed - 42, 100003)
            prompt_id = uid()
            path = tmp_path / (prompt_id + ".png")
            demo_image(path, PromptPlan(group_id="test", positive="reference subject", reason="test", params={"seed":seed}), round_index, ordinal)
            rendered[prompt_id] = path.read_bytes()
            return httpx.Response(200, json={"prompt_id":prompt_id})
        if request.url.path.startswith("/history/"):
            prompt_id = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={prompt_id:{"status":{"completed":True}, "outputs":{"9":{"images":[{"filename":prompt_id + ".png", "type":"output"}]}}}})
        if request.url.path == "/view":
            return httpx.Response(200, content=rendered[request.url.params["filename"].removesuffix(".png")])
        return httpx.Response(404)

    if live:
        transport = httpx.MockTransport(handler)
        service.cloud = Cloud(cloud_config(), service.db, transport)
        service.comfy = Comfy(config, service.project_root, service.db, transport)
    output_root = tmp_path / "generated"
    task_id = service.create_task(TaskSettings(goal="reference subject", autonomous=True, demo=not live,
        content_label="sfw", groups=2, per_group=1, quality_threshold=80, target_styles=["ink painting", "cinematic photography"],
        prompt_examples="subject, medium, (light:1.2)", export_folder=str(output_root), params={"seed":42}), [reference])
    await service.run(task_id)
    assert service.db.one("SELECT state FROM tasks WHERE id=?", (task_id,))["state"] == "COMPLETED"
    assert len(service.db.accepted(task_id)) == 2
    assert not service.db.rows("SELECT id FROM prompt_variants WHERE task_id=? AND status='DRAFT'", (task_id,))
    exported = output_root / ("task_" + task_id)
    manifest = json.loads((exported / "manifest.json").read_text(encoding="utf-8"))
    assert [g["style"] for g in manifest["groups"]] == ["ink painting", "cinematic photography"]
    assert (exported / "reference_analysis.json").is_file()
    assert (exported / "prompt_history.json").is_file()
    for group in manifest["groups"]:
        assert group["shortfall"] == 0
        for item in group["items"]:
            assert item["scores"] and sha256(exported / item["relative_path"]) == item["sha256"]
    tags = json.loads(service.db.one("SELECT metadata FROM assets WHERE task_id=? AND source_kind='reference'", (task_id,))["metadata"])
    assert tags["model_tags"]["writing_style"]
    if live:
        tagging = next(c for c in calls if c["purpose"] == "tagging")
        assert "original_prompt" not in tagging["data"]
        assert "subject first" not in json.dumps(tagging)
        initial = [c for c in calls if c["purpose"]=="prompt_generation" and not c["data"].get("current")]
        assert all("base_prompt" not in c["data"] for c in initial)
        assert {c["data"]["target_style"] for c in initial} == {"ink painting", "cinematic photography"}
        assert all("independent theme" in c["data"]["instruction"] for c in initial)
        revised = [c for c in calls if c["purpose"] == "prompt_generation" and c["data"].get("current")]
        assert revised and all(c["data"]["feedback"] for c in revised)
        assert all("latest_reviews" not in c["data"] and "top_k" not in c["data"] for c in revised)
        assert any(g["6"]["inputs"]["text"].startswith("redesigned composition") for g in posted)
        assert service.db.budget(task_id)["spent"] > 0
        assert "studio-secret" not in json.dumps(service.db.rows("SELECT * FROM events"))


def test_reference_required_and_output_path_absolute(service, tmp_path):
    with pytest.raises(ValueError, match="REFERENCE_IMAGES_REQUIRED"):
        service.create_task(TaskSettings(goal="test", autonomous=True))
    image = tmp_path / "ref.png"
    Image.new("RGB", (64, 64)).save(image)
    with pytest.raises(ValueError, match="ABSOLUTE_OUTPUT_FOLDER_REQUIRED"):
        service.create_task(TaskSettings(goal="test", autonomous=True, export_folder="relative"), [image])


def test_provider_form_keeps_key_out_of_files(service, tmp_path):
    service.provider_path = tmp_path / "providers.yaml"
    result = save_provider(service, "custom", "private-test-key", "text-model", "https://example.test/v1/chat/completions",
        "vision-model", True, False, 1, 2, .05, "sfw", True, "", False)
    assert "private-test-key" not in result
    assert "private-test-key" not in service.provider_path.read_text()
    assert service.cloud.credential_key(service.config.providers[0].credentials[0]) == "private-test-key"
    assert service.config.routes["tagging"] == ["desktop/vision-model"]
    assert service.config.routes["prompt_generation"] == ["desktop/text-model"]
    assert endpoint_parts("https://example.test/v1/chat/completions") == ("https://example.test/v1", "/chat/completions")


def test_infer_standard_workflow_and_reject_ambiguous(service):
    graph = json.loads((service.project_root / "workflows/example-api.json").read_text())
    config = infer_workflow(graph, "workflows/example-api.json", "http://127.0.0.1:8188")
    assert config.bindings["positive"][0].node_id == "6"
    assert config.bindings["negative"][0].node_id == "7"
    graph["duplicate-sampler"] = graph["3"]
    with pytest.raises(ValueError, match="COMPLEX_WORKFLOW"):
        infer_workflow(graph, "test.json", "http://127.0.0.1:8188")


async def test_group_images_are_exported_before_delivery_finishes(service, tmp_path, monkeypatch):
    reference = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), "red").save(reference)
    output = tmp_path / "export"
    task_id = service.create_task(TaskSettings(goal="landscape", autonomous=True, groups=1, per_group=2,
        target_styles=["ink painting"], export_folder=str(output)), [reference])
    observations = []
    original = service.export_file

    def observe(t, relative, source):
        original(t, relative, source)
        if relative.startswith("group_"):
            observations.append((service.db.one("SELECT state FROM tasks WHERE id=?", (t,))["state"],
                (output / ("task_" + t) / "manifest.json").exists()))

    monkeypatch.setattr(service, "export_file", observe)
    await service.run(task_id)
    assert observations[0] == ("RUNNING", False)
    assert service.db.one("SELECT state FROM tasks WHERE id=?", (task_id,))["state"] == "COMPLETED"


def test_export_refuses_to_overwrite_changed_file(service, tmp_path):
    source = tmp_path / "reference.png"
    Image.new("RGB", (64, 64), "red").save(source)
    output = tmp_path / "export"
    task_id = service.create_task(TaskSettings(goal="test", export_folder=str(output)), [source])
    destination = output / ("task_" + task_id) / "group_01" / "image.png"
    destination.parent.mkdir(parents=True)
    Image.new("RGB", (64, 64), "blue").save(destination)
    expected = destination.read_bytes()
    with pytest.raises(CloudError, match="OUTPUT_FILE_CHANGED"):
        service.export_file(task_id, "group_01/image.png", source)
    assert destination.read_bytes() == expected
