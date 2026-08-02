# Policy and review rules

## Disposition precedence

1. Explicit do-not-contact or denied permission: suppressed.
2. Unknown permission or incomplete/ambiguous authority: blocked.
3. Contact-frequency conflict: suppressed.
4. Complete and eligible, but relationship/ask risk: review_required after drafting.
5. Complete and eligible without configured review risk: draft_ready after quality checks only
   for the exact built-in deterministic template; otherwise review_required.

Suppressed and blocked records never enter drafting.

## Ask policy

For strategy none, do not introduce a monetary ask.

For last_gift_multiplier:

1. Require last gift amount, date, and currency.
2. Block a future gift date or currency different from the ask policy.
3. Multiply the amount with Decimal arithmetic.
4. Round half-up to the configured increment.
5. Apply the configured minimum and maximum.
6. Require review at or above the configured threshold.
7. Pass the final amount to drafting as immutable input.

The policy-owned paragraph is exact:

- With an ask: `Would you consider a gift of [computed amount] to support [campaign name]?`
- Without an ask: `Please consider supporting [campaign name] in a way that is right for you.`

The output guard anchors this paragraph as one exact standalone body paragraph and rejects repeats,
embedding, digit- or word-form money, and a bounded lexicon of common solicitation paraphrases in
all provider-controlled spans. Instruction, internal-policy/giving, solicitation, money,
URL/URI, Unicode numeric, claim, pressure, and prohibited-phrase comparisons operate on NFKC and
mark-stripped NFKD security views while preserving original approved display text. This lexical
scan is defense in depth, not semantic proof. Output from every custom/model provider remains
review_required even if it passes.

Money detection uses a dated snapshot of current ISO 4217 List One uppercase codes, a
case-insensitive allowlist of common unambiguous codes, Unicode currency symbols, bounded
punctuation/connectors, digits and number words, and currency unit names in both orders. Ambiguous
unit homographs require a national or monetary cue. The named-unit lexicon is versioned policy,
not a claim to recognize every colloquial denomination.

Do not add uplifts for inferred loyalty, urgency, volunteer status, wealth, or campaign type.

## Salutation

- When both title and last name are supplied, use "Dear [title] [last name],".
- Otherwise use "Hi [first name],".
- Never infer a title or gender.

## Fact handling

- Keep fact IDs unique across donor and campaign inputs.
- Require each fact ID namespace to agree with its source; CRM may use `crm.` or `donor.`, while
  campaign and organization facts use their matching namespaces.
- Exclude unapproved facts.
- Treat instruction-like phrases as data attacks even when approval metadata is wrong.
- Exclude internal consent/suppression/policy-control aliases and raw donor-giving fields or
  donor-subject giving-history prose.
- Exclude solicitation-like and monetary fact text so facts cannot override the ask policy.
- Exclude contact-like fact text so literal/spaced/defanged/encoded emails/domains, URI schemes,
  IP addresses, international/word/vanity phones, and postal-address patterns cannot be smuggled
  through a fact into the provider request.
- Exclude exact structured email/postal values even when too short for a generic contact grammar;
  use a fixed audit sentinel instead of echoing a sensitive fact ID.
- Exclude a fact when its ID or text contains the donor identifier as a token.
- Block instruction-like identity fields before building a salutation.
- Block contact-like identity fields before building a salutation.
- Block identity fields containing the donor identifier as a token.
- Require the provider to declare used fact IDs.
- Require each declared fact to appear once as an exact standalone body paragraph; reject an exact
  eligible fact paragraph with no declared ID.
- Require a corresponding used category for matching gifts, incentives, naming opportunities,
  and event-registration claims.
- Pass eligible facts as structured, delimited data rather than concatenating them into model
  instructions.

## Review gates

Require human review for:

- major or principal segments when configured;
- asks at or above the campaign threshold;
- approved facts removed as instruction-like;
- approved facts removed as solicitation-like or monetary;
- approved facts removed as contact-like;
- approved facts removed as policy-control-like or raw giving history;
- approved facts removed because their ID or text exposes the donor identifier;
- every non-built-in provider candidate, even after automated checks pass;
- any output quality failure;
- provider failure or contract violation;
- blocked input that needs authority or data resolution.

Suppression does not mean "review and send anyway." It means no draft.

## Prohibited content

Never invent or infer:

- matches or urgency;
- outcomes, counts, or impact statistics;
- gifts, benefits, rewards, or naming opportunities;
- donor motivations, relationships, or personal history;
- staff identities or contact links;
- titles, gender, wealth, health, religion, ethnicity, or other sensitive traits.

Avoid guilt, coercion, shame, and "only you can" language.
