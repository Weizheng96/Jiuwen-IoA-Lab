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
