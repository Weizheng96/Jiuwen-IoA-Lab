"""Loopback-only SMTP capture for explicit test evidence; never delivers external email."""
import argparse
from email import policy
from email.parser import BytesParser
from pathlib import Path
import socketserver
import threading
import uuid

class Capture:
    def __init__(self,folder,port=0):
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);self.messages=[]
        capture=self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b'220 IoA local SMTP capture\r\n')
                while True:
                    line=self.rfile.readline(65538)
                    if not line: return
                    op=line.split(b' ',1)[0].strip().upper()
                    if op in (b'EHLO',b'HELO'): self.wfile.write(b'250-localhost\r\n250 8BITMIME\r\n')
                    elif op==b'DATA':
                        self.wfile.write(b'354 End with dot\r\n');body=[]
                        while True:
                            chunk=self.rfile.readline(65538)
                            if chunk in (b'.\r\n',b'.\n',b''): break
                            body.append(chunk[1:] if chunk.startswith(b'..') else chunk)
                        raw=b''.join(body);path=folder/(uuid.uuid4().hex+'.eml');path.write_bytes(raw)
                        capture.messages.append({'path':str(path),'message':BytesParser(policy=policy.default).parsebytes(raw)})
                        self.wfile.write(b'250 Captured locally; not delivered externally\r\n')
                    elif op==b'QUIT': self.wfile.write(b'221 Bye\r\n');return
                    elif op in (b'MAIL',b'RCPT',b'RSET',b'NOOP'): self.wfile.write(b'250 OK\r\n')
                    else: self.wfile.write(b'502 Unsupported\r\n')
        self.server=socketserver.ThreadingTCPServer(('127.0.0.1',port),Handler);self.server.daemon_threads=True
        self.port=self.server.server_address[1];self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)

    def start(self): self.thread.start();return self
    def close(self): self.server.shutdown();self.server.server_close();self.thread.join(3)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--folder',default='data/smtp-capture');parser.add_argument('--port',type=int,default=1025);a=parser.parse_args()
    capture=Capture(a.folder,a.port).start();print(f'TEST SMTP CAPTURE 127.0.0.1:{capture.port}; no external delivery',flush=True)
    try: capture.thread.join()
    except KeyboardInterrupt: capture.close()
