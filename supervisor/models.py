from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationInfo, model_validator

ContentLabel = Literal["sfw", "adult_allowed", "unknown", "blocked"]
PASS_SCORE = 42


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Policy(Contract):
    nsfw_policy: Literal["allowed", "forbidden", "unknown"] = "unknown"
    evidence: str | None = None
    review_due: str | None = None
    allowed_content: list[ContentLabel] = Field(default_factory=lambda: ["sfw"])
    allow_unknown_input: bool = False

    def permits(self, label: ContentLabel) -> bool:
        if label == "blocked" or self.nsfw_policy == "unknown":
            return False
        if label == "sfw":
            return "sfw" in self.allowed_content
        if self.nsfw_policy != "allowed" or not self.evidence or not self.review_due:
            return False
        try:
            expiry = datetime.fromisoformat(self.review_due.replace("Z", "+00:00"))
            expiry = expiry.replace(tzinfo=timezone.utc) if expiry.tzinfo is None else expiry.astimezone(timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                return False
        except ValueError:
            return False
        return label in self.allowed_content and (label != "unknown" or self.allow_unknown_input)


class Pricing(Contract):
    currency: str = "USD"
    input_per_million_micro: int = Field(ge=0)
    output_per_million_micro: int = Field(ge=0)
    image_input_tokens_bound: int = Field(default=4096, ge=0)
    max_call_micro: int = Field(gt=0)
    source: str
    verified_at: str


class ModelConfig(Contract):
    id: str
    text: bool | None = None
    vision: bool | None = None
    json_schema: bool | None = None
    max_output_tokens: int = Field(default=1800, ge=256, le=12000)
    context_window: int | None = Field(default=None, gt=0)
    max_long_edge: int = Field(default=1536, ge=128, le=8192)
    max_images: int = Field(default=10, ge=1, le=100)
    pricing: Pricing | None = None


class Credential(Contract):
    id: str
    api_key_env: str | None = None
    secret_ref: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.api_key_env) == bool(self.secret_ref):
            raise ValueError("Exactly one secret source is required")
        return self


class Provider(Contract):
    id: str
    name: str
    type: Literal["openai_compatible", "openai_responses", "anthropic", "gemini", "custom"] = "openai_compatible"
    enabled: bool = True
    priority: int = 10
    base_url: str
    credentials: list[Credential] = Field(default_factory=list)
    models_endpoint: str | None = "/models"
    chat_endpoint: str = "/chat/completions"
    responses_endpoint: str = "/responses"
    policy: Policy = Field(default_factory=Policy)
    models: list[ModelConfig] = Field(default_factory=list)
    timeout_seconds: int = Field(default=90, ge=1, le=600)
    concurrency: int = Field(default=1, ge=1, le=10)
    rpm: int = Field(default=10, ge=1)
    retries: int = Field(default=1, ge=0, le=3)
    upstream_allowlist: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def url_and_ids(self):
        from urllib.parse import urlparse
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Cloud base_url must be HTTPS without embedded credentials/query")
        if len({x.id for x in self.models}) != len(self.models):
            raise ValueError("Duplicate model ID")
        return self


class ProvidersConfig(Contract):
    schema_version: Literal[1] = 1
    providers: list[Provider] = Field(default_factory=list)
    routes: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({x.id for x in self.providers}) != len(self.providers):
            raise ValueError("Duplicate provider ID")
        return self


class Binding(Contract):
    node_id: str
    class_type: str
    input: str
    value_type: Literal["string", "integer", "number"]
    replace_text_link: bool = False


class WorkflowSwitch(Contract):
    class_type: str
    input: str
    value: StrictBool


class WorkflowConfig(Contract):
    schema_version: Literal[1] = 1
    base_url: str = "http://127.0.0.1:8188"
    ws_url: str | None = None
    workflow_api_json: str
    workflow_hash: str | None = None
    bindings: dict[str, list[Binding]]
    output_nodes: list[str]
    bypass_nodes: dict[str, dict[str, list]] = Field(default_factory=dict)
    switch_nodes: dict[str, WorkflowSwitch] = Field(default_factory=dict)
    constraints: dict = Field(default_factory=dict)
    timeout_seconds: int = Field(default=900, ge=1)
    reconcile_interval: float = Field(default=10, gt=0)


