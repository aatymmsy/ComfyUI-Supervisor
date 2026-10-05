"""Bounded, stage-specific cloud context; full records remain local."""

import re
import hashlib
import random


PERSON = re.compile(r"\b(?:\d+girls?|\d+boys?|woman|women|man|men|girl|boy|person|people|portrait|human|couple)\b|人物|少女|女孩|男孩|女性|男性|女人|男人|人像|肖像", re.I)
FACE_EXCEPTION = re.compile(r"\b(?:closed eyes|eyes closed|eyes shut|shut eyes|sleeping|asleep|back view|from behind|faceless|silhouette|masked|wearing a mask|blindfolded)\b|闭眼|闭目|睡眠|睡觉|背影|背面|不露脸|无脸|剪影|面具|蒙眼", re.I)


def needs_face(positive, goal="", target=""):
    # Only the user's request may intentionally hide a face, not copied metadata.
    visible_people = re.sub(r"\b(?:no|without) (?:people|persons?|humans?)\b|无人物|无人", "", positive, flags=re.I)
    return bool(PERSON.search(visible_people) and not FACE_EXCEPTION.search(goal + " " + target))


def ensure_face(plan, goal, target):
    if not needs_face(plan.positive, goal, target):
        return
    plan.positive = re.sub(r"\b(?:closed eyes|eyes closed|eyes shut|shut eyes|faceless|no face)\b", "", plan.positive, flags=re.I)
    if not re.search(r"facial features|defined face|detailed face|五官|面部细节", plan.positive, re.I):
        plan.positive = plan.positive.rstrip(", ") + ", clearly defined facial features"
    if not re.search(r"open eyes|eyes open|睁眼|睁开|明亮.*眼", plan.positive, re.I):
        plan.positive = plan.positive.rstrip(", ") + ", detailed open eyes, expressive gaze"
    if not re.search(r"faceless|missing face|deformed face|无脸|脸部畸形", plan.negative, re.I):
        plan.negative = plan.negative.rstrip(", ") + (", " if plan.negative else "") + "faceless, missing facial features, deformed face"
    if not re.search(r"closed eyes|eyes closed|闭眼", plan.negative, re.I):
        plan.negative += ", closed eyes"


def compact_card(card):
    fields = ("subject", "style", "medium", "composition", "lighting", "camera", "color", "detail", "negative", "writing_style", "locked_attributes", "variation_axes")
    result = {field: [str(value)[:100] for value in card.get(field, [])[:6 if field in ("subject","locked_attributes") else 3]] for field in fields if card.get(field)}
    facial = [str(value)[:100] for value in card.get("detail", []) if re.search(r"face|facial|eyes|gaze|expression|五官|面部|眼|表情", value, re.I)]
    if facial:
        result["detail"] = list(dict.fromkeys(facial[:2] + result.get("detail", [])))[:4]
    result["prompt_dialect"] = card.get("prompt_dialect", "tags")
    return result


def compact_prompt(plan):
    return {field: plan[field][:2400 if field=="positive" else 800] for field in ("positive", "negative") if field in plan}


def compact_review(review):
    scores = ("overall", "prompt_alignment", "aesthetics", "composition", "anatomy", "structure", "hands", "text_quality", "artifacts", "style_match", "safety_score")
    return {**{key: review[key] for key in scores if review.get(key) is not None},
            "decision": review.get("decision"),
            "issues": [{key: str(issue.get(key, ""))[:200] for key in ("category", "severity", "region", "evidence")} for issue in review.get("issues", [])[:3]],
            "prompt_suggestions": review.get("prompt_suggestions", [])[:2]}


def reference_prompt(metadata):
    extracted = metadata.get("extracted", {})
    return {field: str(extracted[field].get("value", ""))[:1200] for field in ("positive", "negative")
            if isinstance(extracted.get(field), dict) and extracted[field].get("value")}


def normalize_change_field(field):
    value = field.strip().lower().replace(" ", "_")
    for prefix in ("positive.", "positive_prompt."):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    aliases = {"style_tags":"style", "style_match":"style", "visual_style":"style", "风格":"style",
               "negative_prompt":"negative", "negative.prompt":"negative", "negative.tags":"negative", "负向提示词":"negative",
               "positive":"detail", "positive_prompt":"detail", "positive.prompt":"detail", "正向提示词":"detail",
               "details":"detail", "构图":"composition", "光照":"lighting", "色彩":"color"}
    return aliases.get(value,value)


