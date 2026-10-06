from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import secrets
from functools import partial
from pathlib import Path

import gradio as gr
from PIL import Image

from .engine import Supervisor
from .control_words import parse_control_words, control_word_rows, parse_character_input, character_form_value, character_summary
from .models import PASS_SCORE, ProvidersConfig, TaskSettings, WorkflowConfig, dump_json
from .files import atomic_write, studio_reference_paths, reference_source_inputs, validate_sample_count, update_reference_sample, thumbnail_bytes, sha256
from .studio_features import PresetStore, StudioPreset, result_review_html, task_control_preview, control_preview_html
from .appearance import APPEARANCE_CSS, APPEARANCE_JS, translate, localized_action, translation_updates
from .setup import PRESETS, save_provider, save_workflow, discover_models, choose_folder, detect_workflow, workflow_summary, workflow_summary_html, validate_connection_form, test_connection, ConnectionFailure, workflow_defaults
from .studio import StudioFailure, check_live_ready, task_progress
from .generation_progress import generation_progress_html, PROGRESS_CSS
from .group_queue import group_queue_html, GROUP_QUEUE_CSS, GROUP_QUEUE_JS
from .startup_view import startup_html, startup_progress, STARTUP_CSS, STARTUP_JS
from .result_history import result_history
from .workflow_editor import read_workflow_editor, save_workflow_parameter, save_workflow_parameters, add_workflow_lora, lora_options, set_workflow_bypass, set_workflow_switch, cached_editor_rows
from .studio_view import STUDIO_VIEW_CSS, STUDIO_VIEW_JS, RESULT_PREVIEW_JS
from .frosted_view import FROSTED_CSS
from .node_editor_view import node_table, parameter_tabs, parameter_form, lora_form, NODE_EDITOR_CSS, NODE_EDITOR_JS, SCROLL_EDITOR_JS, CHARACTER_ROWS_JS, CHARACTER_ROWS_CSS
from .caption import configured_branch
from .image_workflow import image_workflow, ImageWorkflowError
from .image_prompt import embedded_prompts, enqueue_image_prompt
from .setup import save_workflow_graph, infer_workflow, resolve_workflow_text
from .result_delete import RESULT_DELETE_CSS, RESULT_DELETE_JS
from .result_batch import batch_from_result
from .result_retry import retry_from_result, retry_from_group
from .result_retry_view import RESULT_RETRY_CSS, RESULT_RETRY_JS
from .result_prompt import prompt_from_result
from .discussion import Discussion
from .result_batch_view import RESULT_BATCH_CSS, RESULT_BATCH_JS
from .action_feedback import ACTION_CSS, install_action_feedback
from .delivery import export_image_zip
from .upload_view import UPLOAD_CSS, UPLOAD_JS

CSS = """
.gradio-container { width: 100% !important; max-width: 1440px !important; min-width: 0 !important; box-sizing: border-box !important; margin: auto; }
.app-title { border-bottom: 1px solid #d9dedb; padding: 10px 0 16px; }
.app-title h1 { font-size: 20px; margin: 0; letter-spacing: 0; }
.app-title p { margin: 5px 0 0; color: #68736e; font-size: 13px; }
.block.status-band { border-left: 3px solid #287b62; padding: 9px 14px; background: #f1f6f3; }
.app-header { align-items: center !important; }
.top-toolbar { align-items: center !important; gap: 12px !important; }
.top-toolbar > .block { flex: 0 1 auto !important; width: auto !important; min-width: 120px !important; }
.compact-title { max-width: 280px !important; }
.scroll-section > button + div { max-height: 420px; overflow-y: auto; overflow-x: hidden; overscroll-behavior: contain; }
.gradio-container .block.gr-accordion.padded {padding:0 !important;}
.gr-accordion > button.label-wrap {position:relative; z-index:2; cursor:pointer; width:100%; box-sizing:border-box; min-height:44px; padding:12px 16px; margin:0;}
.gr-accordion > [data-testid=accordion-content] {padding:12px; box-sizing:border-box;}
.gr-accordion > [data-testid=status-tracker] {pointer-events:none;}
.app-title { padding: 4px 0 8px; }
.preferences { gap: 4px !important; }
.preferences .block { padding: 4px !important; }
.api-invalid { outline: 2px solid #dc2626 !important; outline-offset: -2px; }
.api-invalid label, .api-invalid .label { color: #dc2626 !important; }
.api-invalid input, .api-invalid textarea { border-color: #dc2626 !important; }
.caption-disabled { opacity: .42 !important; filter: grayscale(1); pointer-events: none !important; }
#workflow-image-uploader {max-width:300px !important;}
#caption-panel {gap:12px !important; justify-content:flex-start;}
#caption-uploader, #prompt-image-uploader {flex:0 0 220px !important; width:220px !important; min-width:0 !important; max-width:220px !important;}
#caption-input, #caption-workflow-input, #prompt-image-input {height:112px !important; min-height:112px !important; max-height:112px !important; box-sizing:border-box !important;}
#caption-panel .file-preview {width:100% !important; padding:0 !important;}
#caption-panel .file-preview tr {display:flex !important; width:100% !important;}
#caption-panel .file-preview td.filename {flex:1 !important; min-width:0 !important; width:auto !important; padding:4px !important;}
#caption-panel .file-preview td.download {flex:0 0 72px !important; min-width:0 !important; width:72px !important; padding:4px !important; font-size:11px !important;}
#caption-panel .image-prompt-note, #caption-panel .image-prompt-note p {font-size:12px !important; line-height:1.5 !important; margin:0 !important; color:var(--body-text-color-subdued);}
#caption-panel .upload-container p, #caption-panel .wrap p {font-size:13px !important;}
#workflow-image-status {font-size:12px;}
button, .block { border-radius: 6px !important; }
textarea { font-family: inherit !important; font-size: 14px !important; }
.workflow-table { width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 13px; }
.workflow-table th, .workflow-table td { text-align: left; vertical-align: top; padding: 10px; border-bottom: 1px solid var(--border-color-primary); overflow-wrap: anywhere; white-space: normal; }
.workflow-table th:nth-child(1) { width: 7%; }
.workflow-table th:nth-child(2) { width: 19%; }
.workflow-table th:nth-child(3) { width: 42%; }
.workflow-param { display: flex; flex-wrap: wrap; gap: 4px 10px; }
.workflow-param span { color: var(--body-text-color-subdued); }
.workflow-param strong { font-weight: 500; }
.workflow-empty { padding: 12px; }
@media(max-width: 700px) {
  .workflow-table, .workflow-table tbody, .workflow-table tr, .workflow-table td { display: block; width: 100%; box-sizing: border-box; }
  .workflow-table thead { display: none; }
  .workflow-table tr { padding: 8px 0; border-bottom: 1px solid var(--border-color-primary); }
  .workflow-table td { padding: 4px 8px; border: 0; }
  .workflow-table td::before { content: attr(data-label); display: block; font-size: 12px; color: var(--body-text-color-subdued); margin-bottom: 3px; }
  .gradio-container { padding: 12px !important; }
  .row, .column, .block, .tabs, .tabitem, .main { min-width: 0 !important; max-width: 100% !important; box-sizing: border-box !important; }
  .row > .column { flex-basis: 100% !important; }
  .tab-nav { flex-wrap: wrap !important; }
  button { white-space: normal !important; }
}
"""


