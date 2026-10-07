"""Browser appearance preferences and display-only translations."""

import gradio as gr


ZH = {
    'Creative discussion':'创作讨论', 'Discussion history':'讨论记录', 'New discussion':'新讨论',
    'Conversation':'对话', 'Discussion message':'讨论内容', 'Send message':'发送',
    'Please provide your base model information when starting the conversation.':'开始对话时，请提供底模信息。',
    'Available prompts':'可应用的提示词', 'Prompt preview':'提示词预览',
    'Apply and open prompt studio':'一键应用到提示词生图', 'Discussion budget (USD)':'本讨论预算（USD）',
    'INVALID_RESPONSE_DISCUSSION_REQUIRED':'云端模型没有返回有效讨论 JSON，请重试或检查模型。',
    'MANUAL_BINDINGS_REQUIRED':'已识别工作流，需要手动配置输入绑定。',
    'QUEUED':'等待队列', 'CAPTION':'图片反推',
    '导入上次配置':'导入上次配置', '结束本轮工作':'结束本轮工作', '执行剩余队列':'执行剩余队列',
    "Themes (one per line)": "分组主题（可选，每行一组）",
    "Theme groups":"主题组数（不同提示词）",
    "Prompt studio":"提示词生图", "Workflow parameters":"工作流参数",
    "Positive prompt *":"正向提示词 *", "Negative prompt":"负向提示词",
    "Number of images":"生成张数", "Enable quality review":"开启质量审查",
    "Off: save every generated image. On: send images to the configured vision model and save those passing review.":"关闭时每张生成图直接保存；开启时将图片送至已配置的视觉模型，按评审规则保存合格图。",
    "Generate from prompt":"开始提示词生图", "Generated images":"生成图片", "Review results":"审查结果",
    "Read workflow parameters":"读取工作流参数", "Node parameter":"节点参数", "Model or option":"模型或选项",
    "Custom models must be installed in ComfyUI and listed as available.":"自定义模型需先安装到 ComfyUI，并出现在本机可用列表中。",
    "Parameter value":"参数值", "Parameter switch":"参数开关", "Save workflow parameter":"保存节点参数",
    "Node":"节点", "Node type":"节点类型", "Parameter":"参数", "Current value":"当前值",
    "Shared requirements *": "共同要求 *", "Themes (one per line) *": "分组主题（每行独立一组） *", "Theme": "主题",
    "OUTPUT_FOLDER_UNWRITABLE": "无法写入输出文件夹，请重新选择可写入的目录",
    "NO_EXECUTED_WORKFLOW": "ComfyUI 尚无执行记录，请先执行一次工作流或拖入 API JSON",
    "INVALID_COMFYUI_ADDRESS": "ComfyUI 地址无效",
    "MODEL_CATALOG_INCOMPLETE": "服务商返回分页模型列表，当前接口未提供完整目录",
    "ConnectError": "连接失败，请检查服务地址及服务是否启动",
    "ConnectTimeout": "连接超时，请检查服务地址",
    "Image studio": "自动生图", "Advanced review": "高级评审", "Choose image folder": "选择图片文件夹",
    "Local folder path": "本机文件夹路径", "Reference sample limit": "文件夹参考图上限", "Creative direction": "创作方向",
    "Target styles (one per line)": "目标风格（每行一组）", "Prompt examples (optional)": "提示词写作范例（可选）",
    "Output folder": "输出文件夹", "Default: task delivery folder": "留空则存入任务交付文件夹",
    "Generation & limits": "生成参数与限额", "Start automatic generation": "开始自动生图",
    "Generated groups": "生成图组", "Saved folder": "保存位置", "Reference analysis": "参考图打标与分析",
    "Selected references": "已选参考图",
    "Image": "图片", "Subject": "主体", "Visual style": "图片风格", "Original prompt": "原提示词",
    "Writing style": "提示词写作风格", "Prompt evolution": "提示词演进", "Style": "风格", "Round": "轮次",
    "Positive prompt": "正向提示词", "Model reasoning": "模型设计理由", "Cloud review": "云端审查与评分",
    "Evidence": "模型依据", "Learned writing style": "学到的提示词写作风格",
    "Provider": "提供方", "Model name": "模型名称", "Advanced API settings": "高级 API 设置",
    "Current session; optional system credential storage": "默认仅当前运行会话；高级设置可选择系统凭据库",
    "API endpoint": "API 端点", "Synced models": "已同步模型", "Vision model (optional)": "视觉模型（留空使用同一模型）",
    "Image understanding verified": "已确认视觉模型支持图片理解", "Supports strict JSON schema": "支持严格 JSON Schema",
    "Save key in system credential store": "将密钥保存到系统凭据库", "Input USD / million tokens": "输入价格（美元 / 百万 token）",
    "Output USD / million tokens": "输出价格（美元 / 百万 token）", "Conservative USD / call": "单次调用保守费用上限（美元）",
    "Endpoint upload scope": "端点上传内容范围", "Endpoint upload scope verified": "已核验端点可接收所选内容范围",
    "OpenRouter upstream allowlist": "OpenRouter 上游列表", "Comma-separated provider IDs": "上游提供方 ID，以逗号分隔",
    "Save API connection": "保存 API 连接", "Sync models": "同步模型", "ComfyUI connection": "ComfyUI 连接",
    "ComfyUI address": "ComfyUI 地址", "ComfyUI API workflow": "ComfyUI API 格式工作流",
    "Import workflow": "导入工作流", "Configuration JSON": "高级配置 JSON", "Models synced": "模型已同步",
    "API connection not configured": "请先保存 API 连接", "REFERENCE_IMAGES_REQUIRED": "请拖入图片或选择参考图文件夹",
    "TARGET_STYLES_REQUIRED": "请填写至少一种目标风格", "ONE_STYLE_PER_GROUP_REQUIRED": "每组需要一种目标风格",
    "ABSOLUTE_OUTPUT_FOLDER_REQUIRED": "输出文件夹请填写绝对路径", "NO_VALID_REFERENCE_IMAGES": "未找到有效参考图片",
    "WORKFLOW_NOT_CONFIGURED": "请先导入 ComfyUI API 工作流", "VISION_ROUTE_NOT_CONFIGURED": "请配置支持图片理解的模型及端点上传范围",
    "MODEL_AND_CONFIRMED_VISION_CAPABILITY_REQUIRED": "请填写模型并确认视觉模型支持图片理解",
    "CONFIRM_ENDPOINT_UPLOAD_SCOPE": "请先核验并确认端点上传内容范围", "API_KEY_REQUIRED": "请输入 API Key",
    "HTTPS_ENDPOINT_REQUIRED": "API 端点需为不包含密钥的 HTTPS 地址", "PAUSE_TASKS_BEFORE_CONFIGURATION": "请先暂停运行中的任务，等待处理完成后再导入工作流。",
    "WORKFLOW_FILE_REQUIRED": "请选择 ComfyUI API 格式工作流文件", "API_FORMAT_WORKFLOW_REQUIRED": "需要 API 格式工作流，请从 ComfyUI 导出 API JSON",
    "COMPLEX_WORKFLOW_NEEDS_MANUAL_BINDINGS": "复杂工作流请在高级配置中指定节点绑定",
    "COMPLEX_CONDITIONING_NEEDS_MANUAL_BINDINGS": "复杂提示词节点请在高级配置中指定绑定",
    "LATENT_NEEDS_MANUAL_BINDINGS": "潜空间节点需手动绑定", "SAVEIMAGE_OUTPUT_REQUIRED": "工作流需包含 SaveImage 输出节点",
    "Activity": "操作状态", "Ready": "就绪", "Enable completion alerts": "开启完成通知",
    "Tasks": "任务", "Group goal": "分组目标", "Mode": "运行模式", "Demo": "演示", "Live": "实时",
    "Groups": "组数", "Images per group": "每组图片", "Budget (USD)": "预算（美元）",
    "Max rounds / group": "每组最大轮数", "Quality threshold": "质量阈值",
    "Minimum quality score": "综合合格分数",
    "42 or below: basic pass, no obvious character deformation. Above 42: stricter overall quality.": "综合分达到设置值才保存，并检查明显畸形、重复和安全；模型置信度不会额外抬高合格门槛。拖动到 41–43 分轻吸附至 42，直接输入数字不吸附。",
    "View generated images →": "查看生成结果 →",
    "← Back to settings": "← 返回设置",
    "Apply to current task": "应用到当前任务",
    "Maximum attempts per group; qualifying images, stalled scores or other limits can end the task earlier.": "每组最多尝试多少轮；合格图足够、评分停滞或其他限额会提前结束。",
    "Minimum overall score for automatic saving; key criteria and reviewer confidence must also pass.": "达到此综合分数才可自动保存，并检查明显畸形、重复和安全。",
    "User-confirmed content scope": "用户确认的内容范围", "Reference folder": "参考图文件夹",
    "Absolute folder path": "文件夹绝对路径", "Reference images": "参考图片",
    "Generation parameters": "生成参数", "Width": "宽度", "Height": "高度", "Steps": "步数",
    "Base seed": "基础种子", "Approve bounded additive iterations": "允许限额内追加迭代",
    "Automatically accept qualified candidates": "自动接受合格候选图", "Cleanup policy": "清理策略",
    "Delete mode": "删除方式", "Retention days": "保留天数", "Delete confidence threshold": "删除置信度阈值",
    "Reviewer calibrated": "评审模型已校准", "Allow permanent deletion": "允许永久删除",
    "Task limits": "任务限制", "Max generation attempts": "最大生成次数", "Wall-clock limit (hours)": "运行时限（小时）",
    "Create task": "创建任务", "Task": "当前任务", "Refresh tasks": "刷新任务", "No task selected": "尚未选择任务",
    "Progress & budget": "进度与预算", "Task details": "任务详情", "Resume": "恢复当前任务",
    "Stop after current output": "当前输出后停止", "Accept qualified candidates": "接受合格候选图",
    "Task images": "任务图片", "Review queue": "评审队列", "Review asset": "待评审图片",
    "Accept": "接受", "Reject": "拒绝", "Re-review": "重新评审", "Evaluation & audit": "评分与审计",
    "Prompt approval": "提示词审批", "Style card": "风格卡", "Draft prompts": "提示词草稿",
    "Load drafts": "读取草稿", "Approve style & prompts": "批准风格与提示词",
    "Quarantine & delivery": "隔离与交付", "Recoverable image": "可恢复图片", "Restore original": "恢复原图",
    "Delivery manifest": "交付清单", "Contact sheet": "图片总览", "Activity log": "活动日志",
    "Refresh activity log": "刷新活动日志", "Providers & workflow": "服务商与工作流",
    "Providers configuration": "服务商配置", "Workflow bindings": "工作流绑定", "Save configuration": "保存配置",
    "Refresh available models": "刷新可用模型", "Model catalog": "模型目录",
    "Asset": "图片", "Group": "分组", "File state": "文件状态", "Decision": "决策", "Score": "评分",
    "Time (UTC)": "时间（UTC）", "Event": "事件", "Details": "详情", "Saved": "已保存",
    "Light": "亮色", "Dark": "深色", "Custom": "自定义", "Appearance": "外观",
    "Accent": "强调色", "Mist": "雾色", "Drizzle": "烟雨", "Reset appearance": "重置外观",
    "unknown": "未确认", "sfw": "普通内容", "adult_allowed": "允许成人内容",
    "quarantine": "移入隔离区", "delayed": "延迟删除", "direct": "直接删除",
    "AVAILABLE": "可用", "QUARANTINED": "已隔离", "ACCEPTED": "已接受",
    "REVIEW_FAILED": "评审失败", "REVIEW": "待处理", "RECHECK": "待自动复核", "CANDIDATE": "待确认",
    "COMPLETED": "已完成", "PARTIAL": "部分完成", "CANCELLED": "已取消",
    "WAITING_APPROVAL": "等待审批", "FINAL_REVIEW": "最终评审", "RUNNING": "运行中",
    "PAUSED": "已暂停", "FAILED": "失败", "DEMO": "演示", "LIVE": "实时",
    "QUARANTINE": "隔离", "DELIVER": "交付", "GENERATE": "生成", "EVALUATE": "评审",
    "PROMPT": "提示词", "STOPPING": "停止中", "pending": "待处理", "generated": "生成图",
}


