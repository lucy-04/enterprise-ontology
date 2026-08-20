"""Per-source rule-based extractors (B1).

One class per source. Each turns NormalizedDoc rows into typed Mention +
Relation candidates using the patterns measured in CLAUDE.md §7.4 and the shared
regexes Track A's normalizer already exposes (reused, not re-written — §7.4).

Design notes that apply everywhere:
  - ARTIFACT NODES USE THEIR NATURAL KEY AS THE SURFACE FORM. A Ticket's
    surface_form is its id ("ENG-30521"), a Meeting's is its ff_ id. That is what
    makes cross-source joins deterministic: the Jira doc that owns ENG-30521 and
    the Slack message that cites it both emit a mention with surface_form
    "ENG-30521", so entity resolution (exact-key path) collapses them to one node
    linking both documents. This is the multi-hop goldmine.
  - EVERY DOC EMITS A Document MENTION — the provenance anchor that SENT /
    REFERENCES / MENTIONS edges point at.
  - ALIASES ARE NOT EMITTED HERE. Each Entity row (built in B3) carries its
    aliases/handles/emails as lists; Track A's loader turns those into Alias
    nodes. B1 only needs to emit the typed entity mentions themselves.
  - Extra edge properties the candidate schema can't hold (recipient to/cc,
    a commitment string) are stashed in the relation's evidence_snippet.
"""

from __future__ import annotations

import re

from src.common.schemas import NormalizedDoc
from src.extract.base import ExtractionResult, Extractor
from src.extract.classify import (
    classify,
    email_domain,
    is_bot,
    is_internal_email,
    is_proper_name,
    is_role,
)
from src.ingest.normalize import (
    ADDR_RE,
    AT_MENTION_RE,
    FIREFLIES_ACTION_RE,
    INLINE_ROLE_RE,
    PAREN_SPEAKER_RE,
    PR_RE,
    SLACK_SPEAKER_RE,
    TICKET_RE,
)

# ff_ meeting id, e.g. ff_20260304_8b2f1a (referenced by HubSpot). Fireflies docs
# themselves do NOT carry this id (verified), so these become standalone Meeting
# nodes keyed by the id string — see the known-gap note in the Fireflies class.
FF_ID_RE = re.compile(r"\b(ff_\d{6,8}_[0-9a-f]+)\b")


def _project_of(ticket_id: str) -> str:
    """The project prefix of a ticket id: 'ENG-30521' -> 'ENG'."""
    return ticket_id.split("-", 1)[0].upper()


def _cross_ref_ids(doc: NormalizedDoc) -> tuple[list[str], list[str], list[str]]:
    """Ticket ids, PR ids and ff_ ids found in raw_metadata.cross_refs + slug + body.

    cross_refs is pre-parsed by the normalizer; we still sweep the body so ids
    mentioned only in prose are caught too.
    """
    md = doc.raw_metadata or {}
    blob = " ".join(str(md.get(k, "")) for k in ("cross_refs", "slug")) + "\n" + doc.body
    tickets = sorted(set(TICKET_RE.findall(blob)))
    prs = sorted({f"PR#{n}" for n in PR_RE.findall(blob)})
    # cross_refs also stores PRs as "PR#7242"
    prs += sorted({m for m in re.findall(r"PR#\d+", str(md.get("cross_refs", "")))})
    meetings = sorted(set(FF_ID_RE.findall(blob)))
    return tickets, sorted(set(prs)), meetings


class _SourceExtractor(Extractor):
    """Adds convenience emitters shared by the concrete source extractors."""

    def doc_mention(self, doc, res):
        m = self.emit_mention(doc, doc.title or doc.doc_id, "document",
                              context=(doc.title or "")[:120])
        if m:
            res.mentions.append(m)
        return m

    def add(self, res, mention):
        if mention:
            res.mentions.append(mention)
        return mention

    def link(self, res, doc, src, dst, rel, evidence=""):
        r = self.emit_relation(doc, src, dst, rel, evidence=evidence)
        if r:
            res.relations.append(r)
        return r

    def emit_cross_refs(self, doc, docm, res):
        """Ticket/PR/Meeting nodes from cross_refs + a REFERENCES edge from this doc.

        Also links ticket->project (PART_OF) and, when this doc itself owns a
        work item, ticket<->ref RELATES_TO edges.
        """
        tickets, prs, meetings = _cross_ref_ids(doc)
        for tid in tickets:
            tm = self.add(res, self.emit_mention(doc, tid, "ticket"))
            pm = self.add(res, self.emit_mention(doc, _project_of(tid), "project"))
            self.link(res, doc, docm, tm, "REFERENCES", evidence=f"cross-ref {tid}")
            self.link(res, doc, tm, pm, "PART_OF")
        for pr in prs:
            prm = self.add(res, self.emit_mention(doc, pr, "pull_request"))
            self.link(res, doc, docm, prm, "REFERENCES", evidence=f"cross-ref {pr}")
        for mid in meetings:
            mm = self.add(res, self.emit_mention(doc, mid, "meeting"))
            self.link(res, doc, docm, mm, "REFERENCES", evidence=f"cross-ref {mid}")
        return tickets, prs, meetings


