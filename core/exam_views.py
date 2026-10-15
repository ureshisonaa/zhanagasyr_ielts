import json
import re
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse, FileResponse, StreamingHttpResponse, HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from .models import Attempt, SectionRun, MediaAsset
from .services import refresh_attempt, save_answers, SaveConflict, can_review, expire_locked
from .views import roles
from .exams import LABELS
from .media import inspect_upload


@roles('student')
def attempt_view(request, pk):
    attempt = get_object_or_404(Attempt, pk=pk, assignment__student=request.user)
    attempt = refresh_attempt(attempt.pk)
    if attempt.status != 'active':
        return redirect('result_detail', pk=pk)
    run = attempt.runs.get(index=attempt.current_section)
    section = attempt.assignment.version.content['sections'][run.index]
    # Explicit allowlist: never serialize answer keys or raw-to-band tables.
    questions = [{k: q.get(k, '') for k in ('id', 'type', 'prompt', 'options', 'image_id', 'max_words')} for q in section['questions']]
    for q in questions:
        q['value'] = run.responses.get(q['id'], '')
        q['recording'] = run.recordings.filter(question_id=q['id']).first()
    state = {'attempt': str(attempt.pk), 'section': run.index, 'kind': run.kind, 'revision': run.revision, 'responses': run.responses, 'server_now': timezone.now().isoformat(), 'deadline': run.deadline.isoformat(), 'started_at': run.started_at.isoformat(), 'save_url': f'/api/attempts/{pk}/answers/', 'state_url': f'/api/attempts/{pk}/state/'}
    return render(request, 'core/attempt.html', {'attempt': attempt, 'run': run, 'section_name': LABELS[run.kind], 'instructions': section.get('instructions', ''), 'passage': section.get('passage', ''), 'questions': questions, 'state': state, 'steps': list(LABELS.values()), 'has_audio': bool(section.get('audio_id')), 'replay': section.get('replay', False)})


@roles('student')
def state(request, pk):
    attempt = get_object_or_404(Attempt, pk=pk, assignment__student=request.user)
    attempt = refresh_attempt(attempt.pk)
    data = {'status': attempt.status, 'index': attempt.current_section, 'server_now': timezone.now().isoformat()}
    if attempt.status == 'active':
        run = attempt.runs.get(index=attempt.current_section)
        data.update(deadline=run.deadline.isoformat(), revision=run.revision)
    return JsonResponse(data)


