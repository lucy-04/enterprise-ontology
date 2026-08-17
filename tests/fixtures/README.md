# Fixtures

Small, committed slices of the real corpus so Track B can build extractors
without waiting on Track A's full pipeline (`CLAUDE.md` §14.5). `data/` is
gitignored and 500K documents cannot go through GitHub; these can.

## `sample_docs/`

20 real documents per source, straight from EnterpriseRAG-Bench release v1.0.0,
unmodified.

All nine sources, 20 documents each, 180 total (~1.5 MB). Every source in
`CLAUDE.md` §7.4 is represented, so an extractor can be tested against all of
them without downloading anything.

Filenames are the originals: `dsid_<32hex>__<semantic-slug>.txt`. The part
before the double underscore is the `doc_id`, and it is what
`expected_doc_ids` in `questions.jsonl` refers to — so these fixtures can be
checked against real gold answers.

**Read `CLAUDE.md` §7.4 before writing an extractor against these.** The nine
sources here have genuinely different shapes: gmail has real RFC headers, slack
has `handle (team):` speaker lines, jira is prose with recurring `Role (Name):`
patterns, and confluence is largely unstructured prose.

## `normalized_sample.parquet`

Not yet generated. Track A produces it once the A2 normalizers land — the same
documents in the `NormalizedDoc` shape from `src/common/schemas.py`. That is
Track B's development input for the rest of the build.

## Getting more data

These fixtures are for fast iteration, not for real runs. For full-scale work:

```bash
just fetch-data --slices 1   # ~5,000 docs per source, minutes
just fetch-data              # full corpus, 1.26 GB, hours
```
