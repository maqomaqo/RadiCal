import type { Theme } from '@mui/material/styles'
import { alpha } from '@mui/material/styles'
import { inkAt } from './ink'

/**
 * A shadow at the right edge, present only while there is more table to
 * scroll to. Without it the overflow is silent: the far column simply is not
 * there, and nothing on screen suggests it could be.
 * `background-attachment: local` ties it to the scroll position.
 *
 * Built from the theme, because the gradient is a string: a palette token does
 * not resolve inside it, and this once fell back to pure white -- on a warm
 * band, and then on a dark ground, where it would have been a white bar.
 */
export function scrollHintSx(theme: Theme) {
  const { paper } = theme.palette.background
  return {
    backgroundImage:
      `linear-gradient(to left, ${paper} 40%, ${alpha(paper, 0)}),` +
      `radial-gradient(farthest-side at 100% 50%, ${inkAt(theme.palette.mode, 0.16)}, ${alpha(theme.palette.ink, 0)})`,
    backgroundPosition: 'right center, right center',
    backgroundSize: '32px 100%, 14px 100%',
    backgroundRepeat: 'no-repeat',
    backgroundAttachment: 'local, scroll',
  } as const
}
