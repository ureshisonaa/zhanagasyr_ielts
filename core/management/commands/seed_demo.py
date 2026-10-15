"""Explicit development-only seed. Never enables fixed/default credentials."""
import json
import secrets
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from core.models import User, StudyGroup, Exam, TestVersion, Assignment, MediaAsset, Attempt, SectionRun, Review
from core.exams import empty_content
from core.services import publish_version, audit


def sample_content():
    content = empty_content()
    listening, reading, writing, speaking = content['sections']
    listening.update(instructions='Listen to the short introduction to a learning centre. Answer the four questions. This is an original, shortened demonstration, not a calibrated IELTS test.', duration=1800)
    questions = [
        ('l1', 'What is the name of the learning centre?', 'short', ['Riverside', 'Riverside Learning Centre'], []),
        ('l2', 'On which day does the centre open late?', 'choice', ['Thursday'], ['Monday', 'Tuesday', 'Thursday']),
        ('l3', 'How many study rooms are available?', 'short', ['six', '6'], []),
        ('l4', 'Which TWO things can visitors borrow?', 'multi', ['Headphones', 'Laptops'], ['Headphones', 'Bicycles', 'Laptops', 'Cameras']),
    ]
    listening['questions'] = [{'id': i, 'prompt': p, 'type': t, 'answers': a, 'options': o, 'max_words': 4, 'case_sensitive': False, 'image_id': ''} for i, p, t, a, o in questions]
    reading.update(instructions='Read the passage and answer questions 1–4. For completion questions, use NO MORE THAN TWO WORDS from the passage.', passage='A quieter kind of city\n\nIn many cities, small public gardens are changing how people experience their neighbourhoods. Unlike large parks, these gardens occupy unused corners: a former parking space, a narrow strip beside a library, or a neglected courtyard. Their modest size makes them relatively inexpensive to create.\n\nA local survey in the fictional city of Bellford followed twelve such gardens over three years. Volunteers reported that the main benefit was social connection. Residents who had previously never spoken began sharing tools and advice. The survey did not measure changes in property prices.\n\nNot every garden succeeded. Two sites closed because reliable access to water had not been arranged. At other sites, volunteers installed rainwater tanks. This reduced their dependence on the public water supply during dry months.\n\nThe researchers concluded that long-term success depended less on the number of plants than on clear responsibilities. A small team that agreed who would water, repair and organise the space was more effective than a larger group without a plan.\n\nOriginal demonstration passage written for this application; Bellford and the survey are fictional.')
    reading['questions'] = [
        {'id': 'r1', 'type': 'tfng', 'prompt': 'The gardens were all built inside large parks.', 'options': ['True', 'False', 'Not Given'], 'answers': ['False']},
        {'id': 'r2', 'type': 'tfng', 'prompt': 'Property prices rose near every garden.', 'options': ['True', 'False', 'Not Given'], 'answers': ['Not Given']},
        {'id': 'r3', 'type': 'short', 'prompt': 'The main benefit reported by volunteers was ______.', 'answers': ['social connection'], 'options': [], 'max_words': 2},
        {'id': 'r4', 'type': 'short', 'prompt': 'Volunteers installed ______ to reduce dependence on the public water supply.', 'answers': ['rainwater tanks'], 'options': [], 'max_words': 2},
    ]
    for section in [listening, reading]:
        section['bands'] = [[0, 0], [1, 3], [2, 5], [3, 7], [4, 9]]
    writing.update(instructions='Complete both tasks. Spend about 20 minutes on Task 1 and 40 minutes on Task 2. Task 2 carries twice the weight of Task 1.')
    writing['questions'] = [
        {'id': 'w1', 'type': 'essay', 'prompt': 'Task 1\nThe table shows the percentage of students using three study resources at a fictional college. Summarise the information by selecting and reporting the main features, and make comparisons where relevant. Write at least 150 words.\n\nResource           2020     2025\nPrinted books       65%      45%\nOnline courses      30%      60%\nStudy groups        25%      40%'},
        {'id': 'w2', 'type': 'essay', 'prompt': 'Task 2\nSome people believe that learning in a classroom is more effective than learning independently online. To what extent do you agree or disagree? Give reasons for your answer and include relevant examples. Write at least 250 words.'},
    ]
    speaking.update(instructions='Record a separate answer for each part. Your teacher will listen to the recordings. For Part 2, take one minute to prepare before speaking for one to two minutes.')
    speaking['questions'] = [
        {'id': 's1', 'type': 'audio', 'prompt': 'Part 1 · Introduction and interview\nTell us about the place where you live. What do you like about it? How do you usually spend your weekends? Has your routine changed in recent years?'},
        {'id': 's2', 'type': 'audio', 'prompt': 'Part 2 · Long turn\nDescribe a skill you would like to learn. You should say: what the skill is; why you are interested in it; how you would learn it; and explain how it could affect your life.'},
        {'id': 's3', 'type': 'audio', 'prompt': 'Part 3 · Discussion\nWhy is lifelong learning becoming important? Should employers help their staff learn new skills? How might technology change education in the future?'},
    ]
    return content


