"""Local MCP protocol adapter; business operations remain in the resident SDK."""
from .server import create_server, main

__all__ = ['create_server', 'main']
