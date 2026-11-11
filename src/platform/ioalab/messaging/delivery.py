"""Durable participant delivery and interaction authority. No agent execution.

DeliveryBroker is composed of its parts: connections, controls, sending, interactions and retention.
"""
from __future__ import annotations
import threading
import time

from .connections import Connections
from .controls import Controls
from .interactions import Interactions
from .interfaces import PublicInterfaces
from .retention import Retention
from .sending import Sending


class DeliveryBroker(Connections, Controls, Sending, Interactions, Retention):
    CONTROLS=frozenset(('message.send','message.ack','request.start','interaction.close','contract.bind'))

    def __init__(self,db,identities,settings,*,record_in=None,publish=None,clock=time.time):
        self.db,self.identities,self.s,self.clock=db,identities,settings,clock
        self.record_in,self.publish_event=record_in,publish
        self.interfaces=PublicInterfaces(db,identities)
        self.admissions=None
        self.protocol=None
        self.observe_in=None
        self._listeners={}
        self._listener_lock=threading.RLock()
