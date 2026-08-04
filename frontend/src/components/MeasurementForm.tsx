// SUPERSEDED -- not imported by any page or route.
//
// This depended on the old useAnalysisStore shape and the old
// RadiocarbonMeasurement type, neither of which exist anymore: both
// stores/analysisStore.ts and types/index.ts were rebuilt around
// DraftMeasurement/DraftGroup/DraftRelationship for the real
// stratigraphy UI (pages/Stratigraphy.tsx, components/
// HarrisMatrixDiagram.tsx). NewAnalysis.tsx still has its own inline
// measurement-entry UI rather than using a shared form component --
// consolidating the two is a reasonable follow-up, just not part of the
// stratigraphy UI work that replaced this file's reason for existing.
export {}
