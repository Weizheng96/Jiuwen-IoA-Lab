"""Persistent account operation results; never stores raw passwords or Bearer tokens."""
import hashlib
import hmac
import json
from decimal import Decimal
import time
import uuid
from datetime import datetime,timezone

from ..errors import LabError


class AccountSessions:
    def __init__(self,db,auth):
        self.db,self.auth=db,auth

    @staticmethod
    def operation(value):
        value=str(uuid.uuid4()) if value is None else value
        if not isinstance(value,str) or not 1<=len(value)<=128:
            raise LabError('invalid_input','A stable account operationId is required')
        return value

    def fingerprint(self,params):
        value=json.dumps(params,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        return hmac.new(self.auth.secret,b'account-params\0'+value.encode(),hashlib.sha256).hexdigest()

    def credential(self,value):
        return hmac.new(self.auth.secret,b'account-credential\0'+str(value).encode(),hashlib.sha256).hexdigest()

    def restore(self,username,kind,operation,fingerprint,conn=None):
        with self.db.lock:
            conn=conn or self.db.conn
            row=conn.execute('SELECT * FROM account_operations WHERE username=? AND kind=? AND id=?',
                             (username,kind,operation)).fetchone()
            if not row:return None
            if not hmac.compare_digest(row['params'],fingerprint):
                raise LabError('id_conflict','Account operation parameters changed',status=409)
            current=conn.execute('SELECT pw_hash FROM users WHERE id=?',(row['owner'],)).fetchone()
            if not current or not hmac.compare_digest(self.credential(current['pw_hash']),row['credential']):
                raise LabError('forbidden','Account credentials changed; start an explicit new login',status=401,recovery='authenticate')
            return self.render(json.loads(row['result']))

    def save(self,conn,username,kind,operation,fingerprint,user,credential,audience,*,credits=None):
        lifetime=30*86400 if audience=='sdk' else 7*86400
        credits=credits or {'initial':'0.00','login':'0.00'}
        result={**user,'operationId':operation,'credited':any(Decimal(credits.get(key,'0'))>0 for key in ('initial','login')),'credits':credits,'expires':int(time.time())+lifetime,'audience':audience}
        conn.execute('INSERT INTO account_operations VALUES(?,?,?,?,?,?,?,?)',
                     (username,kind,operation,fingerprint,user['id'],self.credential(credential),json.dumps(result),time.time()))
        return self.render(result)

    def render(self,result):
        subject=('assistant:' if result['audience']=='sdk' else '')+result['id']
        return {**{key:value for key,value in result.items() if key not in ('audience','expires')},
                'expiresAt':datetime.fromtimestamp(result['expires'],timezone.utc).isoformat().replace('+00:00','Z'),
                'token':self.auth.token(subject,expires_at=result['expires'])}
