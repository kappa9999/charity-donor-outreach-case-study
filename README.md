# Charity Donor Outreach

[![CI](https://github.com/kappa9999/charity-donor-outreach-case-study/actions/workflows/ci.yml/badge.svg)](https://github.com/kappa9999/charity-donor-outreach-case-study/actions/workflows/ci.yml)

A portable Agent Skill and production-oriented Python reference implementation for creating
consent-aware, claim-grounded, human-reviewable donor outreach drafts.

The design treats fluent copy as the final step of a controlled workflow. Consent, contact
eligibility, ask amounts, approved facts, review gates, and output validation are enforced
outside the drafting provider. The project never sends a message.

All examples are synthetic. No real donor data, credentials, or organization claims are included.
This repository is the complete public submission and can be reviewed directly in GitHub; no
download or local HTML viewer is required.

## Reviewer path

1. Read [the case-study assessment](ASSESSMENT.md) for the issues, decisions, and expected impact.
2. Inspect the rewritten [Agent Skill](charity-donor-outreach/SKILL.md).
3. Review the [runtime architecture](docs/ARCHITECTURE.md) and trust boundaries.
4. Inspect the [behavioral evidence](docs/QUALITY.md) and tests.
5. Run the deterministic example below.

## Direct response to the brief

The supplied scenario names the ASPCA. The rewritten skill is intentionally
organization-portable: approved organization and campaign facts are structured inputs rather
than hard-coded claims. The synthetic examples demonstrate the workflow without inventing or
publishing statements about the real charity.

| Requested outcome | Direct answer |
| --- | --- |
| Assess the supplied skill | [ASSESSMENT.md](ASSESSMENT.md) identifies the control, data, quality, and operating-model gaps. |
| Explain improvements, rationale, and impact | The assessment's problem/refinement/impact matrix and decision sections make each tradeoff explicit. |
| Rewrite the skill | [charity-donor-outreach/SKILL.md](charity-donor-outreach/SKILL.md) is the portable rewrite, supported by focused references and executable schemas. |
| Produce consistent, reliable outputs at growing-list scale | Deterministic policy and output guards provide consistency; strict contracts, review states, and adversarial tests provide reliability; per-record JSONL isolation, atomic writes, resource ceilings, and a replaceable provider seam provide scale. |

The assessment and rewritten skill are the two requested deliverables. The Python package,
architecture, schemas, synthetic examples, and quality evidence are supplementary proof that the
design is implementable and testable, not hidden requirements for using the portable skill.

## Safety properties

- **Fail closed:** denied or unknown permission, missing contact data, ambiguous JSON, invalid
  inputs, future-dated ask history, currency mismatch, and contact-frequency conflicts produce no
  provider call and no draft. Dates use exact YYYY-MM-DD strings; financial JSON uses canonical
  ASCII-digit two-decimal strings and an active ISO 4217 List One code, never floats, fictional
  currency codes, or mixed-script digits. Email contactability uses
  a conservative ASCII dot-atom and DNS-label contract. Contact fields are presence/syntax checks,
  not claims that a mailbox or postal address is deliverable.
- **Policy before prose:** the provider cannot decide consent, eligibility, ask amount, or
  whether review is required.
- **Data minimization:** email addresses, postal addresses, donor IDs, consent/suppression fields,
  internal policy controls, and raw giving history are not included in the provider request.
  Joined identity views, fact IDs, and fact text are checked for exact structured contact reuse,
  literal/defanged/encoded contact details, IP addresses, URI schemes, international addresses,
  word/vanity phones, policy controls, and giving-history labels. Bounded component grammars also
  close reordered, non-adjacent field splits that reconstruct those protected forms, control
  language, solicitation, or currency-plus-amount content. Sensitive excluded fact IDs are
  represented in audit only by `redacted.sensitive-fact-id`.
- **Grounded claims:** only explicitly approved, provenance-labelled facts with a closed
  `campaign|organization|donor|crm` identifier namespace reach the provider. Instruction-like,
  policy-control-like, raw-giving, solicitation-like, monetary, and contact-like fact text is
  removed; unsafe identity text is blocked before request construction.
- **Draft quarantine:** the salutation, purpose, fact paragraphs, ask, call-to-action, and terminal
  sign-off are structurally checked. Unsupported URLs/URI schemes, numbers, claims, HTML,
  pressure, extra solicitation/instruction copy, and internal policy/giving labels cause the
  entire candidate to be withheld. Unicode control/format characters, default-ignorable marks,
  Unicode 14 unassigned/private-use code points, and malformed blank-line structure are rejected
  at the contract boundary. Security scans use both an NFKC view and a mark-stripped NFKD skeleton
  to expose bounded compatibility/combining-mark disguises while retaining approved display text.
- **Human control:** major/principal relationships, high-value asks, and every non-built-in
  provider output are review-required. Delivery is intentionally out of scope.
- **Batch isolation:** one invalid record or provider failure does not stop the remaining records;
  results stream through a temporary file and replace the destination only after a complete run.
  Campaign files and individual donor lines are capped at 1 MiB; direct object graphs, nested
  collections, facts, provider-returned mappings, and emitted diagnostics have deterministic
  resource ceilings. Direct mappings and provider candidates are snapshotted once into detached
  plain structures before security decisions.
- **Auditability:** every result records a policy version, input fingerprint, provider-call flag,
  excluded fact IDs, and enum-constrained reason codes without logging raw donor data. Malformed
  lines are fingerprinted from a digest of their raw bytes, not their contents. Sensitive fact IDs
  use a fixed redaction sentinel; unknown input member names become `$extra` instead of being
  echoed in diagnostics.
  Fingerprints use type-tagged canonical encoding, keeping numeric tokens distinct from strings;
  oversized direct objects use a bounded rejection summary.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m charity_donor_outreach generate \
  --campaign examples/campaign.json \
  --donors examples/donors.jsonl \
  --output out/results.jsonl
```

The command prints only aggregate counts. Drafts and structured decisions are written atomically
to the requested JSONL path. The CLI rejects an output path that aliases either input.

Expected example dispositions:

| Donor | Result | Why |
| --- | --- | --- |
| SYN-001 | draft_ready | Explicit permission and complete inputs |
| SYN-002 | blocked | Permission is unknown |
| SYN-003 | suppressed | Do-not-contact flag is set |
| SYN-004 | review_required | Relationship-managed segment and high-value ask |
| SYN-005 | draft_ready | Complete postal-letter path |
| SYN-006 | suppressed | Contact-frequency limit |

## Result states

| State | Provider called | Draft returned | Intended action |
| --- | :---: | :---: | --- |
| invalid | No | No | Correct the source record |
| blocked | No | No | Resolve missing or ambiguous authority/data |
| suppressed | No | No | Do not create outreach |
| provider_error | Yes | No | Retry or investigate the provider |
| quality_rejected | Yes | No | Review the quarantined failure reason |
| review_required | Yes | Yes | Human approval required |
| draft_ready | Yes | Yes | Continue through the organization's approval process |

"Draft ready" is not "approved to send." This repository has no delivery adapter.

## Repository structure

```text
charity-donor-outreach/        Portable skill, references, and JSON Schemas
src/charity_donor_outreach/    Executable policy and provider boundaries
tests/                         Behavioral, adversarial, contract, and CLI tests
examples/                      Synthetic campaign, donor records, and generated output
docs/                          Architecture, decisions, evidence, and limitations
scripts/                       Skill checks and pinned-metadata refresh utilities
.github/workflows/ci.yml       Windows and Linux quality gate
```

## Design scope

The included template provider makes local runs and CI deterministic. A production model adapter
can implement the small DraftProvider protocol, but it receives only the minimized DraftRequest
as structured data and remains subject to the same output guard. Output from any provider other
than the exact built-in deterministic implementation is always review_required, even when every
automated check passes. Adapters must preserve role separation between instructions and untrusted
field values. Vendor credentials, automatic delivery, prospect scoring, legal consent
determination, and real donor data are intentionally excluded.

The committed schemas declare JSON Schema Draft 2020-12. Money and multiplier inputs use bounded
canonical ASCII decimal strings, currencies use the pinned active ISO 4217 List One snapshot, and
dates use exact valid YYYY-MM-DD strings, so schema consumers and the runtime interpret the same
lexical values. The Pydantic runtime remains authoritative for ordered cross-field comparisons
that standard JSON Schema cannot express.

Cross-field phone reconstruction uses a pinned Google libphonenumber metadata snapshot for
assigned E.164 calling codes and possible national-number lengths. That keeps uncued numeric
fragment checks reproducible while rejecting implausible combinations of unrelated years and
counts; explicit phone labels and cues remain fail-closed independently of the snapshot.

This is a reference control plane, not a claim that generic automated checks prove every sentence
factually correct. Production rollout would also require organization-specific legal/privacy
review, data-retention controls, access controls, provider contracts, monitoring, and calibrated
human evaluation.

## Standards and context

- [Agent Skills specification](https://agentskills.io/specification)
- [JLL Responsible AI Statement](https://www.jll.com/en-ca/responsible-ai-statement)
- [NIST AI Risk Management Framework Core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/)
- [SIX ISO 4217 current currency lists](https://www.six-group.com/en/products-services/financial-information/market-reference-data/data-standards.html)
- [ITU-T E.164 international numbering plan](https://www.itu.int/rec/T-REC-E.164/en)
- [Google libphonenumber metadata source](https://github.com/google/libphonenumber/blob/99ade73f8465edd4a71969c8899bc45a854ed100/resources/PhoneNumberMetadata.xml)
- [ASPCA Privacy Policy](https://www.aspca.org/about-us/privacy-policy)
- [AFP Donor Bill of Rights](https://afpglobal.org/donor-bill-rights)

## License

MIT. See [LICENSE](LICENSE).
