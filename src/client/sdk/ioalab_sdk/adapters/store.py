"""The original A2C Store interface, scoped inside the single resident database."""
import copy
from a2c.codec import digest
from ..runtime.protocol import RuntimeFault


class ProtocolStore:
    def __init__(self,store,actor,interaction):
        relation=store.interaction(interaction_id=interaction)
        if not relation or relation['actor']!=actor or relation['name']!='delegation':
            raise RuntimeFault('forbidden','Protocol storage requires an original local delegation')
        self.store,self.prefix=store,'a2c:'+digest({'actor':actor,'interaction':interaction})+':'

    def put(self,table,key,value):self.store.checkpoint_set(self.prefix+table,key,value)
    def get(self,table,key,default=None):
        value=self.store.checkpoint(self.prefix+table,key)
        return copy.deepcopy(default) if value is None else value
    def all(self,table):return self.store.checkpoints(self.prefix+table)
    def delete(self,table,key):self.store.checkpoint_delete(self.prefix+table,key)