class Params(Contract):
    steps: int = Field(default=20, ge=1, le=100)
    cfg: float = Field(default=7, ge=0, le=30, allow_inf_nan=False)
    sampler: str = "euler"
    scheduler: str = "normal"
    seed: int = Field(default=0, ge=0, le=2**63 - 1)
    width: int = Field(default=768, ge=64, le=4096)
    height: int = Field(default=768, ge=64, le=4096)
    batch_size: Literal[1] = 1


class Change(Contract):
    field: str
    before: str
    after: str
    hypothesis: str


class ThemePlan(Contract):
    themes: list[Annotated[str, Field(min_length=1, max_length=3000)]] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def requested_themes(self, info: ValidationInfo):
        if any(not theme.strip() for theme in self.themes) or len({theme.strip().casefold() for theme in self.themes}) != len(self.themes):
            raise ValueError("Themes must be nonempty and distinct")
        context = info.context or {}
        if context.get("count") is not None and len(self.themes) != context["count"]:
            raise ValueError("Return exactly the requested number of themes")
        if context.get("mode") == "select" and any(theme not in context.get("existing_themes", []) for theme in self.themes):
            raise ValueError("Select existing themes without rewriting them")
        if context.get("mode") == "generate" and {theme.casefold() for theme in self.themes} & {theme.casefold() for theme in context.get("existing_themes", [])}:
            raise ValueError("New themes must differ from existing themes")
        if context.get("mode") == "generate" and any(len(theme) > 100 for theme in self.themes):
            raise ValueError("New themes must be concise")
        return self


class PromptPlan(Contract):
    schema_version: Literal[1] = 1
    group_id: str
    positive: str = Field(min_length=1, max_length=12000)
    negative: str = Field(default="", max_length=6000)
    params: Params = Field(default_factory=Params)
    reason: str = Field(max_length=1500)
    changes: list[Change] = Field(default_factory=list, max_length=2)


class StyleCard(Contract):
    schema_version: Literal[1] = 1
    subject: list[str] = Field(default_factory=list)
    style: list[str] = Field(default_factory=list)
    medium: list[str] = Field(default_factory=list)
    composition: list[str] = Field(default_factory=list)
    lighting: list[str] = Field(default_factory=list)
    camera: list[str] = Field(default_factory=list)
    pose: list[str] = Field(default_factory=list)
    color: list[str] = Field(default_factory=list)
    detail: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)
    reference_images: list[str] = Field(default_factory=list)
    locked_attributes: list[str] = Field(default_factory=list)
    variation_axes: list[str] = Field(default_factory=lambda: ["style"])
    prompt_dialect: Literal["tags", "natural_language"] = "tags"
    writing_style: list[str] = Field(default_factory=list, max_length=30)


Reason = Literal["composition_failure", "anatomy_error", "style_drift", "target_mismatch", "duplicate", "blur", "artifacts", "other"]
Score = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None


class Issue(Contract):
    category: Reason
    severity: Literal["minor", "major", "critical"]
    region: str | None
    evidence: str = Field(min_length=1, max_length=500)


class Suggestion(Contract):
    field: str
    suggestion: str = Field(max_length=1000)
    expected_effect: str = Field(max_length=500)


class VisualChecks(Contract):
    extra_limbs: StrictBool | None
    missing_parts: StrictBool | None
    fused_bodies: StrictBool | None
    disconnected_parts: StrictBool | None
    duplicated_body: StrictBool | None
    content_violation: StrictBool | None
    evidence: str = Field(max_length=500)

    @model_validator(mode='after')
    def visible_evidence(self):
        if any(getattr(self,key) is True for key in type(self).model_fields if key!='evidence') and not self.evidence.strip():
            from pydantic_core import PydanticCustomError
            raise PydanticCustomError('visible_evidence_required', 'Visible defects require concrete evidence')
        return self


