"""Stopped-instance mailbox projection; never republishes rules or replays invitations."""
import os

from a2c.codec import digest
from ioalab_sdk.runtime import Lease, RuntimeFault, private_json, read_json


def configure_email(deployment, recipient):
    from .deployment import Deployment, operator
    from ioalab_tools.calendar import address
    operator(); address(recipient)
    journal = deployment.private/'email-update.json'
    with deployment.lock(), Lease(deployment.home/'resident/client.lock',timeout=0):
        if journal.exists():
            update = read_json(journal)
            if update.get('phase') != 'completed':
                if update['recipient'] != recipient:
                    raise RuntimeFault('configuration_changed','Finish the original email projection first')
            else:
                update = None
        else:
            update = None
        if update is None:
            state = deployment.state(); deployment.prepared(state)
            if deployment.value['recipient'] == recipient:
                return {'status':'completed','recipient':recipient,'changed':False}
            old_recipe = dict(deployment.value)
            new_recipe = {**old_recipe,'recipient':recipient}
            old_runtime = read_json(deployment.runtime_path)
            new_runtime = read_json(deployment.runtime_path)
            for tool in new_runtime['host']['business_tools']:
                if tool['factory']=='calendar': tool['options']['recipient']=recipient
            deployment.value = new_recipe
            ownership = read_json(deployment.state_root/'.ioalab-deployment.json')
            new_state = {**state,'binding':digest({'recipe':new_recipe,'ownership':ownership,
                         'presetDigest':deployment.resource_digest}),'finalConfigDigest':digest(new_runtime)}
            update = {'phase':'writing','recipient':recipient,'files':[
                {'path':str(deployment.path),'old':old_recipe,'new':new_recipe},
                {'path':str(deployment.runtime_path),'old':old_runtime,'new':new_runtime},
                {'path':str(deployment.record_path),'old':state,'new':new_state}]}
            private_json(journal, update)
        # Each file can be only the recorded old or new version after interruption.
        from pathlib import Path
        for item in update['files']:
            path=Path(item['path'])
            if path not in (deployment.path,deployment.runtime_path,deployment.record_path):
                raise RuntimeFault('identity_conflict','Email projection escaped the fixed managed instance')
            current=read_json(path)
            if current not in (item['old'],item['new']):
                raise RuntimeFault('configuration_changed','An email projection input changed concurrently')
            if current != item['new']:
                owner=path.stat()
                private_json(path,item['new'])
                if os.name=='posix':os.chown(path,owner.st_uid,owner.st_gid)
        update['phase']='completed'; private_json(journal,update)
        return {'status':'completed','recipient':recipient,'changed':True}
