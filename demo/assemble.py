"""Join real browser recordings, add timed subtitles and optional narration."""
import argparse,json,subprocess
from pathlib import Path

def stamp(seconds):
    ms=round(seconds*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def ffpath(path):return str(path.resolve()).replace('\\','/').replace(':','\\:').replace("'","\\'")

def ass_stamp(seconds):
    centiseconds=round(seconds*100);s,c=divmod(centiseconds,100);m,s=divmod(s,60);h,m=divmod(m,60)
    return f'{h}:{m:02}:{s:02}.{c:02}'

def main():
    p=argparse.ArgumentParser();p.add_argument('--recording',type=Path,required=True);p.add_argument('--font',type=Path,default=Path('C:/Windows/Fonts/msyh.ttc'));args=p.parse_args()
    import imageio_ffmpeg
    folder=args.recording.resolve();proof=json.loads((folder/'evidence.json').read_text(encoding='utf-8'));captions=json.loads((folder/'captions.json').read_text(encoding='utf-8'));length=proof['duration'];lead=proof.get('captureLead',0)
    srt=[];events=[]
    captions=[line for i,line in enumerate(captions) if i+1==len(captions) or captions[i+1]['at']-line['at']>=1]
    for i,line in enumerate(captions):
        end=min(length,(captions[i+1]['at']-.15) if i+1<len(captions) else length)
        srt.append(str(i+1)+'\n'+stamp(line['at'])+' --> '+stamp(max(line['at']+.5,end))+'\n'+line['text']+'\n')
        safe=line['text'].replace('\\','\\\\').replace('{','').replace('}','').replace('\n','\\N')
        events.append(f'Dialogue: 0,{ass_stamp(line["at"])},{ass_stamp(end)},Default,,0,0,0,,{safe}')
    (folder/'subtitles.srt').write_text('\n'.join(srt),encoding='utf-8')
    header='[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 0\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Default,Microsoft YaHei,48,&H00FFFFFF,&H00FFFFFF,&H00100B08,&HA0000000,-1,0,0,0,100,100,0,0,1,3,1,2,100,100,42,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n'
    size=int(proof.get('subtitleSize',48))
    if not 44<=size<=64:raise ValueError('Caption size outside readable range')
    header=header.replace('Microsoft YaHei,48,','Microsoft YaHei,'+str(size)+',')
    (folder/'subtitles.ass').write_text(header+'\n'.join(events)+'\n',encoding='utf-8-sig')
    single=proof.get('layout')=='single'
    input_file=folder/proof.get('input','screen.webm')
    if not input_file.resolve().is_relative_to(folder):raise ValueError('Video input escapes recording folder')
    inputs=['-i',str(input_file)] if single else ['-i',str(folder/'left.webm'),'-i',str(folder/'right.webm')]
    filters=[f'[0:v]trim=start={lead},setpts=PTS-STARTPTS,scale=1920:1080[screen]'] if single else [f'[0:v]trim=start={lead},setpts=PTS-STARTPTS,scale=960:1080[l]',f'[1:v]trim=start={lead},setpts=PTS-STARTPTS,scale=960:1080[r]','[l][r]hstack=inputs=2[screen]']
    banner='仿真参与方 · 真实协议活动 · 虚拟币 · 样例服务' if proof['case']=='global' else ('本地联调 · 九问/协议真实，服务/日历/收件为测试配置' if proof.get('fixtures') else 'Jiuwen IoA Lab · 实际运行录屏')
    if proof.get('framed'):
        preview=(f"drawtext=fontfile='{ffpath(args.font)}':text='预览 · Outlook接受入历镜头待补录':x=28:y=52:fontsize=24:fontcolor=0xffd985:box=1:boxcolor=0x08111ee6:boxborderw=10," if proof.get('preview') else '')
        filters.append(f"[screen]{preview}subtitles='{ffpath(folder/'subtitles.ass')}'[v]")
    else:
        filters.append(f"[screen]scale=1848:1040,pad=1920:1080:36:40:black,drawtext=fontfile='{ffpath(args.font)}':text='{banner}':x=20:y=8:fontsize=20:fontcolor=white,subtitles='{ffpath(folder/'subtitles.ass')}'[v]")
    maps=['-map','[v]'];voice=folder/'voice.json'
    if voice.exists():
        clips=json.loads(voice.read_text(encoding='utf-8'));audio=[]
        for i,clip in enumerate(clips):
            inputs+=['-i',str(folder/clip['file'])];name='a'+str(i);delay=round(clip['at']*1000);filters.append(f'[{i+(1 if single else 2)}:a]adelay={delay}:all=1[{name}]');audio.append('['+name+']')
        filters.append(''.join(audio)+f'amix=inputs={len(audio)}:normalize=0,apad[a]');maps+=['-map','[a]']
    output=folder/('local-test.mp4' if proof.get('fixtures') else 'demo.mp4')
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y',*inputs,'-filter_complex',';'.join(filters),*maps,'-t',str(length),'-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-movflags','+faststart',str(output)],check=True)
    print(output)

if __name__=='__main__':main()
