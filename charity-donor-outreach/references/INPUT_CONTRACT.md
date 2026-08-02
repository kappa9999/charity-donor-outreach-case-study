# Input contract

## Campaign brief

Require exactly one campaign object:

| Field | Rule |
| --- | --- |
| campaign_id | Stable non-secret identifier |
| organization_name / campaign_name | Approved display names |
| purpose | Approved single-line donor-facing campaign explanation |
| tone | warm, warm-professional, or formal |
| sender | Approved name and role; never fabricate |
| call_to_action | Approved label and canonical lowercase HTTPS URL on a valid ASCII DNS host; no credentials, port, query, fragment, percent encoding, or unsafe path characters |
| as_of_date | Fixed, valid Gregorian date in exact YYYY-MM-DD form; do not use implicit current time |
| minimum_days_between_contacts | Integer from zero through 365 |
| ask_policy | none, or last-gift multiplier with active ISO 4217 List One currency, rounding, floor, and ceiling; every decimal is a canonical string |
| review_policy | Relationship segments and ask threshold requiring review |
| facts | Provenance-labelled facts with explicit outreach approval |
| prohibited_phrases | Campaign-specific phrases that must not appear |

Campaign configuration is controlled data, not a place for model instructions. Control fields
cannot carry monetary expressions or policy-bypassing solicitation. The CTA label may describe
the ordinary action, but only the deterministic ask policy owns amount and ask copy.

## Donor record

Require one object per donor:

| Field | Rule |
| --- | --- |
| donor_id | Stable 3-64 character source-system identifier; never send to the drafting provider |
| first_name | Required |
| last_name / title | Optional; use together only when both are supplied |
| preferred_channel | email or letter |
| channel_consent | granted, denied, or unknown; never default |
| do_not_contact | Explicit boolean; never default |
| email | Required for selected email channel; conservative ASCII dot-atom syntax on a DNS-shaped domain; this does not prove mailbox deliverability; provider must not receive it |
| postal_address | Required nonempty structured fields for selected letter channel with a two-uppercase-letter country code shape; upstream systems remain responsible for address verification and deliverability; provider must not receive it |
| segment | general, mid_value, major, principal, or lapsed |
| giving | Currency plus explicit nullable values for last/largest/lifetime amounts and last-gift date; lifetime cannot be below any supplied individual gift |
| last_contact_date | Valid Gregorian date in exact YYYY-MM-DD form, or null |
| facts | At most 25 provenance-labelled facts |

Reject unknown fields. Do not ingest free-form CRM notes into drafting context.

## Incomplete lists and onboarding

An uploaded CSV or legacy list is not generation-ready merely because it contains names and gift
history. Before normalization, inventory which fields are source-provided, derived, absent, or
supplied as explicit test controls. If channel consent, do-not-contact state, selected-channel
contact data, exact policy dates, campaign configuration, or claim approval/provenance is absent:

1. preserve the source rows without rewriting their meaning;
2. return a readiness report with record count and missing authoritative fields;
3. do not call a drafting provider or create donor-facing copy;
4. request or join the missing values from the system that owns them; and
5. record any test-only augmentation separately from source field lineage.

Never map a missing consent value to granted, a missing suppression flag to false, or an imprecise
year to an exact production date. Synthetic controls and placeholder dates may be used only in a
clearly labelled, non-production demonstration where they cannot be confused with received data.
The executable reference consumes normalized JSON/JSONL after this onboarding boundary.

Parse UTF-8 JSON with duplicate-member rejection at every nesting level. Reject NaN, infinity,
oversized/excessive-exponent numbers, invalid Unicode scalar values, unsafe controls, and excessive
nesting rather than relying on parser-specific behavior. Text uses a pinned Unicode 14 baseline:
reject controls, formats, surrogates, line separators, default-ignorable code points, noncharacters,
and Unicode 14 unassigned/private-use ranges. LF is permitted only inside a draft body. This keeps
Python 3.11-3.14 acceptance stable even when their bundled Unicode databases differ. Isolate an
invalid UTF-8 donor line as an invalid result; do not expose its bytes.

Campaign files and individual donor JSONL lines are each limited to 1,048,576 bytes. Donor direct-
API mappings are limited to 64 container levels, 10,000 expanded nodes, 1,000 items in any generic
collection, 1,048,576 aggregate string/byte units, and 25 facts. Reused mutable-container
references and cycles are not valid JSON-like inputs. Campaign preflight enforces its 50-fact,
50-prohibited-phrase, five-review-segment, collection, and node ceilings before nested model
validation. A direct Mapping is materialized once into a bounded plain dict/list tree, rechecked,
then fingerprinted and validated from that same snapshot. These limits and the one-pass snapshot
bound parser, fingerprint, validation, and diagnostic work while closing stateful-Mapping read
inconsistency.

All money values are JSON strings in canonical fixed-point form, such as `"125.00"`. Require
exactly two ASCII fractional digits, no sign, no exponent, no leading zeroes, a positive value,
and at most ten ASCII integer digits. A multiplier is likewise an ASCII-digit string with exactly
two fractional digits in the inclusive range 0.01 through 5.00. JSON numeric tokens and
mixed-script digits are not valid financial inputs. Every currency field must equal one uppercase
code in the active ISO 4217 List One snapshot dated 2026-08-01; arbitrary three-letter strings are
not currency values.

## Fact object

Every fact contains:

- fact_id: unique stable `campaign|organization|donor|crm.slug` identifier whose namespace agrees
  with source (`campaign`, `organization`, or CRM using `donor`/`crm`);
- text: bounded plain text;
- source: CRM for donor facts; campaign or organization for campaign facts;
- category: donor history/preference, event, impact, incentive, matching gift, naming opportunity,
  or program;
- approved_for_outreach: explicit boolean.

Facts marked false never reach the provider. Facts marked true but containing instruction-like,
policy-control-like, raw-giving, solicitation-like, monetary, contact-like, exact structured
contact, or exact donor-identifier text are excluded and trigger review. Contact scans cover
literal/spaced/defanged/encoded emails and domains, generic URI schemes, Unicode domains, IPv4/6,
international/word/vanity phones, and cue-backed international postal forms. Sensitive fact IDs
are represented only by `redacted.sensitive-fact-id` in audit. Fact text is trimmed, single-line
plain text under the same Unicode 14 baseline. Security comparisons use an NFKC view and a
mark-stripped NFKD skeleton so bounded compatibility/combining-mark variants cannot disguise
instructions, internal controls, solicitation, money, contacts, URLs, numbers, claims, pressure,
or campaign-prohibited phrases; original approved display text is retained.
Monetary scans cover uppercase codes in the current ISO 4217 List One snapshot, a case-insensitive
allowlist of common unambiguous codes, Unicode currency symbols, digit/word ordering, bounded
punctuation/connectors, and a documented currency-name lexicon. Ambiguous English homographs such
as `won`, `real`, `mark`, and `pound` require a national or monetary cue. A canonical uppercase ISO
code remains a fail-closed currency marker even when it is also ordinary uppercase prose (for
example, `TOP 500`, `TRY 10`, `ALL 50`, or `SOS 100`); that bounded ambiguity triggers review
rather than risking an undisclosed monetary instruction.

## Source priority

1. Explicit do-not-contact and channel permission
2. Current campaign configuration
3. Current donor system record
4. Approved organization facts
5. No other source

Do not use model memory, web search, embedded examples, or prior drafts to fill donor-specific
fields or organization claims.

Machine-readable contracts are in ../assets. They provide structural and conditional preflight
validation. Use the executable Pydantic contract for ordered comparisons and policy rules that
standard JSON Schema cannot express.
