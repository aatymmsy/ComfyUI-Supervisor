import asyncio
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from pydantic import ValidationError

from supervisor.comfy import Comfy, GenerationError, SubmissionUnknown
from supervisor.db import BudgetExceeded, now, uid
from supervisor.engine import Supervisor
from supervisor.files import atomic_write, metadata, safe_path, sha256
from supervisor.models import Evaluation, Policy, PromptPlan, ProvidersConfig, StyleCard, TaskSettings, WorkflowConfig, load_yaml
from supervisor.providers import Cloud, CloudError, CloudRefusal, PolicyBlocked


def cloud_config(policy="forbidden"):
    return ProvidersConfig.model_validate({"providers": [{"id": "p", "name": "Test", "base_url": "https://test.invalid/v1", "rpm": 1000000, "retries": 0, "policy": {"nsfw_policy": policy}, "credentials": [{"id": "k", "api_key_env": "TEST_SUPERVISOR_KEY"}], "models": [{"id": "m", "text": True, "vision": True, "json_schema": True, "pricing": {"input_per_million_micro": 1000000, "output_per_million_micro": 2000000, "max_call_micro": 100000, "source": "test", "verified_at": "2026-09-30"}}]}], "routes": {"tagging": ["p/m"], "review": ["p/m"], "prompt_generation": ["p/m"]}})


async def ready(service, **kwargs):
    # Recovery/deletion scenarios deliberately require the stricter legacy gate.
    kwargs.setdefault('quality_threshold',80)
    task_id = service.create_task(TaskSettings(goal="landscape", content_label="sfw", **kwargs))
    await service.run(task_id)
    assert service.db.one("SELECT state FROM tasks WHERE id=?", (task_id,))["state"] == "WAITING_APPROVAL"
    service.approve_prompts(task_id)
    return task_id


def make_image(path, color="red", graph=None):
    image = Image.new("RGB", (64, 64), color)
    info = PngInfo()
    if graph:
        info.add_text("prompt", json.dumps(graph))
    image.save(path, pnginfo=info)