class Evaluation(Contract):
    schema_version: Literal[1] = 1
    asset_id: str
    stage: Literal["prescreen", "final"]
    overall: Score
    prompt_alignment: Score
    aesthetics: Score
    composition: Score
    anatomy: Score
    artifacts: Score
    style_match: Score
    nsfw_target: Score
    structure: Score = None
    hands: Score = None
    text_quality: Score = None
    safety_score: Score = None
    visual_checks: VisualChecks | None = None
    traditional: dict = Field(default_factory=dict)
    decision: Literal["keep", "retry", "delete", "review"]
    delete_reason: list[Reason] = Field(max_length=8)
    prompt_suggestions: list[Suggestion] = Field(max_length=2)
    issues: list[Issue] = Field(max_length=12)
    unassessable_fields: list[str]

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_confidence(cls, value):
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if key != "confidence"}
        return value

    @model_validator(mode="after")
    def score_bounds(self):
        for field in ["overall", "prompt_alignment", "aesthetics", "composition", "anatomy", "artifacts", "style_match", "nsfw_target", "structure", "hands", "text_quality", "safety_score"]:
            value = getattr(self, field)
            if value is not None and (not math.isfinite(value) or not 0 <= value <= 100):
                raise ValueError(f"Invalid score: {field}")
            if field in self.unassessable_fields and value is not None:
                raise ValueError(f"Unassessable field must be null: {field}")
        if self.decision == "delete" and not self.delete_reason:
            raise ValueError("Delete suggestion requires a reason")
        return self

    def effective_score(self) -> float | None:
        weights = {"prompt_alignment": 25, "aesthetics": 15, "composition": 15, "anatomy": 15, "artifacts": 15, "style_match": 15}
        values = [(getattr(self, k), w) for k, w in weights.items() if getattr(self, k) is not None]
        values.extend((getattr(self, k), 10) for k in ("structure", "hands", "text_quality", "safety_score") if getattr(self, k) is not None)
        values.extend((self.traditional[k], w) for k, w in (("clip_score", 10), ("aesthetic_score", 10), ("blur_score", 5)) if isinstance(self.traditional.get(k), (int, float)) and math.isfinite(self.traditional[k]) and 0 <= self.traditional[k] <= 100)
        return round(sum(v * w for v, w in values) / sum(w for _, w in values), 2) if values else None


class ControlWord(Contract):
    word: str = Field(min_length=1, max_length=100)
    weight: float | None = Field(default=None, ge=0.1, le=3, allow_inf_nan=False)
    group: int | None = Field(default=None, ge=1, le=20)
    weight_group: int | None = Field(default=None, ge=1, description="Tags from the same input row share this weight group within their theme group")

    @model_validator(mode="after")
    def plain_term(self):
        if not self.word.strip() or self.word != self.word.strip() or any(c in self.word for c in '()[]:,|\n\r'):
            raise ValueError("控制词须为不含权重语法的单词或短语，请在权重列单独设置")
        return self


class CharacterControl(Contract):
    name: str = Field(min_length=1,max_length=600)
    groups: list[Annotated[int,Field(ge=1,le=20,strict=True)]] = Field(default_factory=list,max_length=20)

    @model_validator(mode='after')
    def clean(self):
        if self.name != self.name.strip() or not self.name.strip():
            raise ValueError('角色名称不能为空或包含首尾空格')
        object.__setattr__(self,'groups',list(dict.fromkeys(self.groups)))
        return self


class ResultRetry(Contract):
    source_task: str = Field(min_length=1, max_length=80)
    source_asset: str = Field(min_length=1, max_length=80)
    feedback: str = Field(default="人工审查认为这张图不达标，请根据已有评审改善明显缺陷，保留原图的内容要求。", max_length=1000)
    review: dict = Field(default_factory=dict)


