"""L2 model observes; this program decides. Executed only inside WASM."""
import json
from a2c_verifier import observe

def verify(ctx):
    d=json.loads(ctx.read('booking.json').decode('utf-8'))
    observation=observe({'task':'Identify booking, cancellation and capacity control in the delivered sources. Return JSON booleans hasBooking, hasCancellation, hasCapacityControl only.'})
    checks=[{'check':k,'ok':observation.get(k) is True} for k in ('hasBooking','hasCancellation','hasCapacityControl')]
    return {'status':'pass' if all(x['ok'] for x in checks) else 'fail','checks':checks}
