"""Approved narration planning and cache/failure boundaries; no live TTS calls."""
import json
import copy
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from demo import approved_voice, build_voice
from demo.review_story import DRAFTS
import hashlib


def test_saved_asset_bytes_are_utf8_lf_for_published_digest(tmp_path):
    target=tmp_path/'asset.json'
    approved_voice.save(target,{'voice':'已批准旁白','recorded':False})
    assert target.read_bytes()==b'{\n  "voice": "'+ '已批准旁白'.encode('utf-8')+b'",\n  "recorded": false\n}\n'


@pytest.mark.parametrize('field,value',[
    ('model','another-model'),('voice','another-voice'),('style','another-style'),
    ('speechRate',1.7),('tempo',1.13),('text','另一句已批准文本'),('format','wav'),
])
def test_approved_cache_binds_every_synthesis_and_encoding_input(field,value):
    settings={'model':'qwen3-tts-flash-realtime','voice':'Elias','style':build_voice.NARRATION_STYLE,
              'speechRate':1.6,'tempo':1.12,'format':'mp3'}
    text='完整批准旁白'
    original=approved_voice.cache_name(text,settings)
    changed=copy.deepcopy(settings)
    if field=='text':text=value
    else:changed[field]=value
    assert approved_voice.cache_name(text,changed)!=original
    assert approved_voice.cache_name('完整批准旁白',dict(reversed(list(settings.items()))))==original
    assert len(Path(original).stem)==64


def test_voice_plan_contains_only_exact_approved_narration_without_movie_data(tmp_path, monkeypatch):
    monkeypatch.setattr(approved_voice, 'DEMO', tmp_path)
    for case, scene in approved_voice.approved_content().items():
        path = approved_voice.prepare(case)
        approved_voice.prepare(case, check=True)
        plan = json.loads(path.read_text(encoding='utf-8'))
        assert plan['recorded'] is False
        assert [clip['voice'] for clip in plan['clips']] == [shot['voice'] for shot in scene['shots']]
        assert {clip['id']:clip['voice'] for clip in plan['clips']} == {shot['id']:shot['voice'] for shot in DRAFTS[case]['shots']}
        assert not any('[' in clip['voice'] for clip in plan['clips'])
    plan['clips'][0]['voice'] = 'unapproved model reply'
    with pytest.raises(ValueError, match='differs from approved'):
        approved_voice.validate_plan(plan)


def test_synthesis_reuses_verified_cache_without_reloading_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(approved_voice, 'DEMO', tmp_path)
    path = approved_voice.prepare('messaging')
    helper = tmp_path / 'operator.py'; helper.write_text('# public helper source')
    calls = []
    def render(client, spoken, destination, speech_rate, tempo):
        calls.append(spoken); destination.write_bytes(spoken.encode('utf-8')); return 2.5
    monkeypatch.setattr(build_voice, 'render_clip', render)
    first = approved_voice.synthesize(path, helper, client_factory=lambda _: object())
    assert first['status'] == 'ready' and len(calls) == len(DRAFTS['messaging']['shots'])
    assert calls == [shot['voice'] for shot in DRAFTS['messaging']['shots']]
    def forbidden(_):
        raise AssertionError('Cache reuse must not reload credentials or call TTS')
    second = approved_voice.synthesize(path, helper, client_factory=forbidden)
    assert second['status'] == 'ready' and all(clip['reused'] for clip in second['clips'])
    assert len(calls) == len(DRAFTS['messaging']['shots'])


def test_failed_synthesis_records_failure_without_private_error_or_fake_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(approved_voice, 'DEMO', tmp_path)
    path = approved_voice.prepare('messaging')
    helper = tmp_path / 'operator.py'; helper.write_text('# public helper source')
    def unavailable(*_):
        raise RuntimeError('private-provider-detail-not-for-assets')
    monkeypatch.setattr(build_voice, 'render_clip', unavailable)
    with pytest.raises(RuntimeError, match='failed: RuntimeError') as error:
        approved_voice.synthesize(path, helper, client_factory=lambda _: object())
    report = (path.parent / 'narration.json').read_text(encoding='utf-8')
    assert json.loads(report)['status'] == 'failed'
    assert json.loads(report)['clips'] == []
    assert 'private-provider-detail' not in report + str(error.value)
    assert not list(path.parent.glob('*.mp3'))


