"""Describe boolean branch switches and apply staged choices on submission."""
import copy
from .workflow_bypass import is_link

BRANCH_PAIRS = (("on_true", "on_false"), ("true", "false"),
                ("if_true", "if_false"), ("true_value", "false_value"),
                ("value_true", "value_false"))


def branch_label(graph, value):
    if is_link(value):
        upstream = graph.get(value[0])
        if upstream is None:
            return f"节点 {value[0]}（连接已失效）"
        title = upstream.get('_meta', {}).get('title') or upstream['class_type']
        details = [f"{key}={item}" for key, item in upstream.get('inputs', {}).items()
                   if key in ('width', 'height', 'ckpt_name', 'unet_name', 'lora_name', 'vae_name')
                   and not isinstance(item, (list, dict))]
        suffix = ' · ' + ', '.join(details) if details else ''
        return f"节点 {value[0]} · {title} · {upstream['class_type']} · 输出 {value[1]}{suffix}"
    if value is None:
        return "未连接"
    return f"直接输入：{value}"


def switch_info(graph, node_id, definitions):
    node = graph[node_id]
    if 'switch' not in node['class_type'].lower():
        return None
    definition = definitions.get(node['class_type'], {}).get('input', {})
    declared = {**definition.get('required', {}), **definition.get('optional', {})}
    booleans = [key for key, spec in declared.items() if isinstance(spec, list) and spec and spec[0] == 'BOOLEAN']
    if len(booleans) != 1:
        return None
    selector = booleans[0]
    inputs = node.get('inputs', {})
    pair = next((pair for pair in BRANCH_PAIRS if any(key in declared for key in pair)), None)
    reason = ''
    if pair is None:
        reason = '无法确认 True / False 的分支含义，保留原节点设置'
    elif type(inputs.get(selector)) is not bool:
        reason = '切换值由上游节点输入，不能在此替换连接'
    true_name, false_name = pair or ('on_true', 'on_false')
    return {'input': selector, 'value': inputs.get(selector) if type(inputs.get(selector)) is bool else None,
            'reason': reason, 'true': branch_label(graph, inputs.get(true_name)) if pair else '无法确认连接，请在 ComfyUI 查看',
            'false': branch_label(graph, inputs.get(false_name)) if pair else '无法确认连接，请在 ComfyUI 查看'}


def apply_switches(graph, switch_nodes):
    result = copy.deepcopy(graph)
    for node_id, selection in switch_nodes.items():
        node = result.get(node_id)
        if node is None or node['class_type'] != selection.class_type:
            raise ValueError('切换节点已变化，请重新读取工作流')
        if type(node.get('inputs', {}).get(selection.input)) is not bool:
            raise ValueError('切换值不是可编辑布尔参数，不能替换连接')
        node['inputs'][selection.input] = selection.value
    return result
