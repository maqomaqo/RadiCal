#!/bin/sh
# Point git at the repository's hooks. Run once per clone.
git config core.hooksPath .githooks && echo "git hooks enabled (.githooks)"