# ---------------------------------------------------------------------------
# GMAIL — richest, highest-confidence Person source.
# ---------------------------------------------------------------------------
# A recipient value in a threaded email can arrive as "\nTo: Ben Carter" (a
# literal "\n" escape plus the header label leaking in from the raw dump). Strip
# escape sequences and any leading From/To/Cc/Bcc label so the name is clean.
_HEADER_LABEL_RE = re.compile(r"^\s*(?:from|to|cc|bcc)\s*:\s*", re.I)

# The same label can also appear part-way through a value, because collapsing the
# newlines out of a folded header run joins two headers into one string:
#   "marissa.cole@redwood.ai Cc: FreightNorth Customs Broker"
# Everything from that label onward belongs to the NEXT header, not this name, so
# the value is truncated there rather than merely de-prefixed. Left unhandled it
# reaches the graph as a person alias and shows up on screen.
_EMBEDDED_LABEL_RE = re.compile(r"\s+(?:from|to|cc|bcc)\s*:\s*", re.I)


def _clean_header_name(name: str) -> str:
    s = name.replace("\\n", " ").replace("\\r", " ").replace("\\t", " ")
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    s = _HEADER_LABEL_RE.sub("", s)
    s = _EMBEDDED_LABEL_RE.split(s, maxsplit=1)[0]
    return s.strip()


class GmailExtractor(_SourceExtractor):
    source_type = "gmail"

    def extract_doc(self, doc: NormalizedDoc) -> ExtractionResult:
        res = ExtractionResult()
        docm = self.doc_mention(doc, res)
        md = doc.raw_metadata or {}

        def people_from_header(value: str):
            out = []
            for name, addr, bare in ADDR_RE.findall(value or ""):
                display = (name or "").strip() or (addr or bare or "").strip()
                email = (addr or bare or "").strip()
                display = _clean_header_name(display)
                if not display:
                    continue
                pm = self.emit_mention(doc, display, "person", context=value[:120])
                out.append((pm, email))
            return [(p, e) for p, e in out if p]

        # From -> SENT
        senders = people_from_header(md.get("from", ""))
        for pm, email in senders:
            self.add(res, pm)
            self.link(res, doc, pm, docm, "SENT")
            self._maybe_org(doc, pm, email, res)

        # To / Cc -> RECEIVED (recipient kind stashed in evidence)
        for kind in ("to", "cc"):
            for pm, email in people_from_header(md.get(kind, "")):
                self.add(res, pm)
                self.link(res, doc, pm, docm, "RECEIVED", evidence=kind)
                self._maybe_org(doc, pm, email, res)
        return res

    def _maybe_org(self, doc, person_m, email, res):
        if email and not is_internal_email(email):
            dom = email_domain(email)
            org_name = dom.split(".")[0].title() if dom else ""
            orgm = self.emit_mention(doc, org_name, "organization", context=email)
            if orgm:
                self.add(res, orgm)
                self.link(res, doc, person_m, orgm, "WORKS_FOR", evidence=email)


