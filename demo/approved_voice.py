"""Prepare/synthesize only approved narration using the existing Lab TTS pipeline.

No recording, movie evidence, business call or model reply is generated here.
The explicitly supplied Aliyun helper owns credential loading and the official
QwenTtsRealtime client; this module never reads account/key files directly.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import math

if __package__:
    from . import build_voice
    from .review_story import APPROVAL, approved_content
else:
    import build_voice
    from review_story import APPROVAL, approved_content

DEMO=Path(__file__).resolve().parent


def cache_name(spoken,settings):
    """Bind approved audio to every synthesis and encoding input."""
    value={name:settings[name] for name in ('model','voice','style','speechRate','tempo','format')}
    value['text']=spoken
    encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()+'.'+value['format']


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    temporary.replace(path)


def voice_plan(case):
    scene=approved_content()[case]
    clips=[];at=0
    for shot in scene['shots']:
        spoken=shot['voice']
        if not spoken.strip() or '[' in spoken or ']' in spoken:
            raise ValueError('Narration must be approved non-placeholder text')
        clips.append({'id':shot['id'],'text':shot['text'],'voice':spoken,'at':at,'plannedSeconds':shot['seconds']})
        at+=shot['seconds']
    plan={'kind':'approved-narration-plan','scene':case,'approvalSha256':APPROVAL['contentSha256'],
          'recorded':False,'source':'demo/review_story.py','plannedDuration':at,'clips':clips}
    if 'recordingAdjustment' in scene:plan['recordingAdjustment']=scene['recordingAdjustment']
    return plan


def validate_plan(plan):
    if plan!=voice_plan(plan['scene']):
        raise ValueError('Narration plan differs from approved source')


def prepare(case,*,check=False):
    plan=voice_plan(case);path=DEMO/case/'assets/voice/voiceplan.json'
    if check:
        if not path.is_file() or json.loads(path.read_text(encoding='utf-8'))!=plan:
            raise ValueError('Regenerate approved narration plan: '+case)
    else:save(path,plan)
    return path


def reindex_cached(plan_path):
    """Reorder verified existing narration only; never synthesize or encode."""
    folder=plan_path.parent
    def read(name):return json.loads((folder/name).read_text(encoding='utf-8'))
    old=read(plan_path.name);plan=voice_plan(old['scene'])
    if any(old.get(key)!=plan[key] for key in
           ('kind','scene','approvalSha256','recorded','source','plannedDuration')):
        raise ValueError('Cached plan has another approval or scope')
    def indexed(clips):
        if (not isinstance(clips,list) or any(not isinstance(c,dict) or not isinstance(c.get('id'),str) for c in clips)
                or len({c['id'] for c in clips})!=len(clips)):
            raise ValueError('Cached narration IDs must be unique')
        result={clip['id']:clip for clip in clips}
        if set(result)!={clip['id'] for clip in plan['clips']}:
            raise ValueError('Cached narration must retain every approved shot')
        for spec in plan['clips']:
            if any(result[spec['id']].get(key)!=spec[key] for key in ('text','voice','plannedSeconds')):
                raise ValueError('Cached narration content changed')
        return result
    indexed(old['clips'])
    raw=read('narration.json');paced=read('pace-review.json')
    if (raw.get('status')!='ready' or paced.get('status')!='paced' or
            any(value.get('scene')!=plan['scene'] or value.get('approvalSha256')!=plan['approvalSha256']
                for value in (raw,paced))):
        raise ValueError('Verified cached narration is required')
    if raw.get('clips')!=read('voice.json') or paced.get('clips')!=read('voice-paced.json'):
        raise ValueError('Cached narration indexes disagree')
    raw_by_id=indexed(raw['clips']);paced_by_id=indexed(paced['clips'])
    durations={}
    def audio(clip):
        name=clip.get('file')
        if not isinstance(name,str) or Path(name).name!=name:
            raise ValueError('Cached audio must be a local asset filename')
        path=folder/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=clip.get('audioSha256'):
            raise ValueError('Cached audio is missing or changed')
        duration=clip.get('duration')
        if type(duration) not in (int,float) or not math.isfinite(duration) or duration<=0:
            raise ValueError('Cached audio lacks a measured duration')
        if path not in durations:
            try:durations[path]=build_voice.audio_duration(path,timeout=5)
            except (RuntimeError,subprocess.TimeoutExpired):
                raise ValueError('Cached audio duration could not be verified') from None
        if not math.isclose(durations[path],duration,rel_tol=0,abs_tol=.011):
            raise ValueError('Cached audio duration changed')
    for spec in plan['clips']:
        original=raw_by_id[spec['id']];fitted=paced_by_id[spec['id']]
        if original.get('file')!=cache_name(original['voice'],raw['settings']):
            raise ValueError('Cached audio settings changed')
        audio(original)
        factor=fitted.get('paceFactor')
        if type(factor) not in (int,float) or not math.isfinite(factor) or not .75<=factor<=1.2:
            raise ValueError('Cached pacing metadata changed')
        expected=original['file']
        if factor!=1:
            suffix=hashlib.sha256((original['audioSha256']+'|'+str(factor)).encode()).hexdigest()[:10]
            expected=Path(expected).stem+'-pace-'+suffix+'.mp3'
        if (fitted.get('rawFile')!=original['file'] or fitted.get('rawAudioSha256')!=original['audioSha256']
                or fitted.get('file')!=expected):
            raise ValueError('Cached pacing source changed')
        audio(fitted)
    for report in (raw,paced):
        total=report.get('audioTotalSeconds')
        if (type(total) not in (int,float) or not math.isfinite(total) or
                not math.isclose(total,sum(c['duration'] for c in report['clips']),rel_tol=0,abs_tol=.001)):
            raise ValueError('Cached audio total changed')
    # Validate all inputs before the first write. The at field is planned
    # position; all original measured durations and audio bytes are retained.
    for report,by_id in ((raw,raw_by_id),(paced,paced_by_id)):
        report['clips']=[{**by_id[spec['id']],'at':spec['at']} for spec in plan['clips']]
        if 'recordingAdjustment' in plan:report['recordingAdjustment']=plan['recordingAdjustment']
    for name,value in ((plan_path.name,plan),('narration.json',raw),('voice.json',raw['clips']),
                       ('pace-review.json',paced),('voice-paced.json',paced['clips'])):
        save(folder/name,value)
    return {'scene':plan['scene'],'reindexed':len(plan['clips']),'audioBytesChanged':False,
            'providerCalls':0,'durationProbes':len(durations),
            'previewNeedsRebuild':(folder/'approved-narration-preview.mp3').exists()}


def operator_client(helper):
    spec=importlib.util.spec_from_file_location('operator_tts',helper.resolve())
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.Aliyun()


def synthesize(plan_path,helper,*,speech_rate=1.6,tempo=1.12,client_factory=operator_client):
    if not 1<=speech_rate<=2 or not 1<=tempo<=1.2:raise ValueError('Narration speed outside supported range')
    plan=json.loads(plan_path.read_text(encoding='utf-8'));validate_plan(plan)
    folder=plan_path.parent;metadata=folder/'narration.json';index_path=folder/'voice.json'
    settings={'voice':'Elias','style':build_voice.NARRATION_STYLE,'speechRate':speech_rate,'tempo':tempo,
              'model':'qwen3-tts-flash-realtime','format':'mp3','pipeline':'demo/build_voice.py'}
    report={'scene':plan['scene'],'approvalSha256':plan['approvalSha256'],'recorded':False,'status':'building',
            'scope':'Approved narration assets only; no business/model replies or recorded pictures',
            'settings':settings,'helperSha256':hashlib.sha256(helper.read_bytes()).hexdigest(),'clips':[]}
    prior=json.loads(metadata.read_text(encoding='utf-8')) if metadata.exists() else {}
    old={item['id']:item for item in prior.get('clips',[])} if prior.get('settings')==settings else {}
    client=None
    try:
        for clip in plan['clips']:
            filename=cache_name(clip['voice'],settings);path=folder/filename
            previous=old.get(clip['id']);reused=False
            if (previous and previous.get('file')==filename and previous.get('voice')==clip['voice'] and path.is_file()
                    and previous.get('audioSha256')==hashlib.sha256(path.read_bytes()).hexdigest()):
                seconds=previous['duration'];reused=True
            else:
                if client is None:client=client_factory(helper)
                seconds=build_voice.render_clip(client,clip['voice'],path,speech_rate,tempo)
            item={**clip,'file':filename,'duration':seconds,'audioSha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                  'speechRate':speech_rate,'tempo':tempo,'reused':reused,
                  'needsLongerPicture':seconds>clip['plannedSeconds']-.2}
            report['clips'].append(item)
            save(index_path,report['clips']);save(metadata,report)
            print(f"{plan['scene']} {len(report['clips'])}/{len(plan['clips'])} {clip['id']}: {seconds:.2f}s {'reused' if reused else 'synthesized'}",flush=True)
        report['status']='ready'
        report['audioTotalSeconds']=round(sum(item['duration'] for item in report['clips']),3)
        report['needsLongerPicture']=[item['id'] for item in report['clips'] if item['needsLongerPicture']]
        save(metadata,report)
        return report
    except Exception as error:
        report.update(status='failed',errorType=type(error).__name__)
        save(metadata,report)
        # Provider/helper errors may include credentials or private paths. Keep
        # only the exception type in public logs and asset metadata.
        raise RuntimeError('Narration synthesis failed: '+type(error).__name__) from None


def fit_pace(plan_path):
    """Locally fit measured outliers; preserve original TTS audio and text."""
    import imageio_ffmpeg
    plan=json.loads(plan_path.read_text(encoding='utf-8'));validate_plan(plan)
    folder=plan_path.parent
    raw=json.loads((folder/'narration.json').read_text(encoding='utf-8'))
    if raw['status']!='ready' or [clip['voice'] for clip in raw['clips']]!=[clip['voice'] for clip in plan['clips']]:
        raise ValueError('Complete approved narration is required before pace fitting')
    clips=[]
    for clip in raw['clips']:
        source=folder/clip['file']
        if source.name!=cache_name(clip['voice'],raw['settings']):
            raise ValueError('Original narration cache differs from synthesis settings')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=clip['audioSha256']:
            raise ValueError('Original narration audio changed')
        seconds=build_voice.audio_duration(source)
        pace=len(clip['voice'])/seconds
        factor=1.0 if 5.3<=pace<=7.2 else round(min(1.2,max(.75,6.5/pace)),5)
        target=source
        if factor!=1:
            suffix=hashlib.sha256((clip['audioSha256']+'|'+str(factor)).encode()).hexdigest()[:10]
            target=folder/(source.stem+'-pace-'+suffix+'.mp3')
            if not target.exists():
                temporary=target.with_name(target.stem+'.pending.mp3')
                try:
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y',
                                    '-i',str(source),'-af','atempo='+str(factor),'-c:a','libmp3lame','-q:a','2',str(temporary)],check=True)
                    if build_voice.audio_duration(temporary)<=0:raise ValueError('Invalid paced audio')
                    temporary.replace(target)
                finally:temporary.unlink(missing_ok=True)
            seconds=build_voice.audio_duration(target)
        paced={**clip,'file':target.name,'duration':seconds,'audioSha256':hashlib.sha256(target.read_bytes()).hexdigest(),
               'rawFile':source.name,'rawAudioSha256':clip['audioSha256'],'paceFactor':factor,
               'charactersPerSecond':round(len(clip['voice'])/seconds,3),
               'needsLongerPicture':seconds>clip['plannedSeconds']-.2}
        clips.append(paced)
    passed=all(5.3<=clip['charactersPerSecond']<=7.2 for clip in clips)
    review={'scene':plan['scene'],'approvalSha256':plan['approvalSha256'],'recorded':False,
            'status':'paced' if passed else 'needs_pace_review','characterPaceRange':[5.3,7.2],
            'manualListening':'pending; numeric pace and file checks do not verify pronunciation',
            'audioTotalSeconds':round(sum(clip['duration'] for clip in clips),3),'clips':clips}
    save(folder/'voice-paced.json',clips);save(folder/'pace-review.json',review)
    if not passed:raise ValueError('Narration pace still needs review')
    print(plan['scene']+': local pace checks passed; original audio preserved',flush=True)
    return review


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene',choices=list(approved_content()),action='append')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--synthesize',action='store_true')
    parser.add_argument('--fit-pace',action='store_true',help='local audio only; no API or credential loading')
    parser.add_argument('--reindex-cached',action='store_true',help='reorder verified audio indexes without provider or encoding calls')
    parser.add_argument('--helper',type=Path)
    parser.add_argument('--speech-rate',type=float,default=1.6)
    parser.add_argument('--tempo',type=float,default=1.12)
    args=parser.parse_args()
    if args.reindex_cached and (args.check or args.synthesize or args.fit_pace):
        parser.error('--reindex-cached only reorders existing verified assets')
    if args.check and (args.synthesize or args.fit_pace):parser.error('--check cannot synthesize or fit audio')
    if args.synthesize and not args.helper:parser.error('--helper is required for synthesis')
    for case in args.scene or approved_content():
        if args.reindex_cached:
            print(json.dumps(reindex_cached(DEMO/case/'assets/voice/voiceplan.json'),ensure_ascii=False),flush=True)
            continue
        path=prepare(case,check=args.check)
        print(case+': approved narration plan '+('checked' if args.check else 'prepared'),flush=True)
        if args.synthesize:
            report=synthesize(path,args.helper,speech_rate=args.speech_rate,tempo=args.tempo)
            print(case+': narration ready, '+str(report['audioTotalSeconds'])+' audio seconds; pictures remain unrecorded',flush=True)
        if args.fit_pace:fit_pace(path)


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    main()