REFERENCE_IDENTITY = re.compile(r"\b(?:hair|eyes?|irises|pupils|face|facial|freckles|moles?|skin|complexion|ethnic|asian|caucasian|age|aged|young|old|teen|girl|boy|woman|man|female|male|character|identity|likeness|named|slim|slender|curvy|breasts?|body type|long legs)\b|头发|发色|瞳|眼睛|五官|脸|肤|痣|雀斑|民族|族裔|年龄|少女|女人|男人|女性|男性|人物身份|同一人物|姓名|身材|胸部", re.I)
LEGACY_POSE = re.compile(r"\b(?:standing|sitting|kneeling|walking|running|crouching|leaning|lying down|arms raised|hands on hips|looking over shoulder)\b|站立|坐姿|跪姿|奔跑|行走|蹲姿|倚靠|躺姿|举手",re.I)


def reference_guidance(card):
    """Only transferable shot/pose/details enter generation or review context."""
    result = {}
    for field in ('camera','pose','composition','lighting','detail'):
        values = [str(value).strip()[:100] for value in card.get(field,[]) if not REFERENCE_IDENTITY.search(str(value))]
        if field=='pose' and not values:
            values = [match.group(0).lower() for value in card.get('subject',[]) for match in LEGACY_POSE.finditer(str(value))]
        if values:
            result[field] = list(dict.fromkeys(values))[:6]
    return result


def reference_study(card):
    from .models import StyleCard
    data = card.model_dump() if hasattr(card,'model_dump') else card
    safe = [value for value in data.get('writing_style',[]) if value in WRITING_TECHNIQUES]
    return StyleCard(**reference_guidance(data), writing_style=safe,
                     reference_images=data.get('reference_images',[]))


WRITING_TECHNIQUES = (
    'concise comma-separated visual tags',
    'concise natural-language description',
    'selective attention weights as (term:weight)',
    'selective emphasis written as weight::phrase::',
    'describe foreground/midground/background and relative positions',
    'describe material, surface texture and light interaction',
    'describe a concrete action and its interaction with a prop/environment',
)


def prompt_techniques(examples):
    """Learn a small syntax/description recipe locally, never retain example nouns."""
    if not examples.strip():
        return []
    techniques = [WRITING_TECHNIQUES[0] if ',' in examples or '，' in examples else WRITING_TECHNIQUES[1]]
    if re.search(r'\([^()]+:\s*\d+(?:\.\d+)?\)',examples):
        techniques.append(WRITING_TECHNIQUES[2])
    if re.search(r'\d+(?:\.\d+)?::[^:]+::',examples):
        techniques.append(WRITING_TECHNIQUES[3])
    patterns = (
        r'foreground|background|midground|upper[- ]|lower[- ]|corner|overlap|beside|前景|后景|背景|左上|右下|遮挡',
        r'texture|reflection|glass|lace|knit|metal|condensation|材质|纹理|反射|玻璃|金属|蕾丝',
        r'holding|grips|rests|leaning|walking|running|握|拿|倚|行走|奔跑',
    )
    for technique, pattern in zip(WRITING_TECHNIQUES[4:], patterns):
        if re.search(pattern, examples, re.I):
            techniques.append(technique)
    return techniques


def prompt_format(examples):
    return '; '.join(prompt_techniques(examples))


def generation_reference_context(card):
    """Transfer descriptive methods, not the reference's literal visual anchors."""
    techniques = [value for value in card.get('writing_style',[]) if value in WRITING_TECHNIQUES]
    guidance = reference_guidance(card)
    methods = []
    if guidance.get('camera'):
        methods.append('Specify a deliberate shot size, viewpoint and perspective chosen for this group.')
    if guidance.get('pose'):
        methods.append('Describe a concrete gesture/action with coherent body and prop interaction.')
    if guidance.get('composition'):
        methods.append('Arrange foreground, subject and background with explicit spatial relationships.')
    if guidance.get('lighting'):
        methods.append('Explain light direction and how it interacts with materials.')
    if guidance.get('detail'):
        methods.append('Add selective material, surface and environmental microdetails relevant to this new scene.')
    return {key:value for key,value in {'writing_methods':methods,'prompt_techniques':techniques[:7]}.items() if value}


