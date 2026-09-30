/**
 * OxCal's two whole-model agreement indices, written as they are in the
 * literature: A with its qualifier set as a subscript, A_model and A_overall.
 * One component so the results page and the glossary cannot drift apart in how
 * they spell the same quantity.
 */
export default function AgreementSymbol({ which }: { which: 'model' | 'overall' }) {
  return (
    <>
      A<sub>{which}</sub>
    </>
  )
}
