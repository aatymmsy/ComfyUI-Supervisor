"""ComfyUI editor metadata built from the exact submitted API graph."""
from __future__ import annotations

import json
import struct
import zlib


def editor_workflow(graph, definitions):
    if not graph or any(n['class_type'] not in definitions for n in graph.values()):
        return None  # Leave the API prompt fallback usable for unknown custom nodes.
    ids = {key: index + 1 for index, key in enumerate(graph)}
    nodes, by_id, links = [], {}, []
    for index, (key, data) in enumerate(graph.items()):
        definition = definitions[data['class_type']]
        specs = {**definition.get('input', {}).get('required', {}), **definition.get('input', {}).get('optional', {})}
        widget_values, inputs, named = [], [], {}
        for name, spec in specs.items():
            kind = spec[0]
            options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            value = data.get('inputs', {}).get(name)
            connected = isinstance(value, list) and len(value) == 2 and str(value[0]) in graph and isinstance(value[1], int)
            widget = (isinstance(kind, list) or kind in ('INT', 'FLOAT', 'STRING', 'BOOLEAN')) and not options.get('forceInput')
            input_type = 'COMBO' if isinstance(kind, list) else kind
            if widget:
                default = options.get('default', kind[0] if isinstance(kind, list) and kind else {'INT':0, 'FLOAT':0.0, 'STRING':'', 'BOOLEAN':False}.get(kind))
                widget_values.append(default if connected or name not in data.get('inputs', {}) else value)
                named[name] = widget_values[-1]
                if options.get('control_after_generate'):
                    widget_values.append('fixed')
                if connected:
                    inputs.append({'name':name, 'type':input_type, 'link':None, 'widget':{'name':name}})
            else:
                inputs.append({'name':name, 'type':input_type, 'link':None})
        # Dynamic custom-node input names can be absent from object_info.
        for name, value in data.get('inputs', {}).items():
            if name not in specs and isinstance(value, list) and len(value) == 2 and str(value[0]) in graph:
                inputs.append({'name':name, 'type':'*', 'link':None})
        outputs = [{'name':name, 'type':kind, 'links':[], 'slot_index':slot} for slot, (name, kind) in enumerate(zip(definition.get('output_name', definition.get('output', [])), definition.get('output', [])))]
        node = {'id':ids[key], 'type':data['class_type'], 'pos':[(index % 5) * 360, (index // 5) * 410],
                'size':[320,360], 'flags':{}, 'order':index, 'mode':0, 'inputs':inputs, 'outputs':outputs,
                'properties':{'Node name for S&R':data['class_type']}, 'widgets_values':widget_values,
                'widgets_values_named':named, 'title':data.get('_meta', {}).get('title', data['class_type'])}
        nodes.append(node)
        by_id[key] = node
    for key, data in graph.items():
        for slot, inlet in enumerate(by_id[key]['inputs']):
            value = data.get('inputs', {}).get(inlet['name'])
            if not (isinstance(value, list) and len(value) == 2 and str(value[0]) in by_id and isinstance(value[1], int)):
                continue
            source, source_slot = by_id[str(value[0])], value[1]
            if not 0 <= source_slot < len(source['outputs']):
                return None
            link_id = len(links) + 1
            kind = source['outputs'][source_slot]['type']
            links.append([link_id, source['id'], source_slot, ids[key], slot, kind])
            inlet['link'] = link_id
            source['outputs'][source_slot]['links'].append(link_id)
    return {'last_node_id':len(nodes), 'last_link_id':len(links), 'nodes':nodes, 'links':links, 'groups':[], 'config':{}, 'extra':{}, 'version':0.4}


def png_with_graph(content, graph, workflow=None):
    """Replace metadata chunks without recompressing pixels or dropping other chunks."""
    if not content.startswith(b'\x89PNG\r\n\x1a\n'):
        return content
    metadata = {'prompt':json.dumps(graph, ensure_ascii=False, allow_nan=False)}
    if workflow:
        metadata['workflow'] = json.dumps(workflow, ensure_ascii=False, allow_nan=False)
    result, offset, added = [content[:8]], 8, False
    while offset < len(content):
        size = struct.unpack('>I', content[offset:offset+4])[0]
        chunk = content[offset:offset+size+12]
        kind, body = chunk[4:8], chunk[8:-4]
        if kind in (b'IDAT',b'IEND') and not added:
            for key, value in metadata.items():
                payload = key.encode('ascii') + b'\0\0\0\0\0' + value.encode('utf-8')
                crc = zlib.crc32(b'iTXt' + payload) & 0xffffffff
                result.append(struct.pack('>I', len(payload)) + b'iTXt' + payload + struct.pack('>I', crc))
            added = True
        # Always discard stale workflow metadata; its values may differ from this run.
        stale = kind in (b'tEXt', b'zTXt', b'iTXt') and body.split(b'\0', 1)[0] in (b'prompt', b'workflow')
        if not stale:
            result.append(chunk)
        offset += size + 12
    return b''.join(result)
