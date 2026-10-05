"""Keep user-entered terms in each local generation prompt."""

import re
import math
import json

from .models import CharacterControl, ControlWord, PromptPlan, TaskSettings, dump_json


def parse_character_input(value, group_count):
    """Accept legacy global text or the new editable, scoped role rows."""
    if not isinstance(value,str):
        raise ValueError('控制角色格式无效')
    value=value.strip()
    if not value.startswith('['):
        TaskSettings(goal='validate',groups=group_count,character=value)
        return value,[]
    if len(value)>30000:
        raise ValueError('控制角色信息过长')
    rows=json.loads(value)
    if not isinstance(rows,list) or len(rows)>30:
        raise ValueError('控制角色最多 30 行')
    controls=[]
    for row in rows:
        if not isinstance(row,dict) or set(row)-{'groups','name'}:
            raise ValueError('角色行需要组号和角色名称')
        name=row.get('name','')
        if not isinstance(name,str):
            raise ValueError('角色名称必须为文本')
        if not name.strip():
            continue
        raw=row.get('groups','')
        if not isinstance(raw,str):
            raise ValueError('角色组号必须为文本')
        parts=[part.strip() for part in re.split('[,，]',raw) if part.strip()]
        if not parts or (len(parts)==1 and parts[0].casefold() in ('全部','全部组','all','*')):
            groups=[]
        elif all(part.isascii() and part.isdigit() for part in parts):
            groups=list(dict.fromkeys(map(int,parts)))
        else:
            raise ValueError('角色组号请填写全部或用逗号分隔的组号')
        controls.append(CharacterControl(name=name.strip(),groups=groups))
    TaskSettings(goal='validate',groups=group_count,character_controls=controls)
    return '',controls


def character_form_value(settings):
    if settings.character_controls:
        return dump_json([{'groups':','.join(map(str,item.groups)) if item.groups else '全部','name':item.name}
            for item in settings.character_controls])
    return settings.character


def character_for_group(settings, ordinal):
    if settings.character_controls:
        return '；'.join(item.name for item in settings.character_controls if not item.groups or ordinal+1 in item.groups)
    return settings.character.strip()


def character_summary(settings):
    if settings.character_controls:
        return '；'.join(('第 '+','.join(map(str,item.groups))+' 组' if item.groups else '全部组')+'：'+item.name
            for item in settings.character_controls)
    return settings.character or '未指定'


def parse_control_words(rows, group_count=None):
    words, seen = [], set()
    for row_index, row in enumerate(rows or [], start=1):
        if not isinstance(row,(list,tuple)) or len(row) not in (2,3):
            raise ValueError('控制词表需要组号、词语和权重三列')
        group, term, weight = row if len(row)==3 else (None,*row)
        term = str(term or '').strip()
        terms = [part.strip() for part in re.split('[,，]',term) if part.strip()]
        if not terms:
            continue
        if len(row)==3:
            if isinstance(group,bool) or group is None:
                raise ValueError('请填写控制词组号')
            parts = [part.strip() for part in re.split('[,，]',str(group)) if part.strip()]
            if not parts:
                raise ValueError('请填写控制词组号')
            if len(parts)==1 and parts[0].casefold() in ('全部','全部组','all','*'):
                groups=[None]
            else:
                groups = list(dict.fromkeys(ControlWord(word='group',group=part).group for part in parts))
        else:
            groups = [None]
        if group_count is not None and any(group is not None and group>group_count for group in groups):
            raise ValueError('控制词组号不能超过主题组数')
        weight = weight.strip() if isinstance(weight,str) else weight
        for group in groups:
            for term in terms:
                word = ControlWord(word=term,weight=None if weight in (None,'') else weight,group=group,
                    weight_group=row_index if len(terms)>1 or len(groups)>1 else None)
                key = (word.group,term.casefold())
                if key in seen or (group is not None and (None,key[1]) in seen) or (group is None and any(term==key[1] for _,term in seen)):
                    raise ValueError('同组控制词重复，请合并同一词语的权重')
                seen.add(key)
                words.append(word)
                if len(words) > 300:
                    raise ValueError('控制词最多 300 个 tag（按应用组数计）')
    return words


