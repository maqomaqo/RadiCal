import type { ReactNode } from 'react'
import { Box } from '@mui/material'
import type { SxProps, Theme } from '@mui/material/styles'

/**
 * The material a page is READ from, on a surface of its own, above the
 * material it is worked in.
 *
 * The model editor used to set its orientation line, its colour key and its
 * gesture reference on the same white card as the model itself, separated
 * from it by the same 16px gap that separates any two paragraphs -- so nothing
 * told the eye where reference ended and the editable model began. The band
 * states that boundary with the surface and a rule instead of asking spacing
 * to carry it, in the token the theme already defines for a band inside an
 * elevated surface (background.muted) rather than a new colour. The diagram
 * tab states its own explainer the same way, so the two tabs of one page
 * introduce themselves identically.
 *
 * The negative margins take it full-bleed to the card's edges, which is what
 * makes it read as a masthead rather than one more boxed panel in the stack.
 * They assume `inset` matches the padding of the surface it sits in (24px,
 * `p: 3`, by default), and that it is that surface's FIRST child -- anything
 * above it (a page-level Alert, say) belongs outside the card, or the band
 * lands on top of it.
 *
 * `orientation="side"` turns the same band ninety degrees: the identity
 * material runs down the left edge of a row and the working material sits to
 * its right, which is how a Dashboard row echoes the Results masthead the
 * same analysis opens into. It expects to be the first cell of a two-column
 * grid and stretches to the row's full height; below `md`, where that grid
 * collapses to one column, it lies back down as a masthead so the rule stays
 * between identity and content rather than beside nothing.
 */
export default function GuideBand({
  children,
  orientation = 'top',
  inset = 3,
  sx,
}: {
  children: ReactNode
  orientation?: 'top' | 'side'
  inset?: number
  sx?: SxProps<Theme>
}) {
  const radius = (theme: Theme) => `${theme.shape.borderRadius}px`

  return (
    <Box
      sx={[
        {
          backgroundColor: 'background.muted',
          borderColor: 'divider',
          px: inset,
        },
        orientation === 'top'
          ? {
              mx: -inset,
              mt: -inset,
              mb: 2.5,
              py: 2.5,
              borderBottom: 1,
              borderTopLeftRadius: radius,
              borderTopRightRadius: radius,
            }
          : {
              // The cell has to be allowed to be narrower than its longest
              // word, or `noWrap` on the analysis name has nothing to
              // truncate against and the grid column widens instead.
              minWidth: 0,
              alignSelf: 'stretch',
              py: inset,
              ml: -inset,
              mt: -inset,
              mr: { xs: -inset, md: 0 },
              mb: { xs: 0, md: -inset },
              borderRight: { xs: 0, md: 1 },
              borderBottom: { xs: 1, md: 0 },
              borderTopLeftRadius: radius,
              borderTopRightRadius: { xs: radius, md: 0 },
              borderBottomLeftRadius: { xs: 0, md: radius },
            },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      {children}
    </Box>
  )
}
