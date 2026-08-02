# Case-study assessment

## Executive view

The supplied skill could produce polished fundraising copy, but its design placed policy, donor
data, calculations, style, and rendering inside one prompt. That is the wrong control boundary for
a sensitive workflow. A model can help draft language; it should not infer permission, invent
claims, calculate an ask, choose whether a major donor can be automated, or hide missing data
inside plausible prose.

The rewrite turns the artifact into a narrow workflow contract:

1. validate explicit donor and campaign inputs;
2. make consent, contact, ask, and review decisions deterministically;
3. disclose only the minimum approved context to a drafting provider;
4. reject output that violates machine-checkable invariants;
5. return a structured result for human review.

The requested outcomes—consistent, reliable, scalable drafts—come from explicit contracts,
deterministic decisions, failure isolation, and human review rather than additional prompt detail.

The received skill also embeds 50 mocked donor rows but omits consent, suppression, contact-path,
exact-date, campaign-control, and claim-approval fields. The implementation therefore preserves
the supplied table as a provenance-locked source fixture and demonstrates all 50 rows only after
adding separately documented synthetic test controls. This makes the sample executable without
misrepresenting inferred operational authority as received data.

## Improvements and impact

| Problem in the supplied artifact | Refinement | Operational impact |
| --- | --- | --- |
| Trigger scope covered almost any nonprofit communication | Limit activation to donor email/letter drafting from supplied records and a campaign brief | Reduces accidental invocation and unrelated data exposure |
| Donor histories were embedded in instructions | Keep all donor data outside the skill and require a versioned input contract | Removes stale/conflicting records and makes the skill reusable |
| Contact details could be repeated inside names or free-form facts | Block contact-like identity text and remove contact-like facts before constructing the provider request | Makes data minimization enforceable across content fields, not only declared columns |
| The donor identifier could be repeated inside a name, fact ID, or fact text | Require a 3-64 character identifier and token-screen every provider-bound identity/fact field | Keeps a structured internal identifier from crossing through an allowed prose field |
| The prompt allowed unsupported matches, benefits, counts, and opportunities | Permit only approved, provenance-labelled facts; quarantine unsupported claim categories | Reduces deceptive or reputationally harmful fundraising claims |
| Titles could be inferred from first names | Use only a supplied title and last name; otherwise use a neutral first-name salutation | Avoids demographic inference and misidentification |
| Missing fields could be guessed | Return invalid, blocked, or suppressed states with stable reason codes | Makes failures visible instead of converting them into hallucinations |
| Parser ambiguity could silently overwrite a safety field | Reject duplicate JSON members, non-finite/oversized values, invalid UTF-8/Unicode, and excessive nesting | Prevents parser behavior from changing consent or do-not-contact meaning while isolating a bad line |
| Invalid structures could amplify parser, fingerprint, or diagnostic work | Bound campaign/line bytes, direct-object depth/nodes/collections/content, facts, and emitted issues before nested validation | Makes batch isolation resilient to resource-amplification inputs, not only ordinary bad rows |
| Invalid-field diagnostics could echo PII-shaped unknown member names | Preserve only declared field names and numeric indexes; map unknown names to `$extra` | Keeps malformed or adversarial keys out of result logs |
| Consent and channel authority were absent | Require explicit granted/denied/unknown permission and a selected-channel contact path | Prevents unknown permission from silently becoming outreach |
| Superficial email checks accepted malformed or control-bearing values | Require a conservative ASCII dot-atom and DNS-label email contract in runtime and schema, while explicitly leaving deliverability upstream | Prevents malformed/disguised values from satisfying syntax without overstating mailbox verification |
| Ask amounts were calculated inside prompt instructions | Require canonical ASCII fixed-point strings and compute asks with Decimal, explicit bounds, and rounding | Makes asks repeatable, schema/runtime consistent, and independent of float, mixed-script, or model behavior |
| Giving history had no enforceable currency/date integrity | Require currency alignment and block future-dated gifts before ask calculation | Prevents mislabeled or temporally impossible asks |
| Arbitrary three-letter values could masquerade as currencies | Pin runtime and schemas to the active ISO 4217 List One snapshot | Rejects fictional/stale codes and keeps cross-version interpretation stable |
| High-value relationships were automated like routine records | Require review for configured segments and ask thresholds | Keeps relationship judgment with fundraising staff |
| HTML prose was the only output | Make JSON the system of record and plain-text copy one optional field | Supports integration, audit, retries, and review queues |
| No scale/failure-isolation model existed; one bad row could compromise a batch | Validate and process each bounded JSONL record independently, contain provider exceptions, and replace output only after a complete run | Supports growing lists without one record stopping the run or leaving partial output |
| A provider could mutate frozen nested objects via low-level Python access | Give it a detached deep copy while retaining separate guard, policy, result, and campaign authority | Prevents one adapter call from corrupting its ask or a later record |
| Stateful mappings and hostile scalar subclasses could change across reads | Snapshot bounded inputs/candidates once with base-type materialization before validation, hashing, or guard checks | Makes each security decision operate on one stable representation |
| No post-generation controls existed | Structurally anchor salutation, purpose, facts, ask, CTA, spacing, and sign-off; check provenance, claims, URLs/URIs, multi-currency money, Unicode text safety, NFKC/NFKD security views, and pressure | Withholds non-conforming drafts before staff can mistake them for approved output |
| Free text could expose consent controls or raw giving fields | Scan joined identity/fact views for policy aliases and donor-giving labels; reject the same residual labels after drafting | Keeps internal CRM authority and history outside the prose boundary |
| Unsafe fact IDs could reappear in audit/provenance | Close the identifier namespace and replace sensitive excluded IDs with one fixed sentinel | Prevents contact, policy, or donor data from leaking through metadata |
| Result fields could contradict their state | Enforce status-specific draft, provider, review, issue, and audit invariants in code and JSON Schema | Keeps downstream queues from acting on impossible envelopes |
| No reliable audit envelope existed | Record policy version, input hash, provider-call flag, exclusions, and reason codes | Supports debugging and monitoring without copying donor content into logs |
| Untyped hashing could collapse a numeric token and the same string | Type-tag every scalar/container before canonical hashing | Preserves audit correlation across invalid type variants |

