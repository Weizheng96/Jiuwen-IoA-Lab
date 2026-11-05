"""One browser session cookie for the configured deployment transport."""
from urllib.parse import urlsplit


def session_cookie_name(public_url):
    return '__Host-ioalab_session' if urlsplit(public_url).scheme=='https' else 'ioalab_session'


def session_cookie_options(public_url):
    return {'httponly':True,'samesite':'lax','secure':urlsplit(public_url).scheme=='https','path':'/'}
