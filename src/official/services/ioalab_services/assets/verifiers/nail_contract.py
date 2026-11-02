"""Runs only in the A2C WASM sandbox, including the delivered application code."""
import json

CRITERIA = __CRITERIA__


def verify(ctx):
    checks=[]
    def test(index,ok): checks.append({'check':CRITERIA[index],'ok':bool(ok)})
    try:
        d=json.loads(ctx.read('booking.json').decode('utf-8'));s=ctx.values
        source=d.get('engineSource','')
        if not isinstance(source,str) or len(source)>30000: raise ValueError('Invalid source')
        # This module is a verifier package inside python.wasm. Never execute on the host.
        namespace={}
        exec(compile(source,'delivered-booking-engine.py','exec'),namespace)
        factory=namespace['BookingEngine']
        capacity=s['spec.booking.capacity']
        def engine(): return factory(capacity)
        def reserve(book,key,slot='10:00'): return book.reserve(key,slot)
        expected_services=[{'name':s['spec.booking.service'+str(i)+'Name'],'minutes':s['spec.booking.service'+str(i)+'Minutes']} for i in (1,2)]
        test(0,all(d.get(key)==s['spec.booking.'+key] for key in ('shop','open','close','capacity'))
             and all(type(d.get(key)) is int for key in ('open','close','capacity')) and d.get('services')==expected_services)
        book=engine();one=reserve(book,'customer-1')
        test(1,one and book.list()==[one])
        for i in range(1,capacity): reserve(book,'fill-'+str(i))
        test(2,reserve(book,'overflow') is None and len(book.list())==capacity)
        book.cancel(one['id']);after=reserve(book,'replacement')
        test(3,after is not None and len(book.list())==capacity)
        mp=d.get('miniProgram',{})
        structure=False
        if isinstance(mp,dict) and all(isinstance(mp.get(n),str) and len(mp[n])>10 for n in ('app.json','pages/index/index.js','pages/index/index.wxml')):
            try:
                pages=json.loads(mp['app.json']).get('pages')
                structure=isinstance(pages,list) and bool(pages) and all(
                    isinstance(page,str) and '\\' not in page and all(part not in ('','.','..') for part in page.split('/'))
                    and all(isinstance(mp.get(page+ext),str) and bool(mp[page+ext].strip()) for ext in ('.js','.wxml')) for page in pages)
            except (ValueError,TypeError,AttributeError):
                pass
        test(4,d.get('format')=='ioalab.booking' and d.get('allowCancel') is True and d.get('idempotent') is True and structure)
        if s['spec.booking.level']=='reliable':
            book=engine();first=reserve(book,'retry');again=reserve(book,'retry')
            test(5,first==again and len(book.list())==1)
            outcomes=[reserve(book,'race-'+str(i),'11:00') for i in range(10)]
            test(6,sum(x is not None for x in outcomes)==capacity)
            book=engine();first=reserve(book,'lost-response');again=reserve(book,'lost-response')
            test(7,first==again and len(book.list())==1)
            book=engine()
            for i in range(capacity): reserve(book,'boundary-'+str(i),'10:00')
            y=reserve(book,'next','11:00')
            test(8,y is not None and len(book.list())==capacity+1)
            book=engine();x=reserve(book,'cancel-twice');book.cancel(x['id']);book.cancel(x['id'])
            test(9,len(book.list())==0 and reserve(book,'after-cancel') is not None)
        return {'status':'pass' if all(x['ok'] for x in checks) else 'fail','checks':checks}
    except Exception as exc:
        return {'status':'fail','checks':checks+[{'check':'可运行的合同交付件','ok':False,'detail':str(exc)[:160]}]}
