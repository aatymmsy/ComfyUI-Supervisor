"""Readiness checks and concise task progress for the live generation studio."""

import json
import asyncio
from pathlib import Path

import httpx

from .models import Params, PromptPlan
from .comfy import Comfy
from .setup import ConnectionFailure
from .delivery import delivered_image_count
from .wire import FORMAT_REASONS


class StudioFailure(ConnectionFailure):
    def __init__(self, message, fields=(), tab="studio"):
        super().__init__(message, fields)
        self.tab = tab


async def check_live_ready(service, scope, params, purposes=None, *, workflow_config=None, on_progress=None):
    report=on_progress or (lambda text:None)
    report('正在检查模型服务配置')
    workflow = workflow_config or service.workflow
    purposes = (("tagging", "参考图打标", 1), ("review", "质量评审", 1), ("prompt_generation", "提示词生成", 0)) if purposes is None else purposes
    if purposes and not service.config.providers:
        raise StudioFailure("尚未配置云端模型。请填写并测试 API 连接。", ["api-endpoint", "api-key", "api-model"], "setup")
    checked_keys={}
    for purpose, name, images in purposes:
        routes = service.cloud.candidates(purpose, scope, images, "USD")
        if not routes:
            raise StudioFailure(name + "没有可用模型，请检查视觉模型能力及上传内容范围。", ["api-vision", "api-scope"], "setup")
        has_key=False
        for _,_,credential in routes:
            if id(credential) not in checked_keys:
                checked_keys[id(credential)]=await asyncio.to_thread(_has_key,service,credential)
            if checked_keys[id(credential)]:
                has_key=True
                break
        if not has_key:
            raise StudioFailure("当前会话缺少 API Key，或系统凭据库无法读取密钥。请重新保存 API 连接。", ["api-key"], "setup")
    if not workflow:
        raise StudioFailure("没有可执行工作流。请识别 ComfyUI 工作流或导入 API JSON。", ["setup-comfy"], "setup")
    try:
        report('正在检查工作流与生成参数')
        preflight_comfy=Comfy(workflow,service.project_root,service.db,transport=service.comfy.transport)
        graph = await asyncio.to_thread(preflight_comfy.graph,PromptPlan(group_id="preflight", positive="connection validation", negative="",
            params=Params.model_validate(params), reason="Preflight only; not submitted", changes=[]), "preflight")
    except Exception as exc:
        raise StudioFailure("工作流绑定不可用（" + type(exc).__name__ + "），请重新识别工作流。", ["setup-comfy"], "setup") from None
    try:
        report('正在连接 ComfyUI，读取可用节点与模型')
        async with httpx.AsyncClient(timeout=10, transport=service.comfy.transport, trust_env=False) as client:
            response = await client.get(workflow.base_url.rstrip("/") + "/object_info")
            response.raise_for_status()
            definitions = response.json()
    except (httpx.HTTPError, ValueError):
        raise StudioFailure("无法连接 ComfyUI，请检查地址以及服务是否已启动。", ["setup-comfy"], "setup") from None
    report('正在核对工作流中的模型和节点')
    for node_id, node in graph.items():
        definition = definitions.get(node["class_type"])
        if not definition:
            raise StudioFailure(f"ComfyUI 缺少节点：{node['class_type']}（{node_id}）。", ["setup-comfy"], "setup")
        declared = {**definition.get("input", {}).get("required", {}), **definition.get("input", {}).get("optional", {})}
        for name, value in node.get("inputs", {}).items():
            options = declared.get(name)
            if name in ("unet_name", "ckpt_name", "lora_name", "vae_name", "model_name", "sampler_name", "scheduler") and isinstance(options, list) and options and isinstance(options[0], list) and not isinstance(value, list) and value not in options[0]:
                raise StudioFailure(f"节点 {node_id} 的 {name} 不可用：{value}。请在 ComfyUI 中检查模型或参数。", ["setup-comfy"], "setup")


def _has_key(service, credential):
    try:
        return bool(service.cloud.credential_key(credential))
    except Exception:
        return False


