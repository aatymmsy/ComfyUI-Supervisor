import json
import shutil
import subprocess

import pytest

from supervisor.ui import build_ui
from supervisor.upload_view import UPLOAD_JS


def test_upload_controls_share_drop_behavior_and_keep_latest_event(service):
    app = build_ui(service)
    ids = {'workflow-api-upload', 'workflow-image-upload', 'studio-references',
           'caption-input', 'caption-workflow-input', 'prompt-image-input', 'manual-references'}
    controls = {c.elem_id: c for c in app.blocks.values() if getattr(c, 'elem_id', None) in ids}
    assert set(controls) == ids
    assert all('supervisor-upload' in c.elem_classes for c in controls.values())
    assert all('supervisor-image-upload' in c.elem_classes for key, c in controls.items() if key != 'workflow-api-upload')
    assert controls['caption-input'].interactive is False
    assert controls['caption-input'].file_types == ['image']
    events = app.config['dependencies']
    for name in ('caption-input', 'prompt-image-input', 'workflow-image-upload', 'caption-workflow-input'):
        upload = next(event for event in events if (controls[name]._id, 'upload') in event['targets'])
        assert upload['trigger_mode'] == 'always_last'
    assert any(event.get('js') == UPLOAD_JS for event in events)
    assert next(event for event in events if event.get('js') == UPLOAD_JS)['queue'] is False


def test_file_drop_adapter_reaches_native_input_without_duplicate_uploads():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed only to run the browser adapter unit test')
    harness = r"""
const assert = require('node:assert/strict');
const listeners = {}, roots = [], notices = {};
global.window = {supervisorLanguage:'zh'};
global.setTimeout = () => 1; global.clearTimeout = () => {};
const observers = [];
global.MutationObserver = class {
  constructor(fn) {this.fn=fn;}
  observe(root) {this.root=root; observers.push(this);}
  disconnect() {this.root=null;}
};
global.DataTransfer = class {
  constructor() {this.files=[];this.items={add:file=>this.files.push(file)};}
};
global.document = {
  body:{appendChild(box){notices[box.id]=box;}},
  getElementById:id=>notices[id],
  createElement:()=>({setAttribute(){}}),
  querySelectorAll:selector=>selector==='.supervisor-upload' ? roots : roots.filter(r=>r.active()),
  addEventListener(name,fn,capture){assert.equal(capture,true);listeners[name]=fn;}
};
function root(id,{preview=false,disabled=false}={}) {
  const classes=new Set(disabled?['caption-disabled']:[]);
  const r={id,dataset:{},isConnected:true,changes:0,clears:0,
    classList:{contains:k=>classes.has(k),add:k=>classes.add(k),remove:(...keys)=>keys.forEach(k=>classes.delete(k))},
    active:()=>classes.has('upload-drag-active') || classes.has('upload-drag-invalid'),
    getBoundingClientRect:()=>({left:10,top:10,right:50,bottom:50,width:40,height:40}),
    contains:target=>target===r,
    closest:()=>r,
    querySelector(selector){
      if(selector==='input[type=file]') return r.input;
      if(selector.startsWith('button[')) return {click(){
        r.clears++;r.input=makeInput();observers.filter(o=>o.root===r).forEach(o=>o.fn());
      }};
      return null;
    }
  };
  function makeInput(){return {value:'previous',files:[],dispatchEvent(e){assert.equal(e.type,'change');assert.equal(e.bubbles,true);r.changes++;}};}
  if(!preview) r.input=makeInput(); roots.push(r);return r;
}
const image=name=>({name,type:'image/png',size:10});
function event(target,files,types=['Files']) {
  return {target,clientX:100,clientY:100,dataTransfer:{files,types},prevented:0,stopped:0,
    preventDefault(){this.prevented++;},stopImmediatePropagation(){this.stopped++;}};
}
const init=eval('('+UPLOAD_SOURCE+')');init();init();
(async()=>{
  const blank=root('prompt-image-input');
  const label={closest:()=>blank};
  let e=event(label,[image('first.png')]);listeners.dragover(e);
  assert(blank.active());assert.equal(e.dataTransfer.dropEffect,'copy');
  listeners.drop(e);assert.equal(blank.changes,1);assert.equal(e.stopped,1);assert(!blank.active());
  assert.equal(blank.input.files[0].name,'first.png');
  const replaced=root('prompt-image-input',{preview:true});
  e=event(replaced,[{name:'bad.json',type:'application/json',size:10}]);listeners.drop(e);
  assert.equal(replaced.clears,0);assert.match(notices['supervisor-upload-feedback'].textContent,/格式不支持/);
  listeners.drop(event(replaced,[image('second.png')]));await Promise.resolve();await Promise.resolve();
  assert.equal(replaced.clears,1);assert.equal(replaced.changes,1);assert.equal(replaced.input.files[0].name,'second.png');
  listeners.drop(event(blank,[image('a.png'),image('b.png')]));assert.equal(blank.changes,1);
  const multi=root('studio-references');listeners.drop(event(multi,[image('a.png'),image('b.png')]));
  assert.equal(multi.changes,1);assert.equal(multi.input.files.length,2);
  const off=root('caption-input',{disabled:true});listeners.drop(event(off,[image('a.png')]));assert.equal(off.changes,0);
  assert.match(notices['supervisor-upload-feedback'].textContent,/尚未启用/);
  const caption=root('caption-input');listeners.drop(event(caption,[{name:'photo.bmp',type:'image/bmp',size:10}]));assert.equal(caption.changes,1);
  e=event({closest:()=>null},[image('a.png')]);listeners.drop(e);assert.equal(e.prevented,1);assert.match(notices['supervisor-upload-feedback'].textContent,/上传框内/);
  e=event(blank,[],['text/plain']);listeners.drop(e);assert.equal(e.prevented,0);assert.equal(e.stopped,0);
  const json=root('workflow-api-upload');listeners.drop(event(json,[image('wrong.png')]));assert.equal(json.changes,0);
  listeners.drop(event(json,[{name:'graph.JSON',type:'application/json',size:10}]));assert.equal(json.changes,1);
  listeners.drop(event(blank,[{...image('large.png'),size:101*1024*1024}]));assert.equal(blank.changes,1);
  e=event({closest:()=>null},[image('a.png')]);e.clientX=20;e.clientY=20;listeners.drop(e);assert.equal(blank.changes,2);
  console.log('upload adapter scenarios passed');
})().catch(err=>{console.error(err);process.exitCode=1;});
"""
    result = subprocess.run([node, '-e', 'const UPLOAD_SOURCE=' + json.dumps(UPLOAD_JS) + ';\n' + harness],
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'scenarios passed' in result.stdout
