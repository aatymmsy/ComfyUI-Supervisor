"""Edit scalar parameters of the configured workflow using ComfyUI definitions."""

import copy
import hashlib
import json
import math
import time
from pathlib import Path

import httpx
import yaml

from .db import uid
from .files import atomic_write
from .setup import ConnectionFailure
from .workflow_bypass import bypass_routes
from .workflow_switch import switch_info
from .models import WorkflowSwitch


KEY_NODES = {"KSampler", "KSamplerAdvanced", "EmptyLatentImage", "EmptySD3LatentImage",
             "LatentUpscale", "ImageScale", "ImageScaleBy", "VAEDecodeTiled"}
EXCLUDED = {"text", "filename_prefix", "seed", "noise_seed", "batch_size"}


def editor_signature(service):
    config=service.workflow
    if config is None:
        return None
    source=Path(config.workflow_api_json)
    source=source if source.is_absolute() else service.project_root/source
    stat=source.stat()
    return (str(source.resolve()),config.base_url,config.workflow_hash,stat.st_mtime_ns,stat.st_size)


def cached_editor_rows(service):
    cache=getattr(service,'_workflow_editor_cache',None)
    if not cache or cache['signature']!=editor_signature(service):
        raise ConnectionFailure('工作流已变化，请先读取工作流参数，再选择绕过节点。')
    rows=copy.deepcopy(cache['rows'])
    for row in rows:
        row['bypassed']=row['node_id'] in service.workflow.bypass_nodes
        selection=service.workflow.switch_nodes.get(row['node_id'])
        if row.get('switch') and selection:
            row['switch']['value']=selection.value
            if row['input']==selection.input:
                row['value']=selection.value
    return rows


async def read_workflow_editor(service):
    config = service.workflow
    if not config:
        raise ConnectionFailure("请先识别或导入 ComfyUI 工作流。")
    signature = editor_signature(service)
    source = Path(config.workflow_api_json)
    source = source if source.is_absolute() else service.project_root / source
    raw = source.read_bytes()
    if config.workflow_hash and hashlib.sha256(raw).hexdigest() != config.workflow_hash:
        raise ConnectionFailure("工作流文件已变化，请重新识别或导入。")
    graph = json.loads(raw)
    try:
        async with httpx.AsyncClient(timeout=10, transport=service.comfy.transport, trust_env=False) as client:
            response = await client.get(config.base_url.rstrip('/') + '/object_info')
            response.raise_for_status()
            definitions = response.json()
    except (httpx.HTTPError, ValueError):
        raise ConnectionFailure("无法读取 ComfyUI 可用参数，请检查连接。") from None
    rows = []
    for node_id, node in graph.items():
        kind = node['class_type']
        model_node = any(name in node.get('inputs', {}) for name in
                         ('ckpt_name','unet_name','lora_name','vae_name','clip_name','clip_name1','clip_name2','clip_name3','model_name'))
        switch = switch_info(graph, node_id, definitions)
        if kind not in KEY_NODES and not model_node and not switch:
            continue
        routes, bypass_reason = bypass_routes(graph, node_id, definitions)
        if node_id in config.output_nodes:
            routes, bypass_reason = {}, '交付输出节点无法绕过'
        definition = definitions.get(kind, {}).get('input', {})
        declared = {**definition.get('required', {}), **definition.get('optional', {})}
        for name, value in node.get('inputs', {}).items():
            if name in EXCLUDED or isinstance(value, (list,dict)) or name not in declared:
                continue
            spec = declared[name]
            if not isinstance(spec, list) or not spec:
                continue
            choices = spec[0] if isinstance(spec[0], list) else None
            value_type = 'enum' if choices is not None else spec[0]
            if value_type not in ('enum','INT','FLOAT','BOOLEAN','STRING'):
                continue
            options = spec[1] if len(spec) > 1 and isinstance(spec[1],dict) else {}
            limits = dict(options)
            for field,bindings in config.bindings.items():
                if any(b.node_id == node_id and b.input == name for b in bindings):
                    constraint = config.constraints.get(field,{})
                    if isinstance(constraint,dict):
                        if 'min' in constraint:
                            limits['min'] = max(limits.get('min',constraint['min']),constraint['min'])
                        if 'max' in constraint:
                            limits['max'] = min(limits.get('max',constraint['max']),constraint['max'])
                        if 'multiple_of' in constraint:
                            limits['multiple_of'] = constraint['multiple_of']
            rows.append({'key':f'{node_id}:{name}','node_id':node_id,'class_type':kind,'input':name,
                         'value':value,'type':value_type,'choices':choices,
                         'title':node.get('_meta',{}).get('title',''),'switch':switch,
                         'bypass_routes':routes,'bypass_reason':bypass_reason,'bypassed':node_id in config.bypass_nodes,
                         'min':limits.get('min'),'max':limits.get('max'),'multiple_of':limits.get('multiple_of')})
        if switch and not any(row['node_id']==node_id for row in rows):
            rows.append({'key':f"{node_id}:{switch['input']}",'node_id':node_id,'class_type':kind,'input':switch['input'],
                         'value':'由上游节点控制','type':'DISPLAY','choices':None,'title':node.get('_meta',{}).get('title',''),
                         'switch':switch,'bypass_routes':routes,'bypass_reason':bypass_reason,'bypassed':node_id in config.bypass_nodes,
                         'min':None,'max':None,'multiple_of':None})
    with service.active_lock:
        if editor_signature(service)!=signature:
            raise ConnectionFailure("工作流已变化，请重新读取参数。")
        revision=config.workflow_hash or hashlib.sha256(json.dumps(graph,sort_keys=True).encode()).hexdigest()
        for row in rows:
            row['revision']=revision
        service._workflow_editor_cache={"signature":signature,"rows":copy.deepcopy(rows),
                                       "graph":copy.deepcopy(graph),"definitions":definitions}
    return config, graph, cached_editor_rows(service)


