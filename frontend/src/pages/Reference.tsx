import { Box, Container, Link, Paper, Typography } from '@mui/material'
import { Link as RouterLink } from 'react-router-dom'

/**
 * The domain vocabulary, defined once.
 *
 * PRODUCT.md is explicit that the terminology is not softened for a general
 * audience: BP, cal BP, HPD and Delta-R are the words the literature uses, and
 * a student who learns a diluted version cannot read a paper afterwards. This
 * page defines the real terms instead; nothing else in the app changes its
 * language because it exists.
 *
 * Definitions come from the implementation's own comments (services/
 * agreement.py, schemas/sample.py, models.py, utils/dateFormat.ts) rather than
 * being written fresh, so the reference and the behaviour cannot drift.
 */

interface Entry {
  term: string
  id: string
  body: React.ReactNode
}

interface Section {
  title: string
  id: string
  intro?: string
  entries: Entry[]
}

const SECTIONS: Section[] = [
  {
    title: 'From measurement to date',
    id: 'measurement',
    intro:
      'A radiocarbon measurement is not a calendar date. Turning one into the other is what this app does, and these are the terms that describe each end of it.',
    entries: [
      {
        term: 'Radiocarbon age (BP)',
        id: 'bp',
        body: (
          <>
            The laboratory reports an age in radiocarbon years (BP, for before present), calculated as though
            atmospheric radiocarbon had always been at its 1950 level. It never was, so a
            radiocarbon age is not a calendar age and the two drift apart by centuries in places.
            &ldquo;Present&rdquo; is fixed at 1950 by international convention, whenever the
            measurement was actually made.
          </>
        ),
      },
      {
        term: '1σ error',
        id: 'error',
        body: (
          <>
            The one-standard-deviation uncertainty the laboratory quotes alongside the age, also in
            radiocarbon years. It describes the measurement, not the calendar result — calibration
            reshapes it, often into something that is no longer symmetric.
          </>
        ),
      },
      {
        term: 'Calibration',
        id: 'calibration',
        body: (
          <>
            Converting a radiocarbon age into a calendar age using a curve built from samples of
            known date. Because the curve wanders, one radiocarbon age can map onto several
            separate stretches of calendar time.
          </>
        ),
      },
      {
        term: 'cal BP, BC/AD, BCE/CE',
        id: 'calbp',
        body: (
          <>
            Three ways of reporting the same calibrated result. <strong>cal BP</strong> counts
            calendar years before 1950. <strong>BC/AD</strong> and <strong>BCE/CE</strong> use the
            conventional calendar, which has no year zero — 1 BC is followed directly by AD 1.
            Switching between them relabels a result; it never moves one.
          </>
        ),
      },
    ],
  },
  {
    title: 'Calibration curves',
    id: 'curves',
    intro:
      'Every calibrated date is a claim about a specific curve. RadiCal ships three and will take your own.',
    entries: [
      {
        term: 'IntCal20, Marine20, SHCal20',
        id: 'shipped-curves',
        body: (
          <>
            The current international curves: IntCal20 for Northern Hemisphere terrestrial samples,
            SHCal20 for Southern Hemisphere terrestrial samples, and Marine20 for marine ones. Which
            curve a sample belongs on is a fact about where it lived, not a preference.
          </>
        ),
      },
      {
        term: 'Plateau',
        id: 'plateau',
        body: (
          <>
            A stretch where the curve runs flat, so a range of calendar years all share nearly the
            same radiocarbon age. A date landing on one calibrates to a wide or split result no
            matter how precise the measurement was — which is worth knowing before you pay for the
            measurement. The{' '}
            <Link component={RouterLink} to="/curves">
              curve browser
            </Link>{' '}
            shows you where they are.
          </>
        ),
      },
      {
        term: 'ΔR (Delta-R)',
        id: 'delta-r',
        body: (
          <>
            The <em>local</em> deviation from the global marine reservoir effect, in radiocarbon
            years. A marine curve already models the global average, so only the local offset goes
            here — entering a whole reservoir age (400–500 years for much of the ocean) counts the
            global effect twice and pulls the result centuries too young. A positive ΔR means the
            local water is more depleted than average, so the sample dates younger than it otherwise
            would.
          </>
        ),
      },
    ],
  },
  {
    title: 'Reading a result',
    id: 'results',
    entries: [
      {
        term: 'HPD (highest posterior density)',
        id: 'hpd',
        body: (
          <>
            The narrowest set of calendar ranges holding a given share of the probability — usually
            68% or 95%. It is reported as one or more <em>disjoint</em> ranges, each with its own
            share, because a calibrated distribution is often multi-modal. Although collapsing those into a
            single span seems convenient it is not accurate, since gaps between ranges are periods when the event did NOT occur.
          </>
        ),
      },
      {
        term: 'Posterior mean and median',
        id: 'point-estimates',
        body: (
          <>
            Single-number summaries of a calibrated distribution. Convenient, but rough: for a
            split or lopsided result the mean can land in a gap where the date almost certainly is
            not. Cite the HPD ranges; use the point estimate to sort and to place a date on an axis.
          </>
        ),
      },
    ],
  },
  {
    title: 'Bayesian modelling',
    id: 'modelling',
    intro:
      'If we know the order of events (as in a stratigraphic sequence) or other facts that affect when an event occurred, we have information radiocarbon measurements alone do not carry. Modelling puts these together, constraining what the dates are allowed to say.',
    entries: [
      {
        term: 'Sequence',
        id: 'sequence',
        body: (
          <>
            A group whose contents are in known order, earliest to latest. A Sequence orders what it
            contains; that ordering is the constraint it contributes.
          </>
        ),
      },
      {
        term: 'Phase',
        id: 'phase',
        body: (
          <>
            A group whose contents are probably contemporary or <em>unordered</em>. A Phase and a Sequence
            are not interchangeable: putting dates in a Phase asserts only that they belong
            together, and the model will not order them for you.
          </>
        ),
      },
      {
        term: 'Boundary',
        id: 'boundary',
        body: (
          <>
            A transition at the start or end of a group — the point at which activity began or
            ceased. Boundaries are estimated by the model rather than measured, and they are usually
            the dates a chronology is actually reported on.
          </>
        ),
      },
      {
        term: 'Hiatus',
        id: 'hiatus',
        body: <>A gap of unknown length between two parts of a Sequence, modelled as a break rather than assumed to be brief.</>,
      },
      {
        term: 'TPQ and TAQ',
        id: 'tpq-taq',
        body: (
          <>
            One-sided limits from something other than a radiocarbon measurement.{' '}
            <strong>TPQ</strong> (<em>terminus post quem</em>) says a sample cannot be older than a
            given point — it postdates the terminus. <strong>TAQ</strong> (
            <em>terminus ante quem</em>) says it cannot be younger. In radiocarbon ages, where
            larger is older, TPQ is therefore a maximum and TAQ a minimum.
          </>
        ),
      },
    ],
  },
  {
    title: 'Diagnostics',
    id: 'diagnostics',
    intro:
      'A Bayesian model moves every date. These are how you find out whether it had to move one further than the evidence justifies.',
    entries: [
      {
        term: 'Agreement index',
        id: 'agreement',
        body: (
          <>
            How well a date&rsquo;s modelled posterior still fits its own unmodelled likelihood
            (Bronk Ramsey 1995). A date the model leaves alone scores 100; one the model sharpened
            onto its own peak scores above 100, which is normal; below about 60 is the conventional
            threshold for a second look. It detects a date the <em>model had to move</em>, which is
            not the same as a date that does not belong — an outlier in an unconstrained Phase can
            score perfectly, because a lone Phase simply widens to accommodate it.
          </>
        ),
      },
      {
        term: 'Outlier probability',
        id: 'outlier',
        body: (
          <>
            The posterior probability that a date does not belong to the population being modelled,
            reported only where you set an outlier prior on it. No value is not the same as zero: it
            means the question was never asked of that date.
          </>
        ),
      },
    ],
  },
]

