import copy
from datetime import timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Attempt, Assignment, SectionRun, Review, AuditLog, TestVersion, MediaAsset
from .exams import auto_score, manual_band, half_band, validate_content


def audit(actor, action, target, **metadata):
    return AuditLog.objects.create(actor=actor, action=action, target=str(target), metadata=metadata)


def visible_attempts(user):
    qs = Attempt.objects.select_related('assignment__student', 'assignment__teacher', 'assignment__version__exam').prefetch_related('runs')
    if user.is_manager:
        return qs
    if user.role == 'teacher':
        return qs.filter(assignment__teacher=user)
    return qs.filter(assignment__student=user)


def can_review(user, attempt):
    return user.is_manager or (user.role == 'teacher' and attempt.assignment.teacher_id == user.id)


@transaction.atomic
def publish_version(user, version_id):
    version = TestVersion.objects.select_for_update().get(pk=version_id)
    if not user.is_manager:
        raise PermissionDenied
    if version.published_at:
        return version
    validate_content(version.content)
    for section in version.content['sections']:
        if section['kind'] == 'listening' and not section.get('audio_id'):
            raise ValidationError('Загрузите аудио Listening перед публикацией.')
        assets = [(section.get('audio_id'), 'audio/')]
        assets.extend((q.get('image_id'), 'image/') for q in section['questions'])
        assets.extend((m.get('id'), 'image/') for m in section.get('materials', []))
        for asset_id, media_type in assets:
            if asset_id and not MediaAsset.objects.filter(pk=asset_id, version=version, content_type__startswith=media_type).exists():
                raise ValidationError('Материал должен принадлежать этой версии и иметь правильный формат.')
    version.published_at = timezone.now()
    version.save()
    audit(user, 'version.published', version.pk)
    return version


@transaction.atomic
def start_attempt(user, assignment_id):
    assignment = Assignment.objects.select_for_update().select_related('version').get(pk=assignment_id, student=user)
    existing = assignment.attempts.filter(status='active').first()
    if existing:
        expire_locked(existing)
        return existing
    now = timezone.now()
    if not assignment.version.published_at or not assignment.opens_at <= now < assignment.due_at:
        raise ValidationError('Экзамен сейчас недоступен.')
    count = assignment.attempts.count()
    if count >= assignment.attempts_allowed:
        raise ValidationError('Лимит попыток исчерпан.')
    attempt = Attempt.objects.create(assignment=assignment, number=count + 1)
    make_run(attempt, 0, now)
    audit(user, 'attempt.started', attempt.pk)
    return attempt


def make_run(attempt, index, started):
    section = attempt.assignment.version.content['sections'][index]
    return SectionRun.objects.create(attempt=attempt, index=index, kind=section['kind'], started_at=started, deadline=min(started + timedelta(seconds=section['duration']), attempt.assignment.due_at))


def finish_run(attempt, run, finished):
    if run.submitted_at:
        return
    section = attempt.assignment.version.content['sections'][run.index]
    if run.kind in ('listening', 'reading'):
        run.raw_score, run.band = auto_score(section, run.responses)
    run.submitted_at = finished
    run.save()
    if run.index == 3:
        attempt.status = 'review'
        attempt.submitted_at = finished
    else:
        attempt.current_section = run.index + 1
        make_run(attempt, attempt.current_section, finished)
    attempt.save()


def expire_locked(attempt):
    now = timezone.now()
    while attempt.status == 'active':
        run = attempt.runs.get(index=attempt.current_section)
        if now < run.deadline:
            break
        finish_run(attempt, run, run.deadline)
    return attempt


@transaction.atomic
def refresh_attempt(attempt_id):
    attempt = Attempt.objects.select_for_update().select_related('assignment__version').get(pk=attempt_id)
    return expire_locked(attempt)


class SaveConflict(Exception):
    pass


@transaction.atomic
def save_answers(user, attempt_id, index, revision, responses, submit=False):
    attempt = Attempt.objects.select_for_update().select_related('assignment__version').get(pk=attempt_id, assignment__student=user)
    expire_locked(attempt)
    # Expiration must commit even when refusing a late write.
    if attempt.status != 'active' or attempt.current_section != index:
        return {'closed': True, 'index': attempt.current_section}
    run = attempt.runs.get(index=index)
    if revision != run.revision:
        raise SaveConflict('Ответы изменились в другой вкладке. Сохраните локальную копию и обновите страницу.')
    section = attempt.assignment.version.content['sections'][index]
    valid = {q['id']: q for q in section['questions']}
    if not isinstance(responses, dict) or set(responses) - valid.keys():
        raise ValidationError('Некорректный набор ответов.')
    for key, value in responses.items():
        q = valid[key]
        if q['type'] == 'audio':
            raise ValidationError('Аудио загружается отдельно.')
        if q['type'] == 'multi':
            if not isinstance(value, list) or len(value) > 30 or any(not isinstance(x, str) or x not in q['options'] for x in value):
                raise ValidationError('Некорректные варианты ответа.')
        elif not isinstance(value, str) or len(value) > (30000 if q['type'] == 'essay' else 2000):
            raise ValidationError('Ответ превышает допустимый размер.')
        elif q['type'] in ('choice', 'matching', 'tfng', 'ynng') and value and value not in q['options']:
            raise ValidationError('Неизвестный вариант ответа.')
    run.responses = copy.deepcopy(responses)
    run.revision += 1
    run.save()
    if submit:
        finish_run(attempt, run, timezone.now())
        audit(user, 'section.submitted', attempt.pk, section=run.kind)
    return {'saved': True, 'revision': run.revision, 'closed': submit, 'index': attempt.current_section, 'server_now': timezone.now().isoformat()}


@transaction.atomic
def grade_run(user, run_id, criteria, feedback, final):
    parent_id = SectionRun.objects.values_list('attempt_id', flat=True).get(pk=run_id)
    attempt = Attempt.objects.select_for_update().select_related('assignment').get(pk=parent_id)
    run = SectionRun.objects.get(pk=run_id)
    if not can_review(user, attempt):
        raise PermissionDenied
    if attempt.status in ('active', 'void') or run.kind not in ('writing', 'speaking'):
        raise ValidationError('Эта работа недоступна для проверки.')
    if attempt.status == 'published' and not (user.role == 'super' or (user.is_manager and user.can_edit_published)):
        raise PermissionDenied('Изменение опубликованной оценки запрещено.')
    band = manual_band(run.kind, criteria)
    previous = str(run.band) if run.band is not None else None
    Review.objects.update_or_create(run=run, defaults={'grader': user, 'criteria': criteria, 'feedback': feedback, 'final': final})
    run.band = band if final else None
    run.save()
    runs = list(attempt.runs.all())
    complete = len(runs) == 4 and all(r.band is not None for r in runs)
    attempt.overall = half_band(sum(r.band for r in runs) / 4) if complete else None
    # An edit always returns the result to editorial review; publication is explicit.
    attempt.status = 'graded' if complete else 'review'
    attempt.published_at = None
    attempt.save()
    audit(user, 'grade.final' if final else 'grade.draft', run.pk, previous=previous, current=str(run.band), criteria=criteria)
    return run


@transaction.atomic
def publish_result(user, attempt_id):
    if not user.is_manager:
        raise PermissionDenied
    attempt = Attempt.objects.select_for_update().get(pk=attempt_id)
    if attempt.status != 'graded' or attempt.overall is None:
        raise ValidationError('Сначала завершите проверку всех четырёх секций.')
    attempt.status = 'published'
    attempt.published_at = timezone.now()
    attempt.save()
    audit(user, 'result.published', attempt.pk)
    return attempt