def translate(text, language):
    return ZH.get(text, text) if language == "zh" else text


def localized_action(text, language):
    if language != "zh":
        if text == "就绪":
            return "Ready"
        if text == "已保存":
            return "Saved"
        if text.startswith("操作未通过："):
            return text.replace("操作未通过：", "Action rejected: ", 1)
        if text.startswith("任务未通过："):
            return text.replace("任务未通过：", "Task rejected: ", 1)
        if text.startswith("任务 ") and text.endswith(" 已创建"):
            return "Task " + text[3:-4] + " created"
        return text
    if text.startswith("Action rejected: "):
        return text.replace("Action rejected: ", "操作未通过：", 1)
    if text.startswith("Task rejected: "):
        return text.replace("Task rejected: ", "任务未通过：", 1)
    if text.startswith("Task ") and text.endswith(" created"):
        return "任务 " + text[5:-8] + " 已创建"
    return translate(text, language)


def translation_updates(registry, language):
    updates = []
    for component, original in registry:
        props = {}
        for key, value in original.items():
            if key == "choices":
                props[key] = [(translate(v, language), v) for v in value]
            elif key == "headers":
                props[key] = [translate(v, language) for v in value]
            else:
                props[key] = translate(value, language)
        updates.append(gr.update(**props))
    return updates