export default function Reference() {
  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
     <Paper sx={{ p: 3, mb: 3 }}>
         <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2, flexWrap: 'wrap', gap: 2 }}>
          <Box sx={{ flexGrow: 1, minWidth: 260 }}>
      <Typography variant="h1" component="h1">
        Reference
      </Typography>
      <Typography color="text.secondary" sx={{ mt: 1.5, maxWidth: '68ch' }}>
        The vocabulary RadiCal uses is standard in the literature. Knowing it
        will enable you to read the output and everyone else&rsquo;s papers.
      </Typography> </Box> </Box> </Paper>

      {/* A contents list, because a glossary is a page people arrive at
          looking for one word, not a page they read top to bottom. */}
      <Box component="nav" aria-label="Sections" sx={{ mt: 3 }}>
        <Box
          component="ul"
          sx={{ display: 'flex', flexWrap: 'wrap', gap: 1.5, listStyle: 'none', m: 0, p: 0 }}
        >
          {SECTIONS.map((section) => (
            <Box component="li" key={section.id}>
              {/* These are standalone navigation targets rather than links
                  inside a sentence, so they carry a real target size. The
                  inline links in the definitions below stay at text size,
                  where WCAG's target-size rule exempts them. */}
              <Link
                href={`#${section.id}`}
                sx={{
                  fontSize: '0.9375rem',
                  display: 'inline-flex',
                  alignItems: 'center',
                  minHeight: 44,
                  px: 0.5,
                }}
              >
                {section.title}
              </Link>
            </Box>
          ))}
        </Box>
      </Box>

      {SECTIONS.map((section) => (
        <Paper key={section.id} id={section.id} sx={{ p: 3, mt: 3, scrollMarginTop: 80 }}>
          <Typography variant="h2" component="h2" sx={{ fontSize: '1.5rem' }}>
            {section.title}
          </Typography>
          {section.intro && (
            <Typography color="text.secondary" sx={{ mt: 1, maxWidth: '68ch' }}>
              {section.intro}
            </Typography>
          )}

          <Box component="dl" sx={{ m: 0, mt: 2.5 }}>
            {section.entries.map((entry) => (
              <Box key={entry.id} id={entry.id} sx={{ mt: 2.5, scrollMarginTop: 80 }}>
                <Typography
                  component="dt"
                  sx={{ fontWeight: 600, fontSize: '1.0625rem', lineHeight: 1.4 }}
                >
                  {entry.term}
                </Typography>
                {/* A reading measure, not the full container width: this is
                    prose, and a 1152px line is not readable. */}
                <Typography
                  component="dd"
                  color="text.secondary"
                  sx={{ m: 0, mt: 0.5, maxWidth: '68ch' }}
                >
                  {entry.body}
                </Typography>
              </Box>
            ))}
          </Box>
        </Paper>
      ))}
    </Container>
  )
}
