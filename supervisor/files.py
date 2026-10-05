from __future__ import annotations

import hashlib
import io
import json
import os
import random
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .db import Database, now, uid
from .models import dump_json, TaskSettings

Image.MAX_IMAGE_PIXELS = 40_000_000


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uid() + ".tmp")
    try:
        with temp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def safe_path(root: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts or ":" in relative:
        raise ValueError("Path must be relative to managed root")
    root = root.resolve()
    target = root / raw
    cursor = root
    for part in raw.parts:
        cursor = cursor / part
        if cursor.exists() or cursor.is_symlink():
            info = cursor.lstat()
            if cursor.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
                raise ValueError("Links and reparse points are not permitted")
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Path escapes managed root")
    return resolved


def thumbnail_bytes(path: Path, edge=128, fmt="JPEG") -> bytes:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        stream = io.BytesIO()
        image.save(stream, format=fmt, quality=85)
        return stream.getvalue()


def metadata(path: Path) -> dict:
    with Image.open(path) as image:
        raw = {k: v for k, v in image.info.items() if k in ("prompt", "workflow") and isinstance(v, str) and len(v) < 2_000_000}
    result = {"raw": raw, "extracted": {}, "warnings": []}
    if "prompt" not in raw:
        result["warnings"].append("No executable PNG prompt metadata")
        return result
    try:
        graph = json.loads(raw["prompt"])
        if not isinstance(graph, dict) or any(not isinstance(n, dict) or not isinstance(n.get("inputs", {}), dict) for n in graph.values()):
            raise ValueError("Graph is not an object")
        outputs = [k for k, v in graph.items() if isinstance(v, dict) and v.get("class_type") == "SaveImage"]
        visited = set()

        def visit(node):
            if node in visited or node not in graph:
                return
            visited.add(node)
            for value in graph[node].get("inputs", {}).values():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    visit(value[0])

        for output in outputs:
            visit(output)
        samplers = [k for k in visited if graph[k].get("class_type") == "KSampler"]
        if len(samplers) != 1:
            result["warnings"].append("Ambiguous or unsupported sampler graph; manual mapping required")
            return result
        sampler_id = samplers[0]
        inputs = graph[sampler_id]["inputs"]
        extracted = result["extracted"]
        for key in ("seed", "steps", "cfg", "sampler_name", "scheduler"):
            if key in inputs and not isinstance(inputs[key], list):
                extracted["sampler" if key == "sampler_name" else key] = {"value": inputs[key], "node_id": sampler_id, "source": "metadata"}
        for label in ("positive", "negative"):
            connection = inputs.get(label)
            if isinstance(connection, list) and connection[0] in graph:
                node = graph[connection[0]]
                if node.get("class_type") == "CLIPTextEncode" and isinstance(node.get("inputs", {}).get("text"), str):
                    extracted[label] = {"value": node["inputs"]["text"], "node_id": connection[0], "source": "metadata"}
                elif node.get("class_type") == "CLIPTextEncode":
                    from .setup import resolve_workflow_text
                    try:
                        text = resolve_workflow_text(graph, node.get("inputs", {}).get("text"))
                        extracted[label] = {"value": text, "node_id": connection[0], "source": "metadata"}
                    except ValueError:
                        result["warnings"].append("Linked prompt requires manual interpretation")
        for class_type, fields in [("EmptyLatentImage", ["width", "height", "batch_size"]), ("EmptySD3LatentImage", ["width", "height", "batch_size"]), ("CheckpointLoaderSimple", ["ckpt_name"])]:
            nodes = [k for k in visited if graph[k].get("class_type") == class_type]
            if len(nodes) == 1:
                for field in fields:
                    value = graph[nodes[0]].get("inputs", {}).get(field)
                    if value is not None and not isinstance(value, list):
                        extracted["model" if field == "ckpt_name" else field] = {"value": value, "node_id": nodes[0], "source": "metadata"}
        result["loras"] = [{"node_id": k, **{f: graph[k]["inputs"].get(f) for f in ["lora_name", "strength_model", "strength_clip"]}} for k in sorted(visited) if graph[k].get("class_type") == "LoraLoader"]
    except (ValueError, TypeError, KeyError, RecursionError):
        result["warnings"].append("Malformed or unsupported prompt metadata")
    return result


def sample_folder(folder: Path, count: int, seed: int):
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
    return files, random.Random(seed).sample(files, min(count, len(files)))


def reference_source_inputs(files, directory, folder):
    if isinstance(directory,dict):
        source = directory.get('source')
        if source == 'images':
            return files,[],''
        if source == 'folder':
            return [],[],folder
        raise ValueError('请选择图片或文件夹作为参考来源')
    return files,directory,folder


def validate_sample_count(count):
    try:
        value = int(count)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('参考图数量须为大于等于 1 的整数') from None
    if isinstance(count,bool) or value < 1 or (not isinstance(count,str) and count != value):
        raise ValueError('参考图数量须为大于等于 1 的整数')
    return value


def reference_candidates(files, directory, folder):
    files,directory,folder = reference_source_inputs(files,directory,folder)
    candidates = [Path(path) for path in list(files or []) + list(directory or [])]
    if folder:
        candidates.extend(path for path in Path(folder).rglob("*") if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and path.is_file())
    candidates = sorted(set(path for path in candidates if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")))
    return candidates


def update_reference_sample(files, directory, folder, seed, count=15, force=False):
    count = validate_sample_count(count)
    candidates = reference_candidates(files,directory,folder)
    previous = directory.get('_sample',{}) if isinstance(directory,dict) else {}
    names = [str(path) for path in candidates]
    # Reuse a pool as well as its current subset, so 15→20→10→15 preserves
    # previously selected images and their cached tags without duplicates.
    matching = previous.get('candidates') == names
    pool = list(previous.get('pool',[])) if matching and not force else []
    pool = list(dict.fromkeys(name for name in pool if name in names))
    wanted = min(count,len(candidates))
    if len(pool)<wanted:
        available = [name for name in names if name not in set(pool)]
        pool.extend(random.Random(seed).sample(available,wanted-len(pool)))
    if force and 0<wanted<len(names) and pool[:wanted]==previous.get('selected'):
        replacement = next(name for name in names if name not in pool)
        pool[wanted-1] = replacement
    sample = {'candidates':names,'pool':pool,'selected':pool[:wanted],'seed':seed,'count':count}
    return [Path(name) for name in sample['selected']], len(candidates), sample


def studio_reference_paths(files, directory, folder, seed, count=15):
    selected,total,_ = update_reference_sample(files,directory,folder,seed,count)
    return selected,total


class FileStore:
    def __init__(self, root: Path, db: Database):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = db

    def path(self, relative):
        return safe_path(self.root, relative)

    def import_image(self, task_id, source: Path, content_label, group_id=None, generation_id=None, identity=None):
        if source.stat().st_size > 100_000_000:
            raise ValueError("Image exceeds 100MB input limit")
        with Image.open(source) as image:
            image.load()
            width, height = image.size
            if image.format not in ("PNG", "JPEG", "WEBP"):
                raise ValueError("Unsupported image format")
            extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[image.format]
        asset_id = uid()
        relative = f"tasks/{task_id}/images/{asset_id}{extension}"
        target = self.path(relative)
        atomic_write(target, source.read_bytes())
        thumb = f"tasks/{task_id}/thumbnails/{asset_id}.jpg"
        atomic_write(self.path(thumb), thumbnail_bytes(target))
        info = metadata(target)
        self.db.execute("INSERT INTO assets(id,task_id,group_id,generation_id,source_kind,path,thumb_path,sha256,width,height,metadata,content_label,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (asset_id, task_id, group_id, generation_id, "generated" if generation_id else "reference", relative, thumb, sha256(target), width, height, dump_json(info), content_label, now()))
        if not generation_id:
            self.db.execute('INSERT INTO asset_sources VALUES(?,?)',(asset_id,str(source.resolve())))
        return self.db.one("SELECT * FROM assets WHERE id=?", (asset_id,))

    def delete_generated(self, task_id, asset_id):
        asset = self.db.one("SELECT * FROM assets WHERE id=? AND task_id=?", (asset_id,task_id))
        if not asset or asset['source_kind'] != 'generated':
            raise ValueError('只能删除当前任务的生成图片。')
        pending = self.db.one('SELECT * FROM user_deletions WHERE asset_id=?',(asset_id,))
        if pending:
            self._apply_user_delete(pending)
            return
        if asset['state'] not in ('AVAILABLE','QUARANTINED'):
            raise ValueError('图片已删除或不可用。')
        generation = self.db.one('SELECT state FROM generations WHERE id=?',(asset['generation_id'],))
        if generation and generation['state'] not in ('DECIDED','FAILED'):
            raise ValueError('这张图片正在处理，请处理完成后再删除。')
        if not asset['path'].startswith(f'tasks/{task_id}/'):
            raise ValueError('图片不在当前任务目录内。')
        specs = []
        def add(root, relative, expected=None):
            path = safe_path(root,relative)
            if path.is_file():
                actual = sha256(path)
                if expected and actual != expected:
                    raise ValueError('图片文件已被修改，未执行删除。')
                specs.append({'root':str(root.resolve()),'relative':relative,'sha256':actual})
        add(self.root,asset['path'],asset['sha256'])
        if asset['thumb_path']:
            add(self.root,asset['thumb_path'])
        settings = json.loads(self.db.one('SELECT settings FROM tasks WHERE id=?',(task_id,))['settings'])
        group = self.db.one('SELECT ordinal FROM groups WHERE id=?',(asset['group_id'],))
        if group:
            folder = f"group_{group['ordinal']+1:02d}"
            roots = [(self.root,f'tasks/{task_id}/delivery/{folder}')]
            if settings.get('export_folder'):
                if settings.get('delivery_layout')=='flat':
                    root=Path(settings['export_folder'])
                    for path in root.glob(f'{task_id[:8]}_{folder}_*_{asset_id}{Path(asset["path"]).suffix}'):
                        add(root,path.name,asset['sha256'])
                else:
                    roots.append((Path(settings['export_folder']),f'task_{task_id}/{folder}'))
            for root,relative in roots:
                for path in safe_path(root,relative).glob('*'):
                    if path.name.endswith('_'+asset_id+Path(asset['path']).suffix):
                        add(root,relative+'/'+path.name,asset['sha256'])
        reference_sources = {row['source_path'] for row in self.db.rows('SELECT source_path FROM asset_sources')}
        for path in self.path('ui-cache').rglob(Path(asset['path']).name):
            if str(path.resolve()) in reference_sources:
                continue
            if path.is_file() and not path.is_symlink() and sha256(path)==asset['sha256']:
                add(self.root,path.relative_to(self.root).as_posix(),asset['sha256'])
        with self.db.transaction() as c:
            c.execute('INSERT INTO user_deletions(asset_id,task_id,files,created_at) VALUES(?,?,?,?)',(asset_id,task_id,dump_json(specs),now()))
            c.execute("UPDATE assets SET state='DELETING' WHERE id=?",(asset_id,))
        self._apply_user_delete(self.db.one('SELECT * FROM user_deletions WHERE asset_id=?',(asset_id,)))

    def _apply_user_delete(self, job):
        if job['state']=='committed':
            return
        for spec in json.loads(job['files']):
            path = safe_path(Path(spec['root']),spec['relative'])
            if path.is_file():
                if sha256(path)!=spec['sha256']:
                    raise ValueError('图片文件已被修改，未继续删除。')
                path.unlink()
        self.db.execute("UPDATE assets SET state='DELETED',protected=0 WHERE id=?",(job['asset_id'],))
        self._prune_delivery(job['task_id'],job['asset_id'])
        from types import SimpleNamespace
        from .delivery import export_image_zip
        settings=TaskSettings.model_validate_json(self.db.one('SELECT settings FROM tasks WHERE id=?',(job['task_id'],))['settings'])
        zip_root=Path(settings.export_folder).resolve() if settings.export_folder else self.path(f'tasks/{job["task_id"]}/delivery')
        if safe_path(zip_root,f'task_{job["task_id"]}_images.zip').is_file():
            export_image_zip(SimpleNamespace(settings=lambda _:settings,files=self,db=self.db),job['task_id'],allow_empty=True)
        with self.db.transaction() as c:
            c.execute('DELETE FROM delivery_items WHERE asset_id=?',(job['asset_id'],))
            c.execute("INSERT INTO decisions VALUES(?,?,NULL,'USER_DELETED','USER_DELETE','user',?)",(uid(),job['asset_id'],now()))
            c.execute("UPDATE user_deletions SET state='committed' WHERE asset_id=?",(job['asset_id'],))
        self.db.event(job['task_id'],'USER_IMAGE_DELETED',{'asset_id':job['asset_id']})

    def _prune_delivery(self, task_id, asset_id):
        # Derived manifests/contact sheets must not keep the deleted image.
        settings = json.loads(self.db.one('SELECT settings FROM tasks WHERE id=?',(task_id,))['settings'])
        roots = [(self.root,f'tasks/{task_id}/delivery')]
        if settings.get('export_folder') and settings.get('delivery_layout','task_folder')=='task_folder':
            roots.append((Path(settings['export_folder']),f'task_{task_id}'))
        for root,prefix in roots:
            manifest_path = safe_path(root,prefix+'/manifest.json')
            if not manifest_path.is_file():
                continue
            body = json.loads(manifest_path.read_bytes())
            paths,labels = [],[]
            for group in body.get('groups',[]):
                group['items'] = [item for item in group.get('items',[]) if item['asset_id']!=asset_id]
                count = len(group['items'])
                group.update(saved_count=count,qualified_count=count if settings.get('review_enabled',True) else 0,
                             unreviewed_count=0 if settings.get('review_enabled',True) else count,
                             shortfall=max(0,group['target_count']-count))
                for item in group['items']:
                    asset = self.db.one("SELECT path FROM assets WHERE id=? AND state='AVAILABLE'",(item['asset_id'],))
                    if asset and self.path(asset['path']).is_file():
                        paths.append(self.path(asset['path']))
                        labels.append(f"Group {group['ordinal']}")
            body['user_deleted_images'] = list(dict.fromkeys(body.get('user_deleted_images',[])+[asset_id]))
            for index,name in enumerate(body.get('contact_sheets',[])):
                sheet = safe_path(root,prefix+'/'+name)
                if sheet.is_file():
                    contact_sheet(paths[index*16:index*16+16],labels[index*16:index*16+16],sheet)
            atomic_write(manifest_path,dump_json(body).encode('utf-8'))

    def quarantine(self, asset, evaluation, reason, days=7, kind="quarantine", allow_permanent=False):
        asset = self.db.one("SELECT * FROM assets WHERE id=?", (asset["id"],))
        latest = self.db.one("SELECT action FROM decisions WHERE asset_id=? ORDER BY rowid DESC LIMIT 1", (asset["id"],))
        if asset["source_kind"] != "generated" or asset["protected"] or asset["state"] != "AVAILABLE" or (latest and latest["action"] == "ACCEPTED"):
            raise ValueError("Asset is protected or not a managed generation")
        if kind == "direct" and not allow_permanent:
            raise ValueError("Permanent deletion not enabled")
        prefix = f"tasks/{asset['task_id']}/"
        if not asset["path"].startswith(prefix):
            raise ValueError("Asset belongs to another task root")
        source = self.path(asset["path"])
        if not source.is_file() or sha256(source) != asset["sha256"]:
            raise ValueError("Asset changed before operation")
        audit_dir = self.path(f"tasks/{asset['task_id']}/audit/{asset['id']}")
        atomic_write(audit_dir / "thumbnail.jpg", thumbnail_bytes(source))
        audit = {"asset_id": asset["id"], "sha256": asset["sha256"], "bytes": source.stat().st_size, "reason": reason, "evaluation": evaluation, "rubric_version": "1", "rule_version": "1", "created_at": now()}
        atomic_write(audit_dir / "audit.json", dump_json(audit).encode("utf-8"))
        json.loads((audit_dir / "audit.json").read_text(encoding="utf-8"))
        with Image.open(audit_dir / "thumbnail.jpg") as image:
            image.verify()
        target_rel = f"tasks/{asset['task_id']}/quarantine/{source.name}"
        operation_id = uid()
        until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        self.db.execute("INSERT INTO file_operations VALUES(?,?,?,?,?,?, 'planned',?,?)", (operation_id, asset["id"], kind, asset["path"], target_rel if kind != "direct" else None, asset["sha256"], until, now()))
        if kind == "direct":
            source.unlink()
        else:
            target = self.path(target_rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError("Quarantine target already exists")
            os.replace(source, target)
        self.db.execute("UPDATE file_operations SET state='applied' WHERE id=?", (operation_id,))
        with self.db.transaction() as c:
            c.execute("UPDATE assets SET state=?,path=? WHERE id=?", ("DELETED" if kind == "direct" else "QUARANTINED", asset["path"] if kind == "direct" else target_rel, asset["id"]))
            c.execute("UPDATE file_operations SET state='committed' WHERE id=?", (operation_id,))
        self.db.event(asset["task_id"], "FILE_OPERATION", {"asset_id": asset["id"], "kind": kind})

    def restore(self, asset_id):
        asset = self.db.one("SELECT * FROM assets WHERE id=?", (asset_id,))
        if not asset or asset["state"] != "QUARANTINED":
            raise ValueError("Full image is not available for restoration")
        source = self.path(asset["path"])
        if sha256(source) != asset["sha256"]:
            raise ValueError("Quarantine hash mismatch")
        dest_rel = f"tasks/{asset['task_id']}/images/restored_{asset_id}{source.suffix}"
        dest = self.path(dest_rel)
        if dest.exists():
            raise ValueError("Restore destination occupied")
        op_id = uid()
        self.db.execute("INSERT INTO file_operations VALUES(?,?,?,?,?,?,'planned',NULL,?)", (op_id, asset_id, "restore", asset["path"], dest_rel, asset["sha256"], now()))
        os.replace(source, dest)
        self.db.execute("UPDATE file_operations SET state='applied' WHERE id=?", (op_id,))
        with self.db.transaction() as c:
            c.execute("UPDATE assets SET path=?,state='AVAILABLE',protected=1 WHERE id=?", (dest_rel, asset_id))
            c.execute("UPDATE file_operations SET state='committed' WHERE id=?", (op_id,))
        self.db.event(asset["task_id"], "RESTORED", {"asset_id": asset_id})

    def recover(self):
        import time
        for job in self.db.rows("SELECT * FROM user_deletions WHERE state='pending'"):
            try:
                self._apply_user_delete(job)
            except (OSError,ValueError):
                self.db.event(job['task_id'],'USER_DELETE_RETRY_REQUIRED',{'asset_id':job['asset_id']})
        for op in self.db.rows("SELECT * FROM file_operations WHERE state IN ('planned','applied')"):
            source = self.path(op["source"])
            target = self.path(op["target"]) if op["target"] else None
            asset = self.db.one("SELECT * FROM assets WHERE id=?", (op["asset_id"],))
            task = self.db.one("SELECT lease_until FROM tasks WHERE id=?", (asset["task_id"],))
            if task["lease_until"] and task["lease_until"] > time.time():
                continue
            audit = self.path(f"tasks/{asset['task_id']}/audit/{asset['id']}/audit.json")
            completed = bool(target and target.is_file() and not source.exists() and sha256(target) == op["expected_hash"])
            completed |= op["kind"] == "direct" and not source.exists() and audit.exists()
            if completed:
                state = "AVAILABLE" if op["kind"] == "restore" else "DELETED" if op["kind"] == "direct" else "QUARANTINED"
                self.db.execute("UPDATE assets SET path=?,state=?,protected=? WHERE id=?", (op["target"] or op["source"], state, 1 if op["kind"] == "restore" else asset["protected"], asset["id"]))
                self.db.execute("UPDATE file_operations SET state='committed' WHERE id=?", (op["id"],))
            elif source.is_file() and (not target or not target.exists()) and sha256(source) == op["expected_hash"]:
                self.db.execute("UPDATE file_operations SET state='failed' WHERE id=?", (op["id"],))
            else:
                self.db.event(asset["task_id"], "FILE_RECONCILE_REQUIRED", {"operation_id": op["id"]})

    def cleanup_due(self, task_id):
        import json
        from .models import TaskSettings
        task = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        settings = TaskSettings.model_validate(json.loads(task["settings"]))
        if not settings.allow_permanent_delete or not settings.calibrated or settings.delete_mode != "delayed":
            raise ValueError("Delayed permanent deletion not enabled")
        count = 0
        for op in self.db.rows("SELECT f.*,a.path,a.protected,a.state asset_state FROM file_operations f JOIN assets a ON a.id=f.asset_id WHERE a.task_id=? AND f.kind='delayed' AND f.state='committed'", (task_id,)):
            if op["asset_state"] != "QUARANTINED" or op["protected"] or op["restore_until"] > now():
                continue
            path = self.path(op["path"])
            if not path.is_file() or sha256(path) != op["expected_hash"]:
                raise ValueError("Cleanup asset hash mismatch")
            audit = self.path(f"tasks/{task_id}/audit/{op['asset_id']}/audit.json")
            thumb = audit.with_name("thumbnail.jpg")
            if not audit.is_file() or not thumb.is_file() or json.loads(audit.read_text(encoding="utf-8")).get("sha256") != op["expected_hash"]:
                raise ValueError("Cleanup audit missing")
            deletion = uid()
            self.db.execute("INSERT INTO file_operations VALUES(?,?,?,?,NULL,?,'planned',NULL,?)", (deletion, op["asset_id"], "direct", op["path"], op["expected_hash"], now()))
            path.unlink()
            self.db.execute("UPDATE assets SET state='DELETED' WHERE id=?", (op["asset_id"],))
            self.db.execute("UPDATE file_operations SET state='committed' WHERE id=?", (deletion,))
            count += 1
        return count


def contact_sheet(paths: list[Path], labels: list[str], target: Path):
    columns = min(4, max(1, len(paths)))
    rows = max(1, (len(paths) + columns - 1) // columns)
    image = Image.new("RGB", (columns * 280, rows * 310), "#f4f5f6")
    draw = ImageDraw.Draw(image)
    for i, (path, label) in enumerate(zip(paths, labels)):
        with Image.open(path) as source:
            tile = ImageOps.contain(source.convert("RGB"), (264, 270))
        x, y = (i % columns) * 280, (i // columns) * 310
        image.paste(tile, (x + (280 - tile.width) // 2, y + 8))
        draw.text((x + 8, y + 284), label, fill="#222222")
    if not paths:
        draw.text((16, 16), "No qualified images", fill="#222222")
    stream = io.BytesIO()
    image.save(stream, format="JPEG", quality=90)
    atomic_write(target, stream.getvalue())
