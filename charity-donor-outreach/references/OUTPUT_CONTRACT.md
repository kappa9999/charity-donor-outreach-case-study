# Output contract

Return one structured result per input line.

## Required envelope

- record_index
- donor_id, or null when the input identifier is invalid
- campaign_id
- channel, or null before donor validation
- status
- review_required
- reason_codes
- validation_issues
- quality_issues
- draft, or null
- audit

## Draft

When present, draft contains:

- subject_line: required for email and null for letter;
- body: LF-normalized plain text without Unicode control or format characters other than LF;
- ask: immutable computed money object or null; amount serializes as a canonical two-decimal string;
- fact_ids_used: unique eligible identifiers from the closed
  `campaign|organization|donor|crm.slug` grammar; arbitrary provenance strings are invalid.

The service requires one exact opening salutation, campaign-purpose paragraph, policy-owned ask
paragraph, call-to-action paragraph, and terminal sender sign-off. Each declared fact maps to one
exact standalone fact paragraph. Paragraphs use exactly one blank line and the body has no leading
or trailing LF.

Never return a candidate draft when status is invalid, blocked, suppressed, provider_error, or
quality_rejected.

## Audit

Include:

- policy_version;
- evaluated_on, as an exact valid YYYY-MM-DD date;
- SHA-256 input_fingerprint over normalized valid input plus campaign, or over the raw-line digest
  plus campaign for malformed input. Structurally oversized direct-API mappings use a bounded,
  non-sensitive rejection summary instead of traversing attacker-amplified content;
- provider_name only when called;
- provider_called;
- excluded_fact_ids, containing only safe namespaced identifiers or the fixed
  `redacted.sensitive-fact-id` sentinel.

Do not include email address, postal address, raw giving history, fact text, provider prompt, model
credentials, or provider exception text in audit output. Validation paths preserve only declared
contract field names and numeric indexes; attacker-controlled unknown member names are reported as
the fixed `$extra` token.

Provider-returned mappings are bounded and snapshotted once into detached base Python types before
candidate validation. Unreadable, oversized, aliased, cyclic, or contract-invalid candidates are
contained as provider_error and are never partially exposed.

## Stable state semantics

| State | Meaning |
| --- | --- |
| invalid | Input does not satisfy the declared schema |
| blocked | Authority or required data is unknown/ambiguous |
| suppressed | Explicit instruction or cadence says not to create outreach |
| provider_error | Draft provider failed or violated its return contract |
| quality_rejected | Candidate failed deterministic checks and is withheld |
| review_required | Candidate passed checks but policy or non-built-in-provider review is mandatory |
| draft_ready | Built-in deterministic candidate passed checks and no policy review gate was triggered |

Draft ready never grants delivery authority.

## Canonical reason codes

These exact values are stable machine-readable policy outcomes:

- `invalid_donor_record`
- `do_not_contact`
- `consent_denied`
- `consent_unknown`
- `missing_email`
- `missing_postal_address`
- `duplicate_fact_id_across_inputs`
- `instruction_like_identity_field`
- `policy_control_like_identity_field`
- `giving_history_like_identity_field`
- `solicitation_like_identity_field`
- `contact_like_identity_field`
- `donor_identifier_in_identity_field`
- `last_contact_date_in_future`
- `last_gift_date_in_future`
- `giving_currency_mismatch`
- `missing_ask_basis`
- `contact_frequency_limit`
- `relationship_managed_segment`
- `high_value_ask`
- `instruction_like_fact_excluded`
- `solicitation_like_fact_excluded`
- `monetary_fact_excluded`
- `contact_like_fact_excluded`
- `donor_identifier_fact_excluded`
- `policy_control_like_fact_excluded`
- `giving_history_fact_excluded`
- `unverified_provider_requires_review`
- `draft_request_invalid`
- `provider_generation_failed`
- `draft_failed_quality_gate`

Quality failures use these exact issue codes:

- `duplicate_fact_reference`
- `unapproved_fact_reference`
- `undeclared_fact_usage`
- `unused_fact_reference`
- `body_structure_invalid`
- `salutation_mismatch`
- `missing_subject`
- `unexpected_subject`
- `campaign_purpose_mismatch`
- `missing_sender`
- `sender_signoff_mismatch`
- `html_not_allowed`
- `missing_call_to_action_url`
- `call_to_action_mismatch`
- `unapproved_url`
- `unapproved_contact_detail`
- `ungrounded_number`
- `unauthorized_ask_amount`
- `unauthorized_ask_language`
- `instruction_like_output`
- `unauthorized_policy_control`
- `ask_copy_mismatch`
- `ask_amount_mismatch`
- `campaign_prohibited_phrase`
- `manipulative_pressure`
- `unsupported_matching_gift`
- `unsupported_naming_opportunity`
- `unsupported_incentive`
- `unsupported_event`
- `unsupported_impact`

Strict JSON ingestion may report these validation issue codes under the `invalid_donor_record`
reason: `invalid_json`, `duplicate_json_key`, `non_finite_json_number`,
`invalid_unicode_scalar`, `invalid_utf8`, `json_number_out_of_range`,
`json_nesting_too_deep`, `input_line_too_large`, and `object_required`. Direct Mapping preflight
may report `input_collection_too_large`, `input_nesting_too_deep`,
`input_structure_too_large`, `input_content_too_large`, `input_cycle_not_allowed`,
`input_shared_reference_not_allowed`, or `unreadable_input_mapping`. Runtime donor-field
validation may also report Pydantic's stable validation type for the rejected field. At most 25
validation issues are emitted; when more exist, the last item is
`validation_issues_truncated` and additional diagnostics are withheld.

Machine-readable schema: ../assets/outreach-result.schema.json. Conditional state rules are
encoded in the declared Draft 2020-12 schema and enforced again by the runtime model. Status reason
codes and quality issue codes are closed enums; record indexes and audit/review booleans are strict
scalar types rather than coercive values.

The quality guard treats generic URI-scheme forms, contact details, instructions, internal
policy/giving labels, and any residual Unicode numeric character as unauthorized provider prose.
Exact approved structural/fact paragraphs are removed from that residual view first.
