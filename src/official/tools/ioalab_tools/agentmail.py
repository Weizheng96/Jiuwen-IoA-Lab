"""AgentMail's iMIP invitation API, isolated from the recipient's calendar."""
from datetime import timezone
import hashlib
from urllib.parse import quote
import httpx
from .calendar import address


class AgentMailNotification:
    test=False
    oauth=None

    def __init__(self,inbox,api_key,*,http=None):
        self.sender=address(inbox)
        if not api_key:raise ValueError('Configure the AgentMail API key locally')
        self.http=http or httpx.Client(base_url='https://api.agentmail.to/v0/',headers={'Authorization':'Bearer '+api_key},timeout=40,trust_env=False)
        self.path='inboxes/'+quote(inbox,safe='')+'/calendar'

    def check(self):
        calendar=self.http.get(self.path).raise_for_status().json()
        if str(calendar.get('inbox_id','')).casefold()!=self.sender.casefold():raise RuntimeError('AgentMail calendar does not match the notification inbox')
        return {'status':'authenticated','sender':self.sender,'auth':'api-key','transport':'agentmail-calendar','messageSent':False}

    def send(self,uid,title,start,end,recipient):
        recipient=address(recipient)
        if start.tzinfo is None or end.tzinfo is None or end<=start:raise ValueError('Invitation times require explicit time zones and a positive duration')
        def wall(value):return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        # Provider IDs reject '@'; preserve the logical UID in metadata and hash
        # it into a bounded, stable client_id instead of changing request identity.
        client_id=hashlib.sha256(uid.encode()).hexdigest()
        request={'title':title,'start':wall(start),'end':wall(end),'timezone':'UTC','attendees':[{'email':recipient,'role':'required'}],'send_invites':True,'client_id':client_id,'metadata':{'ioa_request_id':uid}}
        response=self.http.post(self.path+'/events',json=request)
        if not response.is_success:raise RuntimeError('AgentMail invitation request failed (HTTP '+str(response.status_code)+'); inspect before retrying')
        result=response.json();event=result.get('event',{})
        if not event.get('event_id') or not event.get('uid') or str(event.get('organizer_email','')).casefold()!=self.sender.casefold():raise RuntimeError('Unexpected AgentMail event response; inspect the request before retrying')
        # Only the organizer's event is stored here. Email dispatch is asynchronous.
        return {'status':'invite_submitted','calendar':'awaiting_acceptance','uid':event['uid'],'requestId':uid,'recipient':recipient,'transport':'agentmail-calendar','providerClientId':client_id,'providerEventId':event['event_id'],'providerOperationId':result.get('operation_id'),'delivery':'pending_provider_send'}
