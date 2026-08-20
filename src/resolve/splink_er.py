"""Probabilistic person/org resolution with Splink (B3 core).

Splink implements Fellegi-Sunter probabilistic record linkage, UNSUPERVISED — it
learns how much each field agreement is worth from the data itself, no training
labels (CLAUDE.md §4). We use it to decide that "Sam", "sam", "S. Ratnaparkhi"
and "sam.r@redwood.com" are one person.

The output is a pairwise match probability per candidate pair, which we split
into the three bands from resolve/base.py:
    >= AUTO_MERGE_THRESHOLD   -> merge automatically
    <  KEEP_SEPARATE_THRESHOLD-> definitely different people
    in between                -> the ambiguous middle band, handed to the LLM
                                 adjudicator (B3) if one is configured; otherwise
                                 left unmerged (conservative default).

If Splink cannot fit a model (e.g. too few records, EM fails to converge), we
fall back to a deterministic union-find linker so B3 always produces output.
This module never crashes the pipeline.
"""

from __future__ import annotations

import pandas as pd

from src.resolve.base import (
    AUTO_MERGE_THRESHOLD,
    KEEP_SEPARATE_THRESHOLD,
    email_localpart,
    handle_stem,
    normalize_name,
    surname_key,
)


def build_person_frame(mentions: pd.DataFrame) -> pd.DataFrame:
    """Shape person/bot mentions into the columns Splink compares on.

    Built with plain Python (not pandas .where/.map): pandas insists on storing a
    float NaN for the "no email here" case, and NaN is truthy, which silently
    breaks the name<->email bridge. Building the columns as lists of str-or-None
    keeps every empty blocking key as a real None.
    """
    def none_if_blank(v: str) -> str | None:
        return v or None

    rows = []
    for mid, surface, source, doc in zip(
        mentions["mention_id"], mentions["surface_form"].astype(str),
        mentions["source_type"], mentions["doc_id"], strict=False,
    ):
        is_email = "@" in surface
        rows.append({
            "unique_id": mid,
            "name_norm": normalize_name(surface),
            "surname": none_if_blank(surname_key(surface)),
            "email": normalize_name(surface) if is_email else None,
            "email_local": none_if_blank(email_localpart(surface)) if is_email else None,
            "handle_stem": none_if_blank(handle_stem(surface)),
            "source_type": source,
            "doc_id": doc,
        })
    return pd.DataFrame(rows, columns=[
        "unique_id", "name_norm", "surname", "email", "email_local",
        "handle_stem", "source_type", "doc_id",
    ])


def _splink_pairs(frame: pd.DataFrame) -> pd.DataFrame | None:
    """Run Splink; return a pairwise-predictions DataFrame or None on failure."""
    try:
        import splink.comparison_library as cl
        from splink import DuckDBAPI, Linker, SettingsCreator, block_on

        settings = SettingsCreator(
            link_type="dedupe_only",
            blocking_rules_to_generate_predictions=[
                block_on("surname"),
                block_on("email_local"),
                block_on("handle_stem"),
            ],
            comparisons=[
                cl.NameComparison("name_norm"),
                cl.ExactMatch("email").configure(term_frequency_adjustments=True),
                cl.ExactMatch("handle_stem"),
            ],
            retain_intermediate_calculation_columns=True,
        )
        linker = Linker(frame, settings, db_api=DuckDBAPI())

        # Unsupervised parameter estimation. The prior ("how likely are two random
        # records the same person") must be seeded from signals that actually occur
        # in the data: an exact normalized name or an exact email. Seeding it from
        # email alone collapses the prior (only ~5% of person mentions carry an
        # email) and suppresses every posterior below the merge threshold.
        linker.training.estimate_probability_two_random_records_match(
            ["l.name_norm = r.name_norm", "l.email_local = r.email_local"], recall=0.8
        )
        linker.training.estimate_u_using_random_sampling(max_pairs=1_000_000)
        for rule in (block_on("surname"), block_on("handle_stem")):
            try:
                linker.training.estimate_parameters_using_expectation_maximisation(rule)
            except Exception:
                pass  # one blocking rule failing to converge is not fatal

        preds = linker.inference.predict(threshold_match_probability=KEEP_SEPARATE_THRESHOLD)
        return preds.as_pandas_dataframe()
    except Exception as exc:  # noqa: BLE001
        print(f"  [splink] falling back to deterministic linker: {exc!r}")
        return None


