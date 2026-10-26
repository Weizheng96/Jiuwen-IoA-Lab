# -*- coding: utf-8 -*-
"""pytest 共用夹具：进程内起一次平台（IOALAB_STUB=1 走桩，不调模型），各用例共用；HTTP 客户端按用例新建。

    python -m pytest -q tests
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]          # 仓库根（含 ioalab 包）
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def platform():
    """临时目录 · 桩模式 · 随机空闲端口 · 管理员口令 test；照 ioalab/smoke.py 在线程里起 uvicorn，等 pf.started。"""
    port = _free_port()
    tmp = Path(tempfile.mkdtemp(prefix="ioalab-test-"))
    os.environ.update(IOALAB_STUB="1", IOALAB_PORT=str(port), IOALAB_ADMIN_PASSWORD="test", IOALAB_DATA=str(tmp),
                      IOALAB_MODE="dev", IOALAB_BIND="127.0.0.1")
    import uvicorn
    from ioalab.config import Settings
    from ioalab.platform import Platform
    from ioalab.web.app import create_app

    s = Settings()
    assert not s.llm and not s.a2x_configured(), "IOALAB_STUB=1 下 Settings 应不读凭据（config._stub）"   # 不碰任何模型接口（含嵌入与 A2X 检索）
    pf = Platform(s)
    assert not pf.llm.enabled and pf.discovery.embed_fn is None
    app = create_app(pf)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(150):
        if pf.started:
            break
        time.sleep(0.2)
    assert pf.started, "平台未启动"
    pf.app = app                                     # 供 test_boundary 查路由表
    yield pf
    server.should_exit = True
    th.join(timeout=15)


@pytest.fixture
def client(platform):
    """带 cookie 罐的 HTTP 客户端；trust_env=False 不走系统代理。"""
    import httpx
    with httpx.Client(base_url=platform.s.base_url, trust_env=False, timeout=60) as c:
        yield c
