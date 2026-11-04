"""Authorized read-only interface directory; sending and processing belong to delivery."""
import json

from fastapi import APIRouter,Depends

from ..errors import LabError


class InterfaceDirectory:
    def __init__(self,db,identities):
        self.db,self.identities=db,identities

    def user(self,username):
        row=self.db.one('SELECT id,username FROM users WHERE username=?',username)
        if not row:raise LabError('not_found','Account not found',status=404)
        return row

    def interfaces(self,owner,viewer):
        rows=self.db.rows('SELECT * FROM message_interfaces WHERE owner=? ORDER BY name',owner)
        actor=next((identity['id'] for identity in self.identities.list_owned(owner)
                    if identity['role']=='assistant' and identity['status']!='disabled'),None)
        result=[]
        for row in rows:
            definition=json.loads(row['definition'])
            if owner!=viewer and (not row['enabled'] or (definition.get('allowedIds') is not None
                                                        and viewer not in definition['allowedIds'])):
                continue
            public={key:value for key,value in definition.items()
                    if owner==viewer or key not in ('allowedIds','allowedSenders')}
            result.append({'name':row['name'],'revision':row['revision'],'agentId':actor,
                           **public,'enabled':bool(row['enabled'])})
        return result


def build_router(directory,current_user):
    router=APIRouter(prefix='/api/v1/messaging')

    @router.get('/interfaces')
    def mine(uid=Depends(current_user)):
        return directory.interfaces(uid,uid)

    @router.get('/accounts/{username}/interfaces')
    def lookup(username:str,uid=Depends(current_user)):
        return directory.interfaces(directory.user(username)['id'],uid)

    return router
