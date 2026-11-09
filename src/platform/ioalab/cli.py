"""命令行：ioalab check-config · init · serve · reset · backup。

    ioalab --config <私有JSON> check-config  # 验证唯一文件，打印脱敏概要
    ioalab init [--no-seed]        # 幂等：建目录 · 建表 · 会话密钥 · A2X 凭据文件 · 记下 PUBLIC_URL · 创建空闲内部账号与分类
    ioalab serve                   # 同一配置路径；单进程单worker，禁--reload
    ioalab reset [--keep-keys]   # 停服后清 data/
    python tools/smoke.py                   # 核心、官方服务、本地SDK三进程闭环（显式桩，不调模型）
    ioalab backup [--out 目录]     # 停服后把 data/ 打成 tar.gz
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tarfile
import time
from pathlib import Path



def _logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "a2a", "openjiuwen", "sentence_transformers", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # a2a-sdk 1.0.0 每次请求后都告警「Dispatcher task is not running」，不影响结果，压到 ERROR
    logging.getLogger("a2a.server.events").setLevel(logging.ERROR)


def _file_log(settings) -> None:
    from logging.handlers import RotatingFileHandler
    p = settings.data / "logs" / "ioalab.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(p, maxBytes=20_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(h)


def _print_config(s) -> None:
    key = s.llm.get("api_key", "")
    print(f"[ioalab] 模式 {s.mode} · 绑定 {s.bind}:{s.port} · 对外 {s.public_url} · 数据 {s.data}")
    print(f"[ioalab] 模型 {s.agent_model} / A2X {s.a2x_model} / 嵌入 {s.embedding_model} · 端点 {s.llm.get('api_base') or '（未配置）'}"
          f" · key {'已配置' if key else '未配置'}")
    print(f"[ioalab] 注册 {s.registration} · 登录赠 {s.login_credit} · ISC {s.isc_count} · 流量 {s.traffic} · 自动注入 {s.autoseed}")


def _problems(s) -> list[str]:
    from .seed import load_domains
    return s.check(seed_manifest=load_domains)


def _require_ok(s) -> None:
    problems = _problems(s)
    if problems:
        for x in problems:
            print("[ioalab] ✗ " + x)
        sys.exit(2)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="ioalab")
    ap.add_argument('--config', help='私有平台 JSON 配置文件（或 IOALAB_CONFIG）')
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-config")
    ini = sub.add_parser("init")
    ini.add_argument("--no-seed", action="store_true")
    sv = sub.add_parser("serve")
    rs = sub.add_parser("reset")
    rs.add_argument("--keep-keys", action="store_true")
    bk = sub.add_parser("backup")
    bk.add_argument("--out")
    args = ap.parse_args(argv)
    _logging()
    from .configuration import PlatformConfiguration
    configuration = PlatformConfiguration.load(args.config)
    s = configuration.settings()
    if args.cmd == 'init' and args.no_seed:
        s.autoseed = False

    if args.cmd == "check-config":
        _print_config(s)
        problems = _problems(s)
        for x in problems:
            print("[ioalab] ✗ " + x)
        print("[ioalab] " + ("✓ 配置可用" if not problems else f"✗ {len(problems)} 项需要修正"))
        sys.exit(1 if problems else 0)

    if args.cmd == "init":
        _require_ok(s)
        from .app import Platform
        pf = Platform(s, configuration=configuration)  # 建表 · 校验并记下 PUBLIC_URL · 管理员口令
        _print_config(s)
        if args.no_seed:
            try:
                print("[ioalab] init 完成（未注入种子）")
            finally:
                pf.close()
            return
        from .bootstrap import run_with_server
        summary = run_with_server(pf, lambda: pf.ensure_seeded())
        print(f"[ioalab] init 完成：{summary}")
        return

    if args.cmd == "serve":
        _require_ok(s)
        import uvicorn
        from .app import Platform
        from .web.app import create_app
        pf = Platform(s, configuration=configuration)
        try:
            _file_log(s)
            app = create_app(pf)
            _print_config(s)
            print(f"[ioalab] 用户端 {s.public_url}/  管理员 {s.public_url}/admin")
            uvicorn.run(app, host=s.bind, port=s.port, workers=1, log_level="warning", proxy_headers=False)
        finally:
            pf.close()
        return

    if args.cmd == "reset":
        if not s.data.exists():
            print(f"[ioalab] {s.data} 不存在")
            return
        from .storage import DataOwner
        with DataOwner(s.data) as owner:
            keep = {s.data / "keys"} if args.keep_keys else set()
            for p in s.data.iterdir():
                if p in keep or p.resolve() == owner.path:
                    continue
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        print(f"[ioalab] 已清空 {s.data}" + ("（保留 keys/）" if keep else ""))
        return

    if args.cmd == "backup":
        out = Path(args.out) if args.out else s.data.parent / "backups"
        from .storage import DataOwner
        with DataOwner(s.data):
            out.mkdir(parents=True, exist_ok=True)
            dst = out / f"ioalab-data-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
            with tarfile.open(dst, "w:gz") as tf:
                tf.add(s.data, arcname="data", filter=lambda item: None if item.name == 'data/.core.lock' else item)
        print(f"[ioalab] 已备份到 {dst}")
        return


if __name__ == "__main__":
    main()
