import { alpha, createTheme, type Theme } from '@mui/material/styles'
import { inkAt } from './ink'
import { APP_BAR_DARK, SURFACES, type ColorMode } from './surfaces'

// `background.muted` and `background.raised` are ours, not MUI's -- declared so
// `sx={{ backgroundColor: 'background.muted' }}` resolves and typechecks like
// any built-in token. `palette.ink` is the app's one neutral.
declare module '@mui/material/styles' {
  interface TypeBackground {
    /** A banded surface inside an elevated one: table headers, stat strips. */
    muted: string
    /** A surface floating over an elevated one: a menu, a popover, a tooltip. */
    raised: string
  }
  interface Palette {
    /**
     * What the app writes in, and draws with when a mark is ABOUT the data
     * rather than a category of it. Warm ink on paper; aged paper on ink.
     * Take a strength of it with `inkAt(mode, strength)`.
     */
    ink: string
  }
  interface PaletteOptions {
    ink?: string
  }
}

// Palette pulled from the app's own logo (frontend/public/RadiCal.png) --
// see the palette mockup this replaced -- rather than MUI's stock blue and
// pink, which the previous values (#1976d2 / #dc004e) were verbatim restating.
//
// One entry per colour mode. The dark values are the light ones' own hues
// taken up in lightness until they clear 4.6:1 on `background.muted` (see
// DESIGN.md, Dark theme), so every colour here is still the colour it was.
interface Roles {
  primary: string
  secondary: string
  error: string
  warning: string
  success: string
  /** The text drawn ON a filled primary/secondary/status colour. */
  onColor: string | null
}

const ROLES: Record<ColorMode, Roles> = {
  light: {
    // The logo's pale blue (#f0f0fc) is a tint, not a usable interactive
    // color -- this is that same hue saturated up enough for buttons,
    // filled chips, and focus states.
    primary: '#3c70b8',
    // Darkened from the logo's own #f05460; the logo's exact coral is
    // reserved for illustration use, not interactive surfaces. Measures
    // 3.95:1 against white, so white-on-secondary clears AA for LARGE text
    // only -- see DESIGN.md, which used to claim body-text AA for it.
    secondary: '#e14a56',
    error: '#a3231d',
    warning: '#a85c11',
    success: '#2e7d32',
    onColor: null,
  },
  dark: {
    primary: '#6494d7',
    secondary: '#f66b73',
    // The oxblood is a depth, and a dark ground has no depth to give: both
    // reds have to be light to be read. So `error` leans towards orange-red and
    // is the softer of the pair, and the Boundary coral leans towards rose
    // and stays the stronger -- see diagramColors.ts.
    error: '#ef8168',
    warning: '#c98249',
    success: '#5da25e',
    // MUI would pick pure black at 0.87 for the label on these, which is a
    // cool neutral on the warmest ground in the app. The canvas is what a
    // label sits on in the dark theme, so it is what is cut out of the fill.
    onColor: SURFACES.dark.canvas,
  },
}

/** The ink of a mode as an `r, g, b` triple, for the shadow substitution. */
const SHADOW_INK: Record<ColorMode, string> = {
  light: 'rgba(43, 35, 32,',
  // A shadow on a dark ground is the ground, deepened. Ink-black in the same
  // warm family, not pure black.
  dark: 'rgba(8, 4, 2,',
}

