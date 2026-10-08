# Contributing

## Keep project data out of the repository

RadiCal grew out of fixing problems found in a research project. The code,
tests, docs and commit messages must not name that project's datasets, sites,
lab codes or publications, and must not cite them as the reason for a change.

- **Say what the behaviour is, not where it was seen.** "A tempered chain
  recovers a lobe that an untempered one misses on a two-lobe posterior" stands
  on its own; "this was found on <named dataset>" does not belong.
- **Fixtures use neutral names and lab codes** (`LA10001`, `Hillside`,
  `Dune Section`). Real ages may stay; the labels that identify where they came
  from may not.
- **Public references are fine.** Method papers, calibration curves and
  published reference databases are cited normally. Mark a line that quotes one
  with `names-ok`, or list a file that reproduces public data verbatim in
  `scripts/dataset_names.allow`.

### The check

`scripts/check_dataset_names.py` fails on any blocked term. The terms are kept
as salted hashes in `scripts/dataset_names.sha256`, so the list does not itself
spell out what it guards.

```bash
sh scripts/install-hooks.sh                       # once per clone: runs the check on commit
python3 scripts/check_dataset_names.py            # the whole tracked tree
python3 scripts/check_dataset_names.py --hash TERM  # a line to append for a new blocked term
```

CI runs the same check on every push and pull request, over the new commit
messages as well as the tree, and before the container image is published.
