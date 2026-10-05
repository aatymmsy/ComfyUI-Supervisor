from pathlib import Path
import shutil
import inspect

import pytest

from supervisor.engine import Supervisor


def ui_callbacks(app):
    """Find form handlers through their registered progress-stream wrappers."""
    callbacks={getattr(event.fn,'__name__',''):event.fn for event in app.fns.values() if event.fn}
    for form in ('studio','direct'):
        submit=callbacks.get(form+'_submit')
        if submit:
            handler=inspect.getclosurevars(submit).nonlocals[form+'_create']
            callbacks[form+'_create']=handler
    return callbacks


@pytest.fixture
def service(tmp_path):
    source = Path(__file__).resolve().parents[1]
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "workflows").mkdir()
    for path in (source / "config").glob("*.example.yaml"):
        shutil.copyfile(path, root / "config" / path.name)
    shutil.copyfile(source / "workflows/example-api.json", root / "workflows/example-api.json")
    instance = Supervisor(root, tmp_path / "data")
    yield instance
    instance.close()
