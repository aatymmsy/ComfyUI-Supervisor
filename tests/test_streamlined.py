import json

import httpx
import pytest
from PIL import Image

from supervisor.setup import discover_models, detect_workflow, workflow_summary
from supervisor.metrics import image_checks
from supervisor.models import TaskSettings


async def test_discovery_before_saving_connection(service):
    def handler(request):
        assert str(request.url) == "https://example.test/v1/models"
        assert request.headers["Authorization"] == "Bearer transient-key"
        return httpx.Response(200, json={"data": [{"id": "vision"}, {"id": "text"}]})
    assert await discover_models(service, "https://example.test/v1/chat/completions", "transient-key", httpx.MockTransport(handler)) == ["text", "vision"]
    assert not service.config.providers


async def test_detect_running_workflow(service):
    service.project_root = service.data_root.parent / "project"
    service.project_root.mkdir(exist_ok=True)
    service.provider_path = service.project_root / "providers.yaml"
    service.workflow_path = service.project_root / "workflow.yaml"
    from pathlib import Path
    graph = json.loads((Path(__file__).resolve().parents[1] / "workflows/example-api.json").read_text())
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"queue_running": [[1, "prompt", graph]], "queue_pending": []}))
    config, rows, source = await detect_workflow(service, "http://127.0.0.1:8188", transport)
    assert "运行队列" in source
    assert service.workflow is not None
    assert rows == workflow_summary(graph)
    assert all("KSampler" not in row for row in rows)
    assert service.comfy.graph.__name__ == "graph"


def test_blur_and_phash(tmp_path):
    flat = tmp_path / "flat.png"
    sharp = tmp_path / "sharp.png"
    Image.new("RGB", (256, 256), "gray").save(flat)
    image = Image.new("RGB", (256, 256))
    image.putdata([(255, 255, 255) if (x // 8 + y // 8) % 2 else (0, 0, 0) for y in range(256) for x in range(256)])
    image.save(sharp)
    assert image_checks(flat)["blur_score"] == 0
    assert image_checks(sharp)["blur_score"] > 90
    assert image_checks(sharp)["phash"] != image_checks(flat)["phash"]


async def test_automatic_direct_cleanup(service, tmp_path):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), "red").save(ref)
    task = service.create_task(TaskSettings(goal="landscape", autonomous=True, groups=1, per_group=1,
        export_folder=str(tmp_path / "export"), images_only_delivery=True, quality_threshold=80,
        delete_mode="direct", allow_permanent_delete=True), [ref])
    await service.run(task)
    deleted = service.db.rows("SELECT * FROM assets WHERE task_id=? AND state='DELETED'", (task,))
    assert deleted
    assert all(not service.files.path(a["path"]).exists() for a in deleted)
    assert service.db.one("SELECT state FROM tasks WHERE id=?", (task,))["state"] == "COMPLETED"
    assert ref.exists()
    assert not list((tmp_path / "export").rglob("*.json"))
    assert list((tmp_path / "export").rglob("*.png"))