class Command(BaseCommand):
    help = 'Create clearly labelled demo data and randomly generated local login credentials (DEBUG only).'

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Demo seeding is disabled outside DEBUG mode.')
        if User.objects.filter(username='demo-admin').exists():
            self.stdout.write('Demo already exists; no accounts or passwords changed.')
            return
        text = 'Welcome to the Riverside Learning Centre. We are open every day from nine in the morning until six in the evening. On Thursday, we stay open until eight. There are six study rooms which you can reserve at reception. Visitors can borrow headphones and laptops for use inside the centre. Please bring your student card when you register. Thank you for listening.'
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'narration.txt'
            script.write_text(text)
            audio = Path(folder) / 'listening.wav'
            try:
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'flite=textfile={script}:voice=slt', '-ar', '24000', str(audio)], check=True, timeout=30)
            except (OSError, subprocess.SubprocessError) as exc:
                raise CommandError('Demo narration requires ffmpeg with the flite filter.') from exc
            credentials = []
            with transaction.atomic():
                accounts = []
                for role, name, last in [('super', 'Алия', 'Нурланова'), ('teacher', 'Дана', 'Серикова'), ('student', 'Айлин', 'Ахметова')]:
                    username = 'demo-admin' if role == 'super' else f'demo-{role}'
                    password = secrets.token_urlsafe(18)
                    user = User.objects.create_user(username=username, email=f'{username}@example.com', password=password, first_name=name, last_name=last, role=role)
                    credentials.append({'username': username, 'password': password, 'role': role})
                    accounts.append(user)
                admin, teacher, student = accounts
                group = StudyGroup.objects.create(name='Academic · Demo', description='Демонстрационная группа; все данные учебные.', teacher=teacher)
                group.students.add(student)
                exam = Exam.objects.create(title='Academic Practice · Demo', description='Сокращённый демонстрационный тест: 4 вопроса Listening, 4 Reading, 2 Writing, 3 Speaking. Таблица баллов условная; результат не отражает реальный уровень IELTS.', author=admin)
                version = TestVersion.objects.create(exam=exam, content=sample_content())
                with audio.open('rb') as stream:
                    asset = MediaAsset.objects.create(version=version, owner=admin, file=File(stream, name='listening.wav'), name='Original demo narration.wav', content_type='audio/wav')
                version.content['sections'][0]['audio_id'] = str(asset.pk)
                version.save()
                publish_version(admin, version.pk)
                assignment = Assignment.objects.create(version=version, student=student, teacher=teacher, group=group, opens_at=timezone.now() - timedelta(days=30), due_at=timezone.now() + timedelta(days=30), attempts_allowed=10, assigned_by=admin)
                # Deliberately labelled demonstration history, not actual learner evidence.
                for days, band in [(21, 5.0), (14, 5.5), (7, 6.0)]:
                    date = timezone.now() - timedelta(days=days)
                    attempt = Attempt.objects.create(assignment=assignment, number=4 - days // 7, status='published', current_section=3, started_at=date, submitted_at=date + timedelta(hours=3), published_at=date + timedelta(days=1), overall=band)
                    for index, kind in enumerate(['listening', 'reading', 'writing', 'speaking']):
                        run = SectionRun.objects.create(attempt=attempt, index=index, kind=kind, started_at=date, deadline=date + timedelta(hours=1), submitted_at=date + timedelta(hours=1), responses={}, band=band)
                        if kind in ['writing', 'speaking']:
                            keys = ['task1', 'task2'] if kind == 'writing' else ['speaking']
                            Review.objects.create(run=run, grader=teacher, criteria={k: [band] * 4 for k in keys}, feedback='Демонстрационная запись для знакомства с интерфейсом. Это не оценка реальной работы.', final=True)
                audit(admin, 'demo.seeded', 'demo-only')
            credentials_file = settings.BASE_DIR / 'var' / 'demo-credentials.json'
            credentials_file.write_text(json.dumps(credentials, ensure_ascii=False, indent=2))
            credentials_file.chmod(0o600)
        self.stdout.write(self.style.SUCCESS('Demo ready. Credentials are in var/demo-credentials.json (not tracked by Git).'))