def _deterministic_pairs(frame: pd.DataFrame) -> pd.DataFrame:
    """Fallback: pair records that share an email-local, handle-stem, or exact name.

    Emits match_probability 1.0 for a shared email/handle (strong) and 0.95 for
    an exact normalized name (still strong, but names collide more), so the same
    banding logic downstream applies unchanged.
    """
    rows = []
    for key_col, prob in (("email_local", 1.0), ("handle_stem", 0.97), ("name_norm", 0.95)):
        for _key, grp in frame.dropna(subset=[key_col]).groupby(key_col):
            ids = list(grp["unique_id"])
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    rows.append((ids[i], ids[j], prob))
    return pd.DataFrame(rows, columns=["unique_id_l", "unique_id_r", "match_probability"])


def _name_locals(name_norm: str) -> set[str]:
    """Email-local forms a full name could plausibly produce.

    "karthik iyer" -> {karthik.iyer, karthik_iyer, karthikiyer, kiyer, karthik}
    Used to bridge a name mention to an email mention deterministically — the
    "Karthik Iyer" (Slack) == "karthik_iyer@redwood.com" (Gmail) link that no
    string comparison of the surface forms would ever make.
    """
    parts = [p for p in name_norm.replace(".", " ").split() if p]
    if len(parts) < 2:
        return set()   # a single token ("ben") matches every Ben — too loose, skip
    first, last = parts[0], parts[-1]
    # only multi-component locals: specific enough to be high-precision. Bare
    # "first"/"last" are deliberately excluded (they merged Ben Carter + Ben Turner).
    return {
        f"{first}.{last}", f"{first}_{last}", f"{first}{last}",
        f"{first[0]}{last}", f"{first}{last[0]}",
    }


def bridge_name_email_handle(frame: pd.DataFrame, uf: _UnionFind) -> int:
    """Union email/handle mentions to name mentions by derived local part.

    High precision: only fires on an exact match between an email/handle local
    and a form the name could generate. Runs after Splink to catch the
    cross-surface-form identities Splink's name comparison structurally cannot.
    Returns the number of unions made.
    """
    # NB: pandas may store a missing blocking key as a float NaN (its new str
    # dtype coerces None -> NaN), and NaN is truthy — so test presence with
    # _val() (returns a clean str or None), never bare truthiness.
    def _val(v) -> str | None:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        s = str(v).strip()
        return s or None

    # index: local form -> list of mention ids that own a full ("first last") name
    name_index: dict[str, list[str]] = {}
    for _, row in frame.iterrows():
        nm = _val(row["name_norm"]) or ""
        if " " in nm and _val(row["email_local"]) is None:   # a multi-token human name
            for loc in _name_locals(nm):
                name_index.setdefault(loc, []).append(row["unique_id"])

    unions = 0
    for _, row in frame.iterrows():
        nm = _val(row["name_norm"]) or ""
        # the local to match on: an email's local, or a single-token handle
        local = _val(row["email_local"])
        if local is None and (not nm or " " not in nm):
            local = _val(row["handle_stem"])
        if local is None:
            continue
        for target in name_index.get(local, []):
            # union() may reject (surname guard); count only merges that happened
            if uf.find(target) != uf.find(row["unique_id"]) and uf.union(target, row["unique_id"]):
                unions += 1
    return unions