def test_approved_synthesis_does_not_borrow_legacy_cache_by_filename(tmp_path,monkeypatch):
    monkeypatch.setattr(approved_voice,'DEMO',tmp_path)
    plan=approved_voice.prepare('messaging')
    helper=tmp_path/'operator.py';helper.write_text('# public helper source')
    legacy=tmp_path/'assets/voice/messaging';legacy.mkdir(parents=True)
    for clip in approved_voice.voice_plan('messaging')['clips']:
        (legacy/build_voice.cache_name(clip['voice'],1.6,1.12)).write_bytes(b'cached with another model/voice/style')
    original={path:path.read_bytes() for path in legacy.glob('*.mp3')}
    calls=[]
    def unavailable(*args):
        calls.append(args);raise RuntimeError('provider unavailable')
    monkeypatch.setattr(build_voice,'render_clip',unavailable)
    monkeypatch.setattr(build_voice,'audio_duration',lambda _:2.5)
    with pytest.raises(RuntimeError,match='synthesis failed'):
        approved_voice.synthesize(plan,helper,client_factory=lambda _:object())
    assert len(calls)==1 and not list(plan.parent.glob('*.mp3'))
    assert all(path.read_bytes()==data for path,data in original.items())


@pytest.mark.parametrize('field',['model','voice','style'])
def test_full_cache_name_still_requires_matching_settings_and_audio_metadata(tmp_path,monkeypatch,field):
    monkeypatch.setattr(approved_voice,'DEMO',tmp_path)
    plan=approved_voice.prepare('messaging')
    helper=tmp_path/'operator.py';helper.write_text('# public helper source')
    def render(client,spoken,destination,*_):
        destination.write_bytes(spoken.encode('utf-8'));return 2.5
    monkeypatch.setattr(build_voice,'render_clip',render)
    first=approved_voice.synthesize(plan,helper,client_factory=lambda _:object())
    original={path:path.read_bytes() for path in plan.parent.glob('*.mp3')}
    first['settings'][field]='a different synthesis configuration'
    approved_voice.save(plan.parent/'narration.json',first)
    def unavailable(_):raise RuntimeError('No new provider call is allowed by this test')
    with pytest.raises(RuntimeError,match='synthesis failed'):
        approved_voice.synthesize(plan,helper,client_factory=unavailable)
    assert all(path.read_bytes()==data for path,data in original.items())


def test_legacy_lab_cache_keys_remain_reusable():
    folder = Path(__file__).resolve().parents[1] / 'demo/assets/voice/delegation'
    entry = json.loads((folder / 'voice.json').read_text(encoding='utf-8'))[0]
    assert build_voice.cache_name(entry['voice'], entry['speechRate'], entry['tempo']) == entry['file']


@pytest.mark.parametrize('defect',[None,'missing','audio','settings','duplicate','text','approval','index',
                                  'duration','paced-duration','aggregate','probe-timeout'])
