"""A Web resource ID must fail before the provider registers or accepts a request."""
import sys
import pytest
from swarm import SwarmEngine


def test_model_preflight_blocks_service_registration(platform,tmp_path,monkeypatch):
    from serve import ServiceHost
    from fastapi import FastAPI
    cli=tmp_path/'cli.py';cli.write_text('print("--run-jsonl")')
    engine=SwarmEngine([sys.executable,str(cli)])
    engine.model_catalog=lambda:{'current_selection':'qwen-flash#0','models':[{'selection_key':'qwen-flash#0'},{'selection_key':'qwen-plus#1'}]}
    monkeypatch.setenv('IOALAB_SERVICE_MODEL','mdl_web_only')
    host=ServiceHost(platform=platform.s.base_url,public_url='http://127.0.0.1:59998',data=tmp_path/'provider',stub=False,only=['nail-booking-reliable'],engine=engine)
    try:
        with pytest.raises(RuntimeError,match='selection_key'):host.bootstrap(FastAPI())
        assert host.agents=={} and host.accounts=={} and not (tmp_path/'provider/svc-nail-booking-reliable/keys.json').exists()
    finally:host.close()
    monkeypatch.setenv('IOALAB_SERVICE_MODEL','qwen-plus#1');engine.check()
    monkeypatch.delenv('IOALAB_SERVICE_MODEL');engine.check()
