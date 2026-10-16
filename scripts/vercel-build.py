"""Apply schema before publishing; seed initial accounts once from protected env."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.core.management import call_command
from django.db import transaction
from core.models import User

call_command('check', deploy=True, fail_level='ERROR')
call_command('migrate', interactive=False)
if os.environ.get('VERCEL_ENV') == 'production':
    with transaction.atomic():
        for username, role, name, key in [
            ('admin', 'super', 'Администратор', 'INITIAL_ADMIN_PASSWORD'),
            ('student', 'student', 'Ученик', 'INITIAL_STUDENT_PASSWORD'),
        ]:
            password = os.getenv(key)
            if password and not User.objects.filter(username=username).exists():
                User.objects.create_user(username=username, email=f'{username}@accounts.zhanagasyr.invalid',
                                         password=password, role=role, first_name=name)
                print(f'Created initial {role} account')
