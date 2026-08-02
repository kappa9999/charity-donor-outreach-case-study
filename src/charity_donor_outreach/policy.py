"""Deterministic controls that execute before any drafting provider."""

from __future__ import annotations

import html
import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from urllib.parse import unquote

from ._iana_tlds import IANA_ROOT_ZONE_TLD_SET
from ._iso4217 import ISO_4217_ACTIVE_CODE_SET
from .models import (
    ApprovedFact,
    CampaignBrief,
    Channel,
    ConsentState,
    DonorRecord,
    FactSource,
    Money,
    MultiplierAskPolicy,
    PolicyDecision,
    PolicyDisposition,
    ReasonCode,
)

POLICY_VERSION = "2026-08-01.9"
_SECURITY_SENTENCE_BREAK = "\ue000"
_DECIMAL_CONTEXT = Context(prec=50, rounding=ROUND_HALF_UP)
_REDACTED_SENSITIVE_FACT_ID = "redacted.sensitive-fact-id"
_DRAFTING_INSTRUCTION_PREFIX = (
    r"(?:^|[.!?]\s+|\b(?:reminder|fact|instruction|note)\s*(?::|\u2014|-)\s*|"
    r"\bplease\s+(?:now\s+)?)"
)

_INSTRUCTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"\bignore (?:all )?(?:previous|prior|above) instructions\b",
        r"\b(?:ignore|disregard|forget|override|replace)\b[\s\S]{0,60}"
        r"\b(?:instructions?|directions?|rules?|prompts?|messages?)\b",
        r"\bsystem prompt\b",
        r"\bdeveloper message\b",
        r"\breveal (?:the )?(?:prompt|instructions)\b",
        r"\b(?:assistant|model|agent|system)\b[\s\S]{0,80}"
        r"\b(?:prompt|instructions?|directions?|rules?)\b",
        r"\b(?:reveal|print|output|return|disclose)\b[\s\S]{0,50}"
        r"\b(?:hidden|system|developer|prompt|instructions?|rules?)\b",
        r"\bbypass (?:review|policy|guardrails?)\b",
        r"\bsend (?:this )?(?:now|without review)\b",
        r"\b(?:send|email|deliver|publish|submit)\b[\s\S]{0,40}"
        r"\b(?:now|immediately|without (?:human )?(?:review|approval))\b",
        r"\bjailbreak\b",
        r"\bexfiltrat(?:e|ion)\b",
        rf"{_DRAFTING_INSTRUCTION_PREFIX}"
        r"(?:write|generate|include|omit|use|change|follow|render|rewrite|format|draft|treat)\b"
        r"[^.!?\n]{0,100}\b(?:email|draft|text|copy|verbatim|all caps|campaign purpose|"
        r"salutation|ask amount|directions?|instructions?|json|prompt|fact|message|response)\b",
        rf"{_DRAFTING_INSTRUCTION_PREFIX}(?:return|output)\b[^.!?\n]{{0,100}}",
    )
)
_POLICY_CONTROL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:do\s+not\s+contact|dnc)(?:\s+(?:flag|status|setting))?\b",
        r"\b(?:opt[\s-]?out|unsubscribe)(?:\s+(?:flag|status|setting))?\b",
        r"\b(?:email|marketing|contact)\s+permission\b",
        r"\bcontactable\b",
        r"\b(?:email\s+)?suppression(?:\s+(?:flag|status|setting))?\b",
        r"\bdo[\s-]+not[\s-]+(?:email|mail)\b",
        r"\b(?:channel\s+)?consent\b",
        r"\bpreferred\s+channel\b",
        r"\blast\s+contact\s+date\b",
        r"\b(?:donor\s+)?segment\b",
        r"\bpostal\s+address\b",
        r"\bminimum\s+days\s+between\s+contacts\b",
        r"\b(?:ask|review)\s+policy\b",
        r"\bask\s+amount\s+at\s+or\s+above\b",
        r"\b(?:rounding\s+increment|minimum\s+ask|maximum\s+ask)\b",
        r"\b(?:ask\s+multiplier\b|multiplier\s*(?::|=|\bis\b))",
        r"\b(?:review\s+required|policy\s+review|policy\s+(?:flag|status|setting)|"
        r"guard\s+(?:flag|status|setting))\b",
        r"\b(?:passed|cleared|approved\s+by)\s+(?:the\s+)?"
        r"(?:policy|guard|compliance)(?:\s+(?:review|checks?))?\b",
        r"\b(?:ignore|override|change|set)\b[^.!?\n]{0,40}\b"
        r"(?:dnc|consent|permission|preferred\s+channel|policy|guard|status|"
        r"suppress(?:ion)?|block(?:ing)?|review)\b",
    )
)
_GIVING_HISTORY_FIELD_PATTERN = re.compile(
    r"\b(?:(?:last|largest|previous|prior|most\s+recent)\s+"
    r"(?:gift|donation|contribution)(?:\s+(?:amount|date))?|"
    r"(?:gift|donation|contribution|giving)\s+(?:amount|currency|date|frequency)|"
    r"(?:total|annual|household)\s+(?:gifts?|giving|donations?|contributions?)|"
    r"consecutive\s+giving\s+years|lifetime\s+value|"
    r"(?:giving|donation)\s+history)\b",
    re.IGNORECASE,
)
_GIVING_HISTORY_CONTEXT_PATTERN = re.compile(
    r"(?:\b(?:they|the\s+donor|donor|supporter|member|he|she)\b[^.!?\n]{0,40}"
    r"\b(?:gift(?:ed|ing)?|gave|donat(?:e|ed)|contribut(?:e|ed)|pledg(?:e|ed))\b|"
    r"\btheir\s+(?:gift|giving|donation|contribution)\b|"
    r"\btheir\s+support\s+(?:amount|value|was|is)\b|"
    r"\bsupported\s+(?:us|our\s+organization)\s+(?:with|by)\s+"
    r"(?:a\s+gift|giving|donat(?:e|ing|ion)|contribut(?:e|ing|ion))\b)",
    re.IGNORECASE,
)
_CURRENCY_SYMBOL_CLASS = re.escape(
    "\u0024\u00a2\u00a3\u00a4\u00a5\u058f\u060b\u07fe\u07ff\u09f2\u09f3"
    "\u09fb\u0af1\u0bf9\u0e3f\u17db\u20a0\u20a1\u20a2\u20a3\u20a4\u20a5"
    "\u20a6\u20a7\u20a8\u20a9\u20aa\u20ab\u20ac\u20ad\u20ae\u20af\u20b0"
    "\u20b1\u20b2\u20b3\u20b4\u20b5\u20b6\u20b7\u20b8\u20b9\u20ba\u20bb"
    "\u20bc\u20bd\u20be\u20bf\u20c0\ua838\ufdfc\ufe69\uff04\uffe0\uffe1"
    "\uffe5\uffe6\U00011fdd\U00011fde\U00011fdf\U00011fe0\U0001e2ff"
    "\U0001ecb0"
)
_CURRENCY_NAMES = (
    r"(?:ariary|baht|balboa|birr|bolivares?|cedis?|cordobas?|dalasis?|dinars?|"
    r"dirhams?|dobras?|dollars?|escudos?|euros?|florins?|forints?|francs?|"
    r"gourdes?|guaranis?|guilders?|hryvnias?|korunas?|kronas?|kroner|kronor|"
    r"kwachas?|kwanzas?|laris?|leks?|lempiras?|lei|leus?|lilangeni|liras?|"
    r"lotis?|manats?|meticais|metical|nairas?|ngultrums?|ouguiyas?|paangas?|"
    r"patacas?|pesos?|pulas?|quetzales?|renminbi|rials?|riyals?|roubles?|rubles?|"
    r"rufiyaas?|rupees?|rupiahs?|shekels?|shillings?|somonis?|soms?|takas?|"
    r"talas?|tenges?|tugriks?|vatu|yuan|yen|zlotys?)"
)
_CURRENCY_HOMOGRAPHS_WITH_CUE = (
    r"(?:Armenian\s+drams?|Brazilian\s+(?:real|reais)|British\s+pounds?|"
    r"Costa\s+Rican\s+colones?|Deutsche\s+marks?|German\s+marks?|Lao\s+kips?|"
    r"North\s+Korean\s+won|Peruvian\s+soles?|pounds?\s+sterling|"
    r"Sierra\s+Leonean\s+leones?|South\s+African\s+rand|South\s+Korean\s+won|"
    r"Vietnamese\s+dong)"
)
_AMBIGUOUS_CURRENCY_AFTER_AMOUNT = (
    r"(?:colones?|dong|drams?|kips?|leones?|marks?|pounds?|rand|reals?|soles?|won)"
)
_CURRENCY_UNITS = (
    r"(?:(?:(?:American|Australian|Brazilian|British|Canadian|Chinese|Emirati|"
    r"Hong Kong|Indian|Japanese|Mexican|New Zealand|Russian|Saudi|Singapore|"
    rf"South African|South Korean|Swiss)\s+)?{_CURRENCY_NAMES}|"
    rf"{_CURRENCY_HOMOGRAPHS_WITH_CUE})"
)
# Snapshot of ISO 4217 List One maintained by SIX, retrieved 2026-08-01. Codes
# are intentionally matched case-sensitively: canonical codes are uppercase,
# while several valid codes (for example TOP and TRY) are ordinary English
# words when lowercased and would otherwise create unsafe false positives.
_ISO_CURRENCY_CODE = rf"(?-i:{'|'.join(sorted(ISO_4217_ACTIVE_CODE_SET))})"
_COMMON_CURRENCY_CODE = r"(?i:AED|AUD|BRL|CAD|CHF|CNY|EUR|GBP|INR|JPY|KRW|MXN|SAR|USD|ZAR)"
_COLLOQUIAL_CURRENCY = r"(?:bucks?|quid|cents?|pence|grand)"
_CURRENCY_MARKER = (
    rf"(?:{_COMMON_CURRENCY_CODE}|{_ISO_CURRENCY_CODE}|"
    rf"[{_CURRENCY_SYMBOL_CLASS}]|{_CURRENCY_UNITS}|{_COLLOQUIAL_CURRENCY})"
)
_REVERSE_CURRENCY_MARKER = rf"(?:{_CURRENCY_MARKER}|{_AMBIGUOUS_CURRENCY_AFTER_AMOUNT})"
_MAGNITUDE_SUFFIX = r"(?:mn|mm|bn|k|m|b)"
_DIGIT_RUN = r"\d(?:[\d,_]*\d)?"
_DECIMAL_FRACTION = r"(?:\.\d(?:[\d_]*\d)?)?"
_SCIENTIFIC_EXPONENT = rf"(?:e[+\-\u2212]?{_DIGIT_RUN})?"
_DIGIT_AMOUNT = rf"{_DIGIT_RUN}{_DECIMAL_FRACTION}{_SCIENTIFIC_EXPONENT}(?:{_MAGNITUDE_SUFFIX})?"
_NUMBER_WORDS = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|"
    r"billion|and|a|an)"
)
_NUMBER_VALUE_WORDS = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|"
    r"billion)"
)
_WORD_AMOUNT = (
    rf"(?:(?:{_NUMBER_VALUE_WORDS})(?:[ -]+{_NUMBER_WORDS}){{0,9}}|"
    rf"(?:a|an)[ -]+(?:hundred|thousand|million|billion)"
    rf"(?:[ -]+{_NUMBER_WORDS}){{0,8}})"
)
_WORD_NUMBER_PATTERN = re.compile(rf"(?<!\w){_WORD_AMOUNT}(?!\w)", re.IGNORECASE)
_QUANTIFIED_NUMBER_PATTERN = re.compile(
    r"(?<!\w)(?:a\s+dozen|dozens?|hundreds|thousands|millions|billions|"
    r"scores?\s+of|twice\s+as\s+many|double\s+the\s+(?:number|amount|count))(?!\w)",
    re.IGNORECASE,
)
_ARTICLE_CURRENCY = rf"(?:{_CURRENCY_NAMES}|{_COLLOQUIAL_CURRENCY})"
_MONEY_GAP = r"[\s:;,./=+~\u2212\u223c\u2248()\[\]{}\-\u2010-\u2015]{0,32}"
_MONEY_APPROXIMATION = (
    r"(?:approximately|about|around|roughly|nearly|up\s+to|at\s+least|"
    r"more\s+than|less\s+than)"
)
_MONEY_SEPARATOR = rf"{_MONEY_GAP}(?:{_MONEY_APPROXIMATION}{_MONEY_GAP})?"
_REVERSE_MONEY_SEPARATOR = rf"{_MONEY_GAP}(?:(?:in|of){_MONEY_GAP})?"
_MONEY_PATTERNS = (
    re.compile(
        rf"(?<!\w){_CURRENCY_MARKER}{_MONEY_SEPARATOR}{_DIGIT_AMOUNT}(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<!\w){_CURRENCY_MARKER}\s*\(\s*{_DIGIT_AMOUNT}\s*\)(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<!\w){_DIGIT_AMOUNT}{_REVERSE_MONEY_SEPARATOR}"
        rf"{_REVERSE_CURRENCY_MARKER}(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<!\w){_DIGIT_AMOUNT}\s*\(\s*{_REVERSE_CURRENCY_MARKER}\s*\)(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<!\w){_CURRENCY_MARKER}{_MONEY_SEPARATOR}{_WORD_AMOUNT}(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<!\w){_CURRENCY_MARKER}\s*\(\s*{_WORD_AMOUNT}\s*\)(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<!\w){_WORD_AMOUNT}{_REVERSE_MONEY_SEPARATOR}"
        rf"(?:{_REVERSE_CURRENCY_MARKER}|bucks?|quid)(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<!\w){_WORD_AMOUNT}\s*\(\s*{_REVERSE_CURRENCY_MARKER}\s*\)(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(rf"\b(?:a|an)\s+{_ARTICLE_CURRENCY}\b", re.IGNORECASE),
)
_CURRENCY_MARKER_PATTERN = re.compile(
    rf"(?:(?<!\w)(?:{_COMMON_CURRENCY_CODE}|{_ISO_CURRENCY_CODE}|"
    rf"{_CURRENCY_UNITS}|{_COLLOQUIAL_CURRENCY})(?!\w)|[{_CURRENCY_SYMBOL_CLASS}])",
    re.IGNORECASE,
)
_GIVING_HISTORY_ELIDED_PATTERN = re.compile(
    rf"(?:^|[.!?]\s+)(?:(?:gave|gifted|donated|contributed|pledged)\b"
    rf"[^.!?\n]{{0,48}}(?:{_DIGIT_AMOUNT}|{_WORD_AMOUNT})|"
    rf"(?:a\s+gift|donation|contribution)\s+(?:of|was|is)?\s*"
    rf"(?:{_DIGIT_AMOUNT}|{_WORD_AMOUNT}))(?!\w)",
    re.IGNORECASE | re.MULTILINE,
)
_SOLICITATION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:please|kindly)\s+(?:consider\s+)?(?:donat(?:e|ing)|giv(?:e|ing)|"
        r"contribut(?:e|ing)|pledg(?:e|ing)|support(?:ing)?|help|chip\s+in|pitch\s+in|"
        r"send(?:\s+(?:what|whatever)\s+you\s+can)?)\b",
        r"\b(?:would|could|can|will|might)\s+you\s+(?:please\s+)?(?:consider\s+)?"
        r"(?:donat(?:e|ing)|giv(?:e|ing)|contribut(?:e|ing)|pledg(?:e|ing)|spare|"
        r"chip\s+in|pitch\s+in|send|(?:a|your)\s+(?:gift|donation|contribution))\b",
        r"\b(?:invite|ask|encourage)\s+you\s+to\s+"
        r"(?:donate|give|contribute|pledge|join|support|help)\b",
        r"\b(?:make|send)\s+(?:a|your)\s+(?:gift|donation|contribution)\b",
        r"\byour\s+(?:gift|donation|contribution|generosity)\b",
        r"\b(?:donate|give|contribute|pledge)\s+(?:today|now|here)\b",
        r"\b(?:support|help)\s+(?:us|our\s+(?:work|campaign|mission|program))\b",
        r"\bjoin\s+us\s+(?:with|through|by\s+making)\s+(?:a|your)\s+"
        r"(?:gift|donation|contribution)\b",
        r"\b(?:share|show)\s+(?:your\s+)?generosity\b",
        r"\bmake\s+a\s+difference\s+(?:today|now)\b",
    )
)
_SOURCE_SOLICITATION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"\b(?:donat(?:e|es|ing)|contribut(?:e|es|ing)|pledg(?:e|es|ing))\b",
        r"(?:^|[.!?]\s+|\n\s*)(?:please\s+|kindly\s+)?"
        r"(?:give|help|support|fund|join|chip\s+in|pitch\s+in)\b",
        r"\b(?:click|tap|appeal)\s+to\s+"
        r"(?:donate|give|contribute|pledge|help|support|fund|join)\b",
        r"\b(?:donations?|contributions?|gifts?)\s+(?:are\s+)?welcome\b",
        r"\b(?:we|the\s+(?:campaign|organization|program))\s+"
        r"(?:need|seek|welcome)\s+(?:donations?|contributions?|gifts?|support|funds?)\b",
        r"\b(?:become|join\s+as|sign\s+up\s+as)\s+(?:a\s+)?donor\b",
        r"\bwe\s+hope\s+you\s+(?:donate|give|contribute|pledge|help|support)\b",
    )
)
_PROVIDER_SOLICITATION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"\b(?:donat(?:e|es|ed|ing|ion|ions)|contribut(?:e|es|ed|ing|ion|ions)|"
        r"pledg(?:e|es|ed|ing)|donors?|fundrais(?:e|es|ed|ing)|funding)\b",
        r"(?:^|[.!?]\s+|\n\s*)(?:please\s+|kindly\s+)?"
        r"(?:give|help|support|fund|join|chip\s+in|pitch\s+in)\b",
        r"\b(?:donations?|contributions?|gifts?)\s+(?:are\s+)?welcome\b",
        r"\b(?:we|the\s+(?:campaign|organization|program))\s+"
        r"(?:need|seek|welcome)\s+(?:donations?|contributions?|gifts?|support|funds?)\b",
        r"\b(?:become|join\s+as|sign\s+up\s+as)\s+(?:a\s+)?donor\b",
    )
)
_STREET_SUFFIX = (
    r"(?:avenue|ave|boulevard|blvd|circle|cir|court|ct|drive|dr|highway|hwy|"
    r"lane|ln|parkway|pkwy|place|pl|plaza|plz|road|rd|square|sq|street|st|"
    r"terrace|ter|trail|trl|way)"
)
_WRITTEN_STREET_SUFFIX = (
    r"(?:avenue|boulevard|court|drive|highway|lane|parkway|road|street|terrace)"
)
_OBFUSCATED_EMAIL_LOCAL = r"[A-Z0-9][A-Z0-9._+-]{0,63}"
_OBFUSCATED_DOMAIN_LABEL = r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
_OBFUSCATED_STRONG_AT = (
    r"(?:\[\s*(?:at|@)\s*\]|\(\s*(?:at|@)\s*\)|<\s*(?:at|@)\s*>|"
    r"\{\s*(?:at|@)\s*\})"
)
_OBFUSCATED_AT = rf"(?:\bat\b|{_OBFUSCATED_STRONG_AT})"
_OBFUSCATED_STRONG_DOT = (
    r"(?:\[\s*(?:dot|period|point|\.)\s*\]|"
    r"\(\s*(?:dot|period|point|\.)\s*\)|"
    r"<\s*(?:dot|period|point|\.)\s*>|"
    r"\{\s*(?:dot|period|point|\.)\s*\})"
)
_OBFUSCATED_DOT = rf"(?:\bdot\b|{_OBFUSCATED_STRONG_DOT})"
_HIGH_CONFIDENCE_BARE_TLD = (
    r"(?:app|au|biz|ca|charity|cloud|co|com|de|dev|edu|foundation|fr|gov|in|info|"
    r"io|jp|me|mil|museum|net|ngo|online|org|site|store|tech|uk|us|xyz)"
)
_OBFUSCATED_EMAIL_PATTERNS = (
    re.compile(
        rf"(?<!\w){_OBFUSCATED_EMAIL_LOCAL}\s*"
        rf"{_OBFUSCATED_STRONG_AT}\s*{_OBFUSCATED_DOMAIN_LABEL}"
        rf"(?:\s*{_OBFUSCATED_DOT}\s*{_OBFUSCATED_DOMAIN_LABEL}){{0,3}}"
        rf"\s*{_OBFUSCATED_DOT}\s*[A-Z]{{2,63}}(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<!\w){_OBFUSCATED_EMAIL_LOCAL}\s+at\s+{_OBFUSCATED_DOMAIN_LABEL}"
        rf"(?:\s*{_OBFUSCATED_DOT}\s*{_OBFUSCATED_DOMAIN_LABEL}){{0,3}}"
        rf"\s*{_OBFUSCATED_DOT}\s*{_HIGH_CONFIDENCE_BARE_TLD}(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<!\w){_OBFUSCATED_EMAIL_LOCAL}\s+at\s+{_OBFUSCATED_DOMAIN_LABEL}"
        rf"(?:\s*{_OBFUSCATED_STRONG_DOT}\s*{_OBFUSCATED_DOMAIN_LABEL}){{0,3}}"
        rf"\s*{_OBFUSCATED_STRONG_DOT}\s*[A-Z]{{2,63}}(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:email|e-mail|contact|reach|write\s+to)\s+{_OBFUSCATED_EMAIL_LOCAL}"
        rf"\s*{_OBFUSCATED_AT}\s*{_OBFUSCATED_DOMAIN_LABEL}"
        rf"(?:\s*{_OBFUSCATED_DOT}\s*{_OBFUSCATED_DOMAIN_LABEL}){{0,3}}"
        rf"\s*{_OBFUSCATED_DOT}\s*"
        r"[A-Z]{2,63}(?!\w)",
        re.IGNORECASE,
    ),
)
_OBFUSCATED_DOMAIN_BODY = (
    rf"{_OBFUSCATED_DOMAIN_LABEL}"
    rf"(?:\s*{_OBFUSCATED_DOT}\s*{_OBFUSCATED_DOMAIN_LABEL}){{0,3}}"
    rf"\s*{_OBFUSCATED_DOT}\s*[A-Z]{{2,63}}"
)
_OBFUSCATED_STRONG_DOMAIN_BODY = (
    rf"{_OBFUSCATED_DOMAIN_LABEL}"
    rf"(?:\s*{_OBFUSCATED_STRONG_DOT}\s*{_OBFUSCATED_DOMAIN_LABEL}){{0,3}}"
    rf"\s*{_OBFUSCATED_STRONG_DOT}\s*[A-Z]{{2,63}}"
)
_OBFUSCATED_DOMAIN_PREFIX = rf"(?:(?:hxxps?|https?)\s*:\s*/{{1,2}}\s*|www\s*{_OBFUSCATED_DOT}\s*)"
_OBFUSCATED_DOMAIN_PATTERNS = (
    re.compile(
        rf"(?<!\w){_OBFUSCATED_STRONG_DOMAIN_BODY}(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:visit|website|web\s+site|go\s+to|browse|url)\b\s*:?\s*"
        rf"(?:{_OBFUSCATED_DOMAIN_PREFIX})?{_OBFUSCATED_DOMAIN_BODY}(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?<!\w){_OBFUSCATED_DOMAIN_PREFIX}{_OBFUSCATED_DOMAIN_BODY}(?!\w)",
        re.IGNORECASE,
    ),
)
_URI_SCHEME_PATTERN = re.compile(
    r"(?<![A-Z0-9+.-])[A-Z][A-Z0-9+.-]{0,31}:[^\s<>()]+",
    re.IGNORECASE,
)
_STRUCTURED_CONTACT_VALUE_CUE = re.compile(
    r"\b(?:address|address\s+line|city|code|country(?:\s+code)?|mailing|mailing\s+line|"
    r"post\s+code|postal|region|state|zip)\b",
    re.IGNORECASE,
)
_CONTACT_PATTERNS = (
    re.compile(r"[^\s@]{1,64}@[^\s@]{1,253}"),
    re.compile(
        rf"(?<!\w){_OBFUSCATED_EMAIL_LOCAL}\s*@\s*{_OBFUSCATED_DOMAIN_LABEL}"
        rf"(?:\s*\.\s*{_OBFUSCATED_DOMAIN_LABEL}){{0,3}}\s*\.\s*"
        r"[A-Z]{2,63}(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![\w@])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
        r"(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,63}(?![\w@])",
        re.IGNORECASE,
    ),
    re.compile(r"(?:https?://|mailto:|www\.)[^\s<>()]+", re.IGNORECASE),
    _URI_SCHEME_PATTERN,
    re.compile(
        r"(?<![@\w])(?:[A-Z0-9](?:[A-Z0-9-]{0,62})\.)+[A-Z]{2,63}"
        r"(?:/[^\s<>()]*)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<!\w)(?:\+?[0-9]{1,3}[ .-]?)?(?:\(?[0-9]{3}\)?[ .-])"
        r"[0-9]{3}[ .-][0-9]{4}(?!\w)"
    ),
    re.compile(r"(?<![0-9])(?:[0-9]{3}[ .-][0-9]{4}|[0-9]{10,15})(?![0-9])"),
    re.compile(
        rf"\b[0-9]{{1,6}}(?:-?[A-Z])?[,;:]?\s+"
        rf"[A-Z0-9.'-]+(?:\s+[A-Z0-9.'-]+){{0,4}}\s+"
        rf"{_STREET_SUFFIX}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:{_NUMBER_VALUE_WORDS})(?:[ -]+{_NUMBER_WORDS}){{0,5}}\s+"
        rf"[A-Z0-9.'-]+(?:\s+[A-Z0-9.'-]+){{0,4}}\s+{_WRITTEN_STREET_SUFFIX}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bP\.?\s*O\.?\s+Box\s+[0-9]+\b", re.IGNORECASE),
)
_NORMALIZED_DOMESTIC_PHONE_PATTERNS = (
    re.compile(r"(?<![0-9])(?:\+?[0-9]{1,3}-)?[0-9]{3}-[0-9]{3}-[0-9]{4}(?![0-9])"),
    re.compile(r"(?<![0-9])(?:[0-9]{3}-[0-9]{4}|[0-9]{10,15})(?![0-9])"),
)
_GROUPED_PHONE_CANDIDATE = re.compile(r"(?<![A-Za-z0-9])[0-9]+(?:-[0-9]+){2,6}(?![A-Za-z0-9])")
_PHONE_CUE = (
    r"(?:call(?:\s+(?:me|us|him|her|them))?|telephone|phone|tel|mobile|"
    r"text(?:\s+(?:me|us|him|her|them))?|"
    r"(?:our|my|the|your)\s+(?:phone\s+)?number\s+is|"
    r"(?:phone\s+)?number\s+is|"
    r"(?:reach|contact)(?:\s+[A-Z][A-Z.'-]*){0,2}\s+at)"
)
_PHONE_WORD_DIGIT = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_PHONE_DIGIT_TOKEN = rf"(?:[0-9]|{_PHONE_WORD_DIGIT})"
_UNCUED_WORD_PHONE_PATTERN = re.compile(
    rf"(?<!\w){_PHONE_WORD_DIGIT}(?:[\s,;:./()\-]+{_PHONE_WORD_DIGIT}){{6,14}}"
    rf"(?![\s,;:./()\-]+{_PHONE_WORD_DIGIT})(?!\w)",
    re.IGNORECASE,
)
_CUE_PHONE_PATTERNS = (
    re.compile(
        rf"\b{_PHONE_CUE}\b[\s:,-]{{0,16}}{_PHONE_DIGIT_TOKEN}"
        rf"(?:[\s,;:./()\-]+{_PHONE_DIGIT_TOKEN}){{6,14}}"
        rf"(?![\s,;:./()\-]+{_PHONE_DIGIT_TOKEN})(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_PHONE_CUE}\b[\s:,-]{{0,16}}"
        r"(?:\+?1[\s.()/-]{1,8})?\(?[0-9]{3}\)?[\s./-]{1,8}"
        r"[A-Z][A-Z0-9-]{3,15}(?!\w)",
        re.IGNORECASE,
    ),
)
_CUED_SHORT_CONTACT_CODE_PATTERNS = (
    re.compile(
        r"\b(?:call|dial|extension|ext\.?|phone|sms|tel|telephone|text)\b"
        r"(?:\s+(?:at|code|is|me|us))?[\s:#-]{0,8}(?<![0-9])"
        r"(?:[0-9][\s./()-]*){3,6}(?![\s./()-]*[0-9])",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:call|dial|extension|ext\.?|phone|sms|tel|telephone|text)\b"
        rf"(?:\s+(?:at|code|is|me|us))?[\s:#-]{{0,8}}{_PHONE_DIGIT_TOKEN}"
        rf"(?:[\s,;:./()\-]+{_PHONE_DIGIT_TOKEN}){{2,5}}"
        rf"(?![\s,;:./()\-]+{_PHONE_DIGIT_TOKEN})",
        re.IGNORECASE,
    ),
)
_LITERAL_CHARACTER_ESCAPE = re.compile(
    r"\\x(?P<hex2>[0-9A-Fa-f]{2})|"
    r"\\u(?P<hex4>[0-9A-Fa-f]{4})|"
    r"\\U(?P<hex8>[0-9A-Fa-f]{8})|"
    r"%u(?P<percent4>[0-9A-Fa-f]{4})|"
    r"\\(?P<octal>[0-7]{3})"
)
_CUED_IP_CANDIDATE = re.compile(
    r"\b(?:visit|server|ip\s+address|connect\s+to|endpoint|host)\b"
    r"(?:\s*:(?!:)\s*|\s+)"
    r"\[?([0-9A-F:.]{2,45})\]?",
    re.IGNORECASE,
)
_STANDALONE_IP_CANDIDATE = re.compile(
    r"(?<![A-Z0-9:])(?:\[([0-9A-F:.]{2,45})\]|([0-9A-F:.]{2,45}))(?![A-Z0-9:])",
    re.IGNORECASE,
)
_CUED_ADDRESS_FRAGMENT = re.compile(
    r"\b(address|mail\s+to|write\s+to|visit)\b\s*:?\s*([^.!?\n]{3,120})",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _FactEligibility:
    eligible: list[ApprovedFact]
    excluded_ids: list[str]
    duplicate_id: bool
    instruction_like_excluded: bool
    solicitation_like_excluded: bool
    monetary_excluded: bool
    contact_like_excluded: bool
    donor_identifier_excluded: bool
    policy_control_like_excluded: bool
    giving_history_excluded: bool


def contains_instruction_like_text(text: str) -> bool:
    """Return true when data resembles an instruction to the drafting model."""

    return any(
        pattern.search(view) for view in security_views(text) for pattern in _INSTRUCTION_PATTERNS
    )


def contains_policy_control_like_text(text: str) -> bool:
    """Detect text that exposes or attempts to override policy-owned fields."""

    raw_views = security_views(text)
    views = tuple(
        dict.fromkeys((*raw_views, *(re.sub(r"[._:-]+", " ", view) for view in raw_views)))
    )
    return any(pattern.search(view) for view in views for pattern in _POLICY_CONTROL_PATTERNS)


def contains_giving_history_like_text(
    text: str,
    donor: DonorRecord,
    *,
    include_contextual_claims: bool = True,
) -> bool:
    """Detect raw giving-history prose that must not cross the provider boundary."""

    views = security_views(text)
    if contains_giving_history_field_like_text(text):
        return True
    if not include_contextual_claims:
        return False
    raw_donor_names = [donor.first_name]
    if donor.last_name is not None:
        raw_donor_names.extend((donor.last_name, f"{donor.first_name} {donor.last_name}"))
    donor_names = tuple(
        dict.fromkeys(
            view for name in raw_donor_names for view in security_views(name) if len(view) >= 2
        )
    )
    named_giving_pattern = (
        re.compile(
            rf"(?<!\w)(?:{'|'.join(re.escape(name) for name in donor_names)})(?!\w)"
            rf"(?!\s*,?\s*(?:the\s+)?(?:campaign|foundation|organization|program|"
            rf"team|volunteers?)\b)"
            rf"[^.!?\n]{{0,48}}\b(?:gave|gift(?:ed)?|donat(?:e|ed)|"
            rf"contribut(?:e|ed)|pledg(?:e|ed))\b[^.!?\n]{{0,48}}"
            rf"(?:{_DIGIT_AMOUNT}|{_WORD_AMOUNT})(?!\w)",
            re.IGNORECASE,
        )
        if donor_names
        else None
    )
    return any(
        _GIVING_HISTORY_CONTEXT_PATTERN.search(view)
        or _GIVING_HISTORY_ELIDED_PATTERN.search(view)
        or (named_giving_pattern is not None and named_giving_pattern.search(view))
        for view in views
    )


def contains_giving_history_field_like_text(text: str) -> bool:
    """Detect explicit raw giving-history field names without donor context."""

    return any(_GIVING_HISTORY_FIELD_PATTERN.search(view) for view in security_views(text))


def contains_solicitation_language(text: str) -> bool:
    """Apply a bounded heuristic lexicon for provider-controlled solicitation."""

    return any(
        pattern.search(view)
        for view in security_views(text)
        for pattern in (*_SOLICITATION_PATTERNS, *_SOURCE_SOLICITATION_PATTERNS)
    )


def contains_provider_solicitation_language(text: str) -> bool:
    """Detect solicitation in prose that is not an authorized exact paragraph."""

    return contains_solicitation_language(text) or any(
        pattern.search(view)
        for view in security_views(text)
        for pattern in _PROVIDER_SOLICITATION_PATTERNS
    )


def _contains_unicode_domain(text: str) -> bool:
    """Validate domain-shaped Unicode tokens without depending on a live TLD list."""

    view = _contact_security_view(text)
    candidates: list[str] = []
    run_start: int | None = None
    for index in range(len(view) + 1):
        character = view[index] if index < len(view) else ""
        category = unicodedata.category(character) if character else ""
        is_candidate_character = (
            character == "." or character == "-" or (category[:1] in {"L", "M", "N"})
        )
        if is_candidate_character:
            if run_start is None:
                run_start = index
            continue
        if run_start is None:
            continue
        token = view[run_start:index].strip(".")
        candidates.extend(part for part in re.split(r"\.{2,}", token) if part.count(".") >= 1)
        run_start = None

    for host in candidates:
        labels = host.split(".")
        if len(labels) < 2:
            continue
        if not (2 <= len(labels[-1]) <= 63 and any(char.isalpha() for char in labels[-1])):
            continue
        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or unicodedata.category(label[0]).startswith("M")
            or any(
                not (char == "-" or unicodedata.category(char)[:1] in {"L", "M", "N"})
                for char in label
            )
            for label in labels
        ):
            continue
        try:
            ascii_labels = [label.encode("idna") for label in labels]
        except UnicodeError:
            continue
        if all(len(label) <= 63 for label in ascii_labels) and len(b".".join(ascii_labels)) <= 253:
            return True
    return False


