"""Public protocol service gate; participant execution requires its IoA context."""
from a2c.market import Market
from a2c.errors import OUT_OF_ORDER,fail


class AdmissionMarket(Market):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.requires_admission=lambda provider:False

    def op_card_endorse(self,body,files,task_id):
        card=body.get('card')
        if self.requires_admission(card.get('provider') if isinstance(card,dict) else None):
            fail(OUT_OF_ORDER,'Enrolled provider proposals require the current IoA execution connection and committed admission')
        return super().op_card_endorse(body,files,task_id)

    def op_delivery_anchor(self,body,files,task_id):
        if self.requires_admission((body.get('card') or {}).get('provider')):fail(OUT_OF_ORDER,'Enrolled service delivery requires its original IoA contract context')
        return super().op_delivery_anchor(body,files,task_id)

    def op_card_close(self,body,files,task_id):
        if self.requires_admission((body.get('card') or {}).get('provider')):fail(OUT_OF_ORDER,'Enrolled service closing requires original IoA contract and payment evidence')
        return super().op_card_close(body,files,task_id)
