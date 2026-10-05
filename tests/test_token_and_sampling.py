import json

import httpx
import pytest

from supervisor.context import compact_card, compact_review, reference_prompt
from supervisor.files import studio_reference_paths
from supervisor.models import StyleCard, TaskSettings
from supervisor.providers import CloudError
from PIL import Image
from test_live_readiness import configure_cloud


def test_folder_random_sample_is_fifteen_and_matches_preview(tmp_path):
    for index in range(30):
        (tmp_path / f"{index:02}.png").touch()
    (tmp_path / "notes.txt").touch()
    preview, total = studio_reference_paths([], [], str(tmp_path), 7)
    selected, _ = studio_reference_paths([], [], str(tmp_path), 7)
    assert total == 30 and len(preview) == 15 and preview == selected
    assert selected != sorted(tmp_path.glob("*.png"))[:15]
    other, _ = studio_reference_paths([], [], str(tmp_path), 8)
    assert other != selected
    uploaded, _ = studio_reference_paths([], list(tmp_path.glob("*.png")), "", 7)
    assert uploaded == selected


def test_small_folder_uses_all_images_without_repeating(tmp_path):
    paths = [tmp_path / "one.png", tmp_path / "two.jpg"]
    result, total = studio_reference_paths([str(paths[0])], [str(p) for p in paths], "", 7)
    assert total == 2 and set(result) == set(paths) and len(result) == 2


def test_stage_context_omits_metadata_and_reference_ids():
    card = compact_card({"subject": ["tag" * 100] * 30, "reference_images": ["uuid"] * 15})
    assert len(card["subject"]) == 6 and len(card["subject"][0]) == 100
    assert "reference_images" not in card
    assert reference_prompt({"extracted": {"positive": {"value": "mountains", "node_id": "9"}, "seed": {"value": 5}}}) == {"positive": "mountains"}
    review = compact_review({"overall": 60, "traditional": {"phash": "large"}, "issues": [{"evidence": "x" * 1000}] * 12})
    assert "traditional" not in review and len(review["issues"]) == 3


async def test_truncated_reply_retries_with_larger_limit_and_tracks_both_calls(service):
    limits = []
    def handler(request):
        body = json.loads(request.content)
        limits.append(body["max_tokens"])
        assert "schema" not in json.loads(body["messages"][-1]["content"][0]["text"])
        if len(limits) == 1:
            return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "{"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1800}})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": StyleCard(subject=["mountains"]).model_dump_json()}}], "usage": {"prompt_tokens": 1, "completion_tokens": 100}})
    configure_cloud(service, httpx.MockTransport(handler))
    task = service.create_task(TaskSettings(goal="landscape", demo=False, content_label="sfw"))
    card, _ = await service.cloud.request(task, service.settings(task), "tagging", StyleCard, {})
    assert card.subject == ["mountains"] and limits == [1800, 3600]
    assert [row["status"] for row in service.db.rows("SELECT status FROM api_calls")] == ["INCOMPLETE", "SUCCESS"]


async def test_truncation_retries_are_bounded(service):
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "{"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    configure_cloud(service, httpx.MockTransport(handler))
    task = service.create_task(TaskSettings(goal="landscape", demo=False, content_label="sfw"))
    with pytest.raises(CloudError, match="INCOMPLETE_RESPONSE"):
        await service.cloud.request(task, service.settings(task), "tagging", StyleCard, {})
    assert len(calls) == 2
    assert all(row["error_code"] == "INCOMPLETE_RESPONSE" for row in service.db.rows("SELECT error_code FROM api_calls"))


async def test_tag_cache_reused_across_analysis_and_generation_tasks(service,tmp_path):
    calls=[]
    def handler(request):
        calls.append(1)
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":StyleCard(subject=["mountains"]).model_dump_json()}}],"usage":{"prompt_tokens":1,"completion_tokens":1}})
    configure_cloud(service,httpx.MockTransport(handler))
    image=tmp_path/'reference.png'
    Image.new('RGB',(64,64),'green').save(image)
    settings=TaskSettings(goal="landscape",demo=False,content_label="sfw",reference_only=True)
    analysis=service.create_task(settings,[image])
    await service.run(analysis)
    assert service.db.one('SELECT state FROM tasks WHERE id=?',(analysis,))["state"]=="COMPLETED"
    assert not service.db.rows('SELECT id FROM generations')
    generation=service.create_task(settings.model_copy(update={"reference_only":False}),[image],start=False)
    asset=service.db.one('SELECT * FROM assets WHERE task_id=?',(generation,))
    tags=await service.tag_reference(generation,service.settings(generation),asset)
    assert calls==[1] and tags.reference_images==[asset['id']]
    other=service.create_task(settings.model_copy(update={"goal":"different subject"}),[image],start=False)
    await service.tag_reference(other,service.settings(other),service.db.one('SELECT * FROM assets WHERE task_id=?',(other,)))
    assert len(calls)==1
