"""Record real pages in side-by-side iframes; editing is driven by saved raw timestamps."""

import argparse, json, socket, sys, threading, time, secrets, hashlib, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/access/sdk"))
from ioalab_sdk.messaging import MailboxClient




def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--case", choices=["delegation", "messaging"], required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--resume-quotes", action="store_true", help="Resume the same unsigned quote checkpoint without replaying business actions")
    a = p.parse_args()
    cfg = json.loads(a.config.read_text(encoding="utf-8"))
    if cfg.get("scenario", a.case) != a.case:
        raise ValueError("Use the independent participant stack for this scenario")
    out = a.out.resolve()
    if not a.resume_quotes:out.mkdir(parents=True, exist_ok=False)
    if cfg.get("fixtures"):
        raise ValueError("Formal cases reject fixture participants")
    from urllib.parse import urlsplit

    if any(urlsplit(cfg[k]).hostname != "127.0.0.1" for k in ("base", "provider")):
        raise ValueError(
            "This recording harness is limited to local simulation participants"
        )
    b = cfg["accounts"]["b"]
    import httpx
    box = None if cfg.get('onboarding') and not a.resume_quotes else MailboxClient(cfg['base'],b['username'],b['password'],Path(cfg['folder'])/'client-b')
    health = httpx.get(cfg['provider']+'/health',trust_env=False).raise_for_status().json()
    assert health["execution"] == "swarm"
    proof = {
        "case": a.case,
        "fixtures": [],
        "screens": "real browser pages",
        "providerExecution": "swarm",
        "browserErrors": [],
    }
    if cfg.get('swarmModelName'):
        proof['assistantModel']={'id':cfg['swarmModelId'],'name':cfg['swarmModelName'],'alias':cfg['swarmModelAlias']}
    if cfg.get('onboarding'):
        skill=Path(cfg['skillDirectory'])
        proof['skillAudit']={
            'mainSha256':hashlib.sha256((skill/'SKILL.md').read_bytes()).hexdigest(),
            'delegationSha256':hashlib.sha256((skill/'references/delegation.md').read_bytes()).hexdigest(),
            'privateInteractionSha256':hashlib.sha256((skill/'references/private-interaction.md').read_bytes()).hexdigest(),
            'legacyExampleFilesPresent':any((skill/'references'/n).exists() for n in ('nail-booking.md','nail-booking-plan.json')),
        }
        if proof['skillAudit']['legacyExampleFilesPresent']:raise ValueError('Migrate obsolete installed scenario references before recording a generic skill')
    segments = []
    source = 'screen.webm'
    if a.resume_quotes:
        from artifacts import validate_delegation_resume, require_offer_window
        prior=json.loads((out/'evidence.json').read_text('utf-8'))
        segments=json.loads((out/'segments.json').read_text('utf-8'))
        orders=box.http.get(cfg['base']+'/api/workbench',headers={'Authorization':'Bearer '+box.token}).raise_for_status().json()['orders']
        validate_delegation_resume(prior,segments,orders,proof.get('skillAudit'))
        import sqlite3
        dbpath=Path(cfg['folder'])/'client-b/contracts.sqlite'
        with sqlite3.connect('file:'+dbpath.as_posix()+'?mode=ro',uri=True) as db:
            cards={k:json.loads(v)['card'] for k,v in db.execute("SELECT k,v FROM documents WHERE tbl='cards'")}
            templates={k:json.loads(v) for k,v in db.execute("SELECT k,v FROM documents WHERE tbl='templates'")}
        for quote in prior['quotes']:
            card=cards[quote['cardDigest']];require_offer_window(card,templates[card['templateDigest']])
        assert not (out/'continuation.webm').exists() and not (out/'continuation-raw').exists(), 'Keep earlier continuation captures'
        sessions=[p.parent.name for p in (ROOT/'.swarm-runtime/agent/sessions').glob('*/metadata.json') if json.loads(p.read_text('utf-8')).get('user_id')==cfg['swarmUserId']]
        assert len(sessions)==1,'Expected the same unique native session'
        originals=list((out/'raw').glob('*.webm'));assert len(originals)==1
        if not (out/'screen.webm').exists():shutil.copyfile(originals[0],out/'screen.webm')
        assert hashlib.sha256(originals[0].read_bytes()).digest()==hashlib.sha256((out/'screen.webm').read_bytes()).digest()
        shutil.copyfile(out/'evidence.json',out/'evidence-before-continuation.json')
        proof=prior;source='continuation.webm';cfg['_resume']=True
        cfg['swarmWeb']=cfg['swarmWeb'].rstrip('/')+'/chat/'+sessions[0]
        proof['continuation']={'reason':'Resume a real material question after the unsigned quote checkpoint; no business replay','sameContract':proof['cardDigest'],'source':source,'originalSha256':hashlib.sha256((out/'screen.webm').read_bytes()).hexdigest()}

    html = """<!doctype html><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:#080f1b;color:#e7f5ff;font:18px "Microsoft YaHei"}header{height:42px;display:flex;justify-content:space-between;padding:10px 28px;background:#101f31}#panels{display:grid;grid-template-columns:1fr 1fr;height:928px;gap:3px}iframe{width:100%;height:100%;border:0;background:#0b1421}#cover{position:fixed;inset:42px 0 110px;z-index:20;background:radial-gradient(ellipse at center,#17374b,#0b1424 70%);display:grid;place-content:center;text-align:center}h1{font-size:68px;margin:0 0 26px;color:#c9fff4}h2{font-size:34px;color:#9fced9;font-weight:400}#bottom{height:110px;border-top:2px solid #79daca}body.full #panels{grid-template-columns:0 1fr}body.full #left{visibility:hidden}#tag{position:fixed;bottom:118px;right:20px;font-size:14px;color:#94aabd;background:#09121cdd;padding:6px 10px}</style><header><b id="leftlabel">九问 Swarm</b><span>九问IoA实验平台 · 实际界面录屏</span><b id="rightlabel">我的个人助手</b></header><div id="panels"><iframe id="left"></iframe><iframe id="right"></iframe></div><div id="bottom"></div><div id="tag">真实九问与协议 · 虚拟币 · 长等待将在剪辑中缩短</div><div id="cover"><h1>CASE_TITLE</h1><h2>九问IoA实验平台 · Jiuwen IoA Lab</h2></div><script>left.src=NINE_URL;right.src=LAB_URL;</script>"""
    html = (
        html.replace(
            "CASE_TITLE",
            (
                "美甲预约 · 合同委托"
                if a.case == "delegation"
                else "个人助手 · 留言与预约"
            ),
        )
        .replace("NINE_URL", json.dumps(cfg["swarmWeb"] + "/?user_id="+cfg.get("swarmUserId","ioa-demo-b")))
        .replace("LAB_URL", json.dumps(cfg["base"]))
    )

    from onboarding import editor_html,write_settings
    editor_token=secrets.token_urlsafe(24)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                if self.path!='/save-settings' or self.headers.get('X-Local-Editor-Token')!=editor_token:raise ValueError('Invalid local editor request')
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<4096:raise ValueError('Invalid configuration size')
                result=write_settings(cfg,json.loads(self.rfile.read(size)))
                raw=json.dumps(result).encode();self.send_response(200)
            except (ValueError,KeyError) as e:
                raw=json.dumps({'error':str(e)}).encode();self.send_response(400)
            self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)

        def do_GET(self):
            raw = (editor_html(editor_token) if self.path=='/configuration' else html).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(raw)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from playwright.sync_api import (
        sync_playwright,
        TimeoutError as PlaywrightTimeoutError,
    )

    def save():
        (out / "segments.json").write_text(
            json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "evidence.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                record_video_dir=out / ("continuation-raw" if a.resume_quotes else "raw"),
                record_video_size={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            capture = time.monotonic()
            page.on("pageerror", lambda e: proof["browserErrors"].append(str(e)))
            page.goto(f"http://127.0.0.1:{srv.server_port}")
            page.locator("#cover").evaluate('(e)=>e.style.pointerEvents="none"')
            page.add_style_tag(
                content="#panels{grid-template-columns:minmax(0,1fr) minmax(0,1fr);overflow:hidden}#left{width:calc(100% + 312px);min-width:0;position:relative;left:-312px}"
            )
            nine = page.frame_locator("#left")
            lab = page.frame_locator("#right")
            if not cfg.get('onboarding') or a.resume_quotes:
                lab.locator('#name').fill(b['username']);lab.locator('#password').fill(b['password'])
                lab.locator('#auth button[type=submit]').click();lab.locator('#workspace').wait_for(state='visible')
            page.locator('#left').evaluate('(e)=>{e.style.width="100%";e.style.left="0"}')
            if not a.resume_quotes:nine.get_by_role('button',name='新建任务',exact=True).click()
            page.locator('#left').evaluate('(e)=>{e.style.width="calc(100% + 312px)";e.style.left="-312px"}')
            nine.locator("[contenteditable=true]").last.wait_for(timeout=20000)
            try:
                nine.get_by_text("qwen-flash", exact=True).last.wait_for(timeout=8000)
            except PlaywrightTimeoutError:
                pass  # The previously selected model may already be the configured alias.
            desired=cfg['swarmModelAlias']
            for current in ('qwen-flash','IoA演示模型'):
                selector=nine.get_by_text(current,exact=True)
                if current!=desired and selector.count() and selector.last.is_visible():
                    selector.last.click()
                    nine.get_by_text(desired,exact=True).last.click()
                    break
            nine.get_by_text(desired,exact=True).last.wait_for(state="visible",timeout=20000)

            def shot(key, node=None, seconds=18, action=None):
                if node:
                    lab.locator("[data-graph-node=" + node + "]").click()
                    lab.locator(".ioa-network").evaluate(
                        '(e)=>e.scrollIntoView({block:"start"})'
                    )
                start = time.monotonic() - capture
                if action: action()
                page.screenshot(path=str(out / (key + ".png")))
                page.wait_for_timeout(seconds * 1000)
                segments.append(
                    {"id": key, "start": round(start, 3), "duration": seconds, "source": source}
                )
                save()
                print("SHOT", key, flush=True)

            def send(text):
                editor = nine.locator("[contenteditable=true]").last
                if not editor.count() or not editor.is_visible():
                    confirm=nine.get_by_role('button',name='确定',exact=True)
                    if confirm.count() and confirm.last.is_visible():
                        # A blank native submission is not a user answer. Fill the actual answer control.
                        answer=nine.locator('textarea:visible')
                        if answer.count()!=1 or not answer.last.is_enabled():
                            raise RuntimeError('Expected one editable native question answer field; will not submit empty consent')
                        from artifacts import native_answer_seen
                        traces=[p.parent/'history.jsonl' for p in (ROOT/'.swarm-runtime/agent/sessions').glob('*/metadata.json') if json.loads(p.read_text('utf-8')).get('user_id')==cfg['swarmUserId']]
                        if len(traces)!=1:raise RuntimeError('Expected one native session for answer acknowledgement')
                        trace=traces[0];offset=trace.stat().st_size
                        answer.last.fill(text)
                        page.wait_for_timeout(1000)
                        if answer.last.input_value()!=text:raise RuntimeError('Native answer readback mismatch')
                        confirm.last.click()
                        until=time.monotonic()+20
                        while not native_answer_seen(trace,offset,text):
                            if time.monotonic()>=until:raise RuntimeError('Native answer not acknowledged; do not resubmit')
                            page.wait_for_timeout(200)
                        # Refresh the same session view after acknowledgement if the old questionnaire remains.
                        native_frame=page.locator('#left').element_handle().content_frame()
                        native_frame.goto(native_frame.url)
                        proof.setdefault('nativeConfirmations',[]).append({'semanticReply':text,'action':'filled and reread native answer before clicking 确定'})
                        save();print('Submitted answer to visible native Jiuwen question',flush=True);return
                    raise RuntimeError('Expected a visible native composer or confirmation control')
                if cfg.get('preloadIoASkill') and not proof.get('nativeSkillPreload'):
                    editor.fill('/')
                    nine.get_by_text('ioalab-delegate',exact=True).last.click()
                    editor.press('End')
                    editor.type(' '+text)
                    proof['nativeSkillPreload']='selected in the native Jiuwen skill menu before the first business request'
                elif proof.get('nativeSkillPreload') and not proof.get('firstBusinessRequestSent'):
                    editor.press('End');editor.type(' '+text)
                else:editor.fill(text)
                proof['firstBusinessRequestSent']=True
                editor.press("Enter")
                print("Submitted visible Jiuwen turn", flush=True)

            seen = set()

            def operator():
                command = out / "operator.json"
                if not command.exists():
                    return
                import re

                item = json.loads(command.read_text(encoding="utf-8-sig"))
                ident = item["id"]
                if not re.fullmatch("[A-Za-z0-9_-]{1,80}", ident):
                    raise ValueError("Invalid operator marker")
                if ident in seen:
                    return
                if item["kind"] == "message":
                    if (
                        not isinstance(item.get("text"), str)
                        or not 1 <= len(item["text"]) <= 4000
                    ):
                        raise ValueError("Invalid operator message")
                    send(item["text"])
                elif item["kind"] != "snapshot":
                    raise ValueError("Unknown operator action")
                seen.add(ident)
                page.screenshot(path=str(out / ("operator-" + ident + ".png")))
                (out / ("operator-" + ident + ".txt")).write_text(
                    nine.locator("body").inner_text(), encoding="utf-8"
                )
                proof.setdefault("operatorActions", []).append(
                    {
                        "id": ident,
                        "kind": item["kind"],
                        "at": round(time.monotonic() - capture, 3),
                    }
                )
                save()

            def view():
                return (
                    box.http.get(
                        cfg["base"] + "/api/workbench",
                        headers={"Authorization": "Bearer " + box.token},
                    )
                    .raise_for_status()
                    .json()
                )

            def wait(predicate, label, seconds=900):
                until = time.monotonic() + seconds
                while time.monotonic() < until:
                    operator()
                    value = view()
                    result = predicate(value)
                    if result:
                        return result
                    page.wait_for_timeout(1000)
                page.screenshot(path=str(out / ("timeout-" + label + ".png")))
                (out / ("nine-" + label + ".txt")).write_text(
                    nine.locator("body").inner_text(), encoding="utf-8"
                )
                raise TimeoutError(label)

            if not a.resume_quotes:shot("cover")
            page.locator("#cover").evaluate("(e)=>e.remove()")
            if cfg.get('onboarding') and not a.resume_quotes:
                page.locator('body').evaluate('(e)=>e.classList.add("full")')
                def register():
                    lab.locator('#name').fill(b['username']);lab.locator('#password').fill(b['password'])
                    page.wait_for_timeout(4000)
                    lab.locator('#register').click();lab.locator('#workspace').wait_for(state='visible')
                    proof['onboarding']={'account':b['username'],'registration':'actual UI registration','configuration':'pending','skillSelection':'pending'}
                shot('account',action=register)
                # Explicit administrative preparation of simulation credit; never a real transfer.
                with httpx.Client(base_url=cfg['base'],trust_env=False) as admin:
                    admin.post('/api/admin/login',json={'password':cfg['demoAdminPassword']}).raise_for_status()
                    users=admin.get('/api/admin/users').raise_for_status().json()
                    uid=next(u['id'] for u in users if u['username']==b['username'])
                    admin.post('/api/admin/wallet',json={'user_id':uid,'delta':'1000','note':'显式演示虚拟额度'}).raise_for_status()
                proof['onboarding']['demoCredit']=1000
                page.locator('#right').evaluate('(e,url)=>e.src=url',f'http://127.0.0.1:{srv.server_port}/configuration')
                lab.locator('#configuration').wait_for()
                def configure():
                    values={'IOALAB_BASE_URL':cfg['base'],'IOALAB_USERNAME':b['username'],'IOALAB_PASSWORD':b['password']}
                    values.update(cfg.get('delegationPolicy',{}))
                    lab.locator('#configuration').fill(json.dumps(values,ensure_ascii=False,indent=2))
                    page.wait_for_timeout(1800);lab.locator('#save').click();lab.locator('#status').filter(has_text='已保存').wait_for()
                    proof['onboarding']['configuration']='actual skill file saved and reread'
                    proof['onboarding']['delegationPolicy']=cfg.get('delegationPolicy',{})
                shot('configuration',action=configure)
                box=MailboxClient(cfg['base'],b['username'],b['password'],Path(cfg['folder'])/'client-b')
                page.locator('#right').evaluate('(e,url)=>e.src=url',cfg['base']);lab.locator('#workspace').wait_for(state='visible')
                page.locator('body').evaluate('(e)=>e.classList.remove("full")')
                def select():
                    editor=nine.locator('[contenteditable=true]').last;editor.fill('/')
                    nine.get_by_text('ioalab-delegate',exact=True).last.wait_for();page.wait_for_timeout(1700)
                    nine.get_by_text('ioalab-delegate',exact=True).last.click()
                    proof['nativeSkillPreload']='selected visibly in the native Jiuwen menu before the business request'
                    proof['onboarding']['skillSelection']='native skill menu'
                shot('skill',action=select)
            if a.case == "delegation":
                from delegation_case import record
                record(cfg,out,page,nine,lab,box,proof,shot,send,wait,save)
            else:
                from message_case import record

                record(cfg, out, page, nine, lab, box, proof, shot, send, wait, save)
            page.screenshot(path=str(out / "final.png"))
            video = page.video
            context.close()
            video.save_as(out / source)
            browser.close()
            proof["rawDuration"] = round(time.monotonic() - capture, 3)
            save()
    finally:
        if box:box.close()
        srv.shutdown()


if __name__ == "__main__":
    main()