def _validated_domain_host(host: str, *, require_root_zone_tld: bool) -> bool:
    """Validate a bounded Unicode or ASCII hostname and, when requested, its TLD."""

    normalized = _contact_security_view(host).strip(".")
    labels = normalized.split(".")
    if len(labels) < 2 or len(normalized) > 253:
        return False
    try:
        ascii_labels = tuple(label.encode("idna").decode("ascii").casefold() for label in labels)
    except UnicodeError:
        return False
    if any(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
        for label in ascii_labels
    ):
        return False
    terminal = ascii_labels[-1]
    if terminal.isdecimal():
        return False
    internationalized = any(ord(character) > 127 for character in normalized)
    return (
        not require_root_zone_tld
        or internationalized
        or terminal.startswith("xn--")
        or terminal in IANA_ROOT_ZONE_TLD_SET
    )


_CANONICAL_EMAIL_CANDIDATE = re.compile(
    r"(?<![\w@])(?P<local>[A-Z0-9.!#$%&'*+/=?^_`{|}~-]{1,64})@"
    r"(?P<host>[^\s/@:;,!?<>()\[\]]{3,253})",
    re.IGNORECASE,
)
_STRONG_AT_TOKEN = re.compile(rf"\s*{_OBFUSCATED_STRONG_AT}\s*", re.IGNORECASE)
_STRONG_DOT_TOKEN = re.compile(rf"\s*{_OBFUSCATED_STRONG_DOT}\s*", re.IGNORECASE)
_STRONG_COLON_TOKEN = re.compile(
    r"\s*(?:\[\s*(?:colon|:)\s*\]|\(\s*(?:colon|:)\s*\)|"
    r"<\s*(?:colon|:)\s*>|\{\s*(?:colon|:)\s*\})\s*",
    re.IGNORECASE,
)
_WORD_AT_TOKEN = re.compile(r"(?<=\S)\s+at\s+(?=\S)", re.IGNORECASE)
_WORD_DOT_TOKEN = re.compile(r"(?<=\S)\s+dot\s+(?=\S)", re.IGNORECASE)
_CUED_WORD_DOT_TOKEN = re.compile(
    r"(?<=\S)\s+(?:dot|period|point)\s+(?=\S)",
    re.IGNORECASE,
)
_WORD_COLON_TOKEN = re.compile(r"(?<=\S)\s+colon\s+(?=\S)", re.IGNORECASE)
_CONTACT_EMAIL_CUE = re.compile(
    r"\b(?:contact|e-mail|email|reach|write\s+to)\b",
    re.IGNORECASE,
)
_CONTACT_WEB_CUE = re.compile(
    r"\b(?:browse|go\s+to|url|visit|web\s+site|website)\b",
    re.IGNORECASE,
)
_CONTACT_DOMAIN_CUE = re.compile(
    r"\b(?:browse|contact|e-mail|email|go\s+to|reach|url|visit|web\s+site|website|write\s+to)\b",
    re.IGNORECASE,
)
_ORDINARY_PERSON_LOCATION_PREFIX = re.compile(
    r"\b(?:met|saw|welcomed)\s+$",
    re.IGNORECASE,
)


