"""One owned stdio child, bounded JSON-RPC messages, no replay or global daemon."""
import json
import io
import os
import queue
import subprocess
import threading
import time


class WireError(Exception):
    pass


class Stdio:
    def __init__(self, command, *, cwd):
        self.process=subprocess.Popen(command,cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,bufsize=0,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self._output=io.BufferedReader(self.process.stdout)
        self._messages=queue.Queue(maxsize=256)
        self._outgoing=queue.Queue(maxsize=32)
        self._closed=threading.Event()
        self._write=threading.Lock()
        self._writing=False
        self._sequence=0
        self._reader=threading.Thread(target=self._read,daemon=True)
        self._reader.start()
        self._writer=threading.Thread(target=self._send,daemon=True)
        self._writer.start()

    def _read(self):
        try:
            while not self._closed.is_set():
                line=self._output.readline(4*1024*1024+1)
                if not line:raise WireError('Codex stream ended before its result')
                if len(line)>4*1024*1024:raise WireError('Codex message exceeds its size bound')
                value=json.loads(line)
                if not isinstance(value,dict):raise WireError('Invalid Codex message')
                self._put(value)
        except Exception:
            self._put(WireError('Codex stream is unavailable or invalid'))

    def _put(self,value):
        while not self._closed.is_set():
            try:self._messages.put(value,timeout=.1);return
            except queue.Full:pass

    def send(self,value):
        raw=(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
        if len(raw)>4*1024*1024:raise WireError('Codex request exceeds its size bound')
        with self._write:
            if self._closed.is_set():raise WireError('Codex transport is closed')
            try:self._outgoing.put_nowait(raw)
            except queue.Full:raise WireError('Codex outgoing queue exceeds its bound') from None

    def _send(self):
        try:
            while not self._closed.is_set():
                try:raw=self._outgoing.get(timeout=.1)
                except queue.Empty:continue
                with self._write:
                    if self._closed.is_set():return
                    self._writing=True
                try:
                    remaining=memoryview(raw)
                    while remaining:
                        written=self.process.stdin.write(remaining)
                        if not written:raise OSError('Codex stdin is unavailable')
                        remaining=remaining[written:]
                finally:
                    with self._write:self._writing=False
        except Exception:self._put(WireError('Codex request could not be delivered; do not replay it'))

    def begin(self,method,params):
        with self._write:
            self._sequence+=1;key='ioa-'+str(self._sequence)
        self.send({'id':key,'method':method,'params':params})
        return key

    def receive(self,deadline):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise WireError('Codex deadline exceeded')
        try:value=self._messages.get(timeout=remaining)
        except queue.Empty:raise WireError('Codex deadline exceeded') from None
        if isinstance(value,Exception):raise value
        return value

    def request(self,method,params,deadline,handle=None):
        key=self.begin(method,params)
        while True:
            message=self.receive(deadline)
            if message.get('id')==key and 'method' not in message:
                if 'error' in message:raise WireError('Codex rejected '+method)
                return message['result']
            if handle:handle(message)
            elif 'method' in message and 'id' in message:
                self.send({'id':message['id'],'error':{'code':-32601,'message':'This client does not expose that capability'}})

    def initialize(self,deadline):
        result=self.request('initialize',{'clientInfo':{'name':'ioalab_codex','version':'0.1'},
            'capabilities':{'experimentalApi':True}},deadline)
        self.send({'method':'initialized'})
        return result

    def close(self):
        with self._write:
            self._closed.set()
            blocked=self._writing
        # A blocked pipe writer must not make close() wait for a Python stream
        # lock. Stop only our child first; its pipe then releases the writer.
        if blocked and self.process.poll() is None:self.process.terminate()
        if not blocked:
            try:self.process.stdin.close()
            except OSError:pass
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=3)
        self._reader.join(timeout=2)
        self._writer.join(timeout=2)
        self.process.stdin.close()
        self._output.close()

    def __enter__(self):return self

    def __exit__(self,*_):self.close()
