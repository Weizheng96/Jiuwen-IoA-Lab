"""Record the live admin UI while independent clients perform real protocol work."""
import argparse
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'experiments'))
from network import NetworkTraffic


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--out',type=Path,default=ROOT/'recordings')
    parser.add_argument('--voice-timing',type=Path,help='Use measured narration durations to time camera holds')
    args=parser.parse_args()
    config=json.loads(args.config.read_text(encoding='utf-8'))
    out=args.out.resolve()/('global-'+time.strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True,exist_ok=False)
    traffic=NetworkTraffic(config,Path(config['folder'])/out.name)
    captions=[];errors=[]
    timing={x['text']:x['duration'] for x in json.loads(args.voice_timing.read_text(encoding='utf-8'))} if args.voice_timing else {}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch()
        context=browser.new_context(viewport={'width':1920,'height':1080},record_video_dir=out/'raw',record_video_size={'width':1920,'height':1080})
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        capture=time.monotonic()
        page.goto(config['base'])
        a=config['accounts']['a']
        page.locator('#name').fill(a['username']);page.locator('#password').fill(a['password']);page.locator('#auth button[type=submit]').click()
        page.locator('#workspace').wait_for(state='visible')
        traffic.start()
        page.goto(config['base']+'/admin?view=panorama')
        page.locator('#mainView').wait_for(state='visible')
        page.locator('.pn-asst').first.wait_for()
        page.wait_for_timeout(3000)
        started=time.monotonic()
        lead=started-capture
        def shot(text,voice,hold=9):
            captions.append({'at':round(time.monotonic()-started,2),'text':text,'voice':voice})
            page.wait_for_timeout((timing.get(text,hold-.5)+.5)*1000)
        try:
            shot('一个活跃的智能体互联网','个人助手、专业服务和公共组件，组成一个共同工作的智能体网络。')
            page.locator('#pnRegistry').click()
            shot('找服务，先经过 A2X','左侧的仿真个人助手，经真实 SDK 发现服务、定约和交付。右侧按领域组织专业服务。')
            page.locator('#pnMessages').click()
            shot('委托与消息，两条交互路径','付费委托遵循 A2C 合同，免费消息按账号定向投递；多方可以同时参与。')
            node=page.locator('.pn-asst').filter(has_text=traffic.accounts[0]['username']).first
            node.click()
            shot('聚焦一人，看清关联活动','选择一个助手，同账号的参与方和关联活动高亮，其余变淡，便于追踪一件事。')
            page.keyboard.press('Escape')
            shot('全局可观察，参与方独立运行','平台连接和观察整个网络。个人助手在客户端运行，服务方独立生产，签字和验收由本地 SDK 完成。',10)
        finally:
            result=traffic.close()
            duration=time.monotonic()-started
            page.screenshot(path=str(out/'review-live.png'))
            video=page.video
            context.close();video.save_as(out/'screen.webm');browser.close()
    orders=[r for r in result['records'] if r['kind']=='order']
    messages=[r for r in result['records'] if r['kind']=='message']
    proof={'case':'global','layout':'single','screens':'real browser pages','browserErrors':errors,'captureLead':round(lead,2),'duration':round(duration,2),'fixtures':config.get('fixtures',[]),'experiment':{'participants':result['participants'],'completedOrders':len(orders),'messages':len(messages),'errors':result['errors']}}
    (out/'evidence.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'captions.json').write_text(json.dumps(captions,ensure_ascii=False,indent=2),encoding='utf-8')
    assert not errors and not result['errors'] and len(orders)>=4 and len(messages)>=4,proof
    print('Global recording saved: '+str(out))


if __name__=='__main__':main()
