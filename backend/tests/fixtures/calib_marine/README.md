# Reservoir ingest fixture

`query_response.html` is **real**: 28 KB of the bytes calib.org's generalized
query interface actually returned, captured on 2026-09-14 from

    POST http://calib.org/marine/query/query.php
    query=<services.reservoir_ingest.SOURCE_QUERY>

which answered 200 with 2060 records. What is here is a SUBSET of those rows,
reassembled inside the response's own preamble and table markup — every byte
of every row is the source's, and the only edit is the declared record count
in the `N records` line, changed to match the rows kept.

Kept deliberately, not sampled:

- **all 60 rows carrying a field that fails a strict UTF-8 decode** — the
  `Thybor\xf8n` case, raw cp1252;
- **4 rows whose non-ASCII fields are correct multi-byte UTF-8** — the
  `H\xc3\xa5kansson` case. Both kinds in one response is the whole reason
  `decode_field` works per field rather than per document, so a fixture
  carrying only one of them would not test the thing;
- **all 26 distinct `feeding` spellings**, including the five compound
  judgements the source truncates at sixteen characters and the one that
  arrives with a leading non-breaking space;
- **3 rows carrying a ΔR with no uncertainty on it**, which is a real shape in
  this database and one a weighted mean cannot use;
- **3 rows whose longitude is outside ±180** — the Chatham Islands at 183.5 and
  Fiji at −181, real places written with an unwrapped longitude, which the
  ingest refused outright until it was first run against the whole database;
- **the points behind §2.3's three published results**, and **every neighbour of
  Strangford Lough out to N = 10**, so the plan's own acceptance figure for the
  lookup (−209 ± 44 at N = 5) is an assertion in `tests/test_reservoir.py`
  rather than a note.

The whole 2060-row response is not committed: at 1.0 MB it would be forty
times the rest of the fixtures put together, and nothing in it that these 85
rows do not already carry is exercised by a test.

## Re-capturing it

Needs network. One request, and please keep it to one — this is a courtesy
interface on somebody else's Apache/PHP host, not an API:

```python
from app.services.reservoir_ingest import fetch_records
records, declared = fetch_records()
```

`extract_records` will refuse a response whose header row is not
`EXPECTED_COLUMNS`, which is the check that catches the source changing shape
rather than letting it be read positionally.

## What the capture corrected

Two numbers in the plan's §2, measured on 2026-09-06 and stale by the time
this was captured: **60** fields fail strict UTF-8, not 54, and there are
**26** distinct feeding spellings, not 25. The normalisation table built from
the plan's prose nevertheless folds the real 2060 rows into exactly the class
counts §2.7 tabulates, with nothing unrecognised — see
`tests/test_reservoir_ingest.py`.
