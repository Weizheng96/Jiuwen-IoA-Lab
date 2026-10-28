"""Inspect every encoded shot and its matching narration window."""
import argparse,json,re,subprocess
from pathlib import Path


def main():
 p=argparse.ArgumentParser();p.add_argument('--recording',type=Path,required=True);a=p.parse_args();folder=a.recording.resolve()
 import imageio_ffmpeg
 from PIL import Image,ImageDraw
 ff=imageio_ffmpeg.get_ffmpeg_exe();proof=json.loads((folder/'evidence.json').read_text(encoding='utf-8'));edl=json.loads((folder/'edit-list.json').read_text(encoding='utf-8'));voice=json.loads((folder/'voice.json').read_text(encoding='utf-8'));review=folder/'shot-review';review.mkdir(exist_ok=True);report=[]
 for shot,clip in zip(edl,voice,strict=True):
  assert shot['id']==clip['id'] and shot['finalStart']==clip['at']
  image=review/(shot['id']+'.png');subprocess.run([ff,'-hide_banner','-loglevel','error','-y','-ss',str(shot['finalStart']+min(shot['duration']/2,4)),'-i',str(folder/'demo.mp4'),'-frames:v','1',str(image)],check=True)
  result=subprocess.run([ff,'-hide_banner','-ss',str(clip['at']+.1),'-t',str(max(.5,clip['duration']-.2)),'-i',str(folder/'demo.mp4'),'-af','volumedetect','-f','null','-'],capture_output=True,text=True,encoding='utf-8',errors='replace',check=True)
  volume=re.search(r'mean_volume: ([-\d.]+)',result.stderr);assert volume and float(volume[1])>-38,shot['id']
  report.append({'id':shot['id'],'meanVolumeDb':float(volume[1]),'frame':str(image.relative_to(folder)),'narrationWithinShot':clip['duration']<=shot['duration']})
 canvas=Image.new('RGB',(1440,((len(report)+2)//3)*294),'#071321');draw=ImageDraw.Draw(canvas)
 for i,row in enumerate(report):
  im=Image.open(folder/row['frame']).convert('RGB');im.thumbnail((480,270));x=(i%3)*480;y=(i//3)*294;canvas.paste(im,(x,y));draw.text((x+8,y+272),row['id']+'  '+str(row['meanVolumeDb'])+' dB',fill='white')
 canvas.save(folder/'shot-review.jpg');(folder/'shot-review.json').write_text(json.dumps({'shots':report,'result':'passed','preview':proof.get('preview',False)},ensure_ascii=False,indent=2),encoding='utf-8');print('Every encoded shot and narration window passed:',len(report))


if __name__=='__main__':main()
