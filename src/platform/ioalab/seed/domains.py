"""Read an explicit public deployment manifest, never participant source."""
import json
from pathlib import Path
import re


def load_domains(path):
    if path is None:
        raise ValueError('IOALAB_SEED_FILE is required when initializing accounts and categories')
    source = Path(path)
    if not source.is_file() or source.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Seed manifest must be a readable JSON file of at most 2 MiB')
    try:
        data = json.loads(source.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid seed manifest') from exc
    domains = data.get('domains') if isinstance(data, dict) else None
    if not isinstance(domains, list) or not 1 <= len(domains) <= 64:
        raise ValueError('Seed manifest requires 1 to 64 domains')
    accounts, categories = set(), set()
    for domain in domains:
        if not isinstance(domain, dict):
            raise ValueError('Invalid seed domain')
        account, label, category = domain.get('account'), domain.get('label'), domain.get('category')
        if not isinstance(account, str) or not re.fullmatch(r'ISP_(?!000)\d{3}', account) or account in accounts:
            raise ValueError('Each domain requires a unique ISP account')
        if not isinstance(label, str) or not 1 <= len(label) <= 256:
            raise ValueError('Invalid domain label')
        if not isinstance(category, dict) or not all(isinstance(category.get(k), str) and category[k] for k in ('id','name')):
            raise ValueError('Category id and name are required')
        if category['id'] in categories:
            raise ValueError('Duplicate seed category')
        services = domain.get('services', [])
        if not isinstance(services, list) or len(services) > 100:
            raise ValueError('Invalid seed service summaries')
        if any(not isinstance(s, dict) or not all(isinstance(s.get(k), str) for k in ('id','name','description')) for s in services):
            raise ValueError('Service summaries need id, name and description')
        accounts.add(account)
        categories.add(category['id'])
    return domains


def definitions(path):
    return [service for domain in load_domains(path) for service in domain.get('services', [])]
