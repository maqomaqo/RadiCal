"""
The vocabulary of the marine reservoir database: what a feeding habit is, and
what one of the source's twenty-five spellings means.

Separate from reservoir_ingest.py, which fetches and parses, because this is
the part that encodes a JUDGEMENT about the source data rather than a reading
of it -- and because the lookup in §5.4 filters on these classes without
wanting anything to do with HTTP. See
docs/SPATIAL_METADATA_AND_DISPERSAL_PLAN.md §2.7 for the measurements behind
the table below.
"""
from typing import Dict, Optional


# The seven classes the source's spellings fold into.
#
# 'ambiguous' is the one that is not a habit. It is where the compound
# descriptions go -- see AMBIGUOUS_PREFIXES -- and it exists so that a hedge
# the original author wrote deliberately is never silently resolved into one
# side of the thing they were hedging about.
#
# 'unknown' is a real answer and not a suspect class: measured against the
# suspension pool it is mostly drawn from, it sits at +18 yr [-8.7, +43],
# which is to say indistinguishable. Excluding it by default would throw away
# 14% of the database to fix nothing.
FEEDING_CLASSES = (
    'suspension',
    'deposit',
    'carnivore',
    'grazer',
    'migratory',
    'ambiguous',
    'unknown',
)

# Source spelling (lowercased, stripped) -> class.
#
# Every spelling observed in the 2060 rows of the 2026-05-11 database. The
# case variants are the source's own inconsistency; 'filter' is a synonym for
# suspension feeding; the literal string 'NULL' is a null that came through as
# text.
#
# Grazers fold in with herbivores and browsers because algae and herbivorous
# gastropods eat the same recent carbon, and the paired test finds them
# indistinguishable from suspension feeders at every radius. 'commensalism' is
# not a feeding habit at all, but the organisms so described in this database
# are commensal on algal grazers and behave like them.
_FEEDING_SPELLINGS: Dict[str, str] = {
    # suspension
    'suspension': 'suspension',
    'filter': 'suspension',
    # deposit
    'deposit': 'deposit',
    # carnivore
    'carnivore': 'carnivore',
    'predator': 'carnivore',
    'scavenger': 'carnivore',
    'omnivore': 'carnivore',
    # grazer
    'herbivore': 'grazer',
    'browser': 'grazer',
    'algal grazer': 'grazer',
    'commensalism': 'grazer',
    # migratory
    'migratory': 'migratory',
    # unknown
    'unknown': 'unknown',
    'null': 'unknown',
    '': 'unknown',
}

# The five values the source truncates at sixteen characters.
#
# These are compound judgements -- "deposit or suspension feeder",
# "carnivore/scavenger" -- cut off mid-word by the source's column width. They
# cannot be assigned to one class without deciding the question their author
# declined to decide, so they become 'ambiguous' and the lookup treats them as
# what they are: points whose habit is not known well enough to filter on.
#
# Matched as prefixes rather than as exact strings because the truncation is
# an artefact of the column, and a source that widened the column tomorrow
# would start sending the untruncated text.
AMBIGUOUS_PREFIXES = (
    'deposit or suspe',
    'suspension/depos',
    'deposit feeder o',
    'herbivore/deposi',
    'carnivore/scaven',
)


def normalise_feeding(raw: Optional[str]) -> str:
    """
    One of FEEDING_CLASSES, from whatever the source said.

    Never raises and never returns None: an unrecognised spelling is
    'unknown', which is the honest reading -- the app does not know this
    organism's habit -- and reservoir_ingest counts those separately so a
    source that grows a new vocabulary is noticed rather than absorbed.
    """
    if raw is None:
        return 'unknown'
    text = raw.strip().lower()
    if not text:
        return 'unknown'

    # Prefixes first: 'deposit or suspe' starts with 'deposit', and matching
    # the exact table before the prefixes would file a deliberate hedge as a
    # deposit feeder -- the exact mistake this class exists to prevent.
    for prefix in AMBIGUOUS_PREFIXES:
        if text.startswith(prefix):
            return 'ambiguous'

    return _FEEDING_SPELLINGS.get(text, 'unknown')


def is_recognised_feeding(raw: Optional[str]) -> bool:
    """
    Whether the source's spelling is one this table knows.

    Distinct from normalising to 'unknown', which a NOVEL spelling also does:
    the ingest report counts what this returns False for, so a new vocabulary
    in the source shows up as a number a person can look at rather than as 300
    more rows quietly filed under unknown.
    """
    if raw is None:
        return True
    text = raw.strip().lower()
    if any(text.startswith(prefix) for prefix in AMBIGUOUS_PREFIXES):
        return True
    return text in _FEEDING_SPELLINGS
