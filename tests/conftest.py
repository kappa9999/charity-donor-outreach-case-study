from __future__ import annotations

from typing import Any

import pytest

from charity_donor_outreach.models import CampaignBrief

from .factories import campaign_payload, donor_payload


@pytest.fixture
def valid_campaign_payload() -> dict[str, Any]:
    return campaign_payload()


@pytest.fixture
def valid_campaign(valid_campaign_payload: dict[str, Any]) -> CampaignBrief:
    return CampaignBrief.model_validate(valid_campaign_payload)


@pytest.fixture
def valid_donor_payload() -> dict[str, Any]:
    return donor_payload()
