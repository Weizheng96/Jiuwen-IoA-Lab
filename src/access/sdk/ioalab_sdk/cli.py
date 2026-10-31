"""Stable JSON CLI used by the Jiuwen skill."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .client import LabClient


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0]=='mail':
        from .message_cli import main as mail_main
        return mail_main(argv[1:])
    parser = argparse.ArgumentParser(prog="ioalab-client")
    parser.add_argument("command", choices=["me", "delegate", "authorize", "scope", "quote-progress", "inspect", "reputation", "search", "templates", "compare", "draft", "sign", "request", "adopt", "start",
                                           "poll", "watch", "fetch", "verify", "accept", "pay", "answer-step", "terminate", "arbitrate"])
    parser.add_argument("--args", default="{}", help="JSON parameters")
    parser.add_argument('--args-file',type=Path,help='UTF-8 JSON parameters; avoids shell quoting problems')
    parser.add_argument("--confirmed", action="store_true", help="Pass only after actual user confirmation")
    args = parser.parse_args(argv)
    client = None
    try:
        base, username, password = (os.environ[k] for k in ("IOALAB_BASE_URL", "IOALAB_USERNAME", "IOALAB_PASSWORD"))
        key = hashlib.sha256((base.rstrip("/") + "|" + username).encode()).hexdigest()[:20]
        home = Path(os.environ.get("IOALAB_HOME", str(Path.home() / ".ioalab" / key)))
        client = LabClient(base, username, password, home)
        params = json.loads(args.args_file.read_text(encoding='utf-8-sig') if args.args_file else args.args)
        if args.command == "me":
            out = client.api("GET", "/me")
            out['delegationPolicy']=client.delegation_policy()
        else:
            method = getattr(client, args.command.replace("-", "_"))
            if args.command in ("delegate", "authorize", "sign", "request", "adopt", "start", "accept", "pay", "answer-step", "terminate", "arbitrate"):
                params["confirmed"] = args.confirmed
            if "materials" in params:
                params["materials"] = {n: (Path(v["path"]).read_bytes(), v.get("declared", {})) for n, v in params["materials"].items()}
            out = method(**params)
        print(json.dumps(out, ensure_ascii=False, default=str))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    main()
