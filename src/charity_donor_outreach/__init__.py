"""Policy-gated donor outreach reference implementation."""

from .models import CampaignBrief, DonorRecord, OutreachResult
from .providers import DraftProvider, TemplateProvider
from .service import OutreachService

__all__ = [
    "CampaignBrief",
    "DonorRecord",
    "DraftProvider",
    "OutreachResult",
    "OutreachService",
    "TemplateProvider",
]

__version__ = "1.0.0"
