"""Private protocol files. Names are metadata; disk paths use verified content hashes."""
from pathlib import Path
import re
import time
import uuid

from a2c.codec import digest,digest_bytes
from .errors import LabError


MAX_FILE=16*1024*1024
ACCOUNT_QUOTA=512*1024*1024
SCHEMA='''
CREATE TABLE IF NOT EXISTS private_files(id TEXT PRIMARY KEY,actor TEXT NOT NULL,owner TEXT NOT NULL,
 name TEXT NOT NULL,digest TEXT NOT NULL,size INTEGER NOT NULL,ready INTEGER NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS private_file_operations(actor TEXT NOT NULL,operation TEXT NOT NULL,
 fingerprint TEXT NOT NULL,file TEXT NOT NULL,PRIMARY KEY(actor,operation));
CREATE TABLE IF NOT EXISTS private_file_readers(file TEXT NOT NULL,actor TEXT NOT NULL,PRIMARY KEY(file,actor));
'''


class PrivateFiles:
    def __init__(self,db,identities,root):
        self.db,self.identities,self.root=db,identities,Path(root)
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        with db.lock:db.conn.executescript(SCHEMA)

    def path(self,sha):
        if not isinstance(sha,str) or not re.fullmatch('sha256:[0-9a-f]{64}',sha):raise LabError('invalid_input','A SHA-256 file digest is required')
        return self.root/(sha[7:]+'.bin')

    @staticmethod
    def descriptor(row):return {key:row[key] for key in ('name','digest','size')}|{'fileId':row['id']}

    def put(self,owner,actor,operation,name,expected,data):
        self.identities.owned(owner,actor)
        if not isinstance(operation,str) or not 1<=len(operation)<=128:raise LabError('invalid_input','Use a stable upload operationId')
        if not isinstance(name,str) or not 1<=len(name)<=240 or any(ord(c)<32 for c in name) or name.startswith('/') or any(c in name for c in ('\\',':')) or any(part in ('','.','..') for part in name.split('/')):
            raise LabError('invalid_input','Use a portable relative protocol file name')
        if not isinstance(data,bytes) or len(data)>MAX_FILE:raise LabError('invalid_input','A protocol file may contain at most16MiB')
        actual=digest_bytes(data)
        if actual!=expected:raise LabError('invalid_input','Uploaded bytes do not match the declared digest')
        path=self.path(actual);fingerprint=digest({'name':name,'digest':actual,'size':len(data)})
        with self.db.tx() as conn:
            self.identities.owned(owner,actor)
            old=conn.execute('SELECT * FROM private_file_operations WHERE actor=? AND operation=?',(actor,operation)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise LabError('id_conflict','Original file upload changed',status=409)
                row=conn.execute('SELECT * FROM private_files WHERE id=?',(old['file'],)).fetchone()
                identifier=row['id']
            else:
                used=conn.execute('SELECT COALESCE(SUM(size),0) FROM private_files WHERE owner=?',(owner,)).fetchone()[0]
                if used+len(data)>ACCOUNT_QUOTA:raise LabError('quota_exceeded','Account protocol file quota reached',status=429)
                identifier=str(uuid.uuid5(uuid.NAMESPACE_URL,'ioa-file:'+actor+':'+operation))
                conn.execute('INSERT INTO private_files VALUES(?,?,?,?,?,?,?,?)',(identifier,actor,owner,name,actual,len(data),0,time.time()))
                conn.execute('INSERT INTO private_file_operations VALUES(?,?,?,?)',(actor,operation,fingerprint,identifier))
        # Reserve quota before disk I/O. A pending reference grants no reads;
        # interruption resumes this exact upload rather than allocating another.
        try:
            if path.exists():
                if digest_bytes(path.read_bytes())!=actual:raise LabError('not_ready','Stored file is damaged; restore its original bytes',status=409)
            else:
                temporary=self.root/(uuid.uuid4().hex+'.upload')
                try:
                    temporary.write_bytes(data)
                    temporary.replace(path)
                finally:temporary.unlink(missing_ok=True)
        except OSError:
            raise LabError('not_ready','Storage write interrupted; resume the original upload after recovery',status=503,recovery='resume_original') from None
        with self.db.tx() as conn:
            self.identities.owned(owner,actor)
            conn.execute('UPDATE private_files SET ready=1 WHERE id=?',(identifier,))
            row=conn.execute('SELECT * FROM private_files WHERE id=?',(identifier,)).fetchone()
            return self.descriptor(row)

    def checked_in(self,conn,sender,reference):
        if not isinstance(reference,dict) or set(reference)!={'fileId','name','digest','size'} or not all(isinstance(reference[k],str) for k in ('fileId','name','digest')) or type(reference['size']) is not int or reference['size']<0:raise LabError('invalid_input','Use a complete private file reference')
        row=conn.execute('SELECT * FROM private_files WHERE id=?',(reference['fileId'],)).fetchone()
        if not row or row['actor']!=sender or self.descriptor(row)!=reference:raise LabError('forbidden','Only the original uploader may attach these exact file bytes',status=403)
        if not row['ready']:raise LabError('not_ready','Complete the original file upload first',status=409)
        return row

    def grant_in(self,conn,sender,recipient,references):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('File grants require the accepted-message transaction')
        if not isinstance(references,dict) or len(references)>32:raise LabError('invalid_input','Attach at most32 protocol files')
        rows=[]
        for name,reference in references.items():
            row=self.checked_in(conn,sender,reference)
            if row['name']!=name:raise LabError('invalid_input','Attachment names must preserve the original protocol file map')
            rows.append(row)
        for row in rows:conn.execute('INSERT OR IGNORE INTO private_file_readers VALUES(?,?)',(row['id'],recipient))

    def get(self,owner,actor,identifier):
        self.identities.owned(owner,actor)
        row=self.db.one('SELECT f.* FROM private_files f WHERE f.id=? AND f.ready=1 AND (f.actor=? OR EXISTS(SELECT 1 FROM private_file_readers r WHERE r.file=f.id AND r.actor=?))',identifier,actor,actor)
        if not row:raise LabError('forbidden','File is unavailable',status=404)
        try:data=self.path(row['digest']).read_bytes()
        except OSError:raise LabError('not_ready','Restore the original file bytes',status=409) from None
        if len(data)!=row['size'] or digest_bytes(data)!=row['digest']:raise LabError('not_ready','Stored file integrity check failed',status=409)
        return self.descriptor(row),data