## Rewritten skill

The deliverable is [charity-donor-outreach/SKILL.md](charity-donor-outreach/SKILL.md).
It is deliberately short and portable. Detailed contracts live one level deeper under
[references](charity-donor-outreach/references), and executable JSON Schemas live under
[assets](charity-donor-outreach/assets), following the Agent Skills progressive-disclosure model.

The Python package is supplementary evidence that the contract can be enforced. It is not a
hidden dependency of the skill.

## Product and architecture decisions

### Treat permission as three states

Unknown is not false and it is not granted. Denied permission and a do-not-contact flag suppress
the record. Unknown permission blocks it for resolution. Neither path calls the provider.

**Cost:** more records may require upstream cleanup.

**Why it is worth it:** a false positive creates unwanted donor contact; a false negative creates
review work but not harm.

### Keep policy outside the model

The model/provider receives a completed ask and a list of eligible facts. It cannot alter the ask
policy or override consent. The same policy executes with a template provider or a future model
adapter. A custom/model provider can never produce draft_ready in this implementation; a passing
candidate remains review_required.

**Cost:** policy changes require versioned code/configuration rather than prompt editing.

**Why it is worth it:** consequential decisions become deterministic, reviewable, and testable.

### Minimize the provider request

Contact details, donor ID, address, consent/suppression controls, and raw giving history stay
outside the drafting boundary. Only the chosen salutation, approved campaign content, computed
ask, and selected safe facts cross it. Joined identity views, fact text, and fact IDs are scanned
for exact structured contact reuse, obfuscated/encoded contacts, internal policy controls, raw
giving fields, and donor-identifier tokens before that request exists. Providers receive only a
detached deep copy of the minimized request.

**Cost:** the model has less context for creative personalization.

**Why it is worth it:** less context reduces privacy exposure and limits prompt-injection surface.

### Quarantine rather than repair silently

When a candidate alters a required paragraph or contains an unapproved fact ID, undeclared fact
paragraph, URL/URI scheme, contact detail, number, claim, ask, instruction, internal-policy label,
raw-giving label, HTML/control text, or pressure phrase, the returned draft is null and the result
is quality_rejected. The system does not silently rewrite the candidate and imply that it was
always compliant. Bounded lexical detectors are explicitly defense in depth, not a claim of
semantic completeness.

**Cost:** some drafts need a retry or human inspection.

**Why it is worth it:** the audit trail reflects what actually happened.

### Keep delivery outside the boundary

This repository creates drafts only. Even draft_ready means "passed automated checks," not
"approved to send."

**Cost:** an operational deployment needs a separate reviewed integration.

**Why it is worth it:** content generation cannot accidentally become autonomous donor contact.

## Evidence strategy

The project uses behavioral invariants rather than a self-authored aggregate score. Tests prove
specific properties, including zero provider calls on blocked records, deterministic ask
calculation, contact-frequency suppression, instruction-like fact removal, provider failure
isolation, structural draft quarantine, canonical-money/schema parity, closed output vocabulary,
maximum-length composition, and an end-to-end CLI run. The mapping is in
[docs/QUALITY.md](docs/QUALITY.md).

## Responsible-AI alignment

The design reflects the themes in JLL's public Responsible AI Statement: privacy and data
protection, transparency, security and safety, human oversight, monitoring, and documented
accountability. The risk structure also maps naturally to NIST AI RMF's Govern, Map, Measure, and
Manage functions. These references inform the control approach; they do not substitute for a
deployment-specific impact assessment or legal review.

## Why this demonstrates AI product leadership

The work is intentionally broader than prompt editing and narrower than a speculative platform.
It translates a stakeholder workflow into versioned inputs and outputs, separates product policy
from model behavior, exposes failure states to operators, preserves a replaceable provider seam,
and backs claims with adversarial tests. The decision table also records costs and reversal
triggers so another team can evolve the system without treating the first implementation as
permanent architecture.

## Deliberate non-goals

- No real donor data; the only donor table is the exercise's mocked sample, preserved with explicit
  provenance and separate synthetic operational controls
- No automatic email or postal delivery
- No demographic, wealth, propensity, or sensitive-trait inference
- No provider keys or vendor-specific SDK
- No claim that automated linting proves semantic truth
- No inflated original-versus-rewrite score
- No production legal determination of consent
- No mailbox or postal-address deliverability verification

## Production follow-ups

Before deployment, I would add the organization's consent system of record, role-based access,
retention/deletion controls, approved model/vendor adapter, encrypted audit storage, observability,
human-review UX, evaluation sets sampled from authorized data, incident response, and a formal AI
impact/privacy assessment. The present repository establishes the contract and seams those systems
would integrate with.
