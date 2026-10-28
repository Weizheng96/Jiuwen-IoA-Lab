"""Actual assistant messages and receipts; no successful business request from the recorder."""
import json,time
from pathlib import Path
from ioalab_sdk.messaging import MailboxClient


def record(cfg,out,page,nine,lab,box,proof,shot,send,wait,save):
 a,b=cfg['accounts']['a'],cfg['accounts']['b'];target=a['username']
 def login(account):
  lab.locator('#logout').click();lab.locator('#name').fill(account['username']);lab.locator('#password').fill(account['password']);lab.locator('#auth button[type=submit]').click();lab.locator('#workspace').wait_for(state='visible')
 login(a);page.locator('body').evaluate('(e)=>e.classList.add("full")');lab.locator('#interfaces').scroll_into_view_if_needed()
 assert b['username'] in lab.locator('#interfaces').inner_text()
 outside=cfg['accounts']['outside'];other=MailboxClient(cfg['base'],outside['username'],outside['password'],Path(cfg['folder'])/'client-outside')
 try:
  assert other.interfaces(target)==[]
  definition=next(d for d in box.interfaces(target) if d['name']=='message.forward')
  try:other.api('POST','/messaging/messages',{'to':target,'interface':'message.forward','revision':definition['revision'],'payload':{'text':'Unauthorized negative test'},'idempotencyKey':'formal-acl-negative'})
  except Exception as exc:
   import httpx
   if not isinstance(exc,httpx.HTTPStatusError) or exc.response.status_code!=403:raise
   proof['accessControl']={'outsideInterfaces':[],'directSendStatus':403,'allowedAccount':b['username'],'identityMeaning':'platform account string'}
  else:raise AssertionError('Unauthorized send unexpectedly succeeded')
 finally:other.close()
 shot('policy')
 parent=page.url;page.locator('#right').evaluate('(e,u)=>e.src=u',parent+'schedule');lab.locator('#schedule').wait_for();shot('schedule')
 proof['schedule']={'source':'assistant local schedule file','file':str(Path(cfg['folder'])/'assistant-a/schedule.json'),'busy':cfg['appointments']['busy']};save()
 page.locator('#right').evaluate('(e,u)=>e.src=u',cfg['base']);lab.locator('#workspace').wait_for(state='visible');login(b);page.locator('body').evaluate('(e)=>e.classList.remove("full")')
 lab.locator('#recipient').fill(target);lab.locator('#lookup button').click();lab.locator('#interface').select_option('meeting.request');lab.locator('#fields').scroll_into_view_if_needed()
 busy=cfg['appointments']['busy'];free=cfg['appointments']['free'];note='请在九问IoA实验平台上交流协作方案，我想和你预约一次技术讨论。'
 prompt='本机技能已安装，请先用read_file读取'+cfg['skillDirectory'].replace('\\','/')+'/SKILL.md，不需要在线搜索或安装。使用mail命令实际操作，不要仅解释。先查询账号'+target+'的免费接口与inputSchema，再按message.forward接口发送留言：'+note+'。随后按meeting.request申请会议，参数严格为'+json.dumps(busy,ensure_ascii=False)+'。查询实际处理回执；如果时段冲突，告诉我结果，暂不改约。以上留言与这次预约已获我明确授权。Windows下JSON请用--args-file，避免命令行转义错误。'
 send(prompt);shot('lookup')
 def message(data,interface,title=None,status=None):
  return next((m for m in data['messages'] if m['senderUsername']==b['username'] and m['interface']==interface and (title is None or m['payload'].get('title')==title) and (status is None or (m.get('result') or {}).get('status')==status)),None)
 forward=wait(lambda d:message(d,'message.forward',status='delivered'),'forward');proof['forward']=forward['result'];proof['forwardMessageId']=forward['id'];save()
 b_url=nine.locator('body').evaluate('(e)=>location.href');proof['swarmBUrl']=b_url
 page.locator('#left').evaluate('(e,u)=>e.src=u',cfg['swarmWeb']+'/?user_id=ioa-admin')
 page.locator('#left').evaluate('(e)=>{e.style.width="100%";e.style.left="0"}')
 nine.get_by_text('IoA 留言 · '+b['username'],exact=True).first.wait_for(timeout=30000);nine.get_by_text('IoA 留言 · '+b['username'],exact=True).first.click();nine.get_by_text(note,exact=False).last.wait_for(timeout=30000)
 page.locator('#left').evaluate('(e)=>{e.style.width="calc(100% + 312px)";e.style.left="-312px"}')
 lab.locator('#messages').scroll_into_view_if_needed();shot('forward');proof['swarmAUrl']=nine.locator('body').evaluate('(e)=>location.href');save()
 page.locator('#left').evaluate('(e,u)=>e.src=u',b_url);nine.locator('[contenteditable=true]').last.wait_for(timeout=30000)
 conflict=wait(lambda d:message(d,'meeting.request',busy['title'],'no_time'),'busy');assert conflict['result']['reason']=='calendar_conflict';proof['busy']=conflict['result'];proof['busyMessageId']=conflict['id'];lab.locator('[data-message="'+conflict['id']+'"]').click();shot('busy');lab.locator('#detail').evaluate('(e)=>e.close()');save()
 page.wait_for_timeout(12000)
 send('已了解原时段冲突。我明确同意改约以下空闲时段，请通过相同技能发送实际meeting.request，并查询处理回执：'+json.dumps(free,ensure_ascii=False)+'。只提交这一次邀请，不重复发送；不要宣称Outlook已接受。');shot('reschedule','message')
 invitation=wait(lambda d:message(d,'meeting.request',free['title'],'invite_submitted'),'invitation');proof['meeting']=invitation['result'];proof['meetingMessageId']=invitation['id'];proof['appointment']=free;save()
 lab.locator('#messages').scroll_into_view_if_needed();lab.locator('[data-message="'+invitation['id']+'"]').click();shot('invitation');lab.locator('#detail').evaluate('(e)=>e.close()')
 proof['outlook']={'status':'awaiting_actual_acceptance','title':free['title'],'start':free['start'],'end':free['end'],'recipient':'zhengw.cc@outlook.com'};save()
 print('REAL INVITATION SUBMITTED; Outlook acceptance capture is still required:',json.dumps(proof['outlook'],ensure_ascii=False),flush=True)
 shot('close','message')