class _UnionFind:
    """Union-find with a SURNAME-CONSISTENCY guard.

    Naive transitive closure over Splink's pairs is how the graph over-merges at
    scale: a few false-positive pairs, plus bare-first-name mentions ("Alex")
    acting as bridges, chain dozens of distinct people ("Alex Jenkins", "Alex
    Torres", even "Aisha Patel") into one blob. The guard makes it structurally
    impossible for a cluster to ever hold two *different* surnames: a union that
    would combine two distinct surnames is rejected. Mentions with no surname
    (bare "Alex", a handle) still attach freely, but once a cluster commits to a
    surname, a conflicting one can't join — so the bridge chains are broken.
    """

    def __init__(self, surname_of: dict[str, str | None] | None = None):
        self.parent: dict[str, str] = {}
        self.surnames: dict[str, set[str]] = {}   # authoritative on ROOT nodes
        self._surname_of = surname_of or {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            s = self._surname_of.get(x)
            self.surnames[x] = {s} if s else set()
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:      # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b) -> bool:
        """Merge a and b unless it would put two distinct surnames in one cluster.
        Returns True if merged (or already together), False if rejected."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        combined = self.surnames[ra] | self.surnames[rb]
        if len(combined) > 1:              # two different surnames -> different people
            return False
        self.parent[rb] = ra
        self.surnames[ra] = combined
        return True


def resolve_people(mentions: pd.DataFrame) -> tuple[dict[str, int], list[dict], list[dict]]:
    """Resolve person/bot mentions into clusters.

    Returns:
      cluster_of : mention_id -> integer cluster label
      cluster_rows : rows for clusters.parquet (mention -> cluster, prob, method)
      middle_band : the ambiguous pairs an LLM should adjudicate (B3, optional)
    """
    frame = build_person_frame(mentions)
    pairs = _splink_pairs(frame)
    method = "splink"
    if pairs is None or pairs.empty:
        pairs = _deterministic_pairs(frame)
        method = "deterministic"

    # surname per mention, so the union-find can reject cross-surname merges
    def _clean(v) -> str | None:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        s = str(v).strip()
        return s or None
    surname_of = {row["unique_id"]: _clean(row["surname"])
                  for _, row in frame.iterrows()}

    uf = _UnionFind(surname_of)
    for uid in frame["unique_id"]:
        uf.find(uid)  # ensure every mention is at least a singleton

    # process strongest pairs first, so a cluster commits to its surname before
    # weaker/ambiguous links can attach the wrong one.
    pairs = pairs.sort_values("match_probability", ascending=False)

    prob_of: dict[tuple[str, str], float] = {}
    middle_band: list[dict] = []
    rejected = 0
    for _, row in pairs.iterrows():
        a, b, p = row["unique_id_l"], row["unique_id_r"], float(row["match_probability"])
        prob_of[(a, b)] = p
        if p >= AUTO_MERGE_THRESHOLD:
            if not uf.union(a, b):      # blocked by the surname guard
                rejected += 1
        elif p >= KEEP_SEPARATE_THRESHOLD:
            middle_band.append({"mention_id_l": a, "mention_id_r": b, "match_probability": p})
    if rejected:
        print(f"  [people] surname guard blocked {rejected} cross-surname merges")

    # bridge cross-surface-form identities Splink can't see (name <-> email/handle)
    bridged = bridge_name_email_handle(frame, uf)
    if bridged:
        print(f"  [people] deterministic name<->email/handle bridge merged {bridged} pairs")

    # assign integer labels
    label_of_root: dict[str, int] = {}
    cluster_of: dict[str, int] = {}
    for uid in frame["unique_id"]:
        root = uf.find(uid)
        label_of_root.setdefault(root, len(label_of_root))
        cluster_of[uid] = label_of_root[root]

    cluster_rows = [
        {"mention_id": uid, "cluster": cluster_of[uid],
         "match_probability": 1.0, "method": method}
        for uid in frame["unique_id"]
    ]

    # verify the guard held: no cluster should carry more than one surname
    surnames_by_cluster: dict[int, set[str]] = {}
    for uid in frame["unique_id"]:
        s = surname_of.get(uid)
        if s:
            surnames_by_cluster.setdefault(cluster_of[uid], set()).add(s)
    multi = sum(1 for v in surnames_by_cluster.values() if len(v) > 1)
    tag = "OK" if multi == 0 else f"WARNING {multi} multi-surname clusters"
    print(f"  [people] {len(frame)} mentions -> {len(label_of_root)} clusters "
          f"({method}); {len(middle_band)} middle-band pairs; surname-check: {tag}")
    return cluster_of, cluster_rows, middle_band