def _contact_clause_prefix(text: str, end: int) -> str:
    """Return the current clause without trusting a user-spellable marker token."""

    clause_start = 0
    for separator in (".", "!", "?", ";", "\n", _SECURITY_SENTENCE_BREAK):
        separator_index = text.rfind(separator, 0, end)
        if separator_index >= 0:
            clause_start = max(clause_start, separator_index + len(separator))
    return text[clause_start:end]


def _contact_clause_allows_dot_word(text: str, end: int) -> bool:
    """Require a web cue or an email cue paired with an explicit ``at`` marker."""

    clause_prefix = _contact_clause_prefix(text, end)
    if _CONTACT_WEB_CUE.search(clause_prefix) is not None:
        return True
    return any(
        "@" in clause_prefix[match.end() :] for match in _CONTACT_EMAIL_CUE.finditer(clause_prefix)
    )


def _canonicalize_defanged_contact(
    text: str,
    *,
    include_word_tokens: bool,
    include_cued_dot_words: bool = False,
) -> str:
    canonical = _STRONG_AT_TOKEN.sub("@", text)
    canonical = _STRONG_DOT_TOKEN.sub(".", canonical)
    canonical = _STRONG_COLON_TOKEN.sub(":", canonical)
    if include_word_tokens:
        canonical = _WORD_AT_TOKEN.sub("@", canonical)
        if include_cued_dot_words:
            canonical_parts: list[str] = []
            previous_end = 0
            for match in _CUED_WORD_DOT_TOKEN.finditer(canonical):
                canonical_parts.append(canonical[previous_end : match.start()])
                canonical_parts.append(
                    "."
                    if _contact_clause_allows_dot_word(canonical, match.start())
                    else match.group(0)
                )
                previous_end = match.end()
            canonical_parts.append(canonical[previous_end:])
            canonical = "".join(canonical_parts)
        else:
            canonical = _WORD_DOT_TOKEN.sub(".", canonical)
        canonical = _WORD_COLON_TOKEN.sub(":", canonical)
    return canonical