async def test_demo_complete_with_quarantine_and_manifest(service):
    task_id = await ready(service, auto_iterations=True, auto_candidates=True)
    await service.run(task_id)
    task = service.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
    assert task["state"] == "COMPLETED"
    assert len(service.db.accepted(task_id)) == 4
    assert service.db.one("SELECT COUNT(*) n FROM assets WHERE task_id=? AND state='QUARANTINED'", (task_id,))["n"] == 2
    delivery = service.db.one("SELECT * FROM deliveries WHERE task_id=?", (task_id,))
    manifest_path = service.files.path(delivery["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    assert manifest["demo"] is True
    assert all(g["qualified_count"] == 2 and g["shortfall"] == 0 for g in manifest["groups"])
    for group in manifest["groups"]:
        for item in group["items"]:
            assert sha256(manifest_path.parent / item["relative_path"]) == item["sha256"]
    assert len({a["sha256"] for a in service.db.accepted(task_id)}) == 4


async def test_manual_candidates_do_not_count_before_approval(service):
    task_id = await ready(service, auto_iterations=True)
    await service.run(task_id)
    assert service.db.one("SELECT phase FROM tasks WHERE id=?", (task_id,))["phase"] == "FINAL_REVIEW"
    assert service.db.accepted(task_id) == []
    service.approve_candidates(task_id)
    await service.run(task_id)
    assert service.db.one("SELECT state FROM tasks WHERE id=?", (task_id,))["state"] == "COMPLETED"


async def test_output_target_can_deliver_more_than_four_images_and_resume_with_accepted_images(service):
    task_id = await ready(service,groups=2,per_group=3,auto_iterations=True,auto_candidates=True)
    await service.run(task_id)
    assert service.db.one("SELECT state FROM tasks WHERE id=?",(task_id,))["state"] == "COMPLETED"
    accepted = service.db.accepted(task_id)
    assert len(accepted) == 6
    # Increasing the target of a paused task keeps its already accepted images.
    service.db.transition(task_id,'PAUSED','USER_PAUSE')
    before = {a['id'] for a in accepted}
    service.update_task_limits(task_id,12,80,4)
    service.resume(task_id)
    await service.run(task_id)
    assert service.db.one("SELECT state FROM tasks WHERE id=?",(task_id,))["state"] == "COMPLETED"
    assert len(service.db.accepted(task_id)) == 8
    assert before.issubset({a['id'] for a in service.db.accepted(task_id)})
    delivery = service.db.one("SELECT * FROM deliveries WHERE task_id=? ORDER BY rowid DESC",(task_id,))
    manifest = json.loads(service.files.path(delivery['manifest_path']).read_text())
    assert all(g['qualified_count'] == 4 and g['shortfall'] == 0 for g in manifest['groups'])


async def test_round_limit_produces_partial_without_filling(service):
    task_id = await ready(service, max_rounds=1, auto_iterations=True, auto_candidates=True)
    await service.run(task_id)
    task = service.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
    assert task["state"] == "PARTIAL"
    assert service.db.accepted(task_id) == []


async def test_stop_preserves_pending_and_does_not_generate(service):
    task_id = await ready(service)
    service.stop(task_id)
    await service.run(task_id)
    assert service.db.one("SELECT state FROM tasks WHERE id=?", (task_id,))["state"] == "CANCELLED"
    assert service.db.one("SELECT COUNT(*) n FROM generations WHERE task_id=?", (task_id,))["n"] == 0


async def test_unapproved_iteration_is_durable(service):
    task_id = await ready(service, auto_iterations=False)
    await service.run(task_id)
    task = service.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
    assert task["state"] == "WAITING_APPROVAL"
    assert task["phase"] == "PROMPT"
    before = service.db.one("SELECT COUNT(*) n FROM generations WHERE task_id=?", (task_id,))["n"]
    await service.run(task_id)
    assert service.db.one("SELECT COUNT(*) n FROM generations WHERE task_id=?", (task_id,))["n"] == before


async def test_policy_gate_prevents_any_network(service, monkeypatch):
    monkeypatch.setenv("TEST_SUPERVISOR_KEY", "never-log-this-key")
    seen = []
    cloud = Cloud(cloud_config(), service.db, httpx.MockTransport(lambda r: seen.append(r)))
    settings = TaskSettings(goal="test", demo=False, content_label="unknown")
    task_id = service.create_task(settings)
    with pytest.raises(PolicyBlocked):
        await cloud.request(task_id, settings, "tagging", StyleCard, {})
    assert seen == []
    assert service.db.rows("SELECT * FROM api_calls") == []


def test_no_model_name_blacklist(service):
    config = cloud_config()
    config.providers[0].models[0].id = "arbitrary-adult-model-name"
    config.routes["review"] = ["p/arbitrary-adult-model-name"]
    assert len(Cloud(config, service.db).candidates("review", "sfw", 1, "USD")) == 1


def test_verified_sensitive_scope_and_expiry():
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    policy = Policy(nsfw_policy="allowed", evidence="contract:test", review_due=future, allowed_content=["sfw", "adult_allowed", "unknown"], allow_unknown_input=True)
    assert policy.permits("adult_allowed") and policy.permits("unknown")
    assert not policy.permits("blocked")
    policy.review_due = past
    assert not policy.permits("adult_allowed")


async def test_refusal_is_recorded_not_deleted_or_fallback(service, monkeypatch):
    monkeypatch.setenv("TEST_SUPERVISOR_KEY", "never-log-this-key")
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"refusal": "no"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}})
    settings = TaskSettings(goal="test", demo=False, content_label="sfw")
    task_id = service.create_task(settings)
    cloud = Cloud(cloud_config(), service.db, httpx.MockTransport(handler))
    with pytest.raises(CloudRefusal):
        await cloud.request(task_id, settings, "tagging", StyleCard, {})
    assert len(seen) == 1
    assert service.db.one("SELECT status FROM api_calls")["status"] == "REFUSED"
    assert service.db.budget(task_id)["spent"] == 120
    assert "never-log-this-key" not in json.dumps(service.db.rows("SELECT * FROM api_calls"))


