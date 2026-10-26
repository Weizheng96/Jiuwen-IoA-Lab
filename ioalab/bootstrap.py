"""进程内起一次 HTTP 服务来完成需要回环报文的工作（注入种子要向市场发 template.endorse）。

init 用：起 uvicorn 线程 → 等 Platform.started → 跑 fn → 关服务。平台已在跑时端口被占，init 应改用管理员接口。
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def port_free(host: str, port: int) -> bool:
    with socket.socket() as sk:
        return sk.connect_ex((host, port)) != 0


def run_with_server(pf, fn: Callable[[], T], timeout: float = 30.0) -> T:
    import uvicorn
    from .web.app import create_app
    s = pf.s
    if not port_free("127.0.0.1", s.port):
        raise SystemExit(f"[ioalab] 端口 {s.port} 已被占用：平台正在运行？请先停服，或用管理员接口完成本操作")
    app = create_app(pf)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=s.port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    deadline = time.time() + timeout
    while not pf.started and time.time() < deadline:
        time.sleep(0.2)
    if not pf.started:
        raise SystemExit("[ioalab] 进程内服务未能启动")
    try:
        return fn()
    finally:
        server.should_exit = True
        th.join(timeout=15)
