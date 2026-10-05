from __future__ import annotations

import asyncio
import hashlib
import io
import json
import random
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
import websockets
from .workflow_bypass import apply_bypasses
from .workflow_switch import apply_switches
from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

from .db import Database, now
from .files import atomic_write
from .models import PromptPlan, WorkflowConfig, dump_json
from .workflow_metadata import editor_workflow, png_with_graph


class SubmissionUnknown(Exception):
    pass


class GenerationError(Exception):
    pass


class Comfy:
    def __init__(self, config: WorkflowConfig | None, project_root: Path, db: Database, transport=None):
        self.config = config
        self.project_root = project_root
        self.db = db
        self.transport = transport
        self.node_definitions = None

    async def workflow_metadata(self, client, generation):
        existing = self.db.one('SELECT workflow_json FROM generation_metadata WHERE generation_id=?', (generation['id'],))
        if existing:
            return json.loads(existing['workflow_json'])
        try:
            if self.node_definitions is None:
                response = await client.get(self.config.base_url.rstrip('/') + '/object_info')
                response.raise_for_status()
                self.node_definitions = response.json()
            workflow = editor_workflow(json.loads(generation['graph']), self.node_definitions)
            if workflow:
                self.db.execute('INSERT OR REPLACE INTO generation_metadata VALUES(?,?)', (generation['id'], dump_json(workflow)))
            return workflow
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None

    def fail(self, generation):
        with self.db.transaction() as c:
            changed = c.execute("UPDATE generations SET state='FAILED',updated_at=? WHERE id=? AND state<>'FAILED'", (now(), generation["id"]))
            if changed.rowcount:
                c.execute("UPDATE groups SET round_index=round_index+1,stale_rounds=stale_rounds+1 WHERE id=?", (generation["group_id"],))

    def graph(self, plan: PromptPlan, token: str):
        if not self.config:
            raise GenerationError("WORKFLOW_NOT_CONFIGURED")
        path = Path(self.config.workflow_api_json)
        if not path.is_absolute():
            path = self.project_root / path
        source = path.read_bytes()
        graph_hash = hashlib.sha256(source).hexdigest()
        if self.config.workflow_hash and graph_hash != self.config.workflow_hash:
            raise GenerationError("WORKFLOW_HASH_CHANGED")
        graph = json.loads(source)
        if not isinstance(graph, dict) or any(not isinstance(v, dict) or "class_type" not in v for v in graph.values()):
            raise GenerationError("API_FORMAT_WORKFLOW_REQUIRED")
        values = {"positive": plan.positive, "negative": plan.negative, **plan.params.model_dump(), "filename_prefix": "supervisor/" + token}
        for name, bindings in self.config.bindings.items():
            if name not in values:
                raise GenerationError("UNKNOWN_BINDING_FIELD")
            value = values[name]
            constraint = self.config.constraints.get(name, {})
            if isinstance(constraint, list) and value not in constraint:
                raise GenerationError("PARAMETER_NOT_ALLOWLISTED")
            if isinstance(constraint, dict):
                if ("min" in constraint and value < constraint["min"]) or ("max" in constraint and value > constraint["max"]) or ("multiple_of" in constraint and value % constraint["multiple_of"]):
                    raise GenerationError("PARAMETER_OUT_OF_RANGE")
            for binding in bindings:
                node = graph.get(binding.node_id)
                if not node or node.get("class_type") != binding.class_type or binding.input not in node.get("inputs", {}):
                    raise GenerationError("NODE_MAPPING_CHANGED")
                if isinstance(node["inputs"][binding.input], list):
                    if not (binding.replace_text_link and name in ("positive", "negative") and node["class_type"] == "CLIPTextEncode" and binding.input == "text"):
                        raise GenerationError("CANNOT_REPLACE_GRAPH_CONNECTION")
                if binding.value_type == "integer" and type(value) is not int or binding.value_type == "number" and type(value) not in (int, float) or binding.value_type == "string" and not isinstance(value, str):
                    raise GenerationError("BINDING_TYPE_MISMATCH")
                node["inputs"][binding.input] = value
        for output in self.config.output_nodes:
            if output not in graph or graph[output]["class_type"] != "SaveImage":
                raise GenerationError("OUTPUT_NODE_INVALID")
        if "filename_prefix" not in self.config.bindings:
            raise GenerationError("RECONCILIATION_PREFIX_BINDING_REQUIRED")
        for field in ["positive", "seed", "batch_size"]:
            if field not in self.config.bindings:
                raise GenerationError("REQUIRED_BINDING_MISSING")
        try:
            graph = apply_switches(graph, self.config.switch_nodes)
        except ValueError as exc:
            raise GenerationError('WORKFLOW_SWITCH_INVALID') from exc
        try:
            return apply_bypasses(graph, self.config.bypass_nodes)
        except ValueError as exc:
            raise GenerationError('WORKFLOW_BYPASS_INVALID') from exc

    async def run(self, generation, on_event, demo=False):
        if demo:
            return [{"node_id": "demo", "output_index": 0, "identity": generation["submission_token"], "demo": True}]
        if not self.config:
            raise GenerationError("WORKFLOW_NOT_CONFIGURED")
        url = self.config.base_url.rstrip("/")
        ws_url = self.config.ws_url or url.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/ws"
        ws_url += ("&" if "?" in ws_url else "?") + urlencode({"clientId": generation["client_id"]})
        prompt_id = generation.get("prompt_id")
        socket = None
        socket_prompt = None
        current_node = None
        try:
            try:
                socket = await websockets.connect(ws_url, open_timeout=5, max_size=4_000_000)
            except (OSError, TimeoutError, websockets.WebSocketException):
                on_event("WS_FALLBACK", {})
            async with httpx.AsyncClient(timeout=30, transport=self.transport, follow_redirects=False, trust_env=False) as client:
                if not prompt_id:
                    if generation["state"] != "PREPARED":
                        prompt_id = await self.reconcile(client, generation)
                        if not prompt_id:
                            raise SubmissionUnknown("SUBMISSION_REQUIRES_RECONCILIATION")
                    else:
                        with self.db.transaction() as connection:
                            active = connection.execute("SELECT id FROM generations WHERE id=? AND state='PREPARED' AND group_id IN (SELECT id FROM groups WHERE state<>'CANCELLED')", (generation['id'],)).fetchone()
                            if not active:
                                raise GenerationError('GROUP_CANCELLED')
                            connection.execute("UPDATE generations SET state='SUBMITTING',updated_at=? WHERE id=?", (now(), generation['id']))
                        workflow = await self.workflow_metadata(client, generation)
                        if self.db.one('SELECT state FROM groups WHERE id=?', (generation['group_id'],))['state'] == 'CANCELLED':
                            self.fail(generation)
                            raise GenerationError('GROUP_CANCELLED')
                        try:
                            response = await client.post(url + "/prompt", json={"prompt": json.loads(generation["graph"]), "client_id": generation["client_id"], "extra_data": {"supervisor_token": generation["submission_token"], **({'extra_pnginfo':{'workflow':workflow}} if workflow else {})}})
                            if response.status_code == 400:
                                self.fail(generation)
                                raise GenerationError("WORKFLOW_REJECTED")
                            response.raise_for_status()
                            prompt_id = response.json()["prompt_id"]
                        except (httpx.HTTPError, ValueError, KeyError):
                            self.db.execute("UPDATE generations SET state='SUBMISSION_UNKNOWN' WHERE id=?", (generation["id"],))
                            prompt_id = await self.reconcile(client, generation)
                            if not prompt_id:
                                raise SubmissionUnknown("POST_RESULT_UNKNOWN") from None
                    self.db.execute("UPDATE generations SET prompt_id=?,state='MONITOR',updated_at=? WHERE id=?", (prompt_id, now(), generation["id"]))
                deadline = time.monotonic() + self.config.timeout_seconds
                while time.monotonic() < deadline:
                    response = await client.get(url + "/history/" + prompt_id)
                    response.raise_for_status()
                    item = response.json().get(prompt_id)
                    if item:
                        status = item.get("status", {})
                        if status.get("status_str") == "error":
                            self.fail(generation)
                            raise GenerationError("COMFY_EXECUTION_ERROR")
                        outputs = []
                        for node in self.config.output_nodes:
                            for index, image in enumerate(item.get("outputs", {}).get(node, {}).get("images", [])):
                                if image.get("type", "output") != "output":
                                    continue
                                outputs.append({**image, "node_id": node, "output_index": index, "identity": dump_json([node, index, image.get("filename"), image.get("subfolder", "")])})
                        if status.get("completed") or status.get("status_str") == "success":
                            if not outputs:
                                raise GenerationError("NO_CONFIGURED_OUTPUTS")
                            return outputs
                    if socket:
                        try:
                            message = await asyncio.wait_for(socket.recv(), timeout=self.config.reconcile_interval)
                            if isinstance(message, str):
                                event = json.loads(message)
                                data = event.get("data", {})
                                kind = event.get("type", "")
                                if kind in ("execution_start", "executing") and data.get("prompt_id"):
                                    socket_prompt = data["prompt_id"]
                                scoped = data.get("prompt_id") == prompt_id or (kind == "progress" and not data.get("prompt_id") and socket_prompt == prompt_id)
                                if scoped:
                                    if kind == "executing":
                                        current_node = data.get("node")
                                    progress_data = {k: data[k] for k in ("node", "value", "max") if k in data}
                                    if kind == "progress" and "node" not in progress_data and current_node:
                                        progress_data["node"] = current_node
                                    on_event(kind, progress_data)
                                    if kind in ("execution_error", "execution_interrupted"):
                                        self.fail(generation)
                                        raise GenerationError("COMFY_" + kind.upper())
                        except asyncio.TimeoutError:
                            pass
                        except (websockets.WebSocketException, OSError):
                            socket = None
                            socket_prompt = None
                            on_event("WS_DISCONNECTED", {})
                            try:
                                socket = await websockets.connect(ws_url, open_timeout=5)
                            except (OSError, TimeoutError, websockets.WebSocketException):
                                pass
                    else:
                        await asyncio.sleep(self.config.reconcile_interval)
                raise GenerationError("EXECUTION_TIMEOUT_RESUME_MONITORING")
        finally:
            if socket:
                await socket.close()

    async def reconcile(self, client, generation):
        try:
            queue = (await client.get(self.config.base_url.rstrip("/") + "/queue")).json()
            history = (await client.get(self.config.base_url.rstrip("/") + "/history")).json()
        except (httpx.HTTPError, ValueError):
            return None
        candidates = queue.get("queue_running", []) + queue.get("queue_pending", [])
        candidates += [v.get("prompt", []) for v in history.values() if isinstance(v, dict)]
        matches = set()
        for row in candidates:
            if not isinstance(row, list) or len(row) < 4:
                continue
            extra = row[3]
            if isinstance(extra, dict) and extra.get("supervisor_token") == generation["submission_token"]:
                matches.add(str(row[1]))
            elif isinstance(row[2], dict):
                prefix = "supervisor/" + generation["submission_token"]
                if any(isinstance(n, dict) and n.get("inputs", {}).get("filename_prefix") == prefix for n in row[2].values()):
                    matches.add(str(row[1]))
        return next(iter(matches)) if len(matches) == 1 else None

    async def download(self, output, destination: Path, plan: PromptPlan, round_index, group_ordinal, generation=None):
        if output.get("demo"):
            demo_image(destination, plan, round_index, group_ordinal)
            return
        filename = output.get("filename", "")
        subfolder = output.get("subfolder", "")
        if not filename or "/" in filename or "\\" in filename or ".." in Path(subfolder.replace("\\", "/")).parts or Path(subfolder).is_absolute() or ":" in subfolder:
            raise GenerationError("INVALID_OUTPUT_PATH")
        workflow = None
        async with httpx.AsyncClient(timeout=60, transport=self.transport, follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", self.config.base_url.rstrip("/") + "/view", params={"filename": filename, "subfolder": subfolder, "type": "output"}) as response:
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 100_000_000:
                        raise GenerationError("OUTPUT_TOO_LARGE")
                    chunks.append(chunk)
            if generation:
                workflow = await self.workflow_metadata(client, generation)
        content = b''.join(chunks)
        if generation:
            content = png_with_graph(content, json.loads(generation['graph']), workflow)
        atomic_write(destination, content)


def demo_image(path: Path, plan: PromptPlan, round_index: int, group_ordinal: int):
    rng = random.Random(plan.params.seed)
    width, height = plan.params.width, plan.params.height
    palettes = [("#d9eee8", "#ef766f", "#387b72", "#194e48"), ("#eee6ef", "#e4b653", "#776880", "#3f3749")]
    sky, sun, mountain, foreground = palettes[group_ordinal % len(palettes)]
    image = Image.new("RGB", (width, height), sky)
    draw = ImageDraw.Draw(image)
    sx, sy, radius = width * 0.73, height * 0.24, width * 0.095
    draw.ellipse((sx - radius, sy - radius, sx + radius, sy + radius), fill=sun)
    for layer, color in [(0, mountain), (1, foreground)]:
        points = [(0, height)]
        for i in range(9):
            points.append((i * width / 8, height * (0.44 + layer * 0.22) + rng.randint(-height // 6, height // 8)))
        points.append((width, height))
        draw.polygon(points, fill=color)
    draw.polygon([(width * 0.38, height), (width * 0.52, height * 0.64), (width * 0.56, height * 0.64), (width * 0.64, height)], fill=sky)
    if round_index == 0:
        draw.rectangle((width // 3, height // 3, width * 2 // 3, height * 2 // 3), fill="#ef5072")
    info = PngInfo()
    info.add_text("supervisor_demo", "Synthetic CPU illustration, not ComfyUI output or cloud review")
    info.add_text("submitted_seed", str(plan.params.seed))
    stream = io.BytesIO()
    image.save(stream, format="PNG", pnginfo=info)
    atomic_write(path, stream.getvalue())
