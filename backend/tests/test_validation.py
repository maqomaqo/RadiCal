"""
Tests for request validation on /analysis/calibrate and /analysis/bayesian.

Before schemas/sample.py's CalibrateRequest/BayesianRequest existed, both
endpoints took a raw Dict body with no validation: negative ages, zero
error, blank sample names, and unrecognized curve_id/sample_type/
relationship values all passed straight through to calibration and either
produced a nonsense result or a bare KeyError/numpy exception instead of a
clear, descriptive error. These tests lock in that bad input is now
rejected with a 422 and good input still works.
"""
import pytest


VALID_MEASUREMENT = {
    "age_bp": 3000,
    "error_1sigma": 30,
    "curve_id": "intcal20",
    "sample_name": "Charcoal A",
}


class TestCalibrateValidation:
    def test_valid_request_succeeds(self, client):
        response = client.post("/api/analysis/calibrate", json=VALID_MEASUREMENT)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["saved"] is True
        assert data["calibrated_age_bp"] is not None

    def test_negative_age_bp_rejected(self, client):
        response = client.post(
            "/api/analysis/calibrate",
            json={**VALID_MEASUREMENT, "age_bp": -500},
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "age_bp"]

    def test_age_bp_far_out_of_range_rejected(self, client):
        response = client.post(
            "/api/analysis/calibrate",
            json={**VALID_MEASUREMENT, "age_bp": 99_999_999},
        )
        assert response.status_code == 422

    def test_zero_error_rejected(self, client):
        response = client.post(
            "/api/analysis/calibrate",
            json={**VALID_MEASUREMENT, "error_1sigma": 0},
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "error_1sigma"]

    def test_negative_error_rejected(self, client):
        response = client.post(
            "/api/analysis/calibrate",
            json={**VALID_MEASUREMENT, "error_1sigma": -10},
        )
        assert response.status_code == 422

    def test_unknown_curve_id_rejected(self, client):
        """A typo'd curve_id used to silently fall back to IntCal20 rather
        than error -- now it's a 422, matching the CalibrateRequest schema
        and the hardening in CalibrationCurve.get_curve()."""
        response = client.post(
            "/api/analysis/calibrate",
            json={**VALID_MEASUREMENT, "curve_id": "marine2020"},
        )
        assert response.status_code == 422

    def test_blank_sample_name_rejected(self, client):
        response = client.post(
            "/api/analysis/calibrate",
            json={**VALID_MEASUREMENT, "sample_name": "   "},
        )
        assert response.status_code == 422

    def test_missing_required_field_rejected(self, client):
        response = client.post(
            "/api/analysis/calibrate",
            json={"error_1sigma": 30},
        )
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "age_bp"]

    def test_unknown_sample_type_rejected(self, client):
        response = client.post(
            "/api/analysis/calibrate",
            json={**VALID_MEASUREMENT, "sample_type": "lacustrine"},
        )
        assert response.status_code == 422

    def test_quick_test_run_not_persisted(self, client):
        """save=False should validate the same way as save=True, but not
        write anything to the database."""
        response = client.post(
            "/api/analysis/calibrate",
            json={**VALID_MEASUREMENT, "save": False},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["saved"] is False
        assert data["id"] is None

        response = client.post(
            "/api/analysis/calibrate",
            json={**VALID_MEASUREMENT, "save": False, "age_bp": -1},
        )
        assert response.status_code == 422

    def test_unknown_project_id_rejected(self, client):
        response = client.post(
            "/api/analysis/calibrate",
            json={**VALID_MEASUREMENT, "project_id": "does-not-exist"},
        )
        assert response.status_code == 404


class TestCoordinateValidation:
    """
    Where a date came from, in WGS84 decimal degrees.

    Two rules, and the second is the one worth having a test for: the bounds
    are the real ones (there is no latitude of 91), and the pair is indivisible.
    A half-located date is the quiet failure -- it passes every other check,
    looks placed in the table, and then vanishes from every map and every
    distance the app computes, because nothing downstream can do anything with
    one number.
    """

    def test_a_coordinate_is_accepted(self, client):
        response = client.post(
            "/api/analysis/calibrate",
            json={**VALID_MEASUREMENT, "latitude": 54.5, "longitude": -5.6},
        )
        assert response.status_code == 200

    def test_no_coordinate_at_all_is_still_fine(self, client):
        """The columns are optional, and most dates will never carry one."""
        response = client.post("/api/analysis/calibrate", json=VALID_MEASUREMENT)
        assert response.status_code == 200

    @pytest.mark.parametrize("half", [
        {"latitude": 54.5},
        {"longitude": -5.6},
    ])
    def test_half_a_coordinate_is_refused(self, client, half):
        response = client.post(
            "/api/analysis/calibrate", json={**VALID_MEASUREMENT, **half})
        assert response.status_code == 422
        missing = "longitude" if "latitude" in half else "latitude"
        assert missing in str(response.json()["detail"])

    @pytest.mark.parametrize("out_of_range", [
        {"latitude": 91, "longitude": 0},
        {"latitude": -90.5, "longitude": 0},
        {"latitude": 0, "longitude": 180.1},
        {"latitude": 0, "longitude": -181},
    ])
    def test_a_position_off_the_earth_is_refused(self, client, out_of_range):
        response = client.post(
            "/api/analysis/calibrate", json={**VALID_MEASUREMENT, **out_of_range})
        assert response.status_code == 422

    @pytest.mark.parametrize("edge", [
        {"latitude": 90, "longitude": 180},
        {"latitude": -90, "longitude": -180},
        {"latitude": 0, "longitude": 0},
    ])
    def test_the_edges_of_the_earth_are_accepted(self, client, edge):
        """Including (0, 0). It is almost always an empty spreadsheet cell
        rather than a site in the Gulf of Guinea, but that is a judgement the
        importer makes as a WARNING -- refusing it here would make the one
        person legitimately working there unable to say so."""
        response = client.post(
            "/api/analysis/calibrate", json={**VALID_MEASUREMENT, **edge})
        assert response.status_code == 200


class TestBayesianValidation:
    def _measurement(self, sample_id, age_bp=3000):
        return {
            "id": sample_id,
            "age_bp": age_bp,
            "error_1sigma": 30,
            "sample_name": f"Sample {sample_id}",
        }

    def test_empty_measurements_rejected(self, client):
        response = client.post(
            "/api/analysis/bayesian?wait=true",
            json={"name": "Empty", "measurements": [], "save": False},
        )
        assert response.status_code == 422

    def test_unknown_relationship_type_rejected(self, client):
        """Previously an unrecognized relationship (e.g. a typo like
        "beneath" instead of "below") was silently dropped with no error
        and no effect on the model -- the constraint builder's if/elif chain had no
        else branch. Now it's rejected before the request is even
        processed."""
        response = client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "Test sequence",
                "measurements": [self._measurement("s1"), self._measurement("s2", 3200)],
                "relationships": [
                    {"from_sample_id": "s1", "to_sample_id": "s2", "relationship": "beneath"}
                ],
                "save": False,
            },
        )
        assert response.status_code == 422

    def test_bad_mcmc_chains_rejected(self, client):
        response = client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "Test",
                "measurements": [self._measurement("s1")],
                "bayesian_config": {"mcmc_chains": 0},
                "save": False,
            },
        )
        assert response.status_code == 422

    def test_measurement_with_negative_age_rejected(self, client):
        response = client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "Test",
                "measurements": [self._measurement("s1", age_bp=-100)],
                "save": False,
            },
        )
        assert response.status_code == 422