APPEARANCE_CSS = """
body { background: #f8faf9; }
.gradio-container { --page-accent: #287b62; --page-mist: #e4f2e9; }
.app-header { align-items: center !important; border-bottom: 1px solid var(--border-color-primary); }
.app-title { border: 0 !important; }
.preferences { align-self: flex-start; }
.preferences .wrap { gap: 4px !important; }
.preferences label { font-size: 12px !important; }
.block.status-band { border-left-color: var(--page-accent) !important; background: var(--background-fill-secondary) !important; }
body.dark { background: #171a19; color-scheme: dark; }
.dark .gradio-container { --background-fill-primary: #191e1b; --background-fill-secondary: #242b27;
 --block-background-fill: #242b27; --input-background-fill: #1c231f; --border-color-primary: #414b45;
 --body-text-color: #eef4f0; --body-text-color-subdued: #b9c7be; }
body[data-appearance="dark"] .gradio-container button.secondary, body[data-appearance="dark"] .preferences .wrap label {
 background: #303a35 !important; color: #eef4f0 !important; border-color: #526158 !important; }
.dark .app-title p { color: #b9c7be; }
body[data-appearance="custom"] { background-attachment: fixed !important; }
body[data-appearance="custom"] gradio-app { background: transparent !important; }
body[data-appearance="custom"] .gradio-container { background: transparent !important;
 --color-accent: var(--page-accent); --color-accent-soft: var(--page-mist);
 --button-primary-background-fill: var(--page-accent); --button-primary-background-fill-hover: var(--page-accent);
 --button-primary-text-color: var(--page-on-accent, #fff); --checkbox-background-color-selected: var(--page-accent);
 --checkbox-border-color-selected: var(--page-accent); --slider-color: var(--page-accent); }
body[data-appearance="custom"][data-drizzle="true"]::before {
 content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0; opacity: .12;
 background: repeating-linear-gradient(108deg, transparent 0 109px, #759c8d 110px, transparent 111px 210px);
 mask-image: linear-gradient(#000, transparent 40%); animation: drizzle 18s linear infinite;
}
.gradio-container { position: relative; z-index: 1; }
body[data-reduced-motion="true"]::before { animation: none !important; }
@keyframes drizzle { from { background-position: 0 -100px; } to { background-position: -80px 100px; } }
@media(prefers-reduced-motion: reduce) { body::before { animation: none !important; } }
@media(max-width:700px) { .app-title h1 { font-size:22px; } .preferences { width:100%; } }
"""