def lora_options(service):
    """Only expose installed loaders and typed outputs from the current graph."""
    cached_editor_rows(service)
    cache=service._workflow_editor_cache
    graph, definitions=cache['graph'],cache['definitions']
    loaders={}
    for kind in ('LoraLoader','LoraLoaderModelOnly'):
        info=definitions.get(kind,{})
        required=info.get('input',{}).get('required',{})
        expected={'model','lora_name','strength_model'} | ({'clip','strength_clip'} if kind=='LoraLoader' else set())
        spec=required.get('lora_name',[])
        outputs=['MODEL','CLIP'] if kind=='LoraLoader' else ['MODEL']
        if set(required)==expected and info.get('output')==outputs and spec and isinstance(spec[0],list) and spec[0]:
            loaders[kind]=spec[0]
    sources={'MODEL':[],'CLIP':[]}
    for node_id,node in graph.items():
        info=definitions.get(node['class_type'],{})
        names=info.get('output_name',[])
        title=node.get('_meta',{}).get('title') or node['class_type']
        for index,kind in enumerate(info.get('output',[])):
            if kind in sources and node_id not in service.workflow.bypass_nodes:
                label=f'{node_id} · {title} · {names[index] if index<len(names) else kind}'
                sources[kind].append((f'{node_id}:{index}',label))
    defaults={}
    depths={}
    def depth(node_id,seen=frozenset()):
        if node_id in depths:
            return depths[node_id]
        if node_id in seen:
            return 0
        links=[str(value[0]) for value in graph[node_id].get('inputs',{}).values()
               if isinstance(value,list) and len(value)==2 and str(value[0]) in graph]
        depths[node_id]=1+max([depth(key,seen|{node_id}) for key in links]+[0])
        return depths[node_id]
    for kind in sources:
        used=[]
        for node in graph.values():
            for name,value in node.get('inputs',{}).items():
                if name==kind.lower() and isinstance(value,list) and len(value)==2:
                    key=f'{value[0]}:{value[1]}'
                    if any(option[0]==key for option in sources[kind]):
                        used.append(key)
        defaults[kind]=max(used,key=lambda key:depth(key.rsplit(':',1)[0])) if used else (sources[kind][-1][0] if sources[kind] else '')
    return {'loaders':loaders,'sources':sources,'defaults':defaults,
            'revision':service.workflow.workflow_hash or hashlib.sha256(json.dumps(graph,sort_keys=True).encode()).hexdigest()}


