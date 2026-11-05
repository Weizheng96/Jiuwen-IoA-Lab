"""Resolve frequency-policy source without trusting arbitrary forwarding chains."""
import ipaddress

from ..errors import LabError

HEADER='x-ioalab-client-ip'


def normalize(value):
    address=ipaddress.ip_address(value)
    return str(getattr(address,'ipv4_mapped',None) or address)


def client_ip(request,settings):
    try:peer=normalize(request.client.host if request.client else '')
    except ValueError:return 'unknown'
    trusted={normalize(value) for value in settings.trusted_proxy_addresses}
    if peer not in trusted:
        # A public client cannot assert localhost through any header.
        return peer
    values=[value.decode('latin-1') for name,value in request.scope.get('headers',[]) if name.lower()==HEADER.encode()]
    if len(values)!=1:
        raise LabError('forbidden','Verified proxy source is required',status=403)
    try:return normalize(values[0])
    except ValueError:
        raise LabError('forbidden','Verified proxy source is required',status=403) from None
