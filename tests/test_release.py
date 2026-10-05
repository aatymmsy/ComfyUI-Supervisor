"""Check the exported source tree and installed CLI, without real services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from tools.build_release import ReleaseError, audit, build, release_files


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name', ['providers', 'workflow', 'evaluation', 'task'])
def test_schema_snapshots_match_generated_bytes(name):
    from supervisor.models import Evaluation, ProvidersConfig, TaskSettings, WorkflowConfig
    contracts = {'providers': ProvidersConfig, 'workflow': WorkflowConfig,
                 'evaluation': Evaluation, 'task': TaskSettings}
    generated = json.dumps(contracts[name].model_json_schema(), indent=2).encode('utf-8')
    assert (ROOT / 'schemas' / (name + '.schema.json')).read_bytes() == generated


def test_schema_checkout_keeps_lf_with_windows_git_settings(tmp_path):
    git = shutil.which('git')
    if not git:
        pytest.skip('Git not installed')
    subprocess.run([git, 'init', '--quiet', str(tmp_path)], check=True)
    subprocess.run([git, 'config', 'core.autocrlf', 'true'], cwd=tmp_path, check=True)
    shutil.copyfile(ROOT / '.gitattributes', tmp_path / '.gitattributes')
    schema = tmp_path / 'schemas' / 'task.schema.json'
    schema.parent.mkdir()
    content = b'{"type": "object"}\n'
    schema.write_bytes(content)
    subprocess.run([git, 'add', '.gitattributes', 'schemas/task.schema.json'], cwd=tmp_path, check=True)
    schema.write_bytes(content.replace(b'\n', b'\r\n'))
    subprocess.run([git, 'checkout-index', '--force', '--all'], cwd=tmp_path, check=True)
    assert schema.read_bytes() == content


def test_release_excludes_personal_state_and_contains_required_files(tmp_path):
    first = tmp_path / 'first.zip'
    second = tmp_path / 'second.zip'
    report = build(ROOT, first)
    assert report['audit'] == 'passed'
    assert build(ROOT, second)['sha256'] == report['sha256']
    with zipfile.ZipFile(first) as archive:
        names = {name.removeprefix('comfyui-supervisor/') for name in archive.namelist()}
        assert {'LICENSE', '.gitattributes', 'start.ps1', '.github/workflows/checks.yml', 'tools/build_release.py',
                'supervisor/image_workflow.py', 'config/providers.example.yaml', 'workflows/example-api.json'} <= names
        assert all('.local.' not in name and '__pycache__' not in name and not name.startswith(('data/', '.venv/', 'examples/')) for name in names)
        assert {name for name in names if name.startswith('workflows/')} == {'workflows/example-api.json'}
        assert {name for name in names if name.startswith('docs/screenshots/')} == {
            'docs/screenshots/workflow-image-import.png', 'docs/screenshots/prompt-image-workflow-option.png'}


def test_release_audit_reports_file_without_echoing_secret(tmp_path):
    secret = 'sk-' + 'testfixture' * 4
    source = tmp_path / 'module.py'
    source.write_text('key = ' + repr(secret), encoding='utf-8')
    with pytest.raises(ReleaseError) as failure:
        audit(tmp_path, [source])
    assert 'module.py' in str(failure.value) and secret not in str(failure.value)
    source.write_text('safe = True', encoding='utf-8')
    env = tmp_path / '.env.example'
    env.write_text('SUPERVISOR_API_KEY=nonempty-fixture', encoding='utf-8')
    with pytest.raises(ReleaseError, match='environment example'):
        audit(tmp_path, [env])


def test_missing_release_file_is_not_silently_skipped(tmp_path):
    with pytest.raises(ReleaseError, match='Missing release file'):
        release_files(tmp_path)


def test_gitignore_protects_runtime_files_but_keeps_templates(tmp_path):
    git = shutil.which('git')
    if not git:
        pytest.skip('Git not installed')
    subprocess.run([git, 'init', '--quiet', str(tmp_path)], check=True)
    shutil.copyfile(ROOT / '.gitignore', tmp_path / '.gitignore')
    private = ['config/last-run.local.json', 'config/presets.local.json', 'config/providers.local.yaml',
               'workflows/desktop-api.json', 'workflows/customized-fixture.json', 'workflows/snapshots/fixture.json',
               'data/supervisor.db', '.env', '.env.production', 'docs/screenshots/unreviewed.png']
    public = ['config/providers.example.yaml', 'workflows/example-api.json', '.env.example',
              'docs/screenshots/workflow-image-import.png', 'supervisor/engine.py']
    for name in private + public:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    result = subprocess.run([git, 'check-ignore', '--no-index', '-z', '--stdin'], cwd=tmp_path,
                            input=('\0'.join(private + public) + '\0').encode(), capture_output=True)
    assert result.returncode == 0 and set(result.stdout.decode().strip('\0').split('\0')) == set(private)


def test_installed_cli_defaults_to_current_directory(tmp_path):
    environment = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    result = subprocess.run([sys.executable, '-m', 'supervisor', 'status'], cwd=tmp_path, env=environment,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []
    assert (tmp_path / 'data/supervisor.db').is_file()
