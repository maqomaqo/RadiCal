# User Manual

## Table of Contents

1. [Getting Started](#getting-started)
2. [Creating an Analysis](#creating-an-analysis)
3. [Adding Measurements](#adding-measurements)
4. [Defining Stratigraphy](#defining-stratigraphy)
5. [Configuration Options](#configuration-options)
6. [Viewing Results](#viewing-results)
7. [Exporting Data](#exporting-data)

## Getting Started

1. Navigate to http://localhost:3000 (or your deployment URL)
2. Click "New Analysis" to start a new project
3. Follow the 4-step workflow:
   - **Measurements**: Add radiocarbon samples
   - **Stratigraphy**: Define relationships
   - **Configuration**: Set analysis options
   - **Analysis**: Review and run

## Adding Measurements

### Sample Information
- **Sample Name**: Identifier for the sample (e.g., "Layer A-01", "Burial 3")
- **Sample Type**: Choose between "Terrestrial" or "Marine"
  - Marine samples require reservoir age correction
- **Material Type**: Type of dated material (Charcoal, Bone, Wood, etc.)
- **Calibration Curve**: Select appropriate curve for sample type

### Radiocarbon Measurements
- **Age (BP)**: Radiocarbon age in years Before Present
- **1-Sigma Error (±)**: Standard deviation (uncertainty) of measurement
- **Reservoir Age** (marine only): Delta-R value for marine samples
  - Additional apparent age from marine reservoir effect
  - Typical range: 200-600 years

### Optional Information
- **Depth (cm)**: Sample depth below surface
- **Location**: Site or feature location
- **Notes**: Any additional context

## Defining Stratigraphy

Stratigraphic relationships are crucial for Bayesian analysis:

1. Select "From Sample" (older layer)
2. Choose "Relationship":
   - **is above**: From sample is stratigraphically above To sample (and thus younger)
   - **is below**: From sample is stratigraphically below To sample (and thus older)
   - **is equal**: From and To samples are from same phase (similar age)
3. Select "To Sample"
4. Click "Add Relationship"

**Example**: If Layer A is below Layer B, create relationship "Layer A is below Layer B"

## Configuration Options

### Analysis Name & Description
- Give your analysis a meaningful name
- Add description for future reference

### Analysis Type
- **Standard Calibration**: Treats each sample independently
  - Use when no stratigraphic information available
  - Faster but less precise
  
- **Bayesian Analysis**: Incorporates stratigraphic constraints
  - Recommended when you have sequence information
  - Provides refined age estimates
  - Requires at least one relationship

### MCMC Parameters (Bayesian only)

- **MCMC Samples**: Total posterior samples to generate (default: 2000)
  - More = higher precision but slower
  - Typical: 1000-5000
  
- **Tuning Steps**: Burn-in iterations (default: 1000)
  - Initial iterations to discard
  - Typical: 50% of samples
  
- **Number of Chains**: Parallel MCMC chains (default: 4)
  - More chains improve convergence checking
  - Typical: 2-4
  
- **Target Accept Rate**: Optimal 0.9-0.95
  - Controls sampler step size

## Viewing Results

Results page displays:

### Summary Statistics
- **Mean Age**: Average calibrated age across samples
- **Std Dev**: Standard deviation of ages
- **Min/Max Age**: Age range of all samples

### Results Table
For each sample:
- **Calibrated Age (BP)**: Corrected age estimate
- **68% HPD**: 68% highest posterior density interval (1-sigma equivalent)
- **95% HPD**: 95% highest posterior density interval (2-sigma equivalent)

### Probability Distributions
- Visual representation of age probability
- Peak = most likely age
- Width = uncertainty range

## Exporting Data

### CSV Export
- Contains all results in table format
- Compatible with Excel, R, Python
- Includes: Sample name, age, errors, HPD intervals

### PDF Export
- Full report with:
  - Analysis summary
  - All results tables
  - Probability distribution graphs
  - Metadata

### JSON Export
- Machine-readable format
- Full details including raw data
- Suitable for archival

## Best Practices

1. **Sample Preparation**
   - Use consistent units (always years BP)
   - Verify radiocarbon ages from lab reports
   - Double-check stratigraphic order

2. **Calibration Curves**
   - IntCal20: Northern Hemisphere terrestrial
   - Marine20: Marine samples
   - SHCal20: Southern Hemisphere terrestrial
   - Use most recent curve version available

3. **Marine Corrections**
   - Consult literature for appropriate ΔR values
   - Vary ΔR by ±200 years for sensitivity testing
   - Document ΔR source in notes

4. **MCMC Settings**
   - Start with defaults (2000 samples, 1000 tune, 4 chains)
   - Increase samples if results seem unstable
   - Check convergence diagnostics in output

5. **Stratigraphic Data**
   - Clearly define relationships
   - Specify boundaries between phases
   - Document any ambiguous sequences

## Tips & Tricks

- **Quick Edits**: Remove individual samples before re-running
- **Multiple Analyses**: Save different configurations for comparison
- **Sensitivity Testing**: Run with/without marine correction
- **Phase Constraints**: Use "is equal" for contemporary deposits
