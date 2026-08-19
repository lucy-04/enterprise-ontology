"""Classify a raw surface form into an ontology entity type.

The normalizer hands us surface forms (author_refs, mention_refs) that mix
people, emails, bots, roles and org names all together — e.g. a Jira doc's
`roles` field is "Aisha,Liam,Mei Chen,Verity Labs" (three people and one
company). Before we can emit a typed Mention we have to guess which is which.

These are heuristics, deliberately conservative. Rule-based extraction is
allowed to be imperfect: the two-layer design tolerates some graph noise, and
entity resolution (B3) + the conflict pass (B4) clean up behind it. When unsure,
prefer returning None (skip) over emitting a wrong-typed node.
"""

from __future__ import annotations

import re

from src.ingest.normalize import EMAIL_RE, NOT_A_SPEAKER

# Role abbreviations (from the corpus's inline "Name (AE)" pattern) + a few words.
ROLE_TOKENS = {
    "ae", "se", "pm", "em", "sre", "csm", "am", "swe", "ta", "tam",
    "support", "oncall", "on-call", "runtime oncall", "sales", "security",
    "eng", "engineering", "ops", "legal", "finance", "procurement",
}

# Automated actors. Kept as Bot, never Person (CLAUDE.md §7.4 trap #2).
_BOT_RE = re.compile(r"(?:^|[-_ ])bot\b|bot$|playbot$|incidentbot|opsplaybot", re.IGNORECASE)
_KNOWN_BOTS = {
    "incident-bot", "deploy-bot", "ops-bot", "triage-bot", "release-bot",
    "incidentbot", "opsplaybot", "pagerbot", "ci-bot", "alertbot",
}

# Company-name suffixes — a cheap "this is an Organization" signal.
_ORG_SUFFIX_RE = re.compile(
    r"\b(labs?|bank|inc|inc\.|llc|ltd|corp|co|group|systems?|health|"
    r"technologies|tech|ai|software|solutions|partners|capital|financial|"
    r"security|networks?|cloud|data|analytics|robotics|bio|pharma)\b",
    re.IGNORECASE,
)

# Tokens that slip out of prose but are never entities. Extends NOT_A_SPEAKER
# with a few multiword phrases the header parser leaves behind.
JUNK = {
    "recent activity", "recent", "activity", "timeline", "profile", "customer",
    "customers", "team", "attendees", "unknown", "n/a", "na", "tbd", "someone",
    "everyone", "all", "here", "channel", "thread", "auto-summary",
}

_HANDLE_RE = re.compile(r"^[a-z][\w.\-]{1,30}$")           # jin, maria.s, lena-sales
_NAME_RE = re.compile(r"^[A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,3}$")  # Alex K, Mei Chen


def is_email(token: str) -> bool:
    return bool(EMAIL_RE.fullmatch(token.strip()))


def is_bot(token: str) -> bool:
    low = token.strip().lower()
    return low in _KNOWN_BOTS or bool(_BOT_RE.search(low))


def is_role(token: str) -> bool:
    return token.strip().lower() in ROLE_TOKENS


def looks_like_org(token: str) -> bool:
    t = token.strip()
    if len(t.split()) >= 2 and _ORG_SUFFIX_RE.search(t):
        return True
    # single capitalized word ending in an org suffix, e.g. "Redwood" is NOT caught
    # here on purpose (too ambiguous) — orgs are mostly multiword in this corpus.
    return False


def is_junk(token: str) -> bool:
    # Reuse the normalizer's prose-label stopword set (§7.4) plus our extras, so
    # "Impact", "Owner", "Status" etc. never survive as entities.
    low = token.strip().lower()
    return low in JUNK or low in NOT_A_SPEAKER or len(low) < 2


def email_domain(token: str) -> str:
    m = re.search(r"@([\w.\-]+)", token)
    return m.group(1).lower() if m else ""


def is_internal_email(token: str) -> bool:
    """Redwood addresses are internal staff; anything else is external.

    The corpus uses both redwood.ai and redwood.com, so match any redwood.* —
    otherwise redwood.com senders spawn a bogus external "Redwood" org.
    """
    return "redwood" in email_domain(token)


def is_proper_name(token: str) -> bool:
    """A capitalized 1-4 word name ("Aisha", "Mei Chen"), not a role or junk label.

    Used by the "X (Y)" paren parser to reject prose like "Steps to reproduce
    (staging):" — a real person side is always a Capitalized name in this corpus.
    """
    t = (token or "").strip()
    return bool(_NAME_RE.match(t)) and not is_junk(t) and not is_role(t)


def classify(token: str, *, allow_person: bool = True) -> str | None:
    """Best-guess ontology entity_type for one surface form, or None to skip.

    allow_person=False is used by sources that must not invent people (linear,
    github, google_drive have no reliable person fields — CLAUDE.md §7.4).
    """
    t = (token or "").strip()
    if is_junk(t):
        return None
    if is_email(t):
        return "alias"          # an email is a surface form; its owner Person is emitted separately
    if is_bot(t):
        return "bot"
    if is_role(t):
        return "role"
    if looks_like_org(t):
        return "organization"
    if not allow_person:
        return None
    if _NAME_RE.match(t) or _HANDLE_RE.match(t):
        return "person"
    return None