def _ensure_editable(service,config,graph):
    if service.active or service.db.one("SELECT id FROM tasks WHERE state IN ('RUNNING','STOPPING') OR lease_until>? LIMIT 1",(time.time(),)):
        raise ConnectionFailure('请先停止运行中的任务，等待本轮处理完成后再修改工作流。')
    if service.workflow is not config:
        raise ConnectionFailure('工作流已更换，请重新读取参数。')
    source=Path(config.workflow_api_json)
    source=source if source.is_absolute() else service.project_root/source
    if json.loads(source.read_bytes())!=graph:
        raise ConnectionFailure('工作流文件已变化，请重新读取参数。')


def _persist_editor_graph(service,config,graph,constraints=None):
    relative=f'workflows/customized-{uid()}.json'
    raw=json.dumps(graph,ensure_ascii=False,indent=2).encode('utf-8')
    revised=config.model_copy(update={'workflow_api_json':relative,'workflow_hash':hashlib.sha256(raw).hexdigest(),
                                      'constraints':constraints if constraints is not None else config.constraints})
    atomic_write(service.project_root/relative,raw)
    atomic_write(service.workflow_path,yaml.safe_dump(revised.model_dump(),sort_keys=False).encode('utf-8'))
    transport=service.comfy.transport
    service.reload_config()
    service.comfy.transport=transport


async def save_workflow_parameters(service,request):
    if not isinstance(request,dict) or not isinstance(request.get('changes'),dict) or len(request['changes'])>500:
        raise ConnectionFailure('参数格式无效，请重新读取参数。')
    config,graph,rows=await read_workflow_editor(service)
    revision=lora_options(service)['revision']
    if request.get('revision')!=revision:
        raise ConnectionFailure('工作流已变化，请重新读取参数后再修改。')
    by_key={row['key']:row for row in rows}
    changes={}
    for key,value in request['changes'].items():
        row=by_key.get(key)
        if not row or row['type']=='DISPLAY' or row.get('switch'):
            raise ConnectionFailure('包含不可编辑参数，请重新读取参数。')
        try:
            changes[key]=parameter_value(row,value)
        except ConnectionFailure as exc:
            raise ConnectionFailure(f"节点 {row['node_id']} / {row['input']}：{exc}") from None
    with service.active_lock:
        _ensure_editable(service,config,graph)
        if not changes:
            return rows
        constraints=copy.deepcopy(config.constraints)
        for key,value in changes.items():
            row=by_key[key]
            graph[row['node_id']]['inputs'][row['input']]=value
            for field,bindings in config.bindings.items():
                if row['type']=='enum' and any(b.node_id==row['node_id'] and b.input==row['input'] for b in bindings):
                    constraints[field]=row['choices']
        _persist_editor_graph(service,config,graph,constraints)
        service.db.event(None,'WORKFLOW_PARAMETERS_UPDATED',{'parameters':changes})
    return rows


