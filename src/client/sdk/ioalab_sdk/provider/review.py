"""The absolute review limit of the original countersign request."""
from dataclasses import dataclass
from time import time

from a2c.card import card_digest
from a2c.codec import parse_duration,parse_time
from a2c.errors import A2CError,DEADLINE_PASSED
from a2c.jws import FRESHNESS,ts_of

from ..runtime import RuntimeFault,stamp


@dataclass(frozen=True)
class CountersignWindow:
    admission: str
    deadline: float
    limit: str

    @classmethod
    def from_request(cls,identity,relation,message,original):
        try:
            request=message['body']
            card=request['body']['card']
            receipt=original['admission']
            template=original['template']
            admission=receipt['admissionId']
            if (not isinstance(admission,str) or not admission or
                    (admission,receipt['interactionId'],receipt['originMessageId'],receipt['service'],receipt['client'],
                     receipt['bindingDigest'],receipt['scopeDigest'],card['templateDigest'],template['formation'],receipt['state'])!=
                    (request['admissionId'],relation['id'],relation['origin'],identity.agent_id,relation['peer'],
                     card_digest(card),card['delegationScopeDigest'],template['templateDigest'],'countersign','held')):
                raise ValueError('Admission does not match the original request')
            signed=ts_of(card['signatures']['1'])
            limit,deadline=min({
                'reservation':parse_time(receipt['holdUntil']),
                'protocol request':parse_time(message['expiresAt']),
                'template':parse_time(template['validUntil']),
                'response':signed+parse_duration(template['deadlines']['respondWithin']),
                'slot 1 freshness':signed+FRESHNESS,
            }.items(),key=lambda pair:pair[1])
        except (KeyError,TypeError,ValueError,AttributeError,OverflowError):
            raise RuntimeFault('not_ready','Cannot verify the original countersign admission and deadline','inspect_original_admission') from None
        return cls(admission,deadline.timestamp(),limit)

    def expired(self,phase):
        return A2CError(DEADLINE_PASSED,
                        f'Admission {self.admission} reached its {self.limit} deadline at {stamp(self.deadline)} during {phase}')

    def require_open(self,phase):
        if self.elapsed():
            raise self.expired(phase)

    def elapsed(self):
        return time()>=self.deadline