async def test_invalid_json_remains_failure_with_cost(service, monkeypatch):
    monkeypatch.setenv("TEST_SUPERVISOR_KEY", "x")
    settings = TaskSettings(goal="test", demo=False, content_label="sfw")
    task_id = service.create_task(settings)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}))
    with pytest.raises(CloudError):
        await Cloud(cloud_config(), service.db, transport).request(task_id, settings, "tagging", StyleCard, {})
    assert service.db.budget(task_id)["spent"] == 20
    assert not service.db.rows("SELECT * FROM evaluations")


async def test_timeout_cost_stays_uncertain(service, monkeypatch):
    monkeypatch.setenv("TEST_SUPERVISOR_KEY", "x")
    def handler(request):
        raise httpx.ReadTimeout("sensitive context", request=request)
    settings = TaskSettings(goal="test", demo=False, content_label="sfw")
    task_id = service.create_task(settings)
    with pytest.raises(CloudError):
        await Cloud(cloud_config(), service.db, httpx.MockTransport(handler)).request(task_id, settings, "tagging", StyleCard, {})
    assert service.db.budget(task_id)["uncertain"] == 100000
    assert "sensitive context" not in json.dumps(service.db.rows("SELECT * FROM api_calls"))


def test_budget_reservation_is_atomic(service):
    task_id = service.create_task(TaskSettings(goal="test"))
    def reserve():
        try:
            return service.db.reserve(task_id, "test", "p", "m", "k", {}, 60, 100)
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(x is not None for x in results) == 1
    assert service.db.budget(task_id)["reserved"] == 60
    service.db.recover_calls()
    assert service.db.budget(task_id)["uncertain"] == 60


def test_price_unknown_cannot_route(service):
    config = cloud_config()
    config.providers[0].models[0].pricing = None
    assert Cloud(config, service.db).candidates("tagging", "sfw", 1, "USD") == []


def test_schema_rejects_nan_and_unassessable_score():
    ev = Cloud.demo(Evaluation, "review", {"asset_id": "a", "round_index": 1})
    body = ev.model_dump()
    body["composition"] = float("nan")
    with pytest.raises(ValidationError):
        Evaluation.model_validate(body)
    body["composition"] = 90
    body["anatomy"] = 0
    with pytest.raises(ValidationError):
        Evaluation.model_validate(body)


def test_prescreen_never_accepts_or_deletes():
    settings = TaskSettings(goal="test", auto_candidates=True)
    ev = Cloud.demo(Evaluation, "review", {"asset_id": "a", "stage": "prescreen", "round_index": 1})
    assert Supervisor.evaluate_decision(ev, settings)[0] == "REVIEW"
    ev = Cloud.demo(Evaluation, "review", {"asset_id": "a", "stage": "prescreen", "round_index": 0})
    assert Supervisor.evaluate_decision(ev, settings)[0] == "REVIEW"


def test_visible_failed_score_can_delete_without_confidence():
    ev = Cloud.demo(Evaluation, "review", {"asset_id": "a", "round_index": 0})
    assert Supervisor.evaluate_decision(ev, TaskSettings(goal="test",quality_threshold=80))[0] == "QUARANTINE"


async def test_quarantine_restore_byte_exact_and_protected(service):
    task_id = await ready(service, max_rounds=1, auto_iterations=True)
    await service.run(task_id)
    asset = service.db.one("SELECT * FROM assets WHERE state='QUARANTINED'")
    service.files.restore(asset["id"])
    restored = service.db.one("SELECT * FROM assets WHERE id=?", (asset["id"],))
    assert restored["state"] == "AVAILABLE" and restored["protected"] == 1
    assert sha256(service.files.path(restored["path"])) == asset["sha256"]
    with pytest.raises(ValueError):
        service.files.quarantine(restored, {}, "test")


