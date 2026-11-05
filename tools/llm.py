"""Private development model profiles; explicit initialization never overwrites a runtime input."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

SOURCE = Path(__file__).resolve().parents[1] / '.agents/llm_apikey'


class ModelInputError(RuntimeError):
    """A deliberately redacted input or provider failure."""


def profile(name: str, source: Path = SOURCE) -> dict:
    """Read only the requested profile; do not expose secrets in exceptions."""
    try:
        from deploy.workspace import path_without_links
        resolved = path_without_links(source)
        if not resolved.is_file():
            raise ValueError()
        value = json.loads(resolved.read_text(encoding='utf-8'))['profiles'][name]
        if not isinstance(value, dict) or not all(isinstance(value.get(k), str) and value[k].strip()
                                                  for k in ('name', 'base_url', 'api_key')):
            raise ValueError()
        if not value['base_url'].startswith('https://'):
            raise ValueError()
        from deploy.configuration import model
        return model(value)
    except (OSError, ValueError, KeyError, TypeError):
        raise ModelInputError('Private model profile unavailable or invalid') from None


def initialize(name, layout, *, source=SOURCE, initializer=None):
    """Inject only into the existing explicit deployment initialization flow."""
    settings=profile(name,source)
    if initializer is None:
        from deploy.configure import initialize as initializer
    try:
        return initializer(layout,llm=settings)
    except Exception:
        raise ModelInputError('Platform initialization failed; existing input preserved') from None


def complete(name: str, messages: list, *, source: Path = SOURCE, opener=urlopen) -> dict:
    """One explicitly requested OpenAI-compatible call; no global environment mutation."""
    settings=profile(name,source)
    body=json.dumps({'model':settings['name'],'messages':messages}).encode('utf-8')
    request=Request(settings['base_url'].rstrip('/')+'/chat/completions',data=body,
                    headers={'Authorization':'Bearer '+settings['api_key'],'Content-Type':'application/json'})
    try:
        with opener(request,timeout=120) as response:
            result=json.load(response)
        if not isinstance(result.get('choices'),list): raise ValueError()
        return result
    except Exception:
        raise ModelInputError('Model call failed; inspect private provider diagnostics separately') from None


class Aliyun:
    """Narration adapter using the official SDK and the same private development source."""
    def __init__(self,*,source=SOURCE,client_factory=None):
        self.source,self.client_factory=source,client_factory

    def tts_realtime(self,text,*,voice,instructions,speech_rate):
        import base64
        import threading
        try:
            from deploy.workspace import path_without_links
            document=json.loads(path_without_links(self.source).read_text(encoding='utf-8'))
            settings=document['tts'];credentials=profile(settings['profile'],self.source)
            address=urlsplit(settings['url'])
            if (address.scheme!='wss' or address.hostname!=urlsplit(credentials['base_url']).hostname
                    or address.username or address.password or address.query or address.fragment):raise ValueError()
            from dashscope.audio.qwen_tts_realtime import QwenTtsRealtime,QwenTtsRealtimeCallback,AudioFormat
            ready=threading.Event();audio=[];failed=[]
            class Callback(QwenTtsRealtimeCallback):
                def on_event(self,event):
                    try:
                        if event['type']=='response.audio.delta':audio.append(base64.b64decode(event['delta'],validate=True))
                        elif event['type']=='session.finished':ready.set()
                        elif event['type']=='error':failed.append(True);ready.set()
                    except Exception:failed.append(True);ready.set()
                def on_close(self,*_):
                    if not ready.is_set():failed.append(True);ready.set()
            factory=self.client_factory or QwenTtsRealtime
            client=factory(model=settings['model'],url=settings['url'],callback=Callback())
            client.apikey=credentials['api_key']  # SDK instance state; never mutate global key/environment.
            try:
                client.connect()
                client.update_session(voice=voice,response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
                    mode='server_commit',instructions=instructions,speech_rate=speech_rate)
                client.append_text(text);client.finish()
                if not ready.wait(120) or failed or not b''.join(audio):raise ValueError()
                return b''.join(audio)
            finally:client.close()
        except Exception:
            raise ModelInputError('Narration model call failed; private provider details withheld') from None


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=SOURCE,help='Private source path, never credential content')
    parser.add_argument('--profile',required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--config-dir',type=Path,required=True)
    args=parser.parse_args()
    try:
        from deploy.layout import Layout
        initialize(args.profile,Layout(args.root,args.state,args.config_dir),source=args.source)
    except ModelInputError as error:
        print(str(error));return 1
    print('Private platform input initialized through deployment');return 0


if __name__=='__main__':
    raise SystemExit(main())
