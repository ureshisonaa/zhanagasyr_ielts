import hashlib
import json
import tarfile
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Verify and extract a backup into a NEW directory; never overwrites a running database.'

    def add_arguments(self, parser):
        parser.add_argument('archive')
        parser.add_argument('--destination', required=True)

    def handle(self, *args, **options):
        dest = Path(options['destination']).resolve()
        if dest.exists():
            raise CommandError('Destination must not exist.')
        try:
            with tarfile.open(options['archive'], 'r:gz') as archive:
                members = archive.getmembers()
                names = [m.name for m in members]
                if len(names) != len(set(names)) or any(not m.isfile() or Path(m.name).is_absolute() or '..' in Path(m.name).parts for m in members):
                    raise CommandError('Unsafe archive structure.')
                manifest = json.load(archive.extractfile('manifest.json'))
                if set(names) != set(manifest) | {'manifest.json'} or 'db.sqlite3' not in manifest:
                    raise CommandError('Incomplete manifest.')
                for name, checksum in manifest.items():
                    stream = archive.extractfile(name)
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                        raise CommandError('Backup checksum mismatch.')
                dest.mkdir(parents=True, mode=0o700)
                archive.extractall(dest, filter='data')
        except (tarfile.TarError, KeyError, ValueError) as exc:
            raise CommandError('Invalid backup.') from exc
        self.stdout.write(self.style.SUCCESS(f'Backup verified and restored to {dest}'))