def creative_brief(task_id, ordinal):
    """Stable independent art-direction seeds. No cloud request or other group prompt."""
    rng = random.Random(int.from_bytes(hashlib.sha256(task_id.encode()).digest()[:8], 'big'))
    decks = {
        'shot': ['extreme close-up of an interaction','close-up with layered foreground','medium close-up','waist-up environmental view','medium full shot','full-body view','wide environmental view','distant establishing view','overhead detail view','low viewpoint with foreground depth'],
        'composition': ['off-center subject and negative space','diagonal movement through the frame','foreground framing and deep layers','asymmetric overlapping forms','reflected framing with a clear focal point','balanced geometry with one visual interruption','curved leading lines toward the action','silhouette edges separated by directional light','near/far scale contrast','a frame within the frame'],
        'action': ['capture the instant before an action','show motion and its physical aftermath','build a tactile interaction with a scene-specific object','reveal an unexpected detail of an ordinary activity','use a quiet pause with a meaningful gesture','capture a transition between two actions','show a discovery through a spatial relationship','use an environment-driven gesture','show a purposeful reach or turn','build an interaction between light, material and movement'],
    }
    for values in decks.values():
        rng.shuffle(values)
    return {**{key:values[(ordinal + (ordinal // len(values)) * offset) % len(values)] for offset,(key,values) in enumerate(decks.items())},
            'rule':'Optional creative direction: user theme and explicit framing/action take priority. Invent a fresh scene and subject for this group. Adapt these ideas to non-human subjects when needed; do not add a person by default.'}


def character_anchor_seed(task_id, ordinal):
    """Distinct, restart-stable starting points, adapted by the planner to style."""
    rng = random.Random('character-anchors:' + task_id)
    decks = {
        'hair_color': ['black','dark brown','chestnut','copper','golden blonde',
                       'platinum blonde','auburn','silver','pastel pink','teal'],
        'eye_color': ['brown','blue','green','hazel','amber','gray','violet','turquoise'],
        'hairstyle': ['pixie cut','chin-length bob','shoulder-length waves','long straight hair',
                      'high ponytail','low side ponytail','twin braids','single loose braid',
                      'curly crop','voluminous long curls','messy bun','sleek low bun',
                      'half-up hair','asymmetric bob','short shag','layered shoulder-length hair',
                      'side-swept short hair','crown braid','long wavy hair with bangs','buzz cut'],
    }
    for values in decks.values():
        rng.shuffle(values)
    return {key: values[ordinal % len(values)] for key, values in decks.items()}


APPEARANCE_ANCHORS = re.compile(
    r'\b(?:(?:jet[- ]black|dark brown|light brown|golden blonde|platinum blonde|'
    r'pastel pink|black|brown|chestnut|copper|blond[e]?|auburn|silver|white|gray|grey|'
    r'red|pink|purple|violet|teal|blue|green|hazel|amber|turquoise)\s+(?:hair|eyes|irises)|'
    r'(?:long|short|shoulder[- ]length|chin[- ]length|straight|wavy|curly|braided)\s+hair|'
    r'(?:high|low|side|twin)\s+ponytails?|(?:twin|single|crown)\s+braids?|'
    r'(?:messy|sleek|low|high)\s+bun|pixie cut|asymmetric bob|buzz cut|bob cut|'
    r'ponytail|twintails|braids|bangs|half[- ]up hair)\b|'
    r'(?:黑|棕|栗|铜|金|银|白|灰|红|粉|紫|蓝|绿|琥珀|茶)(?:色)?(?:头发|发|瞳|眼睛)|'
    r'长发|短发|卷发|直发|波浪发|双马尾|高马尾|低马尾|单马尾|麻花辫|丸子头|齐刘海|波波头', re.I)


def appearance_anchors(positive):
    """Bounded appearance-only comparisons; never share another group's scene."""
    return list(dict.fromkeys(match.group(0).casefold() for match in APPEARANCE_ANCHORS.finditer(positive)))[:8]