async def add_workflow_lora(service,request):
    if not isinstance(request,dict):
        raise ConnectionFailure('LoRA 配置格式无效。')
    config,graph,_=await read_workflow_editor(service)
    options=lora_options(service)
    if request.get('revision')!=options['revision']:
        raise ConnectionFailure('工作流已变化，请重新读取参数后添加 LoRA。')
    kind=request.get('loader')
    if kind not in options['loaders'] or request.get('lora_name') not in options['loaders'][kind]:
        raise ConnectionFailure('请选择本机已安装的 LoRA 和加载节点。')
    definitions=service._workflow_editor_cache['definitions']
    required=definitions[kind]['input']['required']
    inputs={'lora_name':request['lora_name']}
    links={}
    for channel in ('MODEL','CLIP') if kind=='LoraLoader' else ('MODEL',):
        selected=request.get(channel.lower())
        if selected not in [item[0] for item in options['sources'][channel]]:
            raise ConnectionFailure(f'请选择有效的 {channel} 接入节点。')
        node_id,index=selected.rsplit(':',1)
        links[channel]=[node_id,int(index)]
        inputs[channel.lower()]=links[channel]
    for name in ('strength_model','strength_clip') if kind=='LoraLoader' else ('strength_model',):
        spec=required[name]
        limits=spec[1] if len(spec)>1 and isinstance(spec[1],dict) else {}
        row={'type':'FLOAT','min':limits.get('min'),'max':limits.get('max')}
        inputs[name]=parameter_value(row,request.get(name))
    node_id=str(max([int(key) for key in graph if key.isdigit()]+[0])+1)
    while node_id in graph:
        node_id=uid()
    revised_graph=copy.deepcopy(graph)
    rewired=0
    output=definitions[kind].get('output',[])
    for node in revised_graph.values():
        for name,value in list(node.get('inputs',{}).items()):
            for channel,link in links.items():
                if value==link:
                    node['inputs'][name]=[node_id,output.index(channel)]
                    rewired+=1
    if not rewired:
        raise ConnectionFailure('接入节点没有下游连接，请选择当前生成链路中的节点。')
    revised_graph[node_id]={'class_type':kind,'inputs':inputs,'_meta':{'title':'LoRA · '+request['lora_name']}}
    visiting,done=set(),set()
    def visit(key):
        if key in visiting:
            raise ConnectionFailure('MODEL 与 CLIP 接入位置形成循环，请选择同一 LoRA 节点的两个输出，或各自上游加载节点。')
        if key in done:
            return
        visiting.add(key)
        for value in revised_graph[key].get('inputs',{}).values():
            if isinstance(value,list) and len(value)==2 and str(value[0]) in revised_graph:
                visit(str(value[0]))
        visiting.remove(key);done.add(key)
    for key in revised_graph:
        visit(key)
    with service.active_lock:
        _ensure_editable(service,config,graph)
        bypasses={}
        for bypass_id in config.bypass_nodes:
            routes,reason=bypass_routes(revised_graph,bypass_id,definitions)
            if not routes:
                raise ConnectionFailure(f'节点 {bypass_id} 的绕过连接无法更新：{reason}')
            bypasses[bypass_id]=routes
        _persist_editor_graph(service,config.model_copy(update={'bypass_nodes':bypasses}),revised_graph)
        service.db.event(None,'WORKFLOW_LORA_ADDED',{'node_id':node_id,'class_type':kind,'lora_name':request['lora_name'],'connections':rewired})
    return node_id


def parameter_value(row, value):
    kind = row['type']
    if kind == 'enum':
        if value not in row['choices']:
            raise ConnectionFailure("请选择 ComfyUI 列表中的可用模型或选项；自定义模型需先安装到 ComfyUI。")
        return value
    if kind == 'BOOLEAN':
        if type(value) is not bool:
            raise ConnectionFailure("此参数需要开关值。")
        return value
    if kind in ('INT','FLOAT'):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ConnectionFailure("此参数需要有效数字。")
        if kind == 'INT' and value != int(value):
            raise ConnectionFailure("此参数需要整数。")
        value = int(value) if kind == 'INT' else float(value)
        if (row['min'] is not None and value < row['min']) or (row['max'] is not None and value > row['max']):
            raise ConnectionFailure("参数超出 ComfyUI 允许的范围。")
        if row.get('multiple_of') and value % row['multiple_of']:
            raise ConnectionFailure("参数不符合工作流要求的步进值。")
        return value
    if not isinstance(value,str) or len(value) > 1000:
        raise ConnectionFailure("此参数需要不超过 1000 字符的文本。")
    return value


