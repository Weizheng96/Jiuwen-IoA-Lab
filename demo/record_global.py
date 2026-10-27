"""Record the live admin UI while independent clients perform real protocol work."""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'experiments'))
from network import NetworkTraffic
from global_story import SHOTS,REFERENCE,NAME


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--out',type=Path,default=ROOT/'recordings')
    parser.add_argument('--voice-timing',type=Path,required=True,help='Measured narration prepared from global_story.py')
    args=parser.parse_args()
    config=json.loads(args.config.read_text(encoding='utf-8'))
    out=args.out.resolve()/('global-'+time.strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True,exist_ok=False)
    prepared=json.loads(args.voice_timing.read_text(encoding='utf-8'))
    timing={line['id']:line for line in prepared}
    voices=[]
    for shot in SHOTS:
        line=timing[shot['id']]
        if line['text']!=shot['text'] or line.get('voice')!=shot['voice']:raise ValueError('Narration changed; rebuild audio before recording')
        source=(args.voice_timing.parent/line['file']).resolve()
        if not source.is_relative_to(args.voice_timing.parent.resolve()):raise ValueError('Voice asset escapes narration directory')
        dest=out/'voice'/source.name;dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(source,dest)
        line['file']=str(dest.relative_to(out))
    traffic=NetworkTraffic(config,Path(config['folder'])/out.name)
    captions=[];errors=[];guide_frames=[]
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
        def participant(index):
            node=page.locator('.pn-asst').filter(has_text=traffic.accounts[index]['username']).first
            return '[data-id="'+node.get_attribute('data-id')+'"]'
        service=page.locator('.pn-svc').filter(has_text='用药提醒表工坊').first
        service.scroll_into_view_if_needed()
        refs={'assistant':participant(0),'other':participant(1),'service':'[data-id="'+service.get_attribute('data-id')+'"]',
              'market':'#pnMarket','registry':'#pnRegistry','templates':'#pnTemplates','notary':'#pnNotary','ledger':'#pnLedger','messages':'#pnMessages','payment':'#pnPay'}
        page.add_script_tag(path=str(ROOT/'demo/guide_overlay.js'))
        platform_width=page.locator('.pn-mid').bounding_box()['width']
        service_width=page.locator('.pn-right').bounding_box()['width']
        assert platform_width>service_width*1.5,'Platform components must dominate the service column'
        started=time.monotonic()
        lead=started-capture
        try:
            for shot in SHOTS:
                if shot.get('action')=='focus':page.locator(refs['assistant']).click()
                if shot.get('action')=='clear':page.keyboard.press('Escape')
                page.evaluate('([shot,refs])=>window.IoaDemoGuide.show(shot,refs)',[shot,refs])
                at=round(time.monotonic()-started,2)
                shot_started=time.monotonic()
                captions.append({'id':shot['id'],'at':at,'text':shot['text'],'voice':shot['voice']})
                voices.append({**timing[shot['id']],'at':at})
                page.wait_for_timeout(500)
                if shot['id'] in ('intro','discovery','delivery','messaging','internal','external'):
                    frame='guide-'+shot['id']+'.png';page.screenshot(path=str(out/frame));guide_frames.append(frame)
                page.wait_for_timeout(max(0,shot_started+timing[shot['id']]['duration']+.35-time.monotonic())*1000)
        finally:
            result=traffic.close()
            duration=time.monotonic()-started
            page.screenshot(path=str(out/'review-live.png'))
            video=page.video
            context.close();video.save_as(out/'screen.webm');browser.close()
    orders=[r for r in result['records'] if r['kind']=='order']
    messages=[r for r in result['records'] if r['kind']=='message']
    proof={'case':'global','layout':'single','screens':'real browser pages','browserErrors':errors,'captureLead':round(lead,2),'duration':round(duration,2),'fixtures':config.get('fixtures',[]),
           'displayName':NAME,'narrationReference':REFERENCE,'editorialOverlay':{'labelled':True,'businessStateModified':False,'reviewFrames':guide_frames},
           'layoutWidths':{'platform':platform_width,'services':service_width},
           'experiment':{'participants':result['participants'],'completedOrders':len(orders),'messages':len(messages),'formations':sorted({r['formation'] for r in orders}),'errors':result['errors']}}
    (out/'evidence.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'captions.json').write_text(json.dumps(captions,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'voice.json').write_text(json.dumps(voices,ensure_ascii=False,indent=2),encoding='utf-8')
    assert not errors and not result['errors'] and len(orders)>=4 and len(messages)>=4,proof
    print('Global recording saved: '+str(out))


if __name__=='__main__':main()