def _canonical_email_hosts(text: str) -> tuple[tuple[str, str, int], ...]:
    candidates: list[tuple[str, str, int]] = []
    for match in _CANONICAL_EMAIL_CANDIDATE.finditer(text):
        host = match.group("host").rstrip(".")
        candidate = f"{match.group('local')}@{host}"
        candidates.append((candidate, host, match.start()))
    return tuple(candidates)


def _contains_defanged_contact(text: str) -> bool:
    """Canonicalize one bounded defang layer, then validate email/domain candidates."""

    has_strong_token = bool(
        _STRONG_AT_TOKEN.search(text)
        or _STRONG_DOT_TOKEN.search(text)
        or _STRONG_COLON_TOKEN.search(text)
    )
    has_contact_cue = bool(_CONTACT_DOMAIN_CUE.search(text))
    strong_view = _canonicalize_defanged_contact(text, include_word_tokens=False)
    if has_strong_token and (
        any(
            _validated_domain_host(host, require_root_zone_tld=False)
            for _, host, _ in _canonical_email_hosts(strong_view)
        )
        or _contains_unicode_domain(strong_view)
        or _contains_standalone_ip_address(strong_view)
        or _contains_cued_ip_address(strong_view)
    ):
        return True

    word_view = _canonicalize_defanged_contact(
        text,
        include_word_tokens=True,
        include_cued_dot_words=has_contact_cue,
    )
    if (
        has_contact_cue
        or len(re.findall(r"\b(?:colon|dot|period|point)\b", text, re.IGNORECASE)) >= 2
    ) and (_contains_standalone_ip_address(word_view) or _contains_cued_ip_address(word_view)):
        return True
    for _candidate, host, start in _canonical_email_hosts(word_view):
        if not _validated_domain_host(
            host,
            require_root_zone_tld=not has_contact_cue,
        ):
            continue
        terminal = host.rstrip(".").rsplit(".", maxsplit=1)[-1].casefold()
        internationalized = any(ord(character) > 127 for character in host)
        ordinary_location_context = bool(
            _ORDINARY_PERSON_LOCATION_PREFIX.search(word_view[max(0, start - 24) : start])
        )
        if ordinary_location_context and not (
            has_contact_cue or internationalized or terminal.startswith("xn--")
        ):
            continue
        if (
            has_contact_cue
            or internationalized
            or terminal.startswith("xn--")
            or len(terminal) == 2
            or re.fullmatch(_HIGH_CONFIDENCE_BARE_TLD, terminal, re.IGNORECASE)
            or not ordinary_location_context
        ):
            return True

    return has_contact_cue and _contains_unicode_domain(word_view)


