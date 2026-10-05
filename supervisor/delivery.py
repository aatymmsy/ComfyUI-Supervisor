"""Export to the chosen directory, with an optional image ZIP."""
import os
from pathlib import Path
import tempfile
import zipfile

from .files import safe_path,sha256


def export_destination(settings,task_id,relative):
    root=Path(settings.export_folder).resolve()
    path=Path(relative)
    if settings.delivery_layout=='flat' and path.parts[0].startswith('group_'):
        return safe_path(root,f'{task_id[:8]}_{path.parts[0]}_{path.name}')
    return safe_path(root,f'task_{task_id}/{relative}')


def delivered_image_count(service,task_id):
    """Count pictures that reached the user's chosen output, not staging copies."""
    settings=service.settings(task_id)
    local=service.files.path(f'tasks/{task_id}/delivery')
    if settings.delivery_format=='zip':
        root=Path(settings.export_folder).resolve() if settings.export_folder else local
        archive_path=safe_path(root,f'task_{task_id}_images.zip')
        try:
            with zipfile.ZipFile(archive_path) as archive:
                names={(name.split('/',1)[0],name.rsplit('_',1)[-1]) for name in archive.namelist() if name.count('/')==1}
        except (OSError,zipfile.BadZipFile):
            return 0
    else:
        names=None
    count=0
    seen=set()
    for group in service.db.rows('SELECT id,ordinal FROM groups WHERE task_id=? ORDER BY ordinal',(task_id,)):
        saved=0
        for asset in service.db.accepted(task_id,group['id']):
            if saved>=settings.per_group:
                break
            if settings.review_enabled and asset['sha256'] in seen:
                continue
            seen.add(asset['sha256'])
            saved+=1
            folder=f"group_{group['ordinal']+1:02d}"
            suffix=f"_{asset['id']}{Path(asset['path']).suffix}"
            if names is not None:
                count+=(folder,suffix[1:]) in names
            else:
                if settings.export_folder and settings.delivery_layout=='flat':
                    root=Path(settings.export_folder).resolve()
                    candidates=root.glob(f'{task_id[:8]}_{folder}_*{suffix}')
                else:
                    root=safe_path(Path(settings.export_folder).resolve(),f'task_{task_id}/{folder}') if settings.export_folder else safe_path(local,folder)
                    candidates=root.glob('*'+suffix)
                count+=any(path.is_file() for path in candidates)
    return count


def export_image_zip(service,task_id,*,allow_empty=False):
    settings=service.settings(task_id)
    root=Path(settings.export_folder).resolve() if settings.export_folder else service.files.path(f'tasks/{task_id}/delivery')
    root.mkdir(parents=True,exist_ok=True)
    target=safe_path(root,f'task_{task_id}_images.zip')
    images=[]
    seen=set()
    for group in service.db.rows('SELECT id,ordinal FROM groups WHERE task_id=? ORDER BY ordinal',(task_id,)):
        saved=0
        for asset in service.db.accepted(task_id,group['id']):
            if saved>=settings.per_group:
                break
            if settings.review_enabled and asset['sha256'] in seen:
                continue
            source=service.files.path(asset['path'])
            if not source.is_file() or sha256(source)!=asset['sha256']:
                raise ValueError('交付图片缺失或被修改，未导出压缩包。')
            seen.add(asset['sha256'])
            saved+=1
            images.append((source,f"group_{group['ordinal']+1:02d}/{saved:03d}_{asset['id']}{source.suffix}"))
    if not images and not allow_empty:
        raise ValueError('尚无已保存的交付图片可打包。')
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=root,prefix='.supervisor-zip-',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name)
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_STORED) as archive:
            for source,name in images:
                archive.write(source,name)
        os.replace(temporary,target)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return str(target)
