"""Bounded byte transport to this epoch's two fixed seat ports.

This is not an HTTP proxy: CONNECT/Host/URLs are bytes sent to the fixed peer,
never destination selectors. No headers, bodies or credentials are logged.
"""
import select
import ipaddress
from pathlib import Path
import signal
import socket
import sys
import threading
import time

TARGET_HOST='seat'
TARGET_PORTS=(8080,8767)
MAX_CONNECTIONS=32
CONNECT_TIMEOUT=5
WRITE_TIMEOUT=5
IDLE_TIMEOUT=90


def seat_connector(hosts=Path('/etc/hosts')):
    # Docker pins the inspected epoch seat address in this unprivileged
    # container's hosts file. Numeric AF_INET connect never calls getaddrinfo;
    # an unavailable DNS service cannot evade the five-second connect bound.
    content=hosts.read_text('utf-8')
    if len(content)>8192:raise ValueError('Unexpected fixed ingress hosts data')
    addresses=[]
    for line in content.splitlines():
        fields=line.split('#',1)[0].split()
        if len(fields)>1 and TARGET_HOST in fields[1:]:
            address=ipaddress.ip_address(fields[0])
            if address.version!=4 or not address.is_private or address.is_loopback or address.is_unspecified:
                raise ValueError('Invalid fixed epoch seat address')
            addresses.append(str(address))
    if len(addresses)!=1:raise ValueError('A single Docker-pinned epoch seat address is required')
    def connect(target,timeout):
        if target[0]!=TARGET_HOST or target[1] not in TARGET_PORTS:raise ValueError('Fixed epoch target required')
        peer=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        try:
            peer.settimeout(timeout);peer.connect((addresses[0],target[1]));return peer
        except BaseException:
            peer.close();raise
    return connect


class Ingress:
    def __init__(self, *, host='0.0.0.0', ports=TARGET_PORTS, dial=socket.create_connection):
        # Tests can bind ephemeral listeners and map the fixed peer to owned
        # dummy servers. The executable exposes none of these as configuration.
        self.dial=dial
        self.stopping=threading.Event()
        self.lock=threading.RLock()
        self.sockets=set()
        self.workers=set()
        self.slots=threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.listeners={}
        try:
            for port,target in zip(ports,TARGET_PORTS,strict=True):
                listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
                listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                listener.bind((host,port));listener.listen(MAX_CONNECTIONS)
                self.listeners[listener]=target
        except BaseException:
            self.stop()
            raise

    def stop(self):
        self.stopping.set()
        with self.lock:
            for connection in (*self.listeners,*self.sockets):
                try:connection.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                connection.close()

    def forward(self, client, target):
        peer=None
        try:
            peer=self.dial((TARGET_HOST,target),timeout=CONNECT_TIMEOUT)
            with self.lock:
                if self.stopping.is_set():return
                self.sockets.add(peer)
            client.settimeout(WRITE_TIMEOUT);peer.settimeout(WRITE_TIMEOUT)
            active=time.monotonic()
            while not self.stopping.is_set():
                ready,_,_=select.select([client,peer],[],[],min(.25,IDLE_TIMEOUT))
                if time.monotonic()-active>=IDLE_TIMEOUT:return
                for source in ready:
                    data=source.recv(65536)
                    if not data:return
                    (peer if source is client else client).sendall(data)
                    active=time.monotonic()
        except (OSError,ValueError):
            pass
        finally:
            with self.lock:
                for connection in (client,peer):
                    if connection is not None:
                        self.sockets.discard(connection)
                        connection.close()
                self.workers.discard(threading.current_thread())
            self.slots.release()

    def serve(self):
        try:
            while not self.stopping.is_set():
                try:ready,_,_=select.select(list(self.listeners),[],[],.25)
                except (OSError,ValueError):
                    if self.stopping.is_set():break
                    raise
                for listener in ready:
                    try:client,_=listener.accept()
                    except OSError:
                        if self.stopping.is_set():break
                        raise
                    if not self.slots.acquire(blocking=False):
                        client.close();continue
                    worker=threading.Thread(target=self.forward,args=(client,self.listeners[listener]))
                    with self.lock:
                        if self.stopping.is_set():
                            client.close();self.slots.release();break
                        self.sockets.add(client);self.workers.add(worker)
                        worker.start()
        finally:
            self.stop()
            with self.lock:workers=tuple(self.workers)
            for worker in workers:worker.join(timeout=CONNECT_TIMEOUT+WRITE_TIMEOUT+1)
            if any(worker.is_alive() for worker in workers):
                raise RuntimeError('Ingress connections have not released')


def main():
    if len(sys.argv)!=1:raise SystemExit('Ingress has no destination configuration')
    relay=Ingress(dial=seat_connector())
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:relay.stop())
    relay.serve()


if __name__=='__main__':main()
