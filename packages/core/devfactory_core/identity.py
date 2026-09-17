"""Identity types, mirroring ``identity/v1`` from agent-platform.

RFC-0006 adopts ``identity/v1`` rather than defining parallel id types, so this
module validates against that contract's shape and does not invent its own.
It is not a schema — the wire format is agent-platform's. It is the minimum
needed to refuse a malformed identifier before it reaches an audit record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .errors import InvalidIdentifier

#: identity/v1 ``$defs.Id`` — lowercase, leading alphanumeric, max 63 characters.
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

#: RFC-0006: single-tenant deployments use this literal rather than omitting the
#: field, so the payload shape is already right when a second tenant appears.
DEFAULT_TENANT = "default"

PrincipalType = Literal["human", "agent", "service"]


def validate_id(field: str, value: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        raise InvalidIdentifier(field, value)
    return value


@dataclass(frozen=True, slots=True)
class Principal:
    """Who acted — the answer to "who" in every audit record.

    ``on_behalf_of`` carries a delegation chain; per identity/v1 it must never
    widen scope beyond the principal it came from.
    """

    type: PrincipalType
    id: str
    display_name: str | None = None
    on_behalf_of: "Principal | None" = None

    def __post_init__(self) -> None:
        if self.type not in ("human", "agent", "service"):
            raise ValueError(f"principal type must be human, agent, or service — got {self.type!r}")
        validate_id("principal.id", self.id)

    def as_payload(self, *, include_display_name: bool = False) -> dict:
        """Render for the wire. ``display_name`` is left out unless asked for.

        RFC-0013: a leaf that may hold human text has to be declared, and every
        other leaf is a pointer. A person's real name is human text, and it was
        in every audit record this engine had ever written — the same finding
        ``care-agent-platform`` reported from 112 real events, and the reason
        ADR-0031 exists.

        The default is off rather than on because the field is supplied by the
        contract rather than filled by the domain, which is exactly why it went
        unnoticed: it looked like the platform's business rather than ours.
        ``id`` and ``type`` already answer *who acted*; the name answers nothing
        an auditor cannot reach through the id, and it cannot be deleted once
        written.

        Callers that need it in process still have :attr:`display_name`.
        """
        payload: dict = {"type": self.type, "id": self.id}
        if include_display_name and self.display_name is not None:
            payload["display_name"] = self.display_name
        if self.on_behalf_of is not None:
            # The chain is rendered under the same rule — a nested principal is
            # a person too, and this is where the reference producer's first cut
            # missed one.
            payload["on_behalf_of"] = self.on_behalf_of.as_payload(
                include_display_name=include_display_name
            )
        return payload
