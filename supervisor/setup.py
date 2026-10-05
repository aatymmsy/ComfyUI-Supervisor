"""Form-based connection configuration, without persisting API key text."""

import hashlib
import base64
import io
import json
import html
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml
import httpx
from PIL import Image

from .comfy import Comfy
from .files import atomic_write
from .models import Binding, Credential, ModelConfig, Policy, Pricing, Provider, ProvidersConfig, WorkflowConfig


PRESETS = {
    "custom": "",
    "deepseek": "https://api.deepseek.com/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}


class ConnectionFailure(ValueError):
    def __init__(self, message, fields=()):
        super().__init__(message)
        self.fields = list(fields)


def connection_key(service, key, endpoint):
    if (key or "").strip():
        return key.strip()
    base, _ = endpoint_parts(endpoint)
    provider = next((p for p in service.config.providers if p.base_url == base), None)
    if provider:
        try:
            return service.cloud.credential_key(provider.credentials[0])
        except Exception:
            pass
    return None


def validate_connection_form(service, key, model, endpoint, vision_confirmed, verified_scope, upstreams):
    fields, names = [], []
    for field, missing, name in (
        ("endpoint", not (endpoint or "").strip(), "API 端点"),
        ("model", not (model or "").strip(), "提示词模型"),
        ("vision_confirmed", not vision_confirmed, "视觉能力确认"),
        ("scope_confirmed", not verified_scope, "上传范围确认"),
    ):
        if missing:
            fields.append(field)
            names.append(name)
    if not (key or "").strip() and (not endpoint or not connection_key_safe(service, endpoint)):
        fields.append("key")
        names.append("API Key")
    if endpoint:
        try:
            base, _ = endpoint_parts(endpoint)
            if urlsplit(base).hostname == "openrouter.ai" and not (upstreams or "").strip():
                fields.append("upstreams")
                names.append("OpenRouter 上游列表")
        except ValueError:
            fields.append("endpoint")
            names.append("有效的 HTTPS API 端点")
    if fields:
        raise ConnectionFailure("请补全或修正：" + "、".join(names), fields)


def connection_key_safe(service, endpoint):
    try:
        return connection_key(service, "", endpoint)
    except ValueError:
        return None


async def test_connection(service, key, model, endpoint, vision, strict_json=False, upstreams="", transport=None):
    base, chat = endpoint_parts(endpoint)
    key = connection_key(service, key, endpoint)
    if not key:
        raise ConnectionFailure("请输入 API Key", ["key"])
    vision = (vision or "").strip() or model
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, format="PNG")
    image_url = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode("ascii")
    async with httpx.AsyncClient(timeout=30, transport=transport or service.cloud.transport, trust_env=False, follow_redirects=False) as client:
        for selected in dict.fromkeys([model, vision]):
            is_vision = selected == vision
            content = [{"type": "text", "text": 'Return only JSON: {"ok":true}' }]
            if is_vision:
                content.append({"type": "image_url", "image_url": {"url": image_url}})
            body = {"model": selected, "messages": [{"role": "user", "content": content}], "max_tokens": 64,
                    "response_format": {"type": "json_object"}}
            if strict_json:
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "connection_test", "strict": True,
                    "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}}}
            if upstreams.strip():
                body["provider"] = {"only": [x.strip() for x in upstreams.split(",") if x.strip()], "allow_fallbacks": False, "require_parameters": True}
            if urlsplit(base).hostname == "api.deepseek.com" and selected in ("deepseek-flash","deepseek-v4-pro","deepseek-v4-flash"):
                body["thinking"] = {"type":"disabled"}
            try:
                response = await client.post(base + chat, headers={"Authorization": "Bearer " + key}, json=body)
            except httpx.TimeoutException:
                raise ConnectionFailure("连接超时，请检查 API 端点或网络", ["endpoint"]) from None
            except httpx.RequestError:
                raise ConnectionFailure("无法连接服务商，请检查 API 端点或网络", ["endpoint"]) from None
            if response.status_code >= 400:
                if response.status_code == 400 and strict_json:
                    try:
                        reason = str(response.json().get("error", {}).get("message", "")).lower()
                    except (ValueError, AttributeError):
                        reason = ""
                    if ("response_format" in reason or "json_schema" in reason) and any(word in reason for word in ("unavailable", "unsupported", "not supported")):
                        raise ConnectionFailure("该模型不支持严格 JSON Schema，请取消高级设置中的对应选项，使用 JSON 模式。", ["strict"])
                explanations = {401: ("API Key 无效或已过期", ["key"]), 403: ("无权调用该模型，请检查密钥权限", ["key"]),
                    404: ("API 端点或模型不存在", ["endpoint", "vision" if is_vision else "model"]),
                    429: ("服务商限流或额度不足", []), 400: ("模型或请求参数不受支持，请检查视觉能力及 JSON 支持", ["vision" if is_vision else "model"])}
                reason, fields = explanations.get(response.status_code, ("服务商请求失败", []))
                raise ConnectionFailure(f"{reason}（HTTP {response.status_code}）", fields)
            try:
                data = response.json()
                message = data["choices"][0]["message"]
                if message.get("refusal") or json.loads(message["content"]).get("ok") is not True:
                    raise ValueError("INVALID_PROBE_RESPONSE")
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                raise ConnectionFailure("模型未返回预期 JSON，连接测试失败", ["vision" if is_vision else "model"]) from None
    return key


