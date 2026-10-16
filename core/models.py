import uuid
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class User(AbstractUser):
    class Role(models.TextChoices):
        SUPER = 'super', 'Главный администратор'
        ADMIN = 'admin', 'Администратор'
        TEACHER = 'teacher', 'Преподаватель'
        STUDENT = 'student', 'Ученик'
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.STUDENT)
    email = models.EmailField(unique=True)
    session_version = models.PositiveIntegerField(default=1)
    can_export = models.BooleanField(default=False)
    can_edit_published = models.BooleanField(default=False)
    can_view_audit = models.BooleanField(default=False)

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def is_manager(self):
        return self.role in (self.Role.SUPER, self.Role.ADMIN)

    @property
    def may_export(self):
        return self.role == self.Role.SUPER or (self.is_manager and self.can_export)

    @property
    def may_audit(self):
        return self.role == self.Role.SUPER or (self.is_manager and self.can_view_audit)


class StudyGroup(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    teacher = models.ForeignKey(User, on_delete=models.PROTECT, related_name='teaching_groups', limit_choices_to={'role': 'teacher'})
    students = models.ManyToManyField(User, related_name='study_groups', blank=True, limit_choices_to={'role': 'student'})

    def __str__(self):
        return self.name


class Exam(models.Model):
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    author = models.ForeignKey(User, on_delete=models.PROTECT)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class TestVersion(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name='versions')
    number = models.PositiveIntegerField(default=1)
    content = models.JSONField(default=dict)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['exam', 'number'], name='unique_exam_version')]

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).objects.get(pk=self.pk)
            if old.published_at and (old.content != self.content or old.number != self.number or old.exam_id != self.exam_id or old.published_at != self.published_at):
                raise ValidationError('Опубликованная версия неизменяема. Создайте новую версию.')
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.exam.title} · v{self.number}'


class Assignment(models.Model):
    version = models.ForeignKey(TestVersion, on_delete=models.PROTECT)
    student = models.ForeignKey(User, on_delete=models.PROTECT, related_name='assignments')
    teacher = models.ForeignKey(User, on_delete=models.PROTECT, related_name='assigned_reviews')
    group = models.ForeignKey(StudyGroup, null=True, blank=True, on_delete=models.SET_NULL)
    opens_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField()
    attempts_allowed = models.PositiveSmallIntegerField(default=1)
    assigned_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['version', 'student'], name='unique_student_version_assignment'), models.CheckConstraint(condition=Q(attempts_allowed__gte=1), name='positive_attempt_limit')]


class Attempt(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'В процессе'
        REVIEW = 'review', 'Ожидает проверки'
        GRADED = 'graded', 'Готов к публикации'
        PUBLISHED = 'published', 'Результат опубликован'
        VOID = 'void', 'Аннулирован'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name='attempts')
    number = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    current_section = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True)
    published_at = models.DateTimeField(null=True)
    overall = models.DecimalField(max_digits=3, decimal_places=1, null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['assignment', 'number'], name='unique_attempt_number'), models.UniqueConstraint(fields=['assignment'], condition=Q(status='active'), name='single_active_attempt')]
        ordering = ['-started_at']


class SectionRun(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name='runs')
    index = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=12)
    started_at = models.DateTimeField()
    deadline = models.DateTimeField()
    submitted_at = models.DateTimeField(null=True)
    responses = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=0)
    raw_score = models.PositiveSmallIntegerField(null=True)
    band = models.DecimalField(max_digits=3, decimal_places=1, null=True)
    audio_started_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['attempt', 'index'], name='unique_section_run')]
        ordering = ['index']


class Review(models.Model):
    run = models.OneToOneField(SectionRun, on_delete=models.CASCADE, related_name='review')
    grader = models.ForeignKey(User, on_delete=models.PROTECT)
    criteria = models.JSONField(default=dict)
    feedback = models.TextField(blank=True)
    final = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


def private_path(instance, filename):
    from pathlib import Path
    return f'{instance.id}{Path(filename).suffix.lower()}'


class MediaAsset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to=private_path)
    name = models.CharField(max_length=180)
    content_type = models.CharField(max_length=80)
    owner = models.ForeignKey(User, on_delete=models.PROTECT)
    version = models.ForeignKey(TestVersion, null=True, on_delete=models.PROTECT, related_name='assets')
    run = models.ForeignKey(SectionRun, null=True, on_delete=models.PROTECT, related_name='recordings')
    question_id = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        constraints = [models.CheckConstraint(condition=(Q(version__isnull=False, run__isnull=True) | Q(version__isnull=True, run__isnull=False)), name='asset_one_parent'), models.UniqueConstraint(fields=['run', 'question_id'], condition=Q(run__isnull=False), name='single_recording_per_question')]


class AuditLog(models.Model):
    actor = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    target = models.CharField(max_length=100)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class LoginThrottle(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    failures = models.PositiveIntegerField(default=0)
    until = models.DateTimeField(default=timezone.now)
