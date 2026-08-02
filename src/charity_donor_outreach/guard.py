"""Post-generation checks that quarantine non-conforming drafts."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from .models import (
    DraftCandidate,
    DraftRequest,
    FactCategory,
    QualityCode,
    QualityIssue,
)
from .policy import (
    contains_contact_like_text,
    contains_giving_history_field_like_text,
    contains_instruction_like_text,
    contains_policy_control_like_text,
    contains_provider_solicitation_language,
    contains_solicitation_language,
    format_ask_paragraph,
    format_money,
    money_expressions,
    security_view,
    security_views,
    word_number_expressions,
)

_URL_PATTERN = re.compile(
    r"(?<![a-z0-9+.-])[a-z][a-z0-9+.-]{0,31}:[^\s<>()]+"
    r"|www\.[^\s<>()]+"
    r"|(?<![@\w])(?:[a-z0-9](?:[a-z0-9-]{0,62})\.)+"
    r"[a-z]{2,63}(?:/[^\s<>()]*)?",
    re.IGNORECASE,
)
_HTML_PATTERN = re.compile(r"<[^>]+>")
_PRESSURE_PATTERNS = (
    re.compile(r"\bevery (?:second|minute|hour) counts\b", re.IGNORECASE),
    re.compile(r"\byou (?:must|need to) (?:give|donate)\b", re.IGNORECASE),
    re.compile(r"\bonly you can\b", re.IGNORECASE),
    re.compile(r"\bdon't let (?:us|them|the animals) down\b", re.IGNORECASE),
    re.compile(r"\bact now\b", re.IGNORECASE),
    re.compile(r"\btime (?:is )?running out\b", re.IGNORECASE),
    re.compile(r"\b(?:this is )?(?:your|our|the) last chance\b", re.IGNORECASE),
    re.compile(r"\b(?:do not|don't|cannot|can't|can not|must not)\s+wait\b", re.IGNORECASE),
    re.compile(
        r"\burgent\b[^.!?\n]{0,24}\b(?:they|we|animals?|families?|people)\s+need\s+you\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:they|we|animals?|families?|people)\s+(?:are\s+)?counting on you\b",
        re.IGNORECASE,
    ),
)
_CLAIM_PATTERNS = {
    FactCategory.MATCHING_GIFT: re.compile(
        r"\b(?:matching\s+gifts?|(?:match|matched|matching)\b[^.!?\n]{0,40}\b"
        r"(?:gifts?|donations?|contributions?|funds?)|"
        r"(?:gifts?|donations?|contributions?|funds?)\b[^.!?\n]{0,40}\b"
        r"(?:match|matched|matching))\b",
        re.IGNORECASE,
    ),
    FactCategory.NAMING_OPPORTUNITY: re.compile(
        r"\bnaming opportunity\b",
        re.IGNORECASE,
    ),
    FactCategory.INCENTIVE: re.compile(
        r"\b(?:free gift|tote bag|reward|incentive)\b",
        re.IGNORECASE,
    ),
    FactCategory.EVENT: re.compile(
        r"\b(?:already registered|people registered|attendees registered)\b",
        re.IGNORECASE,
    ),
    FactCategory.IMPACT: re.compile(
        r"\b(?:save|saves|saved|saving|change|changes|changed|changing|"
        r"transform|transforms|transformed|transforming)\b[^.!?\n]{0,40}"
        r"\b(?:lives?|animals?|people|families|communities)\b",
        re.IGNORECASE,
    ),
}


def _normalized_urls(values: Iterable[str]) -> set[str]:
    return {
        match.group(0).rstrip(".,;:!?")
        for value in values
        for view in security_views(value)
        for match in _URL_PATTERN.finditer(view)
    }


def _contains_normalized_literal(text: str, literal: str) -> bool:
    """Find a full normalized literal without matching it inside a longer token."""

    def is_token_character(character: str) -> bool:
        return character.isalnum() or character == "_"

    for normalized_text in security_views(text):
        view = normalized_text.casefold()
        for normalized_literal in security_views(literal):
            needle = normalized_literal.casefold()
            if not needle:
                continue
            offset = 0
            while (index := view.find(needle, offset)) >= 0:
                end = index + len(needle)
                left_is_clear = (
                    not is_token_character(needle[0])
                    or index == 0
                    or not is_token_character(view[index - 1])
                )
                right_is_clear = (
                    not is_token_character(needle[-1])
                    or end == len(view)
                    or not is_token_character(view[end])
                )
                if left_is_clear and right_is_clear:
                    return True
                offset = index + 1
    return False


class DraftGuard:
    """Enforce claims, provenance, ask, URL, and formatting invariants."""

    def inspect(
        self,
        candidate: DraftCandidate,
        request: DraftRequest,
        prohibited_phrases: Sequence[str],
    ) -> list[QualityIssue]:
        issues: list[QualityIssue] = []
        combined_text = "\n".join(part for part in (candidate.subject_line, candidate.body) if part)
        combined_security_text = security_view(combined_text)
        combined_security_views = security_views(combined_text)
        paragraphs = candidate.body.split("\n\n")
        allowed_fact_ids = {fact.fact_id for fact in request.facts}
        used_fact_ids = set(candidate.fact_ids_used)

        if len(candidate.fact_ids_used) != len(used_fact_ids):
            issues.append(
                QualityIssue(
                    code=QualityCode.DUPLICATE_FACT_REFERENCE,
                    message="fact_ids_used must not contain duplicates",
                )
            )
        if not used_fact_ids.issubset(allowed_fact_ids):
            issues.append(
                QualityIssue(
                    code=QualityCode.UNAPPROVED_FACT_REFERENCE,
                    message="draft references a fact that was not provided to the provider",
                )
            )

        authorized_indices: set[int] = set()
        if (
            candidate.salutation != request.salutation
            or not paragraphs
            or paragraphs[0] != request.salutation
            or paragraphs.count(request.salutation) != 1
        ):
            issues.append(
                QualityIssue(
                    code=QualityCode.SALUTATION_MISMATCH,
                    message="draft must open with one exact supplied salutation paragraph",
                )
            )
        else:
            authorized_indices.add(0)

        purpose_indices = [
            index for index, paragraph in enumerate(paragraphs) if paragraph == request.purpose
        ]
        if len(purpose_indices) != 1:
            issues.append(
                QualityIssue(
                    code=QualityCode.CAMPAIGN_PURPOSE_MISMATCH,
                    message="draft must contain the approved campaign purpose exactly once",
                )
            )
        else:
            authorized_indices.add(purpose_indices[0])

        expected_intro = f"Thank you for being part of {request.organization_name}'s community."
        intro_indices = [
            index for index, paragraph in enumerate(paragraphs) if paragraph == expected_intro
        ]
        if len(intro_indices) == 1:
            authorized_indices.add(intro_indices[0])

        if request.channel.value == "email" and candidate.subject_line is None:
            issues.append(
                QualityIssue(
                    code=QualityCode.MISSING_SUBJECT,
                    message="email drafts require a subject line",
                )
            )
        if request.channel.value == "letter" and candidate.subject_line is not None:
            issues.append(
                QualityIssue(
                    code=QualityCode.UNEXPECTED_SUBJECT,
                    message="letter drafts must not invent an email subject line",
                )
            )

        expected_signoff = f"With gratitude,\n{request.sender.name}\n{request.sender.role}"
        signoff_indices = [
            index for index, paragraph in enumerate(paragraphs) if paragraph == expected_signoff
        ]
        if request.sender.name not in candidate.body or request.sender.role not in candidate.body:
            issues.append(
                QualityIssue(
                    code=QualityCode.MISSING_SENDER,
                    message="draft must include the approved sender name and role",
                )
            )
        if len(signoff_indices) != 1 or signoff_indices[0] != len(paragraphs) - 1:
            issues.append(
                QualityIssue(
                    code=QualityCode.SENDER_SIGNOFF_MISMATCH,
                    message="draft must end with the exact approved sender sign-off block",
                )
            )
        else:
            authorized_indices.add(signoff_indices[0])

        if (
            any(not paragraph.strip() for paragraph in paragraphs)
            or candidate.body.startswith("\n")
            or candidate.body.endswith("\n")
            or re.search(r"\n{3,}", candidate.body) is not None
            or any(line and not line.strip() for line in candidate.body.split("\n"))
            or any("\n" in paragraph and paragraph != expected_signoff for paragraph in paragraphs)
        ):
            issues.append(
                QualityIssue(
                    code=QualityCode.BODY_STRUCTURE_INVALID,
                    message="draft body must use non-empty paragraphs separated by one blank line",
                )
            )

        if _HTML_PATTERN.search(combined_security_text):
            issues.append(
                QualityIssue(
                    code=QualityCode.HTML_NOT_ALLOWED,
                    message="the structured draft contract requires plain text",
                )
            )

        expected_ask_paragraph = format_ask_paragraph(request.campaign_name, request.ask)
        ask_indices = [
            index
            for index, paragraph in enumerate(paragraphs)
            if paragraph == expected_ask_paragraph
        ]
        if len(ask_indices) != 1 or combined_text.count(expected_ask_paragraph) != 1:
            issues.append(
                QualityIssue(
                    code=QualityCode.ASK_COPY_MISMATCH,
                    message="draft must contain one standalone policy-owned ask paragraph",
                )
            )
        else:
            authorized_indices.add(ask_indices[0])

        expected_cta = f"{request.call_to_action.label}: {request.call_to_action.url}"
        cta_indices = [
            index for index, paragraph in enumerate(paragraphs) if paragraph == expected_cta
        ]
        if request.call_to_action.url not in candidate.body:
            issues.append(
                QualityIssue(
                    code=QualityCode.MISSING_CALL_TO_ACTION_URL,
                    message="draft must include the approved call-to-action URL",
                )
            )
        if (
            len(cta_indices) != 1
            or combined_text.count(expected_cta) != 1
            or combined_text.count(request.call_to_action.url) != 1
        ):
            issues.append(
                QualityIssue(
                    code=QualityCode.CALL_TO_ACTION_MISMATCH,
                    message="draft must contain one exact approved call-to-action paragraph",
                )
            )
        else:
            authorized_indices.add(cta_indices[0])

        fact_candidate_indices = set(range(len(paragraphs))) - authorized_indices
        used_facts = [fact for fact in request.facts if fact.fact_id in used_fact_ids]
        unused_reference = False
        for fact in used_facts:
            matching_indices = {
                index for index in fact_candidate_indices if paragraphs[index] == fact.text
            }
            if len(matching_indices) != 1:
                unused_reference = True
            else:
                authorized_indices.update(matching_indices)
        if unused_reference:
            issues.append(
                QualityIssue(
                    code=QualityCode.UNUSED_FACT_REFERENCE,
                    message="each declared fact ID must map to one exact standalone fact paragraph",
                )
            )
        expected_subject = f"Support {request.campaign_name}"
        subject_residual = (
            [candidate.subject_line]
            if candidate.subject_line is not None and candidate.subject_line != expected_subject
            else []
        )
        residual_parts = [
            *subject_residual,
            *(
                paragraph
                for index, paragraph in enumerate(paragraphs)
                if index not in authorized_indices
            ),
        ]
        residual_text = "\n".join(residual_parts)
        residual_security_text = security_view(residual_text)
        residual_security_views = security_views(residual_text)

        if any(_contains_normalized_literal(residual_text, fact.text) for fact in request.facts):
            issues.append(
                QualityIssue(
                    code=QualityCode.UNDECLARED_FACT_USAGE,
                    message=(
                        "draft includes eligible fact text outside one declared standalone "
                        "paragraph"
                    ),
                )
            )

        residual_structural_literals = (
            (
                request.salutation,
                QualityCode.SALUTATION_MISMATCH,
                "draft repeats the supplied salutation outside its approved paragraph",
            ),
            (
                request.purpose,
                QualityCode.CAMPAIGN_PURPOSE_MISMATCH,
                "draft repeats campaign purpose outside its approved paragraph",
            ),
            (
                expected_ask_paragraph,
                QualityCode.ASK_COPY_MISMATCH,
                "draft repeats policy-owned ask copy outside its approved paragraph",
            ),
            (
                expected_cta,
                QualityCode.CALL_TO_ACTION_MISMATCH,
                "draft repeats call-to-action copy outside its approved paragraph",
            ),
            (
                expected_signoff,
                QualityCode.SENDER_SIGNOFF_MISMATCH,
                "draft repeats sender sign-off outside its approved terminal paragraph",
            ),
        )
        existing_codes = {issue.code for issue in issues}
        for literal, code, message in residual_structural_literals:
            if code not in existing_codes and _contains_normalized_literal(
                residual_security_text, literal
            ):
                issues.append(QualityIssue(code=code, message=message))
                existing_codes.add(code)

        grounded_values = [
            request.organization_name,
            request.campaign_name,
            request.purpose,
            request.sender.name,
            request.sender.role,
            request.call_to_action.label,
            request.call_to_action.url,
            *(fact.text for fact in used_facts),
        ]
        if request.ask is not None:
            grounded_values.append(format_money(request.ask))

        draft_urls = _normalized_urls([combined_text])
        grounded_urls = _normalized_urls(grounded_values)
        if not draft_urls.issubset(grounded_urls):
            issues.append(
                QualityIssue(
                    code=QualityCode.UNAPPROVED_URL,
                    message="draft contains a URL that is not grounded in approved input",
                )
            )

        if contains_contact_like_text(residual_text):
            issues.append(
                QualityIssue(
                    code=QualityCode.UNAPPROVED_CONTACT_DETAIL,
                    message="provider-controlled prose contains a contact detail",
                )
            )

        if contains_instruction_like_text(residual_text):
            issues.append(
                QualityIssue(
                    code=QualityCode.INSTRUCTION_LIKE_OUTPUT,
                    message="provider-controlled prose contains model or drafting instructions",
                )
            )

        if contains_policy_control_like_text(
            residual_text
        ) or contains_giving_history_field_like_text(residual_text):
            issues.append(
                QualityIssue(
                    code=QualityCode.UNAUTHORIZED_POLICY_CONTROL,
                    message="provider-controlled prose contains policy-control language",
                )
            )

        residual_numeric_views = (residual_text, *residual_security_views)
        if any(
            character.isnumeric()
            for numeric_view in residual_numeric_views
            for character in numeric_view
        ) or word_number_expressions(residual_text):
            issues.append(
                QualityIssue(
                    code=QualityCode.UNGROUNDED_NUMBER,
                    message="provider-controlled prose contains a numeric or word-number claim",
                )
            )

        if money_expressions(residual_text):
            issues.append(
                QualityIssue(
                    code=QualityCode.UNAUTHORIZED_ASK_AMOUNT,
                    message="provider-controlled prose contains a monetary expression",
                )
            )

        if contains_provider_solicitation_language(residual_text) or any(
            contains_solicitation_language(fact.text) or money_expressions(fact.text)
            for fact in used_facts
        ):
            issues.append(
                QualityIssue(
                    code=QualityCode.UNAUTHORIZED_ASK_LANGUAGE,
                    message="provider-controlled prose contains solicitation language",
                )
            )

        if request.ask is not None and format_money(request.ask) not in candidate.body:
            issues.append(
                QualityIssue(
                    code=QualityCode.ASK_AMOUNT_MISMATCH,
                    message="draft must use the deterministic ask amount exactly",
                )
            )

        for phrase in prohibited_phrases:
            if any(
                phrase_view.casefold() in combined_view.casefold()
                for phrase_view in security_views(phrase)
                for combined_view in combined_security_views
            ):
                issues.append(
                    QualityIssue(
                        code=QualityCode.CAMPAIGN_PROHIBITED_PHRASE,
                        message="draft contains a campaign-prohibited phrase",
                    )
                )
                break
        if any(
            pattern.search(view)
            for view in combined_security_views
            for pattern in _PRESSURE_PATTERNS
        ):
            issues.append(
                QualityIssue(
                    code=QualityCode.MANIPULATIVE_PRESSURE,
                    message="draft contains pressure language prohibited by the base policy",
                )
            )

        for category, pattern in _CLAIM_PATTERNS.items():
            if any(pattern.search(view) for view in residual_security_views):
                issues.append(
                    QualityIssue(
                        code=QualityCode(f"unsupported_{category.value}"),
                        message=(
                            f"draft makes a {category.value} claim without a used approved fact"
                        ),
                    )
                )

        return issues
