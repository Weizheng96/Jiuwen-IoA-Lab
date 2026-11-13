"""Package-owned, self-contained form pages for browser and MCP Apps rendering."""
from importlib.resources import files


def form_page(mode):
    if mode not in ('browser', 'app'):
        raise ValueError('Unknown form presentation')
    root = files('ioalab_sdk').joinpath('runtime/web')
    page = root.joinpath('shared/forms.html').read_text(encoding='utf-8')
    return (page.replace('/* IOA_STYLE */', root.joinpath('shared/forms.css').read_text(encoding='utf-8'))
            .replace('/* IOA_FIELDS */', '\n'.join(root.joinpath(path).read_text(encoding='utf-8') for path in (
                'messaging/fields.js', 'provider/fields.js')))
            .replace('/* IOA_VIEWS */', '\n'.join(root.joinpath(path).read_text(encoding='utf-8') for path in (
                'shared/presentation.js', 'registry/view.js', 'messaging/view.js', 'payments/view.js', 'delegation/view.js', 'provider/view.js')))
            .replace('/* IOA_TRANSPORT */', root.joinpath('shared/' + mode + '.js').read_text(encoding='utf-8'))
            .replace('/* IOA_FORMS */', root.joinpath('shared/forms.js').read_text(encoding='utf-8')))
