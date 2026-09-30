import { visuallyHidden } from '@mui/utils'

/**
 * OxCal's two whole-model agreement indices, written as they are in the
 * literature: A with its qualifier set as a subscript, A_model and A_overall.
 * One component so the results page and the glossary cannot drift apart in how
 * they spell the same quantity.
 *
 * The subscript is hidden from assistive technology and spoken as words
 * instead: read flat, "A<sub>model</sub>" comes out as "Amodel", one run-on
 * word that names nothing.
 */
export default function AgreementSymbol({ which }: { which: 'model' | 'overall' }) {
  return (
    <>
      <span aria-hidden="true">
        A<sub>{which}</sub>
      </span>
      <span style={visuallyHidden}>A {which}</span>
    </>
  )
}
