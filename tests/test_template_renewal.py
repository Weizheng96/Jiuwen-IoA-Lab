"""Public template validity stays relative to its explicit construction time."""
from datetime import datetime,timezone,timedelta
from a2c.codec import parse_time
from ioalab_sdk.templates import template_from_definition


def test_template_validity_is_relative_beyond_2027():
    d={'formation':'proposal','deliverable':{'name':'result'},'pricing':{'proposalFee':1}}
    at=datetime(2031,6,1,tzinfo=timezone.utc)
    t=template_from_definition(d,packages=None,payment_provider={'id':'urn:pay'},now=at)
    assert parse_time(t['validUntil'])==at+timedelta(days=30)