def test_input_original_never_deleted(service, tmp_path):
    source = tmp_path / "favorite.png"
    make_image(source)
    task_id = service.create_task(TaskSettings(goal="test"), [source])
    asset = service.db.one("SELECT * FROM assets WHERE task_id=?", (task_id,))
    with pytest.raises(ValueError):
        service.files.quarantine(asset, {}, "bad")
    assert source.exists()


def test_path_escape_and_absolute_rejected(tmp_path):
    for path in ["../escape.png", "C:/escape.png", "/escape.png", "a/../../escape"]:
        with pytest.raises(ValueError):
            safe_path(tmp_path, path)


def test_metadata_traces_output_not_first_text(tmp_path):
    graph = {"wrong": {"class_type": "CLIPTextEncode", "inputs": {"text": "wrong"}}, "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "correct"}}, "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blur"}}, "3": {"class_type": "KSampler", "inputs": {"positive": ["6", 0], "negative": ["7", 0], "seed": 42, "steps": 20, "cfg": 7, "sampler_name": "euler"}}, "9": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}}}
    path = tmp_path / "meta.png"
    make_image(path, graph=graph)
    assert metadata(path)["extracted"]["positive"]["value"] == "correct"
    assert metadata(path)["extracted"]["seed"]["value"] == 42


def test_complex_metadata_ambiguous_not_guessed(tmp_path):
    graph = {"3": {"class_type": "KSampler", "inputs": {"seed": 1}}, "4": {"class_type": "KSampler", "inputs": {"seed": 2}}, "9": {"class_type": "SaveImage", "inputs": {"a": ["3", 0], "b": ["4", 0]}}}
    path = tmp_path / "meta.png"
    make_image(path, graph=graph)
    assert metadata(path)["extracted"] == {}


def test_workflow_mapping_checks_type_and_hash(service):
    config = load_yaml(service.project_root / "config/workflow.example.yaml", WorkflowConfig)
    comfy = Comfy(config, service.project_root, service.db)
    plan = PromptPlan(group_id="g", positive="landscape", reason="test")
    graph = comfy.graph(plan, "token")
    assert graph["9"]["inputs"]["filename_prefix"] == "supervisor/token"
    assert graph["3"]["inputs"]["seed"] == plan.params.seed
    config.bindings["positive"][0].class_type = "Wrong"
    with pytest.raises(GenerationError):
        comfy.graph(plan, "token")


async def test_comfy_submit_disconnect_collect_history(service, monkeypatch):
    config = load_yaml(service.project_root / "config/workflow.example.yaml", WorkflowConfig)
    config.reconcile_interval = 0.001
    config.timeout_seconds = 2
    requests = []
    async def unavailable(*a, **k):
        raise OSError("no websocket")
    monkeypatch.setattr("supervisor.comfy.websockets.connect", unavailable)
    task_id = await ready(service)
    group = service.db.one("SELECT * FROM groups WHERE task_id=? ORDER BY ordinal", (task_id,))
    variant = service.db.one("SELECT * FROM prompt_variants WHERE group_id=?", (group["id"],))
    plan = PromptPlan.model_validate_json(variant["body"])
    gen_id = uid()
    graph = Comfy(config, service.project_root, service.db).graph(plan, "token")
    service.db.execute("INSERT INTO generations(id,task_id,group_id,variant_id,submission_token,client_id,state,graph,graph_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,'PREPARED',?,?,?,?)", (gen_id, task_id, group["id"], variant["id"], "token", "client", json.dumps(graph), "hash", now(), now()))
    def handler(request):
        requests.append(request)
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "pid"})
        if request.url.path == "/history/pid":
            return httpx.Response(200, json={"pid": {"status": {"completed": True, "status_str": "success"}, "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "supervisor", "type": "output"}]}, "bad": {"images": [{"filename": "preview.png"}]}}}})
        return httpx.Response(404)
    comfy = Comfy(config, service.project_root, service.db, httpx.MockTransport(handler))
    events = []
    outputs = await comfy.run(service.db.one("SELECT * FROM generations WHERE id=?", (gen_id,)), lambda k, d: events.append(k))
    assert len(outputs) == 1 and outputs[0]["node_id"] == "9"
    assert events == ["WS_FALLBACK"]
    assert sum(r.method == "POST" for r in requests) == 1
    # Persisted prompt_id resumes monitoring, never re-posting the graph.
    await comfy.run(service.db.one("SELECT * FROM generations WHERE id=?", (gen_id,)), lambda k, d: None)
    assert sum(r.method == "POST" for r in requests) == 1


async def test_unknown_submission_not_blindly_retried(service, monkeypatch):
    config = load_yaml(service.project_root / "config/workflow.example.yaml", WorkflowConfig)
    async def unavailable(*a, **k):
        raise OSError()
    monkeypatch.setattr("supervisor.comfy.websockets.connect", unavailable)
    posts = []
    def handler(request):
        if request.method == "POST":
            posts.append(request)
            raise httpx.ReadTimeout("unknown", request=request)
        return httpx.Response(200, json={"queue_running": [], "queue_pending": []} if request.url.path == "/queue" else {})
    task_id = await ready(service)
    group = service.db.one("SELECT * FROM groups WHERE task_id=?", (task_id,))
    variant = service.db.one("SELECT * FROM prompt_variants WHERE group_id=?", (group["id"],))
    gen_id = uid()
    service.db.execute("INSERT INTO generations(id,task_id,group_id,variant_id,submission_token,client_id,state,graph,graph_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,'PREPARED','{}','hash',?,?)", (gen_id, task_id, group["id"], variant["id"], uid(), uid(), now(), now()))
    comfy = Comfy(config, service.project_root, service.db, httpx.MockTransport(handler))
    for _ in range(2):
        with pytest.raises(SubmissionUnknown):
            await comfy.run(service.db.one("SELECT * FROM generations WHERE id=?", (gen_id,)), lambda k, d: None)
    assert len(posts) == 1


def test_permanent_mode_requires_two_explicit_flags():
    with pytest.raises(ValidationError):
        TaskSettings(goal="x", delete_mode="direct", allow_permanent_delete=True)


async def test_crash_after_collection_resume_without_duplicate_generation(service, monkeypatch):
    task_id = await ready(service, groups=1, per_group=1, auto_iterations=True, auto_candidates=True)
    original = service.save_evaluation
    def crash(*a, **k):
        raise RuntimeError("synthetic crash")
    monkeypatch.setattr(service, "save_evaluation", crash)
    await service.run(task_id)
    assert service.db.one("SELECT state FROM tasks WHERE id=?", (task_id,))["state"] == "FAILED"
    assert service.db.one("SELECT COUNT(*) n FROM generations WHERE task_id=?", (task_id,))["n"] == 1
    monkeypatch.setattr(service, "save_evaluation", original)
    service.resume(task_id)
    await service.run(task_id)
    assert service.db.one("SELECT state FROM tasks WHERE id=?", (task_id,))["state"] == "COMPLETED"
    assert service.db.one("SELECT COUNT(*) n FROM generations WHERE task_id=?", (task_id,))["n"] == 2


async def test_recovery_after_move_before_db_commit(service):
    task_id = await ready(service, groups=1, max_rounds=1, auto_iterations=True)
    await service.run(task_id)
    asset = service.db.one("SELECT * FROM assets WHERE state='QUARANTINED'")
    op = service.db.one("SELECT * FROM file_operations WHERE asset_id=?", (asset["id"],))
    service.db.execute("UPDATE file_operations SET state='applied' WHERE id=?", (op["id"],))
    service.db.execute("UPDATE assets SET state='AVAILABLE',path=? WHERE id=?", (op["source"], asset["id"]))
    service.files.recover()
    assert service.db.one("SELECT state FROM assets WHERE id=?", (asset["id"],))["state"] == "QUARANTINED"


async def test_id_mismatch_never_saves_score(service):
    task_id = service.create_task(TaskSettings(goal="x"))
    evaluation = Cloud.demo(Evaluation, "review", {"asset_id": "wrong", "round_index": 1})
    with pytest.raises(CloudError):
        service.save_evaluation({"id": "actual"}, evaluation, None, "final")
    assert service.db.rows("SELECT * FROM evaluations") == []


async def test_live_path_mock_cloud_and_comfy_end_to_end(service, monkeypatch, tmp_path):
    from supervisor.comfy import demo_image
    monkeypatch.setenv("TEST_SUPERVISOR_KEY", "integration-secret-never-log")
    config = load_yaml(service.project_root / "config/workflow.example.yaml", WorkflowConfig)
    config.reconcile_interval = 0.001
    requests, pending, progress = [], {}, []
    ws_prompt = {"id": None}

    class Socket:
        async def recv(self):
            return json.dumps({"type": "progress", "data": {"prompt_id": ws_prompt["id"], "node": "3", "value": 20, "max": 20}})
        async def close(self):
            pass

    async def connect(*a, **k):
        return Socket()

    monkeypatch.setattr("supervisor.comfy.websockets.connect", connect)

    def handler(request):
        requests.append(request)
        if request.url.host == "test.invalid":
            body = json.loads(request.content)
            data = json.loads(body["messages"][1]["content"][0]["text"])
            purpose = data["purpose"]
            contract = {"tagging": StyleCard, "prompt_generation": PromptPlan, "review": Evaluation}[purpose]
            value = Cloud.demo(contract, purpose, data["data"])
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": value.model_dump_json()}}], "usage": {"prompt_tokens": 100, "completion_tokens": 100}})
        if request.url.path == "/prompt":
            body = json.loads(request.content)
            prompt_id = uid()
            graph = body["prompt"]
            params = graph["3"]["inputs"]
            seed = params["seed"]
            group = (seed - 42) // 100003
            round_index = (seed - 42) % 100003
            plan = PromptPlan(group_id="test", positive="landscape", reason="test", params={"seed": seed, "width": 768, "height": 768})
            image_path = tmp_path / (prompt_id + ".png")
            demo_image(image_path, plan, round_index, group)
            pending[prompt_id] = {"reads": 0, "bytes": image_path.read_bytes()}
            ws_prompt["id"] = prompt_id
            return httpx.Response(200, json={"prompt_id": prompt_id})
        if request.url.path.startswith("/history/"):
            prompt_id = request.url.path.rsplit("/", 1)[1]
            pending[prompt_id]["reads"] += 1
            if pending[prompt_id]["reads"] == 1:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={prompt_id: {"status": {"completed": True, "status_str": "success"}, "outputs": {"9": {"images": [{"filename": prompt_id + ".png", "subfolder": "supervisor", "type": "output"}]}}}})
        if request.url.path == "/view":
            prompt_id = request.url.params["filename"].removesuffix(".png")
            return httpx.Response(200, content=pending[prompt_id]["bytes"])
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    service.cloud = Cloud(cloud_config(), service.db, transport)
    service.comfy = Comfy(config, service.project_root, service.db, transport)
    source = tmp_path / "reference.png"
    make_image(source)
    task_id = service.create_task(TaskSettings(goal="landscape", demo=False, content_label="sfw", groups=2, per_group=1, auto_iterations=True, auto_candidates=True, quality_threshold=80, params={"seed": 42}), [source])
    await service.run(task_id)
    service.approve_prompts(task_id)
    await service.run(task_id)
    assert service.db.one("SELECT state FROM tasks WHERE id=?", (task_id,))["state"] == "COMPLETED"
    assert len(service.db.accepted(task_id)) == 2
    assert len(service.db.rows("SELECT * FROM events WHERE kind='COMFY_progress'")) == 4
    assert sum(r.url.path == "/prompt" for r in requests) == 4
    assert service.db.budget(task_id)["spent"] > 0
    assert service.db.budget(task_id)["reserved"] == 0
    assert all(a["actual_seed"] is None for a in service.db.rows("SELECT * FROM generation_outputs"))
    assert "integration-secret-never-log" not in json.dumps(service.db.rows("SELECT * FROM events"))


async def test_budget_exhaustion_delivers_partial_without_call(service, monkeypatch):
    monkeypatch.setenv("TEST_SUPERVISOR_KEY", "x")
    seen = []
    service.cloud = Cloud(cloud_config(), service.db, httpx.MockTransport(lambda r: seen.append(r)))
    task_id = service.create_task(TaskSettings(goal="landscape", demo=False, content_label="sfw", budget_micro=1))
    await service.run(task_id)
    assert service.db.one("SELECT reason FROM tasks WHERE id=?", (task_id,))["reason"] == "BUDGET_EXHAUSTED"
    assert seen == []


def test_read_only_open_does_not_recover_current_live_reservation(service):
    task_id = service.create_task(TaskSettings(goal="x"))
    assert service.db.claim(task_id, "other-worker")
    service.db.reserve(task_id, "test", "p", "m", "k", {}, 30, 100)
    service.db.recover_calls()
    assert service.db.budget(task_id)["reserved"] == 30


def test_stop_state_not_overwritten_by_late_progress(service):
    task_id = service.create_task(TaskSettings(goal="x"))
    service.stop(task_id)
    service.db.transition(task_id, "RUNNING", "EVALUATE")
    assert service.db.one("SELECT state FROM tasks WHERE id=?", (task_id,))["state"] == "STOPPING"


async def test_stop_prepared_generation_never_posts(service, monkeypatch):
    task_id = await ready(service)
    group = service.db.one("SELECT * FROM groups WHERE task_id=?", (task_id,))
    variant = service.db.one("SELECT * FROM prompt_variants WHERE group_id=?", (group["id"],))
    gen_id = uid()
    service.db.execute("INSERT INTO generations(id,task_id,group_id,variant_id,submission_token,client_id,state,graph,graph_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,'PREPARED','{}','hash',?,?)", (gen_id, task_id, group["id"], variant["id"], uid(), uid(), now(), now()))
    async def forbidden(*a, **k):
        raise AssertionError("No new submission after stop")
    monkeypatch.setattr(service.comfy, "run", forbidden)
    service.stop(task_id)
    await service.run(task_id)
    assert service.db.one("SELECT state FROM tasks WHERE id=?", (task_id,))["state"] == "CANCELLED"


async def test_confirmed_execution_failure_consumes_round_once(service):
    task_id = await ready(service)
    group = service.db.one("SELECT * FROM groups WHERE task_id=?", (task_id,))
    variant = service.db.one("SELECT * FROM prompt_variants WHERE group_id=?", (group["id"],))
    gen_id = uid()
    service.db.execute("INSERT INTO generations(id,task_id,group_id,variant_id,submission_token,client_id,state,graph,graph_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,'MONITOR','{}','hash',?,?)", (gen_id, task_id, group["id"], variant["id"], uid(), uid(), now(), now()))
    generation = service.db.one("SELECT * FROM generations WHERE id=?", (gen_id,))
    service.comfy.fail(generation)
    service.comfy.fail(generation)
    assert service.db.one("SELECT round_index FROM groups WHERE id=?", (group["id"],))["round_index"] == 1


async def test_fallback_missing_key_uses_next_credential(service, monkeypatch):
    monkeypatch.setenv("TEST_SUPERVISOR_KEY", "x")
    config = cloud_config()
    from supervisor.models import Credential
    config.providers[0].credentials.insert(0, Credential(id="missing", api_key_env="MISSING_UNSET_TEST_KEY"))
    settings = TaskSettings(goal="test", demo=False, content_label="sfw")
    task_id = service.create_task(settings)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": StyleCard(subject=["test"]).model_dump_json()}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}))
    card, _ = await Cloud(config, service.db, transport).request(task_id, settings, "tagging", StyleCard, {})
    assert card.subject == ["test"]
    assert service.db.one("SELECT credential_id FROM api_calls")["credential_id"] == "k"


async def test_explicit_direct_delete_keeps_complete_audit(service):
    task_id = await ready(service, groups=1, max_rounds=1, auto_iterations=True, delete_mode="direct", allow_permanent_delete=True, calibrated=True)
    await service.run(task_id)
    asset = service.db.one("SELECT * FROM assets WHERE task_id=? AND source_kind='generated'", (task_id,))
    assert asset["state"] == "DELETED"
    assert not service.files.path(asset["path"]).exists()
    audit = service.files.path(f"tasks/{task_id}/audit/{asset['id']}/audit.json")
    assert json.loads(audit.read_text())["sha256"] == asset["sha256"]
    assert audit.with_name("thumbnail.jpg").is_file()
    with pytest.raises(ValueError):
        service.files.restore(asset["id"])


async def test_delayed_cleanup_only_after_due_and_restore_prevents_it(service):
    task_id = await ready(service, max_rounds=1, auto_iterations=True, delete_mode="delayed", allow_permanent_delete=True, calibrated=True)
    await service.run(task_id)
    assets = service.db.rows("SELECT * FROM assets WHERE task_id=? AND state='QUARANTINED'", (task_id,))
    assert service.files.cleanup_due(task_id) == 0
    service.files.restore(assets[0]["id"])
    service.db.execute("UPDATE file_operations SET restore_until=? WHERE kind='delayed'", ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))
    assert service.files.cleanup_due(task_id) == 1
    assert service.db.one("SELECT state FROM assets WHERE id=?", (assets[0]["id"],))["state"] == "AVAILABLE"
    assert service.db.one("SELECT state FROM assets WHERE id=?", (assets[1]["id"],))["state"] == "DELETED"


async def test_audit_failure_never_deletes_original(service, monkeypatch):
    import supervisor.files as file_module
    original = file_module.atomic_write
    def reject_audit(path, data):
        if path.name == "audit.json":
            raise OSError("synthetic disk full")
        original(path, data)
    monkeypatch.setattr(file_module, "atomic_write", reject_audit)
    task_id = await ready(service, groups=1, max_rounds=1, auto_iterations=True, delete_mode="direct", allow_permanent_delete=True, calibrated=True)
    await service.run(task_id)
    asset = service.db.one("SELECT * FROM assets WHERE task_id=? AND source_kind='generated'", (task_id,))
    assert service.files.path(asset["path"]).is_file()
    assert asset["state"] == "AVAILABLE"
    assert not service.db.rows("SELECT * FROM file_operations")


async def test_generation_limit_produces_partial(service):
    task_id = await ready(service, max_generations=1, auto_iterations=True)
    await service.run(task_id)
    assert service.db.one("SELECT reason FROM tasks WHERE id=?", (task_id,))["reason"] == "GENERATION_LIMIT"
    assert service.db.one("SELECT COUNT(*) n FROM generations WHERE task_id=?", (task_id,))["n"] == 1


async def test_expired_wall_clock_never_starts_generation(service):
    task_id = await ready(service, auto_iterations=True)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    service.db.execute("UPDATE tasks SET created_at=? WHERE id=?", (old, task_id))
    await service.run(task_id)
    assert service.db.one("SELECT reason FROM tasks WHERE id=?", (task_id,))["reason"] == "WALL_CLOCK_LIMIT"
    assert not service.db.rows("SELECT * FROM generations WHERE task_id=?", (task_id,))
