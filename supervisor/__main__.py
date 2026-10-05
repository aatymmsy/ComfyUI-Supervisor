from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from .engine import Supervisor
from .models import Evaluation, ProvidersConfig, TaskSettings, WorkflowConfig


def main():
    parser = argparse.ArgumentParser(description="ComfyUI Supervisor")
    parser.add_argument("command", choices=["serve", "demo", "status", "schemas", "cleanup", "backup"], nargs="?", default="serve")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project/config directory (default: current working directory)")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--task")
    args = parser.parse_args()
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    load_dotenv(args.root / ".env", override=False)
    service = Supervisor(args.root, args.data, background=args.command == "serve")
    if args.command == "serve":
        os.environ["GRADIO_TEMP_DIR"] = str(service.data_root / "ui-cache")
        from .ui import launch
        service.executor.submit(lambda: asyncio.run(service.cloud.refresh()))
        # Resume only tasks that were actively running, never approval gates.
        for row in service.db.rows("SELECT id FROM tasks WHERE state IN ('RUNNING','STOPPING')"):
            service.enqueue(row["id"])
        try:
            launch(service, args.port)
        finally:
            service.close()
        return
    try:
        if args.command == "demo":
            settings = TaskSettings(goal="Mountain landscapes with distinct graphic styles", content_label="sfw", auto_iterations=True, auto_candidates=True, demo=True)
            task_id = service.create_task(settings)
            asyncio.run(service.run(task_id))
            service.approve_prompts(task_id)
            asyncio.run(service.run(task_id))
            print(json.dumps({"task_id": task_id, "task": service.db.one("SELECT state,reason FROM tasks WHERE id=?", (task_id,)), "delivery": service.db.one("SELECT manifest_path,sheet_path FROM deliveries WHERE task_id=?", (task_id,))}, indent=2))
        elif args.command == "status":
            print(json.dumps(service.db.rows("SELECT id,state,phase,reason,created_at FROM tasks WHERE phase<>'DISCUSSION' ORDER BY created_at DESC"), indent=2))
        elif args.command == "schemas":
            from .files import atomic_write
            for name, contract in [("providers", ProvidersConfig), ("workflow", WorkflowConfig), ("evaluation", Evaluation), ("task", TaskSettings)]:
                atomic_write(args.root / "schemas" / (name + ".schema.json"), json.dumps(contract.model_json_schema(), indent=2).encode("utf-8"))
            print("Schemas exported")
        elif args.command == "cleanup":
            if not args.task:
                parser.error("--task required")
            print(service.files.cleanup_due(args.task))
        elif args.command == "backup":
            target = service.data_root / "backup.db"
            service.db.backup(target)
            print(target)
    finally:
        service.close()


if __name__ == "__main__":
    main()
