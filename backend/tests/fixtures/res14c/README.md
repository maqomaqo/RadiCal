# res14c parity fixture

`test_resolution.py::TestParityWithTheReferenceImplementation` compares this
app's resolution profile against the published output of the implementation
the method comes from, [res14c_online](https://github.com/demjanp/res14c_online)
(Demján, after Svetlik et al. 2019). That output is not committed here, so the
test **skips** unless you generate it — a clean checkout still runs green.

Two reasons it is not in the repository. It is 2 MB of derived data, and
res14c_online is GPL-3.0 where this project is CC0: its *method* is what this
app implements (from the paper, composed with this app's own calibration and
HPD code — not translated from that source), and keeping its output out of the
tree keeps that boundary unambiguous.

To generate it:

```bash
git clone https://github.com/demjanp/res14c_online
cd res14c_online/src/res14c_online
# The published CSV ships with the repository; copy it here as-is:
cp web/c14_resolution_data.csv \
   <this repo>/backend/tests/fixtures/res14c/intcal20_u15_reference.csv
```

The file is IntCal20 at `u0 = 15`, half-year steps, its first column in years
AD (negative for BC). The test converts to cal BP itself.

Last measured against it (2026-08-18): mean absolute difference **0.36%** over
1,000–4,000 cal BP, maximum 1.39%.
