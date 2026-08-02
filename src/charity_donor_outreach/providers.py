"""Provider boundary and deterministic offline implementation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, final

from .models import Channel, DraftCandidate, DraftRequest, FactSource
from .policy import format_ask_paragraph


class DraftProvider(Protocol):
    """Minimal model/provider contract enforced by OutreachService."""

    @property
    def name(self) -> str:
        """Return a stable provider identifier without secrets."""

    def generate(self, request: DraftRequest) -> DraftCandidate | Mapping[str, Any]:
        """Generate a candidate draft from the minimized request."""


@final
class TemplateProvider:
    """Deterministic provider for local execution, CI, and contract testing."""

    __slots__ = ()
    name = "template-v1"

    def generate(self, request: DraftRequest) -> DraftCandidate:
        campaign_facts = [fact for fact in request.facts if fact.source != FactSource.CRM]
        donor_facts = [fact for fact in request.facts if fact.source == FactSource.CRM]
        selected_facts = [*campaign_facts[:1], *donor_facts[:1]]

        paragraphs = [
            request.salutation,
            f"Thank you for being part of {request.organization_name}'s community.",
            request.purpose,
        ]
        paragraphs.extend(fact.text for fact in selected_facts)
        paragraphs.append(format_ask_paragraph(request.campaign_name, request.ask))
        paragraphs.extend(
            [
                f"{request.call_to_action.label}: {request.call_to_action.url}",
                (f"With gratitude,\n{request.sender.name}\n{request.sender.role}"),
            ]
        )

        subject = f"Support {request.campaign_name}" if request.channel == Channel.EMAIL else None
        return DraftCandidate(
            subject_line=subject,
            salutation=request.salutation,
            body="\n\n".join(paragraphs),
            fact_ids_used=[fact.fact_id for fact in selected_facts],
        )
