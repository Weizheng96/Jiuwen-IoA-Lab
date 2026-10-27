"""Build narration for recorded timestamps; credential access is explicit and asset-only."""
import argparse,hashlib,importlib.util,json,subprocess,re
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--recording',type=Path,required=True);p.add_argument('--helper',type=Path,required=True,help='Operator-provided Aliyun helper; credentials remain local');p.add_argument('--speech-rate',type=float,default=1.12);p.add_argument('--tempo',type=float,default=1.0);args=p.parse_args()
    if not 1<=args.speech_rate<=2 or not 1<=args.tempo<=1.2:raise ValueError('Narration speed outside supported range')
    import imageio_ffmpeg
    spec=importlib.util.spec_from_file_location('operator_tts',args.helper.resolve());module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);client=module.Aliyun()
    folder=args.recording.resolve();assets=folder/'voice';assets.mkdir(exist_ok=True)
    captions=json.loads((folder/'captions.json').read_text(encoding='utf-8'));index=[]
    proof=json.loads((folder/'evidence.json').read_text(encoding='utf-8'))
    # An initial test label also remains as a permanent banner; don't speak it over
    # the first action when the browser starts that action immediately.
    captions=[line for i,line in enumerate(captions) if not (i==0 and line['text'].startswith('本地联调')) and (i+1==len(captions) or captions[i+1]['at']-line['at']>=1)]
    def duration(path):
        r=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-i',str(path)],capture_output=True,text=True,encoding='utf-8',errors='replace')
        match=re.search(r'Duration: (\d+):(\d+):([\d.]+)',r.stderr)
        if not match:raise RuntimeError('Audio duration unavailable')
        return int(match[1])*3600+int(match[2])*60+float(match[3])
    for i,line in enumerate(captions):
        spoken=line.get('voice',line['text'])
        path=assets/(hashlib.sha256((f'clear-fast-v3|{args.speech_rate}|{args.tempo}|'+spoken).encode()).hexdigest()[:16]+'.mp3')
        if not path.exists():
            pcm=client.tts_realtime(spoken,voice='Elias',instructions='产品演示解说，清晰、笃定、简洁，语速偏快，避免拖长尾音，句内停顿短而自然。A to C按英文A、to、C朗读。',speech_rate=args.speech_rate)
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y','-f','s16le','-ar','24000','-ac','1','-i','-','-af',f'atempo={args.tempo}','-c:a','libmp3lame','-q:a','2',str(path)],input=pcm,check=True)
        seconds=duration(path);available=(captions[i+1]['at'] if i+1<len(captions) else proof['duration'])-line['at']-.2
        if seconds>available:
            rate=seconds/max(available,.5)
            if rate>1.18:raise RuntimeError('Recording needs a longer pause for narration: '+line['text'])
            fitted=path.with_name(path.stem+'-fit.mp3')
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y','-i',str(path),'-af','atempo='+str(rate),str(fitted)],check=True);path=fitted;seconds=duration(path)
        index.append({**line,'file':str(path.relative_to(folder)),'duration':seconds,'speechRate':args.speech_rate,'tempo':args.tempo});print('Narration '+str(i+1)+'/'+str(len(captions))+' '+str(round(seconds,2))+'s',flush=True)
    (folder/'voice.json').write_text(json.dumps(index,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