class TaskSettings(Contract):
    goal: str = Field(min_length=1, max_length=3000)
    autonomous: bool = False
    direct_prompt: str | None = Field(default=None, max_length=12000)
    direct_negative: str = Field(default="", max_length=6000)
    result_retry: ResultRetry | None = None
    review_enabled: bool = True
    reference_only: bool = False
    images_only_delivery: bool = False
    prompt_examples: str = Field(default="", max_length=12000)
    lora_trigger_words: str = Field(default="", max_length=2000)
    character: str = Field(default="", max_length=600)
    character_controls: list[CharacterControl] = Field(default_factory=list,max_length=30)
    control_words: list[ControlWord] = Field(default_factory=list, max_length=300)
    target_styles: list[str] = Field(default_factory=list, max_length=100)
    export_folder: str | None = None
    delivery_layout: Literal['task_folder','flat'] = 'task_folder'
    delivery_format: Literal['files','zip'] = 'files'
    groups: int = Field(default=2, ge=1, le=20)
    per_group: int = Field(default=2, ge=1, le=50)
    max_rounds: int = Field(default=8, ge=1, le=100)
    max_generations: int = Field(default=1000, ge=1, le=5000)
    max_runtime_seconds: int = Field(default=7200, ge=1, le=86400)
    patience: int = Field(default=3, ge=1, le=20)
    budget_micro: int = Field(default=1000000, ge=0)
    token_budget: int = Field(default=50000, ge=1000, le=2000000)
    currency: str = "USD"
    quality_threshold: float = Field(default=PASS_SCORE, ge=0, le=100)
    content_label: ContentLabel = "unknown"
    delete_mode: Literal["quarantine", "delayed", "direct"] = "quarantine"
    allow_permanent_delete: bool = False
    calibrated: bool = False
    retention_days: int = Field(default=7, ge=1, le=365)
    prescreen: bool = True
    auto_candidates: bool = False
    auto_iterations: bool = False
    demo: bool = True
    input_folder: str | None = None
    sample_count: int = Field(default=15, ge=1)
    sample_seed: int = 42
    params: Params = Field(default_factory=Params)

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_delete_confidence(cls, value):
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if key != "delete_confidence"}
        return value

    @property
    def basic_pass(self):
        return self.quality_threshold <= PASS_SCORE

    @model_validator(mode="after")
    def safety(self):
        shared_weights = {}
        for word in self.control_words:
            if word.weight_group is not None:
                key = (word.group,word.weight_group)
                if key in shared_weights and shared_weights[key] != word.weight:
                    raise ValueError("同行控制词必须使用相同权重")
                shared_weights[key] = word.weight
        if any(word.group is not None and word.group > self.groups for word in self.control_words):
            raise ValueError("控制词组号不能超过主题组数")
        if any(group>self.groups for item in self.character_controls for group in item.groups):
            raise ValueError('控制角色组号不能超过主题组数')
        for group in range(1,self.groups+1):
            names=[item.name for item in self.character_controls if not item.groups or group in item.groups]
            if len({name.casefold() for name in names}) != len(names):
                raise ValueError('同组控制角色重复，请合并角色信息')
            if len('；'.join(names))>600:
                raise ValueError('同组角色信息不能超过 600 字')
        if self.direct_prompt is not None and not self.direct_prompt.strip():
            raise ValueError("DIRECT_PROMPT_REQUIRED")
        if self.result_retry is not None and self.direct_prompt is None:
            raise ValueError('重画任务必须包含原图提示词')
        if not self.review_enabled and self.direct_prompt is None:
            raise ValueError("REVIEW_OPTION_ONLY_FOR_DIRECT_PROMPT")
        if self.content_label == "blocked":
            raise ValueError("Blocked content cannot start a task")
        if self.delete_mode != "quarantine" and not (self.allow_permanent_delete and (self.calibrated or self.autonomous)):
            raise ValueError("Permanent deletion requires explicit opt-in and calibrated reviewer")
        return self


def load_yaml(path: Path, contract):
    return contract.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def dump_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
