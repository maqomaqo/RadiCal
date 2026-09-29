import { Link } from '@mui/material'
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft'
import { Link as RouterLink } from 'react-router-dom'

/**
 * The way back up, above a page's title: a chevron and the name of the page it
 * returns to, in secondary ink until pointed at.
 *
 * One component because four pages each drew their own -- a chevron link on a
 * project, a bare blue "Help" on the glossary, a "← Back to the project" with a
 * text arrow on Compare, and MUI Breadcrumbs on the curve browser -- so the
 * same question, "how do I get back", had four answers depending on where it
 * was asked. The label names the destination, not the act of going back.
 */
export default function BackLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link
      component={RouterLink}
      to={to}
      variant="body2"
      sx={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 0.25,
        // The chevron's own side bearing, so the glyph's ink -- not its box --
        // lines up with the title underneath.
        ml: -0.5,
        color: 'text.secondary',
        textDecoration: 'none',
        '&:hover': { color: 'primary.main', textDecoration: 'underline' },
      }}
    >
      <ChevronLeftIcon fontSize="small" />
      {children}
    </Link>
  )
}