# ---------------------------------------------------------------------------
# SLACK — backbone of the people graph at scale.
# ---------------------------------------------------------------------------
class SlackExtractor(_SourceExtractor):
    source_type = "slack"

    def extract_doc(self, doc: NormalizedDoc) -> ExtractionResult:
        res = ExtractionResult()
        docm = self.doc_mention(doc, res)
        md = doc.raw_metadata or {}

        channel_name = md.get("channel") or doc.title
        chan = self.add(res, self.emit_mention(doc, channel_name, "channel"))

        # Teams declared in metadata become Team nodes up front.
        for team in str(md.get("teams", "")).split(","):
            self.add(res, self.emit_mention(doc, team.strip(), "team"))

        # Parse "speaker (team):" lines from the body for speaker->team membership.
        seen_speaker = set()
        for speaker, team in SLACK_SPEAKER_RE.findall(doc.body):
            speaker = speaker.strip()
            key = speaker.lower()
            etype = "bot" if is_bot(speaker) else "person"
            sm = self.emit_mention(doc, speaker, etype, context=f"{speaker} in {channel_name}")
            if not sm:
                continue
            if key not in seen_speaker:
                seen_speaker.add(key)
                self.add(res, sm)
                self.link(res, doc, sm, chan, "POSTED_IN")
            if team and etype == "person":
                tm = self.add(res, self.emit_mention(doc, team.strip(), "team"))
                self.link(res, doc, sm, tm, "MEMBER_OF", evidence=f"{speaker} ({team})")

        # @mentions inside message text -> Document MENTIONS Person.
        for handle in set(AT_MENTION_RE.findall(doc.body)):
            etype = "bot" if is_bot(handle) else "person"
            pm = self.add(res, self.emit_mention(doc, handle, etype, context="@mention"))
            self.link(res, doc, docm, pm, "MENTIONS", evidence=f"@{handle}")
        return res


# ---------------------------------------------------------------------------
# The "X (Y)" family shares one parser across jira / fireflies / hubspot.
# ---------------------------------------------------------------------------
def _paren_people(extractor, doc, res, context=""):
    """Emit Person + Role mentions from 'Name (Role)' / 'Role (Name)' pairs.

    Returns the list of (person_mention, role_or_none) so callers can add edges.
    """
    out = []
    for left, right in PAREN_SPEAKER_RE.findall(doc.body):
        left, right = left.strip(), right.strip()
        # A real person side is always a Capitalized proper name; the other side,
        # if it is a role, becomes the Role. If NEITHER side is a proper name the
        # match is prose ("Steps to reproduce (staging):") and is skipped.
        left_name, right_name = is_proper_name(left), is_proper_name(right)
        if left_name and not right_name:
            person_s, role_s = left, (right if is_role(right) else None)
        elif right_name and not left_name:
            person_s, role_s = right, (left if is_role(left) else None)
        elif left_name and right_name:
            person_s, role_s = left, None       # two names: take the speaker (left)
        else:
            continue                            # no proper name -> not a person line
        pm = extractor.emit_mention(doc, person_s, "person", context=context or left)
        if not pm:
            continue
        extractor.add(res, pm)
        rm = None
        if role_s:
            rm = extractor.add(res, extractor.emit_mention(doc, role_s, "role"))
            extractor.link(res, doc, pm, rm, "HAS_ROLE", evidence=f"{person_s} ({role_s})")
        out.append((pm, rm))
    # inline "Name (AE)" style
    for name, role in INLINE_ROLE_RE.findall(doc.body):
        pm = extractor.add(res, extractor.emit_mention(doc, name.strip(), "person"))
        rm = extractor.add(res, extractor.emit_mention(doc, role.strip(), "role"))
        extractor.link(res, doc, pm, rm, "HAS_ROLE", evidence=f"{name} ({role})")
        if pm:
            out.append((pm, rm))
    return out


# ---------------------------------------------------------------------------
# JIRA — prose, but pattern-rich. The ticket + cross-ref workhorse.
# ---------------------------------------------------------------------------
class JiraExtractor(_SourceExtractor):
    source_type = "jira"

    def extract_doc(self, doc: NormalizedDoc) -> ExtractionResult:
        res = ExtractionResult()
        docm = self.doc_mention(doc, res)
        md = doc.raw_metadata or {}

        # The ticket this doc IS (id in the slug) -> Ticket + PART_OF Project.
        own = TICKET_RE.findall(str(md.get("slug", "")))
        own_ticket_m = None
        for tid in sorted(set(own)):
            own_ticket_m = self.add(res, self.emit_mention(doc, tid, "ticket"))
            pm = self.add(res, self.emit_mention(doc, _project_of(tid), "project"))
            self.link(res, doc, own_ticket_m, pm, "PART_OF")

        tickets, prs, meetings = self.emit_cross_refs(doc, docm, res)
        # This doc's own ticket relates to the artifacts it cross-references.
        if own_ticket_m:
            for tid in tickets:
                if tid in own:
                    continue
                tm = self.emit_mention(doc, tid, "ticket")
                self.link(res, doc, own_ticket_m, tm, "RELATES_TO", evidence=f"{tid}")

        # People + roles from the prose; owner: Name -> OWNS.
        _paren_people(self, doc, res, context="jira ticket")
        for owner in re.findall(r"[Oo]wner:\s*([A-Z][A-Za-z.'\- ]{1,40})", doc.body):
            pm = self.emit_mention(doc, owner.strip(), "person")
            if pm and own_ticket_m:
                self.add(res, pm)
                self.link(res, doc, pm, own_ticket_m, "OWNS", evidence=f"owner: {owner.strip()}")

        # Orgs named in prose (customer references) -> MENTIONS.
        for ref in (doc.mention_refs or []):
            if classify(ref) == "organization":
                om = self.add(res, self.emit_mention(doc, ref, "organization"))
                self.link(res, doc, docm, om, "MENTIONS")
        return res


