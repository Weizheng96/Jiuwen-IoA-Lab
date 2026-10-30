"""Record actual Jiuwen + Lab browser pages; never replay fabricated protocol data."""
import argparse,json,os,subprocess,sys,threading,time,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src/access/sdk'))
from ioalab_sdk.messaging import MailboxClient

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--case',choices=['delegation','messaging'],required=True);parser.add_argument('--out',type=Path,default=ROOT/'recordings');parser.add_argument('--headed',action='store_true');parser.add_argument('--formal',action='store_true');args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8'));folder=Path(config['folder']);out=args.out.resolve()/(args.case+'-'+time.strftime('%Y%m%d-%H%M%S'));out.mkdir(parents=True)
    if not config.get('swarm'):raise ValueError('Real Jiuwen WebChannel is required for both recordings')
    if args.formal and config.get('fixtures'):raise ValueError('Formal recording requires real provider, calendar and notification configuration; fixtures are not allowed')
    if args.formal and args.case=='messaging' and not (args.headed and config.get('outlookStorageState')):raise ValueError('Formal meeting acceptance requires --headed and a locally authorized outlookStorageState; user accepts the invitation in Outlook')
    from playwright.sync_api import sync_playwright
    from verify_local import wait_message
    from urllib.parse import urlsplit,parse_qs
    nine_origin=config.get('swarmWeb','http://127.0.0.1:15173');b=config['accounts']['b'];a=config['accounts']['a']
    box=MailboxClient(config['base'],b['username'],b['password'],folder/'client-b',connect_identity=True)
    captions=[];started=time.monotonic();proof={'case':args.case,'fixtures':config.get('fixtures',[]),'screens':'real browser pages','started':time.time()}
    def mark(text):captions.append({'at':round(time.monotonic()-started,2),'text':text})
    def login(page,account):
        page.goto(config['base']);page.locator('#name').fill(account['username']);page.locator('#password').fill(account['password']);page.locator('#auth button[type=submit]').click();page.locator('#workspace').wait_for(state='visible')
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=not args.headed)
        contexts=[browser.new_context(viewport={'width':960,'height':1080},record_video_dir=out/side,record_video_size={'width':960,'height':1080}) for side in ('left','right')]
        capture_started=time.monotonic();left,right=[c.new_page() for c in contexts]
        for page in (left,right):page.on('pageerror',lambda e:errors.append(str(e)))
        login(right,b);left.goto(nine_origin+'/?user_id='+('ioa-demo-b' if args.case=='delegation' else 'ioa-admin'));left.wait_for_timeout(3000)
        started=time.monotonic();proof['captureLead']=round(started-capture_started,2);mark('本地联调：真实九问与协议；服务/日历/收件使用明确测试配置。' if config.get('fixtures') else '真实九问与IoA平台端到端交互。')
        if args.case=='delegation':
            from nine_driver import DELEGATION
            sys.path.insert(0,str(ROOT/'official-services'))
            from catalog import definitions
            sample=next(d for d in definitions() if d['id']=='med-reminder')
            scenario=DELEGATION+' 本轮具体输入spec为：'+json.dumps({sample['deliverable']['name']:sample['sample_spec']},ensure_ascii=False)+'。这是用户已提供的数据，不要改写药名或擅自替换。'
            before={o['card_digest'] for o in box.http.get(config['base']+'/api/workbench',headers={'Authorization':'Bearer '+box.token}).raise_for_status().json()['orders']}
            # Use Jiuwen's real input and model selector, so its own session routing
            # and the visible conversation are exactly the execution being recorded.
            if config.get('swarmModelId'):
                left.get_by_text('qwen-flash',exact=True).last.click(timeout=15000)
                left.get_by_text(config.get('swarmModelAlias','IoA演示模型'),exact=False).last.click(timeout=15000)
            if config.get('skillDirectory'):
                script=(Path(config['skillDirectory'])/'scripts/ioalab.py').as_posix()
                scenario+=' 本机已经配置好的实际技能入口：python "'+script+'" me。直接使用该路径，不要读取settings.local.json。'
            editor=left.locator('[contenteditable=true]').last
            editor.fill(scenario);left.wait_for_timeout(2000);editor.press('Enter')
            mark('九问调用本地IoA技能，发现服务、读取模板并发起合同委托。')
            deadline=time.monotonic()+600;new=[]
            while time.monotonic()<deadline:
                view=box.http.get(config['base']+'/api/workbench',headers={'Authorization':'Bearer '+box.token}).raise_for_status().json()
                new=[o for o in view['orders'] if o['card_digest'] not in before and o['state']=='completed']
                if new:break
                right.wait_for_timeout(1000)
            if not new:raise RuntimeError('No newly completed contract; recording is not accepted')
            proof['delegation']={'cardDigest':new[0]['card_digest'],'state':new[0]['state'],'signatures':new[0]['signatures']}
            assert new[0]['signatures'].get('7')==[True],'Local signed acceptance was not observed'
            assert '该会话没有可恢复的消息' not in left.locator('body').inner_text(),'The visible Jiuwen page is not the executed conversation'
            mark('本地SDK完成取件、WASM验收和签字；平台记录合同完成。');right.reload();right.wait_for_timeout(4000)
            right.locator('#logout').click();login(right,config.get('providerAccount',{'username':'ISP_001','password':'001'}));right.locator('[data-view=provider]').click();right.locator('#services').wait_for(state='visible');right.wait_for_timeout(4000)
            assert new[0]['card_digest'][:23] in right.locator('#services').inner_text(),'Provider history is missing this contract'
            mark('切换服务方账号，按独立服务查看同一笔完成记录。');right.wait_for_timeout(5000)
        else:
            right.locator('#recipient').fill(a['username']);right.locator('#lookup button').click();right.locator('#compose').wait_for(state='visible')
            right.locator('#interface').select_option('message.forward');text='IoA 交流：请查看本次会议预约。'+time.strftime('%H:%M:%S');right.locator('[data-field=text]').fill(text);mark('B查询A的授权接口，按inputSchema发送结构化留言。');right.wait_for_timeout(2000);right.locator('#send button').click();right.wait_for_timeout(5000)
            messages=box.api('GET','/messaging/messages');forward=next(m for m in messages if m['payload'] and m['payload'].get('text')==text);forward=wait_message(box,forward['id'])
            assert forward['result'] and forward['result']['status']=='delivered',forward
            proof['forward']={'id':forward['id'],**forward['result']};left.reload();left.get_by_text('IoA 留言 · '+b['username'],exact=True).first.click(timeout=30000);left.get_by_text(text,exact=False).wait_for(timeout=20000)
            mark('A的真实九问会话收到留言；投递成功与用户已读分别记录。');right.wait_for_timeout(4000)
            start=datetime.now(timezone.utc)+timedelta(days=2)
            while start.weekday()>4:start+=timedelta(days=1)
            used={m['payload']['start'] for m in messages if m['interface']=='meeting.request' and m['payload'] and (m.get('result') or {}).get('status')=='invite_submitted'}
            start=start.replace(hour=3,minute=0,second=0,microsecond=0)
            while start.isoformat() in used:
                start+=timedelta(minutes=30)
                if start.hour>=8:start=(start+timedelta(days=1)).replace(hour=3,minute=0)
                while start.weekday()>4:start+=timedelta(days=1)
            end=start+timedelta(minutes=30)
            right.locator('#interface').select_option('meeting.request')
            for key,value in {'title':'IoA 项目交流 '+time.strftime('%H:%M:%S'),'start':start.isoformat(),'end':end.isoformat(),'timeZone':'Asia/Shanghai'}.items():right.locator('[data-field='+key+']').fill(value)
            mark('按预配置的预约规则自动判断时段，符合条件后发送日历邀请。');right.wait_for_timeout(3000);right.locator('#send button').click();right.wait_for_timeout(5000)
            messages=box.api('GET','/messaging/messages');meeting=next(m for m in messages if m['interface']=='meeting.request' and m['payload'] and m['payload'].get('start')==start.isoformat());meeting=wait_message(box,meeting['id'])
            assert meeting['result'] and meeting['result']['status']=='invite_submitted',meeting
            proof['meeting']={'id':meeting['id'],**meeting['result']};mark('邀请邮件已提交，等待用户接受；不会直接写入管理员日历。');right.wait_for_timeout(4000)
            if args.formal:
                outlook=browser.new_context(storage_state=config['outlookStorageState']);mail=outlook.new_page();mail.goto(config.get('outlookUrl','https://outlook.live.com/mail/'));input('请在真实Outlook页面接受邀请、查看日历后按Enter结束录制：');proof['outlookAcceptance']='user-reviewed';outlook.close()
            else:
                right.locator('#send button').click();right.wait_for_timeout(5000);mark('再次预约同一时段被拒绝。本地收件证据独立保存，未向外部发信。');proof['smtpEvidence']=[p.name for p in (folder/'mail').glob('*.eml')];right.wait_for_timeout(4000)
        for page,name in ((left,'left'),(right,'right')):page.screenshot(path=str(out/(name+'.png')))
        videos=[page.video for page in (left,right)]
        for c in contexts:c.close()
        for video,name in zip(videos,('left','right')):video.save_as(out/(name+'.webm'))
        browser.close()
    box.close();proof['browserErrors']=errors;proof['duration']=round(time.monotonic()-started,2)
    (out/'evidence.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8');(out/'captions.json').write_text(json.dumps(captions,ensure_ascii=False,indent=2),encoding='utf-8')
    assert not errors,errors
    print('Actual browser recording saved: '+str(out))

if __name__=='__main__':main()
