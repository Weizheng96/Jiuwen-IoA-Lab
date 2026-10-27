"""Join real browser recordings, add timed subtitles and optional narration."""
import argparse,json,subprocess
from pathlib import Path

def stamp(seconds):
    ms=round(seconds*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def ffpath(path):return str(path.resolve()).replace('\\','/').replace(':','\\:').replace("'","\\'")

def main():
    p=argparse.ArgumentParser();p.add_argument('--recording',type=Path,required=True);p.add_argument('--font',type=Path,default=Path('C:/Windows/Fonts/msyh.ttc'));args=p.parse_args()
    import imageio_ffmpeg
    folder=args.recording.resolve();proof=json.loads((folder/'evidence.json').read_text(encoding='utf-8'));captions=json.loads((folder/'captions.json').read_text(encoding='utf-8'));length=proof['duration'];lead=proof.get('captureLead',0)
    srt=[]
    captions=[line for i,line in enumerate(captions) if i+1==len(captions) or captions[i+1]['at']-line['at']>=1]
    for i,line in enumerate(captions):
        end=min(length,(captions[i+1]['at']-.15) if i+1<len(captions) else length)
        srt.append(str(i+1)+'\n'+stamp(line['at'])+' --> '+stamp(max(line['at']+.5,end))+'\n'+line['text']+'\n')
    (folder/'subtitles.srt').write_text('\n'.join(srt),encoding='utf-8')
    inputs=['-i',str(folder/'left.webm'),'-i',str(folder/'right.webm')]
    filters=[f'[0:v]trim=start={lead},setpts=PTS-STARTPTS,scale=960:1080[l]',f'[1:v]trim=start={lead},setpts=PTS-STARTPTS,scale=960:1080[r]','[l][r]hstack=inputs=2[screen]']
    banner='本地联调 · 九问/协议真实，服务/日历/收件为测试配置' if proof.get('fixtures') else 'Jiuwen IoA Lab · 实际运行录屏'
    filters.append(f"[screen]drawbox=x=0:y=0:w=iw:h=40:color=black@0.8:t=fill,drawtext=fontfile='{ffpath(args.font)}':text='{banner}':x=20:y=8:fontsize=20:fontcolor=white,subtitles='{ffpath(folder/'subtitles.srt')}':force_style='FontName=Microsoft YaHei,FontSize=10,Outline=1,MarginV=10'[v]")
    maps=['-map','[v]'];voice=folder/'voice.json'
    if voice.exists():
        clips=json.loads(voice.read_text(encoding='utf-8'));audio=[]
        for i,clip in enumerate(clips):
            inputs+=['-i',str(folder/clip['file'])];name='a'+str(i);delay=round(clip['at']*1000);filters.append(f'[{i+2}:a]adelay={delay}:all=1[{name}]');audio.append('['+name+']')
        filters.append(''.join(audio)+f'amix=inputs={len(audio)}:normalize=0,apad[a]');maps+=['-map','[a]']
    output=folder/('local-test.mp4' if proof.get('fixtures') else 'demo.mp4')
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y',*inputs,'-filter_complex',';'.join(filters),*maps,'-t',str(length),'-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-movflags','+faststart',str(output)],check=True)
    print(output)

if __name__=='__main__':main()
