import json
import uuid
from django import forms
from django.contrib.auth.forms import SetPasswordForm, PasswordChangeForm
from .models import User, StudyGroup, Exam, Assignment, TestVersion
from .exams import QUESTION_TYPES


class LoginForm(forms.Form):
    identifier = forms.CharField(label='Email или логин', max_length=150, widget=forms.TextInput(attrs={'autocomplete': 'username', 'placeholder': 'your@email.com'}))
    password = forms.CharField(label='Пароль', widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'email', 'role', 'can_export', 'can_edit_published', 'can_view_audit']
        labels = {'first_name': 'Имя', 'last_name': 'Фамилия', 'username': 'Логин', 'email': 'Email', 'role': 'Роль', 'can_export': 'Разрешить экспорт отчётов', 'can_edit_published': 'Разрешить исправление опубликованных оценок', 'can_view_audit': 'Разрешить просмотр аудита'}

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        if actor.role != 'super':
            self.fields['role'].choices = [('student', 'Ученик'), ('teacher', 'Преподаватель')]
            for key in ['can_export', 'can_edit_published', 'can_view_audit']:
                self.fields.pop(key)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Этот email уже используется.')
        return email

    def clean_username(self):
        username = self.cleaned_data['username'].strip().lower()
        if '@' in username:
            raise forms.ValidationError('Логин не должен содержать @; для входа по email используйте отдельное поле.')
        if User.objects.filter(username__iexact=username).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Этот логин уже используется.')
        return username


class GroupForm(forms.ModelForm):
    class Meta:
        model = StudyGroup
        fields = ['name', 'description', 'teacher', 'students']
        labels = {'name': 'Название группы', 'description': 'Описание', 'teacher': 'Преподаватель', 'students': 'Ученики'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['teacher'].queryset = User.objects.filter(role='teacher', is_active=True)
        self.fields['students'].queryset = User.objects.filter(role='student', is_active=True)


class ExamForm(forms.ModelForm):
    class Meta:
        model = Exam
        fields = ['title', 'description']
        labels = {'title': 'Название экзамена', 'description': 'Описание и источник материалов'}


class SectionForm(forms.Form):
    duration = forms.IntegerField(label='Продолжительность, секунд', min_value=60, max_value=10800)
    instructions = forms.CharField(label='Инструкция', required=False, widget=forms.Textarea(attrs={'rows': 3}), max_length=10000)
    passage = forms.CharField(label='Текст / материалы секции', required=False, widget=forms.Textarea(attrs={'rows': 12}), max_length=60000)
    replay = forms.BooleanField(label='Разрешить повторное прослушивание Listening', required=False)
    bands = forms.JSONField(label='Таблица raw → band', required=False, help_text='Пары [минимум правильных, band], например [[0,0],[1,3],[2,5],[3,7],[4,9]]. Каждый вопрос даёт 1 балл. Таблица относится только к этой версии.', widget=forms.Textarea(attrs={'rows': 4}))
    audio_id = forms.ModelChoiceField(label='Аудио Listening', queryset=None, required=False)

    def __init__(self, *args, version, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['audio_id'].queryset = version.assets.filter(content_type__startswith='audio/')


class QuestionForm(forms.Form):
    type = forms.ChoiceField(label='Тип вопроса', choices=QUESTION_TYPES)
    prompt = forms.CharField(label='Задание', widget=forms.Textarea(attrs={'rows': 4}), max_length=10000)
    options = forms.CharField(label='Варианты ответа — по одному в строке', required=False, widget=forms.Textarea(attrs={'rows': 4}), max_length=8000)
    answers = forms.CharField(label='Ключи — допустимые ответы по одному в строке', required=False, help_text='Для «Несколько вариантов» перечислите весь правильный набор. Для Writing / Speaking оставьте пустым.', widget=forms.Textarea(attrs={'rows': 3}), max_length=8000)
    max_words = forms.IntegerField(label='Максимум слов в коротком ответе (0 — без ограничения)', min_value=0, max_value=100, initial=0)
    case_sensitive = forms.BooleanField(label='Учитывать регистр', required=False)
    image_id = forms.ModelChoiceField(label='Изображение / диаграмма', queryset=None, required=False)
    delete = forms.BooleanField(label='Удалить этот вопрос', required=False)

    def __init__(self, *args, version, kind, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import MediaAsset
        self.fields['image_id'].queryset = MediaAsset.objects.filter(version=version, content_type__startswith='image/')
        allowed = ['essay'] if kind == 'writing' else ['audio'] if kind == 'speaking' else ['short', 'choice', 'multi', 'tfng', 'ynng', 'matching']
        self.fields['type'].choices = [(k, v) for k, v in QUESTION_TYPES if k in allowed]

    def question(self, question_id=None):
        data = self.cleaned_data.copy()
        data.pop('delete')
        data['id'] = question_id or uuid.uuid4().hex[:12]
        for key in ['answers', 'options']:
            data[key] = [line.strip() for line in data[key].splitlines() if line.strip()]
        if data['type'] == 'tfng':
            data['options'] = ['True', 'False', 'Not Given']
        if data['type'] == 'ynng':
            data['options'] = ['Yes', 'No', 'Not Given']
        data['image_id'] = str(data['image_id'].id) if data['image_id'] else ''
        return data


class AssignmentForm(forms.Form):
    version = forms.ModelChoiceField(label='Опубликованная версия', queryset=TestVersion.objects.none())
    student = forms.ModelChoiceField(label='Ученик', required=False, queryset=User.objects.none())
    group = forms.ModelChoiceField(label='Или вся группа', required=False, queryset=StudyGroup.objects.all())
    teacher = forms.ModelChoiceField(label='Преподаватель для проверки', queryset=User.objects.none())
    opens_at = forms.DateTimeField(label='Доступен с (время Кызылорды)', widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'))
    due_at = forms.DateTimeField(label='Завершить до (время Кызылорды)', widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'))
    attempts_allowed = forms.IntegerField(label='Количество попыток', min_value=1, max_value=20, initial=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['version'].queryset = TestVersion.objects.filter(published_at__isnull=False, exam__archived=False).select_related('exam')
        self.fields['student'].queryset = User.objects.filter(role='student', is_active=True)
        self.fields['teacher'].queryset = User.objects.filter(role='teacher', is_active=True)

    def clean(self):
        data = super().clean()
        if bool(data.get('student')) == bool(data.get('group')):
            raise forms.ValidationError('Выберите одного ученика или группу.')
        if data.get('opens_at') and data.get('due_at') and data['due_at'] <= data['opens_at']:
            raise forms.ValidationError('Окончание должно быть позже начала.')
        return data


class UploadForm(forms.Form):
    file = forms.FileField(label='Аудио или изображение', help_text='Аудио: MP3, WAV, OGG, WebM, M4A. Изображения: PNG, JPEG, WebP. Не более 25 МБ.')
