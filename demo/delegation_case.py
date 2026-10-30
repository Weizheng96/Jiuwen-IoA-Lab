"""Original named-service scenario, with natural user turns and four actual confirmation gates."""
import json,socket,threading,time,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

NEED='我是小美美甲店店主，帮我做个预约微信小程序，每个时段最多接两人，顾客还能自己取消预约。预算上限500虚拟币。'
AUTHORIZE='确认授权，委托合同和验收都由我确认。'
CONTRACT='确认这份码上飞的合同，按你列出的范围提供资料。'
REGISTER='授权实名注册。'
ACCEPT='确认验收。'

def record(cfg,out,page,nine,lab,box,proof,shot,send,wait,save):
    b=cfg['accounts']['b'];base=cfg['base'];home=Path(cfg['folder'])/'client-b'
    last_turn=['']
    proof['flow']='nail-original';proof['userTurns']=[]
    if cfg.get('onboarding'):proof['executionPath']='generic-primitives'
    def view():return box.http.get(base+'/api/workbench',headers={'Authorization':'Bearer '+box.token}).raise_for_status().json()
    def turn(kind,text):
        last_turn[0]=text
        proof['userTurns'].append({'kind':kind,'text':text});save();send(text)
    def local(table):
        path=home/'contracts.sqlite'
        if not path.exists():return {}
        with sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True) as db:
            return {k:json.loads(v) for k,v in db.execute('SELECT k,v FROM documents WHERE tbl=? ORDER BY rowid',(table,))}
    def prompt(words,label,seconds=600):
        # Do not inject technical rescue prompts. Failure remains a failed recording.
        until=time.monotonic()+seconds;last_snapshot=0
        while time.monotonic()<until:
            text=nine.locator('body').inner_text()
            if time.monotonic()-last_snapshot>15:
                last_snapshot=time.monotonic()
                (out/'nine-current.txt').write_text(text,encoding='utf-8')
                page.screenshot(path=str(out/'current.png'))
            bubbles=nine.locator('[data-testid="chat-panel-message-bubble"][data-variant="assistant"][data-state="final"]')
            text=bubbles.last.inner_text() if bubbles.count() else ''
            idle=nine.locator('[data-testid="chat-panel-input-send"][data-variant="stop"]').count()==0
            native=nine.locator('[data-testid="chat-panel-inline-question-item-body"]:visible')
            if native.count() and nine.locator('textarea:visible').count():text=native.last.inner_text()
            # A planning statement containing '授权' is not the actual consent question.
            question=('？' in text[-1200:] or '?' in text[-1200:]) and any(word in text[-1200:] for word in words)
            if idle and question:
                proof.setdefault('visibleQuestions',[]).append({'gate':label,'text':text})
                save()
                page.wait_for_timeout(7000)
                (out/('nine-'+label+'.txt')).write_text(nine.locator('body').inner_text(),encoding='utf-8')
                return
            page.wait_for_timeout(1000)
        (out/('nine-timeout-'+label+'.txt')).write_text(nine.locator('body').inner_text(),encoding='utf-8')
        raise TimeoutError('Jiuwen did not request '+label)
    assert not view()['orders'],'Use a fresh participant stack'
    turn('request',NEED);shot('need','assistant')
    prompt(('确认授权','委托授权','是否授权','是否同意','请确认'),'delegation-authorization')
    assert not view()['orders'] and not local('scopes'),'No paid proposal or implicit authorization before user consent'
    shot('authorization','assistant');turn('authorization',AUTHORIZE)
    wait(lambda d:any(e['op']=='discovery.search' for e in d['events']),'discovery');shot('discovery','a2x')
    proposals=wait(lambda d:d['orders'] if len(d['orders'])>=3 else None,'three-proposals',seconds=1200)
    assert sorted(float(o['total']) for o in proposals)==[239,259,299],proposals
    assert not any(o['signatures'].get('1') for o in proposals),'No final contract without confirmation'
    proof['quotes']=[{'cardDigest':o['card_digest'],'total':o['total'],'provider':o['providerName']} for o in proposals]
    chosen=next(o for o in proposals if float(o['total'])==299);cd=chosen['card_digest']
    proof.update(cardDigest=cd,focusedCardDigest=cd)
    lab.locator('[data-order="'+cd+'"]').first.wait_for(timeout=30000)
    lab.locator('[data-order="'+cd+'"]').first.click();lab.locator('#detail').evaluate('(e)=>e.close()')
    shot('quotes','service')
    prompt(('确认合同','签署合同','是否确认','请确认'),'contract')
    comparison=list(local('comparisons').values())
    assert comparison and comparison[-1]['selected']==local('cards')[cd]['card']['provider'],'Expected an actual recommendation of the authored service, not a forced user replacement'
    proof['recommendation']={'provider':chosen['providerName'],'reason':comparison[-1]['reason'],'source':'actual SDK comparison before contract confirmation'}
    shot('choice','contract');turn('contract',CONTRACT)
    wait(lambda d:any(o['card_digest']==cd and o['signatures'].get('1') for o in d['orders']),'signed')
    shot('contract','contract')
    prompt(('实名注册','实名授权','实名认证'),'registration')
    order=next(o for o in view()['orders'] if o['card_digest']==cd)
    assert not any(order['signatures'].get('5',[])),'Production cannot bypass the unconfirmed step'
    proof['registration']={'step':'realname_registration','beforeDelivery':True,'mode':'authorization-only; no real identity submission'}
    shot('registration','service');turn('registration',REGISTER);shot('production','service')
    wait(lambda d:any(o['card_digest']==cd and any(o['signatures'].get('5',[])) for o in d['orders']),'delivery',seconds=1200)
    shot('delivery','contract')
    wait(lambda d:any(e['op']=='order.verified' and e['card_digest']==cd for e in d['events']),'verification',seconds=600)
    assert not any(next(o for o in view()['orders'] if o['card_digest']==cd)['signatures'].get('7',[]))
    prompt(('确认验收','亲签验收','请您验收','请确认接受'),'acceptance');shot('verification','assistant')
    turn('acceptance',ACCEPT)
    order=wait(lambda d:next((o for o in d['orders'] if o['card_digest']==cd and o['state']=='completed'),None),'completion')
    assert order['signatures']['7']==[True] and float(order['paid'])==299
    proof['delegation']={'state':order['state'],'cardDigest':cd,'signatures':order['signatures'],'paid':order['paid']}
    scopes=list(local('scopes').values());assert len(scopes)==1
    scope=scopes[0];assert scope['selected']==cd and len(scope['requests'])==3
    proof['authorization']={k:scope[k] for k in ('budget','proposalFeeLimit','expiresAt')}
    proof['authorization']['proposalFees']=sum(float(r['fee']) for r in scope['requests'].values())
    proof['confirmationGates']=['authorization','contract','registration','acceptance']
    shot('payment','payment')
    lab.locator('#logout').click();lab.locator('#name').fill(cfg['providerAccount']['username']);lab.locator('#password').fill(cfg['providerAccount']['password'])
    lab.locator('#auth button[type=submit]').click();lab.locator('#workspace').wait_for(state='visible');lab.locator('[data-view=provider]').click()
    page.locator('body').evaluate('(e)=>e.classList.add("full")');page.locator('#rightlabel').evaluate('(e)=>e.textContent="服务运营台 · 码上飞"')
    lab.locator('[data-service]').filter(has_text='码上飞').click()
    assert cd[:23] in lab.locator('#services').inner_text();shot('provider')
    files=list((home/'deliveries'/cd.split(':')[-1]/'1').glob('booking.json'));assert len(files)==1
    proof['deliveryFile']=str(files[0]);save()
    import importlib.util
    spec=importlib.util.spec_from_file_location('trusted_booking_runner',ROOT/'examples/booking_app.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    tool=f'http://127.0.0.1:{port}';config=json.loads(files[0].read_text(encoding='utf-8'))
    thread=threading.Thread(target=runner.serve,args=(config,out/'booking-demo.sqlite',port),daemon=True);thread.start()
    page.locator('#right').evaluate('(e,url)=>e.src=url',tool);lab.locator('#form').wait_for()
    page.locator('#rightlabel').evaluate('(e)=>e.textContent="已验收交付 · 预约工具"')
    # shot() owns the capture clock; perform the real interactions during its recorded interval.
    def book_and_cancel():
        lab.locator('#name').fill('预约客户');lab.locator('#start').fill('2026-10-30T11:00');lab.locator('#form button').click();lab.get_by_text('预约成功',exact=True).wait_for()
        page.wait_for_timeout(2500);lab.get_by_role('button',name='取消预约').click()
    shot('tool',action=book_and_cancel)
    proof['bookingDemo']=box.http.get(tool+'/bookings').raise_for_status().json();assert len(proof['bookingDemo'])==1 and proof['bookingDemo'][0]['cancelled']==1
    page.locator('#right').evaluate('(e,url)=>e.src=url',base);lab.locator('#workspace').wait_for(state='visible');lab.locator('#logout').click()
    lab.locator('#name').fill(b['username']);lab.locator('#password').fill(b['password']);lab.locator('#auth button[type=submit]').click();lab.locator('#workspace').wait_for(state='visible')
    lab.locator('[data-order="'+cd+'"]').first.click();lab.locator('#detail').evaluate('(e)=>e.close()')
    page.locator('body').evaluate('(e)=>e.classList.remove("full")');page.locator('#rightlabel').evaluate('(e)=>e.textContent="我的个人助手"');shot('close','assistant');save()
