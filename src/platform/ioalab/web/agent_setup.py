"""Read-only checksums for the two role-skill downloads."""
import hashlib

from fastapi import APIRouter


def router(platform):
    result = APIRouter()

    @result.get('/api/access/bundles')
    def bundles():
        items = []
        for role, name in (('assistant', 'ioalab-assistant-skill.zip'), ('service', 'ioalab-provider-skill.zip')):
            path = platform.s.bundles_dir / name
            item = {'role': role, 'name': name, 'url': platform.s.public_url.rstrip('/') + '/downloads/' + name, 'available': path.is_file()}
            if path.is_file():
                with path.open('rb') as source: checksum = hashlib.file_digest(source, 'sha256').hexdigest()
                item.update(size=path.stat().st_size, sha256=checksum)
            items.append(item)
        return {'schema': 'ioalab.access-bundles/1', 'bundles': items}

    return result
