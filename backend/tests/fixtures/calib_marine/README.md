# Reservoir ingest fixtures

**These are synthesised, not captured.** The plan (§14 op 3b) asks for the raw
bytes of a real calib.org response; calib.org was not reachable from the
environment this was built in, so what is here instead is a fixture
constructed to carry every property §2 of the plan documents about the real
one:

- both encodings in one response, per field — `Håkansson` as correct UTF-8
  beside `Thybor\xf8n` as raw cp1252, which is what makes a per-field decode
  necessary rather than a per-document one;
- a byte that is valid in neither UTF-8 nor cp1252 (0x81), which is the third
  branch of `decode_field` and is reachable in the real data;
- all 25 feeding spellings, including the five compound values the source
  truncates at sixteen characters;
- rows carrying a ΔR with no uncertainty on it.

What it therefore does NOT test is the shape of calib.org's actual HTML table,
because that shape is unknown here. That is exactly why
`reservoir_ingest.fetch_records` is unimplemented rather than guessed: see its
docstring for the contract, and replace this fixture with a captured response
when one is available.

`records.py` builds the records rather than storing a blob, so what each row
is meant to demonstrate is readable beside it.
