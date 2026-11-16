"""Lazy unified product entry, keeping installation and skill exports lightweight."""
def assembled(path):
    from .assembly import assembled as build
    return build(path)
