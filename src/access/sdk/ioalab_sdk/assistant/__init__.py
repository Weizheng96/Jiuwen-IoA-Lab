"""Personal assistant role entry: delegation, private messages and local inbox rules."""
from .role import AssistantRole
from .rules import parse, render

__all__ = ['AssistantRole', 'parse', 'render']
