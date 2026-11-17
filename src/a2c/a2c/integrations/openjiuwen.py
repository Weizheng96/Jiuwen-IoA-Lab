"""Provider side on openJiuwen: serve the Provider SDK as an A2A agent, no agent-core change.

openjiuwen's ``A2AServerAdapter`` builds its Agent Card without extensions and hands the agent
only the first text part, so an A2C provider serves its own a2a-sdk app (:func:`a2c.binding.build_app`)
beside its agent. The agent's work plugs in through :class:`CallbackPolicy`: ``on_start`` gets the
card and materials when work may start; the agent delivers with ``Provider.deliver`` and asks for
step authorizations with ``Provider.request_step``. To keep the ``Runner``/``AgentAdapter`` path,
register a factory under a new protocol name with openjiuwen's public
``register_server_adapter("A2C", factory)``.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from ..binding import A2C_EXTENSION, AP2_EXTENSION, build_app
from ..provider import DefaultPolicy, Provider


class CallbackPolicy(DefaultPolicy):
    """A ProviderPolicy made of plain callables (any of them may be omitted)."""

    def __init__(self, *, on_start: Callable | None = None, review: Callable | None = None,
                 draft: Callable | None = None, termination_fee: Callable | None = None) -> None:
        self._on_start, self._review, self._draft, self._fee = on_start, review, draft, termination_fee

    def on_start(self, template, card, materials):
        if self._on_start:
            self._on_start(template, card, materials)

    def review(self, template, card, materials):
        return self._review(template, card, materials) if self._review else None

    def draft(self, template, request, materials):
        return self._draft(template, request, materials) if self._draft else super().draft(template, request, materials)

    def termination_fee(self, template, card, period):
        return self._fee(template, card, period) if self._fee else super().termination_fee(template, card, period)


def provider_app(provider: Provider, *, base_url: str, name: str, jwks: Mapping[str, Any] | None = None):
    """The provider's A2A app: A2C operations plus the AP2 payee role."""
    if jwks is None:
        keys = [provider.signer.jwk]
        if provider.ap2_key is not None: keys.append(provider.ap2_key.jwk)
        jwks = {'keys':keys}
    return build_app(provider, name=name, base_url=base_url, role="provider",
                     jwks=jwks, extensions=(A2C_EXTENSION, AP2_EXTENSION))


def serve(provider: Provider, *, base_url: str, name: str, host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn
    uvicorn.run(provider_app(provider, base_url=base_url, name=name), host=host, port=port)


__all__ = ["CallbackPolicy", "provider_app", "serve"]
