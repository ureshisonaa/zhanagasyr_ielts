from getpass import getpass
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from core.models import User


class Command(BaseCommand):
    help = 'Create the first Super Admin; password is entered securely.'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True)
        parser.add_argument('--username', default='admin')
        parser.add_argument('--name', default='Администратор')

    def handle(self, *args, **options):
        if User.objects.filter(role='super').exists():
            raise CommandError('A Super Admin already exists. Use the application to manage accounts.')
        user = User(username=options['username'], email=options['email'].lower(), first_name=options['name'], role='super')
        password = getpass('Password: ')
        if password != getpass('Repeat password: '):
            raise CommandError('Passwords do not match.')
        try:
            validate_password(password, user)
            user.full_clean(exclude=['password'])
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS('Super Admin created.'))
