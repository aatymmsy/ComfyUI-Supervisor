"""Local studio presets and displays tied to the actual generation records."""

import html
import json
import threading
from pathlib import Path

from pydantic import Field, model_validator

from .control_words import parse_control_words, enforce_control_words, prompt_parts, character_for_group
from .files import atomic_write
from .models import PASS_SCORE, Contract, Params, PromptPlan, TaskSettings, CharacterControl


class StudioPreset(Contract):
    sample_count: int = Field(default=15,ge=1)
    goal: str = Field(min_length=1,max_length=3000)
    lora_trigger_words: str = Field(default="",max_length=2000)
    character: str = Field(default="",max_length=600)
    character_controls: list[CharacterControl] = Field(default_factory=list,max_length=30)
    themes: str = Field(default='',max_length=12000)
    examples: str = Field(default='',max_length=12000)
    groups: int = Field(ge=1,le=20)
    per_group: int = Field(default=1,ge=1,le=50)
    params: Params = Field(default_factory=Params)
    quality: float = Field(default=PASS_SCORE,ge=0,le=100,allow_inf_nan=False)
    rounds: int = Field(default=6,ge=1,le=100)
    budget: float = Field(default=1,ge=0,allow_inf_nan=False)
    tokens: int = Field(default=50000,ge=1000,le=2000000)
    hours: float = Field(default=2,gt=0,le=24,allow_inf_nan=False)
    scope: str = 'sfw'
    rows: list[list] = Field(default_factory=list,max_length=30)
    visible_rows: int = Field(default=1,ge=1,le=30)

    @model_validator(mode='after')
    def validate_recipe(self):
        TaskSettings(goal=self.goal,groups=self.groups,per_group=self.per_group,
            target_styles=[line.strip() for line in self.themes.splitlines() if line.strip()],
            control_words=parse_control_words(self.rows,self.groups),character=self.character,
            character_controls=self.character_controls,content_label=self.scope)
        if any(len(row)!=3 for row in self.rows):
            raise ValueError('控制词方案需要组号、tags、权重三列')
        return self


