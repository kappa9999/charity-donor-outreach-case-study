from __future__ import annotations

from copy import deepcopy
from typing import Any

from charity_donor_outreach.models import CampaignBrief, DraftCandidate, DraftRequest
from charity_donor_outreach.providers import TemplateProvider


def campaign_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "campaign_id": "TEST-CAMPAIGN",
        "organization_name": "Harbor Paws Animal Rescue",
        "campaign_name": "Emergency Foster Network",
        "purpose": (
            "This campaign will expand temporary foster capacity "
            "for animals displaced by severe weather."
        ),
        "tone": "warm-professional",
        "sender": {
            "name": "Jordan Lee",
            "role": "Director of Development",
        },
        "call_to_action": {
            "label": "Donate securely",
            "url": "https://donate.example.org/foster-network",
        },
        "as_of_date": "2026-08-01",
        "minimum_days_between_contacts": 30,
        "ask_policy": {
            "strategy": "last_gift_multiplier",
            "currency": "USD",
            "multiplier": "1.25",
            "rounding_increment": "5.00",
            "minimum": "25.00",
            "maximum": "5000.00",
        },
        "review_policy": {
            "segments": ["major", "principal"],
            "ask_amount_at_or_above": "1000.00",
        },
        "facts": [
            {
                "fact_id": "campaign.foster-purpose",
                "text": "The foster network gives displaced animals a temporary place to stay.",
                "source": "campaign",
                "category": "program",
                "approved_for_outreach": True,
            }
        ],
        "prohibited_phrases": ["every hour counts", "guaranteed match"],
    }
    payload.update(deepcopy(overrides))
    return payload


def campaign(**overrides: Any) -> CampaignBrief:
    return CampaignBrief.model_validate(campaign_payload(**overrides))


def donor_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "donor_id": "TEST-001",
        "first_name": "Maya",
        "last_name": "Chen",
        "title": None,
        "preferred_channel": "email",
        "channel_consent": "granted",
        "do_not_contact": False,
        "email": "maya.chen@example.org",
        "postal_address": None,
        "segment": "general",
        "giving": {
            "currency": "USD",
            "last_gift_amount": "100.00",
            "largest_gift_amount": "150.00",
            "lifetime_value": "450.00",
            "last_gift_date": "2026-01-10",
        },
        "last_contact_date": "2026-05-01",
        "facts": [],
    }
    payload.update(deepcopy(overrides))
    return payload


class SpyProvider:
    name = "spy-provider"

    def __init__(
        self,
        *,
        fail_on_calls: set[int] | None = None,
    ) -> None:
        self.requests: list[DraftRequest] = []
        self.fail_on_calls = fail_on_calls or set()

    def generate(self, request: DraftRequest) -> DraftCandidate:
        self.requests.append(request)
        if len(self.requests) in self.fail_on_calls:
            raise RuntimeError("provider failure with data that must not be returned")
        return TemplateProvider().generate(request)


class TransformProvider:
    name = "transform-provider"

    def __init__(self, transform: Any) -> None:
        self.transform = transform
        self.requests: list[DraftRequest] = []

    def generate(self, request: DraftRequest) -> DraftCandidate | dict[str, Any]:
        self.requests.append(request)
        candidate = TemplateProvider().generate(request)
        return self.transform(candidate, request)
