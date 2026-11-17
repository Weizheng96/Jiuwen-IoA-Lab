"""Public MCP stdio boundary; no desktop, model, private pipe or business call."""
import json
from pathlib import Path
import sys
import time

import pytest

from ioalab_codex.native_app_tools import NativeAppTools, BOOTSTRAP
from ioalab_codex.transport import WireError, WireRejected


PEER = r'''
import json, sys
for line in sys.stdin:
 p=json.loads(line)
 assert p.get('jsonrpc')=='2.0'
 if 'id' not in p: continue
 method=p['method']; a=p['params']; error=None
 if method=='initialize':
  assert a['protocolVersion']=='2025-03-26' and a['capabilities']=={}
  value={'protocolVersion':'2025-03-26','serverInfo':{'name':'codex-app-tools','version':'test'}}
 elif method=='tools/list':
  value={'tools':[{'name':n} for n in ('create_thread','send_message_to_thread','read_thread')]}
 elif method=='tools/call':
  assert a['_meta']=={'x-codex-turn-metadata':{'thread_id':'real-caller'}}
  tool=a['name']; args=a['arguments']
  if tool=='create_thread':
   assert 'business-secret' not in args['prompt']
   value={'threadId':'actual-sid','hostId':'local','projectlessOutputDirectory':'actual-output'}
  elif tool=='send_message_to_thread':
   assert args['threadId']=='actual-sid'
   if args['prompt']=='rpc-error': error={'code':-32000,'message':'private endpoint must not leak'}
   if args['prompt']=='tool-error':
    result={'isError':True,'content':[{'type':'text','text':'side effect may already exist'}]}
   elif args['prompt']=='unrecognized':
    result={'content':[{'type':'text','text':'not a JSON receipt'}]}
   else: value={'threadId':args['threadId']}
  elif tool=='read_thread':
   assert args['includeOutputs'] is True
   assert args['turnLimit']<=10 and args['maxOutputCharsPerItem']<=20000
   older=args.get('cursor')=='older'
   value={'thread':{'id':args['threadId']},'turns':[{'id':'t-old' if older else 't-new'}],
          'page':{'hasMore':not older,'nextCursor':None if older else 'older'}}
  else: raise AssertionError(tool)
  if not (tool=='send_message_to_thread' and args['prompt'] in ('tool-error','unrecognized')):
   result={'content':[{'type':'text','text':json.dumps(value)}],'isError':False}
 else: raise AssertionError(method)
 if method!='tools/call': result=value
 output={'jsonrpc':'2.0','id':p['id']}
 if error: output['error']=error
 else: output['result']=result
 print(json.dumps(output),flush=True)
'''


def context(tmp_path):
    plugin = tmp_path/'official-plugin-fixture'
    plugin.mkdir()
    peer = plugin/'peer.py'
    peer.write_text(PEER, encoding='utf-8')
    (plugin/'.mcp.json').write_text(json.dumps({'mcpServers': {'codex_app': {
        'command': sys.executable, 'args': ['-u', str(peer)]}}}), encoding='utf-8')
    return {'format': 'ioalab.codex-desktop-context/1', 'pipePath': 'not-a-real-pipe',
            'nodePath': str(Path(sys.executable).resolve()), 'pluginDirectory': str(plugin.resolve()),
            'threadId': 'real-caller', 'hostId': None}


def test_mcp_stdio_bootstrap_send_and_exact_cursor_pages(tmp_path):
    with NativeAppTools(context(tmp_path)) as client:
        created = client.create('business-secret', deadline=time.monotonic()+5)
        assert created == {'threadId': 'actual-sid', 'hostId': 'local', 'projectlessOutputDirectory': 'actual-output'}
        assert client.send('actual-sid', 'authorized message', deadline=time.monotonic()+5) == {'threadId': 'actual-sid'}
        turns, complete = client.turns('actual-sid', deadline=time.monotonic()+5)
        assert complete and [turn['id'] for turn in turns] == ['t-new', 't-old']
        child = client._wire.process
    assert child.poll() is not None
    assert 'business-secret' not in BOOTSTRAP


@pytest.mark.parametrize('message', ['rpc-error', 'tool-error', 'unrecognized'])
def test_host_errors_and_unrecognized_ack_are_unknown_without_replay(tmp_path, message):
    with NativeAppTools(context(tmp_path)) as client:
        with pytest.raises(WireError) as caught:
            client.send('actual-sid', message, deadline=time.monotonic()+5)
        assert not isinstance(caught.value, WireRejected)
        assert 'private endpoint' not in str(caught.value)
        assert 'replay' in str(caught.value)


def test_turn_page_bound_retains_incomplete_fact(tmp_path):
    with NativeAppTools(context(tmp_path)) as client:
        turns, complete = client.turns('actual-sid', deadline=time.monotonic()+5, pages=1)
        assert not complete and turns == [{'id': 't-new'}]
