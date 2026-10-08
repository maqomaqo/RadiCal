#!/usr/bin/env python3
"""
Fail if the repository names a dataset, site, lab code or publication from the
research project that motivated it.

Why this exists: development work done to fix problems found in a research
project had a habit of citing that project's data as its motivation -- in
comments, test names, fixture files, docs and commit messages. The code should
stand on its own technical reasoning and on public references; the project's
own material stays out of it.

How it works: the blocked terms are NOT stored here in clear (a list of the
names would itself be the leak). `scripts/dataset_names.sha256` holds salted
hashes, and this script hashes every word in the text it checks and compares.

  python3 scripts/check_dataset_names.py             # the whole tracked tree
  python3 scripts/check_dataset_names.py --staged    # what is about to be committed
  python3 scripts/check_dataset_names.py --message FILE|-   # a commit message
  python3 scripts/check_dataset_names.py --hash TERM [TERM ...]   # print list lines

To block a new term, run --hash on it and append the output to
dataset_names.sha256. To let a genuine public citation through, put `names-ok`
on that line (in a comment), or list the file in scripts/dataset_names.allow.

Matches are reported by file and line only, not echoed, so CI logs do not
repeat what they are guarding.
"""
import fnmatch
import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIST = os.path.join(HERE, 'dataset_names.sha256')
ALLOW = os.path.join(HERE, 'dataset_names.allow')
SALT = 'radical-dataset-names:'
MARKER = 'names-ok'
# Terms this long or longer are also caught inside a longer run of letters
# (TermSuffixed); shorter ones only as whole words, so a short term cannot
# fire inside an unrelated word.
SUBSTRING_MIN = 6
SKIP_SUFFIX = ('.png', '.ico', '.svg', '.woff', '.woff2', '.pdf', '.gz', '.zip')
SKIP_EXACT = {'LICENSE', 'frontend/package-lock.json'}

WORD = re.compile(r'[A-Za-z]+')
CODE = re.compile(r'(?<![A-Za-z0-9])([A-Za-z]{2,5})[- ]?(\d{3,6})(?![0-9])')
CAMEL = re.compile(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+')


def digest(text):
    return hashlib.sha256((SALT + text.lower()).encode('utf-8')).hexdigest()


def load_list():
    words, subs, codes = set(), {}, set()
    if not os.path.exists(LIST):
        return words, subs, codes
    for raw in open(LIST, encoding='utf-8'):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        kind, size, h = line.split(':')
        if kind == 'w':
            words.add(h)
        elif kind == 's':
            subs.setdefault(int(size), set()).add(h)
        elif kind == 'c':
            codes.add(h)
    return words, subs, codes


def hash_lines(terms):
    out = []
    for term in terms:
        norm = re.sub(r'[^a-z0-9]', '', term.lower())
        if re.fullmatch(r'[a-z]{2,5}\d{3,6}', norm):
            out.append(f'c:{len(norm)}:{digest(norm)}')
        elif len(norm) >= SUBSTRING_MIN:
            out.append(f's:{len(norm)}:{digest(norm)}')
        else:
            out.append(f'w:{len(norm)}:{digest(norm)}')
    return out


def line_hits(line, lists):
    words, subs, codes = lists
    for m in CODE.finditer(line):
        if digest(m.group(1) + m.group(2)) in codes:
            return True
    for run in WORD.findall(line):
        for piece in CAMEL.findall(run) + [run]:
            if digest(piece) in words:
                return True
        low = run.lower()
        for size, hashes in subs.items():
            for i in range(len(low) - size + 1):
                if digest(low[i:i + size]) in hashes:
                    return True
    return False


def scan_text(text, lists):
    bad = []
    for n, line in enumerate(text.split('\n'), 1):
        if MARKER in line:
            continue
        if line_hits(line, lists):
            bad.append(n)
    return bad


def allowed(path, globs):
    return any(fnmatch.fnmatch(path, g) for g in globs)


def git(*args):
    return subprocess.check_output(('git',) + args, cwd=ROOT)


def main(argv):
    if argv[:1] == ['--hash']:
        print('\n'.join(hash_lines(argv[1:])))
        return 0
    lists = load_list()
    if not any(lists):
        print('check_dataset_names: scripts/dataset_names.sha256 is empty or missing')
        return 2
    globs = []
    if os.path.exists(ALLOW):
        globs = [l.split('#', 1)[0].strip() for l in open(ALLOW, encoding='utf-8')]
        globs = [g for g in globs if g]

    if argv[:1] == ['--message']:
        text = sys.stdin.read() if argv[1] == '-' else open(argv[1], encoding='utf-8').read()
        # Lines git adds itself ("# Please enter...") are comments, not message.
        text = '\n'.join(l for l in text.split('\n') if not l.startswith('#'))
        bad = scan_text(text, lists)
        if bad:
            print(f'commit message line(s) {bad} name a blocked dataset, site, '
                  'lab code or publication. See CONTRIBUTING.md.')
            return 1
        return 0

    if argv[:1] == ['--staged']:
        names = git('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z').decode().split('\0')
        read = lambda p: git('show', ':' + p)
    else:
        names = git('ls-files', '-z').decode().split('\0')
        read = lambda p: open(os.path.join(ROOT, p), 'rb').read()

    failed = 0
    for path in filter(None, names):
        # The file NAME counts too: a fixture called after a site is the leak.
        label_bad = line_hits(path, lists)
        if path in SKIP_EXACT or path.endswith(SKIP_SUFFIX) or allowed(path, globs):
            continue
        if label_bad:
            print(f'{path}: the file name is blocked')
            failed += 1
        try:
            text = read(path).decode('utf-8')
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for n in scan_text(text, lists):
            print(f'{path}:{n}: names a blocked dataset, site, lab code or publication')
            failed += 1
    if failed:
        print(f'\n{failed} problem(s). Describe the behaviour, not the project it came from; '
              'see CONTRIBUTING.md.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
