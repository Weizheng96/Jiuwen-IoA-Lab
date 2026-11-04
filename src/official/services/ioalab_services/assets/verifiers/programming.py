"""Owned software contract verifier. Delivered Python executes in A2C WASM only."""
import json

CRITERIA = __CRITERIA__


def verify(ctx):
    checks = []
    def test(index, ok):
        checks.append({'check': CRITERIA[index], 'ok': bool(ok)})
    try:
        data = json.loads(ctx.read('booking.json').decode('utf-8'))
        values = ctx.values
        def spec(key):
            return values['spec.booking.' + key]
        services = [{'name': spec('service' + str(i) + 'Name'), 'minutes': spec('service' + str(i) + 'Minutes')} for i in (1, 2)]
        test(0, all(data.get(key) == spec(key) for key in ('shop', 'open', 'close', 'capacity', 'mode'))
             and all(type(data.get(key)) is int for key in ('open', 'close', 'capacity'))
             and data.get('services') == services)
        source = data.get('engineSource')
        if not isinstance(source, str) or not source.strip() or len(source) > 30000:
            raise ValueError('Invalid contract source')
        namespace = {}
        # This entry point runs inside python.wasm; never call it on the host.
        exec(compile(source, 'delivered-business.py', 'exec'), namespace)
        capacity = spec('capacity')
        if spec('mode') == 'catalog':
            catalog = namespace['Catalog'](services)
            test(1, catalog.list() == services)
            test(2, catalog.search(services[0]['name']) == [services[0]])
            test(3, catalog.search('no-such-item-000') == [])
        else:
            factory = namespace['BookingEngine']
            book = factory(capacity)
            first = book.reserve('first', '10:00')
            test(1, isinstance(first, dict) and first.get('slot') == '10:00' and 'id' in first and book.list() == [first])
            for i in range(1, capacity):
                book.reserve('fill-' + str(i), '10:00')
            test(2, book.reserve('overflow', '10:00') is None and len(book.list()) == capacity)
            if spec('carrier') == 'display':
                test(3, not callable(getattr(book, 'cancel', None)) and data.get('allowCancel') is False)
            else:
                book.cancel(first['id'])
                test(3, book.reserve('replacement', '10:00') is not None and len(book.list()) == capacity and data.get('allowCancel') is True)
        carrier = spec('carrier')
        field = {'wechat-miniapp': 'miniProgram', 'website': 'website', 'android': 'androidApp', 'display': 'displayPage'}[carrier]
        files = data.get(field)
        structure = isinstance(files, dict) and all(isinstance(k, str) and k and '\\' not in k
            and all(part not in ('', '.', '..') for part in k.split('/')) and isinstance(v, str) and v.strip() for k, v in files.items())
        if structure and carrier == 'wechat-miniapp':
            pages = json.loads(files.get('app.json', '{}')).get('pages')
            structure = isinstance(pages, list) and bool(pages) and all(isinstance(p, str)
                and all(part not in ('', '.', '..') for part in p.split('/'))
                and all(files.get(p + ext, '').strip() for ext in ('.js', '.wxml')) for p in pages)
        elif structure and carrier in ('website', 'display'):
            structure = all(files.get(path, '').strip() for path in ('index.html', 'app.js', 'style.css'))
            structure = structure and '<html' in files['index.html'] and 'app.js' in files['index.html'] and 'style.css' in files['index.html']
        elif structure and carrier == 'android':
            structure = all(files.get(path, '').strip() for path in ('AndroidManifest.xml', 'MainActivity.kt', 'build.gradle.kts', 'settings.gradle.kts'))
            structure = structure and '<manifest' in files['AndroidManifest.xml'] and 'class MainActivity' in files['MainActivity.kt']
        test(4, data.get('format') == 'ioalab.booking' and structure
             and not any(key in data for key in ('miniProgram', 'website', 'androidApp', 'displayPage') if key != field))
        if spec('level') == 'reliable':
            book = factory(capacity); first = book.reserve('retry', '10:00')
            test(5, first == book.reserve('retry', '10:00') and len(book.list()) == 1)
            outcomes = [book.reserve('race-' + str(i), '11:00') for i in range(10)]
            test(6, sum(row is not None for row in outcomes) == capacity)
            book = factory(capacity); first = book.reserve('lost', '10:00')
            test(7, first == book.reserve('lost', '10:00') and len(book.list()) == 1)
            book = factory(capacity)
            for i in range(capacity):
                book.reserve('slot-' + str(i), '10:00')
            test(8, book.reserve('next', '11:00') is not None and len(book.list()) == capacity + 1)
            book = factory(capacity); first = book.reserve('cancel', '10:00')
            book.cancel(first['id']); book.cancel(first['id'])
            test(9, len(book.list()) == 0 and book.reserve('after', '10:00') is not None)
        return {'status': 'pass' if all(row['ok'] for row in checks) else 'fail', 'checks': checks}
    except Exception as exc:
        return {'status': 'fail', 'checks': checks + [{'check': '可运行的合同交付件', 'ok': False, 'detail': str(exc)[:160]}]}
