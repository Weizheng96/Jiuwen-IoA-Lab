"""Official Jiuwen extension API; no source edits or direct history-file writes."""
import asyncio
import hmac
import json
import os
import re
import sqlite3
from pathlib import Path
from jiuwenswarm.extensions.sdk import ApplicationPluginExtension
from jiuwenswarm.common.e2a.models import E2AEnvelope

class IoANotifications(ApplicationPluginExtension):
    plugin_id='ioa-notifications'

    async def initialize(self,config):
        self.secret=os.environ.get('IOALAB_SWARM_BRIDGE_SECRET','')
        self.user=os.environ.get('IOALAB_SWARM_USER_ID','ioa-admin')
        self.lock=asyncio.Lock()
        root=Path(os.environ['JIUWENSWARM_DATA_DIR'])/'plugins';root.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(root/'ioa-notifications.sqlite',isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,state TEXT,result TEXT)')

    async def shutdown(self): self.db.close()

    def bind_web_channel(self,channel,services):
        client=services.require_agent_client()
        async def notify(ws,req_id,params,session_id):
            if not self.secret or not hmac.compare_digest(str(params.get('bridge_token','')).encode(),self.secret.encode()):
                await channel.send_response(ws,req_id,ok=False,error='IoA bridge authorization required',code='UNAUTHORIZED');return
            mid=params.get('messageId','');sender=params.get('sender','');text=params.get('text','')
            if not isinstance(mid,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',mid) or not isinstance(text,str) or len(text.encode())>32768 or not isinstance(sender,str) or len(sender)>128:
                await channel.send_response(ws,req_id,ok=False,error='Invalid notification',code='BAD_REQUEST');return
            sid='sess_ioa_'+mid
            async with self.lock:
                old=self.db.execute('SELECT state,result FROM receipts WHERE id=?',(mid,)).fetchone()
                if old:
                    result=json.loads(old[1]) if old[1] else {'status':'unknown','sessionId':sid}
                    await channel.send_response(ws,req_id,ok=True,payload=result);return
                self.db.execute('INSERT INTO receipts VALUES(?,?,NULL)',(mid,'sending'))
                envelope=E2AEnvelope.from_dict({'protocol_version':'1.0','request_id':'ioa-notify-'+mid,'session_id':sid,'channel':'web','method':'history.append_record','identity_origin':'service','user_id':self.user,'params':{'session_id':sid,'request_id':'ioa-'+mid,'role':'assistant','event_type':'chat.final','mode':'agent','title':'IoA 留言 · '+sender,'content':'【IoA 外部留言 · 仅供阅读】\n来自：'+sender+'\n消息编号：'+mid+'\n\n'+text}})
                try:
                    response=await client.send_request(envelope)
                    if not response.ok or not response.payload.get('persisted'): raise RuntimeError('History not persisted')
                    result={'status':'delivered','sessionId':sid,'read':'unknown','transport':'jiuwen-web'}
                except Exception:
                    result={'status':'unknown','sessionId':sid,'reason':'Review Jiuwen history before retrying'}
                self.db.execute('UPDATE receipts SET state=?,result=? WHERE id=?',(result['status'],json.dumps(result,ensure_ascii=False),mid))
                await channel.send_response(ws,req_id,ok=True,payload=result)
        channel.register_method('ioa.notification.deliver',notify,local_only=True)

async def register_extensions(registry):
    plugin=IoANotifications();registry.register_application_plugin(plugin);return [plugin]
