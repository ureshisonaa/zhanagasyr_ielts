"""Private durable storage. Downloads must pass the application's access checks."""
import mimetypes
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible
from vercel.blob import sync as blob
from vercel.blob.errors import BlobNotFoundError


@deconstructible
class PrivateBlobStorage(Storage):
    def _open(self, name, mode='rb'):
        if mode != 'rb':
            raise ValueError('Private blobs are read-only after creation')
        return ContentFile(blob.get_blob(name, access='private').body, name=name)

    def _save(self, name, content):
        result = blob.put_blob(name, content.chunks(), access='private',
                               content_type=mimetypes.guess_type(name)[0] or 'application/octet-stream',
                               add_random_suffix=True)
        return result.pathname

    def exists(self, name):
        try:
            blob.head_blob(name)
            return True
        except BlobNotFoundError:
            return False

    def size(self, name):
        return blob.head_blob(name).size

    def delete(self, name):
        blob.delete_blob(name)

    def url(self, name):
        raise ValueError('Use the authenticated media endpoint for private files')
