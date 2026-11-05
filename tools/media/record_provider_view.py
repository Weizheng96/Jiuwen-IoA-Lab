"""Retake one real provider view from an already completed, unchanged contract."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from artifacts import read_json, require, validate_real_case, write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--recording',type=Path,required=True);p.add_argument('--source-name',default='provider-retake')
    a=p.parse_args();out=a.recording.resolve();cfg=read_json(a.config);proof=read_json(out/'evidence.json')
    validate_real_case(proof)
    import re
    require(re.fullmatch('[A-Za-z0-9_-]{1,60}',a.source_name),'Invalid owned video source name')
    source=a.source_name+'.webm'
    require(not (out/source).exists(),'Keep previous retakes; use a new source for another attempt')
    cd=proof['cardDigest'];name=next(q['provider'] for q in proof['quotes'] if q['cardDigest']==cd)
    html='''<!doctype html><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:#080f1b;color:#e7f5ff;font:18px Microsoft YaHei}header{height:42px;display:flex;justify-content:space-between;padding:10px 28px;background:#101f31}iframe{width:100%;height:928px;border:0;display:block}footer{height:110px;border-top:2px solid #79daca}</style><header><b>服务方视角</b><span>九问IoA实验平台 · 实际界面录屏</span><b>服务运营台 · SERVICE</b></header><iframe id="app"></iframe><footer></footer>'''.replace('SERVICE',name)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            raw=html.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch();context=browser.new_context(viewport={'width':1920,'height':1080},record_video_dir=out/(a.source_name+'-raw'),record_video_size={'width':1920,'height':1080})
            page=context.new_page();origin=time.monotonic();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}');page.locator('#app').evaluate('(e,u)=>e.src=u',cfg['base'])
            app=page.frame_locator('#app');account=cfg['providerAccount']
            app.locator('#name').fill(account['username']);app.locator('#password').fill(account['password']);app.locator('#auth button[type=submit]').click()
            app.locator('#workspace').wait_for(state='visible');app.locator('[data-view=provider]').click()
            app.locator('[data-service]').filter(has_text=name).click();page.wait_for_timeout(1200)
            actual=context.request.get(cfg['base']+'/api/workbench').json()
            owned=[o for s in actual['services'] for o in s['orders'] if o['card_digest']==cd]
            require(len(owned)==1 and owned[0]['state']=='completed' and float(owned[0]['paid'])==299,'Same completed and paid contract required')
            require(app.locator('.provider-hero').bounding_box()['y']>=app.locator('.provider-layout').bounding_box()['y']-1,'Selected service title was scrolled away')
            app.locator('.provider-nav').evaluate('(e)=>e.scrollTop=0')
            start=time.monotonic()-origin;page.screenshot(path=str(out/(a.source_name+'.png')));page.wait_for_timeout(4000)
            app.locator('[data-service]').filter(has_text=name).scroll_into_view_if_needed()
            app.locator('.provider-section').last.scroll_into_view_if_needed();page.wait_for_timeout(14000)
            require(not errors,'Provider page errors')
            video=page.video;context.close();video.save_as(out/source);browser.close()
        segments=read_json(out/'segments.json');write_json(out/('segments-before-'+a.source_name+'.json'),segments)
        index=next(i for i,s in enumerate(segments) if s['id']=='provider');segments[index]={'id':'provider','start':round(start,3),'duration':18,'source':source}
        proof['providerRetake']={'source':source,'sameContract':cd,'reason':'Independent navigation/content scrolling keeps service title visible','browserErrors':errors,'titleVisible':True}
        write_json(out/'segments.json',segments);write_json(out/'evidence.json',proof)
        print('Same-contract provider view retaken; title, real history and payment checked')
    finally:server.shutdown();server.server_close()


if __name__=='__main__':main()