# ---------------------------------------------------------------------------
# LINEAR — ticket + cross-refs only; no people (no author field).
# ---------------------------------------------------------------------------
class LinearExtractor(_SourceExtractor):
    source_type = "linear"

    def extract_doc(self, doc: NormalizedDoc) -> ExtractionResult:
        res = ExtractionResult()
        docm = self.doc_mention(doc, res)
        md = doc.raw_metadata or {}
        own = TICKET_RE.findall(str(md.get("slug", "")))
        own_m = None
        for tid in sorted(set(own)):
            own_m = self.add(res, self.emit_mention(doc, tid, "ticket"))
            pm = self.add(res, self.emit_mention(doc, _project_of(tid), "project"))
            self.link(res, doc, own_m, pm, "PART_OF")
        tickets, prs, meetings = self.emit_cross_refs(doc, docm, res)
        if own_m:
            for tid in tickets:
                if tid not in own:
                    tm = self.emit_mention(doc, tid, "ticket")
                    self.link(res, doc, own_m, tm, "RELATES_TO")
        return res


# ---------------------------------------------------------------------------
# GITHUB — PR + cross-refs; strong for artifacts, no people.
# ---------------------------------------------------------------------------
class GithubExtractor(_SourceExtractor):
    source_type = "github"

    def extract_doc(self, doc: NormalizedDoc) -> ExtractionResult:
        res = ExtractionResult()
        docm = self.doc_mention(doc, res)
        md = doc.raw_metadata or {}

        # PR id lives in the slug, e.g. "pr-99501-...".
        pr_m = None
        m = re.search(r"pr[-#]?(\d+)", str(md.get("slug", "")), re.IGNORECASE)
        if m:
            pr_m = self.add(res, self.emit_mention(doc, f"PR#{m.group(1)}", "pull_request"))

        tickets, prs, meetings = self.emit_cross_refs(doc, docm, res)
        # PR -> RESOLVES/RELATES tickets it references.
        if pr_m:
            fixes = set(re.findall(r"(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s+"
                                   r"((?:SUP|TRACK|OPS|DOC|ENG|INFRA|SEC)-\d+)",
                                   doc.body, re.IGNORECASE))
            for tid in tickets:
                tm = self.emit_mention(doc, tid, "ticket")
                rel = "RESOLVES" if tid in fixes else "RELATES_TO"
                self.link(res, doc, pr_m, tm, rel, evidence=tid)
        return res


