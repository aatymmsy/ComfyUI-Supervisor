"""Build an audited source ZIP using an explicit list of publishable files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
import zipfile


ROOT_FILES = ('.gitignore', '.gitattributes', '.env.example', 'LICENSE', 'README.md', 'pyproject.toml',
              'requirements-tested.txt', 'start.ps1', 'start.bat')
DOC_FILES = ('streamlined-studio.md', 'release.md')
SCREENSHOTS = ('workflow-image-import.png', 'prompt-image-workflow-option.png')
SCHEMAS = ('providers', 'workflow', 'evaluation', 'task')
SENSITIVE = (
    ('possible access key', re.compile(r'\b(?:sk-(?:proj-|or-v1-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16})\b')),
    ('private key', re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----')),
    ('personal home path', re.compile(r'[A-Za-z]:[\\/]+Users[\\/]+|' + '/' + r'Users/[^/\s]+/')),
    ('literal credential', re.compile(r'''["']?(?:api_key|access_token|password)["']?\s*[:=]\s*["'][^"'\r\n]{20,}["']''', re.I)),
)


class ReleaseError(ValueError):
    pass


def release_files(root: Path) -> list[Path]:
    root = root.resolve()
    files = [root / name for name in ROOT_FILES]
    files += [root / 'docs' / name for name in DOC_FILES]
    files += [root / 'docs/screenshots' / name for name in SCREENSHOTS]
    files += [root / 'schemas' / (name + '.schema.json') for name in SCHEMAS]
    files += [root / 'workflows/example-api.json']
    for folder, pattern in (('supervisor', '*.py'), ('tests', '*.py'), ('tools', '*.py'),
                            ('config', '*.example.yaml'), ('.github/workflows', '*.yml')):
        files += [path for path in (root / folder).rglob(pattern) if '__pycache__' not in path.parts]
    for path in files:
        relative = path.relative_to(root).as_posix()
        if not path.is_file():
            raise ReleaseError('Missing release file: ' + relative)
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ReleaseError('Release file points outside the project: ' + relative)
    return sorted(set(files))


def audit(root: Path, files: list[Path]) -> dict[str, bytes]:
    root = root.resolve()
    contents = {}
    issues = []
    for path in files:
        name = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        if len(raw) > 5_000_000:
            issues.append(name + ': exceeds the 5 MB source-file limit')
        if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
            text = raw.decode('utf-8-sig')
            for label, pattern in SENSITIVE:
                if pattern.search(text):
                    issues.append(name + ': ' + label)
            if name == '.env.example' and any(
                line.partition('=')[2].strip() for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith('#') and '=' in line
            ):
                issues.append(name + ': environment example must not contain values')
        contents[name] = raw
    if issues:
        # Never echo the suspected secret itself.
        raise ReleaseError('Release audit failed:\n' + '\n'.join(issues))
    return contents


def build(root: Path, output: Path | None = None, *, check=False) -> dict:
    root = root.resolve()
    files = release_files(root)
    contents = audit(root, files)
    version = tomllib.loads(contents['pyproject.toml'].decode('utf-8'))['project']['version']
    report = {'version': version, 'files': len(contents), 'source_bytes': sum(map(len, contents.values())),
              'audit': 'passed'}
    if check:
        return report
    output = (output or root / 'dist' / f'comfyui-supervisor-{version}-source.zip').resolve()
    if output.suffix.lower() != '.zip' or output in files:
        raise ReleaseError('Output must be a ZIP and must not overwrite a source file')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.release-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, raw in sorted(contents.items()):
                item = zipfile.ZipInfo('comfyui-supervisor/' + name)
                item.external_attr = 0o100644 << 16
                item.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(item, raw)
        os.replace(temporary, output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    report.update(archive=str(output), sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', action='store_true', help='Audit files without creating an archive')
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.root, args.output, check=args.check), ensure_ascii=False, indent=2))
    except (ReleaseError, UnicodeError, OSError, KeyError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
