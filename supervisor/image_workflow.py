"""Read ComfyUI execution graphs from original image metadata."""
import json
from pathlib import Path

from PIL import Image, UnidentifiedImageError


class ImageWorkflowError(ValueError):
    pass


def _text(value):
    if isinstance(value, bytes):
        if value.startswith(b'ASCII\x00\x00\x00'):
            value=value[8:]
        value=value.decode('utf-8',errors='replace')
    return value if isinstance(value,str) and len(value)<=2_000_000 else None


def image_workflow(image):
    if not image:
        raise ImageWorkflowError('请先上传包含 ComfyUI 工作流元数据的原始图片。')
    path=Path(image)
    if path.stat().st_size>100_000_000:
        raise ImageWorkflowError('图片文件超过 100 MB。')
    try:
        with Image.open(path) as source:
            if source.format not in ('PNG','WEBP','JPEG'):
                raise ImageWorkflowError('请上传 PNG、WebP 或 JPEG 原图。')
            values={key:_text(source.info.get(key)) for key in ('prompt','workflow')}
            exif=source.getexif() if source.format!='PNG' or 'exif' in source.info else {}
            entries=list(exif.values())
            if 0x8769 in exif:
                entries+=list(exif.get_ifd(0x8769).values())
            for value in entries:
                text=_text(value)
                if text:
                    for key in values:
                        if text.startswith(key+':'):
                            values[key]=values[key] or text[len(key)+1:].strip()
        with Image.open(path) as source:
            source.verify()
    except (UnidentifiedImageError,OSError,SyntaxError):
        raise ImageWorkflowError('无法读取图片，请上传未损坏的原始图片。') from None
    layout=False
    for key in ('prompt','workflow'):
        if not values[key]:
            continue
        try:
            graph=json.loads(values[key])
        except (ValueError,TypeError,RecursionError):
            continue
        if isinstance(graph,dict) and isinstance(graph.get('nodes'),list):
            layout=True
        if isinstance(graph,dict) and 0<len(graph)<=10_000 and all(
            isinstance(node_id,str) and isinstance(node,dict)
            and isinstance(node.get('class_type'),str) and node['class_type'].strip()
            and isinstance(node.get('inputs'),dict) for node_id,node in graph.items()):
            return graph
    if layout:
        raise ImageWorkflowError('图片有编辑器工作流，但缺少可执行的生图数据，无法自动导入。请使用保留完整元数据的原图。')
    if any(values.values()):
        raise ImageWorkflowError('图片中的工作流元数据无效，无法识别。')
    raise ImageWorkflowError('图片没有 ComfyUI 工作流元数据。请使用 ComfyUI 保存的原图，截图或转存图片可能没有元数据。')