def _contact_security_view(text: str) -> str:
    """Normalize domain separators and every Unicode decimal digit for privacy scans."""

    view = security_view(text).translate(
        {
            ord("\u3002"): ".",
            ord("\uff0e"): ".",
            ord("\uff61"): ".",
        }
    )
    normalized_characters: list[str] = []
    for character in view:
        category = unicodedata.category(character)
        if category == "Nd":
            normalized_characters.append(str(unicodedata.decimal(character)))
        elif category == "Pd" or character in {"\u2043", "\u2212"}:
            normalized_characters.append("-")
        else:
            normalized_characters.append(character)
    return "".join(normalized_characters)


def _contains_international_phone(text: str) -> bool:
    for index, character in enumerate(text):
        if character != "+" or (
            index > 0 and (text[index - 1].isalnum() or text[index - 1] == "_")
        ):
            continue
        digits = 0
        leading_separators = 0
        for candidate_character in text[index + 1 : index + 65]:
            category = unicodedata.category(candidate_character)
            if candidate_character.isdecimal():
                digits += 1
                if digits > 15:
                    break
                continue
            if candidate_character.isalnum() or category.startswith("C"):
                break
            if digits == 0:
                leading_separators += 1
                if leading_separators > 4:
                    break
        if 7 <= digits <= 15:
            return True
    return False