async def discover_models(service, endpoint, key, transport=None):
    base, _ = endpoint_parts(endpoint)
    if not key and service.config.providers:
        provider = service.config.providers[0]
        if provider.base_url == base:
            key = service.cloud.credential_key(provider.credentials[0])
    if not key:
        raise ValueError("API_KEY_REQUIRED")
    async with httpx.AsyncClient(timeout=20, transport=transport, follow_redirects=False, trust_env=False) as client:
        response = await client.get(base + "/models", headers={"Authorization": "Bearer " + key})
        response.raise_for_status()
        body = response.json()
    if body.get("has_more") or body.get("next"):
        raise ValueError("MODEL_CATALOG_INCOMPLETE")
    return sorted({str(m["id"]) for m in body.get("data", []) if isinstance(m, dict) and m.get("id")})


FOLDER_DIALOG_SCRIPT = """
import json,sys,tkinter as tk
from tkinter import filedialog
sys.stdout.reconfigure(encoding='utf-8')
current=json.loads(sys.stdin.read())
root=tk.Tk()
root.withdraw()
root.attributes('-topmost',True)
try:
    selected=filedialog.askdirectory(parent=root,title='选择文件夹',initialdir=current or None,mustexist=True)
    print(json.dumps(selected,ensure_ascii=False))
finally:
    root.destroy()
"""