def control_word_rows(words):
    """Restore row origins, combining identical tags/weight across theme groups."""
    origins = {}
    for index, word in enumerate(words):
        origin = word.weight_group if word.weight_group is not None else f'word-{index}'
        groups = origins.setdefault((origin,word.weight), {})
        groups.setdefault(word.group, []).append(word.word)
    rows = []
    for (_, weight), groups in origins.items():
        matching = {}
        for group, terms in groups.items():
            matching.setdefault(tuple(terms), []).append(group)
        for terms, group_ids in matching.items():
            rows.append(['全部' if None in group_ids else ','.join(map(str,group_ids)),', '.join(terms),'' if weight is None else str(weight)])
    return rows


def prompt_parts(text):
    # Commas inside existing prompt attention syntax belong to that expression.
    depth, start = 0, 0
    for index,char in enumerate(text):
        if char in '([':
            depth += 1
        elif char in ')]':
            depth = max(0,depth-1)
        elif char == ',' and depth == 0:
            yield text[start:index].strip()
            start = index+1
    yield text[start:].strip()


def enforce_control_words(plan, words):
    if not words:
        return plan
    patterns = [re.compile(r'^(?:' + re.escape(w.word) + r'|\(' + re.escape(w.word) + r'(?::[^()]*)?\))$',re.I) for w in words]
    def unlocked(text):
        return [part for part in prompt_parts(text) if part and not any(p.fullmatch(part) for p in patterns)]
    positive_parts = list(prompt_parts(plan.positive))
    def proposed_weight(word):
        if word.weight is not None:
            return word.weight
        weighted = re.compile(r'^\(' + re.escape(word.word) + r':\s*([^()]+)\)$',re.I)
        for part in positive_parts:
            match = weighted.fullmatch(part)
            if match:
                try:
                    proposed = float(match[1])
                except ValueError:
                    continue
                if math.isfinite(proposed) and 0.1 <= proposed <= 3:
                    return proposed
        return None

    weights = [proposed_weight(word) for word in words]
    locked, shared_weights = [], {}
    # Select the first valid proposed weight in input order for each row.
    # Rows without an ID keep legacy behavior; missing weights default to 1.
    for word, weight in zip(words,weights):
        if word.weight_group is not None and weight is not None:
            key = (word.group,word.weight_group)
            shared_weights.setdefault(key,weight)
    for word, weight in zip(words,weights):
        if word.weight_group is not None:
            weight = shared_weights.get((word.group,word.weight_group),1)
        elif weight is None:
            weight = 1
        locked.append(f'({word.word}:{weight:g})')
    # A copied plan ensures length validation also applies to locally added terms.
    return PromptPlan.model_validate({**plan.model_dump(),
        'positive':', '.join(locked+unlocked(plan.positive)),
        'negative':', '.join(unlocked(plan.negative))})


def controls_for_group(settings, ordinal):
    # Missing group preserves old tasks' global controls; new UI rows are scoped.
    return [word for word in settings.control_words if word.group in (None,ordinal+1)]


def controlled_goal(settings, ordinal=None):
    words = settings.control_words if ordinal is None else controls_for_group(settings,ordinal)
    return settings.goal + ' ' + ' '.join(w.word for w in words)


def enforce_lora_triggers(plan, text):
    if not text.strip():
        return plan
    def identity(part):
        match = re.fullmatch(r'\((.+?)(?::\s*[-+0-9.]+)?\)',part)
        return (match[1] if match else part).strip().casefold()
    triggers = []
    seen = set()
    for part in prompt_parts(text.replace('，',',').replace('\r','').replace('\n',',')):
        key = identity(part)
        if part and key not in seen:
            triggers.append(part); seen.add(key)
    positive = [part for part in prompt_parts(plan.positive) if part]
    existing = {identity(part) for part in positive}
    added = [part for part in triggers if identity(part) not in existing]
    negative = [part for part in prompt_parts(plan.negative) if part and identity(part) not in seen]
    return PromptPlan.model_validate({**plan.model_dump(),
        'positive':', '.join(added+positive),'negative':', '.join(negative)})
