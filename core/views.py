import copy
import csv
import io
import json
from datetime import timedelta
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Avg, Q
from django.http import HttpResponse, JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from . import forms, services
from .auth_views import activation_link
from .exams import empty_content, LABELS, WRITING_CRITERIA, SPEAKING_CRITERIA
from .media import inspect_upload
from .models import User, StudyGroup, Exam, TestVersion, Assignment, Attempt, SectionRun, Review, MediaAsset, AuditLog


def roles(*allowed):
    def decorate(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.user.role not in allowed:
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def notify_error(request, exc):
    messages.error(request, '; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc))


def form_page(request, form, title, **extra):
    return render(request, 'core/form.html', {'form': form, 'title': title, 'submit': 'Сохранить', **extra})


def listing(request, title, columns, rows, **extra):
    return render(request, 'core/list.html', {'title': title, 'columns': columns, 'rows': rows, **extra})


@login_required
def dashboard(request):
    attempts = services.visible_attempts(request.user)
    for attempt_id in attempts.filter(status='active').values_list('id', flat=True):
        services.refresh_attempt(attempt_id)
    published = attempts.filter(status='published')
    average = published.aggregate(value=Avg('overall'))['value']
    if request.user.role == 'student':
        assignments = request.user.assignments.select_related('version__exam', 'teacher').order_by('due_at')
        upcoming = assignments.filter(due_at__gt=timezone.now())[:4]
        stats = [('Назначенные экзамены', assignments.count(), 'Ваш следующий шаг'), ('Завершённые попытки', attempts.exclude(status__in=['active', 'void']).count(), 'Практика приносит результат'), ('На проверке', attempts.filter(status__in=['review', 'graded']).count(), 'Обратная связь уже скоро'), ('Средний Overall', f'{average:.1f}' if average is not None else '—', 'Только опубликованные результаты')]
    else:
        assignments = Assignment.objects.all() if request.user.is_manager else Assignment.objects.filter(teacher=request.user)
        upcoming = []
        students = User.objects.filter(role='student', is_active=True) if request.user.is_manager else User.objects.filter(Q(assignments__teacher=request.user) | Q(study_groups__teacher=request.user), role='student', is_active=True).distinct()
        stats = [('Активные ученики', students.count(), 'Обучаются вместе с нами'), ('Назначения', assignments.count(), 'Индивидуально и по группам'), ('Ожидают проверки', attempts.filter(status='review').count(), 'Writing & Speaking'), ('Средний Overall', f'{average:.1f}' if average is not None else '—', 'По опубликованным работам')]
    history = list(published.order_by('published_at')[:12])
    chart = [{'label': a.published_at.strftime('%d.%m'), 'score': float(a.overall), 'title': a.assignment.version.exam.title} for a in history]
    sections = [{'name': LABELS[k], 'value': published.filter(runs__kind=k).aggregate(value=Avg('runs__band'))['value']} for k in LABELS]
    return render(request, 'core/dashboard.html', {'stats': stats, 'upcoming': upcoming, 'recent': attempts[:5], 'chart_data': chart, 'section_stats': sections, 'published_count': published.count()})


@roles('super', 'admin')
def users(request):
    qs = User.objects.all().order_by('first_name', 'username')
    search = request.GET.get('q', '')[:100]
    if search:
        qs = qs.filter(Q(username__icontains=search) | Q(first_name__icontains=search) | Q(email__icontains=search))
    rows = [{'cells': [u.display_name, u.email, u.get_role_display(), 'Активен' if u.is_active else 'Заблокирован'], 'url': reverse('user_edit', args=[u.pk]) if request.user.role == 'super' or u.role in ['student', 'teacher'] else ''} for u in qs[:200]]
    return listing(request, 'Пользователи', ['Имя', 'Email', 'Роль', 'Статус'], rows, create_url=reverse('user_create'), create_label='Добавить пользователя', search=search, description='Закрытый доступ: аккаунты создаёт только администратор. Показано до 200 записей; используйте поиск.')


@roles('super', 'admin')
def user_edit(request, pk=None):
    account = get_object_or_404(User, pk=pk) if pk else None
    if account and (account == request.user or (request.user.role != 'super' and account.role not in ('student', 'teacher'))):
        raise PermissionDenied('Для своего пароля используйте профиль. Аккаунты администраторов доступны главному администратору.')
    form = forms.UserForm(request.POST or None, instance=account, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            user = form.save(commit=False)
            if not pk:
                user.set_unusable_password()
            else:
                old = User.objects.select_for_update().get(pk=pk)
                if old.role != user.role and (old.assignments.exists() or old.assigned_reviews.exists() or old.teaching_groups.exists() or old.study_groups.exists()):
                    form.add_error('role', 'Нельзя менять роль аккаунта, связанного с группами или экзаменами. Создайте отдельный аккаунт.')
                    return form_page(request, form, 'Пользователь', account=account)
                user.session_version = old.session_version + 1
            user.save()
            services.audit(request.user, 'account.updated' if pk else 'account.created', user.pk, role=user.role)
        if not pk:
            return render(request, 'core/invitation.html', {'account': user, 'invite_url': activation_link(user)})
        messages.success(request, 'Пользователь сохранён.')
        return redirect('users')
    return form_page(request, form, 'Редактирование пользователя' if pk else 'Новый пользователь', account=account, description='После создания вы получите одноразовую ссылку активации на 24 часа.')


@roles('super', 'admin')
@require_POST
def user_action(request, pk):
    with transaction.atomic():
        user = get_object_or_404(User.objects.select_for_update(), pk=pk)
        if user == request.user or (request.user.role != 'super' and user.role not in ('student', 'teacher')):
            raise PermissionDenied
        if request.POST.get('action') == 'invite':
            if not user.is_active:
                raise PermissionDenied('Сначала восстановите доступ аккаунту.')
            # A monotonic version invalidates earlier links even within the same second.
            user.session_version += 1
            user.save(update_fields=['session_version'])
            services.audit(request.user, 'account.invitation_issued', user.pk)
            return render(request, 'core/invitation.html', {'account': user, 'invite_url': activation_link(user)})
        user.is_active = not user.is_active
        user.session_version += 1
        user.save()
        services.audit(request.user, 'account.unblocked' if user.is_active else 'account.blocked', user.pk)
    messages.success(request, 'Доступ обновлён. Старые сессии отозваны.')
    return redirect('users')


@roles('super', 'admin', 'teacher')
def groups(request):
    qs = StudyGroup.objects.select_related('teacher').prefetch_related('students')
    if request.user.role == 'teacher':
        qs = qs.filter(teacher=request.user)
    rows = [{'cells': [g.name, g.teacher.display_name, g.students.count(), g.description], 'url': reverse('group_edit', args=[g.pk]) if request.user.is_manager else ''} for g in qs]
    return listing(request, 'Учебные группы', ['Группа', 'Преподаватель', 'Учеников', 'Описание'], rows, create_url=reverse('group_create') if request.user.is_manager else '', create_label='Создать группу')


@roles('super', 'admin')
def group_edit(request, pk=None):
    group = get_object_or_404(StudyGroup, pk=pk) if pk else None
    form = forms.GroupForm(request.POST or None, instance=group)
    if request.method == 'POST' and form.is_valid():
        group = form.save()
        services.audit(request.user, 'group.saved', group.pk)
        messages.success(request, 'Группа сохранена. Проверяющий уже назначенных экзаменов не изменён.')
        return redirect('groups')
    return form_page(request, form, 'Редактирование группы' if pk else 'Новая группа')


@roles('super', 'admin')
def exam_bank(request):
    exams = Exam.objects.prefetch_related('versions').order_by('-created_at')
    return render(request, 'core/bank.html', {'exams': exams})


@roles('super', 'admin')
def exam_create(request):
    form = forms.ExamForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            exam = form.save(commit=False)
            exam.author = request.user
            exam.save()
            version = TestVersion.objects.create(exam=exam, content=empty_content())
            services.audit(request.user, 'exam.created', exam.pk)
        return redirect('version_edit', pk=version.pk)
    return form_page(request, form, 'Новый экзамен', description='Создайте IELTS Academic, затем заполните четыре секции и опубликуйте версию.')


@roles('super', 'admin')
def version_edit(request, pk):
    version = get_object_or_404(TestVersion.objects.select_related('exam'), pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'publish':
                services.publish_version(request.user, pk)
                messages.success(request, 'Версия опубликована. Теперь её можно назначить ученикам.')
            elif action == 'clone':
                with transaction.atomic():
                    exam = Exam.objects.select_for_update().get(pk=version.exam_id)
                    latest = exam.versions.order_by('-number').first()
                    cloned = TestVersion.objects.create(exam=exam, number=latest.number + 1, content=copy.deepcopy(version.content))
                    replacements = {}
                    for asset in version.assets.all():
                        new = MediaAsset.objects.create(file=asset.file.name, name=asset.name, content_type=asset.content_type, owner=request.user, version=cloned)
                        replacements[str(asset.pk)] = str(new.pk)
                    for section in cloned.content['sections']:
                        section['audio_id'] = replacements.get(section.get('audio_id'), '')
                        for material in section.get('materials', []):
                            material['id'] = replacements.get(material['id'], '')
                        for q in section['questions']:
                            q['material_id'] = replacements.get(q.get('material_id'), '')
                            q['image_id'] = replacements.get(q.get('image_id'), '')
                    cloned.save()
                    services.audit(request.user, 'version.cloned', cloned.pk, source=version.pk)
                return redirect('version_edit', pk=cloned.pk)
            elif action == 'archive':
                version.exam.archived = not version.exam.archived
                version.exam.save()
                services.audit(request.user, 'exam.archive_toggled', version.exam_id)
        except ValidationError as exc:
            notify_error(request, exc)
        return redirect('version_edit', pk=pk)
    sections = [{'index': i, 'label': LABELS[s['kind']], **s} for i, s in enumerate(version.content['sections'])]
    return render(request, 'core/version.html', {'version': version, 'sections': sections, 'upload_form': forms.UploadForm()})


@roles('super', 'admin')
def section_edit(request, pk, index):
    with transaction.atomic():
        version = get_object_or_404(TestVersion.objects.select_for_update(), pk=pk, published_at__isnull=True)
        if index not in range(4):
            raise Http404
        section = version.content['sections'][index]
        form = forms.SectionForm(request.POST or None, initial=section, version=version)
        if request.method == 'POST' and form.is_valid():
            data = form.cleaned_data
            data['audio_id'] = str(data['audio_id'].id) if data['audio_id'] else ''
            data['bands'] = data['bands'] or []
            section.update(data)
            version.save()
            services.audit(request.user, 'section.updated', pk, section=index)
            return redirect('version_edit', pk=pk)
    return form_page(request, form, f'Настройка {LABELS[section["kind"]]}', description='Время и правила сохраняются в этой версии экзамена.')


@roles('super', 'admin')
def question_edit(request, pk, index, question_id=None):
    with transaction.atomic():
        version = get_object_or_404(TestVersion.objects.select_for_update(), pk=pk, published_at__isnull=True)
        if index not in range(4):
            raise Http404
        section = version.content['sections'][index]
        question = next((q for q in section['questions'] if q['id'] == question_id), None)
        if question_id and not question:
            raise Http404
        initial = copy.deepcopy(question) if question else {'type': 'essay' if index == 2 else 'audio' if index == 3 else 'short'}
        for key in ['options', 'answers']:
            initial[key] = '\n'.join(initial.get(key, []))
        form = forms.QuestionForm(request.POST or None, initial=initial, version=version, kind=section['kind'])
        if request.method == 'POST' and form.is_valid():
            if form.cleaned_data['delete']:
                section['questions'] = [q for q in section['questions'] if q['id'] != question_id]
            elif question:
                section['questions'][section['questions'].index(question)] = form.question(question_id)
            else:
                section['questions'].append(form.question())
            version.save()
            services.audit(request.user, 'question.saved', pk, section=index)
            return redirect('version_edit', pk=pk)
    return form_page(request, form, f'{LABELS[section["kind"]]} · ' + ('Редактировать задание' if question else 'Новое задание'))


@roles('super', 'admin')
@require_POST
def upload_asset(request, pk):
    form = forms.UploadForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            upload = form.cleaned_data['file']
            content_type = inspect_upload(upload)
            with transaction.atomic():
                version = get_object_or_404(TestVersion.objects.select_for_update(), pk=pk, published_at__isnull=True)
                asset = MediaAsset.objects.create(file=upload, name=upload.name[:180], content_type=content_type, owner=request.user, version=version)
                services.audit(request.user, 'asset.uploaded', asset.pk)
            messages.success(request, 'Файл загружен. Выберите его в настройках секции или задания.')
        except ValidationError as exc:
            notify_error(request, exc)
    else:
        messages.error(request, 'Выберите файл для загрузки.')
    return redirect('version_edit', pk=pk)


@roles('super', 'admin')
def assignments(request):
    qs = Assignment.objects.select_related('student', 'teacher', 'version__exam').order_by('-pk')
    rows = [{'cells': [a.student.display_name, str(a.version), a.teacher.display_name, timezone.localtime(a.due_at).strftime('%d.%m.%Y %H:%M'), a.attempts_allowed]} for a in qs[:200]]
    return listing(request, 'Назначения', ['Ученик', 'Экзамен', 'Проверяющий', 'Срок', 'Попыток'], rows, create_url=reverse('assignment_create'), create_label='Назначить экзамен', description='Каждое назначение закрепляет конкретную версию и преподавателя. Последние 200 назначений.')


@roles('super', 'admin')
def assignment_create(request):
    form = forms.AssignmentForm(request.POST or None, initial={'opens_at': timezone.localtime(), 'due_at': timezone.localtime() + timedelta(days=7)})
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        students = [data['student']] if data['student'] else list(data['group'].students.filter(is_active=True))
        if not students:
            form.add_error(None, 'В группе нет активных учеников.')
        else:
            created = 0
            with transaction.atomic():
                for student in students:
                    assignment, fresh = Assignment.objects.get_or_create(version=data['version'], student=student, defaults={'teacher': data['teacher'], 'group': data['group'], 'opens_at': data['opens_at'], 'due_at': data['due_at'], 'attempts_allowed': data['attempts_allowed'], 'assigned_by': request.user})
                    created += int(fresh)
                    if fresh:
                        services.audit(request.user, 'exam.assigned', assignment.pk, student=student.pk)
            messages.success(request, f'Создано назначений: {created}. Существующие назначения сохранены.')
            return redirect('assignments')
    return form_page(request, form, 'Назначить экзамен', description='Выберите ученика или группу. Указанный срок ограничивает и время уже начатой попытки.')


@roles('student')
def student_exams(request):
    assignments = request.user.assignments.select_related('version__exam', 'teacher').prefetch_related('attempts').order_by('due_at')
    return render(request, 'core/exams.html', {'assignments': assignments, 'now': timezone.now()})


@roles('student')
def instructions(request, pk):
    assignment = get_object_or_404(Assignment.objects.select_related('version__exam', 'teacher'), pk=pk, student=request.user)
    if request.method == 'POST':
        if request.POST.get('ready') != 'yes':
            messages.error(request, 'Подтвердите готовность и ознакомление с записью аудио.')
        else:
            try:
                attempt = services.start_attempt(request.user, assignment.pk)
                return redirect('attempt', pk=attempt.pk)
            except ValidationError as exc:
                notify_error(request, exc)
    sections = [{'name': LABELS[s['kind']], 'minutes': s['duration'] // 60} for s in assignment.version.content['sections']]
    return render(request, 'core/instructions.html', {'assignment': assignment, 'sections': sections})


@login_required
def results(request):
    qs = services.visible_attempts(request.user)
    status = request.GET.get('status', '')
    search = request.GET.get('q', '')[:100]
    if status in dict(Attempt.Status.choices):
        qs = qs.filter(status=status)
    if search:
        qs = qs.filter(Q(assignment__student__first_name__icontains=search) | Q(assignment__version__exam__title__icontains=search) | Q(assignment__student__username__icontains=search))
    rows = []
    for a in qs[:200]:
        score = str(a.overall) if a.status == 'published' or request.user.role != 'student' else '—'
        rows.append({'cells': [a.assignment.version.exam.title, a.assignment.student.display_name, a.get_status_display(), timezone.localtime(a.started_at).strftime('%d.%m.%Y'), score if a.overall is not None else '—'], 'url': reverse('result_detail', args=[a.pk])})
    return listing(request, 'История и результаты', ['Экзамен', 'Ученик', 'Статус', 'Дата', 'Overall'], rows, search=search, statuses=Attempt.Status.choices, status=status, export_url=reverse('export_results') if request.user.may_export else '', description='Результаты пробных экзаменов — оценка учебной платформы, не официальный сертификат IELTS. Показано до 200 попыток.')


@login_required
def result_detail(request, pk):
    attempt = get_object_or_404(services.visible_attempts(request.user), pk=pk)
    attempt = services.refresh_attempt(attempt.pk)
    visible = request.user.role != 'student' or attempt.status == 'published'
    runs = []
    if visible:
        for run in attempt.runs.all():
            review = Review.objects.filter(run=run).first()
            runs.append({'name': LABELS[run.kind], 'band': run.band, 'raw': run.raw_score, 'review': review, 'run': run})
    return render(request, 'core/result.html', {'attempt': attempt, 'visible': visible, 'runs': runs})


@roles('super', 'admin', 'teacher')
def reviews(request):
    attempts = services.visible_attempts(request.user).filter(status__in=['review', 'graded'])
    rows = []
    for attempt in attempts:
        for run in attempt.runs.filter(kind__in=['writing', 'speaking']):
            review = Review.objects.filter(run=run).first()
            rows.append({'cells': [attempt.assignment.student.display_name, attempt.assignment.version.exam.title, LABELS[run.kind], 'Проверено' if review and review.final else 'Черновик' if review else 'Ожидает проверки', attempt.assignment.teacher.display_name], 'url': reverse('review_detail', args=[run.pk])})
    return listing(request, 'Очередь проверки', ['Ученик', 'Экзамен', 'Секция', 'Статус', 'Проверяющий'], rows, description='Writing и Speaking оцениваются по четырём критериям. Публикация доступна администратору после проверки обеих секций.')


@roles('super', 'admin', 'teacher')
def review_detail(request, pk):
    run = get_object_or_404(SectionRun.objects.select_related('attempt__assignment__version', 'attempt__assignment__student'), pk=pk, kind__in=['writing', 'speaking'])
    if not services.can_review(request.user, run.attempt):
        raise PermissionDenied
    if run.attempt.status in ['active', 'void']:
        raise PermissionDenied('Работа ещё не сдана или аннулирована.')
    review = Review.objects.filter(run=run).first()
    keys = ['task1', 'task2'] if run.kind == 'writing' else ['speaking']
    labels = WRITING_CRITERIA if run.kind == 'writing' else SPEAKING_CRITERIA
    if request.method == 'POST':
        criteria = {key: [request.POST.get(f'{key}_{i}', '') for i in range(4)] for key in keys}
        try:
            services.grade_run(request.user, pk, criteria, request.POST.get('feedback', '')[:20000], request.POST.get('action') == 'final')
            messages.success(request, 'Оценка сохранена. Ученик увидит её только после публикации результата.')
            return redirect('review_detail', pk=pk)
        except ValidationError as exc:
            notify_error(request, exc)
    else:
        criteria = review.criteria if review else {}
    groups = [{'name': key, 'label': 'Task 1' if key == 'task1' else 'Task 2 · двойной вес' if key == 'task2' else 'Speaking', 'fields': [{'name': f'{key}_{i}', 'label': label, 'value': str(criteria.get(key, ['', '', '', ''])[i])} for i, label in enumerate(labels)]} for key in keys]
    section = run.attempt.assignment.version.content['sections'][run.index]
    answers = [{'prompt': q['prompt'], 'text': run.responses.get(q['id'], ''), 'recording': run.recordings.filter(question_id=q['id']).first()} for q in section['questions']]
    return render(request, 'core/review.html', {'run': run, 'review': review, 'groups': groups, 'answers': answers, 'scores': [f'{n / 2:g}' for n in range(19)], 'feedback': request.POST.get('feedback', review.feedback if review else '')})


@roles('super', 'admin')
@require_POST
def publish_result(request, pk):
    try:
        services.publish_result(request.user, pk)
        messages.success(request, 'Результат опубликован и доступен ученику.')
    except ValidationError as exc:
        notify_error(request, exc)
    return redirect('result_detail', pk=pk)


@roles('super', 'admin')
@require_POST
def void_attempt(request, pk):
    reason = request.POST.get('reason', '').strip()[:500]
    if not reason:
        messages.error(request, 'Укажите причину аннулирования.')
    else:
        with transaction.atomic():
            attempt = get_object_or_404(Attempt.objects.select_for_update(), pk=pk)
            if attempt.status == 'published':
                raise PermissionDenied('Опубликованную попытку нельзя аннулировать.')
            if attempt.status != 'void':
                attempt.status = 'void'
                attempt.save(update_fields=['status'])
                assignment = Assignment.objects.select_for_update().get(pk=attempt.assignment_id)
                assignment.attempts_allowed += 1
                assignment.save(update_fields=['attempts_allowed'])
                services.audit(request.user, 'attempt.voided', pk, reason=reason)
                messages.success(request, 'Попытка аннулирована с сохранением истории. Добавлена одна новая попытка в пределах прежнего срока.')
    return redirect('result_detail', pk=pk)


@roles('super', 'admin')
def audit_log(request):
    if not request.user.may_audit:
        raise PermissionDenied
    rows = [{'cells': [timezone.localtime(a.created_at).strftime('%d.%m.%Y %H:%M'), a.actor.display_name if a.actor else 'Система', a.action, a.target, json.dumps(a.metadata, ensure_ascii=False)]} for a in AuditLog.objects.select_related('actor')[:200]]
    return listing(request, 'Журнал действий', ['Время', 'Кто', 'Действие', 'Объект', 'Подробности'], rows, description='Последние 200 событий. Пароли, ссылки активации и ответы учеников в журнал не записываются.')


@roles('super', 'admin')
def export_results(request):
    if not request.user.may_export:
        raise PermissionDenied
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Ученик', 'Экзамен', 'Дата', 'Listening', 'Reading', 'Writing', 'Speaking', 'Overall'])
    def safe(value):
        text = str(value)
        return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text
    for attempt in services.visible_attempts(request.user).filter(status='published'):
        bands = {r.kind: r.band for r in attempt.runs.all()}
        writer.writerow([safe(attempt.assignment.student.display_name), safe(attempt.assignment.version.exam.title), attempt.published_at.isoformat(), *[bands.get(k, '') for k in LABELS], attempt.overall])
    services.audit(request.user, 'results.exported', 'published-results')
    response = HttpResponse('\ufeff' + output.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="zhanagasyr-results.csv"'
    return response


def error_403(request, exception=None):
    return render(request, 'core/error.html', {'code': 403, 'title': 'Доступ ограничен', 'description': 'Эта страница недоступна вашей роли или работа назначена другому преподавателю.'}, status=403)


def error_404(request, exception=None):
    return render(request, 'core/error.html', {'code': 404, 'title': 'Страница не найдена', 'description': 'Проверьте адрес или вернитесь в личный кабинет.'}, status=404)


def error_500(request):
    return render(request, 'core/error.html', {'code': 500, 'title': 'Не удалось выполнить запрос', 'description': 'Повторите попытку. Ранее сохранённые ответы остаются в системе.'}, status=500)


def health(request):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
    return JsonResponse({'status': 'ok'})