def _phone_security_view(text: str) -> str:
    """Canonicalize separators and single-digit Unicode numbers for phone matching."""

    normalized: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        try:
            numeric_value = unicodedata.numeric(character)
        except (TypeError, ValueError):
            numeric_value = None
        if numeric_value is not None and numeric_value.is_integer() and 0 <= numeric_value <= 9:
            normalized.append(str(int(numeric_value)))
        elif character == "+" or character.isdecimal() or character.isalnum():
            normalized.append(character)
        elif character.isspace() or category[:1] in {"P", "S", "Z"}:
            if not normalized or normalized[-1] != "-":
                normalized.append("-")
        else:
            normalized.append(character)
    return "".join(normalized)


def _contains_grouped_international_phone(text: str) -> bool:
    """Detect plausible grouped international numbers without treating dates as phones."""

    for match in _GROUPED_PHONE_CANDIDATE.finditer(text):
        groups = match.group(0).split("-")
        digit_count = sum(len(group) for group in groups)
        if not 7 <= digit_count <= 15:
            continue
        first_group = groups[0]
        has_dial_prefix = first_group.startswith("00") or first_group.startswith("011")
        has_bare_country_code = len(groups) >= 4 and 1 <= len(first_group) <= 3
        if has_dial_prefix or has_bare_country_code:
            return True
    return False


def _decode_security_obfuscation(text: str) -> str:
    """Decode one bounded layer of common security-relevant encoding syntax."""

    if not any(marker in text for marker in ("%", "&", "\\")):
        return text
    decoded = html.unescape(unquote(text))

    def replace_escape(match: re.Match[str]) -> str:
        octal = match.group("octal")
        encoded_value = next(value for value in match.groups() if value is not None)
        value = int(encoded_value, 8 if octal is not None else 16)
        return match.group(0) if value > 0x10FFFF or 0xD800 <= value <= 0xDFFF else chr(value)

    return _LITERAL_CHARACTER_ESCAPE.sub(replace_escape, decoded)


def _contains_cued_ip_address(text: str) -> bool:
    for match in _CUED_IP_CANDIDATE.finditer(text):
        candidate = match.group(1).rstrip(".,;!?")
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return True
    return False


def _contains_standalone_ip_address(text: str) -> bool:
    """Parse bounded standalone IP-shaped tokens without relying on lexical cues."""

    for match in _STANDALONE_IP_CANDIDATE.finditer(text):
        candidate = (match.group(1) or match.group(2)).rstrip(".,;!?")
        prefix = text[max(0, match.start() - 16) : match.start()]
        if re.search(r"\bversion\s*:?\s*$", prefix, re.IGNORECASE):
            continue
        candidates = [candidate]
        if "." in candidate and ":" in candidate:
            host, _, port = candidate.rpartition(":")
            if port.isdecimal():
                candidates.append(host)
        for host in candidates:
            try:
                ipaddress.ip_address(host)
            except ValueError:
                continue
            return True
    return False


def _contains_cued_international_address(text: str) -> bool:
    for match in _CUED_ADDRESS_FRAGMENT.finditer(text):
        cue = match.group(1).casefold()
        fragment = match.group(2)
        if not any(character.isdecimal() for character in fragment):
            continue
        word_count = len(re.findall(r"[^\W\d_]{2,}", fragment, re.UNICODE))
        if word_count < 2:
            continue
        starts_with_number = re.match(r"\s*\d", fragment) is not None
        number_before_comma = re.search(r"\d[^,]{0,48},", fragment) is not None
        if cue != "visit" or starts_with_number or number_before_comma:
            return True
    return False


def contains_contact_like_text(text: str) -> bool:
    """Detect contact details that must not cross the drafting boundary."""

    for security_text in security_views(text):
        view = _contact_security_view(security_text)
        phone_view = _phone_security_view(view)
        if (
            any(pattern.search(view) for pattern in _CONTACT_PATTERNS)
            or any(pattern.search(view) for pattern in _OBFUSCATED_EMAIL_PATTERNS)
            or any(pattern.search(view) for pattern in _OBFUSCATED_DOMAIN_PATTERNS)
            or _contains_defanged_contact(view)
            or any(pattern.search(view) for pattern in _CUE_PHONE_PATTERNS)
            or _UNCUED_WORD_PHONE_PATTERN.search(view)
            or any(pattern.search(view) for pattern in _CUED_SHORT_CONTACT_CODE_PATTERNS)
            or any(pattern.search(phone_view) for pattern in _NORMALIZED_DOMESTIC_PHONE_PATTERNS)
            or _contains_grouped_international_phone(phone_view)
            or _contains_unicode_domain(view)
            or _contains_international_phone(view)
            or _contains_standalone_ip_address(view)
            or _contains_cued_ip_address(view)
            or _contains_cued_international_address(view)
        ):
            return True
    return False


def money_expressions(text: str) -> tuple[str, ...]:
    """Extract digit- and word-based currency expressions for comparison."""

    expressions: list[str] = []
    for view in dict.fromkeys((text, *security_views(text))):
        matches: list[re.Match[str]] = []
        for pattern in _MONEY_PATTERNS:
            matches.extend(pattern.finditer(view))
        expressions.extend(
            match.group(0) for match in sorted(matches, key=lambda match: match.start())
        )
        for marker in _CURRENCY_MARKER_PATTERN.finditer(view):
            window_start = max(0, marker.start() - 32)
            window_end = min(len(view), marker.end() + 32)
            window = view[window_start:window_end]
            if any(character.isnumeric() and not character.isascii() for character in window):
                expressions.append(window.strip())
    return tuple(dict.fromkeys(expressions))


def word_number_expressions(text: str) -> tuple[str, ...]:
    """Extract bounded cardinal-number words from provider-controlled prose."""

    expressions: list[str] = []
    for view in dict.fromkeys((text, *security_views(text))):
        for pattern in (_WORD_NUMBER_PATTERN, _QUANTIFIED_NUMBER_PATTERN):
            expressions.extend(match.group(0) for match in pattern.finditer(view))
    return tuple(dict.fromkeys(expressions))


def security_view(text: str) -> str:
    """Return a compatibility-normalized view used only by security comparisons."""

    if text.isascii():
        return text
    return unicodedata.normalize("NFKC", text).translate(
        {
            ord("\u02bc"): "'",
            ord("\u2018"): "'",
            ord("\u2019"): "'",
            ord("\u201b"): "'",
        }
    )


def security_skeleton(text: str) -> str:
    """Return a mark-stripped compatibility skeleton for bounded English lexicons."""

    if text.isascii():
        return text
    decomposed = unicodedata.normalize("NFKD", security_view(text))
    return "".join(
        character for character in decomposed if not unicodedata.category(character).startswith("M")
    )


_ASCII_SECURITY_TOKEN_TRANSLATION = str.maketrans(
    {
        character: f" {_SECURITY_SENTENCE_BREAK} " if character in ".!?;\n\r" else " "
        for character in "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~\n\r"
    }
)


def _security_token_view(text: str) -> str:
    """Expose punctuation-separated payload tokens without discarding the original view."""

    if text.isascii():
        tokenized = text.translate(_ASCII_SECURITY_TOKEN_TRANSLATION)
        return re.sub(r"\s+", " ", tokenized).strip()
    tokenized = "".join(
        (
            f" {_SECURITY_SENTENCE_BREAK} "
            if character in ".!?;\n\r"
            else " "
            if character == "_" or unicodedata.category(character)[:1] in {"P", "S"}
            else character
        )
        for character in text
    )
    return re.sub(r"\s+", " ", tokenized).strip()


def security_views(text: str) -> tuple[str, ...]:
    """Return normalized original and one-layer-decoded lexical security views."""

    base_views = (security_view(text), security_skeleton(text))
    decoded_views = tuple(_decode_security_obfuscation(view) for view in base_views)
    normalized_views = (
        *base_views,
        *(security_view(view) for view in decoded_views),
        *(security_skeleton(view) for view in decoded_views),
    )
    distinct_normalized_views = tuple(dict.fromkeys(normalized_views))
    return tuple(
        dict.fromkeys(
            (
                *distinct_normalized_views,
                *(_security_token_view(view) for view in distinct_normalized_views),
            )
        )
    )


def contains_donor_identifier(value: str, donor_id: str) -> bool:
    """Detect the exact structured donor identifier in provider-bound text."""

    return any(
        re.search(
            rf"(?<![A-Za-z0-9]){re.escape(donor_id_view)}(?![A-Za-z0-9])",
            value_view,
            re.IGNORECASE,
        )
        is not None
        for donor_id_view in security_views(donor_id)
        for value_view in security_views(value)
    )


