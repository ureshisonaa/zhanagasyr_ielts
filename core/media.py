import json
import os
import subprocess
import tempfile
from pathlib import Path
from PIL import Image
from django.core.exceptions import ValidationError


def inspect_upload(upload, audio_only=False, max_seconds=7200):
    if not upload.size or upload.size > 25 * 1024 * 1024:
        raise ValidationError('Файл должен быть непустым и не больше 25 МБ.')
    ext = Path(upload.name).suffix.lower()
    image_types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
    audio_types = {'.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.ogg': 'audio/ogg', '.webm': 'audio/webm', '.m4a': 'audio/mp4'}
    if not audio_only and ext in image_types:
        try:
            img = Image.open(upload)
            if img.width * img.height > 20_000_000 or img.format not in ('PNG', 'JPEG', 'WEBP'):
                raise ValueError()
            expected = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}[img.format]
            if expected != image_types[ext]:
                raise ValueError()
            img.verify()
            return expected
        except Exception as exc:
            raise ValidationError('Изображение повреждено или имеет неподдерживаемый формат.') from exc
        finally:
            upload.seek(0)
    if ext not in audio_types:
        raise ValidationError('Неподдерживаемое расширение файла.')
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            temp_path = tmp.name
            for chunk in upload.chunks():
                tmp.write(chunk)
        result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_streams', '-show_format', '-of', 'json', temp_path], capture_output=True, timeout=15, check=True)
        info = json.loads(result.stdout)
        streams = info.get('streams', [])
        if not streams or any(s.get('codec_type') != 'audio' for s in streams):
            raise ValueError()
        if float(info.get('format', {}).get('duration', 0)) > max_seconds:
            raise ValidationError('Запись превышает разрешённую продолжительность.')
        formats = set(info.get('format', {}).get('format_name', '').split(','))
        expected = {'.mp3': 'mp3', '.wav': 'wav', '.ogg': 'ogg', '.webm': 'webm', '.m4a': 'mov'}
        if expected[ext] not in formats:
            raise ValueError()
        return audio_types[ext]
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        raise ValidationError('Не удалось проверить аудио. Используйте корректный аудиофайл; на сервере требуется ffprobe.') from exc
    finally:
        upload.seek(0)
        if temp_path:
            os.unlink(temp_path)