def build_ui(service: Supervisor):
    discussion = Discussion(service)
    try:
        defaults = workflow_defaults(service)
    except (OSError, ValueError, KeyError):
        defaults = {}
    configured_nodes = []
    if service.workflow:
        path = Path(service.workflow.workflow_api_json)
        try:
            configured_nodes = workflow_summary(json.loads((path if path.is_absolute() else service.project_root / path).read_text(encoding="utf-8-sig")))
        except (OSError, ValueError):
            pass
    def task_choices(language="zh"):
        return [(f"{r['created_at'][5:16]} | {r['id'][:8]} | {translate('QUEUED' if r['phase']=='QUEUED' else r['state'],language)}", r["id"]) for r in service.db.rows("SELECT * FROM tasks WHERE phase<>'DISCUSSION' ORDER BY created_at DESC")]

    def refresh(task_id, language="en"):
        task = service.db.one("SELECT * FROM tasks WHERE id=?", (task_id,)) if task_id else None
        if not task:
            return translate("No task selected", language), {}, [], [], gr.update(choices=[]), None, None, gr.update(choices=[])
        settings = service.settings(task_id)
        groups = service.db.rows("SELECT * FROM groups WHERE task_id=? ORDER BY ordinal", (task_id,))
        stats = {"mode": "DEMO" if settings.demo else "LIVE", "state": task["state"], "phase": task["phase"], "reason": task["reason"], "budget": {**service.db.budget(task_id), "limit_micro": settings.budget_micro, "currency": settings.currency}, "groups": [{"group": g["ordinal"] + 1, "qualified": len(service.db.accepted(task_id, g["id"])), "target": settings.per_group, "round": g["round_index"], "best": g["best_score"]} for g in groups]}
        assets = service.db.rows("""SELECT a.*,d.action,e.effective_score FROM assets a
          LEFT JOIN decisions d ON d.asset_id=a.id AND d.rowid=(SELECT MAX(rowid) FROM decisions WHERE asset_id=a.id)
          LEFT JOIN evaluations e ON e.id=d.evaluation_id WHERE a.task_id=? ORDER BY a.created_at DESC""", (task_id,))
        gallery, table, choices, quarantine = [], [], [], []
        for a in assets:
            group = next((g["ordinal"] + 1 for g in groups if g["id"] == a["group_id"]), "ref")
            if a["state"] == "AVAILABLE":
                image_path = service.files.path(a['path'])
                if not image_path.is_file() and a['source_kind'] == 'reference' and a['thumb_path']:
                    image_path = service.files.path(a['thumb_path'])
                if len(gallery) < 50 and image_path.is_file():
                    gallery.append((str(image_path), f"G{group} | {translate(a['action'] or a['source_kind'], language)} | {a['effective_score'] or '-'}"))
                if a["source_kind"] == "generated":
                    choices.append((f"{a['id'][:8]} | {translate(a['action'] or 'pending', language)}", a["id"]))
            if a["state"] == "QUARANTINED":
                quarantine.append((a["id"][:8], a["id"]))
            table.append([a["id"][:8], str(group), translate(a["state"], language), translate(a["action"] or "-", language), a["effective_score"] if a["effective_score"] is not None else "-"])
        delivery = service.db.one("SELECT * FROM deliveries WHERE task_id=? ORDER BY created_at DESC LIMIT 1", (task_id,))
        manifest = str(service.files.path(delivery["manifest_path"])) if delivery else None
        sheet = str(service.files.path(delivery["sheet_path"])) if delivery else None
        qualified = sum(len(service.db.accepted(task_id,g['id'])) for g in groups if g['state']!='CANCELLED')
        target = sum(settings.per_group for group in groups if group['state']!='CANCELLED')
        count_label = "qualified" if settings.review_enabled else "saved without review"
        heading = f"**{stats['mode']} / {task['state']}** &nbsp; {qualified}/{target} qualified &nbsp; | &nbsp; {task['phase']}"
        heading = heading.replace(" qualified", " " + count_label)
        if language == "zh":
            count_label = "合格" if settings.review_enabled else "已保存（未审查）"
            heading = f"**{translate(stats['mode'], language)} / {translate(task['state'], language)}** &nbsp; {qualified}/{target} {count_label} &nbsp; | &nbsp; {translate(task['phase'], language)} <!-- {task['state']} -->"
        if task["state"] in ("PAUSED", "FAILED", "BLOCKED_POLICY", "WAITING_APPROVAL"):
            heading += "\n\n" + task_progress(service, task_id)
        return heading, stats, gallery, table, gr.update(choices=choices), manifest, sheet, gr.update(choices=quarantine)

    def safe_action(callback, *args):
        try:
            callback(*args)
            return "Saved"
        except Exception as exc:
            # Gradio exception details are not allowed to disclose transport payloads.
            return "Action rejected: " + type(exc).__name__

    def create(goal, groups, per_group, budget, rounds, threshold, label, mode, auto_iterations, auto_candidates, folder, uploads, width, height, steps, cfg, seed, delete_mode, retention, permanent, calibrated, max_generations, runtime_hours):
        try:
            settings = TaskSettings(goal=goal, groups=int(groups), per_group=int(per_group), budget_micro=int(budget * 1_000_000), max_rounds=int(rounds), quality_threshold=threshold, content_label=label, demo=mode == "Demo", auto_iterations=auto_iterations, auto_candidates=auto_candidates, input_folder=folder.strip() or None, delete_mode=delete_mode, retention_days=int(retention), allow_permanent_delete=permanent, calibrated=calibrated, max_generations=int(max_generations), max_runtime_seconds=int(runtime_hours * 3600), params={"width": int(width), "height": int(height), "steps": int(steps), "cfg": cfg, "seed": int(seed)})
            task_id = service.create_task(settings, [Path(x) for x in uploads or []])
        except Exception as exc:
            return gr.update(), "Task rejected: " + type(exc).__name__
        service.enqueue(task_id)
        return gr.update(choices=task_choices(), value=task_id), f"Task {task_id[:8]} created"

    def approve(task_id, card, plans):
        service.approve_prompts(task_id, card, plans)
        service.enqueue(task_id)

    def resume(task_id):
        current = service.db.one('SELECT state FROM tasks WHERE id=?', (task_id,))
        resumed_id = service.restart_task(task_id) if current and current['state']=='RUNNING' else service.resume(task_id)
        if service.round_ended():
            service.resume_round()
        service.enqueue(resumed_id)
        return resumed_id

    def continue_task(task_id, language):
        try:
            resumed_id = resume(task_id)
            restarted = resumed_id != task_id
            if language == 'zh':
                text = f'已开始新一轮 {resumed_id[:8]}：沿用上轮图量、提示词和生图配置，按顺序排队。' if restarted else '已继续任务，保留原进度并按顺序运行。'
            else:
                text = f'New round {resumed_id[:8]} queued with the previous targets, prompts and settings.' if restarted else 'Task resumed with its existing progress.'
            gr.Info(text)
            return gr.update(choices=task_choices(language),value=resumed_id),text
        except ValueError as exc:
            text = str(exc)
            gr.Warning(text)
            return gr.update(),text
        except Exception:
            text = '无法继续任务，请检查上轮工作流文件和输出目录。' if language == 'zh' else 'Unable to continue. Check the previous workflow file and output folder.'
            gr.Warning(text)
            return gr.update(),text

    def stop(task_id):
        service.stop(task_id)
        service.enqueue(task_id)

    def accept_all(task_id):
        service.approve_candidates(task_id)
        service.enqueue(task_id)

    def save_config(provider_text, workflow_text):
        providers = ProvidersConfig.model_validate_json(provider_text)
        workflow = WorkflowConfig.model_validate_json(workflow_text) if workflow_text.strip() not in ("", "null", "{}") else None
        with service.active_lock:
            if service.active:
                raise ValueError("Worker active")
        import yaml
        atomic_write(service.provider_path, yaml.safe_dump(providers.model_dump(), sort_keys=False).encode("utf-8"))
        if workflow:
            atomic_write(service.workflow_path, yaml.safe_dump(workflow.model_dump(), sort_keys=False).encode("utf-8"))
        service.reload_config()

    with gr.Blocks(title="ComfyUI Supervisor", analytics_enabled=False) as app:
        with gr.Column(elem_id="editor-page") as editor_page:
            with gr.Row(elem_classes=["top-toolbar"], elem_id="top-toolbar"):
                language = gr.Radio([("中文", "zh"), ("English", "en")], value="zh", show_label=False, container=False, elem_id="language-switch")
                with gr.Row(elem_id="appearance-controls"):
                    appearance = gr.Radio([("亮色", "light"), ("深色", "dark"), ("自定义", "custom")], value="light", show_label=False, container=False, elem_id="appearance-switch")
                    with gr.Accordion("外观", open=False, elem_id="appearance-compact") as appearance_panel:
                        gr.Markdown("配色与磨砂调整实时生效，仅保存在本机浏览器。")
                        appearance_preset = gr.Radio(choices=[("雾绿","mist"),("冰蓝","blue"),("暖砂","sand")],label="配色预设",value=None)
                        with gr.Row():
                            accent = gr.ColorPicker(value="#287b62", label="强调色", min_width=100)
                            mist = gr.ColorPicker(value="#e4f2e9", label="雾色", min_width=100)
                        glass_blur = gr.Slider(0,32,value=20,step=1,label="磨砂强度",elem_id="glass-blur")
                        glass_opacity = gr.Slider(45,95,value=66,step=1,label="面板不透明度",info="越高越清晰，越低越通透。",elem_id="glass-opacity")
                        drizzle = gr.Checkbox(value=False, label="烟雨")
                        reset_appearance = gr.Button("重置外观", size="sm")
                refresh_tasks = gr.Button("Refresh tasks", size="sm", scale=0)
                alerts = gr.Button("Enable completion alerts", size="sm", scale=0)
                open_results = gr.Button("View generated images →",size="sm",scale=0,elem_id="open-results")
                last_run_import = gr.Button('导入上次配置',size='sm',scale=0,elem_id='last-run-import')
            with gr.Row():
                queue_text = gr.Markdown(service.queue_status(),elem_id='queue-status')
                round_end = gr.Button('结束本轮工作',variant='stop',size='sm',scale=0,elem_id='round-end')
                round_resume = gr.Button('执行剩余队列',size='sm',scale=0,elem_id='round-resume')
            title = gr.HTML('<div class="app-title"><h1>ComfyUI Supervisor</h1></div>', elem_classes=["compact-title"])
            with gr.Row():
                choices = task_choices()
                live_tasks=[row for row in service.db.rows("SELECT id,settings,state FROM tasks ORDER BY created_at DESC") if not json.loads(row["settings"]).get("demo", True) and not json.loads(row["settings"]).get("reference_only")]
                latest_live = next((row['id'] for row in reversed(live_tasks) if row['state']=='RUNNING'),live_tasks[0]['id'] if live_tasks else None)
                task = gr.Dropdown(choices=choices, value=latest_live, label="Task", scale=2, min_width=180)
                message = gr.Textbox(label="Activity", value="Ready", interactive=False, scale=2, min_width=180)
            status = gr.Markdown("No task selected", elem_classes=["status-band"])
            with gr.Tabs(selected="studio" if service.config.providers or service.workflow or choices else "setup") as views:
                with gr.Tab("必填配置", id="setup",render_children=True):
                    setup_feedback = gr.Markdown("", elem_id="setup-feedback")
                    with gr.Row():
                        comfy_url = gr.Textbox(label="ComfyUI 地址 *", value=service.workflow.base_url if service.workflow else "http://127.0.0.1:8188", scale=4, elem_id="setup-comfy")
                        detect_comfy = gr.Button("连接并识别工作流", scale=1)
                    setup_workflow_status = gr.Markdown("尚未验证 ComfyUI 工作流")
                    configured = next(iter(service.config.providers), None)
                    configured_model = configured.models[0].id if configured and configured.models else ""
                    with gr.Row():
                        api_preset = gr.Dropdown(choices=[("Custom API", "custom"), ("DeepSeek", "deepseek"), ("OpenRouter", "openrouter")], value="deepseek" if configured and "api.deepseek.com" in configured.base_url else "openrouter" if configured and "openrouter.ai" in configured.base_url else "custom", label="Provider")
                        api_key = gr.Textbox(label="API Key *", type="password", info="系统密钥已保存，留空可复用" if configured and any(c.secret_ref for c in configured.credentials) else "Current session; optional system credential storage", elem_id="api-key")
                    api_endpoint = gr.Textbox(label="API 端点 *", value=(configured.base_url + configured.chat_endpoint) if configured else "", placeholder="https://your-provider.example/v1", elem_id="api-endpoint")
                    with gr.Row():
                        api_model = gr.Dropdown(label="提示词模型 *", choices=[configured_model] if configured_model else [], value=configured_model or None, allow_custom_value=True, elem_id="api-model")
                        api_vision = gr.Dropdown(label="视觉评审模型（可留空，使用提示词模型）", choices=[m.id for m in configured.models] if configured else [], value=next((m.id for m in configured.models if m.vision), None) if configured else None, allow_custom_value=True, elem_id="api-vision")
                        sync_api = gr.Button("同步可用模型", scale=0)
                    model_sync_status = gr.Markdown("")
                    api_catalog = gr.Dropdown(visible=False, choices=[], allow_custom_value=True)
                    api_vision_confirmed = gr.Checkbox(label="确认视觉模型支持图片理解 *", value=bool(configured and any(m.vision for m in configured.models)), elem_id="api-vision_confirmed")
                    with gr.Row():
                        api_scope = gr.Dropdown(["unknown", "sfw", "adult_allowed"], value=configured.policy.allowed_content[0] if configured and configured.policy.allowed_content else "unknown", label="图片内容范围 *", elem_id="api-scope")
                        api_scope_confirmed = gr.Checkbox(label="确认服务商允许上传所选内容 *", value=bool(configured and configured.policy.evidence), elem_id="api-scope_confirmed")
                    studio_scope = gr.Dropdown(["unknown", "sfw", "adult_allowed"], value=configured.policy.allowed_content[0] if configured and configured.policy.allowed_content else "unknown", visible=False)
                    api_upstreams = gr.Textbox(label="OpenRouter 上游列表（使用 OpenRouter 时必填）", value=", ".join(configured.upstream_allowlist) if configured else "", elem_id="api-upstreams", visible=bool(configured and "openrouter.ai" in configured.base_url))
                    api_errors = gr.Textbox(visible=False)
                    with gr.Accordion("Advanced API settings", open=False, elem_classes=["scroll-section"]) as api_advanced:
                        api_strict = gr.Checkbox(label="Supports strict JSON schema", value=bool(configured and configured.models and configured.models[0].json_schema), elem_id="api-strict")
                        api_persist = gr.Checkbox(label="Save key in system credential store", value=bool(configured and any(c.secret_ref for c in configured.credentials)))
                        with gr.Row():
                            api_input_price = gr.Number(label="Input USD / million tokens", value=configured.models[0].pricing.input_per_million_micro / 1_000_000 if configured and configured.models and configured.models[0].pricing else 0, minimum=0, elem_id="api-input_price")
                            api_output_price = gr.Number(label="Output USD / million tokens", value=configured.models[0].pricing.output_per_million_micro / 1_000_000 if configured and configured.models and configured.models[0].pricing else 0, minimum=0, elem_id="api-output_price")
                            api_call_limit = gr.Number(label="Conservative USD / call", value=configured.models[0].pricing.max_call_micro / 1_000_000 if configured and configured.models and configured.models[0].pricing else .05, minimum=.000001, elem_id="api-call_limit")
                    with gr.Row():
                        save_api = gr.Button("Save API connection", variant="primary")
                        enter_studio = gr.Button("检查配置并进入自动生图")
                    with gr.Accordion("Configuration JSON", open=False, elem_classes=["scroll-section"]):
                        provider_text = gr.Code(value=json.dumps(service.config.model_dump(), indent=2), language="json", label="Providers configuration", lines=20)
                        workflow_text = gr.Code(value=json.dumps(service.workflow.model_dump(), indent=2) if service.workflow else "null", language="json", label="Workflow bindings", lines=20)
                        save = gr.Button("Save configuration")
                    with gr.Row():
                        refresh_models = gr.Button("Refresh available models", visible=False)
                    catalog = gr.JSON(label="Model catalog", visible=False)
                with gr.Tab("Workflow parameters",id="workflow-editor",render_children=True) as workflow_editor_tab:
                    with gr.Accordion("ComfyUI 工作流", open=False, elem_classes=["scroll-section"], elem_id='workflow-import-panel'):
                        workflow_source = gr.Textbox(label="识别来源", interactive=False, value="尚未识别" if not service.workflow else "已配置工作流")
                        workflow_nodes = gr.HTML(value=workflow_summary_html(configured_nodes), elem_id="workflow-nodes", visible=False)
                        with gr.Row():
                            workflow_upload = gr.File(label="拖入其他工作流（API JSON）", file_types=[".json"], type="filepath", height=150, elem_id='workflow-api-upload', elem_classes=['supervisor-upload'])
                            save_comfy = gr.Button("Import workflow", scale=0)
                    with gr.Row():
                        with gr.Column(scale=0,min_width=260,elem_id='workflow-image-uploader'):
                            workflow_image_upload = gr.File(label='上传图片识别工作流',file_types=['.png','.webp','.jpg','.jpeg'],type='filepath',height=112,elem_id='workflow-image-upload',elem_classes=['supervisor-upload','supervisor-image-upload'])
                        workflow_image_status = gr.Markdown('需包含 ComfyUI 工作流元数据；推荐使用原始 PNG。上传后自动导入。',elem_id='workflow-image-status')
                    gr.Markdown("下表读取本应用已导入的工作流，不会读取 ComfyUI 画布上尚未执行的修改。更换工作流请上传 API JSON 或原图；表下 LoRA 列表是本机可添加的模型，不代表已加载。绕过和分支选择开始新任务时生效；其他参数需保存。")
                    editor_refresh = gr.Button("Read workflow parameters",elem_id="editor-refresh")
                    editor_table = gr.HTML(value=node_table([]),elem_id="editor-table")
                    editor_lora = gr.HTML(elem_id='editor-lora')
                    editor_bulk_request = gr.Textbox(value='',show_label=False,elem_id='editor-bulk-request')
                    editor_bulk_trigger = gr.Button('保存节点修改',elem_id='editor-bulk-trigger')
                    editor_bypass_request = gr.Textbox(value='',show_label=False,elem_id='editor-bypass-request')
                    editor_bypass_trigger = gr.Button('绕过节点',elem_id='editor-bypass-trigger')
                    editor_rows = gr.State([])
                    editor_field = gr.Textbox(show_label=False,container=False,elem_id="editor-field")
                    with gr.Column(elem_id="editor-controls"):
                        editor_parameters = gr.HTML(elem_id="editor-parameters")
                        with gr.Column(visible=False):
                            editor_choice = gr.Dropdown(label="Model or option",choices=[],allow_custom_value=True,visible=False,elem_id="editor-model")
                            editor_number = gr.Number(label="Parameter value",visible=False,elem_id="editor-number")
                            editor_boolean = gr.Checkbox(label="Parameter switch",visible=False)
                            editor_text = gr.Textbox(label="Parameter value",visible=False)
                            editor_save = gr.Button("Save workflow parameter",variant="primary",elem_id="editor-save")
                        editor_status = gr.Markdown("",elem_id="editor-status")
                with gr.Tab("Image studio", id="studio",render_children=True):
                    with gr.Row():
                        with gr.Column(scale=2, min_width=360):
                            with gr.Column(elem_id="studio-reference-source"):
                                with gr.Row(elem_id="reference-source-settings"):
                                    reference_source = gr.Radio(choices=[("选图片","images"),("选文件夹","folder")],value="images",label="参考来源（任选一种）",elem_id="reference-source",scale=0,min_width=220)
                                    with gr.Column(scale=0,min_width=300,elem_id="studio-reference-count"):
                                        with gr.Row(elem_id="reference-count-header"):
                                            gr.Markdown("抽样",elem_id="reference-count-label")
                                            sample_slider = gr.Slider(1,50,value=15,step=1,precision=0,label="参考图数量滑条（1–50 张）",show_label=False,container=False,scale=1,min_width=140,elem_id="reference-count-slider")
                                            sample_count = gr.Number(value=15,minimum=1,precision=0,step=1,label="参考图数量（默认 15 张，输入不设上限）",show_label=False,container=False,scale=0,min_width=64,elem_id="reference-count-input")
                                            sample_reset = gr.Button("↺",size="sm",scale=0,min_width=28,elem_id="reference-count-reset")
                                            sample_shuffle = gr.Button("换一批",size="sm",scale=0,min_width=60,elem_id="reference-shuffle")
                                with gr.Column(elem_id="reference-images") as reference_images:
                                    studio_uploads = gr.File(label="参考图片 *", file_count="multiple", file_types=["image"], type="filepath", height=110, elem_id="studio-references", elem_classes=['supervisor-upload','supervisor-image-upload'])
                                with gr.Column(visible=False,elem_id="reference-folder") as reference_folder:
                                    browse_reference = gr.Button("选择文件夹…",elem_id="reference-folder-picker")
                                    studio_folder = gr.Textbox(label="已选文件夹",value="",interactive=False,placeholder="点击上方按钮选择，路径自动填写",info="按设置的参考图数量随机抽样，只缓存抽中的图片。",elem_id="studio-folder")
                                upload_status = gr.Markdown("尚未选择参考图片")
                                reference_preview_open = gr.State(False)
                                with gr.Accordion("抽样缩略图",open=False,elem_id="reference-preview-panel") as reference_preview_panel:
                                    with gr.Row():
                                        reference_page=gr.Number(value=1,minimum=1,precision=0,label="页码",scale=0,min_width=70)
                                        reference_show=gr.Button("查看缩略图",size="sm",scale=0)
                                        reference_page_status=gr.Markdown("每页最多 12 张；展开时读取抽中图片。")
                                    reference_preview = gr.Gallery(label="Selected references", columns=6, height=180, object_fit="contain", buttons=["fullscreen"], visible=False)
                            sampling_seed = gr.State(value=lambda: secrets.randbits(32))
                            studio_directory = gr.State({"source":"images"})
                            with gr.Accordion("常用方案",open=False,elem_id="studio-presets"):
                                with gr.Row():
                                    preset_choice=gr.Dropdown(label="已保存方案",choices=[])
                                    preset_name=gr.Textbox(label="方案名称",placeholder="例如：人物暖光")
                                with gr.Row():
                                    preset_load=gr.Button("载入方案",size="sm")
                                    preset_save=gr.Button("保存方案",size="sm")
                                preset_status=gr.Markdown("保存参考数量、主题、控制词和生成参数；同名保存会更新方案。")
                            studio_goal = gr.Textbox(label="Shared requirements *", value="学习参考图的提示词写法、权重与细节表达；每组独立设计镜头、动作和构图，不复刻人物或画面", lines=2, elem_id="studio-goal")
                            control_line_count = gr.State(1)
                            control_inputs,control_lines,control_groups,control_delete_buttons = [],[],[],[]
                            with gr.Row(elem_id="studio-theme-columns"):
                                with gr.Column(scale=1,min_width=0,elem_id="studio-theme-count"):
                                    studio_count = gr.Number(value=2,minimum=1,maximum=20,precision=0,label="Theme groups",info="每组使用独立提示词。",elem_id="studio-groups")
                                    studio_per_group = gr.Number(value=1,minimum=1,maximum=50,precision=0,label="每组产出张数",info="合格图目标；总张数 = 组数 × 每组张数，仍受轮数、预算和评审限制。",elem_id="studio-per-group")
                                    studio_group_status = gr.Markdown("已填写 2 / 2 组主题 · 目标 2 张合格图",elem_id="studio-group-status")
                                with gr.Column(scale=1,min_width=0,elem_id="studio-control-words"):
                                    gr.Markdown("**控制词（可选）**")
                                    for index in range(30):
                                        with gr.Row(visible=index==0,elem_classes=["control-word-line"]) as control_line:
                                            group_number = gr.Textbox(label="组号",value="1",placeholder="全部 或 1,2",scale=1,min_width=44)
                                            word = gr.Textbox(label="控制词 / tags",placeholder="柔和光线, 逆光, 暖色调",scale=1,min_width=44)
                                            weight = gr.Textbox(label="权重",value="",placeholder="自动",scale=1,min_width=44)
                                            control_delete_buttons.append(gr.Button("×",size="sm",scale=1,min_width=0,elem_id=f"control-word-delete-{index}",elem_classes=["control-word-delete"]))
                                        control_lines.append(control_line)
                                        control_groups.append(group_number)
                                        control_inputs.extend([group_number,word,weight])
                                    control_add = gr.Button("＋",size="sm",elem_id="control-word-add")
                                    gr.Markdown("组号填“全部”可应用到所有组；或用逗号指定组号，如 1,2,3。同行控制词共用权重（留空自动），权重 0.1–3，最多 30 行。")
                                    studio_control_scope=gr.Markdown('',elem_id='studio-control-scope')
                                with gr.Column(scale=1,min_width=0,elem_id="studio-theme-list"):
                                    studio_styles = gr.Textbox(label="Themes (one per line)", value="江南水墨\n电影摄影", lines=4, info="不足组数时模型随机补足，多于组数时模型随机选取；留空则全部随机。选定主题会固定到任务中。", elem_id="studio-styles")
                            studio_mode = gr.Radio(["Live"], value="Live", label="Mode", visible=False)
                            studio_character = gr.Textbox(value='',show_label=False,elem_id="studio-character",elem_classes=['result-delete-bridge'])
                            gr.HTML('<div class="character-rows-editor"><strong>控制角色（可选）</strong><p>提示词没有角色特征时使用此项，大模型会补充基本外观。每行指定组号，留空或填“全部”作用于全部组。</p><div class="character-rows"></div><button type="button" class="character-add" aria-label="添加控制角色">＋</button><span class="character-rows-status" role="status"></span></div>',elem_id='studio-character-rows')
                            studio_lora_triggers = gr.Textbox(label="LoRA 触发词（可选）",placeholder="例：my_style, character_tag；填后自动加入正向提示词",lines=1,max_lines=2,elem_id="studio-lora-triggers")
                            with gr.Accordion("控制词与实际权重",open=False,elem_id="studio-control-preview") as control_preview_panel:
                                control_preview_button=gr.Button("预览当前控制词",size="sm")
                                configured_control_preview=gr.HTML('<p>点击预览查看当前填写的各组 tag 和权重。</p>')
                                actual_control_preview=gr.HTML('<p>开始生图后显示本轮实际使用的控制词权重。</p>')
                            with gr.Row():
                                studio_output = gr.Textbox(label="输出文件夹 *", value="", placeholder="请浏览选择输出文件夹", scale=4, elem_id="studio-output")
                                browse_output = gr.Button("浏览…", scale=0, min_width=110)
                            studio_zip=gr.Checkbox(value=False,label="以压缩包交付",info="默认将图片直接存入输出文件夹；也可以生成后再打包。")
                            with gr.Row(elem_id="studio-limits"):
                                studio_rounds = gr.Slider(1, 100, value=6, step=1, label="Max rounds / group", info="Maximum attempts per group; qualifying images, stalled scores or other limits can end the task earlier.", elem_id="studio-rounds", min_width=180)
                                studio_threshold = gr.Slider(0, 100, value=PASS_SCORE, step=1, label="Minimum quality score", info="综合分达到设置值才保存，并检查明显畸形、重复和安全。", elem_id="studio-quality", elem_classes=["pass-slider"], min_width=180)
                            studio_apply_limits = gr.Button("Apply to current task", size="sm", elem_id="studio-apply-limits")
                            studio_limit_status = gr.Markdown("", elem_id="studio-limit-status")
                            with gr.Accordion("Generation & limits", open=False, elem_classes=["scroll-section"]):
                                with gr.Row():
                                    studio_width = gr.Number(value=defaults.get("width", 768), minimum=64, maximum=4096, precision=0, label="Width")
                                    studio_height = gr.Number(value=defaults.get("height", 768), minimum=64, maximum=4096, precision=0, label="Height")
                                with gr.Row():
                                    studio_steps = gr.Number(value=defaults.get("steps", 20), minimum=1, maximum=100, precision=0, label="Steps")
                                    studio_cfg = gr.Number(value=defaults.get("cfg", 7), minimum=0, maximum=30, label="CFG")
                                studio_seed = gr.Number(value=defaults.get("seed", 42), minimum=0, precision=0, label="Base seed")
                                with gr.Row():
                                    studio_sampler = gr.Textbox(value=defaults.get("sampler", "euler"), label="采样器")
                                    studio_scheduler = gr.Textbox(value=defaults.get("scheduler", "normal"), label="调度器")
                                studio_budget = gr.Number(value=1, minimum=0, label="Budget (USD)")
                                studio_token_budget = gr.Number(value=50000,minimum=1000,maximum=2000000,precision=0,label="任务 token 上限",info="达到上限后停止新 API 请求；当前调用可能略超出")
                                studio_hours = gr.Number(value=2, minimum=.01, maximum=24, label="Wall-clock limit (hours)")
                                studio_examples = gr.Textbox(label="Prompt examples (optional)", value="", lines=2)
                            studio_feedback = gr.Markdown("", elem_id="studio-feedback")
                            studio_errors = gr.Textbox(visible=False)
                            studio_start = gr.Button("Start automatic generation", variant="primary",elem_id="studio-start")
                            studio_start_status = gr.HTML(startup_html(),elem_id="studio-start-status")
                            with gr.Row():
                                studio_stop = gr.Button("Stop after current output", variant="stop")
                                studio_resume = gr.Button("Resume")
                    studio_gallery = gr.Gallery(visible=False)
                    saved_folder = gr.Textbox(label="Saved folder", interactive=False, info="合格图通过评审后立即保存；新任务默认直接写入输出目录，文件名标注任务和组号。待处理图保留在本机任务中。")
                    runtime_generation_meter = gr.HTML(elem_id="runtime-generation-meter")
                    runtime_progress = gr.Markdown("等待开始", elem_id="runtime-progress")
                    with gr.Accordion("评审与提示词迭代", open=False, elem_classes=["scroll-section"]):
                        learned = gr.Markdown()
                        study = gr.Dataframe(headers=["Image", "Subject", "Visual style", "Original prompt", "Writing style"], label="Reference analysis", interactive=False, wrap=True)
                        studio_prompts = gr.Dataframe(headers=["Group", "Theme", "Round", "Positive prompt", "Model reasoning"], label="Prompt evolution", interactive=False, wrap=True)
                        studio_reviews = gr.Dataframe(headers=["Image", "Group", "Decision", "Score", "Evidence"], label="Cloud review", interactive=False, wrap=True)
                with gr.Tab("Prompt studio", id="prompt-studio",render_children=True):
                    gr.Markdown("填写提示词后，按当前工作流生成图片。模型和关键节点参数可在「工作流参数」栏目修改。")
                    with gr.Row(elem_id='caption-panel'):
                        with gr.Column(scale=0,min_width=200,elem_id='caption-uploader'):
                            caption_available = gr.Checkbox(value=bool(configured_branch(service)),visible=False)
                            caption_input = gr.File(label='图片反推',type='filepath',file_types=['image'],height=96,
                                interactive=bool(configured_branch(service)),elem_id='caption-input',
                                elem_classes=['supervisor-upload','supervisor-image-upload'] + ([] if configured_branch(service) else ['caption-disabled']))
                            caption_workflow_input = gr.File(label='上传图片识别工作流',file_types=['.png','.webp','.jpg','.jpeg'],type='filepath',height=96,visible=False,elem_id='caption-workflow-input',elem_classes=['supervisor-upload','supervisor-image-upload'])
                            caption_workflow_mode = gr.Checkbox(label='传图识别工作流',value=False,elem_id='caption-workflow-mode')
                            caption_status = gr.Markdown('仅运行工作流反推分支，填入正向提示词。' if configured_branch(service) else '当前工作流没有反推分支；可用右侧图片识别。',elem_id='caption-status',elem_classes=['image-prompt-note'])
                        with gr.Column(scale=0,min_width=200,elem_id='prompt-image-uploader'):
                            # File preserves original metadata; Image preprocessing can strip it.
                            prompt_image_input = gr.File(label='输入图片识别',file_types=['.png','.webp','.jpg','.jpeg'],type='filepath',height=96,elem_id='prompt-image-input',elem_classes=['supervisor-upload','supervisor-image-upload'])
                            prompt_image_status = gr.Markdown('优先读取元数据提示词；没有则用视觉模型反推，填入正负提示词。',elem_id='prompt-image-status',elem_classes=['image-prompt-note'])
                            with gr.Accordion('模型反推限额',open=False):
                                image_prompt_budget = gr.Number(value=.1,minimum=0,label='预算上限（USD）')
                                image_prompt_tokens = gr.Number(value=5000,minimum=1000,maximum=2000000,precision=0,label='Token 上限')
                    with gr.Row():
                        with gr.Column(scale=2, min_width=360):
                            direct_positive = gr.Textbox(label="Positive prompt *", lines=5, elem_id="direct-positive")
                            direct_negative = gr.Textbox(label="Negative prompt", lines=3, elem_id="direct-negative")
                            direct_result_status = gr.Markdown('',elem_id='direct-result-status')
                            direct_count = gr.Number(value=2,minimum=1,maximum=50,precision=0,label="Number of images")
                            direct_review = gr.Checkbox(value=False,label="Enable quality review",info="Off: save every generated image. On: send images to the configured vision model and save those passing review.",elem_id="direct-review")
                            with gr.Column(visible=False) as direct_review_limits:
                                direct_threshold = gr.Slider(0,100,value=PASS_SCORE,step=1,label="Minimum quality score",info="综合分达到设置值才保存，并检查明显畸形、重复和安全。",elem_id="direct-quality",elem_classes=["pass-slider"])
                                direct_rounds = gr.Slider(1,100,value=12,step=1,label="Max rounds / group")
                                direct_budget = gr.Number(value=1,minimum=0,label="Budget (USD)")
                                direct_tokens = gr.Number(value=50000,minimum=1000,maximum=2000000,precision=0,label="任务 token 上限")
                            with gr.Row():
                                direct_output = gr.Textbox(label="输出文件夹 *",placeholder="请浏览选择输出文件夹",elem_id="direct-output",scale=4)
                                direct_browse = gr.Button("浏览…",scale=0,min_width=110)
                            direct_zip=gr.Checkbox(value=False,label="以压缩包交付",info="默认将图片直接存入输出文件夹；也可以生成后再打包。")
                            with gr.Accordion("Generation parameters",open=False,elem_classes=["scroll-section"]):
                                with gr.Row():
                                    direct_width = gr.Number(value=defaults.get("width",768),minimum=64,maximum=4096,precision=0,label="Width")
                                    direct_height = gr.Number(value=defaults.get("height",768),minimum=64,maximum=4096,precision=0,label="Height")
                                with gr.Row():
                                    direct_steps = gr.Number(value=defaults.get("steps",20),minimum=1,maximum=100,precision=0,label="Steps")
                                    direct_cfg = gr.Number(value=defaults.get("cfg",7),minimum=0,maximum=30,label="CFG")
                                direct_seed = gr.Number(value=defaults.get("seed",42),minimum=0,precision=0,label="Base seed")
                                direct_sampler = gr.Textbox(value=defaults.get("sampler","euler"),label="采样器")
                                direct_scheduler = gr.Textbox(value=defaults.get("scheduler","normal"),label="调度器")
                                direct_scope = gr.Dropdown(["sfw","adult_allowed"],value="sfw",label="User-confirmed content scope")
                                direct_hours = gr.Number(value=2,minimum=.01,maximum=24,label="Wall-clock limit (hours)")
                            direct_feedback = gr.Markdown("",elem_id="direct-feedback")
                            direct_start = gr.Button("Generate from prompt",variant="primary",elem_id="direct-start")
                            direct_start_status = gr.HTML(startup_html(),elem_id="direct-start-status")
                            with gr.Row():
                                direct_stop = gr.Button("Stop after current output",variant="stop")
                                direct_resume = gr.Button("Resume")
                    direct_gallery = gr.Gallery(visible=False)
                    direct_saved = gr.Textbox(label="Saved folder",interactive=False)
                    direct_progress = gr.Markdown("等待提示词生图任务",elem_id="direct-progress")
                    with gr.Accordion("Review results",open=False,elem_classes=["scroll-section"]):
                        direct_reviews = gr.Dataframe(headers=["Image","Group","Decision","Score","Evidence"],interactive=False,wrap=True)
                with gr.Tab("人工干预",id="intervention",render_children=True):
                    gr.Markdown('缓存自动清理：下次工作开始时检查，此后每天检查一次，清理已结束任务的参考图副本、临时文件及已核对导出的交付副本；保留生成原图与任务记录。')
                    with gr.Row():
                        with gr.Column(scale=1, min_width=280, visible=False):
                            goal = gr.Textbox(label="Group goal", value="不同画风的山水景色", lines=3)
                            mode = gr.Radio(["Demo", "Live"], value="Demo", label="Mode")
                            with gr.Row():
                                groups = gr.Number(value=2, minimum=1, maximum=20, precision=0, label="Groups", min_width=90)
                                per_group = gr.Number(value=2, minimum=1, maximum=50, precision=0, label="Images per group", min_width=90)
                            with gr.Row():
                                budget = gr.Number(value=1, minimum=0, label="Budget (USD)", min_width=90)
                                rounds = gr.Number(value=8, minimum=1, maximum=100, precision=0, label="Max rounds / group", min_width=90)
                            threshold = gr.Slider(0, 100, value=PASS_SCORE, step=1, label="Quality threshold")
                            label = gr.Dropdown(["unknown", "sfw", "adult_allowed"], value="unknown", label="User-confirmed content scope")
                            folder = gr.Textbox(label="Reference folder", placeholder="Absolute folder path")
                            uploads = gr.File(label="Reference images", file_count="multiple", file_types=["image"], type="filepath", elem_id='manual-references', elem_classes=['supervisor-upload','supervisor-image-upload'])
                            with gr.Accordion("Generation parameters", open=False, elem_classes=["scroll-section"]):
                                with gr.Row():
                                    width = gr.Number(value=768, precision=0, label="Width")
                                    height = gr.Number(value=768, precision=0, label="Height")
                                with gr.Row():
                                    steps = gr.Number(value=20, precision=0, label="Steps")
                                    cfg = gr.Number(value=7, label="CFG")
                                seed = gr.Number(value=42, precision=0, label="Base seed")
                            auto_iterations = gr.Checkbox(value=True, label="Approve bounded additive iterations")
                            auto_candidates = gr.Checkbox(value=False, label="Automatically accept qualified candidates")
                            with gr.Accordion("Cleanup policy", open=False, elem_classes=["scroll-section"]):
                                delete_mode = gr.Dropdown(["quarantine", "delayed", "direct"], value="quarantine", label="Delete mode")
                                retention = gr.Number(value=7, minimum=1, maximum=365, precision=0, label="Retention days")
                                calibrated = gr.Checkbox(value=False, label="Reviewer calibrated")
                                permanent = gr.Checkbox(value=False, label="Allow permanent deletion")
                            with gr.Accordion("Task limits", open=False, elem_classes=["scroll-section"]):
                                max_generations = gr.Number(value=1000, minimum=1, maximum=5000, precision=0, label="Max generation attempts")
                                runtime_hours = gr.Number(value=2, minimum=0.01, maximum=24, label="Wall-clock limit (hours)")
                            start = gr.Button("Create task", variant="primary")
                        with gr.Column(scale=3, min_width=320):
                            with gr.Accordion("Progress & budget", open=False, elem_classes=["scroll-section"]):
                                stats = gr.JSON(label="Task details")
                            with gr.Row():
                                resume_button = gr.Button("Resume")
                                stop_button = gr.Button("Stop after current output", variant="stop")
                                accept_button = gr.Button("Accept qualified candidates",visible=False)
                            gallery = gr.Gallery(label="Task images", columns=3, height=420, object_fit="contain", buttons=["download", "download_all", "fullscreen"])
                            table = gr.Dataframe(headers=["Asset", "Group", "File state", "Decision", "Score"], datatype=["str"] * 5, interactive=False, label="Review queue")
                            with gr.Row():
                                selected = gr.Dropdown(label="Review asset", choices=[])
                                keep = gr.Button("Accept",visible=False)
                                reject = gr.Button("Reject",visible=False)
                                reevaluate = gr.Button("Re-review")
                            with gr.Accordion("Evaluation & audit", open=False, elem_classes=["scroll-section"]):
                                score_details = gr.JSON(label="Evaluation & audit")
                with gr.Tab('Creative discussion', id='discussion', render_children=True):
                    gr.Markdown('讨论画面需求、构图和提示词。这里只对话；应用提示词后，由你在生图页点击开始。')
                    discussion_id = gr.State(None)
                    discussion_request = gr.State(lambda: secrets.token_hex(16))
                    with gr.Row():
                        discussion_history = gr.Dropdown(label='Discussion history', choices=discussion.sessions(), value=None, scale=4)
                        discussion_load = gr.Button('载入讨论', scale=0)
                        discussion_new = gr.Button('New discussion', scale=0)
                    discussion_chat = gr.Chatbot(label='Conversation', height=420, buttons=['copy'], feedback_options=(),
                        render_markdown=False, allow_tags=False, allow_file_downloads=False, elem_id='discussion-chat')
                    discussion_message = gr.Textbox(label='Discussion message', lines=4, max_lines=10,
                        info='Please provide your base model information when starting the conversation.',
                        placeholder='描述想要的画面，或带入现有提示词继续讨论…', elem_id='discussion-message')
                    with gr.Row():
                        discussion_send = gr.Button('Send message', variant='primary', scale=0, elem_id='discussion-send')
                        discussion_status = gr.Markdown(discussion.connection_status(), elem_id='discussion-status')
                    with gr.Accordion('讨论设置', open=False):
                        with gr.Row():
                            discussion_scope = gr.Dropdown([('沿用 API 配置','configured'),('普通内容','sfw'),('允许成人内容','adult_allowed'),('未确认内容','unknown')], value='configured', label='User-confirmed content scope')
                            discussion_budget = gr.Number(value=1, minimum=.01, maximum=100, label='Discussion budget (USD)')
                        gr.Markdown('每段讨论使用独立预算；新建讨论可调整范围和预算。对话记录仅保存在本机。')
                    discussion_prompt = gr.Dropdown(label='Available prompts', choices=[], value=None, elem_id='discussion-prompt-choice')
                    with gr.Accordion('Prompt preview', open=False):
                        discussion_positive = gr.Textbox(label='Positive prompt', interactive=False, lines=4)
                        discussion_negative = gr.Textbox(label='Negative prompt', interactive=False, lines=2)
                    discussion_apply = gr.Button('Apply and open prompt studio', interactive=False, elem_id='discussion-apply')
                    discussion_applied = gr.Checkbox(value=False, visible=False)
                with gr.Tab("Prompt approval", visible=False):
                    card = gr.Code(label="Style card", language="json", lines=16)
                    plans = gr.Code(label="Draft prompts", language="json", lines=22)
                    with gr.Row():
                        load_prompts = gr.Button("Load drafts")
                        approve_prompts = gr.Button("Approve style & prompts", variant="primary")
                with gr.Tab("Quarantine & delivery", visible=False):
                    quarantine = gr.Dropdown(label="Recoverable image", choices=[])
                    restore = gr.Button("Restore original")
                    with gr.Row():
                        manifest = gr.File(label="Delivery manifest", interactive=False)
                        sheet = gr.Image(label="Contact sheet", type="filepath", interactive=False, buttons=["download", "fullscreen"])
                    events = gr.Dataframe(headers=["Time (UTC)", "Event", "Details"], interactive=False, label="Activity log")
                    load_events = gr.Button("Refresh activity log")

        with gr.Column(visible=False,elem_id="results-page") as results_page:
            return_tab = gr.Textbox(value="studio",visible=False)
            with gr.Row(elem_classes=["results-nav"]):
                back_to_editor = gr.Button("← Back to settings",scale=0,elem_id="back-to-editor")
            results_title = gr.Markdown("## 生成结果",elem_id="results-title")
            result_status = gr.Markdown("No task selected",elem_classes=["status-band"])
            result_generation_meter = gr.HTML(elem_id="result-generation-meter")
            result_progress = gr.Markdown("等待开始",elem_id="result-progress")
            result_group_progress = gr.HTML(group_queue_html(service),elem_id='result-group-progress')
            group_cancel_request = gr.Textbox(value='',elem_id='group-cancel-request',elem_classes=['result-delete-bridge'],show_label=False)
            group_cancel_trigger = gr.Button('Cancel group',elem_id='group-cancel-trigger',elem_classes=['result-delete-bridge'])
            group_cancel_response = gr.Textbox(value='{}',elem_id='group-cancel-response',elem_classes=['result-delete-bridge'],show_label=False)
            group_retry_request = gr.Textbox(value='',elem_id='group-retry-request',elem_classes=['result-delete-bridge'],show_label=False)
            group_retry_trigger = gr.Button('Redraw group',elem_id='group-retry-trigger',elem_classes=['result-delete-bridge'])
            group_retry_response = gr.Textbox(value='{}',elem_id='group-retry-response',elem_classes=['result-delete-bridge'],show_label=False)
            result_queue_text = gr.Markdown(service.queue_status(),elem_id='result-queue-status')
            with gr.Row():
                result_round_end = gr.Button('结束本轮工作',variant='stop',scale=0,elem_id='result-round-end')
                result_round_resume = gr.Button('执行剩余队列',scale=0)
                result_stop = gr.Button("Stop after current output",variant="stop",scale=0)
                result_resume = gr.Button("Resume",scale=0)
            result_action_help = gr.Markdown("执行剩余队列：处理已排队任务。恢复当前任务：沿用当前进度。再生成一轮：沿用设置新建一轮。",elem_classes=["task-action-help"])
            result_gallery_signature=gr.State(None)
            task_choices_signature=gr.State(None)
            result_preview_asset=gr.Textbox(value='',show_label=False,elem_classes=['result-delete-bridge'])
            result_history_toggle = gr.Checkbox(value=True,label="展示此前任务与各轮图片",info="按原缩略图方式展示最近 50 张图片。")
            result_image_map = gr.Textbox(value='{}',elem_id='result-image-map',elem_classes=['result-delete-bridge'],show_label=False)
            result_delete_request = gr.Textbox(value='',elem_id='result-delete-request',elem_classes=['result-delete-bridge'],show_label=False)
            result_delete_trigger = gr.Button('Delete image',elem_id='result-delete-trigger',elem_classes=['result-delete-bridge'])
            result_batch_request = gr.Textbox(value='',elem_id='result-batch-request',elem_classes=['result-delete-bridge'],show_label=False)
            result_batch_trigger = gr.Button('Batch images',elem_id='result-batch-trigger',elem_classes=['result-delete-bridge'])
            result_batch_response = gr.Textbox(value='{}',elem_id='result-batch-response',elem_classes=['result-delete-bridge'],show_label=False)
            result_retry_request = gr.Textbox(value='',elem_id='result-retry-request',elem_classes=['result-delete-bridge'],show_label=False)
            result_retry_trigger = gr.Button('Retry image',elem_id='result-retry-trigger',elem_classes=['result-delete-bridge'])
            result_retry_response = gr.Textbox(value='{}',elem_id='result-retry-response',elem_classes=['result-delete-bridge'],show_label=False)
            result_batch_view_request = gr.Textbox(value='',elem_id='result-batch-view-request',elem_classes=['result-delete-bridge'],show_label=False)
            result_batch_view_trigger = gr.Button('View batch',elem_id='result-batch-view-trigger',elem_classes=['result-delete-bridge'])
            result_prompt_request = gr.Textbox(value='',elem_id='result-prompt-request',elem_classes=['result-delete-bridge'],show_label=False)
            result_prompt_trigger = gr.Button('Edit result prompt',elem_id='result-prompt-trigger',elem_classes=['result-delete-bridge'])
            result_prompt_response = gr.Textbox(value='{}',elem_id='result-prompt-response',elem_classes=['result-delete-bridge'],show_label=False)
            result_discussion_request = gr.Textbox(value='',elem_id='result-discussion-request',elem_classes=['result-delete-bridge'],show_label=False)
            result_discussion_trigger = gr.Button('Discuss result prompt',elem_id='result-discussion-trigger',elem_classes=['result-delete-bridge'])
            result_discussion_response = gr.Textbox(value='{}',elem_id='result-discussion-response',elem_classes=['result-delete-bridge'],show_label=False)
            result_gallery = gr.Gallery(label="Generated images",columns=2,height=820,object_fit="contain",buttons=["download","download_all","fullscreen"],elem_id="result-gallery")
            gr.Markdown('点开图片即可查看这张图的评分、提示词与生成参数。下载原图 PNG 后拖入 ComfyUI，可恢复该图工作流。')
            result_saved = gr.Textbox(label="Saved folder",interactive=False)
            result_zip_button=gr.Button('将所选任务的交付图片打包',size='sm')
            result_zip_status=gr.Markdown('')
            with gr.Accordion("评分与此轮提示词",open=False,visible=False,elem_classes=["scroll-section"],elem_id="result-review-panel"):
                result_reviews = gr.HTML(value=result_review_html(service,None),elem_id="result-review-details")

        def task_action_updates(task_id,lang):
            row=service.db.one('SELECT state,phase FROM tasks WHERE id=?',(task_id,)) if task_id else None
            enabled=False
            label='请选择任务'
            detail='请选择任务后恢复进度或再生成一轮。'
            if row:
                state=row['state']
                reference_only=service.settings(task_id).reference_only
                if state in ('RUNNING','COMPLETED','PARTIAL') and not reference_only:
                    label='再生成一轮';enabled=True
                    detail='再生成一轮：沿用所选任务的图量、已生成的提示词和工作流，独立新建一轮并排队；当前工作继续，尚未规划的组将在执行时规划。'
                elif state in ('RUNNING','STOPPING'):
                    label='当前任务处理中'
                    detail='当前任务已进入处理；无需重复提交。'
                elif state=='WAITING_APPROVAL' and row['phase']=='PROMPT':
                    label='请先确认提示词'
                    detail='请先到人工干预页确认提示词，再恢复当前任务。'
                elif state in ('PAUSED','FAILED','BLOCKED_POLICY','WAITING_APPROVAL'):
                    label='恢复当前任务';enabled=True
                    detail='恢复当前任务：沿用所选任务的已有进度，继续完成剩余目标。'
                else:
                    label='当前任务已结束'
            en={'请选择任务':'Select a task','再生成一轮':'Generate another round','当前任务处理中':'Task in progress','请先确认提示词':'Approve prompts first','恢复当前任务':'Resume selected task','当前任务已结束':'Task finished'}
            if lang!='zh':
                detail='Run queued tasks processes existing queued work. Resume selected task keeps its progress; Generate another round creates a new task using the selected settings and prompts.'
            else:
                detail='执行剩余队列：按顺序处理已排队任务，不新建生图任务。 '+detail
            if enabled and service.round_ended():
                detail+=' 本轮已结束；此操作也会恢复已有队列。' if lang=='zh' else ' This action also resumes the existing queue when the work round has ended.'
            return (*[gr.update(value=label if lang=='zh' else en[label],interactive=enabled) for _ in range(4)],detail)

        refresh_inputs=[task,language,result_history_toggle,result_preview_asset,result_gallery_signature]
        refresh_selection_js="""(...values) => {
            const img=document.querySelector('#result-gallery .preview img');
            let asset=null;
            if(img && !window.supervisorPreviewClosed) {
                const url=decodeURIComponent(img.currentSrc || img.src || '');
                try { const map=JSON.parse(document.querySelector('#result-image-map textarea, #result-image-map input')?.value || '{}');
                    asset=(map.images || []).find(record=>url.includes(record.filename))?.asset_id || null;
                } catch {}
            }
            values[3]=asset;
            return values;
        }"""
        def refresh_all(task_id, lang,include_history=True,preview_asset=None,previous_signature=None):
            base = refresh(task_id, lang)
            if not task_id:
                return (*base, [], "", "", [], [], [], "等待开始",[],"","等待提示词生图任务",[],[],translate("No task selected",lang),"等待开始","",result_review_html(service,None),task_control_preview(service,None),"{}","","",*task_action_updates(None,lang),group_queue_html(service,None,lang),None)
            settings = service.settings(task_id)
            refs = service.db.rows("SELECT * FROM assets WHERE task_id=? AND source_kind='reference' ORDER BY created_at", (task_id,))
            analysis = []
            for asset in refs:
                info = json.loads(asset["metadata"])
                tags = info.get("model_tags", {})
                analysis.append([asset["id"][:8], ", ".join(tags.get("subject", [])), ", ".join(tags.get("style", [])), info.get("extracted", {}).get("positive", {}).get("value", ""), "; ".join(tags.get("writing_style", []))])
            card_row = service.db.one("SELECT body FROM style_cards WHERE task_id=? ORDER BY version DESC LIMIT 1", (task_id,))
            card_data = json.loads(card_row["body"]) if card_row else {}
            summary = "**" + translate("Learned writing style", lang) + "**  " + "; ".join(card_data.get("writing_style", []))
            prompt_rows = []
            for row in service.db.rows("SELECT p.*,g.ordinal FROM prompt_variants p JOIN groups g ON g.id=p.group_id WHERE p.task_id=? ORDER BY p.created_at DESC LIMIT 30", (task_id,)):
                recipe = json.loads(row["body"])
                prompt_rows.append([row["ordinal"] + 1, settings.target_styles[row["ordinal"]] if settings.target_styles else "", row["round_index"] + 1, recipe["positive"], recipe["reason"]])
            reviews = []
            for row in service.db.rows("SELECT a.id,g.ordinal,d.action,e.effective_score,e.body FROM assets a JOIN groups g ON g.id=a.group_id LEFT JOIN decisions d ON d.asset_id=a.id AND d.rowid=(SELECT MAX(rowid) FROM decisions WHERE asset_id=a.id) LEFT JOIN evaluations e ON e.id=d.evaluation_id WHERE a.task_id=? ORDER BY a.created_at DESC LIMIT 50", (task_id,)):
                evaluation = json.loads(row["body"]) if row["body"] else {}
                reviews.append([row["id"][:8], row["ordinal"] + 1, translate(row["action"] or "pending", lang), row["effective_score"], "; ".join(x["evidence"] for x in evaluation.get("issues", []))])
            output_path = str(Path(settings.export_folder) / (f"task_{task_id}" if settings.delivery_layout=='task_folder' else '')) if settings.export_folder else str(service.files.path(f"tasks/{task_id}/delivery"))
            generated_paths = {str(service.files.path(row["path"])) for row in service.db.rows("SELECT path FROM assets WHERE task_id=? AND source_kind='generated' AND state='AVAILABLE'", (task_id,))}
            generated_gallery = [(path, caption) for path, caption in base[2] if path in generated_paths] if not settings.demo else []
            progress = "此任务是旧演示任务，未调用云端或 ComfyUI，自动生图页不展示演示产物。" if settings.demo else task_progress(service, task_id)
            direct_task = settings.direct_prompt is not None
            result_images,review_html,image_map=result_history(service,task_id,lang,include_history)
            current_count=len(generated_paths)
            history_count=sum(record['task_id']!=task_id for record in image_map['images'])
            context=f"当前任务 {task_id[:8]} · 控制角色：{character_summary(settings)} · 本任务可浏览图片 {current_count} 张"
            if history_count:
                context+=f"；下方另有历史图片 {history_count} 张，详情标注各自来源。"
            gallery_signature=(lang,bool(include_history),tuple((record['asset_id'],image[0],image[1]) for record,image in zip(image_map['images'],result_images)))
            map_json=dump_json(image_map)
            signature=(gallery_signature,hashlib.sha256(map_json.encode('utf-8')).hexdigest())
            if previous_signature and gallery_signature==previous_signature[0]:
                result_gallery_update=gr.update()
            else:
                selected_index=next((index for index,record in enumerate(image_map['images']) if record['asset_id']==preview_asset),None)
                result_gallery_update=gr.update(value=result_images,selected_index=selected_index)
            # Avoid replacing every historical review card on each progress tick.
            details_unchanged=signature==previous_signature
            result_reviews_update=gr.update() if details_unchanged else review_html
            result_map_update=gr.update() if details_unchanged else map_json
            return (*base, generated_gallery, output_path, summary, analysis, prompt_rows, reviews, progress,
                    generated_gallery if direct_task else [], output_path if direct_task else "",
                    progress if direct_task else "请选择提示词生图任务，或填写提示词开始新任务。", reviews if direct_task else [],
                    result_gallery_update,base[0].split('\n\n',1)[0]+'\n\n'+context,task_progress(service,task_id,include_groups=False),output_path,result_reviews_update,task_control_preview(service,task_id),result_map_update,generation_progress_html(service,task_id),generation_progress_html(service,task_id),*task_action_updates(task_id,lang),group_queue_html(service,task_id,lang),signature)

        outputs = [status, stats, gallery, table, selected, manifest, sheet, quarantine, studio_gallery, saved_folder, learned, study, studio_prompts, studio_reviews, runtime_progress,direct_gallery,direct_saved,direct_progress,direct_reviews,result_gallery,result_status,result_progress,result_saved,result_reviews,actual_control_preview,result_image_map,runtime_generation_meter,result_generation_meter,studio_resume,direct_resume,result_resume,resume_button,result_action_help,result_group_progress,result_gallery_signature]
        def cancel_result_group(request, lang):
            body = {}
            try:
                body = json.loads(request)
                if not isinstance(body,dict):
                    raise ValueError('取消请求无效，请刷新后重试。')
                service.cancel_group(body['task_id'],body['group_id'])
                text = '已取消该组后续工作，已有图片保留；已提交的当前图片收回后停止。' if lang=='zh' else 'Remaining group work cancelled. Existing images kept; an already submitted image finishes collecting first.'
                gr.Info(text)
                return text,dump_json({'request_id':body.get('request_id'),'ok':True,'text':text})
            except (ValueError,KeyError,TypeError) as exc:
                text = str(exc) if isinstance(exc,ValueError) else '取消请求无效，请刷新后重试。'
                gr.Warning(text)
                return text,dump_json({'request_id':body.get('request_id') if isinstance(body,dict) else None,'ok':False,'text':text})
            except Exception:
                text = '取消状态可能已保存，请刷新检查；无法保存交付文件时请检查输出目录。' if lang=='zh' else 'Refresh to check cancellation; verify the output folder if delivery could not be saved.'
                gr.Warning(text)
                return text,dump_json({'request_id':body.get('request_id') if isinstance(body,dict) else None,'ok':False,'text':text})
        group_cancel_trigger.click(cancel_result_group,[group_cancel_request,language],[message,group_cancel_response],
            js="(request,lang) => [document.querySelector('#group-cancel-request textarea, #group-cancel-request input').value,lang]",
            show_progress='hidden',concurrency_id='group-cancel',concurrency_limit=1).then(refresh_all,refresh_inputs,outputs,js=refresh_selection_js,show_progress='hidden')
        async def retry_result_group(request,lang):
            body={}
            try:
                body=json.loads(request)
                if not isinstance(body,dict):
                    raise ValueError('重绘请求无效，请刷新后重试。')
                new_id=await retry_from_group(service,body['task_id'],body['group_id'],body['request_id'])
                text=(f'已加入本组重绘队列 {new_id[:8]}：修订最后一轮提示词，沿用本组目标张数，原图保留。'
                    if lang=='zh' else f'Group redraw queued: {new_id[:8]}. Revises the latest prompt with the original group image target; originals kept.')
                if service.round_ended():
                    text += ' 点击“执行剩余队列”后运行。' if lang=='zh' else ' Resume the queue to run it.'
                gr.Info(text)
                return text,dump_json({'request_id':body.get('request_id'),'ok':True,'text':text})
            except Exception as exc:
                text=str(exc) if isinstance(exc,(ValueError,ConnectionFailure)) else '无法提交本组重绘，请检查图片记录、连接和输出目录。'
                gr.Warning(text)
                return text,dump_json({'request_id':body.get('request_id') if isinstance(body,dict) else None,'ok':False,'text':text})
        group_retry_trigger.click(retry_result_group,[group_retry_request,language],[message,group_retry_response],
            js="(request,lang) => [document.querySelector('#group-retry-request textarea, #group-retry-request input').value,lang]",
            show_progress='hidden',concurrency_id='result-retry',concurrency_limit=1).then(refresh_all,refresh_inputs,outputs,js=refresh_selection_js,show_progress='hidden')
        app.load(fn=None,js=GROUP_QUEUE_JS)
        app.load(fn=None,js=CHARACTER_ROWS_JS)
        def package_result(task_id):
            try:
                if not task_id:
                    raise ValueError('请先选择任务。')
                path=export_image_zip(service,task_id)
                return '压缩包已保存：'+path
            except (ValueError,OSError) as exc:
                gr.Warning(str(exc))
                return '打包失败：'+str(exc)
        result_zip_button.click(package_result,task,result_zip_status,show_progress='hidden')
        def delete_result_image(request, language):
            try:
                body = json.loads(request)
                service.delete_generated(body['task_id'],body['asset_id'])
                return '图片已删除。' if language=='zh' else 'Image deleted.'
            except (ValueError,KeyError,TypeError) as exc:
                text = str(exc) if isinstance(exc,ValueError) else '删除请求无效，请刷新生成结果。'
                gr.Warning(text)
                return text
            except OSError:
                text = '图片文件暂时无法删除，请关闭占用文件的程序后重试。'
                gr.Warning(text)
                return text

        result_delete_trigger.click(delete_result_image,[result_delete_request,language],message,js="(request,lang) => [document.querySelector('#result-delete-request textarea, #result-delete-request input').value,lang]",show_progress='hidden',concurrency_id='result-delete',concurrency_limit=1).then(refresh_all,refresh_inputs,outputs,js=refresh_selection_js,show_progress='hidden').then(fn=None,js="() => { window.supervisorDeleteBusy=false; document.querySelectorAll('#result-gallery .result-delete-icon').forEach(b => b.disabled=false); }")
        app.load(fn=None,js=RESULT_DELETE_JS)
        app.load(fn=None,js=RESULT_PREVIEW_JS)
        app.load(fn=None,js=RESULT_BATCH_JS)
        app.load(fn=None,js=RESULT_RETRY_JS)

        async def create_result_retry(request,lang):
            body = {}
            try:
                body = json.loads(request)
                if not isinstance(body,dict):
                    raise ValueError('重画请求无效，请刷新后重试。')
                new_id = await retry_from_result(service,body['task_id'],body['asset_id'],body['request_id'],body.get('feedback'))
                text = (f'已加入重画队列 {new_id[:8]}：将修订这张图的提示词后重新生成，原图保留。'
                    if lang=='zh' else f'Retry {new_id[:8]} queued: revise this image’s prompt and regenerate. Original kept.')
                if service.round_ended():
                    text += ' 点击“执行剩余队列”后运行。' if lang=='zh' else ' Resume the queue to run it.'
                gr.Info(text,duration=8)
                return gr.update(choices=task_choices(lang)),text,dump_json({'request_id':body['request_id'],'ok':True,'text':text,'task_id':new_id})
            except Exception as exc:
                text = str(exc) if isinstance(exc,(ValueError,ConnectionFailure)) else '暂时无法重画，请检查图片记录、模型连接和输出目录。'
                if str(exc)=='OUTPUT_FOLDER_UNWRITABLE':
                    text = '无法写入原输出目录，请检查目录权限后重试。'
                gr.Warning(text,duration=12)
                return gr.update(),text,dump_json({'request_id':body.get('request_id') if isinstance(body,dict) else None,'ok':False,'text':text})

        result_retry_trigger.click(create_result_retry,[result_retry_request,language],[task,message,result_retry_response],
            js="(request,lang) => [document.querySelector('#result-retry-request textarea, #result-retry-request input').value,lang]",
            show_progress='hidden',concurrency_id='result-retry',concurrency_limit=1)

        async def create_result_batch(request, lang):
            body = {}
            try:
                body = json.loads(request)
                if not isinstance(body,dict):
                    raise ValueError('批量请求无效，请刷新结果后重试。')
                new_id = await batch_from_result(service,body['task_id'],body['asset_id'],body['count'],body['review'],body['request_id'])
                text = f'已创建批量任务 {new_id[:8]}，加入顺序队列。' if lang=='zh' else f'Batch {new_id[:8]} added to the queue.'
                if service.round_ended():
                    text += ' 点击“执行剩余队列”后运行。' if lang=='zh' else ' Resume the queue to run it.'
                gr.Info(text,duration=8)
                return gr.update(choices=task_choices(lang)),text,dump_json({'request_id':body['request_id'],'ok':True,'text':text,'task_id':new_id})
            except Exception as exc:
                text = str(exc) if isinstance(exc,(ValueError,ConnectionFailure)) else '暂时无法批量产出，请检查图片记录和输出目录后重试。'
                if str(exc)=='OUTPUT_FOLDER_UNWRITABLE':
                    text = '无法写入原输出目录，请检查目录权限后重试。'
                gr.Warning(text,duration=12)
                return gr.update(),text,dump_json({'request_id':body.get('request_id') if isinstance(body,dict) else None,'ok':False,'text':text})

        result_batch_trigger.click(create_result_batch,[result_batch_request,language],[task,message,result_batch_response],
            js="(request,lang) => [document.querySelector('#result-batch-request textarea, #result-batch-request input').value,lang]",
            show_progress='hidden',concurrency_id='result-batch',concurrency_limit=1)

        def view_result_batch(request,lang):
            try:
                body=json.loads(request)
                if not isinstance(body,dict) or any(not isinstance(body.get(name),str) for name in ('request_id','task_id','asset_id')):
                    raise ValueError('批量请求无效。')
                row=service.db.one('SELECT task_id FROM result_batch_requests WHERE request_id=? AND source_task=? AND source_asset=?',
                    (body['request_id'],body['task_id'],body['asset_id']))
                if not row:
                    raise ValueError('找不到批量任务，请刷新任务列表。')
                return gr.update(choices=task_choices(lang),value=row['task_id'])
            except (ValueError,TypeError,KeyError):
                gr.Warning('找不到批量任务，请刷新任务列表。')
                return gr.update()

        result_batch_view_trigger.click(view_result_batch,[result_batch_view_request,language],task,
            js="(request,lang) => [document.querySelector('#result-batch-view-request textarea, #result-batch-view-request input').value,lang]",
            show_progress='hidden').then(refresh_all,refresh_inputs,outputs,js=refresh_selection_js,show_progress='hidden')

        def edit_result_prompt(request,lang):
            body={}
            try:
                body,plan,group,attempt=prompt_from_result(service,request)
                text=(f'已载入任务 {body["task_id"][:8]} · 第 {group} 组 · 第 {attempt} 轮的正负提示词，可编辑后开始生图。'
                      if lang=='zh' else f'Loaded prompts from task {body["task_id"][:8]} · group {group} · attempt {attempt}. Edit and generate when ready.')
                return (plan.positive,plan.negative,text,gr.update(visible=True),gr.update(visible=False),
                        gr.update(),'prompt-studio',dump_json({'request_id':body['request_id'],'ok':True}))
            except (ValueError,TypeError,KeyError):
                try:
                    body=json.loads(request)
                except (ValueError,TypeError):
                    body={}
                text='找不到这张图的生成提示词，请刷新结果后重试。'
                gr.Warning(text)
                return (*[gr.update() for _ in range(7)],dump_json({'request_id':body.get('request_id') if isinstance(body,dict) else None,'ok':False,'text':text}))

        result_prompt_trigger.click(edit_result_prompt,[result_prompt_request,language],
            [direct_positive,direct_negative,direct_result_status,editor_page,results_page,views,return_tab,result_prompt_response],
            js="(request,lang) => [document.querySelector('#result-prompt-request textarea, #result-prompt-request input').value,lang]",
            show_progress='hidden',concurrency_id='result-prompt',concurrency_limit=1).then(fn=None,
            js="() => { if(document.querySelector('#editor-page')?.offsetHeight) { window.supervisorReturnTab='prompt-studio'; document.querySelector('#editor-page [role=tab][data-tab-id=\"prompt-studio\"]')?.click(); window.scrollTo(0,0); } return []; }")

        discussion_outputs=[discussion_id,discussion_request,discussion_chat,discussion_message,discussion_history,
            discussion_prompt,discussion_positive,discussion_negative,discussion_apply,discussion_status,
            discussion_scope,discussion_budget,discussion_send]
        def reset_discussion(message='',scope='configured',status=''):
            return (None,secrets.token_hex(16),[],message,gr.update(choices=discussion.sessions(),value=None),
                gr.update(choices=[],value=None),'','',gr.update(interactive=False),status or discussion.cost(None),
                gr.update(value=scope,interactive=True),gr.update(value=1,interactive=True),gr.update(interactive=True))
        def new_discussion():
            return reset_discussion()
        def load_discussion(session_id):
            if not session_id:
                return reset_discussion()
            settings=discussion.session(session_id)
            prompts=discussion.prompts(session_id)
            chosen=prompts[-1][1] if prompts else None
            positive,negative=discussion.prompt(session_id,chosen) if chosen else ('','')
            return (session_id,secrets.token_hex(16),discussion.history(session_id),'',gr.update(choices=discussion.sessions(),value=session_id),
                gr.update(choices=prompts,value=chosen),positive,negative,gr.update(interactive=bool(chosen)),discussion.cost(session_id),
                gr.update(value=settings.content_label,interactive=False),gr.update(value=settings.budget_micro/1_000_000,interactive=False),gr.update(interactive=True))
        async def send_discussion(session_id,request_id,message,scope,budget,lang):
            if not (message or '').strip():
                blank=[gr.update() for _ in discussion_outputs]
                blank[9]='请先输入讨论内容。' if lang=='zh' else 'Enter your message first.'
                yield tuple(blank)
                return
            pending=[gr.update() for _ in discussion_outputs]
            pending[2]=discussion.history(session_id)+[{'role':'user','content':message}]
            pending[8]=gr.update(interactive=False)
            pending[9]='已收到，正在等待模型回复…' if lang=='zh' else 'Message received; waiting for the model…'
            pending[12]=gr.update(interactive=False)
            yield tuple(pending)
            try:
                session_id,turn_id,reply=await discussion.send(session_id,message,request_id,scope,budget,lang)
                result=list(load_discussion(session_id))
                result[5]=gr.update(choices=discussion.prompts(session_id),value=turn_id if reply.prompt_ready else None)
                result[6:9]=[reply.positive or '',reply.negative,gr.update(interactive=reply.prompt_ready)]
                result[9]=('已回复。'+('完整提示词可一键应用。' if reply.prompt_ready else '可继续补充需求。')+'\n\n'+discussion.cost(session_id))
                yield tuple(result)
            except Exception as exc:
                turn=service.db.one('SELECT session_id FROM discussion_turns WHERE request_id=?',(request_id,))
                session_id=turn['session_id'] if turn else session_id
                result=list(load_discussion(session_id)) if session_id else list(reset_discussion(message))
                result[3]=message
                result[5]=gr.update(choices=discussion.prompts(session_id) if session_id else [],value=None)
                result[6:9]=['','',gr.update(interactive=False)]
                detail=str(exc) if isinstance(exc,ValueError) else form_error(exc,lang)
                from .db import BudgetExceeded
                if isinstance(exc,BudgetExceeded):
                    detail='讨论预算已到上限，请新建讨论或调整新讨论预算。'
                result[9]='讨论失败：'+detail+'\n\n'+discussion.cost(session_id)
                gr.Warning(result[9],duration=10)
                yield tuple(result)
        discussion_inputs=[discussion_id,discussion_request,discussion_message,discussion_scope,discussion_budget,language]
        for send_event in (discussion_send.click,discussion_message.submit):
            send_event(send_discussion,discussion_inputs,discussion_outputs,show_progress='hidden',
                concurrency_id='creative-discussion',concurrency_limit=1)
        discussion_new.click(new_discussion,outputs=discussion_outputs,show_progress='hidden',concurrency_id='creative-discussion',concurrency_limit=1)
        discussion_chat.clear(new_discussion,outputs=discussion_outputs,show_progress='hidden',concurrency_id='creative-discussion',concurrency_limit=1)
        discussion_load.click(load_discussion,discussion_history,discussion_outputs,show_progress='hidden',concurrency_id='creative-discussion',concurrency_limit=1)
        def select_discussion_prompt(session_id,turn_id):
            if not turn_id:
                return '','',gr.update(interactive=False)
            positive,negative=discussion.prompt(session_id,turn_id)
            return positive,negative,gr.update(interactive=True)
        discussion_prompt.change(select_discussion_prompt,[discussion_id,discussion_prompt],[discussion_positive,discussion_negative,discussion_apply],show_progress='hidden')
        prompt_navigation_js="(ok) => {if(ok){window.supervisorReturnTab='prompt-studio';document.querySelector('#editor-page [role=tab][data-tab-id=\"prompt-studio\"]')?.click();window.scrollTo(0,0);}return [];}"
        def apply_discussion_prompt(session_id,turn_id,lang):
            try:
                positive,negative=discussion.prompt(session_id,turn_id)
                text='已填入讨论选定的正负提示词，请确认参数后点击开始生图。' if lang=='zh' else 'Prompts filled. Check the settings and click Generate when ready.'
                return positive,negative,text,gr.update(visible=True),gr.update(visible=False),gr.update(),'prompt-studio',True
            except ValueError as exc:
                gr.Warning(str(exc))
                return (*[gr.update() for _ in range(7)],False)
        discussion_apply.click(apply_discussion_prompt,[discussion_id,discussion_prompt,language],
            [direct_positive,direct_negative,direct_result_status,editor_page,results_page,views,return_tab,discussion_applied],show_progress='hidden').then(fn=None,inputs=discussion_applied,js=prompt_navigation_js)
        def discuss_result_prompt(request,lang):
            body={}
            try:
                body,plan,group,attempt=prompt_from_result(service,request)
                settings=service.settings(body['task_id'])
                message=('请帮我讨论并优化这张图的提示词。\n\n正向提示词：\n'+plan.positive+'\n\n负向提示词：\n'+plan.negative
                    if lang=='zh' else 'Help me discuss and improve this image prompt.\n\nPositive prompt:\n'+plan.positive+'\n\nNegative prompt:\n'+plan.negative)
                text=f'已带入第 {group} 组 · 第 {attempt} 轮提示词；可补充需求后发送，尚未调用模型。'
                return (*reset_discussion(message,settings.content_label,text),gr.update(visible=True),gr.update(visible=False),
                    gr.update(),'discussion',dump_json({'request_id':body['request_id'],'ok':True}))
            except (ValueError,TypeError,KeyError):
                try: body=json.loads(request)
                except (ValueError,TypeError): body={}
                text='找不到这张图的生成提示词，请刷新结果后重试。'
                gr.Warning(text)
                return (*[gr.update() for _ in range(len(discussion_outputs)+4)],dump_json({'request_id':body.get('request_id') if isinstance(body,dict) else None,'ok':False,'text':text}))
        result_discussion_trigger.click(discuss_result_prompt,[result_discussion_request,language],
            [*discussion_outputs,editor_page,results_page,views,return_tab,result_discussion_response],
            js="(request,lang) => [document.querySelector('#result-discussion-request textarea, #result-discussion-request input').value,lang]",
            show_progress='hidden',concurrency_id='creative-discussion',concurrency_limit=1).then(fn=None,
            js="() => {if(document.querySelector('#editor-page')?.offsetHeight){window.supervisorReturnTab='discussion';document.querySelector('#editor-page [role=tab][data-tab-id=\"discussion\"]')?.click();window.scrollTo(0,0);}return [];}")

        def show_results():
            return gr.update(visible=False),gr.update(visible=True)

        def show_editor(origin="studio"):
            if origin not in {"studio","prompt-studio","workflow-editor","intervention","setup","discussion"}:
                origin = "studio"
            return gr.update(visible=True),gr.update(visible=False),gr.update(selected=origin)

        def open_results_if_started(task_id,activity):
            if task_id and activity.startswith(("任务已开始：","提示词生图已开始：")):
                return show_results()
            return gr.update(),gr.update()

        open_results.click(show_results,outputs=[editor_page,results_page],show_progress="hidden").then(fn=lambda:None,js="() => { window.scrollTo(0,0); return []; }").then(refresh_all,refresh_inputs,outputs,js=refresh_selection_js,show_progress="hidden")
        back_to_editor.click(show_editor,inputs=return_tab,outputs=[editor_page,results_page,views],js="(origin) => [window.supervisorReturnTab || origin]",show_progress="hidden").then(fn=lambda:None,js="() => { window.scrollTo(0,0); return []; }")
        result_stop.click(lambda t,l: localized_action(safe_action(stop,t),l),[task,language],message).then(refresh_all,refresh_inputs,outputs,js=refresh_selection_js,show_progress="hidden")
        result_resume.click(continue_task,[task,language],[task,message]).then(refresh_all,refresh_inputs,outputs,js=refresh_selection_js,show_progress="hidden")
        timer = gr.Timer(3)
        timer.tick(lambda lang:(service.queue_status(lang),service.queue_status(lang)),language,[queue_text,result_queue_text],show_progress='hidden')
        def refresh_task_labels(lang,previous):
            choices=task_choices(lang)
            signature=tuple(choices)
            if signature==previous:
                return gr.update(),signature
            # Update labels without selecting a different task or refreshing inputs.
            return gr.update(choices=choices),signature
        timer.tick(refresh_task_labels,[language,task_choices_signature],[task,task_choices_signature],show_progress='hidden')
        for button in (round_end,result_round_end):
            button.click(service.end_round,outputs=message,show_progress='hidden').then(lambda lang:(service.queue_status(lang),service.queue_status(lang)),language,[queue_text,result_queue_text],show_progress='hidden')
        for button in (round_resume,result_round_resume):
            button.click(service.resume_round,outputs=message,show_progress='hidden').then(lambda lang:(service.queue_status(lang),service.queue_status(lang)),language,[queue_text,result_queue_text],show_progress='hidden')
        # Translate component metadata only: editable values and machine JSON stay intact.
        registry = []
        preferences = {language, appearance, appearance_panel, accent, mist, drizzle, reset_appearance, title}
        for component in app.blocks.values():
            if component in preferences:
                continue
            original = {}
            for key in ("label", "placeholder", "info"):
                value = getattr(component, key, None)
                if isinstance(value, str) and value:
                    original[key] = value
            if isinstance(component, gr.Button):
                original["value"] = component.value
            if isinstance(component, gr.Dataframe):
                original["headers"] = component.headers
            if component in (mode, label, delete_mode, studio_mode, studio_scope, api_scope,direct_scope):
                original["choices"] = [v[1] for v in component.choices]
            if original:
                registry.append((component, original))
                for key, value in original.items():
                    if key == "choices":
                        setattr(component, key, [(translate(v, "zh"), v) for v in value])
                    elif key == "headers":
                        setattr(component, key, [translate(v, "zh") for v in value])
                    else:
                        setattr(component, key, translate(value, "zh"))
        message.value = "就绪"
        status.value = "尚未选择任务"

        def switch_language(lang, activity):
            heading = '<div class="app-title"><h1>ComfyUI Supervisor</h1></div>'
            return translation_updates(registry, lang) + [heading,
                gr.update(choices=[(translate(x, lang), v) for x, v in [("Light", "light"), ("Dark", "dark"), ("Custom", "custom")]]),
                gr.update(label=translate("Appearance", lang)), gr.update(label=translate("Accent", lang)),
                gr.update(label=translate("Mist", lang)), gr.update(label=translate("Drizzle", lang)),
                gr.update(value=translate("Reset appearance", lang)), localized_action(activity, lang), gr.update(choices=task_choices(lang))]

        # Task/message metadata is already in the registry; combine their values into those updates.
        def switch_preferences(lang, activity, current_task):
            service.db.execute("INSERT OR REPLACE INTO runtime_state VALUES('review_language',?)",('en' if lang=='en' else 'zh',))
            changes = switch_language(lang, activity)
            translated_activity, task_update = changes[-2:]
            changes = changes[:-2]
            for index, (component, _) in enumerate(registry):
                if component is message:
                    changes[index]["value"] = translated_activity
                if component is task:
                    changes[index]["choices"] = task_update["choices"]
                    changes[index]["value"] = current_task
            return changes

        language_outputs = [c for c, _ in registry] + [title, appearance, appearance_panel, accent, mist, drizzle, reset_appearance]
        app.load(fn=None,js=UPLOAD_JS,queue=False)
        init = app.load(fn=None, outputs=[language, appearance, accent, mist, drizzle,glass_blur,glass_opacity,appearance_preset], js=APPEARANCE_JS).then(fn=lambda:None,js=STUDIO_VIEW_JS).then(fn=lambda:None,js=NODE_EDITOR_JS)
        init.then(switch_preferences, [language, message, task], language_outputs,show_progress="hidden").then(refresh_all, refresh_inputs, outputs,js=refresh_selection_js,show_progress="hidden")
        language.change(switch_preferences, [language, message, task], language_outputs, show_progress="hidden",js="(lang, activity, taskId) => { window.supervisorLanguage = lang; try { localStorage.setItem('supervisor.language', lang); } catch {} return [lang, activity, taskId]; }").then(refresh_all, refresh_inputs, outputs,js=refresh_selection_js,show_progress="hidden")
        appearance_inputs = [appearance,accent,mist,drizzle,glass_blur,glass_opacity]
        appearance.change(fn=None,inputs=appearance_inputs,js="(mode, accent, mist, rain, blur, opacity) => { window.supervisorAppearance?.(mode, accent, mist, rain, blur, opacity); }",queue=False)
        for control in (accent,mist,drizzle,glass_blur,glass_opacity):
            control.input(fn=None,inputs=appearance_inputs,outputs=[appearance,appearance_preset],js="(mode, accent, mist, rain, blur, opacity) => { const nextMode = mode==='dark' ? 'dark' : 'custom'; const settings = window.supervisorAppearance?.(nextMode, accent, mist, rain, blur, opacity); return [nextMode,settings?.preset ?? null]; }",queue=False)
        appearance_preset.input(fn=None,inputs=[appearance,appearance_preset,drizzle,glass_blur,glass_opacity],outputs=[appearance,accent,mist],js="""(mode, preset, rain, blur, opacity) => {
          const nextMode = mode==='dark' ? 'dark' : 'custom';
          const colors = {mist:['#287b62','#e4f2e9'],blue:['#386caa','#e1edf9'],sand:['#95633a','#f3e8d8']}[preset];
          if(!colors) return [nextMode,'#287b62','#e4f2e9'];
          window.supervisorAppearance?.(nextMode,colors[0],colors[1],rain,blur,opacity);
          return [nextMode,colors[0],colors[1]];
        }""",queue=False)
        reset_appearance.click(lambda: ("custom", "#287b62", "#e4f2e9", False,20,66,"mist"), outputs=[appearance, accent, mist, drizzle,glass_blur,glass_opacity,appearance_preset]).then(fn=None,inputs=appearance_inputs,js="(mode, accent, mist, rain, blur, opacity) => { window.supervisorAppearance?.(mode, accent, mist, rain, blur, opacity); }",queue=False)
        timer.tick(refresh_all, refresh_inputs, outputs, js=refresh_selection_js,show_progress="hidden")
        task.change(refresh_all, refresh_inputs, outputs,js=refresh_selection_js,show_progress="hidden")
        def review_controls(task_id):
            manual=bool(task_id and not service.settings(task_id).autonomous)
            return tuple(gr.update(visible=manual) for _ in range(3))
        task.change(review_controls,task,[accept_button,keep,reject],show_progress='hidden')
        app.load(review_controls,task,[accept_button,keep,reject],show_progress='hidden')
        result_history_toggle.change(refresh_all,refresh_inputs,outputs,js=refresh_selection_js,show_progress="hidden")

        def limit_summary(task_id, lang):
            if not task_id:
                return "新任务使用上方张数、轮数和分数。选择原任务后，可修改并应用到该任务。" if lang == "zh" else "New tasks use these image targets and limits. Select an existing task to edit them."
            settings = service.settings(task_id)
            groups = service.db.rows("SELECT ordinal,round_index FROM groups WHERE task_id=? ORDER BY ordinal", (task_id,))
            used = " / ".join(str(g["round_index"]) for g in groups)
            if lang == "zh":
                return f"当前任务目标：{settings.groups} 组 × {settings.per_group} 张 = {settings.groups * settings.per_group} 张合格图；每组已用轮数：{used}；每组上限 {settings.max_rounds} 轮，综合分数门槛 {settings.quality_threshold:g}。修改后点击「应用到当前任务」；暂停任务可恢复已有进度，已结束任务可「再生成一轮」。"
            return f"Image target: {settings.groups} × {settings.per_group} = {settings.groups * settings.per_group}. Rounds used per group: {used}; limit {settings.max_rounds}, minimum score {settings.quality_threshold:g}. Apply changes, then resume a paused task or generate another round from a finished task."

        def load_task_limits(task_id, lang):
            if not task_id:
                return gr.update(), gr.update(), limit_summary(task_id, lang), gr.update()
            settings = service.settings(task_id)
            return settings.max_rounds, settings.quality_threshold, limit_summary(task_id, lang), settings.per_group

        def studio_update_limits(task_id, n_rounds, quality, lang, per_group_target=None):
            try:
                service.update_task_limits(task_id, n_rounds, quality, per_group_target)
                current = service.db.one("SELECT state,phase FROM tasks WHERE id=?", (task_id,))
                if current["state"] == "PARTIAL":
                    text = "设置已保存。原任务已用完这些轮数，请提高每组最大轮数后再继续。" if lang == "zh" else "Settings saved. Increase the round limit beyond consumed rounds to resume this task."
                elif current["state"] == "WAITING_APPROVAL" and current["phase"] == "PROMPT":
                    text = "设置已保存，请先批准当前提示词。" if lang == "zh" else "Settings saved. Approve the current prompts first."
                elif current['state']=='COMPLETED':
                    text='设置已保存，点击「再生成一轮」沿用更新后的设置。' if lang=='zh' else 'Settings saved. Generate another round using the updated settings.'
                else:
                    text = "设置已保存，点击「恢复当前任务」沿用已有进度。" if lang == "zh" else "Settings saved. Click Resume to continue from existing progress."
                gr.Info(text, duration=8)
                return text, limit_summary(task_id, lang), task_progress(service, task_id)
            except Exception as exc:
                messages = {"TASK_REQUIRED":"请先选择要修改的原任务。",
                            "PAUSE_TASK_BEFORE_LIMITS":"请先停止当前任务，等待本轮处理完毕后再修改。",
                            "TASK_LIMITS_NOT_EDITABLE":"仅支持修改暂停、等待审批或达到轮数上限的任务。"}
                text = messages.get(str(exc), "设置无效：每组张数须为 1–50 的整数，轮数须为 1–100 的整数，分数须为 0–100。")
                if lang != "zh":
                    text = {"TASK_REQUIRED":"Select a task first.","PAUSE_TASK_BEFORE_LIMITS":"Stop the task and wait for the current output before editing.",
                            "TASK_LIMITS_NOT_EDITABLE":"Edit paused, approval-gated or round-limited tasks only."}.get(str(exc),"Use integer image counts from 1 to 50, integer rounds from 1 to 100 and a score from 0 to 100.")
                gr.Warning(text, duration=10)
                return text, gr.update(), gr.update()

        target_summary_js = "() => { requestAnimationFrame(() => window.supervisorUpdateThemes?.()); return []; }"
        app.load(load_task_limits, [task, language], [studio_rounds, studio_threshold, studio_limit_status, studio_per_group], show_progress="hidden").then(fn=None, js=target_summary_js)
        task.change(load_task_limits, [task, language], [studio_rounds, studio_threshold, studio_limit_status, studio_per_group], show_progress="hidden").then(fn=None, js=target_summary_js)
        language.change(limit_summary, [task, language], studio_limit_status, show_progress="hidden")
        studio_apply_limits.click(studio_update_limits, [task, studio_rounds, studio_threshold, language, studio_per_group], [message, studio_limit_status, runtime_progress], concurrency_id="task-limits", concurrency_limit=1)
        alerts.click(fn=None, inputs=None, outputs=message, js="""async () => {
          const zh = window.supervisorLanguage !== 'en';
          if (!('Notification' in window)) return zh ? '此浏览器不支持桌面通知' : 'Desktop alerts unavailable';
          const permission = await Notification.requestPermission();
          window.supervisorAlerts = permission === 'granted';
          return window.supervisorAlerts ? (zh ? '桌面通知已开启' : 'Desktop alerts enabled') : (zh ? '桌面通知未开启' : 'Desktop alerts not enabled');
        }""")
        status.change(fn=None, inputs=[task, status], outputs=None, js=r"""(taskId, text) => {
          if (!window.supervisorAlerts || Notification.permission !== 'granted') return;
          const state = text.match(/\b(COMPLETED|PARTIAL|CANCELLED)\b/);
          if (!state || !taskId) return;
          window.supervisorNotified = window.supervisorNotified || new Set();
          if (window.supervisorNotified.has(taskId)) return;
          window.supervisorNotified.add(taskId);
          new Notification('ComfyUI Supervisor', { body: (window.supervisorLanguage === 'en' ? 'Task ' : '任务 ') + taskId.slice(0, 8) + ': ' + state[1] });
        }""")
        refresh_tasks.click(lambda l: gr.update(choices=task_choices(l)), language, outputs=task)
        def localized_create(*args):
            selection, result = create(*args[:-1])
            if "choices" in selection:
                selection["choices"] = task_choices(args[-1])
            return selection, localized_action(result, args[-1])

        start.click(localized_create, [goal, groups, per_group, budget, rounds, threshold, label, mode, auto_iterations, auto_candidates, folder, uploads, width, height, steps, cfg, seed, delete_mode, retention, permanent, calibrated, max_generations, runtime_hours, language], [task, message])

        def preview_references(files, directory, folder="", count=15, force=False):
            state = dict(directory) if isinstance(directory,dict) else {'source':'folder' if folder else 'images'}
            new_seed = state.get('_sample',{}).get('seed',secrets.randbits(32)) if not force else secrets.randbits(32)
            try:
                paths, candidates, sample = update_reference_sample(files,directory,folder,new_seed,count,force)
            except OSError:
                state.pop('_sample',None)
                return gr.update(value=[],visible=False), "参考图文件夹读取失败，请检查目录是否存在或重新选择。", new_seed, state
            except ValueError as exc:
                state.pop('_sample',None)
                return gr.update(value=[],visible=False), str(exc), new_seed, state
            state['_sample'] = sample
            # Decode only a page of selected thumbnails when the user expands it.
            readable = sum(path.is_file() for path in paths)
            result = f"候选 {candidates} 张 · 已随机抽样 {readable} 张 · 已打标待检查；展开可查看缩略图"
            return gr.update(value=[],visible=False), result if readable else "尚未选择有效参考图片",new_seed,state

        def reshuffle_references(files,directory,folder,count):
            return preview_references(files,directory,folder,count,force=True)

        def reference_preview_page(directory,page=1):
            import hashlib
            selected = directory.get('_sample',{}).get('selected',[]) if isinstance(directory,dict) else []
            pages = max(1,(len(selected)+11)//12)
            page = max(1,min(pages,int(page or 1)))
            images=[]
            failures=0
            for name in selected[(page-1)*12:page*12]:
                path=Path(name)
                try:
                    stat=path.stat()
                    key=hashlib.sha256(f'{path}:{stat.st_mtime_ns}:{stat.st_size}'.encode('utf-8')).hexdigest()
                    thumb=service.files.path(f'ui-cache/reference-thumbnails/{key}.jpg')
                    if not thumb.is_file():
                        atomic_write(thumb,thumbnail_bytes(path,edge=192))
                    images.append((str(thumb),path.name))
                except (OSError,ValueError,Image.DecompressionBombError):
                    failures+=1
            text=f'第 {page}/{pages} 页 · 抽样共 {len(selected)} 张'+(f' · {failures} 张无法预览' if failures else '')
            return gr.update(value=images,visible=bool(images)),text,page

        def refresh_open_reference_preview(directory,page,opened):
            if not opened:
                return gr.update(),gr.update(),gr.update()
            return reference_preview_page(directory,page)

        reference_preview_panel.expand(lambda:True,outputs=reference_preview_open,queue=False,show_progress='hidden').then(reference_preview_page,[studio_directory,reference_page],[reference_preview,reference_page_status,reference_page],show_progress='hidden')
        reference_preview_panel.collapse(lambda:False,outputs=reference_preview_open,queue=False,show_progress='hidden')
        reference_show.click(reference_preview_page,[studio_directory,reference_page],[reference_preview,reference_page_status,reference_page],show_progress='hidden')

        preview_inputs = [studio_uploads, studio_directory, studio_folder, sample_count]
        preview_outputs = [reference_preview, upload_status, sampling_seed,studio_directory]
        upload_preview = studio_uploads.change(preview_references, preview_inputs, preview_outputs,concurrency_id='reference-analysis',concurrency_limit=1)
        folder_preview = studio_folder.change(preview_references, preview_inputs, preview_outputs,concurrency_id='reference-analysis',concurrency_limit=1)
        def switch_reference_source(source):
            return {"source":source},gr.update(visible=source=="images"),gr.update(visible=source=="folder")
        source_preview = reference_source.input(switch_reference_source,reference_source,[studio_directory,reference_images,reference_folder],show_progress="hidden",concurrency_id='reference-analysis',concurrency_limit=1).then(preview_references,preview_inputs,preview_outputs,show_progress="hidden",concurrency_id='reference-analysis',concurrency_limit=1)
        # Keep the unrestricted number as the actual setting. The separate
        # slider only reflects its position within 1–50 and never clamps it.
        sample_count.input(fn=None,inputs=sample_count,outputs=sample_slider,
            js="(value) => Math.max(1, Math.min(50, Number(value) || 15))",queue=False)
        sample_slider.input(fn=None,inputs=sample_slider,outputs=sample_count,js="(value) => value",queue=False)
        count_previews = [
            sample_count.blur(preview_references,preview_inputs,preview_outputs,show_progress="hidden",concurrency_id='reference-analysis',concurrency_limit=1),
            sample_count.submit(preview_references,preview_inputs,preview_outputs,show_progress="hidden",concurrency_id='reference-analysis',concurrency_limit=1),
            sample_slider.release(preview_references,preview_inputs,preview_outputs,show_progress="hidden",concurrency_id='reference-analysis',concurrency_limit=1),
            sample_reset.click(lambda:(15,15),outputs=[sample_count,sample_slider],queue=False,show_progress="hidden")
                .then(preview_references,preview_inputs,preview_outputs,show_progress="hidden",concurrency_id='reference-analysis',concurrency_limit=1),
            sample_shuffle.click(reshuffle_references,preview_inputs,preview_outputs,show_progress='hidden',concurrency_id='reference-analysis',concurrency_limit=1)]
        async def auto_tag_references(files, directory, folder, reference_seed, direction, examples, scope, usd, token_limit, count=15):
            tag_task = None
            try:
                count = validate_sample_count(count)
                inputs, candidates = studio_reference_paths(files,directory,folder,reference_seed,count)
                if not inputs:
                    yield "尚未选择有效参考图片"
                    return
                settings = TaskSettings(goal=direction or "Analyze reference images",prompt_examples=examples or "",
                    demo=False,reference_only=True,sample_count=count,sample_seed=reference_seed,content_label=scope,budget_micro=int(usd*1_000_000),token_budget=int(token_limit),groups=1)
                pending=[]
                for path in inputs:
                    if not service.cached_reference_tag(settings,sha256(path)):
                        pending.append(path)
                cached_count=len(inputs)-len(pending)
                if not pending:
                    yield f"候选 {candidates} 张 · 已抽取 {len(inputs)} 张 · 已打标 {len(inputs)}/{len(inputs)} · 全部复用缓存"
                    return
                routes = service.cloud.candidates("tagging",scope,1,"USD")
                if not routes:
                    raise ValueError("请先在必填配置中保存支持视觉能力的 API 连接。")
                if not any(service.cloud.credential_key(credential) for _,_,credential in routes):
                    raise ValueError("未找到 API Key，请先保存 API 连接。")
                tag_task = service.create_task(settings,pending)
                refs = service.db.rows("SELECT * FROM assets WHERE task_id=? AND source_kind='reference'", (tag_task,))
                if not refs:
                    raise ValueError("NO_VALID_REFERENCE_IMAGES")
                service.enqueue(tag_task)
                yield f"候选 {candidates} 张 · 已抽取 {len(inputs)} 张 · 已打标 {cached_count}/{len(inputs)} · 已加入顺序队列"
                previous=None
                while True:
                    task_row=service.db.one('SELECT state,reason FROM tasks WHERE id=?',(tag_task,))
                    if task_row['state']=='COMPLETED':
                        break
                    if task_row['state'] in ('PAUSED','FAILED','CANCELLED','BLOCKED_POLICY','WAITING_APPROVAL'):
                        yield ('本轮已结束，参考图打标进度已保留；点击执行剩余队列。' if task_row['reason']=='ROUND_ENDED' else '参考图打标未完成：'+task_progress(service,tag_task))
                        return
                    tagged=sum(bool(json.loads(row['metadata']).get('model_tags')) for row in service.db.rows('SELECT metadata FROM assets WHERE task_id=?',(tag_task,)))
                    if tagged!=previous:
                        yield f"候选 {candidates} 张 · 已抽取 {len(inputs)} 张 · 已打标 {cached_count+tagged}/{len(inputs)}"
                        previous=tagged
                    await asyncio.sleep(.25)
                yield f"候选 {candidates} 张 · 已抽取 {len(inputs)} 张 · 已打标 {cached_count+len(refs)}/{len(inputs)} · 开始生图时复用结果"
            except Exception as exc:
                if tag_task:
                    service.db.transition(tag_task,"PAUSED","ANALYZE",str(exc) if len(str(exc))<100 else type(exc).__name__)
                    result = task_progress(service,tag_task)
                else:
                    result = str(exc) if isinstance(exc,ValueError) else form_error(exc,"zh")
                yield "参考图打标失败：" + result
        tagging_inputs = [studio_uploads,studio_directory,studio_folder,sampling_seed,studio_goal,studio_examples,api_scope,studio_budget,studio_token_budget,sample_count]
        for preview_event in (upload_preview,folder_preview,source_preview,*count_previews):
            preview_event.then(refresh_open_reference_preview,[studio_directory,reference_page,reference_preview_open],[reference_preview,reference_page_status,reference_page],show_progress='hidden')
            preview_event.then(auto_tag_references,tagging_inputs,upload_status,show_progress="hidden",concurrency_id="reference-tagging",concurrency_limit=1)
        def select_reference_folder(current):
            try:
                return gr.update(value=choose_folder(current))
            except Exception:
                gr.Warning("系统文件夹选择器未能打开，请重试或改用选图片。",duration=8)
                return gr.update()
        browse_reference.click(select_reference_folder, studio_folder, studio_folder, concurrency_id="folder-picker", concurrency_limit=1)
        browse_output.click(choose_folder, studio_output, studio_output, concurrency_id="folder-picker", concurrency_limit=1)

        def add_control_row(count,*groups):
            previous = max(1,min(30,int(count)))
            count = min(30,previous+1)
            inherited = groups[previous-1]
            return (count,*[gr.update(visible=index<count) for index in range(30)],
                *[gr.update(value=inherited) if index==previous and previous<30 else gr.update() for index in range(30)],
                gr.update(interactive=count<30))
        control_add.click(add_control_row,[control_line_count,*control_groups],[control_line_count,*control_lines,*control_groups,control_add],show_progress="hidden",concurrency_id="control-rows",concurrency_limit=1)

        def remove_control_row(index,count,*values):
            count=max(1,min(30,int(count)))
            rows=[list(row) for row in zip(values[::3],values[1::3],values[2::3])]
            kept=rows[:index]+rows[index+1:count] if index<count else rows[:count]
            kept=kept or [[rows[0][0] if rows else '1','','']]
            count=len(kept)
            kept += [['1','',''] for _ in range(30-count)]
            return (count,*[v for row in kept for v in row],*[gr.update(visible=i<count) for i in range(30)],gr.update(interactive=count<30))
        for index,delete_button in enumerate(control_delete_buttons):
            delete_button.click(lambda count,*values,index=index:remove_control_row(index,count,*values),
                [control_line_count,*control_inputs],[control_line_count,*control_inputs,*control_lines,control_add],
                show_progress='hidden',concurrency_id='control-rows',concurrency_limit=1)

        def preview_control_words(group_count,*values):
            import html
            try:
                rows=list(zip(values[::3],values[1::3],values[2::3]))
                return control_preview_html(parse_control_words(rows,int(group_count)),int(group_count))
            except (ValueError,TypeError) as exc:
                return '<p>控制词格式无效：'+html.escape(str(exc))+'</p>'
        control_preview_button.click(preview_control_words,[studio_count,*control_inputs],configured_control_preview,show_progress='hidden')
        control_preview_panel.expand(preview_control_words,[studio_count,*control_inputs],configured_control_preview,show_progress='hidden')
        def control_scope(group_count,*values):
            try:
                words=parse_control_words(list(zip(values[::3],values[1::3],values[2::3])),int(group_count))
                if not words:
                    return '尚未填写控制词。'
                covered=[i for i in range(1,int(group_count)+1) if any(word.group in (None,i) for word in words)]
                missing=[i for i in range(1,int(group_count)+1) if i not in covered]
                return '控制词应用组：'+', '.join(map(str,covered))+('；未应用组：'+', '.join(map(str,missing))+'。需全部应用时，组号填“全部”。' if missing else '（全部覆盖）。')
            except (ValueError,TypeError):
                return '控制词格式尚未完整，请核对组号和权重。'
        for field in [studio_count,*control_inputs]:
            field.change(control_scope,[studio_count,*control_inputs],studio_control_scope,show_progress='hidden')

        preset_store=PresetStore(service.project_root/'config/studio-presets.json')
        preset_fields=[sample_count,studio_goal,studio_styles,studio_examples,studio_count,studio_per_group,
            studio_width,studio_height,studio_steps,studio_cfg,studio_seed,studio_sampler,studio_scheduler,
            studio_threshold,studio_rounds,studio_budget,studio_token_budget,studio_hours,api_scope,studio_lora_triggers,studio_character]
        preset_outputs=[*preset_fields,*control_inputs,control_line_count,*control_lines,control_add,sample_slider,preset_status]

        def refresh_presets():
            try:
                return gr.update(choices=sorted(preset_store.read()))
            except (OSError,ValueError):
                gr.Warning('常用方案文件读取失败，请检查 config/studio-presets.json。')
                return gr.update()

        def save_studio_preset(name,*values):
            try:
                count,direction,themes,examples,group_count,per_group,w,h,steps,cfg,seed,sampler,scheduler,quality,rounds,usd,tokens,hours,scope=values[:19]
                trigger=values[19] if len(values)>110 else ''
                character=values[20] if len(values)>111 else ''
                character,character_controls=parse_character_input(character or '',int(group_count))
                offset=21 if len(values)>111 else (20 if len(values)>110 else 19)
                visible=values[offset]
                flat=values[offset+1:]
                rows=[list(row) for row in zip(flat[::3],flat[1::3],flat[2::3])]
                preset=StudioPreset(sample_count=validate_sample_count(count),goal=direction,themes=themes,
                    examples=examples or '',lora_trigger_words=trigger or '',character=character or '',character_controls=character_controls,groups=group_count,per_group=per_group,
                    params={'width':w,'height':h,'steps':steps,'cfg':cfg,'seed':seed,'sampler':sampler,'scheduler':scheduler},
                    quality=quality,rounds=rounds,budget=usd,tokens=tokens,hours=hours,scope=scope,rows=rows,visible_rows=visible)
                name=preset_store.save(name,preset)
                return gr.update(choices=sorted(preset_store.read()),value=name),f'已保存方案「{name}」，下次启动仍可载入。'
            except (OSError,ValueError,TypeError):
                return gr.update(),'方案未保存：请填写名称，并检查主题组数、控制词、参数和限额。'

        def load_studio_preset(name):
            try:
                preset=preset_store.read().get(name)
                if preset is None:
                    raise ValueError('请选择已保存方案')
                p=preset.params
                rows=preset.rows+[['1','',''] for _ in range(30-len(preset.rows))]
                fields=[preset.sample_count,preset.goal,preset.themes,preset.examples,preset.groups,preset.per_group,
                    p.width,p.height,p.steps,p.cfg,p.seed,p.sampler,p.scheduler,preset.quality,preset.rounds,
                    preset.budget,preset.tokens,preset.hours,preset.scope,preset.lora_trigger_words,character_form_value(preset)]
                return (*fields,*[value for row in rows for value in row],preset.visible_rows,
                    *[gr.update(visible=index<preset.visible_rows) for index in range(30)],
                    gr.update(interactive=preset.visible_rows<30),min(50,preset.sample_count),f'已载入方案「{name}」，检查参考图后开始生图。')
            except (OSError,ValueError,TypeError):
                return (*[gr.update() for _ in preset_outputs[:-1]],'方案未载入：请选择有效的已保存方案。')

        app.load(refresh_presets,outputs=preset_choice,show_progress='hidden')
        preset_save.click(save_studio_preset,[preset_name,*preset_fields,control_line_count,*control_inputs],[preset_choice,preset_status],show_progress='hidden',concurrency_id='studio-presets',concurrency_limit=1)
        preset_preview=(preset_load.click(load_studio_preset,preset_choice,preset_outputs,show_progress='hidden',concurrency_id='reference-analysis',concurrency_limit=1)
            .then(preview_references,preview_inputs,preview_outputs,show_progress='hidden',concurrency_id='reference-analysis',concurrency_limit=1))
        preset_preview.then(refresh_open_reference_preview,[studio_directory,reference_page,reference_preview_open],[reference_preview,reference_page_status,reference_page],show_progress='hidden')
        preset_preview.then(auto_tag_references,tagging_inputs,upload_status,show_progress='hidden',concurrency_id='reference-tagging',concurrency_limit=1)

        last_direct_fields=[direct_positive,direct_negative,direct_count,direct_review,direct_output,direct_width,direct_height,
            direct_steps,direct_cfg,direct_seed,direct_scope,direct_threshold,direct_rounds,direct_budget,direct_tokens,
            direct_hours,direct_sampler,direct_scheduler]
        last_import_outputs=[*preset_outputs,studio_uploads,reference_source,studio_folder,sampling_seed,studio_directory,studio_output,
            reference_images,reference_folder,*last_direct_fields,views,editor_page,results_page,message,comfy_url,workflow_text,workflow_nodes,workflow_source,setup_workflow_status,studio_errors,direct_feedback,studio_feedback,studio_zip,direct_zip]

        def import_last_configuration():
            try:
                settings,folder = service.load_last_run()
                p=settings.params
                scope=settings.content_label if settings.content_label in ('sfw','adult_allowed') else 'sfw'
                positive,negative=service.last_run_prompts()
                rows=control_word_rows(settings.control_words)[:30]
                visible=max(1,len(rows))
                rows += [['1','',''] for _ in range(30-len(rows))]
                status='已导入上次配置和提示词。'+('参考图将按文件夹路径重新抽样。' if folder else '未恢复拖入图片；请重新选择参考图或文件夹。')
                fields=[settings.sample_count,settings.goal,'\n'.join(settings.target_styles),settings.prompt_examples,settings.groups,
                    settings.per_group,p.width,p.height,p.steps,p.cfg,p.seed,p.sampler,p.scheduler,settings.quality_threshold,
                    settings.max_rounds,settings.budget_micro/1_000_000,settings.token_budget,settings.max_runtime_seconds/3600,scope,settings.lora_trigger_words,character_form_value(settings)]
                preset=(*fields,*[value for row in rows for value in row],visible,*[gr.update(visible=index<visible) for index in range(30)],
                    gr.update(interactive=visible<30),min(50,settings.sample_count),status)
                direct=[positive,negative,min(50,settings.per_group if settings.direct_prompt is not None else settings.groups*settings.per_group),
                    settings.review_enabled,settings.export_folder or '',p.width,p.height,p.steps,p.cfg,p.seed,scope,
                    settings.quality_threshold,settings.max_rounds,settings.budget_micro/1_000_000,settings.token_budget,
                    settings.max_runtime_seconds/3600,p.sampler,p.scheduler]
                graph=json.loads((service.project_root/service.workflow.workflow_api_json).read_bytes()) if service.workflow else {}
                gr.Info(status,duration=8)
                return (*preset,None,'folder' if folder else 'images',folder or '',secrets.randbits(32),{'source':'folder' if folder else 'images'},
                    settings.export_folder or '',gr.update(visible=not bool(folder)),gr.update(visible=bool(folder)),*direct,gr.update(selected='prompt-studio' if settings.direct_prompt is not None else 'studio'),
                    gr.update(visible=True),gr.update(visible=False),status,service.workflow.base_url if service.workflow else 'http://127.0.0.1:8188',service.workflow.model_dump_json(indent=2) if service.workflow else '{}',
                    workflow_summary_html(workflow_summary(graph)),'上次使用的工作流','已导入上次工作流','[]','','',settings.delivery_format=='zip',settings.delivery_format=='zip')
            except (OSError,ValueError) as exc:
                gr.Warning(str(exc),duration=10)
                result=[gr.update() for _ in last_import_outputs]
                result[last_import_outputs.index(message)]=str(exc)
                return tuple(result)

        last_import_preview=last_run_import.click(import_last_configuration,outputs=last_import_outputs,show_progress='hidden',concurrency_id='reference-analysis',concurrency_limit=1).then(fn=None,
            js="() => { document.querySelector('#editor-page [role=tab][aria-selected=\"true\"]')?.click(); window.scrollTo(0,0); return []; }").then(preview_references,preview_inputs,preview_outputs,show_progress='hidden',concurrency_id='reference-analysis',concurrency_limit=1)
        last_import_preview.then(refresh_open_reference_preview,[studio_directory,reference_page,reference_preview_open],[reference_preview,reference_page_status,reference_page],show_progress='hidden')
        last_import_preview.then(auto_tag_references,tagging_inputs,upload_status,show_progress='hidden',concurrency_id='reference-tagging',concurrency_limit=1)

        def refresh_caption_input(mode=False,announce=False):
            enabled=bool(configured_branch(service))
            text=('原图元数据导入工作流、正负提示词及参数。' if mode else ('仅运行工作流反推分支，填入正向提示词。' if enabled else '当前工作流没有反推分支；可用右侧图片识别。'))
            return gr.update(interactive=enabled,visible=not mode,value=None),gr.update(visible=mode),enabled,(text if announce or not mode else gr.update())

        caption_mode_outputs=[caption_input,caption_workflow_input,caption_available,caption_status]
        app.load(lambda mode:refresh_caption_input(mode,True),inputs=caption_workflow_mode,outputs=caption_mode_outputs,show_progress='hidden')
        workflow_text.change(refresh_caption_input,inputs=caption_workflow_mode,outputs=caption_mode_outputs,show_progress='hidden')
        caption_workflow_mode.change(lambda mode:refresh_caption_input(mode,True),caption_workflow_mode,caption_mode_outputs,show_progress='hidden')
        caption_available.change(fn=None,inputs=caption_available,js="(enabled) => { document.getElementById('caption-input')?.classList.toggle('caption-disabled', !enabled); }")

        async def caption_uploaded(image):
            if not image or not configured_branch(service):
                yield gr.update(),'当前工作流没有可独立执行的反推节点，图片输入已禁用。'
                return
            try:
                task_id=service.enqueue_caption(image)
                yield gr.update(),'反推已加入顺序队列，等待 ComfyUI 返回文字。'
                waiting_round=False
                while True:
                    row=service.db.one('SELECT state,reason FROM tasks WHERE id=?',(task_id,))
                    job=service.db.one('SELECT result FROM caption_jobs WHERE task_id=?',(task_id,))
                    if row['state']=='COMPLETED' and job['result']:
                        gr.Info('反推成功，已填入正向提示词；本次反推已结束。',duration=8)
                        yield job['result'],'反推完成，已填入正向提示词；本次操作已结束。'
                        return
                    if row['state'] in ('PAUSED','FAILED','CANCELLED'):
                        if row['reason']=='ROUND_ENDED':
                            if not waiting_round:
                                yield gr.update(),'本轮已结束，反推任务已保留；点击执行剩余队列后自动回填结果。'
                                waiting_round=True
                            await asyncio.sleep(1)
                            continue
                        yield gr.update(),'反推未完成：'+str(row['reason'] or '任务已停止')
                        return
                    await asyncio.sleep(1)
            except (ValueError,OSError) as exc:
                gr.Warning(str(exc),duration=10)
                yield gr.update(),'反推失败：'+str(exc)
        caption_input.upload(caption_uploaded,caption_input,[direct_positive,caption_status],concurrency_id='prompt-image-input',concurrency_limit=1,show_progress='hidden',trigger_mode='always_last')

        async def prompt_image_uploaded(image,scope,usd,token_limit):
            if not image:
                yield gr.update(),gr.update(),'请上传原始图片。'
                return
            try:
                pair=embedded_prompts(image)
                if pair:
                    yield pair[0],pair[1],'已读取原图元数据提示词，未调用模型；当前工作流保留。'
                    return
                task_id=enqueue_image_prompt(service,image,scope,usd,token_limit)
                yield gr.update(),gr.update(),'没有可读取的提示词，模型反推已加入顺序队列。'
                while True:
                    row=service.db.one('SELECT state,reason FROM tasks WHERE id=?',(task_id,))
                    job=service.db.one('SELECT result FROM image_prompt_jobs WHERE task_id=?',(task_id,))
                    if row['state']=='COMPLETED' and job['result']:
                        result=json.loads(job['result'])
                        yield result['positive'],result['negative'],'已填入模型反推提示词（推测描述，并非原始提示词）。'
                        return
                    if row['state'] in ('PAUSED','FAILED','CANCELLED','PARTIAL','BLOCKED_POLICY','WAITING_APPROVAL'):
                        yield gr.update(),gr.update(),'模型反推未完成：'+task_progress(service,task_id)+'；原提示词已保留。'
                        return
                    await asyncio.sleep(.25)
            except Exception as exc:
                result=str(exc) if isinstance(exc,(ValueError,OSError)) else form_error(exc,'zh')
                yield gr.update(),gr.update(),'图片识别失败：'+result+'；原提示词已保留。'
        prompt_image_input.upload(prompt_image_uploaded,[prompt_image_input,direct_scope,image_prompt_budget,image_prompt_tokens],
            [direct_positive,direct_negative,prompt_image_status],concurrency_id='prompt-image-input',concurrency_limit=1,show_progress='hidden',trigger_mode='always_last')

        async def studio_create(files, directory, input_folder, count, direction, styles, examples, group_count, run_mode,
                          output, w, h, n_steps, guidance, base_seed, scope, quality, usd, n_rounds, hours, lang, sampler, scheduler, reference_seed=None,token_limit=50000,per_group_target=1,control_rows=None,*control_values,on_progress=None,delivery_format='files'):
            report=on_progress or (lambda text:None)
            report("正在检查输入")
            try:
                try:
                    count = validate_sample_count(count)
                except ValueError as exc:
                    raise StudioFailure(str(exc),["studio-reference-count"]) from None
                if isinstance(directory,dict) and not directory:
                    directory={'source':'images'}
                sampling_directory = directory
                files,directory,input_folder = reference_source_inputs(files,directory,input_folder)
                fields, missing = [], []
                if not files and not directory and not (input_folder or "").strip():
                    fields.append("studio-references")
                    missing.append("参考图片")
                if input_folder and not Path(input_folder).is_dir():
                    raise StudioFailure("参考图文件夹不存在，请重新选择。", ["studio-folder"])
                if not (direction or "").strip():
                    fields.append("studio-goal")
                    missing.append("共同要求")
                target_styles = [line.strip() for line in (styles or "").splitlines() if line.strip()]
                if not (output or '').strip():
                    fields.append('studio-output')
                    missing.append('输出文件夹')
                if fields:
                    raise StudioFailure("请补全：" + "、".join(missing), fields)
                if group_count is None or isinstance(group_count,bool) or not math.isfinite(group_count) or int(group_count) != group_count or not 1 <= group_count <= 20:
                    raise StudioFailure("主题组数须为 1–20 的整数。", ["studio-groups"])
                if per_group_target is None or isinstance(per_group_target,bool) or not math.isfinite(per_group_target) or int(per_group_target) != per_group_target or not 1 <= per_group_target <= 50:
                    raise StudioFailure("每组产出张数须为 1–50 的整数。", ["studio-per-group"])
                if output and not Path(output).is_absolute():
                    raise StudioFailure("输出目录必须是绝对路径，请使用浏览按钮选择。", ["studio-output"])
                try:
                    lora_triggers = ''
                    character = ''
                    if control_values:
                        flat = [control_rows,*control_values]
                        if len(flat)==92:
                            character=flat.pop() or ''
                        if len(flat)==91:
                            lora_triggers=flat.pop() or '' 
                        if len(flat) % 3:
                            raise ValueError("Invalid control inputs")
                        control_rows = list(zip(flat[::3],flat[1::3],flat[2::3]))
                    control_words = parse_control_words(control_rows,int(group_count))
                    character,character_controls=parse_character_input(character,int(group_count))
                except (ValueError,TypeError):
                    raise StudioFailure("控制词格式无效：组号填“全部”或主题组数范围内的整数；多个组号或 tag 用逗号分隔，同行同权重（留空或填 0.1–3）。同组 tag 不要重复，不要填括号及权重语法；每个 tag 最多 100 字符，共最多 300 个。",["studio-control-words"])
                reference_seed = reference_seed if reference_seed is not None else secrets.randbits(32)
                report("正在准备参考图片")
                inputs, candidate_count = await asyncio.to_thread(studio_reference_paths,files,sampling_directory,input_folder,reference_seed,count)
                if not inputs:
                    raise StudioFailure("参考图文件夹中没有可读取的图片。", ["studio-folder"])
                settings = TaskSettings(goal=direction, autonomous=True, images_only_delivery=True, target_styles=target_styles,
                    prompt_examples=examples or "", character=character.strip(),character_controls=character_controls, lora_trigger_words=lora_triggers, control_words=control_words, groups=int(group_count), per_group=int(per_group_target),
                    demo=False, sample_count=count, sample_seed=reference_seed,
                    export_folder=(output or "").strip() or None, delivery_layout='flat',delivery_format=delivery_format,auto_candidates=True, auto_iterations=True,
                    delete_mode="direct", allow_permanent_delete=True, prescreen=False,
                    content_label=scope, quality_threshold=quality, budget_micro=int(usd * 1_000_000),token_budget=int(token_limit),
                    max_rounds=int(n_rounds), patience=min(3, int(n_rounds)), max_runtime_seconds=int(hours * 3600),
                    params={"width":int(w), "height":int(h), "steps":int(n_steps), "cfg":guidance, "seed":int(base_seed), "sampler":sampler, "scheduler":scheduler})
                await check_live_ready(service, scope, settings.params.model_dump(),on_progress=report)
                report("正在建立任务并整理参考图片")
                task_id = await asyncio.to_thread(service.create_task,settings,[Path(p) for p in inputs])
                await asyncio.to_thread(service.remember_last_run,settings,input_folder if isinstance(sampling_directory,dict) and sampling_directory.get('source') == 'folder' else None,task_id=task_id)
                if candidate_count:
                    service.db.event(task_id, "SAMPLE", {"seed":reference_seed,"candidate_count":candidate_count,"selected_count":len(inputs),"requested_count":count})
                report("正在加入生成队列")
                service.enqueue(task_id)
                result = "任务已开始：已加入顺序队列 · 云端打标 → " + ("模型随机补选主题 → " if len(target_styles) != int(group_count) else "") + "提示词生成 → ComfyUI 生图 → 质量评审" + (' · 本轮已结束，点击执行剩余队列后运行' if service.round_ended() else '')
                gr.Info(result, duration=8)
                return gr.update(choices=task_choices(lang), value=task_id), result, result, "[]", gr.update(selected="studio"), gr.update()
            except Exception as exc:
                if str(exc) == "OUTPUT_FOLDER_UNWRITABLE":
                    exc = StudioFailure("无法写入输出文件夹，请重新选择可写入的目录。", ["studio-output"])
                result = "无法开始：" + (str(exc) if isinstance(exc, StudioFailure) else form_error(exc, lang))
                fields = exc.fields if isinstance(exc, StudioFailure) else []
                tab = exc.tab if isinstance(exc, StudioFailure) else "studio"
                gr.Warning(result, duration=15)
                return gr.update(), result, result, json.dumps({"fields":fields, "nonce":time.monotonic_ns()}), gr.update(selected=tab), result if tab == "setup" else gr.update()

        def form_error(exc, lang):
            code = str(exc)
            code = code if code and len(code) < 100 and all(c.isupper() or c == "_" for c in code) else type(exc).__name__
            return translate(code, lang)

        async def direct_create(positive,negative,count,review,output,w,h,n_steps,guidance,base_seed,scope,quality,n_rounds,usd,token_limit,hours,sampler,scheduler,lang,lora_triggers="",*,on_progress=None,delivery_format='files'):
            report=on_progress or (lambda text:None)
            report("正在检查输入")
            try:
                missing = []
                if not (positive or '').strip():
                    missing.append('direct-positive')
                if not (output or '').strip():
                    missing.append('direct-output')
                if missing:
                    raise StudioFailure('请补全：' + '、'.join({'direct-positive':'正向提示词','direct-output':'输出文件夹'}[field] for field in missing),missing,'prompt-studio')
                if output and not Path(output).is_absolute():
                    raise StudioFailure("输出目录必须是绝对路径，请使用浏览按钮选择。",["direct-output"],"prompt-studio")
                settings = TaskSettings(goal=positive[:3000],direct_prompt=positive,direct_negative=negative or "",lora_trigger_words=lora_triggers or "",review_enabled=review,
                    autonomous=True,images_only_delivery=True,groups=1,per_group=int(count),demo=False,
                    export_folder=(output or "").strip() or None,delivery_layout='flat',delivery_format=delivery_format,auto_candidates=True,auto_iterations=True,prescreen=False,
                    content_label=scope,quality_threshold=quality,max_rounds=int(n_rounds) if review else int(count),
                    budget_micro=int(usd*1_000_000),token_budget=int(token_limit),max_runtime_seconds=int(hours*3600),
                    params={"width":int(w),"height":int(h),"steps":int(n_steps),"cfg":guidance,"seed":int(base_seed),"sampler":sampler,"scheduler":scheduler})
                purposes = (("review","质量评审",1),) if review else ()
                await check_live_ready(service,scope,settings.params.model_dump(),purposes=purposes,on_progress=report)
                report("正在建立生图任务")
                task_id = await asyncio.to_thread(service.create_task,settings)
                await asyncio.to_thread(service.remember_last_run,settings,task_id=task_id)
                report("正在加入生成队列")
                service.enqueue(task_id)
                text = "提示词生图已开始：已加入顺序队列，" + ("生成后进行质量审查。" if review else "不审查，生成后直接保存。") + ('本轮已结束，点击执行剩余队列后运行。' if service.round_ended() else '')
                gr.Info(text,duration=8)
                return gr.update(choices=task_choices(lang),value=task_id),text,text,gr.update(selected="prompt-studio"), '[]'
            except Exception as exc:
                if str(exc) == 'OUTPUT_FOLDER_UNWRITABLE':
                    exc = StudioFailure('无法写入输出文件夹，请重新选择可写入的目录。',['direct-output'],'prompt-studio')
                text = "无法开始：" + (str(exc) if isinstance(exc,(ConnectionFailure,StudioFailure)) else form_error(exc,lang))
                gr.Warning(text,duration=12)
                return gr.update(),text,text,gr.update(selected=exc.tab if isinstance(exc,StudioFailure) else 'prompt-studio'), json.dumps({'fields':exc.fields if isinstance(exc,StudioFailure) else [],'nonce':time.monotonic_ns()})

        async def direct_submit(*values):
            async for update in startup_progress(partial(direct_create,delivery_format='zip' if values[-1] else 'files'),values[:-1],5):
                yield update

        async def studio_submit(*values):
            async for update in startup_progress(partial(studio_create,delivery_format='zip' if values[-1] else 'files'),values[:-1],6):
                yield update

        app.load(fn=None,js=STARTUP_JS)
        direct_start.click(direct_submit,[direct_positive,direct_negative,direct_count,direct_review,direct_output,
            direct_width,direct_height,direct_steps,direct_cfg,direct_seed,direct_scope,direct_threshold,direct_rounds,
            direct_budget,direct_tokens,direct_hours,direct_sampler,direct_scheduler,language,direct_zip],[task,message,direct_feedback,views,studio_errors,direct_start_status],
            show_progress="hidden",concurrency_id="direct-start",concurrency_limit=1).then(open_results_if_started,[task,message],[editor_page,results_page],show_progress="hidden").then(fn=lambda:None,js="() => { if(document.querySelector('#results-page')?.offsetHeight) window.scrollTo(0,0); return []; }").then(refresh_all,refresh_inputs,outputs,js=refresh_selection_js,show_progress="hidden")
        direct_review.change(lambda enabled: gr.update(visible=enabled),direct_review,direct_review_limits,show_progress="hidden")
        direct_browse.click(choose_folder,direct_output,direct_output,concurrency_id="folder-picker",concurrency_limit=1)
        direct_stop.click(lambda t,l: localized_action(safe_action(stop,t),l),[task,language],message)
        direct_resume.click(continue_task,[task,language],[task,message])

        async def read_editor():
            try:
                config,graph,rows = await read_workflow_editor(service)
                key = rows[0]["key"] if rows else ""
                loras=sum('lora' in node['class_type'].lower() for node in graph.values())
                text=f'已读取已导入工作流 {Path(config.workflow_api_json).name}：{len(graph)} 个节点，{loras} 个 LoRA 节点。下方添加列表为本机可选模型。'
                return node_table(rows,key),gr.update(value=key),rows,text
            except Exception as exc:
                text = str(exc) if isinstance(exc,ConnectionFailure) else "读取失败，请检查工作流和 ComfyUI 连接。"
                return node_table([]),gr.update(value=""),[],text

        async def open_cached_editor(key=""):
            try:
                rows=cached_editor_rows(service)
                key=key if any(row['key']==key for row in rows) else (rows[0]['key'] if rows else '')
                return node_table(rows,key),gr.update(value=key),rows,'已显示缓存参数；绕过和分支选择将在开始新任务时生效。'
            except (ConnectionFailure,OSError):
                return await read_editor()

        def select_editor(key,rows):
            row = next((r for r in rows if r['key']==key),None)
            kind = row['type'] if row else None
            value = row['value'] if row else None
            return (gr.update(visible=kind=='enum',choices=row['choices'] if kind=='enum' else [],value=value if kind=='enum' else None),
                    gr.update(visible=kind in ('INT','FLOAT'),value=value if kind in ('INT','FLOAT') else None,
                              precision=0 if kind=='INT' else None,minimum=row['min'] if row else None,maximum=row['max'] if row else None),
                    gr.update(visible=kind=='BOOLEAN',value=value if kind=='BOOLEAN' else False),
                    gr.update(visible=kind=='STRING',value=value if kind=='STRING' else ""),
                    parameter_form(rows,key,rows[0].get('revision','') if rows else ''),node_table(rows,key))

        async def save_editor(key,choice,number,boolean,text,rows):
            try:
                row = next((r for r in rows if r['key']==key),None)
                if row is None:
                    raise ConnectionFailure("请先读取并选择节点参数。")
                value = choice if row['type']=='enum' else number if row['type'] in ('INT','FLOAT') else boolean if row['type']=='BOOLEAN' else text
                await save_workflow_parameter(service,key,value)
                table,field,new_rows,_ = await read_editor()
                field['value'] = key
                result = "已保存：两种生图方式将使用新工作流参数，提示词及节点连接保留。"
                gr.Info(result,duration=8)
                return result,table,field,new_rows,workflow_summary_html(workflow_summary(json.loads((service.project_root/service.workflow.workflow_api_json).read_text(encoding='utf-8')))),service.workflow.model_dump_json(indent=2),"已保存自定义工作流"
            except Exception as exc:
                result = str(exc) if isinstance(exc,ConnectionFailure) else "保存失败，请重新读取参数后再试。"
                gr.Warning(result,duration=10)
                return result,*[gr.update() for _ in range(6)]

        async def toggle_editor_bypass(request,key=""):
            recorded=False
            try:
                body=json.loads(request)
                if body.get('kind')=='switch':
                    rows=await set_workflow_switch(service,body['node_id'],body['value'])
                    result=f"节点 {body['node_id']}：已记录分支切换为 {str(body['value'])}，开始新任务时生效。"
                else:
                    rows=await set_workflow_bypass(service,body['node_id'],body['enabled'])
                    result=f"节点 {body['node_id']}："+('已记录绕过，下次提交生图时生效。' if body['enabled'] else '已记录恢复，下次提交生图时启用。')
                recorded=True
                gr.Info(result,duration=6)
            except Exception as exc:
                result=str(exc) if isinstance(exc,ConnectionFailure) else '记录失败，请重新读取工作流。'
                gr.Warning(result,duration=8)
                try:
                    rows=cached_editor_rows(service)
                except (ConnectionFailure,OSError):
                    rows=[]
            key=key if any(row['key']==key for row in rows) else (rows[0]['key'] if rows else '')
            selected=next((row for row in rows if row['key']==key),None)
            boolean=gr.update(value=selected['value']) if selected and selected.get('switch') and selected['type']=='BOOLEAN' and selected['input']==selected['switch']['input'] else gr.update()
            return node_table(rows,key),gr.update(value=key),rows,result,service.workflow.model_dump_json(indent=2) if service.workflow else '', '节点选择已记录' if recorded else gr.update(),boolean

        bypass_saved=editor_bypass_trigger.click(toggle_editor_bypass,inputs=[editor_bypass_request,editor_field],
            outputs=[editor_table,editor_field,editor_rows,editor_status,workflow_text,workflow_source,editor_boolean],
            js="(_,key) => [document.querySelector('#editor-bypass-request textarea, #editor-bypass-request input').value,key]",
            concurrency_id='workflow-bypass',concurrency_limit=1,show_progress='hidden')
        bypass_saved.then(fn=None,js="() => {window.supervisorBypassBusy=false;}")

        editor_outputs = [editor_choice,editor_number,editor_boolean,editor_text,editor_parameters,editor_table]
        editor_read_outputs = [editor_table,editor_field,editor_rows,editor_status]
        def show_lora_editor():
            try:
                return lora_form(lora_options(service))
            except (ConnectionFailure,OSError):
                return '<div class="lora-add-panel"><strong>添加 LoRA 节点</strong><p>请先读取工作流参数和本机模型列表。</p></div>'
        editor_loaded=editor_refresh.click(read_editor,outputs=editor_read_outputs).then(select_editor,[editor_field,editor_rows],editor_outputs,show_progress="hidden")
        editor_loaded.then(show_lora_editor,outputs=editor_lora,show_progress="hidden")
        editor_opened=workflow_editor_tab.select(open_cached_editor,inputs=editor_field,outputs=editor_read_outputs).then(select_editor,[editor_field,editor_rows],editor_outputs,show_progress="hidden")
        editor_opened.then(show_lora_editor,outputs=editor_lora,show_progress="hidden")
        editor_field.change(select_editor,[editor_field,editor_rows],editor_outputs,show_progress="hidden").then(fn=lambda:None,js=SCROLL_EDITOR_JS)
        bypass_saved.then(select_editor,[editor_field,editor_rows],editor_outputs,show_progress='hidden')
        async def save_editor_rows(request,key):
            try:
                body=json.loads(request)
                if not isinstance(body,dict):
                    raise ConnectionFailure('修改格式无效，请重新读取工作流。')
                if body.get('kind')=='lora':
                    node_id=await add_workflow_lora(service,body)
                    key=f'{node_id}:lora_name'
                    result=f'已添加 LoRA 节点 {node_id}，并保存到新工作流。'
                elif body.get('kind')=='parameters':
                    await save_workflow_parameters(service,body)
                    result='已统一保存修改的参数。新任务将使用新工作流。'
                else:
                    raise ConnectionFailure('修改类型无效。')
                _,_,rows=await read_workflow_editor(service)
                graph=service._workflow_editor_cache['graph']
                gr.Info(result,duration=8)
                return result,node_table(rows,key),gr.update(value=key),rows,workflow_summary_html(workflow_summary(graph)),service.workflow.model_dump_json(indent=2),'已保存自定义工作流'
            except Exception as exc:
                result=str(exc) if isinstance(exc,ConnectionFailure) else '保存失败，请检查 ComfyUI 连接并重新读取参数。'
                gr.Warning(result,duration=10)
                return result,*[gr.update() for _ in range(6)]
        bulk_edited=editor_bulk_trigger.click(save_editor_rows,[editor_bulk_request,editor_field],
            [editor_status,editor_table,editor_field,editor_rows,workflow_nodes,workflow_text,workflow_source],
            js="(_,key) => [document.querySelector('#editor-bulk-request textarea, #editor-bulk-request input').value,key]",
            concurrency_id='workflow-edit',concurrency_limit=1,show_progress='hidden')
        bulk_edited.then(select_editor,[editor_field,editor_rows],editor_outputs,show_progress='hidden').then(show_lora_editor,outputs=editor_lora,show_progress='hidden').then(fn=None,js="() => {window.supervisorWorkflowEditBusy=false;window.supervisorRenderParameters?.();}")
        edited = editor_save.click(save_editor,[editor_field,editor_choice,editor_number,editor_boolean,editor_text,editor_rows],
            [editor_status,editor_table,editor_field,editor_rows,workflow_nodes,workflow_text,workflow_source],concurrency_id="workflow-edit",concurrency_limit=1)
        edited.then(select_editor,[editor_field,editor_rows],editor_outputs,show_progress="hidden")

        studio_start.click(studio_submit, [studio_uploads, studio_directory, studio_folder, sample_count, studio_goal,
            studio_styles, studio_examples, studio_count, studio_mode, studio_output, studio_width, studio_height,
            studio_steps, studio_cfg, studio_seed, api_scope, studio_threshold, studio_budget, studio_rounds, studio_hours, language, studio_sampler, studio_scheduler, sampling_seed,studio_token_budget,studio_per_group,*control_inputs,studio_lora_triggers,studio_character,studio_zip], [task, message, studio_feedback, studio_errors, views, setup_feedback,studio_start_status], show_progress="hidden",concurrency_id="studio-start",concurrency_limit=1).then(open_results_if_started,[task,message],[editor_page,results_page],show_progress="hidden").then(fn=lambda:None,js="() => { if(document.querySelector('#results-page')?.offsetHeight) window.scrollTo(0,0); return []; }").then(refresh_all,refresh_inputs,outputs,js=refresh_selection_js,show_progress="hidden")
        studio_errors.change(fn=None, inputs=studio_errors, js="""(value) => {
            document.querySelectorAll('.api-invalid').forEach(el => el.classList.remove('api-invalid'));
            let fields=[]; try { const data=JSON.parse(value || '[]'); fields=Array.isArray(data) ? data : data.fields || []; } catch {}
            setTimeout(() => {
                fields.forEach(id => document.getElementById(id)?.classList.add('api-invalid'));
                const first=document.getElementById(fields[0]);
                if(first) { first.scrollIntoView({behavior:'smooth',block:'center'}); first.querySelector('input,textarea')?.focus(); }
            },200);
        }""")
        for field,control in (('studio-output',studio_output),('direct-output',direct_output),('direct-positive',direct_positive),('studio-goal',studio_goal)):
            control.change(fn=None,inputs=control,js="(value) => { if ((value || '').trim()) document.getElementById("+json.dumps(field)+")?.classList.remove('api-invalid'); return []; }",show_progress='hidden')
        async def enter_generation(scope):
            try:
                params = {**{"width":768,"height":768,"steps":20,"cfg":7,"seed":42,"sampler":"euler","scheduler":"normal"}, **workflow_defaults(service)}
                await check_live_ready(service, scope, params)
                return gr.update(selected="studio"), "配置检查通过：云端模型与 ComfyUI 工作流已就绪", "[]"
            except Exception as exc:
                result = "配置未就绪：" + (str(exc) if isinstance(exc, StudioFailure) else form_error(exc, "zh"))
                fields = exc.fields if isinstance(exc, StudioFailure) else []
                gr.Warning(result, duration=15)
                return gr.update(selected="setup"), result, json.dumps({"fields":fields,"nonce":time.monotonic_ns()})
        enter_studio.click(enter_generation, api_scope, [views, setup_feedback, studio_errors])
        runtime_progress.change(fn=None, inputs=runtime_progress, js="""(text) => {
            const el=document.getElementById('runtime-progress');
            if(el && text.includes('任务已停止')) {
                el.setAttribute('role','alert');
                el.style.color='#dc2626';
                if(window.supervisorLastFailure !== text) { el.scrollIntoView({behavior:'smooth',block:'center'}); window.supervisorLastFailure=text; }
            } else if(el) { el.style.color=''; el.removeAttribute('role'); }
        }""")
        studio_stop.click(lambda t, l: localized_action(safe_action(stop, t), l), [task, language], message)
        studio_resume.click(continue_task, [task, language], [task,message])

        async def save_api_form(*args):
            try:
                preset, key, model, endpoint, vision, confirmed, strict, input_price, output_price, limit, scope, verified, upstreams, persist, lang = args
                model = (model or "").strip()
                vision = (vision or "").strip()
                endpoint = (endpoint or "").strip()
                validate_connection_form(service, key, model, endpoint, confirmed, verified, upstreams)
                invalid_prices = [name for name, value, positive in (("input_price", input_price, False), ("output_price", output_price, False), ("call_limit", limit, True)) if value is None or not math.isfinite(value) or value < 0 or (positive and value < .000001)]
                if invalid_prices:
                    raise ConnectionFailure("价格须非负，单次调用上限须至少为 0.000001 美元", invalid_prices)
                with service.active_lock:
                    if service.active:
                        raise ConnectionFailure("请先停止正在运行的任务，再修改 API 连接")
                key = await test_connection(service, key, model, endpoint, vision, strict, upstreams)
                text = save_provider(service, preset, key, model, endpoint, vision, confirmed, strict, input_price, output_price, limit, scope, verified, upstreams, persist)
                result = "连接成功：提示词与视觉模型测试通过，API 配置已保存"
                gr.Info(result, title="API 连接成功", duration=8)
                return text, "", result, "[]", gr.update(), result, gr.update(selected="setup")
            except Exception as exc:
                result = str(exc) if isinstance(exc, ConnectionFailure) else form_error(exc, args[-1])
                fields = exc.fields if isinstance(exc, ConnectionFailure) else []
                if type(exc).__name__ == "ValidationError":
                    fields = ["input_price", "output_price", "call_limit"]
                    result = "价格与调用上限无效：价格须非负，单次调用上限须大于零"
                result = "连接失败：" + result
                gr.Warning(result, title="API 连接失败", duration=10)
                return gr.update(), gr.update(), result, json.dumps({"fields": fields, "nonce": time.monotonic_ns()}), gr.update(open=True) if set(fields) & {"strict", "input_price", "output_price", "call_limit"} else gr.update(), result, gr.update(selected="setup")

        save_api.click(save_api_form, [api_preset, api_key, api_model, api_endpoint, api_vision, api_vision_confirmed,
            api_strict, api_input_price, api_output_price, api_call_limit, api_scope, api_scope_confirmed,
            api_upstreams, api_persist, language], [provider_text, api_key, message, api_errors, api_advanced, setup_feedback, views])
        api_errors.change(fn=None, inputs=api_errors, js="""(value) => {
            document.querySelectorAll('.api-invalid').forEach(el => {
                el.classList.remove('api-invalid');
                el.querySelectorAll('input,textarea').forEach(input => input.removeAttribute('aria-invalid'));
            });
            let fields = []; try { const parsed = JSON.parse(value || '[]'); fields = Array.isArray(parsed) ? parsed : parsed.fields || []; } catch {}
            fields.forEach(field => {
                const el = document.getElementById('api-' + field);
                if (!el) return;
                el.classList.add('api-invalid');
                el.querySelectorAll('input,textarea').forEach(input => input.setAttribute('aria-invalid', 'true'));
            });
            const first = document.getElementById('api-' + fields[0]);
            if (first) { first.scrollIntoView({behavior:'smooth', block:'center'}); first.querySelector('input,textarea')?.focus(); }
        }""")
        for field, control in (("key", api_key), ("endpoint", api_endpoint), ("model", api_model), ("vision", api_vision),
                ("vision_confirmed", api_vision_confirmed), ("scope_confirmed", api_scope_confirmed), ("upstreams", api_upstreams), ("input_price", api_input_price), ("output_price", api_output_price), ("call_limit", api_call_limit), ("strict", api_strict)):
            control.input(fn=None, inputs=control, js="(value) => { const el = document.getElementById('api-" + field + "'); el?.classList.remove('api-invalid'); el?.querySelectorAll('input,textarea').forEach(input => input.removeAttribute('aria-invalid')); }")
        api_preset.change(lambda p: gr.update(value=PRESETS[p]) if PRESETS[p] else gr.update(), api_preset, api_endpoint)
        api_endpoint.change(lambda endpoint: gr.update(visible="openrouter.ai" in (endpoint or "")), api_endpoint, api_upstreams)
        api_catalog.input(lambda value: value or gr.update(), api_catalog, api_model)

        async def sync_api_models(endpoint, key, model, vision, lang):
            try:
                ids = await discover_models(service, endpoint, key)
                result = translate("Models synced", lang)
                return gr.update(choices=ids), gr.update(choices=ids), result, result
            except Exception as exc:
                result = "模型同步失败：" + form_error(exc, lang)
                return gr.update(), gr.update(), result, result

        sync_inputs = [api_endpoint, api_key, api_model, api_vision, language]
        sync_outputs = [api_model, api_vision, message, model_sync_status]
        sync_api.click(sync_api_models, sync_inputs, sync_outputs)
        api_endpoint.change(sync_api_models, sync_inputs, sync_outputs)
        api_key.submit(sync_api_models, sync_inputs, sync_outputs)

        def import_workflow(upload, address, lang):
            rows = []
            try:
                if not upload:
                    raise ValueError("WORKFLOW_FILE_REQUIRED")
                rows = workflow_summary(json.loads(Path(upload).read_text(encoding="utf-8-sig")))
                config = save_workflow(service, upload, address)
                gr.Info("工作流已导入，可用于实际生成", duration=8)
                return config, workflow_summary_html(rows), "已导入工作流", "工作流已导入，可用于实际生成"
            except Exception as exc:
                result = ("节点已识别；自动运行绑定未导入：" if rows else "") + form_error(exc, lang)
                gr.Warning(result, duration=15)
                return gr.update(), workflow_summary_html(rows) if rows else gr.update(), result, result

        imported = save_comfy.click(import_workflow, [workflow_upload, comfy_url, language], [workflow_text, workflow_nodes, workflow_source, studio_feedback])
        async def identify_workflow(address, lang):
            try:
                config, rows, result = await detect_workflow(service, address)
                return config if config is not None else gr.update(), workflow_summary_html(rows), result, result
            except Exception as exc:
                result = "工作流连接或识别失败：" + form_error(exc, lang)
                if service.workflow:
                    result += "；下方保留已保存的工作流节点，非实时识别结果"
                return gr.update(), gr.update(), result, result
        def sync_workflow_params():
            values = workflow_defaults(service)
            return [gr.update(value=values[field]) if field in values else gr.update() for field in ("width", "height", "steps", "cfg", "seed", "sampler", "scheduler")*2]
        parameter_outputs = [studio_width, studio_height, studio_steps, studio_cfg, studio_seed, studio_sampler, studio_scheduler,direct_width,direct_height,direct_steps,direct_cfg,direct_seed,direct_sampler,direct_scheduler]
        image_import_outputs=[workflow_text,workflow_nodes,workflow_source,workflow_image_status,caption_status,direct_positive,direct_negative,*parameter_outputs]
        def import_workflow_image(upload,address,lang,fill_prompt=False):
            pending=[gr.update() for _ in image_import_outputs]
            pending[3]=pending[4]='正在读取图片中的 ComfyUI 工作流元数据…' if lang=='zh' else 'Reading ComfyUI workflow metadata…'
            yield tuple(pending)
            rows=[]
            try:
                graph=image_workflow(upload)
                rows=workflow_summary(graph)
                binding=infer_workflow(graph,'workflows/desktop-api.json',address)
                prompts=[]
                for field in ('positive','negative'):
                    target=binding.bindings[field][0]
                    prompts.append(resolve_workflow_text(graph,graph[target.node_id]['inputs'][target.input]))
                config=save_workflow_graph(service,graph,address)
                values=workflow_defaults(service)
                parameters=[gr.update(value=values[field]) if field in values else gr.update() for field in ('width','height','steps','cfg','seed','sampler','scheduler')*2]
                result=(f'已导入图片工作流（{len(graph)} 个节点）'+('，并填入正负提示词及生成参数。' if fill_prompt else '，生成参数已同步。')) if lang=='zh' else f'Imported image workflow ({len(graph)} nodes); '+('prompts and parameters filled.' if fill_prompt else 'parameters synced.')
                gr.Info(result,duration=8)
                yield (config,gr.update(value=workflow_summary_html(rows),visible=False),'已从图片元数据导入工作流',result,result,
                    prompts[0] if fill_prompt else gr.update(),prompts[1] if fill_prompt else gr.update(),*parameters)
            except Exception as exc:
                detail=str(exc) if isinstance(exc,ImageWorkflowError) else form_error(exc,lang)
                result=('图片节点已识别，自动运行绑定未导入：' if rows else '图片工作流识别失败：')+detail
                gr.Warning(result,duration=10)
                failed=[gr.update() for _ in image_import_outputs]
                if rows:
                    failed[1]=gr.update(value=workflow_summary_html(rows),visible=True)
                failed[3]=failed[4]=result
                yield tuple(failed)
        image_imported=workflow_image_upload.upload(import_workflow_image,[workflow_image_upload,comfy_url,language],image_import_outputs,show_progress='hidden',concurrency_id='image-workflow-import',concurrency_limit=1,trigger_mode='always_last')
        def import_prompt_workflow_image(upload,address,lang):
            yield from import_workflow_image(upload,address,lang,fill_prompt=True)
        prompt_image_imported=caption_workflow_input.upload(import_prompt_workflow_image,[caption_workflow_input,comfy_url,language],image_import_outputs,show_progress='hidden',concurrency_id='image-workflow-import',concurrency_limit=1,trigger_mode='always_last')
        for event in (image_imported,prompt_image_imported):
            loaded=event.then(read_editor,outputs=editor_read_outputs,show_progress='hidden').then(select_editor,[editor_field,editor_rows],editor_outputs,show_progress='hidden')
            loaded.then(show_lora_editor,outputs=editor_lora,show_progress='hidden')
        identified = detect_comfy.click(identify_workflow, [comfy_url, language], [workflow_text, workflow_nodes, workflow_source, setup_workflow_status])
        identified.then(sync_workflow_params, outputs=parameter_outputs)
        imported.then(sync_workflow_params, outputs=parameter_outputs)
        for event in (identified,imported,last_import_preview):
            loaded=event.then(read_editor,outputs=editor_read_outputs,show_progress='hidden').then(select_editor,[editor_field,editor_rows],editor_outputs,show_progress='hidden')
            loaded.then(show_lora_editor,outputs=editor_lora,show_progress='hidden')
        async def load_saved_workflow(address,lang):
            if service.workflow:
                path = Path(service.workflow.workflow_api_json)
                graph = json.loads((path if path.is_absolute() else service.project_root/path).read_text(encoding='utf-8-sig'))
                return service.workflow.model_dump_json(indent=2),workflow_summary_html(workflow_summary(graph)),"已保存的工作流（包括自定义参数）","已载入工作流"
            return await identify_workflow(address,lang)
        app.load(load_saved_workflow, [comfy_url, language], [workflow_text, workflow_nodes, workflow_source, setup_workflow_status],show_progress="hidden").then(sync_workflow_params, outputs=parameter_outputs,show_progress="hidden")
        edited.then(sync_workflow_params,outputs=parameter_outputs,show_progress="hidden")
        bulk_edited.then(sync_workflow_params,outputs=parameter_outputs,show_progress="hidden")
        load_prompts.click(service.prompt_preview, task, [card, plans])
        approve_prompts.click(lambda t, c, p, l: localized_action(safe_action(approve, t, c, p), l), [task, card, plans, language], message)
        resume_button.click(continue_task, [task, language], [task,message])
        stop_button.click(lambda t, l: localized_action(safe_action(stop, t), l), [task, language], message)
        accept_button.click(lambda t, l: localized_action(safe_action(accept_all, t), l), [task, language], message)
        keep.click(lambda t, a, l: localized_action(safe_action(service.candidate_action, t, a, True), l), [task, selected, language], message)
        reject.click(lambda t, a, l: localized_action(safe_action(service.candidate_action, t, a, False), l), [task, selected, language], message)
        reevaluate.click(lambda t, a, l: localized_action(safe_action(service.enqueue_review, t, a), l), [task, selected, language], message)
        restore.click(lambda a, l: localized_action(safe_action(service.files.restore, a), l), [quarantine, language], message)
        save.click(lambda p, w, l: localized_action(safe_action(save_config, p, w), l), [provider_text, workflow_text, language], message)

        async def update_models():
            return await service.cloud.refresh()

        refresh_models.click(update_models, outputs=catalog)
        load_events.click(lambda t: [[r["created_at"], r["kind"], r["body"]] for r in service.db.rows("SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT 50", (t,))], task, events)
        selected.change(lambda a: {"evaluations": [json.loads(r["body"]) for r in service.db.rows("SELECT body FROM evaluations WHERE asset_id=? ORDER BY created_at DESC", (a,))], "decisions": service.db.rows("SELECT action,reason,approved_by,created_at FROM decisions WHERE asset_id=? ORDER BY rowid DESC", (a,))} if a else {}, selected, score_details)
        # Accordion toggles are already local to the browser. Do not write an
        # open property back to the same accordion from its expand/collapse
        # event: delayed responses can override a newer click and re-open it.
        install_action_feedback(app)
    return app


def launch(service: Supervisor, port=7860):
    # Serve managed files in place instead of copying every image into ui-cache.
    gr.set_static_paths(paths=[service.files.path('tasks')])
    app = build_ui(service)
    return app.launch(server_name="127.0.0.1", server_port=port, share=False, show_error=False, run_history=False, footer_links=[], max_file_size="100mb", allowed_paths=[str(service.data_root)], blocked_paths=[str(service.data_root / "supervisor.db"), str(service.data_root / "supervisor.db-wal"), str(service.data_root / "supervisor.db-shm")], css=CSS + APPEARANCE_CSS + STUDIO_VIEW_CSS + NODE_EDITOR_CSS + FROSTED_CSS + RESULT_DELETE_CSS + RESULT_RETRY_CSS + GROUP_QUEUE_CSS + PROGRESS_CSS + RESULT_BATCH_CSS + STARTUP_CSS + ACTION_CSS + CHARACTER_ROWS_CSS + UPLOAD_CSS, theme=gr.themes.Default(primary_hue="emerald", secondary_hue="rose", neutral_hue="gray"))