def _contains_bounded_security_literal(
    value_views: tuple[str, ...],
    literal: str,
) -> bool:
    for literal_view in security_views(literal):
        if any(view.casefold() == literal_view.casefold() for view in value_views):
            return True
        pattern = rf"(?<![\w]){re.escape(literal_view)}(?![\w])"
        allow_embedded = len(literal_view) >= 4 or (
            len(literal_view) >= 3 and literal_view.isdecimal()
        )
        allow_embedded = allow_embedded or any(
            _STRUCTURED_CONTACT_VALUE_CUE.search(view) for view in value_views
        )
        if allow_embedded and any(re.search(pattern, view, re.IGNORECASE) for view in value_views):
            return True
    return False


def donor_contact_literals(donor: DonorRecord) -> tuple[str, ...]:
    """Return the structured contact literals that may never cross the provider boundary."""

    literals: list[str] = []
    if donor.email is not None:
        literals.append(donor.email)
    address = donor.postal_address
    if address is not None:
        literals.extend(
            candidate
            for candidate in (
                address.line_1,
                address.line_2,
                address.city,
                address.region,
                address.postal_code,
                address.country_code,
            )
            if candidate is not None
        )
        address_parts = [
            address.line_1,
            *(value for value in (address.line_2,) if value is not None),
            address.city,
            address.region,
            address.postal_code,
            address.country_code,
        ]
        literals.extend(
            (
                " ".join(address_parts),
                ", ".join(address_parts),
                f"{address.city} {address.region} {address.postal_code}",
            )
        )
    return tuple(dict.fromkeys(literals))


def contains_donor_contact_value(value: str, donor: DonorRecord) -> bool:
    """Detect exact structured donor contact values in provider-bound free text."""

    value_views = security_views(value)
    return any(
        _contains_bounded_security_literal(value_views, literal)
        for literal in donor_contact_literals(donor)
    )


def _fact_id_security_view(fact_id: str) -> str:
    """Expose identifier separators to bounded content-safety lexicons."""

    return re.sub(r"[._:-]+", " ", security_view(fact_id))


def format_money(money: Money) -> str:
    """Render money without relying on locale-specific process state."""

    with localcontext(_DECIMAL_CONTEXT):
        amount = money.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rendered = f"{amount:,.2f}"
    if rendered.endswith(".00"):
        rendered = rendered[:-3]
    return f"{money.currency} {rendered}"


def format_ask_paragraph(campaign_name: str, ask: Money | None) -> str:
    """Return the policy-owned solicitation paragraph used by every provider."""

    if ask is None:
        return f"Please consider supporting {campaign_name} in a way that is right for you."
    return f"Would you consider a gift of {format_money(ask)} to support {campaign_name}?"


def calculate_ask(donor: DonorRecord, campaign: CampaignBrief) -> Money | None:
    """Calculate the approved ask outside the drafting provider."""

    policy = campaign.ask_policy
    if policy.strategy == "none":
        return None

    assert isinstance(policy, MultiplierAskPolicy)
    last_gift = donor.giving.last_gift_amount
    if last_gift is None:
        return None

    with localcontext(_DECIMAL_CONTEXT):
        raw_amount = last_gift * policy.multiplier
        steps = (raw_amount / policy.rounding_increment).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
        rounded = steps * policy.rounding_increment
        bounded = min(max(rounded, policy.minimum), policy.maximum)
        amount = bounded.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Money.model_construct(amount=amount, currency=policy.currency)


def _eligible_facts(
    donor: DonorRecord,
    campaign: CampaignBrief,
) -> _FactEligibility:
    combined = [*campaign.facts, *donor.facts]
    seen: set[str] = set()
    eligible: list[ApprovedFact] = []
    excluded: list[str] = []
    ambiguous = False
    instruction_like_excluded = False
    solicitation_like_excluded = False
    monetary_excluded = False
    contact_like_excluded = False
    donor_identifier_excluded = False
    policy_control_like_excluded = False
    giving_history_excluded = False

    for fact in combined:
        fact_id_view = _fact_id_security_view(fact.fact_id)
        fact_slug = fact.fact_id.split(".", maxsplit=1)[1]
        fact_slug_view = re.sub(r"[._:-]+", " ", security_view(fact_slug))
        fact_id_has_donor_identifier = contains_donor_identifier(
            fact.fact_id, donor.donor_id
        ) or contains_donor_identifier(fact_id_view, donor.donor_id)
        fact_id_has_contact = contains_contact_like_text(fact_slug) or contains_contact_like_text(
            fact_id_view
        )
        fact_id_has_contact = fact_id_has_contact or contains_donor_contact_value(
            fact_id_view, donor
        )
        fact_id_has_instruction = contains_instruction_like_text(fact_id_view)
        fact_id_has_solicitation = contains_solicitation_language(fact_id_view)
        fact_id_has_money = bool(money_expressions(fact_id_view))
        fact_id_has_policy_control = contains_policy_control_like_text(fact_id_view)
        fact_id_has_giving_history = contains_giving_history_like_text(
            fact_id_view,
            donor,
            include_contextual_claims=False,
        )
        fact_text_has_donor_identifier = contains_donor_identifier(
            fact.text,
            donor.donor_id,
        )
        fact_text_has_contact = contains_donor_contact_value(
            fact.text,
            donor,
        ) or contains_contact_like_text(fact.text)
        fact_text_has_instruction = contains_instruction_like_text(fact.text)
        fact_text_has_policy_control = contains_policy_control_like_text(fact.text)
        fact_text_has_solicitation = contains_solicitation_language(fact.text)
        fact_text_has_money = bool(money_expressions(fact.text))
        fact_text_has_giving_history = contains_giving_history_like_text(
            fact.text,
            donor,
            include_contextual_claims=fact.source == FactSource.CRM,
        )
        joined_fact_values = tuple(
            dict.fromkeys(
                (
                    f"{fact_slug} {fact.text}",
                    f"{fact_slug_view} {fact.text}",
                    f"{fact_slug}{fact.text}",
                    f"{fact_slug}-{fact.text}",
                )
            )
        )
        joined_has_donor_identifier = any(
            contains_donor_identifier(value, donor.donor_id) for value in joined_fact_values
        )
        joined_has_contact = any(
            contains_donor_contact_value(value, donor) or contains_contact_like_text(value)
            for value in joined_fact_values
        )
        joined_has_instruction = any(
            contains_instruction_like_text(value) for value in joined_fact_values
        )
        joined_has_policy_control = any(
            contains_policy_control_like_text(value) for value in joined_fact_values
        )
        joined_has_solicitation = any(
            contains_solicitation_language(value) for value in joined_fact_values
        )
        joined_has_money = any(money_expressions(value) for value in joined_fact_values)
        joined_has_giving_history = any(
            contains_giving_history_like_text(
                value,
                donor,
                include_contextual_claims=fact.source == FactSource.CRM,
            )
            for value in joined_fact_values
        )
        joined_only_donor_identifier = joined_has_donor_identifier and not (
            fact_id_has_donor_identifier or fact_text_has_donor_identifier
        )
        joined_only_contact = joined_has_contact and not (
            fact_id_has_contact or fact_text_has_contact
        )
        joined_only_instruction = joined_has_instruction and not (
            fact_id_has_instruction or fact_text_has_instruction
        )
        joined_only_policy_control = joined_has_policy_control and not (
            fact_id_has_policy_control or fact_text_has_policy_control
        )
        joined_only_solicitation = joined_has_solicitation and not (
            fact_id_has_solicitation or fact_text_has_solicitation
        )
        joined_only_money = joined_has_money and not (fact_id_has_money or fact_text_has_money)
        joined_only_giving_history = joined_has_giving_history and not (
            fact_id_has_giving_history or fact_text_has_giving_history
        )
        joined_only_sensitive = any(
            (
                joined_only_donor_identifier,
                joined_only_contact,
                joined_only_instruction,
                joined_only_policy_control,
                joined_only_solicitation,
                joined_only_money,
                joined_only_giving_history,
            )
        )
        fact_id_is_sensitive = any(
            (
                fact_id_has_donor_identifier,
                fact_id_has_contact,
                fact_id_has_instruction,
                fact_id_has_solicitation,
                fact_id_has_money,
                fact_id_has_policy_control,
                fact_id_has_giving_history,
            )
        )
        audit_fact_id = (
            _REDACTED_SENSITIVE_FACT_ID
            if fact_id_is_sensitive or joined_only_sensitive
            else fact.fact_id
        )
        if fact.fact_id in seen:
            ambiguous = True
            excluded.append(audit_fact_id)
            donor_identifier_excluded |= fact_id_has_donor_identifier
            contact_like_excluded |= fact_id_has_contact
            instruction_like_excluded |= fact_id_has_instruction
            solicitation_like_excluded |= fact_id_has_solicitation
            monetary_excluded |= fact_id_has_money
            policy_control_like_excluded |= fact_id_has_policy_control
            giving_history_excluded |= fact_id_has_giving_history
            continue
        seen.add(fact.fact_id)
        if not fact.approved_for_outreach:
            excluded.append(audit_fact_id)
            continue
        if fact_id_is_sensitive:
            donor_identifier_excluded |= fact_id_has_donor_identifier
            contact_like_excluded |= fact_id_has_contact
            instruction_like_excluded |= fact_id_has_instruction
            solicitation_like_excluded |= fact_id_has_solicitation
            monetary_excluded |= fact_id_has_money
            policy_control_like_excluded |= fact_id_has_policy_control
            giving_history_excluded |= fact_id_has_giving_history
            excluded.append(_REDACTED_SENSITIVE_FACT_ID)
            continue
        if joined_only_sensitive:
            donor_identifier_excluded |= joined_only_donor_identifier
            contact_like_excluded |= joined_only_contact
            instruction_like_excluded |= joined_only_instruction
            solicitation_like_excluded |= joined_only_solicitation
            monetary_excluded |= joined_only_money
            policy_control_like_excluded |= joined_only_policy_control
            giving_history_excluded |= joined_only_giving_history
            excluded.append(_REDACTED_SENSITIVE_FACT_ID)
            continue
        if fact_text_has_donor_identifier:
            donor_identifier_excluded = True
            excluded.append(fact.fact_id)
            continue
        if fact_text_has_contact:
            contact_like_excluded = True
            excluded.append(fact.fact_id)
            continue
        if fact_text_has_instruction:
            instruction_like_excluded = True
            excluded.append(fact.fact_id)
            continue
        if fact_text_has_policy_control:
            policy_control_like_excluded = True
            excluded.append(fact.fact_id)
            continue
        if fact_text_has_solicitation:
            solicitation_like_excluded = True
            excluded.append(fact.fact_id)
            continue
        if fact_text_has_money:
            monetary_excluded = True
            excluded.append(fact.fact_id)
            continue
        if fact_text_has_giving_history:
            giving_history_excluded = True
            excluded.append(fact.fact_id)
            continue
        eligible.append(fact)
    return _FactEligibility(
        eligible=eligible,
        excluded_ids=sorted(set(excluded)),
        duplicate_id=ambiguous,
        instruction_like_excluded=instruction_like_excluded,
        solicitation_like_excluded=solicitation_like_excluded,
        monetary_excluded=monetary_excluded,
        contact_like_excluded=contact_like_excluded,
        donor_identifier_excluded=donor_identifier_excluded,
        policy_control_like_excluded=policy_control_like_excluded,
        giving_history_excluded=giving_history_excluded,
    )


