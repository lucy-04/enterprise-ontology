"""Entity-resolution scaffolding (B3).

Entity resolution = deciding that several mentions ("Sam", "@soham",
"S. Ratnaparkhi") are the same real entity, and merging them into one canonical
node without throwing away any surface form. This module holds the shared pieces
the Splink pipeline builds on; the real matching logic lands in src/resolve/.

Kept deliberately small for night one — enough that import paths exist and the
normalization helpers are shared, not enough to prejudge the Splink model.
"""

from __future__ import annotations

import re
import unicodedata

# The three confidence bands (CLAUDE.md §11 B3). Splink gives each candidate pair
# a match probability; where it lands decides what happens:
#   >= HIGH        -> auto-merge, no LLM
#   <  LOW         -> keep separate, no LLM
#   in [LOW, HIGH) -> the ambiguous middle band -> LLM adjudicates with context
AUTO_MERGE_THRESHOLD = 0.92
KEEP_SEPARATE_THRESHOLD = 0.55


def normalize_name(surface: str) -> str:
    """Lowercased, accent-stripped, punctuation-flattened form for blocking/compare.

    "S. Ratnaparkhi" and "s ratnaparkhi" collapse to the same key so they can be
    compared at all. This is a comparison key only — the original surface form is
    always preserved as an alias, never overwritten.
    """
    s = unicodedata.normalize("NFKD", surface)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9@._+\- ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def surname_key(surface: str) -> str:
    """Blocking key: the last token of a normalized name.

    "Blocking" means only comparing mentions that share a cheap key, instead of
    comparing all N^2 pairs. Surname is a strong, cheap blocker for people.
    """
    parts = normalize_name(surface).split()
    return parts[-1] if parts else ""


def email_localpart(surface: str) -> str:
    """The bit before @ in an email — another strong blocking key."""
    m = re.match(r"\s*([^@\s]+)@", surface)
    return m.group(1).lower() if m else ""


def handle_stem(surface: str) -> str:
    """Normalize a chat handle for blocking: drop leading @, team suffix, dots.

    "@maria.s", "maria (oncall)", "maria.s:" all stem toward "maria".
    """
    s = surface.strip().lstrip("@")
    s = re.split(r"[(:]", s)[0]          # cut a "(team)" or trailing colon
    s = normalize_name(s).replace(".", " ")
    return s.split(" ")[0] if s else ""