class PresetStore:
    def __init__(self,path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            if not self.path.exists():
                return {}
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('version')!=1 or not isinstance(data.get('presets'),dict):
                raise ValueError('常用方案文件格式无效')
            return {name:StudioPreset.model_validate(body) for name,body in data['presets'].items()}

    def save(self,name,preset):
        name = str(name or '').strip()
        if not name or len(name)>80:
            raise ValueError('方案名称须为 1–80 个字符')
        preset = StudioPreset.model_validate(preset)
        with self.lock:
            presets = self.read()
            presets[name] = preset
            atomic_write(self.path,json.dumps({'version':1,'presets':{key:value.model_dump() for key,value in presets.items()}},ensure_ascii=False,indent=2).encode('utf-8'))
        return name


SCORE_LABELS = {'prompt_alignment':'提示词一致性','aesthetics':'美感','composition':'构图',
    'anatomy':'解剖','artifacts':'瑕疵','style_match':'主题匹配','structure':'结构',
    'hands':'手部','text_quality':'文字质量','safety_score':'安全','nsfw_target':'内容范围'}


def unreviewed_status(task, generation_state, review_enabled, group_cancelled=False):
    if group_cancelled:
        return '未评审：该组已取消','已取消该组后续处理，原图与提示词保留，不会继续自动评审。'
    if not review_enabled:
        return '未开启审查','此图未开启质量审查。'
    if task and task['reason']=='TOKEN_BUDGET_LIMIT':
        return '未评审：token 额度用尽','本任务 token 额度已用尽，未能评审这张图。原图与提示词已保留；后续任务不会自动补评。'
    if task and task['reason']=='INVALID_RESPONSE_REVIEW_REQUIRED' and generation_state=='EVALUATE':
        return '评审失败，尚无评分','这张图的云端评审失败，尚未取得有效评分；生成提示词已保留。'
    if task and task['state'] in ('COMPLETED','PARTIAL','CANCELLED','FAILED','BLOCKED_POLICY'):
        reason={'BUDGET_EXHAUSTED':'费用额度用尽','WALL_CLOCK_LIMIT':'运行时长到限','USER_STOP':'任务已停止'}.get(task['reason'],'任务已结束')
        return '未评审：'+reason,'本任务已结束，此图没有有效评分。原图与提示词已保留；后续任务不会自动补评。'
    return '评分待完成','此图尚未完成质量评审。'


def result_review_html(service,task_id,lang='zh',*,task_ids=None,asset_ids=None):
    if not task_id:
        return '<p>开始生图后，在这里查看各张图片的评分和此轮提示词。</p>'
    owners=list(dict.fromkeys(task_ids if task_ids is not None else [task_id]))
    if not owners:
        return '<p>尚无生成图片。</p>'
    placeholders=','.join('?' for _ in owners)
    asset_filter=' AND a.id IN ('+','.join('?' for _ in asset_ids)+')' if asset_ids else ''
    rows = service.db.rows(f'''SELECT a.id,a.task_id,a.group_id,a.path,a.state,n.state generation_state,g.ordinal,p.round_index,p.body prompt,
        e.id evaluation_id,e.body evaluation,e.effective_score,d.action,d.reason FROM assets a
        LEFT JOIN groups g ON g.id=a.group_id
        LEFT JOIN generations n ON n.id=a.generation_id
        LEFT JOIN prompt_variants p ON p.id=n.variant_id
        LEFT JOIN decisions d ON d.asset_id=a.id AND d.rowid=(SELECT MAX(rowid) FROM decisions WHERE asset_id=a.id)
        LEFT JOIN evaluations e ON e.id=COALESCE(d.evaluation_id,(SELECT id FROM evaluations WHERE asset_id=a.id AND stage='final' ORDER BY rowid DESC LIMIT 1))
        WHERE a.task_id IN ({placeholders}) AND a.source_kind='generated' AND a.state NOT IN ('DELETED','DELETING'){asset_filter} ORDER BY a.created_at DESC,a.rowid DESC''',(*owners,*(asset_ids or [])))
    parts = []
    settings_by_task={owner:service.settings(owner) for owner in owners}
    tasks_by_id={owner:service.db.one('SELECT state,reason FROM tasks WHERE id=?',(owner,)) for owner in owners}
    states = {'ACCEPTED':'已保存','CANDIDATE':'待确认','QUARANTINE':'已淘汰','REVIEW':'待人工检查','RECHECK':'待自动复核','REJECTED':'已拒绝'}
    esc = lambda value:html.escape(str(value),quote=True)
    for row in rows:
        owner=row['task_id']
        settings=settings_by_task[owner]
        plan = json.loads(row['prompt']) if row['prompt'] else None
        evaluation = json.loads(row['evaluation']) if row['evaluation'] else {}
        localized = service.db.one('SELECT body FROM review_localizations WHERE evaluation_id=? AND language=?',
            (row['evaluation_id'],lang)) if row['evaluation_id'] else None
        if localized:
            evaluation = {**evaluation, **json.loads(localized['body'])}
        task=tasks_by_id[owner]
        pending_label,pending_message=unreviewed_status(task,row['generation_state'],settings.review_enabled,service.group_cancelled(row['group_id']))
        group = row['ordinal']+1 if row['ordinal'] is not None else '?'
        round_index = row['round_index']+1 if row['round_index'] is not None else '?'
        score = f"综合分 {row['effective_score']:.1f}" if row['effective_score'] is not None else pending_label
        state = states.get(row['action'],row['action'] or (pending_label if pending_label.startswith('未评审') else '待处理'))
        if settings.autonomous and row['action'] in ('REVIEW','RECHECK','CANDIDATE'):
            state='待自动复核' if row['action']!='CANDIDATE' else '待自动判定'
        parts.append(f'<details class="result-review-card" data-asset-id="{esc(row["id"])}" data-persist-key="review-{esc(row["id"])}"><summary>任务 {esc(owner[:8])} · 第 {group} 组 · 第 {round_index} 轮 · {esc(row["id"][:8])} · {esc(score)} · {esc(state)}</summary>')
        parts.append(f'<p>图片来源任务 {esc(owner[:8])} · 控制角色：{esc(character_for_group(settings,row["ordinal"] or 0) or "未指定")}</p>')
        reason_text = {'BASIC_STRUCTURE_UNCERTAIN':'旧规则要求模型置信度达到 0.8；可按当前规则重新检查。',
            'SCORE_FIELDS_MISSING':'缺少必需评分，暂时不能自动判定。','FINAL_SCORE_REQUIRED':'尚未完成最终评分。',
            'SAFETY_CHECK_FAILED':'内容范围检查未通过，请结合逐项检查和评分依据核对。','BASIC_STRUCTURE_FAILED':'检测到多余、缺失、融合、断连或重复部位，或解剖、结构、手部低于 42 分。',
            'STRUCTURE_REVIEW_REQUIRED':'旧评审留下了结构疑点。自动任务恢复后会用原图复核，并按结果自动保存或继续迭代；原图和已有进度保留。',
            'QUALITY_BELOW_THRESHOLD':f'综合评分低于本任务设置的 {settings.quality_threshold:g} 分。',
            'CRITICAL_DEFECT':'检测到严重缺陷。','EXACT_DUPLICATE':'与已有图片重复。',
            'PROTECTED_ASSET':'复查未通过质量规则，不计入合格交付；受保护的原图保留，无需人工批准。',
            'CONTENT_SCOPE_UNCERTAIN':'内容范围尚未明确：自动任务使用原图模型复核，再自动保存或继续迭代；内容匹配分不等于实际违规。'}.get(row['reason'])
        if settings.autonomous and row['reason']=='CONTENT_SCOPE_UNCERTAIN' and (evaluation.get('visual_checks') or {}).get('content_violation') is False:
            reason_text='旧规则因内容匹配分留下待审状态，但逐项检查未发现范围违规。恢复后按当前质量规则自动处理，无需人工批准。'
        if reason_text:
            parts.append('<p>判定原因：'+esc(reason_text)+'</p>')
        if evaluation:
            parts.append('<div class="review-score-grid">'+''.join(f'<span>{label}：<b>{esc(evaluation.get(key)) if evaluation.get(key) is not None else "不可判定"}</b></span>' for key,label in SCORE_LABELS.items())+'</div>')
            checks=evaluation.get('visual_checks')
            if checks:
                names={'extra_limbs':'多余肢体','missing_parts':'部位缺失','fused_bodies':'人物融合','disconnected_parts':'连接断裂','duplicated_body':'身体重复','content_violation':'内容范围违规'}
                parts.append('<p>逐项检查：'+' · '.join(esc(label)+'：'+('发现异常' if checks.get(key) is True else '未见异常' if checks.get(key) is False else '遮挡或无法确认') for key,label in names.items())+'</p>')
                if checks.get('evidence'):
                    parts.append('<p>检查依据：'+esc(checks['evidence'])+'</p>')
            issues = evaluation.get('issues',[])
            if issues:
                parts.append('<ul>'+''.join(f'<li>{esc(issue.get("evidence",""))}</li>' for issue in issues)+'</ul>')
            suggestions = evaluation.get('prompt_suggestions',[])
            if suggestions:
                parts.append('<p>改进建议：'+esc('；'.join(item.get('suggestion','') for item in suggestions))+'</p>')
        else:
            parts.append('<p>'+esc(pending_message)+'</p>')
        parts.append(f'<details class="round-prompt" data-persist-key="prompt-{esc(row["id"])}"><summary>此轮提示词</summary>')
        if plan:
            parts.append('<p>正向提示词</p><pre>'+esc(plan['positive'])+'</pre><p>负向提示词</p><pre>'+esc(plan.get('negative',''))+'</pre>')
            params = plan.get('params',{})
            parts.append('<p>'+esc(f"{params.get('width','?')} × {params.get('height','?')} · steps {params.get('steps','?')} · CFG {params.get('cfg','?')} · seed {params.get('seed','?')} · {params.get('sampler','')} / {params.get('scheduler','')}")+'</p>')
            if plan.get('reason'):
                parts.append('<p>本轮设计：'+esc(plan['reason'])+'</p>')
        else:
            parts.append('<p>这张图片没有关联的生成提示词记录。</p>')
        parts.append('</details>')
        if plan:
            enabled = row['generation_state']=='DECIDED' and service.files.path(row['path']).is_file()
            disabled = '' if enabled else ' disabled'
            label = '产出张数' if lang=='zh' else 'Images'
            review_label = '生成后审查' if lang=='zh' else 'Review images'
            execute = '批量产出' if lang=='zh' else 'Generate batch'
            edit_prompt = '转到提示词生图' if lang=='zh' else 'Edit in prompt studio'
            discuss_prompt = '转到创作讨论' if lang=='zh' else 'Discuss this prompt'
            checked = ' checked' if settings.review_enabled else ''
            parts.append(f'<div class="result-batch-panel" data-task-id="{esc(owner)}" data-asset-id="{esc(row["id"])}" data-rounds="{settings.max_rounds}" data-threshold="{settings.quality_threshold:g}" data-lang="{esc(lang)}">'
                f'<div class="result-batch-controls"><label>{label}<input class="result-batch-count" type="number" min="1" max="50" step="1" value="1"{disabled}></label>'
                f'<label><input class="result-batch-review" type="checkbox"{checked}{disabled}>{review_label}</label>'
                f'<button type="button" class="result-batch-start"{disabled}>{execute}</button>'
                f'<button type="button" class="result-prompt-open">{edit_prompt}</button>'
                f'<button type="button" class="result-discussion-open">{discuss_prompt}</button></div>'
                '<p class="result-batch-note"></p><p class="result-batch-feedback" role="status" aria-live="polite"></p></div>')
        parts.append('</details>')
    return ''.join(parts) or '<p>尚无生成图片。</p>'


def control_preview_html(words,groups,plans=None):
    plans = plans or {}
    parts=[]
    missing=[i for i in range(1,groups+1) if not any(word.group in (None,i) for word in words)]
    if words and missing:
        parts.append('<p>以下组未应用控制词：'+', '.join(map(str,missing))+'。组号填“全部”可覆盖所有组。</p>')
    for ordinal in range(groups):
        controls=[word for word in words if word.group in (None,ordinal+1)]
        if not controls:
            continue
        plan=plans.get(ordinal)
        if plan:
            controlled=enforce_control_words(PromptPlan.model_validate(plan),controls)
            terms=list(prompt_parts(controlled.positive))[:len(controls)]
            label='本轮实际权重'
        else:
            terms=[f'({word.word}:{word.weight:g})' if word.weight is not None else f'{word.word}（模型统一选择）' for word in controls]
            label='配置预览'
        parts.append(f'<p><b>第 {ordinal+1} 组 · {label}</b></p><pre>{html.escape(", ".join(terms))}</pre>')
    return ''.join(parts) or '<p>未设置控制词。</p>'


def task_control_preview(service,task_id):
    if not task_id:
        return '<p>填写控制词后可查看预览；开始生图后显示模型实际使用的权重。</p>'
    settings=service.settings(task_id)
    plans={}
    for row in service.db.rows('''SELECT p.body,g.ordinal FROM prompt_variants p JOIN groups g ON g.id=p.group_id
        WHERE p.task_id=? ORDER BY p.rowid''',(task_id,)):
        plans[row['ordinal']]=json.loads(row['body'])
    return control_preview_html(settings.control_words,settings.groups,plans)
