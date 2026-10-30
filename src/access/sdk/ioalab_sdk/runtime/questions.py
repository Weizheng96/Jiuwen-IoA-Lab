"""Answer only the original native question through the trusted local user channel."""
import uuid
from .protocol import RuntimeFault,encode


def native_answer(runtime,actor,job,record,stop):
    payload=record['payload'];native=payload['interaction']
    operation=str(uuid.uuid5(uuid.NAMESPACE_URL,encode([job,payload['interaction_id']])))
    binding={'actor':actor,'operationId':operation,
             'subject':{'requestId':record['request_id'],'sessionId':record['session_id'],'interactionId':payload['interaction_id']},
             'stage':'host.interaction','action':'native.answer','preview':{'title':'九问需要您的补充答复','question':native}}
    value=runtime.confirmations.ensure(binding,kind='question',questions=native.get('questions',[]),ttl=300)
    cancelled=record.get('_cancelled',lambda:False)
    try:
        with runtime.scheduler.user_wait(job,cancelled=lambda:stop.is_set() or cancelled()):
            while not stop.is_set() and not cancelled():
                answer=runtime.confirmations.native_answer(value['reference'])
                if answer is not None:return answer
                stop.wait(.2)
    finally:runtime.confirmations.cancel_question(value['reference'])
    raise RuntimeFault('closed','Local execution stopped while waiting for the user')