def choose_folder(current=""):
    # Tk must own a main thread; Gradio callbacks run on reusable worker threads.
    result = subprocess.run([sys.executable,"-c",FOLDER_DIALOG_SCRIPT],input=json.dumps(current or ""),
        text=True,encoding="utf-8",capture_output=True,check=True,
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    selected = json.loads(result.stdout)
    if not isinstance(selected,str):
        raise ValueError("INVALID_FOLDER_SELECTION")
    return selected or current


def workflow_input_value(graph, value, visited=None):
    if not isinstance(value, (list, dict)):
        return value
    if not isinstance(value, list) or len(value) != 2:
        return None
    node_id = str(value[0])
    visited = set(visited or ())
    if node_id in visited:
        return None
    visited.add(node_id)
    node = graph.get(node_id, {})
    inputs = node.get("inputs", {})
    kind = node.get("class_type", "")
    if kind == "ComfySwitchNode":
        switch = workflow_input_value(graph, inputs.get("switch"), visited)
        if isinstance(switch, bool):
            return workflow_input_value(graph, inputs.get("on_true" if switch else "on_false"), visited)
    if kind in ("Reroute", "Reroute (rgthree)") and len(inputs) == 1:
        return workflow_input_value(graph, next(iter(inputs.values())), visited)
    if kind.startswith("Primitive") or kind in ("INTConstant", "FloatConstant", "StringConstant"):
        for field in ("value", "int", "float", "string"):
            if field in inputs:
                return workflow_input_value(graph, inputs[field], visited)
    return None


def workflow_summary(graph):
    rows = []
    for node_id, node in graph.items():
        kind = node.get("class_type", "")
        if any(token in kind.lower() for token in ("unet", "checkpoint", "lora", "vae", "upscale")):
            params = {k: v for k, v in node.get("inputs", {}).items() if not isinstance(v, (list, dict))}
            for name, value in node.get("inputs", {}).items():
                if isinstance(value, list) and len(value) == 2:
                    resolved = workflow_input_value(graph, value)
                    if resolved is not None:
                        params[name] = resolved
                    elif name.endswith("_name") or name.startswith("strength"):
                        params[name] = f"未解析：节点 {value[0]} / 输出 {value[1]}"
                elif isinstance(value, dict) and "lora" in value:
                    params[name] = value
            if params:
                model_fields = ("unet_name", "ckpt_name", "lora_name", "vae_name", "model_name")
                models = [str(params.pop(name)) for name in model_fields if name in params]
                for name, value in list(params.items()):
                    if isinstance(value, dict) and "lora" in value:
                        models.append(str(value["lora"]))
                        params[name] = {key: item for key, item in value.items() if key != "lora"}
                rows.append([str(node_id), kind, ", ".join(models) or "-", json.dumps(params, ensure_ascii=False) if params else "-"])
    return rows


def workflow_summary_html(rows):
    if not rows:
        return '<div class="workflow-empty">尚未识别到模型或放大节点</div>'
    labels = {"strength_model": "模型强度", "strength_clip": "CLIP 强度", "weight_dtype": "权重精度",
              "scale_by": "放大倍数", "upscale_method": "放大方式", "width": "宽度", "height": "高度"}
    rendered = []
    for node_id, kind, model, parameters in rows:
        values = json.loads(parameters) if parameters != "-" else {}
        details = ''.join('<div class="workflow-param"><span>' + html.escape(labels.get(key, key)) +
                          '</span><strong>' + html.escape(json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value)) +
                          '</strong></div>' for key, value in values.items())
        rendered.append('<tr><td data-label="节点">' + html.escape(node_id) + '</td><td data-label="类型">' +
                        html.escape(kind) + '</td><td data-label="模型">' + html.escape(model) +
                        '</td><td data-label="参数">' + (details or '无额外参数') + '</td></tr>')
    return '<table class="workflow-table"><thead><tr><th>节点</th><th>类型</th><th>模型</th><th>参数</th></tr></thead><tbody>' + ''.join(rendered) + '</tbody></table>'


async def detect_workflow(service, address, transport=None):
    parsed = urlsplit(address)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("INVALID_COMFYUI_ADDRESS")
    async with httpx.AsyncClient(timeout=10, transport=transport, trust_env=False) as client:
        response = await client.get(address.rstrip("/") + "/queue")
        response.raise_for_status()
        queue = response.json()
        entries = queue.get("queue_running", []) or queue.get("queue_pending", [])
        source = "运行队列" if queue.get("queue_running") else "等待队列"
        if entries:
            graph = entries[0][2]
        else:
            response = await client.get(address.rstrip("/") + "/history", params={"max_items": 1})
            response.raise_for_status()
            history = list(response.json().values())
            if not history:
                raise ValueError("NO_EXECUTED_WORKFLOW")
            graph = history[-1]["prompt"][2]
            source = "最近执行记录"
    rows = workflow_summary(graph)
    # Recognition must still show model details when automatic bindings are unsupported.
    try:
        infer_workflow(graph, "workflows/detected-api.json", address)
    except ValueError as exc:
        return None, rows, "已识别：" + source + "；节点已展示，自动运行绑定需配置（" + str(exc) + "）"
    path = service.project_root / "workflows" / "detected-api.json"
    atomic_write(path, json.dumps(graph).encode("utf-8"))
    config = save_workflow(service, str(path), address)
    return config, workflow_summary(graph), "已识别：" + source


