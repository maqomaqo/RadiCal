"""
Tests for a project's date collection (models.ProjectSample,
api/project_samples.py) and the duplicate / edit-inputs endpoints that
make "same dates, different model" possible.

The load-bearing test here is TestSnapshotSemantics: deploying a date into
an analysis must COPY it, so editing the collection afterwards can never
mutate an already-run analysis or silently invalidate its stored results.
Everything else is CRUD and validation.
"""
import pytest



def _date(name="OxA-1", age_bp=3000, **overrides):
    d = {
        "sample_name": name,
        "age_bp": age_bp,
        "error_1sigma": 30,
        "material_type": "charcoal",
    }
    d.update(overrides)
    return d


def _make_project(client, name="Test Site"):
    return client.post("/api/projects", json={"name": name}).json()["data"]["id"]


def _add_dates(client, project_id, dates):
    response = client.post(f"/api/projects/{project_id}/samples", json={"samples": dates})
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


class TestProjectSampleCrud:
    def test_add_and_list_dates(self, client):
        pid = _make_project(client)
        _add_dates(client, pid, [_date("OxA-1", 3000), _date("OxA-2", 3200)])

        items = client.get(f"/api/projects/{pid}/samples").json()["data"]["items"]
        assert [i["sample_name"] for i in items] == ["OxA-1", "OxA-2"]
        assert items[0]["age_bp"] == 3000

    def test_bulk_add_in_one_request(self, client):
        pid = _make_project(client)
        created = _add_dates(client, pid, [_date(f"S{i}", 3000 + i * 10) for i in range(25)])
        assert len(created) == 25

    def test_update_a_date(self, client):
        pid = _make_project(client)
        sample_id = _add_dates(client, pid, [_date("OxA-1", 3000)])[0]["id"]

        response = client.patch(
            f"/api/projects/{pid}/samples/{sample_id}",
            json={"age_bp": 3055, "default_context_name": "Pit 12"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["age_bp"] == 3055
        assert response.json()["data"]["default_context_name"] == "Pit 12"

    def test_delete_a_date(self, client):
        pid = _make_project(client)
        sample_id = _add_dates(client, pid, [_date("OxA-1")])[0]["id"]

        assert client.delete(f"/api/projects/{pid}/samples/{sample_id}").status_code == 200
        assert client.get(f"/api/projects/{pid}/samples").json()["data"]["items"] == []

    def test_unknown_project_404s(self, client):
        assert client.get("/api/projects/nope/samples").status_code == 404

    def test_validation_rejects_bad_measurement(self, client):
        pid = _make_project(client)
        assert client.post(
            f"/api/projects/{pid}/samples", json={"samples": [_date(age_bp=-5)]}
        ).status_code == 422
        assert client.post(
            f"/api/projects/{pid}/samples", json={"samples": [_date(error_1sigma=0)]}
        ).status_code == 422
        assert client.post(
            f"/api/projects/{pid}/samples", json={"samples": [_date(curve_id="nonsense")]}
        ).status_code == 422

    def test_update_rejects_contradictory_bounds(self, client):
        """taq > tpq must be caught even when the two arrive in separate
        PATCHes, since the merged row is what matters."""
        pid = _make_project(client)
        sample_id = _add_dates(client, pid, [_date(tpq_age_bp=2000)])[0]["id"]

        response = client.patch(
            f"/api/projects/{pid}/samples/{sample_id}", json={"taq_age_bp": 2500}
        )
        assert response.status_code == 422


class TestDeploy:
    def test_deploy_creates_pending_analysis_with_copies(self, client):
        pid = _make_project(client)
        created = _add_dates(client, pid, [_date("OxA-1", 3000), _date("OxA-2", 3200)])
        ids = [c["id"] for c in created]

        response = client.post(
            f"/api/projects/{pid}/deploy",
            json={"name": "Model A", "sample_ids": ids},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "pending"
        assert data["measurements_count"] == 2

        analysis = client.get(f"/api/analysis/{data['id']}").json()["data"]
        assert analysis["project_id"] == pid
        assert sorted(m["sample_name"] for m in analysis["measurements"]) == ["OxA-1", "OxA-2"]

    def test_deploy_groups_by_default_context_name(self, client):
        pid = _make_project(client)
        created = _add_dates(client, pid, [
            _date("A", 3000, default_context_name="Pit 12"),
            _date("B", 3100, default_context_name="Pit 12"),
            _date("C", 3300, default_context_name="Layer 3"),
            _date("D", 3400),
        ])
        ids = [c["id"] for c in created]

        deployed = client.post(
            f"/api/projects/{pid}/deploy", json={"name": "M", "sample_ids": ids}
        ).json()["data"]
        assert deployed["contexts_created"] == 2

        analysis = client.get(f"/api/analysis/{deployed['id']}").json()["data"]
        by_name = {g["id"]: g["name"] for g in analysis["groups"]}
        assignment = {m["sample_name"]: by_name.get(m["group_id"]) for m in analysis["measurements"]}
        assert assignment["A"] == "Pit 12"
        assert assignment["B"] == "Pit 12"
        assert assignment["C"] == "Layer 3"
        assert assignment["D"] is None

    def test_same_dates_deploy_into_independent_analyses(self, client):
        """The whole point of the collection: one set of dates, several
        analyses, each free to model them differently."""
        pid = _make_project(client)
        ids = [c["id"] for c in _add_dates(client, pid, [_date("A", 3000), _date("B", 3200)])]

        first = client.post(f"/api/projects/{pid}/deploy", json={"name": "M1", "sample_ids": ids}).json()["data"]
        second = client.post(f"/api/projects/{pid}/deploy", json={"name": "M2", "sample_ids": ids}).json()["data"]

        assert first["id"] != second["id"]
        a1 = client.get(f"/api/analysis/{first['id']}").json()["data"]
        a2 = client.get(f"/api/analysis/{second['id']}").json()["data"]
        # Distinct sample rows, not shared ones.
        assert {m["id"] for m in a1["measurements"]}.isdisjoint({m["id"] for m in a2["measurements"]})

    def test_deploy_rejects_unknown_sample_id(self, client):
        pid = _make_project(client)
        assert client.post(
            f"/api/projects/{pid}/deploy", json={"name": "M", "sample_ids": ["ghost"]}
        ).status_code == 404

    def test_deploy_rejects_empty_selection(self, client):
        pid = _make_project(client)
        assert client.post(
            f"/api/projects/{pid}/deploy", json={"name": "M", "sample_ids": []}
        ).status_code == 422


class TestDeployingAsCalibration:
    """
    `analysis_type: "calibration"` means one date calibrated on its own.
    Deploying several dates under it used to keep only the first and say
    nothing -- _rerun_analysis's calibration branch read samples[0], which was
    correct back when a single-date analysis was the only way to reach it.

    Several independent dates with no model relating them is what
    'group_calibration' already is, so that is what a multi-date request
    produces now.
    """

    def test_several_dates_become_a_group_calibration(self, client):
        pid = _make_project(client, "Deploy calibration many")
        ids = [d["id"] for d in _add_dates(
            client, pid, [_date("DC-1", 3000), _date("DC-2", 3100), _date("DC-3", 3200)])]

        deployed = client.post(
            f"/api/projects/{pid}/deploy",
            json={"name": "Three dates", "sample_ids": ids, "analysis_type": "calibration"},
        ).json()["data"]

        # The response reports what was created, not what was asked for.
        assert deployed["analysis_type"] == "group_calibration"
        assert deployed["measurements_count"] == 3

    def test_every_deployed_date_gets_a_result(self, client):
        """
        The actual defect: three dates in, one result out. Asserted on the run
        rather than on the stored type, because the type is only a label and
        the missing results were the harm.
        """
        pid = _make_project(client, "Deploy calibration results")
        ids = [d["id"] for d in _add_dates(
            client, pid, [_date("DR-1", 3000), _date("DR-2", 3100), _date("DR-3", 3200)])]
        deployed = client.post(
            f"/api/projects/{pid}/deploy",
            json={"name": "Three dates", "sample_ids": ids, "analysis_type": "calibration"},
        ).json()["data"]

        assert client.post(
            f"/api/analysis/{deployed['id']}/rerun?wait=true").status_code == 200

        analysis = client.get(f"/api/analysis/{deployed['id']}").json()["data"]
        assert analysis["status"] == "completed"
        assert len(analysis["results"]) == 3, "a date was dropped"
        # Each result belongs to a different sample, and each has a real
        # calibrated age -- three copies of the first date would also be three
        # results.
        assert len({r["sample_id"] for r in analysis["results"]}) == 3
        assert all(r["posterior_mean"] for r in analysis["results"])

    def test_a_single_date_is_still_a_plain_calibration(self, client):
        """The routing is for the case that was broken, not a redefinition of
        the type: one date is what 'calibration' means and stays that."""
        pid = _make_project(client, "Deploy calibration one")
        ids = [d["id"] for d in _add_dates(client, pid, [_date("DS-1", 3000)])]

        deployed = client.post(
            f"/api/projects/{pid}/deploy",
            json={"name": "One date", "sample_ids": ids, "analysis_type": "calibration"},
        ).json()["data"]

        assert deployed["analysis_type"] == "calibration"
        assert client.post(
            f"/api/analysis/{deployed['id']}/rerun?wait=true").status_code == 200
        analysis = client.get(f"/api/analysis/{deployed['id']}").json()["data"]
        assert len(analysis["results"]) == 1

    def test_a_bayesian_deploy_is_untouched(self, client):
        pid = _make_project(client, "Deploy bayesian many")
        ids = [d["id"] for d in _add_dates(
            client, pid, [_date("DB-1", 3000), _date("DB-2", 3100)])]

        deployed = client.post(
            f"/api/projects/{pid}/deploy",
            json={"name": "Two dates", "sample_ids": ids, "analysis_type": "bayesian"},
        ).json()["data"]
        assert deployed["analysis_type"] == "bayesian"

    def test_an_analysis_already_stored_as_multi_date_calibration_is_repaired(self, client):
        """
        Deploy can no longer create that state, but a database may already
        hold one -- the API accepted it before today. A rerun must calibrate
        every date rather than truncating again, which is why the fix is in
        _rerun_analysis as well as in the deploy route.

        Built behind the endpoint, since there is deliberately no longer an
        API that produces it.
        """
        from app.database import SessionLocal
        from app.models import Analysis

        pid = _make_project(client, "Legacy multi calibration")
        ids = [d["id"] for d in _add_dates(
            client, pid, [_date("LG-1", 3000), _date("LG-2", 3100), _date("LG-3", 3200)])]
        deployed = client.post(
            f"/api/projects/{pid}/deploy",
            json={"name": "Legacy", "sample_ids": ids, "analysis_type": "calibration"},
        ).json()["data"]

        with SessionLocal() as db:
            analysis = db.query(Analysis).filter(Analysis.id == deployed["id"]).first()
            analysis.analysis_type = "calibration"
            db.commit()

        assert client.post(
            f"/api/analysis/{deployed['id']}/rerun?wait=true").status_code == 200
        analysis = client.get(f"/api/analysis/{deployed['id']}").json()["data"]
        assert analysis["analysis_type"] == "calibration"
        assert len(analysis["results"]) == 3, "the stored analysis was truncated again"


class TestBulkUpdate:
    """
    PATCH /projects/{id}/samples -- one value across a selection.

    Two rules carry the weight here, and both exist because neither field is
    a plain assignment: a curve brings the sample type with it, and a
    reservoir offset is skipped on dates it would be wrong for.
    """

    def _mixed(self, client, tag):
        """Two marine dates and one terrestrial, which is the selection both
        rules are actually about."""
        pid = _make_project(client, f"Bulk {tag}")
        ids = [d["id"] for d in _add_dates(client, pid, [
            _date(f"{tag}-M1", 3000, sample_type="marine", curve_id="marine20",
                  material_type="shell"),
            _date(f"{tag}-M2", 3100, sample_type="marine", curve_id="marine20",
                  material_type="shell"),
            _date(f"{tag}-T1", 3200),
        ])]
        return pid, ids

    def test_delta_r_reaches_the_marine_dates_and_skips_the_rest(self, client):
        """
        The confirmed behaviour: a mixed selection still works, and the ones
        it could not apply to are reported rather than silently included.
        Delta-R corrects a marine curve's global average, so writing it to a
        terrestrial date is not redundant but wrong -- it would be applied at
        calibration and move the result.
        """
        pid, ids = self._mixed(client, "dr")
        response = client.patch(f"/api/projects/{pid}/samples",
                                json={"sample_ids": ids, "delta_r": -120,
                                      "delta_r_error": 40})
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert (data["updated_count"], data["skipped_count"]) == (2, 1)

        by_name = {s["sample_name"]: s for s in data["items"]}
        assert by_name["dr-M1"]["delta_r"] == -120
        assert by_name["dr-M1"]["delta_r_error"] == 40
        assert by_name["dr-T1"]["delta_r"] is None

    def test_delta_r_without_its_uncertainty_is_rejected(self, client):
        """Same rule as a single measurement's, applied to a whole selection:
        an offset with no uncertainty would understate every date it touched."""
        pid, ids = self._mixed(client, "dre")
        response = client.patch(f"/api/projects/{pid}/samples",
                                json={"sample_ids": ids, "delta_r": -120})
        assert response.status_code == 422
        assert "delta_r_error is required" in response.text

    def test_delta_r_error_alone_is_rejected(self, client):
        pid, ids = self._mixed(client, "dro")
        response = client.patch(f"/api/projects/{pid}/samples",
                                json={"sample_ids": ids, "delta_r_error": 40})
        assert response.status_code == 422

    def test_a_null_offset_clears_both_halves(self, client):
        """
        Clearing has to take the uncertainty with it, or a stray error is left
        behind with no offset to belong to. `delta_r: null` means clear;
        omitting the key entirely means leave alone, which is the distinction
        the endpoint reads from model_fields_set rather than from the value.
        """
        pid, ids = self._mixed(client, "drc")
        client.patch(f"/api/projects/{pid}/samples",
                     json={"sample_ids": ids, "delta_r": -120, "delta_r_error": 40})

        response = client.patch(f"/api/projects/{pid}/samples",
                                json={"sample_ids": ids, "delta_r": None})
        assert response.status_code == 200
        for item in response.json()["data"]["items"]:
            assert item["delta_r"] is None
            assert item["delta_r_error"] is None

    def test_omitting_delta_r_leaves_it_alone(self, client):
        """The other half of the same distinction: a curve-only request must
        not wipe an offset that was already there."""
        pid, ids = self._mixed(client, "drk")
        client.patch(f"/api/projects/{pid}/samples",
                     json={"sample_ids": ids, "delta_r": -80, "delta_r_error": 20})

        response = client.patch(f"/api/projects/{pid}/samples",
                                json={"sample_ids": ids, "curve_id": "marine20"})
        assert response.status_code == 200
        marine = [s for s in response.json()["data"]["items"]
                  if s["sample_name"] != "drk-T1"]
        assert all(s["delta_r"] == -80 for s in marine)

    def test_the_sample_type_follows_the_curve(self, client):
        """
        A date calibrated against a marine curve is marine. The single-date
        form already keeps the two in step in the other direction; doing less
        here would leave a date whose curve and declared type disagree, which
        nothing else in the app can produce.
        """
        pid, ids = self._mixed(client, "ct")
        response = client.patch(f"/api/projects/{pid}/samples",
                                json={"sample_ids": ids, "curve_id": "marine20"})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["sample_type_changed_count"] == 1
        assert all(s["sample_type"] == "marine" for s in data["items"])

        response = client.patch(f"/api/projects/{pid}/samples",
                                json={"sample_ids": ids, "curve_id": "intcal20"})
        data = response.json()["data"]
        assert data["sample_type_changed_count"] == 3
        assert all(s["sample_type"] == "terrestrial" for s in data["items"])

    def test_a_curve_and_an_offset_in_one_request_agree_with_each_other(self, client):
        """
        The offset is applied against the type the SAME request just set, not
        the one the dates arrived with. Judging against the old type would
        skip exactly the dates the caller was correcting -- the terrestrial
        date they have just declared marine.
        """
        pid, ids = self._mixed(client, "cd")
        response = client.patch(f"/api/projects/{pid}/samples",
                                json={"sample_ids": ids, "curve_id": "marine20",
                                      "delta_r": -60, "delta_r_error": 15})
        assert response.status_code == 200
        data = response.json()["data"]
        assert (data["updated_count"], data["skipped_count"]) == (3, 0)
        assert all(s["delta_r"] == -60 for s in data["items"])

    def test_an_unknown_curve_is_rejected(self, client):
        pid, ids = self._mixed(client, "cu")
        response = client.patch(f"/api/projects/{pid}/samples",
                                json={"sample_ids": ids, "curve_id": "no-such-curve"})
        assert response.status_code == 422

    def test_an_empty_request_is_rejected(self, client):
        """Sending only ids is a request that says nothing; answering 200 to
        it would suggest something happened."""
        pid, ids = self._mixed(client, "ce")
        assert client.patch(f"/api/projects/{pid}/samples",
                            json={"sample_ids": ids}).status_code == 422

    def test_an_id_from_another_project_is_rejected(self, client):
        pid, ids = self._mixed(client, "cx")
        assert client.patch(f"/api/projects/{pid}/samples",
                            json={"sample_ids": ids + ["ghost"],
                                  "curve_id": "intcal20"}).status_code == 404

    def test_a_deployed_analysis_keeps_the_values_it_was_given(self, client):
        """
        The snapshot rule, applied to bulk edits: an analysis deployed before
        the edit holds its own copies and must not move. This is the same
        guarantee TestSnapshotSemantics makes for single edits, and bulk is
        exactly where it would be easiest to break.
        """
        pid, ids = self._mixed(client, "cs")
        deployed = client.post(f"/api/projects/{pid}/deploy",
                               json={"name": "Before", "sample_ids": ids}).json()["data"]

        client.patch(f"/api/projects/{pid}/samples",
                     json={"sample_ids": ids, "curve_id": "intcal20"})

        analysis = client.get(f"/api/analysis/{deployed['id']}").json()["data"]
        curves = {m["curve_id"] for m in analysis["measurements"]}
        assert "marine20" in curves, "a bulk edit reached an already-deployed analysis"


class TestDeltaRTravelsWithADeployedDate:
    def test_a_deployed_date_arrives_already_corrected(self, client):
        """
        Delta-R is a property of where a sample came from, so it belongs to
        the collection entry and has to survive the copy into an analysis.
        Before it was stored here, a marine date deployed out of a collection
        lost its reservoir correction silently and had to have it re-entered
        on every analysis it was deployed into.
        """
        pid = _make_project(client, "Delta R deploy")
        ids = [d["id"] for d in _add_dates(client, pid, [
            _date("DD-M1", 3000, sample_type="marine", curve_id="marine20",
                  material_type="shell"),
        ])]
        client.patch(f"/api/projects/{pid}/samples",
                     json={"sample_ids": ids, "delta_r": -95, "delta_r_error": 30})

        deployed = client.post(f"/api/projects/{pid}/deploy",
                               json={"name": "Carried", "sample_ids": ids}).json()["data"]
        measurement = client.get(
            f"/api/analysis/{deployed['id']}").json()["data"]["measurements"][0]
        assert measurement["delta_r"] == -95
        assert measurement["delta_r_error"] == 30


class TestBulkDelete:
    def test_several_dates_go_at_once(self, client):
        pid = _make_project(client, "Bulk delete")
        ids = [d["id"] for d in _add_dates(client, pid, [
            _date("BD-1", 3000), _date("BD-2", 3100), _date("BD-3", 3200)])]

        response = client.request("DELETE", f"/api/projects/{pid}/samples",
                                  json={"sample_ids": ids[:2]})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["deleted_count"] == 2

        remaining = client.get(f"/api/projects/{pid}/samples").json()["data"]["items"]
        assert [s["sample_name"] for s in remaining] == ["BD-3"]

    def test_one_bad_id_deletes_nothing(self, client):
        """
        All-or-nothing on purpose. A partial delete is not something a caller
        can recover from: it cannot tell which of its ids went without
        diffing the collection afterwards.
        """
        pid = _make_project(client, "Bulk delete partial")
        ids = [d["id"] for d in _add_dates(client, pid, [
            _date("BP-1", 3000), _date("BP-2", 3100)])]

        response = client.request("DELETE", f"/api/projects/{pid}/samples",
                                  json={"sample_ids": ids + ["ghost"]})
        assert response.status_code == 404
        assert len(client.get(f"/api/projects/{pid}/samples").json()["data"]["items"]) == 2

    def test_a_deployed_analysis_survives_the_collection_entry(self, client):
        """Deleting from the collection is not deleting the analysis -- it
        holds its own copies."""
        pid = _make_project(client, "Bulk delete deployed")
        ids = [d["id"] for d in _add_dates(client, pid, [
            _date("BX-1", 3000), _date("BX-2", 3100)])]
        deployed = client.post(f"/api/projects/{pid}/deploy",
                               json={"name": "Kept", "sample_ids": ids}).json()["data"]

        client.request("DELETE", f"/api/projects/{pid}/samples", json={"sample_ids": ids})

        analysis = client.get(f"/api/analysis/{deployed['id']}").json()["data"]
        assert len(analysis["measurements"]) == 2

    def test_a_duplicate_id_is_rejected(self, client):
        pid = _make_project(client, "Bulk delete dupe")
        ids = [d["id"] for d in _add_dates(client, pid, [_date("BQ-1", 3000)])]
        response = client.request("DELETE", f"/api/projects/{pid}/samples",
                                  json={"sample_ids": ids + ids})
        assert response.status_code == 422


class TestSnapshotSemantics:
    def test_editing_a_collected_date_does_not_touch_deployed_analyses(self, client):
        pid = _make_project(client)
        sample_id = _add_dates(client, pid, [_date("OxA-1", 3000)])[0]["id"]
        deployed = client.post(
            f"/api/projects/{pid}/deploy", json={"name": "M", "sample_ids": [sample_id]}
        ).json()["data"]

        client.patch(f"/api/projects/{pid}/samples/{sample_id}", json={"age_bp": 9999})

        analysis = client.get(f"/api/analysis/{deployed['id']}").json()["data"]
        assert analysis["measurements"][0]["age_bp"] == 3000

    def test_deleting_a_collected_date_does_not_touch_deployed_analyses(self, client):
        pid = _make_project(client)
        sample_id = _add_dates(client, pid, [_date("OxA-1", 3000)])[0]["id"]
        deployed = client.post(
            f"/api/projects/{pid}/deploy", json={"name": "M", "sample_ids": [sample_id]}
        ).json()["data"]

        client.delete(f"/api/projects/{pid}/samples/{sample_id}")

        analysis = client.get(f"/api/analysis/{deployed['id']}").json()["data"]
        assert len(analysis["measurements"]) == 1
        assert analysis["measurements"][0]["age_bp"] == 3000


class TestDuplicate:
    def _saved_bayesian(self, client, name="Original"):
        return client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": name,
                "measurements": [
                    {"id": "a", "sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "group_id": "g1"},
                    {"id": "b", "sample_name": "B", "age_bp": 3200, "error_1sigma": 30, "group_id": "g2"},
                ],
                "groups": [
                    {"id": "g1", "name": "Upper", "tpq_age_bp": None, "taq_age_bp": None},
                    {"id": "g2", "name": "Lower", "tpq_age_bp": None, "taq_age_bp": None},
                ],
                "relationships": [
                    {"from_sample_id": "g2", "to_sample_id": "g1", "relationship": "below",
                     "from_type": "group", "to_type": "group"},
                ],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )

    def test_duplicate_copies_inputs_but_not_results(self, client):
        original_id = self._saved_bayesian(client).json()["data"]["id"]

        response = client.post(f"/api/analysis/{original_id}/duplicate")
        assert response.status_code == 200
        copy_id = response.json()["data"]["id"]
        assert copy_id != original_id
        assert response.json()["data"]["status"] == "pending"

        copy = client.get(f"/api/analysis/{copy_id}").json()["data"]
        assert copy["name"] == "Original (copy)"
        assert len(copy["measurements"]) == 2
        assert len(copy["groups"]) == 2
        assert len(copy["relationships"]) == 1
        # Inputs copied, results not.
        assert copy["results"] == []
        # MCMC settings carried across.
        assert copy["bayesian_config"]["mcmc_samples"] == 200

        # Ids are remapped to the copy's own rows, not shared with the original.
        original = client.get(f"/api/analysis/{original_id}").json()["data"]
        assert {m["id"] for m in copy["measurements"]}.isdisjoint({m["id"] for m in original["measurements"]})
        assert {g["id"] for g in copy["groups"]}.isdisjoint({g["id"] for g in original["groups"]})

        # The copied relationship points at the COPY's groups.
        copy_group_ids = {g["id"] for g in copy["groups"]}
        rel = copy["relationships"][0]
        assert rel["from_sample_id"] in copy_group_ids
        assert rel["to_sample_id"] in copy_group_ids

    def test_running_a_duplicate_leaves_the_original_untouched(self, client):
        original_id = self._saved_bayesian(client).json()["data"]["id"]
        before = client.get(f"/api/analysis/{original_id}").json()["data"]

        copy_id = client.post(f"/api/analysis/{original_id}/duplicate").json()["data"]["id"]
        assert client.post(f"/api/analysis/{copy_id}/rerun?wait=true").status_code == 200

        after = client.get(f"/api/analysis/{original_id}").json()["data"]
        assert after["status"] == before["status"]
        assert len(after["results"]) == len(before["results"])

    def test_duplicate_unknown_analysis_404s(self, client):
        assert client.post("/api/analysis/ghost/duplicate").status_code == 404


class TestReplaceInputs:
    def _body(self, **overrides):
        body = {
            "name": "Edited",
            "measurements": [
                {"id": "m1", "sample_name": "A", "age_bp": 3000, "error_1sigma": 30},
                {"id": "m2", "sample_name": "B", "age_bp": 3300, "error_1sigma": 30},
            ],
            "groups": [],
            "relationships": [],
            "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
        }
        body.update(overrides)
        return body

    def test_replacing_inputs_clears_stale_results(self, client):
        created = client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "Original",
                "measurements": [
                    {"id": "a", "sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "group_id": "g1"},
                    {"id": "b", "sample_name": "B", "age_bp": 3100, "error_1sigma": 30, "group_id": "g1"},
                ],
                "groups": [{"id": "g1", "name": "Ctx", "tpq_age_bp": None, "taq_age_bp": None}],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        ).json()["data"]["id"]

        before = client.get(f"/api/analysis/{created}").json()["data"]
        assert len(before["results"]) == 2
        assert before["groups"][0]["modeled_start"] is not None

        response = client.put(f"/api/analysis/{created}/inputs", json=self._body())
        assert response.status_code == 200

        after = client.get(f"/api/analysis/{created}").json()["data"]
        assert after["name"] == "Edited"
        assert after["status"] == "pending"
        assert after["results"] == []
        assert sorted(m["sample_name"] for m in after["measurements"]) == ["A", "B"]
        # The replacement supplied no contexts, so the rebuilt model tree
        # collects the dates into a single "Unassigned dates" phase -- which
        # is what `groups` now reflects, being derived from the tree.
        assert [g["name"] for g in after["groups"]] == ["Unassigned dates"]
        assert after["model_tree"] is not None

        # And it can be run again from the new inputs.
        assert client.post(f"/api/analysis/{created}/rerun?wait=true").status_code == 200
        assert len(client.get(f"/api/analysis/{created}").json()["data"]["results"]) == 2

    def test_replacing_inputs_rejects_a_cycle(self, client):
        created = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "material_type": "wood", "save": True},
        ).json()["data"]["id"]

        response = client.put(f"/api/analysis/{created}/inputs", json=self._body(
            measurements=[
                {"id": "x", "sample_name": "X", "age_bp": 3000, "error_1sigma": 30},
                {"id": "y", "sample_name": "Y", "age_bp": 3100, "error_1sigma": 30},
            ],
            relationships=[
                {"from_sample_id": "x", "to_sample_id": "y", "relationship": "above"},
                {"from_sample_id": "y", "to_sample_id": "x", "relationship": "above"},
            ],
        ))
        assert response.status_code == 422

    def test_replacing_inputs_rejects_dangling_group_reference(self, client):
        created = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "A", "age_bp": 3000, "error_1sigma": 30, "material_type": "wood", "save": True},
        ).json()["data"]["id"]

        response = client.put(f"/api/analysis/{created}/inputs", json=self._body(
            measurements=[{"id": "x", "sample_name": "X", "age_bp": 3000, "error_1sigma": 30, "group_id": "ghost"}],
        ))
        assert response.status_code == 422

    def test_replace_inputs_unknown_analysis_404s(self, client):
        assert client.put("/api/analysis/ghost/inputs", json=self._body()).status_code == 404
