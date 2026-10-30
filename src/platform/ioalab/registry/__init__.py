"""Registered service facts; category authorization never trusts message-supplied labels."""
import json

from ..errors import LabError


class RegistryView:
    def __init__(self,db):self.db=db

    def category_in(self,conn,provider):
        row=conn.execute("SELECT definition FROM agents WHERE role='service' AND status='online' AND json_extract(meta,'$.url')=?",(provider,)).fetchone()
        if not row:raise LabError('not_ready','The service is not registered',status=409)
        definition=json.loads(row['definition'] or '{}')
        category=definition.get('category')
        if not isinstance(category,str) or not category:raise LabError('not_ready','The actual registered category is unavailable',status=409)
        return category