@roles('student')
@require_POST
def save(request, pk):
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict) or type(data.get('index')) is not int or type(data.get('revision')) is not int or type(data.get('submit', False)) is not bool:
            raise ValueError()
        result = save_answers(request.user, pk, data['index'], data['revision'], data.get('responses'), data.get('submit', False))
        return JsonResponse(result)
    except Attempt.DoesNotExist:
        raise Http404
    except SaveConflict as exc:
        return JsonResponse({'error': str(exc), 'conflict': True}, status=409)
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        return JsonResponse({'error': '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'Некорректный запрос.'}, status=400)


@roles('student')
@require_POST
def listening_start(request, pk):
    with transaction.atomic():
        attempt = get_object_or_404(Attempt.objects.select_for_update().select_related('assignment__version'), pk=pk, assignment__student=request.user)
        expire_locked(attempt)
        if attempt.status != 'active' or attempt.current_section != 0:
            return JsonResponse({'error': 'Секция Listening завершена.'}, status=409)
        run = attempt.runs.get(index=0)
        section = attempt.assignment.version.content['sections'][0]
        if run.audio_started_at and not section.get('replay'):
            return JsonResponse({'error': 'Запись уже запускалась. При технической ошибке обратитесь к администратору.'}, status=409)
        run.audio_started_at = timezone.now()
        run.save(update_fields=['audio_started_at'])
        token = signing.dumps({'attempt': str(pk), 'user': request.user.pk, 'asset': section['audio_id'], 'session': request.user.session_version}, salt='listening')
    return JsonResponse({'url': f'/media/{section["audio_id"]}/?play={token}'})


@roles('student')
@require_POST
def recording_upload(request, pk, question_id):
    attempt = get_object_or_404(Attempt, pk=pk, assignment__student=request.user)
    upload = request.FILES.get('file')
    if not upload:
        return JsonResponse({'error': 'Выберите аудиозапись.'}, status=400)
    try:
        media_type = inspect_upload(upload, audio_only=True, max_seconds=900)
    except ValidationError as exc:
        return JsonResponse({'error': '; '.join(exc.messages)}, status=400)
    with transaction.atomic():
        attempt = Attempt.objects.select_for_update().select_related('assignment__version').get(pk=pk)
        expire_locked(attempt)
        if attempt.status != 'active' or attempt.current_section != 3:
            return JsonResponse({'error': 'Speaking завершён; запись не принята.'}, status=409)
        run = attempt.runs.get(index=3)
        questions = attempt.assignment.version.content['sections'][3]['questions']
        if question_id not in [q['id'] for q in questions]:
            raise Http404
        existing = run.recordings.filter(question_id=question_id).first()
        if existing:
            # Replacing one recording never creates duplicate answers. Old blob is retained for backup GC.
            existing.file = upload
            existing.content_type = media_type
            existing.name = upload.name[:180]
            existing.save()
            asset = existing
        else:
            asset = MediaAsset.objects.create(file=upload, name=upload.name[:180], content_type=media_type, owner=request.user, run=run, question_id=question_id)
    return JsonResponse({'saved': True, 'url': f'/media/{asset.pk}/'})


def ranged_file(request, asset):
    asset.file.open('rb')
    stream = asset.file.file
    size = asset.file.size
    range_header = request.headers.get('Range', '')
    if range_header:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
        if not match or not any(match.groups()):
            stream.close()
            return HttpResponse(status=416, headers={'Content-Range': f'bytes */{size}'})
        first, last = match.groups()
        start = int(first) if first else max(0, size - int(last))
        end = min(int(last), size - 1) if first and last else size - 1
        if start > end or start >= size:
            stream.close()
            return HttpResponse(status=416, headers={'Content-Range': f'bytes */{size}'})
        def chunks():
            try:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                stream.close()
        response = StreamingHttpResponse(chunks(), status=206, content_type=asset.content_type)
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
        response['Content-Length'] = str(end - start + 1)
    else:
        response = FileResponse(stream, content_type=asset.content_type)
        response['Content-Length'] = str(size)
    response['Accept-Ranges'] = 'bytes'
    response['Content-Disposition'] = 'inline'
    return response


@login_required
def private_media(request, pk):
    asset = get_object_or_404(MediaAsset.objects.select_related('version', 'run__attempt__assignment'), pk=pk)
    user = request.user
    allowed = user.is_manager
    if not allowed and asset.run_id:
        attempt = refresh_attempt(asset.run.attempt_id)
        if user.role == 'teacher':
            allowed = can_review(user, attempt) and attempt.status not in ('active', 'void')
        else:
            allowed = attempt.assignment.student_id == user.pk and attempt.status == 'active' and attempt.current_section == asset.run.index
    elif not allowed and asset.version_id:
        if user.role == 'teacher':
            allowed = Attempt.objects.filter(assignment__version=asset.version, assignment__teacher=user, status__in=['review', 'graded', 'published']).exists()
        elif user.role == 'student':
            attempt = Attempt.objects.filter(assignment__version=asset.version, assignment__student=user, status='active').first()
            if attempt:
                attempt = refresh_attempt(attempt.pk)
                if attempt.status == 'active':
                    section = asset.version.content['sections'][attempt.current_section]
                    allowed_ids = [q.get('image_id') for q in section['questions']]
                    if str(asset.pk) in allowed_ids and asset.content_type.startswith('image/'):
                        allowed = True
                    elif str(asset.pk) == section.get('audio_id'):
                        try:
                            token = signing.loads(request.GET.get('play', ''), salt='listening', max_age=section['duration'] + 120)
                            allowed = token == {'attempt': str(attempt.pk), 'user': user.pk, 'asset': str(asset.pk), 'session': user.session_version}
                        except signing.BadSignature:
                            allowed = False
    if not allowed:
        raise PermissionDenied
    try:
        return ranged_file(request, asset)
    except FileNotFoundError:
        raise Http404