def _task_progress(service, task_id):
    task = service.db.one("SELECT state,phase,reason FROM tasks WHERE id=?", (task_id,)) if task_id else None
    if not task:
        return "等待开始"
    labels = {"QUEUED":"等待前面的任务完成", "CAPTION":"ComfyUI 图片反推", "IMAGE_PROMPT":"视觉模型图片反推", "ANALYZE": "云端打标与参考图分析", "SUBMIT": "向 ComfyUI 提交工作流", "GENERATE": "ComfyUI 生图中",
              "EVALUATE": "云端质量评审", "DELIVER": "保存图组", "THEMES": "模型随机补选主题", "PROMPT": "生成或迭代提示词"}
    total = service.db.one("SELECT COUNT(*) n FROM assets WHERE task_id=? AND source_kind='reference'", (task_id,))["n"]
    tagged = sum(bool(json.loads(row["metadata"]).get("model_tags")) for row in service.db.rows("SELECT metadata FROM assets WHERE task_id=? AND source_kind='reference'", (task_id,)))
    settings = service.settings(task_id)
    prefix = f"参考图已打标 {tagged}/{total} · token {service.db.token_usage(task_id):,}/{settings.token_budget:,} · "
    if settings.direct_prompt is not None:
        prefix = ("提示词生图 · 开启质量审查" if settings.review_enabled else "提示词生图 · 不审查，直接保存") + f" · token {service.db.token_usage(task_id):,}/{settings.token_budget:,} · "
    saved = 0
    if not settings.reference_only:
        generated = service.db.one("SELECT COUNT(*) n FROM assets WHERE task_id=? AND source_kind='generated'", (task_id,))["n"]
        pending = service.db.one("SELECT COUNT(*) n FROM assets a LEFT JOIN decisions d ON d.asset_id=a.id AND d.rowid=(SELECT MAX(rowid) FROM decisions WHERE asset_id=a.id) WHERE a.task_id=? AND a.group_id IN (SELECT id FROM groups WHERE state<>'CANCELLED') AND a.source_kind='generated' AND a.state='AVAILABLE' AND (d.action IS NULL OR d.action IN ('REVIEW','RECHECK','CANDIDATE'))", (task_id,))["n"]
        saved = delivered_image_count(service,task_id)
        target = service.db.one("SELECT COUNT(*) n FROM groups WHERE task_id=? AND state<>'CANCELLED'", (task_id,))['n'] * settings.per_group
        saved_text = f'{saved} · 有效目标 {target}' if target < settings.groups * settings.per_group else f'{saved}/{target}'
        prefix += f"已生成 {generated} · 已保存 {saved_text} · 待处理 {pending} · "
    if task["state"] in ("PAUSED", "FAILED", "BLOCKED_POLICY", "WAITING_APPROVAL"):
        if task['reason'] == 'ROUND_ENDED':
            return '**本轮已结束，进度已保留** · ' + prefix + '点击「执行剩余队列」按顺序继续。'
        if task["reason"] == "USER_LIMITS_UPDATED":
            return "**任务设置已更新** · " + prefix + f"每组最多 {settings.max_rounds} 轮 · 综合分数门槛 {settings.quality_threshold:g} · 点击「恢复当前任务」沿用已有进度"
        if task["reason"] == "REVIEW_RECOVERED_READY_TO_RESUME":
            return "**已有图片评审已恢复** · " + prefix + "点击「恢复当前任务」接着迭代，参考图打标和已有图片评审无需重做"
        reasons = {"NO_VERIFIED_ROUTE_WITH_REQUIRED_CAPABILITY_AND_PRICING": "没有满足视觉能力和上传范围的云端模型",
            "MISSING_API_KEY": "API Key 缺失，请重新连接", "SERVICE_REFUSAL": "服务商拒绝了本次请求",
            "INVALID_RESPONSE_REVIEW_REQUIRED": "云端评审 JSON 回复格式未通过校验", "TRANSPORT_ERROR_COST_UNCERTAIN": "云端请求网络失败",
            "INVALID_RESPONSE_PROMPT_REQUIRED": "云端提示词 JSON 回复格式未通过校验",
            "RESULT_RETRY_PROMPT_UNCHANGED": "模型未修改提示词，重画已暂停；请检查模型输出后再试。",
            "INVALID_RESPONSE_TAGGING_REQUIRED": "云端参考图标签 JSON 回复格式未通过校验",
            "INCOMPLETE_RESPONSE": "云端回复被截断，未获得完整 JSON；这与账户余额无关。请重试或更换能稳定返回简短 JSON 的模型",
            "BUDGET_EXCEEDED": "达到本任务预算上限（与服务商账户余额不同），请检查任务预算",
            "HTTP_401": "API 密钥无效或过期，请重新保存连接", "HTTP_402": "服务商拒绝计费，请检查当前模型或上游的额度",
            "HTTP_429": "服务商限流或当前模型额度不足，请稍后重试"}
        reasons["ITERATION_CHANGE_SCOPE_INVALID"] = "提示词迭代的修改字段不符合要求，修正重试后仍未通过；请重试或更换提示词模型"
        reasons["TOKEN_BUDGET_LIMIT"] = "已达到任务 token 上限，停止继续调用"
        reasons["OUTPUT_FOLDER_UNWRITABLE"] = "无法写入指定输出文件夹，请检查文件夹权限或重新选择"
        reasons["OUTPUT_FILE_CHANGED"] = "输出目录有同名且内容不同的文件，已停止写入以免覆盖"
        reason = task["reason"] or "未知原因"
        reason = reasons.get(reason, reason)
        latest = service.db.one("SELECT id,purpose,status FROM api_calls WHERE task_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (task_id,))
        # Older versions used the review code for every kind of cloud reply.
        if task["reason"] == "INVALID_RESPONSE_REVIEW_REQUIRED" and latest and latest["status"] == "INVALID_RESPONSE":
            reason = {"prompt_generation": reasons["INVALID_RESPONSE_PROMPT_REQUIRED"],
                      "tagging": reasons["INVALID_RESPONSE_TAGGING_REQUIRED"]}.get(latest["purpose"], reason)
        step = {"tagging":"云端参考图打标","prompt_generation":"云端提示词生成","review":"云端质量评审"}.get(latest["purpose"], "") if latest and latest["status"] != "SUCCESS" else labels.get(task["phase"], "工作流执行")
        if task["reason"] == "ITERATION_CHANGE_SCOPE_INVALID":
            step = "提示词迭代"
        if latest and latest["status"] == "INVALID_RESPONSE":
            failed = service.db.one("SELECT body FROM events WHERE task_id=? AND kind='CLOUD_REPLY_VALIDATION_FAILED' ORDER BY id DESC LIMIT 1", (task_id,))
            if failed:
                details = json.loads(failed["body"])
                if details.get("call_id") == latest.get("id"):
                    fields = [".".join(map(str,error["loc"])) for error in details.get("errors",[]) if error.get("type") == "missing" and error.get("loc")]
                    if fields:
                        reason += "（缺少字段：" + "、".join(fields[:3]) + "）"
                    unexpected = [".".join(map(str,error['loc'])) for error in details.get('errors',[]) if error.get('type') == 'extra_forbidden' and error.get('loc')]
                    if unexpected:
                        reason += '（回复含不支持的字段：' + '、'.join(unexpected[:3]) + '）'
                    empty = [".".join(map(str,error['loc'])) for error in details.get('errors',[]) if error.get('type') == 'string_too_short' and error.get('loc')]
                    if empty:
                        reason += '（必需说明为空：' + '、'.join(empty[:3]) + '）'
                    invalid_scores = ['.'.join(map(str,error['loc'])) for error in details.get('errors',[])
                        if error.get('type') in ('float_parsing','float_type','finite_number','greater_than_equal','less_than_equal','review_score_type') and error.get('loc')]
                    if invalid_scores:
                        reason += '（评分须为 0–100 数字或 null：' + '、'.join(invalid_scores[:3]) + '）'
                    if any(error.get('type') == 'json_invalid' for error in details.get('errors',[])):
                        reason += '（回复不是完整、可解析的 JSON）'
                    oversized = [".".join(map(str,error["loc"])) for error in details.get("errors",[]) if error.get("type") in ("too_long","string_too_long") and error.get("loc")]
                    if oversized:
                        labels = [f'第 {int(field.split(".")[1])+1} 条问题说明' if field.startswith('problems.') and field.endswith('.evidence') and field.split('.')[1].isdigit() else field for field in oversized[:3]]
                        reason += "（数量或长度超限：" + "、".join(labels) + "）"
                    explanations = list(dict.fromkeys(FORMAT_REASONS[error['type']] for error in details.get('errors',[])
                        if error.get('type') in FORMAT_REASONS))
                    if explanations:
                        reason += '（' + '；'.join(explanations[:3]) + '）'
                    elif any(error.get('type') == 'value_error' and not error.get('loc') for error in details.get('errors',[])):
                        reason += '（回复字段未满足校验约束，该次记录未保存更具体的原因）'
        return "**任务已停止或等待处理** · " + prefix + step + "失败：" + reason
    if task["state"] in ("COMPLETED", "PARTIAL", "CANCELLED"):
        if task["reason"] == "TOKEN_BUDGET_LIMIT":
            return prefix + "达到任务 token 上限，已停止新 API 请求并保存已有合格图"
        result = {"COMPLETED": "任务完成", "PARTIAL": "未达到目标交付数量", "CANCELLED": "任务已停止"}[task["state"]]
        if task["state"] == "PARTIAL":
            stop_reasons = {"BUDGET_EXHAUSTED":"达到任务费用预算上限",
                            "GENERATION_LIMIT":f"达到最大生成次数（{settings.max_generations} 次）",
                            "WALL_CLOCK_LIMIT":"达到任务运行时长上限",
                            "DELIVERY_SHORTFALL":"交付数量不足"}
            if task["reason"] == "ROUND_OR_PATIENCE_LIMIT":
                groups = service.db.rows("SELECT id,round_index,stale_rounds FROM groups WHERE task_id=?", (task_id,))
                unfinished = [g for g in groups if len(service.db.accepted(task_id,g["id"])) < settings.per_group]
                limits = []
                if any(g["round_index"] >= settings.max_rounds for g in unfinished):
                    limits.append(f"达到每组最大轮数（{settings.max_rounds} 轮）")
                if any(g["stale_rounds"] >= settings.patience and g["round_index"] < settings.max_rounds for g in unfinished):
                    limits.append(f"连续 {settings.patience} 轮评分无明显提升")
                result = "；".join(limits) or "达到轮数或评分停滞限制"
            else:
                result = stop_reasons.get(task["reason"],result)
        if not settings.reference_only and saved == 0:
            best = service.db.one("SELECT MAX(e.effective_score) score FROM evaluations e JOIN assets a ON a.id=e.asset_id WHERE a.task_id=? AND a.source_kind='generated' AND e.stage='final' AND e.rowid=(SELECT MAX(rowid) FROM evaluations WHERE asset_id=a.id AND stage='final')", (task_id,))
            if best and best["score"] is not None:
                result += f"；已评审图最高综合评分 {best['score']:.2f}，保存门槛 {settings.quality_threshold:g}"
            result += "；尚无合格交付图，指定目录暂无图片"
        return prefix + result
    return prefix + labels.get(task["phase"], task["phase"])


def task_progress(service, task_id, *, include_groups=True):
    summary = _task_progress(service,task_id)
    if not task_id or not service.db.one('SELECT id FROM tasks WHERE id=?',(task_id,)):
        return summary
    settings=service.settings(task_id)
    if settings.direct_prompt is None and service.db.one("SELECT rowid FROM events WHERE task_id=? AND kind='TASK_RESTARTED' LIMIT 1",(task_id,)):
        summary+=' · 沿用上轮参考图分析与提示词'
    budget=service.db.budget(task_id)
    available=max(0,settings.budget_micro-sum(budget.values()))/1_000_000
    summary+=f"\n\n费用 {budget['spent']/1_000_000:.4f} / {settings.budget_micro/1_000_000:g} {settings.currency} · 可用 {available:.4f} · 占用/待结算 {(budget['reserved']+budget['uncertain'])/1_000_000:.4f}"
    if settings.autonomous and settings.direct_prompt is None:
        from .round_feedback import round_feedback
        shared=round_feedback(service,task_id)['directions']
        names={'face':'五官清晰','hands':'手部结构','geometry':'结构比例','clarity':'细节清晰','artifacts':'融合与伪影','composition':'构图遮挡','alignment':'控制词对应','style':'风格一致','text':'文字可读'}
        if shared:
            summary+='\n\n本轮共享优化：'+'、'.join(names[item['focus']] for item in shared)+'；后续各组规划与迭代时应用。'
    if not settings.reference_only and include_groups:
        import html
        lines=[]
        remaining=0
        for group in service.db.rows('SELECT * FROM groups WHERE task_id=? ORDER BY ordinal',(task_id,)):
            if group['state']=='CANCELLED':
                continue
            count=len(service.db.accepted(task_id,group['id']))
            shortfall=max(0,settings.per_group-count)
            remaining+=shortfall
            theme=settings.target_styles[group['ordinal']] if group['ordinal'] < len(settings.target_styles) else ''
            name=html.escape(theme[:60])
            label='合格' if settings.review_enabled else '已保存（未审查）'
            lines.append(f"第 {group['ordinal']+1} 组 {name}：{label} {count}/{settings.per_group} · 剩余 {shortfall} · 轮次 {group['round_index']}/{settings.max_rounds}")
        summary+='\n\n<details data-persist-key="group-progress-'+task_id+'"><summary>各组进度 · 剩余目标 '+str(remaining)+' 张</summary><p>'+'<br>'.join(lines)+'</p></details>'
    task=service.db.one('SELECT state,reason FROM tasks WHERE id=?',(task_id,))
    if task['state'] in ('COMPLETED','PARTIAL') and not settings.reference_only:
        summary+='\n\n点击“再生成一轮”可重新开始一轮，沿用本轮图量、提示词和生图配置。'
    if task['state'] in ('PAUSED','FAILED','BLOCKED_POLICY','WAITING_APPROVAL'):
        reason=task['reason'] or ''
        if reason == 'ROUND_ENDED':
            action='点击执行剩余队列，按原顺序接着处理；已有图片、打标和提示词均保留。'
        elif reason in ('USER_LIMITS_UPDATED','REVIEW_RECOVERED_READY_TO_RESUME'):
            action='设置已更新或审查已恢复；确认当前参数后点击恢复当前任务。'
        elif any(term in reason for term in ('API_KEY','HTTP_401','NO_VERIFIED_ROUTE')):
            action='回到必填配置，检查 API 连接和模型，验证后恢复当前任务。'
        elif reason.startswith('INVALID_RESPONSE') or reason == 'INCOMPLETE_RESPONSE':
            action='云端回复格式未通过校验，已有图片和进度已保留；点击恢复当前任务重试该步骤。若反复出现，可更换对应模型。'
        elif 'TRANSPORT' in reason:
            action='云端连接失败，请检查服务地址和网络后恢复当前任务。'
        elif any(term in reason for term in ('BUDGET','TOKEN_BUDGET')):
            action='本任务已达到预算或 token 限额；保存当前结果，并调整新任务的限额。'
        elif 'OUTPUT' in reason:
            action='检查输出目录是否可写及同名文件情况，修正后恢复当前任务。'
        elif task['state']=='WAITING_APPROVAL':
            action=('旧任务等待状态已保留；点击恢复当前任务后由程序自动复核和判定，无需逐张人工确认。'
                if settings.autonomous else '到人工干预页检查待确认图片或提示词，确认后恢复当前任务。')
        elif task['state']=='BLOCKED_POLICY':
            action='检查内容范围和服务商允许的内容，修正配置后再继续。'
        else:
            action='按上方具体停止原因处理后，点击恢复当前任务沿用已有进度。'
        if task['state']=='FAILED':
            action='任务已失败，请按上方原因修正配置后重新开始；可先保存常用方案复用设置。'
        summary+='\n\n下一步：'+action
    return summary
