"""Read-only native menu routing preflight; no host patches and no business confirmations."""
import argparse,json,time
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--kind',choices=('delegation','private'),required=True);p.add_argument('--without-selection',action='store_true');a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False)
    request=('请帮我寻找翻译品牌介绍的专业服务，预算上限100虚拟币。' if a.kind=='delegation' else '帮我向另一个个人助手报个平安，告诉对方我已经安全到达。')
    events=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000})
        def ws_open(ws):
            def received(data):
                try:
                    item=json.loads(data)
                    if item.get('type')=='event':events.append(item)
                except (ValueError,TypeError):pass
            ws.on('framereceived',received)
        page.on('websocket',ws_open);page.goto('http://127.0.0.1:5173/?user_id=ioa-skill-check')
        page.get_by_role('button',name='新建任务',exact=True).click()
        editor=page.locator('[contenteditable=true]').last;editor.wait_for(timeout=20000)
        page.wait_for_timeout(2000)
        for name in ('qwen-flash','IoA演示模型'):
            item=page.get_by_text(name,exact=True)
            if item.count() and item.last.is_visible():
                if name=='qwen-flash':item.last.click();page.get_by_text('IoA演示模型',exact=True).last.click()
                break
        if not a.without_selection:
            editor.fill('/');page.get_by_text('ioalab-delegate',exact=True).last.click();editor.press('End');editor.type(' '+request)
        else:editor.fill(request)
        editor.press('Enter');until=time.monotonic()+150
        while time.monotonic()<until:
            bubbles=page.locator('[data-testid="chat-panel-message-bubble"][data-variant="assistant"][data-state="final"]')
            idle=page.locator('[data-testid="chat-panel-input-send"][data-variant="stop"]').count()==0
            if idle and bubbles.count():break
            page.wait_for_timeout(1000)
        text=page.locator('body').inner_text();(a.out/'screen.txt').write_text(text,encoding='utf-8');page.screenshot(path=str(a.out/'screen.png'))
        (a.out/'events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
        # Raw events remain local; emit only tool names and loader/branch evidence.
        calls=[]
        for item in events:
            payload=item.get('payload',{})
            if payload.get('event_type')=='chat.tool_call':
                call=payload.get('tool_call',{});args=call.get('arguments',{})
                if isinstance(args,str):
                    try:args=json.loads(args)
                    except ValueError:args={}
                calls.append((call.get('name'),args))
        flow='references/'+('delegation.md' if a.kind=='delegation' else 'private-interaction.md')
        import re
        def path_matches(args):
            path=args.get('relative_file_path') or args.get('file_path') or args.get('path') or ''
            return re.sub('/+','/',path.replace('\\','/')).endswith(flow)
        proof={'kind':a.kind,'selected':not a.without_selection,
               'mainSkillLoaded':any(n=='skill_tool' and args.get('skill_name')=='ioalab-delegate' and not args.get('relative_file_path') for n,args in calls),
               'flowRead':any(n in ('skill_tool','read_file') and path_matches(args) for n,args in calls),'request':request,'toolNames':[n for n,args in calls]}
        (a.out/'proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(proof,ensure_ascii=False))
        browser.close()

if __name__=='__main__':main()