async def save_workflow_parameter(service, key, value):
    try:
        cached = next((r for r in cached_editor_rows(service) if r['key']==key),None)
    except (ConnectionFailure,OSError):
        cached = None
    if cached and cached.get('switch') and cached['input']==cached['switch']['input']:
        return await set_workflow_switch(service,cached['node_id'],value)
    config, graph, rows = await read_workflow_editor(service)
    row = next((r for r in rows if r['key'] == key), None)
    if row is None:
        raise ConnectionFailure("请先选择可编辑的节点参数。")
    if row.get('switch') and row['input']==row['switch']['input']:
        return await set_workflow_switch(service,row['node_id'],value)
    value = parameter_value(row,value)
    with service.active_lock:
        if service.active or service.db.one("SELECT id FROM tasks WHERE state IN ('RUNNING','STOPPING') OR lease_until>? LIMIT 1",(time.time(),)):
            raise ConnectionFailure("请先停止运行中的任务，等待本轮处理完成后再修改工作流。")
        if service.workflow is not config:
            raise ConnectionFailure("工作流已更换，请刷新参数列表。")
        source = Path(config.workflow_api_json)
        source = source if source.is_absolute() else service.project_root/source
        if json.loads(source.read_bytes()) != graph:
            raise ConnectionFailure("工作流文件已变化，请重新读取参数。")
        graph[row['node_id']]['inputs'][row['input']] = value
        relative = f'workflows/customized-{uid()}.json'
        raw = json.dumps(graph,ensure_ascii=False,indent=2).encode('utf-8')
        constraints = dict(config.constraints)
        for field,bindings in config.bindings.items():
            if row['type']=='enum' and any(b.node_id==row['node_id'] and b.input==row['input'] for b in bindings):
                constraints[field] = row['choices']
        revised = config.model_copy(update={'workflow_api_json':relative,'workflow_hash':hashlib.sha256(raw).hexdigest(),'constraints':constraints})
        atomic_write(service.project_root / relative,raw)
        atomic_write(service.workflow_path,yaml.safe_dump(revised.model_dump(),sort_keys=False).encode('utf-8'))
        transport = service.comfy.transport
        service.reload_config()
        service.comfy.transport = transport
        service.db.event(None,'WORKFLOW_PARAMETER_UPDATED',{'node_id':row['node_id'],'input':row['input'],'value':value})
    return rows


async def set_workflow_bypass(service, node_id, enabled):
    # A toggle records intent only. Comfy.graph compiles the bypass on submission.
    with service.active_lock:
        config=service.workflow
        rows=cached_editor_rows(service)
        row=next((r for r in rows if r['node_id']==node_id),None)
        if row is None or type(enabled) is not bool:
            raise ConnectionFailure('请重新读取并选择有效的节点。')
        if enabled and not row['bypass_routes']:
            raise ConnectionFailure(row['bypass_reason'])
        bypasses=copy.deepcopy(config.bypass_nodes)
        if enabled:
            bypasses[node_id]=row['bypass_routes']
        else:
            bypasses.pop(node_id,None)
        revised=config.model_copy(update={'bypass_nodes':bypasses})
        atomic_write(service.workflow_path,yaml.safe_dump(revised.model_dump(),sort_keys=False).encode('utf-8'))
        service.workflow=revised
        # An active worker owns its immutable task snapshot; preserve its Comfy instance.
        if not service.active and not service.executing_task:
            service.comfy.config=revised
        service.db.event(None,'WORKFLOW_BYPASS_UPDATED',{'node_id':node_id,'enabled':enabled,'apply_on':'next_submission'})
        return cached_editor_rows(service)


async def set_workflow_switch(service, node_id, value):
    # Like bypasses, record only; an active/queued task keeps its own snapshot.
    with service.active_lock:
        config = service.workflow
        rows = cached_editor_rows(service)
        row = next((r for r in rows if r['node_id']==node_id and r.get('switch')), None)
        if row is None or type(value) is not bool:
            raise ConnectionFailure('请重新读取并选择有效的切换节点。')
        info = row['switch']
        if info['reason']:
            raise ConnectionFailure(info['reason'])
        selections = copy.deepcopy(config.switch_nodes)
        selections[node_id] = WorkflowSwitch(class_type=row['class_type'],input=info['input'],value=value)
        revised = config.model_copy(update={'switch_nodes':selections})
        atomic_write(service.workflow_path,yaml.safe_dump(revised.model_dump(),sort_keys=False).encode('utf-8'))
        service.workflow = revised
        if not service.active and not getattr(service,'executing_task',None):
            service.comfy.config = revised
        service.db.event(None,'WORKFLOW_SWITCH_UPDATED',{'node_id':node_id,'value':value,'apply_on':'next_submission'})
        return cached_editor_rows(service)