def evaluate_policy(donor: DonorRecord, campaign: CampaignBrief) -> PolicyDecision:
    """Evaluate consent, contactability, cadence, facts, ask, and review gates."""

    facts = _eligible_facts(donor, campaign)
    reason_codes: list[ReasonCode] = []

    if donor.do_not_contact:
        reason_codes.append(ReasonCode.DO_NOT_CONTACT)
    if donor.channel_consent == ConsentState.DENIED:
        reason_codes.append(ReasonCode.CONSENT_DENIED)
    if reason_codes:
        return PolicyDecision(
            disposition=PolicyDisposition.SUPPRESS,
            generation_allowed=False,
            reason_codes=reason_codes,
            ask=None,
            eligible_facts=(),
            excluded_fact_ids=facts.excluded_ids,
        )

    if donor.channel_consent == ConsentState.UNKNOWN:
        reason_codes.append(ReasonCode.CONSENT_UNKNOWN)
    if donor.preferred_channel == Channel.EMAIL and donor.email is None:
        reason_codes.append(ReasonCode.MISSING_EMAIL)
    if donor.preferred_channel == Channel.LETTER and donor.postal_address is None:
        reason_codes.append(ReasonCode.MISSING_POSTAL_ADDRESS)
    if facts.duplicate_id:
        reason_codes.append(ReasonCode.DUPLICATE_FACT_ID_ACROSS_INPUTS)
    identity_values = [
        value for value in (donor.first_name, donor.last_name, donor.title) if value is not None
    ]
    derived_identity = (
        f"{donor.title} {donor.last_name}"
        if donor.title is not None and donor.last_name is not None
        else donor.first_name
    )
    identity_security_views = [*identity_values, " ".join(identity_values), derived_identity]
    if any(contains_instruction_like_text(value) for value in identity_security_views):
        reason_codes.append(ReasonCode.INSTRUCTION_LIKE_IDENTITY_FIELD)
    if any(contains_policy_control_like_text(value) for value in identity_security_views):
        reason_codes.append(ReasonCode.POLICY_CONTROL_LIKE_IDENTITY_FIELD)
    if any(contains_giving_history_like_text(value, donor) for value in identity_security_views):
        reason_codes.append(ReasonCode.GIVING_HISTORY_LIKE_IDENTITY_FIELD)
    if any(
        contains_solicitation_language(value) or money_expressions(value)
        for value in identity_security_views
    ):
        reason_codes.append(ReasonCode.SOLICITATION_LIKE_IDENTITY_FIELD)
    if any(
        contains_contact_like_text(value) or contains_donor_contact_value(value, donor)
        for value in identity_security_views
    ):
        reason_codes.append(ReasonCode.CONTACT_LIKE_IDENTITY_FIELD)
    if any(contains_donor_identifier(value, donor.donor_id) for value in identity_security_views):
        reason_codes.append(ReasonCode.DONOR_IDENTIFIER_IN_IDENTITY_FIELD)

    days_since_contact: int | None = None
    if donor.last_contact_date is not None:
        days_since_contact = (campaign.as_of_date - donor.last_contact_date).days
        if days_since_contact < 0:
            reason_codes.append(ReasonCode.LAST_CONTACT_DATE_IN_FUTURE)

    if (
        donor.giving.last_gift_date is not None
        and donor.giving.last_gift_date > campaign.as_of_date
    ):
        reason_codes.append(ReasonCode.LAST_GIFT_DATE_IN_FUTURE)

    if (
        campaign.ask_policy.strategy != "none"
        and donor.giving.currency != campaign.ask_policy.currency
    ):
        reason_codes.append(ReasonCode.GIVING_CURRENCY_MISMATCH)

    ask = calculate_ask(donor, campaign)
    if campaign.ask_policy.strategy != "none" and ask is None:
        reason_codes.append(ReasonCode.MISSING_ASK_BASIS)

    if reason_codes:
        return PolicyDecision(
            disposition=PolicyDisposition.BLOCK,
            generation_allowed=False,
            reason_codes=reason_codes,
            ask=None,
            eligible_facts=(),
            excluded_fact_ids=facts.excluded_ids,
        )

    if (
        days_since_contact is not None
        and days_since_contact < campaign.minimum_days_between_contacts
    ):
        return PolicyDecision(
            disposition=PolicyDisposition.SUPPRESS,
            generation_allowed=False,
            reason_codes=[ReasonCode.CONTACT_FREQUENCY_LIMIT],
            ask=None,
            eligible_facts=(),
            excluded_fact_ids=facts.excluded_ids,
        )

    review_reasons: list[ReasonCode] = []
    if donor.segment in campaign.review_policy.segments:
        review_reasons.append(ReasonCode.RELATIONSHIP_MANAGED_SEGMENT)
    if ask is not None and ask.amount >= campaign.review_policy.ask_amount_at_or_above:
        review_reasons.append(ReasonCode.HIGH_VALUE_ASK)
    if facts.instruction_like_excluded:
        review_reasons.append(ReasonCode.INSTRUCTION_LIKE_FACT_EXCLUDED)
    if facts.solicitation_like_excluded:
        review_reasons.append(ReasonCode.SOLICITATION_LIKE_FACT_EXCLUDED)
    if facts.monetary_excluded:
        review_reasons.append(ReasonCode.MONETARY_FACT_EXCLUDED)
    if facts.contact_like_excluded:
        review_reasons.append(ReasonCode.CONTACT_LIKE_FACT_EXCLUDED)
    if facts.donor_identifier_excluded:
        review_reasons.append(ReasonCode.DONOR_IDENTIFIER_FACT_EXCLUDED)
    if facts.policy_control_like_excluded:
        review_reasons.append(ReasonCode.POLICY_CONTROL_LIKE_FACT_EXCLUDED)
    if facts.giving_history_excluded:
        review_reasons.append(ReasonCode.GIVING_HISTORY_FACT_EXCLUDED)

    disposition = PolicyDisposition.REVIEW if review_reasons else PolicyDisposition.ALLOW
    return PolicyDecision(
        disposition=disposition,
        generation_allowed=True,
        reason_codes=review_reasons,
        ask=ask,
        eligible_facts=tuple(facts.eligible),
        excluded_fact_ids=facts.excluded_ids,
    )