def test_cached_reindex_preserves_audio_and_refuses_inconsistent_inputs_before_writing(tmp_path,monkeypatch,defect):
    monkeypatch.setattr(approved_voice,'DEMO',tmp_path)
    plan=approved_voice.voice_plan('delegation-provider')
    old=copy.deepcopy(plan);old.pop('recordingAdjustment')
    by_id={clip['id']:clip for clip in old['clips']}
    old['clips']=[by_id[shot['id']] for shot in DRAFTS['delegation-provider']['shots']]
    at=0
    for clip in old['clips']:
        clip['at']=at;at+=clip['plannedSeconds']
    folder=tmp_path/'delegation-provider/assets/voice';folder.mkdir(parents=True)
    settings={'model':'qwen3-tts-flash-realtime','voice':'Elias','style':build_voice.NARRATION_STYLE,
              'speechRate':1.6,'tempo':1.12,'format':'mp3'}
    clips=[];measured={}
    for spec in old['clips']:
        data=spec['voice'].encode('utf-8');name=approved_voice.cache_name(spec['voice'],settings)
        (folder/name).write_bytes(data)
        clips.append({**spec,'file':name,'duration':len(spec['voice'])/6,'audioSha256':hashlib.sha256(data).hexdigest()})
        measured[name]=len(spec['voice'])/6
    raw={'scene':plan['scene'],'approvalSha256':plan['approvalSha256'],'status':'ready','settings':settings,'clips':clips}
    fitted=[{**clip,'rawFile':clip['file'],'rawAudioSha256':clip['audioSha256'],'paceFactor':1.0} for clip in clips]
    paced={'scene':plan['scene'],'approvalSha256':plan['approvalSha256'],'status':'paced','clips':fitted}
    raw['audioTotalSeconds']=paced['audioTotalSeconds']=round(sum(c['duration'] for c in clips),3)
    preview=folder/'approved-narration-preview.mp3';preview.write_bytes(b'old-order-preview')
    if defect=='missing':(folder/clips[0]['file']).unlink()
    elif defect=='audio':(folder/clips[0]['file']).write_bytes(b'changed audio')
    elif defect=='settings':settings['model']='other-model'
    elif defect=='duplicate':old['clips'][1]['id']=old['clips'][0]['id']
    elif defect=='text':clips[0]['voice']='different approved text'
    elif defect=='approval':raw['approvalSha256']='another-approval'
    elif defect=='duration':
        clips[0]['duration']=9999
        raw['audioTotalSeconds']=round(sum(c['duration'] for c in clips),3)
    elif defect=='paced-duration':
        fitted[0]['duration']=9999
        paced['audioTotalSeconds']=round(sum(c['duration'] for c in fitted),3)
    elif defect=='aggregate':raw['audioTotalSeconds']=9999
    for name,value in (('voiceplan.json',old),('narration.json',raw),('voice.json',clips),
                       ('pace-review.json',paced),('voice-paced.json',fitted[:-1] if defect=='index' else fitted)):
        approved_voice.save(folder/name,value)
    before={p:p.read_bytes() for p in folder.iterdir()}
    def forbidden(*_a,**_k):raise AssertionError('Reindex must not load credentials, call TTS or encode audio')
    monkeypatch.setattr(approved_voice,'operator_client',forbidden)
    monkeypatch.setattr(build_voice,'render_clip',forbidden)
    monkeypatch.setattr(approved_voice.subprocess,'run',forbidden)
    def probe(path,**kwargs):
        assert kwargs=={'timeout':5}
        if defect=='probe-timeout':raise subprocess.TimeoutExpired('bounded-probe',5)
        return measured[path.name]
    monkeypatch.setattr(build_voice,'audio_duration',probe)
    if defect:
        with pytest.raises(ValueError):approved_voice.reindex_cached(folder/'voiceplan.json')
        assert {p:p.read_bytes() for p in folder.iterdir()}==before
    else:
        report=approved_voice.reindex_cached(folder/'voiceplan.json')
        assert report['providerCalls']==0 and report['previewNeedsRebuild']
        assert json.loads((folder/'voiceplan.json').read_text(encoding='utf-8'))==plan
        for name in ('voice.json','voice-paced.json'):
            actual=json.loads((folder/name).read_text(encoding='utf-8'))
            assert [c['id'] for c in actual]==[c['id'] for c in plan['clips']]
            assert [c['at'] for c in actual]==[c['at'] for c in plan['clips']]
        assert all(p.read_bytes()==data for p,data in before.items() if p.suffix=='.mp3')
        once={p:p.read_bytes() for p in folder.iterdir()}
        approved_voice.reindex_cached(folder/'voiceplan.json')
        assert {p:p.read_bytes() for p in folder.iterdir()}==once


