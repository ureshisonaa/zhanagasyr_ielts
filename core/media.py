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
    try:
        import av
        with av.open(upload, options={'protocol_whitelist': 'file,pipe'}) as container:
            if not container.streams.audio or any(stream.type != 'audio' for stream in container.streams):
                raise ValueError()
            if container.duration and container.duration / av.time_base > max_seconds:
                raise ValidationError('Запись превышает разрешённую продолжительность.')
            formats = set(container.format.name.split(','))
            expected = {'.mp3': 'mp3', '.wav': 'wav', '.ogg': 'ogg', '.webm': 'webm', '.m4a': 'mov'}
            if expected[ext] not in formats:
                raise ValueError()
            # Decode at least one frame: container headers alone are insufficient.
            if next(container.decode(audio=0), None) is None:
                raise ValueError()
        return audio_types[ext]
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError('Не удалось проверить аудио. Используйте корректный аудиофайл.') from exc
    finally:
        upload.seek(0)