# ---------------------------------------------------------------------------
# FIREFLIES — person + org + commitment on one line.
# ---------------------------------------------------------------------------
class FirefliesExtractor(_SourceExtractor):
    source_type = "fireflies"

    def extract_doc(self, doc: NormalizedDoc) -> ExtractionResult:
        res = ExtractionResult()
        docm = self.doc_mention(doc, res)

        # This corpus's fireflies docs carry NO ff_ id (verified), so the Meeting
        # node is keyed by doc_id. KNOWN GAP: HubSpot's ff_ references therefore
        # can't join to these transcripts by id — a later fuzzy (date+customer)
        # pass could, but B1 stays deterministic and leaves them as two nodes.
        meeting_m = self.add(res, self.emit_mention(doc, doc.doc_id, "meeting",
                                                    context=doc.title[:120]))
        if meeting_m:
            self.link(res, doc, docm, meeting_m, "ABOUT")

        # Org from the meeting title (customer name usually leads the title).
        for ref in (doc.mention_refs or []):
            c = classify(ref)
            if c == "organization" and meeting_m:
                om = self.add(res, self.emit_mention(doc, ref, "organization"))
                self.link(res, doc, meeting_m, om, "INVOLVES")

        # Attendees / roles -> PARTICIPATED_IN.
        for pm, _rm in _paren_people(self, doc, res, context="meeting"):
            if meeting_m:
                self.link(res, doc, pm, meeting_m, "PARTICIPATED_IN")

        # Action items: "Org (Person) to <do X>".
        for line in doc.body.splitlines():
            m = FIREFLIES_ACTION_RE.match(line)
            if not m:
                continue
            org_s, person_s = m.group(1).strip(), m.group(2).strip()
            # pattern is "Org (Person) to ..." -> group1=Org, group2=Person
            pm = self.emit_mention(doc, person_s, "person", context=line[:160])
            if not pm:
                continue
            self.add(res, pm)
            self.link(res, doc, pm, docm, "ACTION_ITEM", evidence=line.strip()[:160])
            om = self.add(res, self.emit_mention(doc, org_s, "organization"))
            if om and meeting_m:
                self.link(res, doc, meeting_m, om, "INVOLVES")
        return res


# ---------------------------------------------------------------------------
# HUBSPOT — accounts + cross-refs to meetings.
# ---------------------------------------------------------------------------
class HubspotExtractor(_SourceExtractor):
    source_type = "hubspot"

    def extract_doc(self, doc: NormalizedDoc) -> ExtractionResult:
        res = ExtractionResult()
        docm = self.doc_mention(doc, res)

        # The account is the title (often "Company <Name>"), always an Organization.
        account = re.sub(r"^\s*company\s+", "", doc.title, flags=re.IGNORECASE).strip()
        acct_m = self.add(res, self.emit_mention(doc, account, "organization",
                                                 context="hubspot account"))
        if acct_m:
            self.link(res, doc, docm, acct_m, "ABOUT")
            redwood = self.add(res, self.emit_mention(doc, "Redwood", "organization"))
            self.link(res, doc, acct_m, redwood, "CUSTOMER_OF")

        # People with roles on the account (Jordan (AE), Maya (SE)).
        for pm, _rm in _paren_people(self, doc, res, context=account):
            if acct_m:
                self.link(res, doc, pm, acct_m, "WORKS_FOR", evidence="account team")

        # ff_ meeting cross-refs -> REFERENCES (the key multi-hop link).
        self.emit_cross_refs(doc, docm, res)
        return res


# ---------------------------------------------------------------------------
# CONFLUENCE + GOOGLE_DRIVE — weak graph sources, mostly Layer 1.
# ---------------------------------------------------------------------------
class _WeakDocExtractor(_SourceExtractor):
    def extract_doc(self, doc: NormalizedDoc) -> ExtractionResult:
        res = ExtractionResult()
        docm = self.doc_mention(doc, res)
        # Only the cheap, high-precision signals: "Owner: <team>" -> Team OWNS doc-subject.
        for owner in re.findall(r"[Oo]wner:\s*([A-Za-z][\w\- ]{1,40})", doc.body):
            tm = self.emit_mention(doc, owner.strip(), "team")
            if tm:
                self.add(res, tm)
                self.link(res, doc, tm, docm, "OWNS", evidence=f"Owner: {owner.strip()}")
        # cross-refs still worth capturing where present.
        self.emit_cross_refs(doc, docm, res)
        return res


class ConfluenceExtractor(_WeakDocExtractor):
    source_type = "confluence"


class GoogleDriveExtractor(_WeakDocExtractor):
    source_type = "google_drive"


# ---------------------------------------------------------------------------
# Registry: source_type -> extractor class.
# ---------------------------------------------------------------------------
EXTRACTORS: dict[str, type[_SourceExtractor]] = {
    "gmail": GmailExtractor,
    "slack": SlackExtractor,
    "jira": JiraExtractor,
    "linear": LinearExtractor,
    "github": GithubExtractor,
    "fireflies": FirefliesExtractor,
    "hubspot": HubspotExtractor,
    "confluence": ConfluenceExtractor,
    "google_drive": GoogleDriveExtractor,
}


def get_extractor(source_type: str, **kw) -> _SourceExtractor:
    cls = EXTRACTORS.get(source_type)
    if cls is None:
        raise KeyError(f"no extractor for source_type {source_type!r}")
    return cls(**kw)
