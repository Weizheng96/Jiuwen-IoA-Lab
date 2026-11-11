"""Registered service directory facts; category authorization never trusts message-supplied labels."""
from ..errors import LabError


class RegistryView:
    def __init__(self,db,identities):self.db,self.identities=db,identities

    def category_in(self,conn,provider):
        row=conn.execute('SELECT actor,category FROM registry_services WHERE protocol=?',(provider,)).fetchone()
        if not row or not self.identities.active_service(row['actor']):
            raise LabError('not_ready','The service is not registered',status=409)
        if not row['category']:raise LabError('not_ready','The actual registered category is unavailable',status=409)
        return row['category']

    def listings(self):
        """Listed services by id: public name, protocol address and actual category."""
        return {row['actor']:row for row in self.db.rows('SELECT actor,protocol,name,category FROM registry_services')}
