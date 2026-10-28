"""Cut actual raw intervals to measured narration; retain a traceable edit decision list."""
import argparse,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from case_story import STORIES
from assemble import ffpath


def main():
 p=argparse.ArgumentParser();p.add_argument('--recording',type=Path,required=True);p.add_argument('--voice-timing',type=Path,required=True);p.add_argument('--preview',action='store_true',help='Explicitly omit an unavailable Outlook shot; never publish as a complete formal film');a=p.parse_args()
 import imageio_ffmpeg
 ff=imageio_ffmpeg.get_ffmpeg_exe();out=a.recording.resolve();proof=json.loads((out/'evidence.json').read_text(encoding='utf-8'));segments=json.loads((out/'segments.json').read_text(encoding='utf-8'));prepared={v['id']:v for v in json.loads(a.voice_timing.read_text(encoding='utf-8'))};raw={s['id']:s for s in segments}
 if proof.get('fixtures') or proof.get('browserErrors'):raise ValueError('Formal editing rejects fixture footage or page errors')
 if proof['case']=='delegation':
  assert proof['delegation']['state']=='completed' and proof['delegation']['signatures']['7']==[True] and float(proof['delegation']['paid'])==299
  assert proof['bookingDemo'][0]['cancelled']==1
 else:
  assert proof['forward']['transport']=='jiuwen-web' and proof['meeting']['status']=='invite_submitted'
  assert proof['busy']['status']=='no_time' and proof['busy']['reason']=='calendar_conflict' and proof['accessControl']['directSendStatus']==403
  if not a.preview:
   assert proof.get('outlook',{}).get('status')=='accepted_and_visible' and raw.get('outlook'),'Actual Outlook acceptance footage is required'
 proof['preview']=a.preview
 story=[s for s in STORIES[proof['case']] if not (a.preview and s[0]=='outlook')]
 clips=out/'clips';clips.mkdir(exist_ok=True);voices=[];captions=[];edl=[];at=0
 for i,(key,text,spoken) in enumerate(story):
  entry=raw[key];voice=prepared[key];assert voice['text']==text and voice['voice']==spoken
  duration=round(voice['duration']+.55,3)
  if duration>entry['duration']:raise ValueError('Raw shot too short for narration: '+key)
  source=(out/entry.get('source','screen.webm')).resolve();assert source.is_relative_to(out)
  dest=clips/f'{i:02}-{key}.mp4';start=entry['start'];filters=[]
  if proof['case']=='delegation' and key in ('quotes','choice'):
   # Explanation overlay is derived from the two actual signed proposals, never a UI receipt.
   from PIL import Image,ImageDraw,ImageFont
   canvas=Image.new('RGBA',(820,320),(10,24,41,247));draw=ImageDraw.Draw(canvas)
   font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',36);small=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',23);big=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',58)
   draw.text((30,20),'实测签名提案 · 讲解叠层',font=small,fill='#a2bdcc')
   rows=sorted(proof['quotes'],key=lambda r:float(r['total']));assert [float(r['total']) for r in rows]==[259,299]
   for n,row in enumerate(rows):
    x=28+n*395;chosen=key=='choice' and float(row['total'])==299;color='#80f2cc' if chosen else '#d8e9f1'
    draw.rounded_rectangle((x,72,x+368,287),radius=14,outline=color,width=3)
    draw.text((x+22,86),'可靠版' if float(row['total'])==299 else '基础版',font=font,fill=color)
    draw.text((x+22,137),str(int(float(row['total'])))+' 虚拟币',font=big,fill=color)
    draw.text((x+22,225),('10项配置判据 · 已选择' if chosen else '10项配置判据') if float(row['total'])==299 else '5项配置判据',font=small,fill=color)
   overlay=clips/(key+'-proposals.png');canvas.save(overlay)
   # movie source is fixed and escaped; filter output is still real recorded footage plus labelled explanation.
   filters.append("movie='"+ffpath(overlay)+"'[q];[0:v][q]overlay=70:380")
  if i and start>segments[segments.index(entry)-1]['start']+segments[segments.index(entry)-1]['duration']+1:
   filters.append("drawtext=fontfile='"+ffpath(Path('C:/Windows/Fonts/msyh.ttc'))+"':text='等待片段已剪短':x=28:y=52:fontsize=18:fontcolor=0xd3dfed:box=1:boxcolor=0x08111ee6:boxborderw=8")
  cmd=[ff,'-hide_banner','-loglevel','error','-y','-ss',str(start),'-i',str(source),'-t',str(duration),'-an','-r','25']
  if filters:cmd+=['-vf',','.join(filters)]
  subprocess.run([*cmd,'-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p',str(dest)],check=True)
  audio=(a.voice_timing.parent/voice['file']).resolve();target=out/'voice'/audio.name;target.parent.mkdir(exist_ok=True);shutil.copyfile(audio,target)
  voices.append({**voice,'at':round(at,3),'file':str(target.relative_to(out))});captions.append({'id':key,'at':round(at,3),'text':text,'voice':spoken});edl.append({'id':key,'rawStart':start,'rawSource':str(source.relative_to(out)),'finalStart':round(at,3),'duration':duration})
  at+=duration
  print('Edited',key,flush=True)
 concat=out/'concat.txt';concat.write_text('\n'.join("file '"+(clips/f'{i:02}-{s[0]}.mp4').as_posix()+"'" for i,s in enumerate(story))+'\n',encoding='utf-8')
 subprocess.run([ff,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(out/'edited.mp4')],check=True)
 proof.update(layout='single',input='edited.mp4',framed=True,subtitleSize=52,duration=round(at,3),captureLead=0,editedFromActualRaw=True)
 for name,value in [('evidence.json',proof),('voice.json',voices),('captions.json',captions),('edit-list.json',edl)]:
  (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
 subprocess.run([sys.executable,str(ROOT/'demo/assemble.py'),'--recording',str(out)],check=True)
 subprocess.run([sys.executable,str(ROOT/'demo/check_recording.py'),'--recording',str(out)],check=True)


if __name__=='__main__':main()
