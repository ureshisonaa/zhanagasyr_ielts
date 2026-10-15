import hashlib
import json
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Create a consistent SQLite + private media backup. Pause uploads during the backup.'

    def add_arguments(self, parser):
        parser.add_argument('output')

    def handle(self, *args, **options):
        if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
            raise CommandError('For PostgreSQL use pg_dump plus the media procedure in docs/OPERATIONS.md.')
        output = Path(options['output']).resolve()
        if output.exists():
            raise CommandError('Output already exists; use a new filename.')
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / 'db.sqlite3'
            with sqlite3.connect(settings.DATABASES['default']['NAME']) as source, sqlite3.connect(db_path) as target:
                source.backup(target)
            files = {'db.sqlite3': db_path}
            if settings.MEDIA_ROOT.exists():
                for path in settings.MEDIA_ROOT.rglob('*'):
                    if path.is_symlink():
                        raise CommandError('Symlinks in media storage are not supported.')
                    if path.is_file():
                        files['private/' + path.relative_to(settings.MEDIA_ROOT).as_posix()] = path
            manifest = {name: hashlib.file_digest(path.open('rb'), 'sha256').hexdigest() for name, path in files.items()}
            manifest_path = Path(folder) / 'manifest.json'
            manifest_path.write_text(json.dumps(manifest))
            with tarfile.open(output, 'x:gz') as archive:
                archive.add(manifest_path, arcname='manifest.json')
                for name, path in files.items():
                    archive.add(path, arcname=name)
        output.chmod(0o600)
        self.stdout.write(self.style.SUCCESS(f'Backup created: {output}'))