# All preferences are local to the browser; task content never enters localStorage.
APPEARANCE_JS = """() => {
 window.supervisorAppearance = (mode, accent, mist, rain, blur, opacity) => {
   const safeColor = (value, fallback) => /^#[0-9a-f]{6}$/i.test(value || '') ? value : fallback;
   const bounded = (value, fallback, min, max) => value != null && Number.isFinite(Number(value)) ? Math.max(min,Math.min(max,Number(value))) : fallback;
   const settings = {mode: ['light','dark','custom'].includes(mode) ? mode : 'light',
     accent: safeColor(accent, '#287b62'), mist: safeColor(mist, '#e4f2e9'), rain: !!rain,
     blur:bounded(blur,20,0,32), opacity:bounded(opacity,66,45,95)};
   document.body.classList.toggle('dark', settings.mode === 'dark');
   document.documentElement.classList.toggle('dark', settings.mode === 'dark');
   document.body.dataset.appearance = settings.mode;
   document.body.dataset.drizzle = String(settings.rain);
   document.body.style.setProperty('--page-mist', settings.mist);
   const dark = settings.mode === 'dark';
   const blend = (color,base,amount) => color.slice(1).match(/../g).map((c,i)=>Math.round(parseInt(c,16)*(1-amount)+parseInt(base.slice(1).match(/../g)[i],16)*amount));
   const darkPanel = blend(settings.accent,'#1c2025',.86).join(',');
   const darkField = blend(settings.accent,'#101318',.92).join(',');
   const darkHalo = blend(settings.accent,'#17191e',.52).join(',');
   const secondHalo = blend(settings.mist,'#17191e',.82).join(',');
   document.body.style.setProperty('--glass-popup-rgb',dark ? darkPanel : '255,255,255');
   const alpha = Math.min(.95,settings.opacity/100 + (dark ? .1 : 0));
   document.body.style.setProperty('--glass-blur', `${settings.blur}px`);
   document.body.style.setProperty('--glass-panel', `rgba(${dark ? darkPanel : '255,255,255'},${alpha})`);
   document.body.style.setProperty('--glass-field', `rgba(${dark ? darkField : '255,255,255'},${Math.min(.96,alpha+.12)})`);
   document.body.style.setProperty('background-image', settings.mode === 'dark' ? `radial-gradient(ellipse at 8% 4%, rgb(${darkHalo}) 0%, transparent 48%), radial-gradient(ellipse at 95% 42%, rgb(${secondHalo}) 0%, transparent 46%)` : `radial-gradient(ellipse at 8% 4%, ${settings.mode === 'custom' ? settings.mist : '#dcece3'} 0%, transparent 52%), radial-gradient(ellipse at 95% 42%, #e6e9f2 0%, transparent 48%), linear-gradient(145deg, #f6f9f7, #f4f5fa)`, 'important');
   document.body.style.setProperty('background-attachment', 'fixed');
   document.body.style.setProperty('background-color', settings.mode === 'dark' ? '#171a19' : '#f8faf9', 'important');
   const container = document.querySelector('.gradio-container');
   if (container) {
     container.style.setProperty('--page-accent', settings.accent); container.style.setProperty('--page-mist', settings.mist);
     for (const name of ['--color-accent','--slider-color','--checkbox-background-color-selected','--checkbox-border-color-selected']) container.style.setProperty(name,settings.accent);
     for (const name of ['--background-fill-primary','--background-fill-secondary']) {
       if(dark) container.style.setProperty(name,`rgb(${darkPanel})`);
       else container.style.removeProperty(name);
     }
     const rgb = settings.accent.slice(1).match(/../g).map(x => parseInt(x, 16) / 255).map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4);
     const luminance = .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2];
     container.style.setProperty('--page-tab-accent', dark ? `rgb(${blend(settings.accent,'#ffffff',.55).join(',')})` : settings.accent);
     container.style.setProperty('--page-on-accent', luminance > .179 ? '#14231a' : '#fff');
   }
   const palettes = {mist:['#287b62','#e4f2e9'],blue:['#386caa','#e1edf9'],sand:['#95633a','#f3e8d8']};
   settings.preset = Object.entries(palettes).find(([name,colors]) => colors[0]===settings.accent.toLowerCase() && colors[1]===settings.mist.toLowerCase())?.[0] ?? null;
   try { localStorage.setItem('supervisor.appearance', JSON.stringify(settings)); } catch {}
   return settings;
 };
 let saved = {};
 try { saved = JSON.parse(localStorage.getItem('supervisor.appearance') || '{}') || {}; } catch {}
 const settings = window.supervisorAppearance(saved.mode, saved.accent, saved.mist, saved.rain ?? false, saved.blur, saved.opacity);
 let language = 'zh';
 try { language = localStorage.getItem('supervisor.language') === 'en' ? 'en' : 'zh'; } catch {}
 window.supervisorLanguage = language;
 if (!window.supervisorMotionListener) {
   const media = window.matchMedia('(prefers-reduced-motion: reduce)');
   window.supervisorMotionListener = () => { document.body.dataset.reducedMotion = String(media.matches); };
   media.addEventListener('change', window.supervisorMotionListener);
 }
 window.supervisorMotionListener();
 return [language, settings.mode, settings.accent, settings.mist, settings.rain,settings.blur,settings.opacity,settings.preset];
}"""