const warmShadows = (mode: ColorMode) =>
  createTheme().shadows.map((shadow) =>
    shadow.replace(/rgba\(0,\s*0,\s*0,/g, SHADOW_INK[mode])
  ) as ReturnType<typeof createTheme>['shadows']

/**
 * The app's theme for a colour mode.
 *
 * MUI's default shadows are tuned (offset/blur/spread) per elevation level,
 * but colored pure black -- recoloring them to the theme's own ink rather
 * than replacing the 25-entry scale by hand keeps that tuning intact while
 * making every elevated surface (Card, Menu, Dialog, Snackbar...) pick up
 * the same warm cast as everything else, not just the components this file
 * styles directly.
 */
export function createAppTheme(mode: ColorMode): Theme {
  const dark = mode === 'dark'
  const surfaces = SURFACES[mode]
  const roles = ROLES[mode]
  const PRIMARY = roles.primary
  const ink = (strength: number) => inkAt(mode, strength)
  const withText = (main: string) =>
    roles.onColor ? { main, contrastText: roles.onColor } : { main }

  return createTheme({
    palette: {
      mode,
      primary: withText(PRIMARY),
      secondary: withText(roles.secondary),
      // The semantic trio, shifted slightly from MUI's stock values to accommodate app colour clash/overlap.
      info: withText(PRIMARY),
      error: withText(roles.error),
      warning: withText(roles.warning),
      success: withText(roles.success),
      ink: surfaces.ink,
      background: {
        default: surfaces.canvas,
        paper: surfaces.paper,
        muted: surfaces.muted,
        raised: surfaces.raised,
      },
      // The shadows and the text below were warmed so these are too.
      // `divider` alone reaches every TableCell, every
      // `DialogContent dividers`, every Accordion border and some twenty
      // explicit borderColor sites.
      divider: ink(0.14),
      action: {
        // Hover and the active-icon ink are the same substitution as divider.
        // The alphas are not MUI's own, because warm ink is lighter than black.
        // Each is the alpha that MATCHES the contrast of the value it replaces: 0.64 here
        // measures 4.75:1 against paper against black-at-0.54's 4.61:1 -- this is the
        // ink an unselected IconButton is drawn in, so it is a real graphic mark
        // and not a wash. 0.30 disabled measures 1.87:1 against 1.88:1.
        hover: ink(0.05),
        hoverOpacity: dark ? 0.06 : 0.05,
        active: ink(0.64),
        disabled: ink(0.3),
        disabledBackground: ink(0.14),
        // Selection and focus are the exception: they take the PRIMARY, not the
        // ink. The ordering matrix already reserves the primary blue for
        // selection and focus on its own grid; there was no reason that rule
        // stopped at one component. A selected row now reads as chosen rather
        // than as smudged. The light primary is darker than its ground and the
        // dark one lighter, so the dark wash is a step stronger to read as much.
        selected: alpha(PRIMARY, dark ? 0.18 : 0.1),
        selectedOpacity: dark ? 0.18 : 0.1,
        focus: alpha(PRIMARY, dark ? 0.2 : 0.12),
        focusOpacity: dark ? 0.2 : 0.12,
      },
      // MUI's defaults here are pure black at opacity (rgba(0,0,0,0.87) /
      // 0.6), the same cool-neutral-on-a-warm-canvas mismatch the shadows and
      // background.muted above were written to fix -- and text is far more of
      // the page than either.
      //
      // This is a warmth change, not a contrast fix: the defaults already pass
      // AA on this canvas (15.15:1 and 5.62:1). The replacements are the same
      // warm ink as the shadows, at alphas chosen not to give any of that
      // margin back -- 0.92 measures 11.39:1 on the canvas and 0.74 measures
      // 6.33:1, so secondary text ends up slightly better than the default it
      // replaces rather than trading legibility for tone. `disabled` is below
      // AA by design, as MUI's own default is; disabled text is exempt, and
      // making it pass would stop it reading as disabled. The dark theme holds
      // the same two contrasts, not the same two alphas.
      text: {
        primary: ink(0.92),
        secondary: ink(0.74),
        disabled: ink(0.4),
      },
    },
    shape: {
      borderRadius: 8,
    },
    components: {
      // The surfaces the app does not draw, which ship in colours belonging to
      // no design system. The selection highlight in particular was a THIRD
      // blue on screen -- the browser's own -- beside the primary and the info
      // blue this palette just merged.
      MuiCssBaseline: {
        styleOverrides: {
          '::selection': {
            backgroundColor: alpha(PRIMARY, dark ? 0.34 : 0.24),
            // Not white: at 0.24 the highlight is a wash, and the text under it
            // stays the text it was.
            color: 'inherit',
          },
          // The caret is a mark the user makes, so it takes the ink the app
          // writes in rather than the browser's black.
          'input, textarea, [contenteditable]': { caretColor: surfaces.ink },
          // MUI v5 gives a focused Button a ripple and very little else, so the
          // visible focus ring on most controls is whatever the browser draws.
          // One ring, in the colour this palette reserves for focus, offset far
          // enough to clear an 8px radius.
          ':focus-visible': {
            outline: `2px solid ${PRIMARY}`,
            outlineOffset: '2px',
          },
        },
      },
      // MUI v5 lays a white wash over every dark-mode Paper, stronger the higher
      // it sits. That is a pure white over a warm ground -- the cool neutral
      // this theme exists to avoid -- and it makes elevation a property of the
      // component rather than of the palette. The dark theme states its own
      // lift, in `background.*`; this turns the stock one off.
      MuiPaper: {
        styleOverrides: { root: { backgroundImage: 'none' } },
      },
      // The one place the palette cannot reach. MUI v5's OutlinedInput hardcodes
      // its resting border as `rgba(0,0,0,0.23)` instead of reading `divider`,
      // so every text field in the app kept a pure-black outline after every
      // other line in the theme had been warmed. 0.27 is the alpha that matches
      // its contrast (1.738:1 against black-at-0.23's 1.744:1), not its number.
      // Hover and focus are MUI's own (text.primary, then primary at 2px) and
      // are left alone.
      MuiOutlinedInput: {
        styleOverrides: {
          notchedOutline: { borderColor: ink(0.27) },
        },
      },
      MuiButton: {
        styleOverrides: {
          // Material's stock uppercasing is the loudest un-customized default
          // left in the app: every button on every page shouts, including the
          // three nav links and the primary action. The label is written in
          // the product's own words -- "New Analysis", not "NEW ANALYSIS" --
          // and uppercase also throws away the ascender/descender shapes that
          // make a label quick to recognize.
          root: { textTransform: 'none' },
        },
      },
      // The same default reached every Tab and ToggleButton the Button override
      // above never covered: "ANALYSES (3)" beside a "New Analysis" button, and
      // "STACKED / INDIVIDUAL / CURVE" above a plot whose every other control is
      // written in sentence case.
      MuiTab: {
        styleOverrides: {
          root: { textTransform: 'none', fontSize: '0.9375rem' },
        },
      },
      MuiToggleButton: {
        styleOverrides: {
          root: { textTransform: 'none' },
        },
      },
      // The ring above never reached a Button: ButtonBase sets `outline: 0` on
      // its root at the same specificity and later in the cascade, so a focused
      // text button -- every "Download PNG" -- showed only the ripple. Stated on
      // the class MUI puts on keyboard focus, which it only puts there for
      // keyboard focus.
      MuiButtonBase: {
        styleOverrides: {
          root: {
            '&.Mui-focusVisible': {
              outline: `2px solid ${PRIMARY}`,
              outlineOffset: '2px',
            },
          },
        },
      },
      // A Switch's focusable part is the round thumb button, which the root
      // clips (overflow: hidden), so a ring on it is cut off. The ring goes
      // round the whole track instead.
      MuiSwitch: {
        styleOverrides: {
          root: {
            '&:has(.Mui-focusVisible)': {
              outline: `2px solid ${PRIMARY}`,
              outlineOffset: '0px',
              borderRadius: 12,
            },
          },
          switchBase: {
            '&.Mui-focusVisible': { outline: 'none' },
          },
        },
      },
      // The dark theme's own surfaces for the parts that float and the parts
      // that dim. Each is a gap MUI fills with a cool neutral if left alone.
      ...(dark
        ? {
            // In the light theme the bar is the primary, and MUI paints a
            // primary bar in the primary. In the dark one the primary is the
            // LIGHT blue that text and lines need, and a bar that size in it
            // would be the loudest thing on the screen. The bar is the brand
            // blue taken down to a band instead, with white on it at 10.5:1 --
            // so every `color="inherit"` control on it is unchanged, and so is
            // the rule that no inactive label is dimmed.
            MuiAppBar: {
              defaultProps: { enableColorOnDark: true },
              styleOverrides: {
                colorPrimary: { backgroundColor: APP_BAR_DARK, color: '#fff' },
              },
            },
            // A shadow is nearly invisible on a dark ground, so what floats
            // over a card is told from it by lightness, with a hairline for the
            // edge the shadow no longer draws.
            MuiPopover: {
              styleOverrides: {
                paper: {
                  backgroundColor: surfaces.raised,
                  border: `1px solid ${ink(0.14)}`,
                },
              },
            },
            MuiTooltip: {
              styleOverrides: {
                tooltip: {
                  backgroundColor: surfaces.raised,
                  color: ink(0.92),
                  border: `1px solid ${ink(0.14)}`,
                },
                arrow: { color: surfaces.raised },
              },
            },
            // MUI's scrim is pure black at 0.5.
            MuiBackdrop: {
              styleOverrides: {
                root: { backgroundColor: 'rgba(8, 4, 2, 0.62)' },
                invisible: { backgroundColor: 'transparent' },
              },
            },
          }
        : {}),
    },
    shadows: warmShadows(mode),
    typography: {
      fontFamily: 'Roboto, sans-serif',
      // One step down on phones, for every page title at once: at 2.5rem a
      // two-word title ("Calibration Curves", a long project name) broke onto a
      // line per word in a 311px column. ProjectDetail used to do this locally.
      h1: {
        fontSize: '2.5rem',
        fontWeight: 600,
        '@media (max-width:599.95px)': { fontSize: '2rem' },
      },
      h2: { fontSize: '2rem', fontWeight: 600 },
    },
  })
}
