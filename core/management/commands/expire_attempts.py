import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from core.models import Attempt
from core.services import refresh_attempt


class Command(BaseCommand):
    help = 'Finalize expired sections, including attempts with no connected browser.'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true')

    def handle(self, *args, **options):
        while True:
            for pk in Attempt.objects.filter(status='active').values_list('pk', flat=True):
                refresh_attempt(pk)
            if not options['loop']:
                self.stdout.write('Expired sections processed.')
                break
            close_old_connections()
            time.sleep(5)
