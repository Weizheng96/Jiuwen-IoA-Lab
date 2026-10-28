"""Publish only checked movies and an allowlisted evidence summary; raw runs stay private."""
import argparse,hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def publish(folder,case):
 folder=folder.resolve();proof=read(folder/'evidence.json');qa=read(folder/'qa.json');shots=read(folder/'shot-review.json')
 assert proof['case']==case and not proof.get('fixtures') and not proof['browserErrors'] and shots['result']=='passed'
 if case=='delegation':
  assert qa['result']=='passed' and not proof.get('preview') and proof['focusedCardDigest']==proof['cardDigest']
  assert proof['delegation']['state']=='completed' and proof['delegation']['signatures']['7']==[True] and float(proof['delegation']['paid'])==299
  assert proof['bookingDemo'][0]['cancelled']==1
  movie='02-delegation.mp4';keys=('case','providerExecution','fixtures','browserErrors','quotes','focusedCardDigest','cardDigest','delegation')
  evidence={k:proof[k] for k in keys};evidence['bookingDemo']={'booked':True,'cancelled':True,'configuration':'actual accepted delivery'}
 else:
  assert proof['accessControl']['directSendStatus']==403 and proof['busy']['reason']=='calendar_conflict' and proof['forward']['transport']=='jiuwen-web' and proof['meeting']['status']=='invite_submitted'
  preview=proof.get('preview',False)
  assert (qa['result']=='preview-passed') if preview else (qa['result']=='passed' and proof['outlook']['status']=='accepted_and_visible')
  movie='03-messaging-preview.mp4' if preview else '03-messaging.mp4'
  evidence={k:proof[k] for k in ('case','fixtures','browserErrors','accessControl','busy','outlook','appointment')};evidence['preview']=preview
  evidence['forward']={k:proof['forward'][k] for k in ('status','transport','read')}
  evidence['meeting']={k:proof['meeting'][k] for k in ('status','calendar','recipient','transport','delivery')}
 target=ROOT/'demo/out'/movie;shutil.copyfile(folder/'demo.mp4',target)
 evidence.update(movie='out/'+movie,sha256=hashlib.sha256(target.read_bytes()).hexdigest(),bytes=target.stat().st_size,qa=qa,allShots=[{k:v for k,v in shot.items() if k!='frame'} for shot in shots['shots']])
 dest=ROOT/'demo/assets/cases'/case;dest.mkdir(parents=True,exist_ok=True);write(dest/'evidence.json',evidence);write(dest/'edit-list.json',read(folder/'edit-list.json'));shutil.copyfile(folder/'shot-review.jpg',dest/'encoded-review.jpg')
 for name in qa['frames']:shutil.copyfile(folder/name,dest/name)
 voices=ROOT/'demo/assets/voice'/case;voices.mkdir(parents=True,exist_ok=True);index=read(folder/'voice.json')
 for clip in index:
  source=(folder/clip['file']).resolve();assert source.is_relative_to(folder);target=voices/source.name;shutil.copyfile(source,target);clip['file']=source.name
 write(voices/'voice.json',index)
 return evidence


def main():
 p=argparse.ArgumentParser();p.add_argument('--delegation',type=Path);p.add_argument('--messaging',type=Path);p.add_argument('--date',required=True);a=p.parse_args();dest=ROOT/'demo/verification/cases.json';summary=read(dest) if dest.exists() else {'cases':{}}
 for case,folder in [('delegation',a.delegation),('messaging',a.messaging)]:
  if folder:summary['cases'][case]=publish(folder,case)
 summary['checkedAt']=a.date;write(dest,summary);print(json.dumps({k:{'movie':v['movie'],'sha256':v['sha256'],'qa':v['qa']['result']} for k,v in summary['cases'].items()},ensure_ascii=False))


if __name__=='__main__':main()
