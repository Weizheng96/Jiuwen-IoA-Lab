"""Connector shape and SDK command confirmation, rather than remote delegation APIs."""
import json
import subprocess
import sys
from pathlib import Path

def test_skill_package_shape():
    root=Path(__file__).resolve().parents[1];base=root/'connector/ioalab-client'
    manifest=json.loads((base/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['integration']['type']=='skill-only'
    schema=json.loads((base/'token-schema.json').read_text(encoding='utf-8'))
    assert [f['key'] for f in schema['fields']]==['IOALAB_BASE_URL','IOALAB_USERNAME','IOALAB_PASSWORD']
    assert next(f for f in schema['fields'] if f['key']=='IOALAB_PASSWORD')['type']=='password'

def test_cli_help():
    result=subprocess.run([sys.executable,'-m','ioalab_sdk.cli','--help'],capture_output=True,text=True)
    assert result.returncode==0 and '--confirmed' in result.stdout and 'verify' in result.stdout
