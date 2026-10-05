"""Compile persisted bypass switches without altering the imported source graph."""
import copy


def is_link(value):
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and type(value[1]) is int


def bypass_routes(graph, node_id, definitions):
    node = graph[node_id]
    definition = definitions.get(node['class_type'], {})
    inputs = definition.get('input', {})
    declared = {**inputs.get('required', {}), **inputs.get('optional', {})}
    outputs = definition.get('output', [])
    used = {value[1] for other in graph.values() for value in other.get('inputs', {}).values()
            if is_link(value) and value[0] == node_id}
    if not used:
        return {}, '此节点没有连接到后续节点，无需绕过'
    routes = {}
    for slot in sorted(used):
        if slot < 0 or slot >= len(outputs):
            return {}, '无法识别输出类型，请检查节点定义'
        candidates = [value for name, value in node.get('inputs', {}).items()
                      if is_link(value) and declared.get(name) and declared[name][0] == outputs[slot]]
        if len(candidates) != 1:
            return {}, '没有唯一的同类型输入可直接接通，无法绕过'
        routes[str(slot)] = candidates[0]
    return routes, ''


def apply_bypasses(graph, bypass_nodes):
    graph = copy.deepcopy(graph)
    def resolve(link, seen):
        if not is_link(link) or link[0] not in bypass_nodes:
            if is_link(link) and link[0] not in graph:
                raise ValueError('绕过连接引用了不存在的节点')
            return link
        key = (link[0], link[1])
        if key in seen:
            raise ValueError('绕过节点形成了循环连接')
        replacement = bypass_nodes[link[0]].get(str(link[1]))
        if not is_link(replacement):
            raise ValueError('绕过节点缺少对应输出的兼容输入')
        return resolve(replacement, seen | {key})
    for node_id in bypass_nodes:
        if node_id not in graph:
            raise ValueError('绕过的节点已不存在，请重新读取工作流')
    for node_id, node in graph.items():
        if node_id not in bypass_nodes:
            for name, value in node.get('inputs', {}).items():
                if is_link(value):
                    node['inputs'][name] = resolve(value, set())
    for node_id in bypass_nodes:
        del graph[node_id]
    return graph