@pytest.mark.parametrize('bounded',[False,True])
def test_audio_duration_forwards_optional_probe_timeout(monkeypatch,bounded):
    monkeypatch.setitem(sys.modules,'imageio_ffmpeg',SimpleNamespace(get_ffmpeg_exe=lambda:'ffmpeg'))
    def probe(argv,**kwargs):
        assert argv==['ffmpeg','-hide_banner','-i','clip.mp3']
        assert (kwargs.get('timeout')==5) if bounded else ('timeout' not in kwargs)
        return SimpleNamespace(stderr='Duration: 00:00:02.50')
    monkeypatch.setattr(build_voice.subprocess,'run',probe)
    assert build_voice.audio_duration('clip.mp3',**({'timeout':5} if bounded else {}))==2.5


def test_local_pace_fit_preserves_original_and_never_calls_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(approved_voice, 'DEMO', tmp_path)
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', SimpleNamespace(get_ffmpeg_exe=lambda: 'ffmpeg'))
    plan = approved_voice.prepare('messaging')
    helper = tmp_path / 'operator.py'; helper.write_text('# public helper')
    durations = {}
    def raw_render(client, spoken, path, *_):
        path.write_bytes(spoken.encode()); durations[str(path)] = len(spoken) / 8
        return durations[str(path)]
    monkeypatch.setattr(build_voice, 'render_clip', raw_render)
    approved_voice.synthesize(plan, helper, client_factory=lambda _: object())
    originals = {path: path.read_bytes() for path in plan.parent.glob('*.mp3')}
    def local_encoder(argv, **kwargs):
        source = argv[argv.index('-i') + 1]
        factor = float(argv[argv.index('-af') + 1].split('=')[1])
        output = Path(argv[-1]); output.write_bytes(b'locally-paced')
        durations[str(output)] = durations[source] / factor
        durations[str(output.with_name(output.name.replace('.pending.mp3', '.mp3')))] = durations[str(output)]
    monkeypatch.setattr(approved_voice.subprocess, 'run', local_encoder)
    monkeypatch.setattr(build_voice, 'audio_duration', lambda path: durations[str(path)])
    review = approved_voice.fit_pace(plan)
    assert review['status'] == 'paced' and review['manualListening'].startswith('pending')
    assert all(5.3 <= clip['charactersPerSecond'] <= 7.2 for clip in review['clips'])
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert [clip['voice'] for clip in review['clips']] == [shot['voice'] for shot in DRAFTS['messaging']['shots']]


@pytest.mark.parametrize('encoder_fails', [False, True])
def test_common_encoder_is_atomic_and_uses_existing_helper_contract(tmp_path, monkeypatch, encoder_fails):
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', SimpleNamespace(get_ffmpeg_exe=lambda: 'ffmpeg'))
    output = tmp_path / 'clip.mp3'; output.write_bytes(b'previous-good-clip')
    calls = []
    def tts(text, **kwargs):
        calls.append((text, kwargs)); return b'actual-pcm-boundary-double'
    def encode(argv, **kwargs):
        assert kwargs['input'] == b'actual-pcm-boundary-double'
        if encoder_fails:
            raise subprocess.CalledProcessError(1, argv)
        Path(argv[-1]).write_bytes(b'encoded-mp3-boundary-double')
    monkeypatch.setattr(build_voice.subprocess, 'run', encode)
    monkeypatch.setattr(build_voice, 'audio_duration', lambda _: 1.25)
    if encoder_fails:
        with pytest.raises(subprocess.CalledProcessError):
            build_voice.render_clip(SimpleNamespace(tts_realtime=tts), '已批准旁白', output, 1.12, 1.0)
        assert output.read_bytes() == b'previous-good-clip'
    else:
        assert build_voice.render_clip(SimpleNamespace(tts_realtime=tts), '已批准旁白', output, 1.12, 1.0) == 1.25
        assert output.read_bytes() == b'encoded-mp3-boundary-double'
    assert calls == [('已批准旁白', {'voice': 'Elias', 'instructions': build_voice.NARRATION_STYLE, 'speech_rate': 1.12})]
    assert not list(tmp_path.glob('*.pending.mp3'))
