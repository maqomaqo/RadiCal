# RadiCal

A comprehensive web-based tool for radiocarbon dating analysis with Bayesian statistical modeling, stratigraphic relationship handling, and interactive visualization.

## Features

- **Calibration Curve Selection**: Choose from all published curves (IntCal20, Marine20, SHCal20, and more)
- **Sample Type Support**: Terrestrial and marine samples with appropriate curve corrections
- **Bayesian Analysis**: Statistical modeling incorporating stratigraphic relationships
- **Harris Matrix Support**: Visual input of stratigraphic relationships via interactive diagram
- **Data Input GUI**: User-friendly interface for entering radiocarbon measurements
- **Results Visualization**: 
  - Posterior probability distributions
  - Calibrated age tables
  - Interactive graphs
  - PDF export capabilities
- **Download Functionality**: Export all tables and graphs as CSV, PDF, and image files

## Tech Stack

### Frontend
- **React 18** with TypeScript
- **Vite** for fast development and building
- **D3.js** for data visualization
- **React Flow** for Harris matrix diagram editor
- **Material-UI** for UI components
- **Plotly.js** for interactive graphs

### Backend
- **Python 3.11** with FastAPI
- **PyMC** for Bayesian statistical modeling
- **NumPy/SciPy** for numerical computations
- **Pandas** for data handling
- **ReportLab** for PDF generation

### Database
- **PostgreSQL** for data persistence
- **SQLAlchemy** ORM

## Project Structure

```
RadiCal/
├── frontend/                 # React TypeScript application
│   ├── src/
│   │   ├── components/      # React components
│   │   ├── pages/           # Page components
│   │   ├── services/        # API services
│   │   ├── types/           # TypeScript types
│   │   ├── stores/          # State management
│   │   ├── utils/           # Utility functions
│   │   └── App.tsx
│   ├── public/
│   ├── vite.config.ts
│   └── package.json
├── backend/                  # FastAPI application
│   ├── app/
│   │   ├── api/             # API routes
│   │   ├── models/          # Data models
│   │   ├── services/        # Business logic
│   │   ├── schemas/         # Pydantic schemas
│   │   └── main.py
│   ├── calibration_data/    # Calibration curves
│   ├── requirements.txt
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## Quick Start

### Prerequisites
- Docker and Docker Compose (recommended)
- Node.js 18+
- Python 3.11+
- PostgreSQL 14+

### Installation

```bash
# Clone the repository
git clone https://github.com/maqomaqo/RadiCal.git
cd RadiCal

# Copy environment variables
cp .env.example .env

# Start with Docker Compose
docker-compose up -d

# Access the webapp at http://localhost:3000
```

### Development

#### Frontend
```bash
cd frontend
npm install
npm run dev
```

#### Backend
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

#### Backend tests
The runtime image installs `requirements.txt` only, so the test
dependencies live in a separate file:
```bash
cd backend
pip install -r requirements-dev.txt
pytest
```
Tests needing PyMC are marked `requires_pymc` and skip automatically when
it isn't installed.

## API Endpoints

### Calibration Curves
- `GET /api/curves` - List all available calibration curves
- `GET /api/curves/{curve_id}` - Get curve data
- `GET /api/curves/type/{type}` - Get curves by type

### Radiocarbon Analysis
- `POST /api/analysis/calibrate` - Perform single sample calibration
- `POST /api/analysis/bayesian` - Perform Bayesian multi-sample analysis
- `POST /api/analysis/multi` - Multi-sample analysis
- `GET /api/analysis/{analysis_id}` - Retrieve analysis results
- `GET /api/analysis` - List all analyses

### Harris Matrix
- `POST /api/harris-matrix/validate` - Validate matrix relationships
- `POST /api/harris-matrix/relationships` - Generate relationships from diagram

### Results
- `GET /api/results/{analysis_id}/table` - Export results as table
- `GET /api/results/{analysis_id}/pdf` - Generate PDF report
- `GET /api/results/{analysis_id}/graphs` - Retrieve visualization data
- `GET /api/results/{analysis_id}/export/csv` - Export as CSV
- `GET /api/results/{analysis_id}/export/json` - Export as JSON

## Usage Guide

### Basic Workflow

1. **Create New Analysis**
   - Navigate to "New Analysis"
   - Enter sample information (name, type, radiocarbon age ± 1σ)

2. **Select Calibration Curve**
   - Choose between marine/terrestrial samples
   - Select appropriate calibration curve (default: IntCal20)
   - Apply reservoir age correction if marine

3. **Define Stratigraphic Relationships**
   - Use Harris Matrix diagram to visualize layer relationships
   - Specify boundaries and phases
   - Set any temporal constraints

4. **Run Analysis**
   - Select Bayesian or frequentist calibration
   - Configure MCMC parameters if Bayesian
   - Run analysis

5. **View & Export Results**
   - Review posterior probability distributions
   - Examine calibrated age ranges
   - Download tables and graphs

## Advanced Features

### Bayesian Modeling
The webapp uses PyMC for Bayesian inference:
- Specify prior distributions
- Define likelihood models
- Run MCMC sampling with diagnostics
- Calculate highest posterior density (HPD) intervals

### Harris Matrix Support
- Drag-and-drop interface for creating stratigraphic matrices
- Automatic validation of relationships
- Integration with Bayesian model constraints

### Multi-Sample Analysis
- Simultaneously analyze multiple samples
- Incorporate sequence constraints
- Generate pooled posterior distributions

## References

- Reimer, P. J., et al. (2020). IntCal20 Northern Hemisphere radiocarbon age calibration curve. Radiocarbon, 62(4).
- Bronk Ramsey, C. (2009). Bayesian analysis of radiocarbon dates. Radiocarbon, 51(1).
- Buck, C. E., et al. (1996). Bayesian Approach to Interpreting Archaeological Data.
- Heaton, T. J., et al. (2020). Marine20—the marine radiocarbon age calibration curve. Radiocarbon, 62(4).

## License

MIT License - see LICENSE file for details

## Contributing

Contributions are welcome! Please see CONTRIBUTING.md for guidelines.

## Support

For issues and questions, please open an issue on GitHub.

## Authors

- Initial development: maqomaqo