def endpoint_parts(endpoint):
    parsed = urlsplit(endpoint.strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("HTTPS_ENDPOINT_REQUIRED")
    path = parsed.path.rstrip("/")
    suffix = "/chat/completions"
    base_path = path[:-len(suffix)] if path.endswith(suffix) else path
    return urlunsplit((parsed.scheme, parsed.netloc, base_path, "", "")), suffix


def save_provider(service, preset, key, model_id, endpoint, vision_id, vision_confirmed, strict_json,
                  input_price, output_price, max_call, scope, verified_scope, upstreams, persist):
    model_id = (model_id or "").strip()
    vision_id = (vision_id or "").strip() or model_id
    if not model_id or not vision_confirmed:
        raise ValueError("MODEL_AND_CONFIRMED_VISION_CAPABILITY_REQUIRED")
    if not verified_scope:
        raise ValueError("CONFIRM_ENDPOINT_UPLOAD_SCOPE")
    base, chat = endpoint_parts(endpoint)
    credential = Credential(id="desktop", api_key_env="SUPERVISOR_DESKTOP_KEY")
    if persist:
        credential = Credential(id="desktop", secret_ref="keyring://comfyui-supervisor/desktop")
    pricing = Pricing(input_per_million_micro=int(input_price * 1_000_000),
        output_per_million_micro=int(output_price * 1_000_000), max_call_micro=int(max_call * 1_000_000),
        source="User-entered USD pricing and conservative call ceiling", verified_at=datetime.now(timezone.utc).isoformat())
    policy = Policy(nsfw_policy="allowed", allowed_content=[scope], allow_unknown_input=scope == "unknown",
        evidence="Endpoint upload scope confirmed by operator", review_due=(datetime.now(timezone.utc) + timedelta(days=90)).isoformat())
    models = [ModelConfig(id=value, text=True, vision=value == vision_id, json_schema=strict_json,
        pricing=pricing, max_output_tokens=4000) for value in dict.fromkeys([model_id, vision_id])]
    provider = Provider(id="desktop", name={"deepseek":"DeepSeek", "openrouter":"OpenRouter"}.get(preset, "Custom API"),
        base_url=base, chat_endpoint=chat, credentials=[credential], models=models, policy=policy,
        upstream_allowlist=[x.strip() for x in (upstreams or "").split(",") if x.strip()])
    config = ProvidersConfig(providers=[provider], routes={
        "tagging":["desktop/" + vision_id], "review":["desktop/" + vision_id], "prompt_generation":["desktop/" + model_id]})
    with service.active_lock:
        if service.active:
            raise ValueError("PAUSE_TASKS_BEFORE_CONFIGURATION")
        if key:
            if persist:
                import keyring
                keyring.set_password("comfyui-supervisor", "desktop", key)
            else:
                service.session_keys["SUPERVISOR_DESKTOP_KEY"] = key
        elif not (service.session_keys.get("SUPERVISOR_DESKTOP_KEY") or persist):
            raise ValueError("API_KEY_REQUIRED")
        atomic_write(service.provider_path, yaml.safe_dump(config.model_dump(), sort_keys=False).encode("utf-8"))
        service.reload_config()
    return json.dumps(config.model_dump(), indent=2)


def infer_workflow(graph, path, base_url):
    if not isinstance(graph, dict) or any(not isinstance(v, dict) or "class_type" not in v for v in graph.values()):
        raise ValueError("API_FORMAT_WORKFLOW_REQUIRED")
    samplers = [(k, v) for k, v in graph.items() if v["class_type"] == "KSampler"]
    if len(samplers) != 1:
        raise ValueError("COMPLEX_WORKFLOW_NEEDS_MANUAL_BINDINGS")
    sampler_id, sampler = samplers[0]
    bindings = {}

    def bind(field, node_id, input_name, kind, replace_text_link=False):
        node = graph.get(node_id, {})
        if input_name not in node.get("inputs", {}) or (isinstance(node["inputs"][input_name], list) and not replace_text_link):
            raise ValueError("MANUAL_BINDINGS_REQUIRED")
        bindings[field] = [Binding(node_id=node_id, class_type=node["class_type"], input=input_name, value_type=kind, replace_text_link=replace_text_link)]

    for field in ("positive", "negative"):
        connection = sampler["inputs"].get(field)
        if not isinstance(connection, list) or graph.get(connection[0], {}).get("class_type") != "CLIPTextEncode":
            raise ValueError("COMPLEX_CONDITIONING_NEEDS_MANUAL_BINDINGS")
        text = graph[connection[0]].get("inputs", {}).get("text")
        if isinstance(text, list):
            resolve_workflow_text(graph, text)
        bind(field, connection[0], "text", "string", isinstance(text, list))
    for field, input_name, kind in [("seed","seed","integer"), ("steps","steps","integer"),
                                  ("cfg","cfg","number"), ("sampler","sampler_name","string"), ("scheduler","scheduler","string")]:
        bind(field, sampler_id, input_name, kind)
    latent = sampler["inputs"].get("latent_image")
    visited = set()
    while isinstance(latent, list) and str(latent[0]) not in visited:
        node_id = str(latent[0])
        visited.add(node_id)
        node = graph.get(node_id, {})
        if node.get("class_type") != "ComfySwitchNode":
            break
        selector = node.get("inputs", {}).get("switch")
        if not isinstance(selector, bool):
            raise ValueError("LATENT_NEEDS_MANUAL_BINDINGS")
        latent = node["inputs"].get("on_true" if selector else "on_false")
    if not isinstance(latent, list) or graph.get(latent[0], {}).get("class_type") not in ("EmptyLatentImage", "EmptySD3LatentImage"):
        raise ValueError("LATENT_NEEDS_MANUAL_BINDINGS")
    for field in ("width", "height", "batch_size"):
        bind(field, latent[0], field, "integer")
    outputs = [k for k, v in graph.items() if v["class_type"] == "SaveImage"]
    if not outputs:
        raise ValueError("SAVEIMAGE_OUTPUT_REQUIRED")
    bindings["filename_prefix"] = [Binding(node_id=k, class_type="SaveImage", input="filename_prefix", value_type="string") for k in outputs]
    return WorkflowConfig(base_url=base_url.strip().rstrip("/"), workflow_api_json=path,
        workflow_hash=hashlib.sha256(json.dumps(graph).encode()).hexdigest(), bindings=bindings, output_nodes=outputs)


def resolve_workflow_text(graph, value, visited=None):
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("MANUAL_BINDINGS_REQUIRED")
    node_id = str(value[0])
    visited = set(visited or ())
    if node_id in visited:
        raise ValueError("MANUAL_BINDINGS_REQUIRED")
    visited.add(node_id)
    node = graph.get(node_id, {})
    inputs = node.get("inputs", {})
    if node.get("class_type") in ("PrimitiveString", "PrimitiveStringMultiline"):
        return resolve_workflow_text(graph, inputs.get("value"), visited)
    if node.get("class_type") == "StringConcatenate":
        delimiter = inputs.get("delimiter", "")
        if not isinstance(delimiter, str):
            raise ValueError("MANUAL_BINDINGS_REQUIRED")
        return delimiter.join(resolve_workflow_text(graph, inputs.get(name), visited) for name in ("string_a", "string_b"))
    if node.get("class_type") == "ComfySwitchNode" and isinstance(inputs.get("switch"), bool):
        return resolve_workflow_text(graph, inputs.get("on_true" if inputs["switch"] else "on_false"), visited)
    raise ValueError("MANUAL_BINDINGS_REQUIRED")


def workflow_defaults(service):
    if not service.workflow:
        return {}
    path = Path(service.workflow.workflow_api_json)
    graph = json.loads((path if path.is_absolute() else service.project_root / path).read_text(encoding="utf-8-sig"))
    result = {}
    for field, bindings in service.workflow.bindings.items():
        if field in ("width", "height", "steps", "cfg", "seed", "sampler", "scheduler") and bindings:
            binding = bindings[0]
            result[field] = graph[binding.node_id]["inputs"][binding.input]
    return result


def save_workflow(service, upload, base_url):
    if not upload:
        raise ValueError("WORKFLOW_FILE_REQUIRED")
    graph = json.loads(Path(upload).read_text(encoding="utf-8-sig"))
    return save_workflow_graph(service,graph,base_url)


def save_workflow_graph(service,graph,base_url):
    path = "workflows/desktop-api.json"
    config = infer_workflow(graph, path, base_url)
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("INVALID_COMFYUI_ADDRESS")
    with service.active_lock:
        if service.active or service.db.one("SELECT id FROM tasks WHERE state IN ('RUNNING','STOPPING') OR lease_until>? LIMIT 1",(time.time(),)):
            raise ValueError("PAUSE_TASKS_BEFORE_CONFIGURATION")
        atomic_write(service.project_root / path, json.dumps(graph).encode("utf-8"))
        atomic_write(service.workflow_path, yaml.safe_dump(config.model_dump(), sort_keys=False).encode("utf-8"))
        transport=service.comfy.transport if service.comfy else None
        service.reload_config()
        service.comfy.transport=transport
    return json.dumps(config.model_dump(), indent=2)
