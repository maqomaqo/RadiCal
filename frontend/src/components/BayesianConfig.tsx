// SUPERSEDED -- not imported by any page or route.
//
// This depended on the old useAnalysisStore shape (bayesianConfig/
// setBayesianConfig), which stores/analysisStore.ts no longer provides:
// that store was rebuilt to hold the shared Stratigraphy draft
// (measurements/groups/relationships) for pages/Stratigraphy.tsx and
// pages/NewAnalysis.tsx instead. NewAnalysis.tsx still hardcodes MCMC
// parameters (2000 samples / 1000 tune / 4 chains) rather than exposing
// them in the UI -- letting a user configure mcmc_samples/mcmc_tune/
// mcmc_chains (already accepted by BayesianConfigInput on the backend)
// remains a reasonable follow-up, just not part of the stratigraphy UI
// work that replaced this file's reason for existing. See
// REMAINING_WORK.md.
export {}
