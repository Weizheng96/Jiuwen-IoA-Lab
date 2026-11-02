"""Limited display/Android scene verifier. Execute delivered code only in WASM."""
import json

CRITERIA = __CRITERIA__
APP_KIND = __APP_KIND__


def verify(ctx):
    checks = []

    def test(index, ok):
        checks.append({'check': CRITERIA[index], 'ok': bool(ok)})

    try:
        data = json.loads(ctx.read('booking.json').decode('utf-8'))
        values = ctx.values
        source = data.get('engineSource')
        if not isinstance(source, str) or not source or len(source) > 30000:
            raise ValueError('Invalid booking source')
        namespace = {}
        # Inside A2C python.wasm only; never import/execute deliveries on the host.
        exec(compile(source, 'limited-booking-engine.py', 'exec'), namespace)
        capacity = values['spec.booking.capacity']
        book = namespace['BookingEngine'](capacity)
        services = [{'name': values['spec.booking.service' + str(i) + 'Name'],
                     'minutes': values['spec.booking.service' + str(i) + 'Minutes']} for i in (1, 2)]
        test(0, all(data.get(key) == values['spec.booking.' + key] for key in ('shop', 'open', 'close', 'capacity'))
             and all(type(data.get(key)) is int for key in ('open', 'close', 'capacity'))
             and data.get('services') == services)
        first = book.reserve('customer-1', '10:00')
        test(1, isinstance(first, dict) and first.get('slot') == '10:00'
             and 'id' in first and book.list() == [first])
        for i in range(1, capacity):
            book.reserve('fill-' + str(i), '10:00')
        test(2, book.reserve('overflow', '10:00') is None and len(book.list()) == capacity)
        if APP_KIND == 'display':
            test(3, not callable(getattr(book, 'cancel', None)) and data.get('allowCancel') is False)
            files = data.get('displayPage', {})
            page = files.get('index.html') if isinstance(files, dict) else None
            structure = isinstance(page, str) and '<html' in page and '</html>' in page
        else:
            book.cancel(first['id'])
            test(3, book.reserve('replacement', '10:00') is not None and len(book.list()) == capacity)
            files = data.get('androidApp', {})
            structure = (isinstance(files, dict)
                         and all(isinstance(files.get(name), str) and len(files[name].strip()) > 10
                                 for name in ('AndroidManifest.xml', 'MainActivity.kt'))
                         and '<manifest' in files['AndroidManifest.xml'] and 'class ' in files['MainActivity.kt']
                         and data.get('allowCancel') is True)
        test(4, data.get('format') == 'ioalab.booking' and 'miniProgram' not in data and structure)
        return {'status': 'pass' if all(row['ok'] for row in checks) else 'fail', 'checks': checks}
    except Exception as exc:
        return {'status': 'fail', 'checks': checks + [
            {'check': '可运行的合同交付件', 'ok': False, 'detail': str(exc)[:160]}]}
