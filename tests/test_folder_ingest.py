from pathlib import Path

import gradio as gr
from PIL import Image

from supervisor.files import studio_reference_paths
from supervisor.models import TaskSettings
from supervisor.ui import build_ui


def test_local_folder_samples_before_any_read_or_cache(service,tmp_path,monkeypatch):
    folder=tmp_path/'references';folder.mkdir()
    nested=folder/'nested';nested.mkdir()
    for index in range(40):
        Image.new('RGB',(64,64),(index,100,150)).save((folder if index<20 else nested)/f'{index:02}.png')
    (folder/'notes.txt').write_text('Not an image')
    reads=[]
    original_open=Path.open
    def record_open(path,*args,**kwargs):
        if path.is_relative_to(folder):
            reads.append(path)
        return original_open(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',record_open)
    selected,total=studio_reference_paths([],[],str(folder),123)
    assert total==40 and len(selected)==15 and not reads
    assert studio_reference_paths([],[],str(folder),123)[0]==selected
    task=service.create_task(TaskSettings(goal='landscape',groups=1),selected,start=False)
    assert set(reads)==set(selected)
    assets=service.db.rows("SELECT path FROM assets WHERE task_id=? AND source_kind='reference'",(task,))
    assert len(assets)==15
    assert len(list(service.files.path(f'tasks/{task}/images').iterdir()))==15
    assert not service.db.rows('SELECT id FROM api_calls')


def test_folder_preview_only_counts_selected_paths_without_decoding(service,tmp_path,monkeypatch):
    for index in range(30):
        (tmp_path/f'{index}.png').touch()
    app=build_ui(service)
    assert not any(isinstance(component,gr.File) and component.file_count=='directory' for component in app.blocks.values())
    callback=next(f.fn for f in app.fns.values() if getattr(f.fn,'__name__','')=='preview_references')
    def unexpected_open(*args,**kwargs):
        raise AssertionError('Hidden preview must not decode source images')
    monkeypatch.setattr(Image,'open',unexpected_open)
    preview,message,seed,directory=callback([],[],str(tmp_path))
    assert preview['value']==[] and preview['visible'] is False
    assert '30' in message and '15' in message
    assert isinstance(seed,int)
    assert len(directory['_sample']['selected'])==15
    assert not service.db.rows('SELECT id FROM assets')


def test_reference_source_is_exclusive_and_switches_visible_controls(service,tmp_path):
    image=tmp_path/'manual.png';image.touch()
    folder=tmp_path/'collection';folder.mkdir()
    (folder/'from-folder.jpg').touch()
    assert studio_reference_paths([image],{'source':'images'},str(folder),1)==([image],1)
    assert studio_reference_paths([image],{'source':'folder'},str(folder),1)==([folder/'from-folder.jpg'],1)
    # An inactive folder is not even scanned or validated.
    assert studio_reference_paths([image],{'source':'images'},str(folder/'missing'),1)==([image],1)
    app=build_ui(service)
    callback=next(f.fn for f in app.fns.values() if getattr(f.fn,'__name__','')=='switch_reference_source')
    assert callback('folder')==({'source':'folder'},gr.update(visible=False),gr.update(visible=True))
    assert callback('images')==({'source':'images'},gr.update(visible=True),gr.update(visible=False))
    field=next(c for c in app.blocks.values() if getattr(c,'elem_id',None)=='studio-folder')
    assert field.interactive is False


def test_folder_picker_populates_readonly_path_and_cancel_preserves_choice(service,monkeypatch):
    app=build_ui(service)
    callback=next(f.fn for f in app.fns.values() if getattr(f.fn,'__name__','')=='select_reference_folder')
    monkeypatch.setattr('supervisor.ui.choose_folder',lambda current:'C:/图片/参考')
    assert callback('')['value']=='C:/图片/参考'
    monkeypatch.setattr('supervisor.ui.choose_folder',lambda current:current)
    assert callback('C:/图片/参考')['value']=='C:/图片/参考'


def test_native_folder_dialog_runs_separately_and_handles_unicode_and_cancel(monkeypatch):
    import json
    from types import SimpleNamespace
    from supervisor.setup import choose_folder
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs))
        return SimpleNamespace(stdout=json.dumps('C:/图片/参考'))
    monkeypatch.setattr('supervisor.setup.subprocess.run',run)
    assert choose_folder('C:/旧目录')=='C:/图片/参考'
    assert json.loads(calls[0][1]['input'])=='C:/旧目录'
    assert calls[0][1]['encoding']=='utf-8' and calls[0][1]['check']
    monkeypatch.setattr('supervisor.setup.subprocess.run',lambda *a,**k:SimpleNamespace(stdout='""'))
    assert choose_folder('C:/旧目录')=='C:/旧目录'
