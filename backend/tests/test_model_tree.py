"""
Tests for the explicit model structure: the tree itself
(services/model_tree.py), its persistence, and PUT /analysis/{id}/model-tree.

The planner tests here need no MCMC -- they are pure structure, which is
most of the point of keeping model_tree.py free of PyMC.
"""
import csv
import io

import pytest

from app.services.model_tree import (
    validate_tree,
    plan_model,
    inherited_bounds,
    migrate_from_relationships,
    flatten_structure,
)



def _boundary(element_id, name="Boundary"):
    return {"id": element_id, "element_type": "boundary", "name": name, "children": []}


def _phase(element_id, name="Phase", **kwargs):
    return {"id": element_id, "element_type": "phase", "name": name, "children": [], **kwargs}


def _sequence(element_id, name, children, **kwargs):
    return {"id": element_id, "element_type": "sequence", "name": name,
            "children": children, **kwargs}


def _dates(*, element_id, count, start=3000):
    return [{"id": f"{element_id}_{i}", "sample_name": f"{element_id}{i}",
             "age_bp": start + i * 30, "error_1sigma": 25, "element_id": element_id}
            for i in range(count)]


class TestValidation:
    def test_accepts_a_site_sequence(self):
        tree = _sequence("r", "Site", [
            _boundary("b0", "Start"), _phase("p1"), _boundary("b1"), _phase("p2"), _boundary("b2"),
        ])
        validate_tree(tree, _dates(element_id="p1", count=2) + _dates(element_id="p2", count=2))

    def test_rejects_date_attached_to_a_boundary(self):
        tree = _sequence("r", "Site", [_boundary("b0"), _phase("p1"), _boundary("b1")])
        with pytest.raises(ValueError, match="dates belong to a phase"):
            validate_tree(tree, _dates(element_id="b0", count=1))

    def test_rejects_orphan_date(self):
        tree = _sequence("r", "Site", [_boundary("b0"), _phase("p1"), _boundary("b1")])
        with pytest.raises(ValueError, match="unknown model element"):
            validate_tree(tree, _dates(element_id="nope", count=1))

    def test_rejects_duplicate_element_id(self):
        tree = _sequence("r", "Site", [_phase("dup"), _phase("dup")])
        with pytest.raises(ValueError, match="more than once"):
            validate_tree(tree, [])

    def test_rejects_hiatus_not_between_boundaries(self):
        tree = _sequence("r", "Site", [
            _phase("p1"), {"id": "h", "element_type": "hiatus", "name": "Gap", "children": []},
            _phase("p2"),
        ])
        with pytest.raises(ValueError, match="hiatus must sit between two boundaries"):
            validate_tree(tree, [])

    def test_accepts_hiatus_between_boundaries(self):
        tree = _sequence("r", "Site", [
            _boundary("b0"), _phase("p1"), _boundary("b1"),
            {"id": "h", "element_type": "hiatus", "name": "Gap", "children": []},
            _boundary("b2"), _phase("p2"), _boundary("b3"),
        ])
        validate_tree(tree, _dates(element_id="p1", count=2) + _dates(element_id="p2", count=2))

    def test_rejects_boundary_inside_a_phase(self):
        tree = _sequence("r", "Site", [_phase("p1", children=[_boundary("b")])])
        with pytest.raises(ValueError, match="cannot sit directly inside a phase"):
            validate_tree(tree, [])

    def test_rejects_contradictory_limiters(self):
        tree = _sequence("r", "Site", [_phase("p1", tpq_age_bp=100, taq_age_bp=500)])
        with pytest.raises(ValueError, match="taq_age_bp"):
            validate_tree(tree, [])


class TestPlanner:
    def test_shared_boundary_is_one_interior_parameter(self):
        tree = _sequence("r", "Site", [
            _boundary("b0", "Start"), _phase("p1"), _boundary("b1", "Transition"),
            _phase("p2"), _boundary("b2", "End"),
        ])
        measurements = _dates(element_id="p1", count=3) + _dates(element_id="p2", count=4)
        plan = plan_model(tree, measurements)

        assert plan.sampled_boundaries == ["b1"]
        assert plan.marginalized_boundaries == {"b0", "b2"}
        # Two groups, sharing b1 -- not four independent per-phase boundaries.
        assert [(g.older_boundary_id, g.younger_boundary_id, len(g.date_ids))
                for g in plan.span_groups] == [("b0", "b1", 3), ("b1", "b2", 4)]

    def test_nested_group_dates_are_not_double_counted(self):
        inner = _sequence("inner", "Inner", [_boundary("i0"), _phase("pi"), _boundary("i1")])
        tree = _sequence("r", "Site", [_boundary("b0"), inner, _boundary("b1")])
        measurements = _dates(element_id="pi", count=3)
        plan = plan_model(tree, measurements)

        by_pair = {(g.older_boundary_id, g.younger_boundary_id): len(g.date_ids)
                   for g in plan.span_groups}
        # The inner group claims its dates; the outer group sees none of them.
        assert by_pair[("i0", "i1")] == 3
        assert by_pair[("b0", "b1")] == 0

    def test_phase_of_sequences_is_unordered_between_branches(self):
        branch_a = _sequence("sa", "A", [_phase("pa1"), _phase("pa2")])
        branch_b = _sequence("sb", "B", [_phase("pb1"), _phase("pb2")])
        tree = _sequence("r", "Site", [_phase("par", children=[branch_a, branch_b])])
        measurements = (_dates(element_id="pa1", count=1) + _dates(element_id="pa2", count=1)
                        + _dates(element_id="pb1", count=1) + _dates(element_id="pb2", count=1))
        plan = plan_model(tree, measurements)

        pairs = [({t for _k, t in o}, {t for _k, t in y}) for o, y in plan.ordering]
        assert ({"pa1_0"}, {"pa2_0"}) in pairs
        assert ({"pb1_0"}, {"pb2_0"}) in pairs
        for older, younger in pairs:
            assert not (older & {"pa1_0", "pa2_0"} and younger & {"pb1_0", "pb2_0"})
            assert not (older & {"pb1_0", "pb2_0"} and younger & {"pa1_0", "pa2_0"})

    def test_hiatus_gives_two_boundaries_where_shared_gives_one(self):
        shared = _sequence("r", "S", [
            _boundary("b0"), _phase("p1"), _boundary("b1"), _phase("p2"), _boundary("b2")])
        gapped = _sequence("r", "S", [
            _boundary("b0"), _phase("p1"), _boundary("b1"),
            {"id": "h", "element_type": "hiatus", "name": "Gap", "children": []},
            _boundary("b2"), _phase("p2"), _boundary("b3")])
        m = _dates(element_id="p1", count=2) + _dates(element_id="p2", count=2)

        assert len(plan_model(shared, m).span_groups) == 2
        # The gapped model has an extra group -- the hiatus itself, empty --
        # and one more boundary, which is exactly the difference.
        gapped_plan = plan_model(gapped, m)
        assert len(gapped_plan.boundary_names) == 4
        assert any(len(g.date_ids) == 0 for g in gapped_plan.span_groups)

    def test_limiters_are_inherited_through_nesting(self):
        inner = _sequence("inner", "Inner", [_phase("p", tpq_age_bp=4000)], taq_age_bp=1000)
        tree = _sequence("r", "Site", [inner], tpq_age_bp=5000)
        bounds = inherited_bounds(tree)
        # Tightest wins: tpq from the phase, taq from the inner sequence.
        assert bounds["p"] == (1000, 4000)


class TestMigration:
    def _new_id(self):
        counter = iter(range(1000))
        return lambda: f"gen{next(counter)}"

    def test_chain_becomes_a_sequence_with_boundaries(self):
        measurements = [{"id": "a", "group_id": "g1"}, {"id": "b", "group_id": "g2"}]
        groups = [{"id": "g1", "name": "Upper"}, {"id": "g2", "name": "Lower"}]
        relationships = [{"from_sample_id": "g1", "to_sample_id": "g2",
                          "relationship": "above", "from_type": "group", "to_type": "group"}]

        root, linearized, group_boundaries = migrate_from_relationships(
            measurements, groups, relationships, self._new_id())

        types = [c["element_type"] for c in root["children"]]
        assert types == ["boundary", "phase", "boundary", "phase", "boundary"]
        # Law of superposition: g1 is above g2, so g2 (older) comes first.
        assert [c["id"] for c in root["children"] if c["element_type"] == "phase"] == ["g2", "g1"]
        assert not linearized
        # Adjacent contexts share the boundary between them.
        assert group_boundaries["g2"][1] == group_boundaries["g1"][0]

    def test_unordered_contexts_are_flagged_as_linearized(self):
        measurements = [{"id": "a", "group_id": "g1"}, {"id": "b", "group_id": "g2"}]
        groups = [{"id": "g1", "name": "One"}, {"id": "g2", "name": "Two"}]
        _root, linearized, _gb = migrate_from_relationships(
            measurements, groups, [], self._new_id())
        assert linearized

    def test_unrelated_contexts_keep_the_order_they_arrived_in(self):
        """
        With no relationships every context has indegree zero, so the
        tie-break decides the whole order -- and a Sequence ASSERTS that
        order. Broken on context id, which is a random uuid, eight sites
        deployed west to east came back as a Sequence claiming 5 before 3
        before 4: a model contradicting its own dates, which the solver then
        refused as unsolvable. The caller's order is the analyst's.
        """
        names = [f"Site {i}" for i in range(8)]
        # Ids deliberately sort the OPPOSITE way to the given order, so an id
        # tie-break cannot pass this by luck.
        groups = [{"id": f"z{7 - i}-{n}", "name": n} for i, n in enumerate(names)]
        measurements = [{"id": f"m{i}", "group_id": g["id"]}
                        for i, g in enumerate(groups)]

        root, _linearized, _gb = migrate_from_relationships(
            measurements, groups, [], self._new_id())

        phases = [c["name"] for c in root["children"] if c["element_type"] == "phase"]
        assert phases == names

    def test_relationships_still_outrank_the_arrival_order(self):
        """The tie-break is only a tie-break: a stated relationship wins."""
        groups = [{"id": "g1", "name": "Upper"}, {"id": "g2", "name": "Lower"}]
        measurements = [{"id": "a", "group_id": "g1"}, {"id": "b", "group_id": "g2"}]
        relationships = [{"from_sample_id": "g1", "to_sample_id": "g2",
                          "relationship": "above", "from_type": "group",
                          "to_type": "group"}]

        root, _linearized, _gb = migrate_from_relationships(
            measurements, groups, relationships, self._new_id())

        # g1 arrived first but is stratigraphically above, so g2 is older and
        # comes first regardless.
        assert [c["name"] for c in root["children"]
                if c["element_type"] == "phase"] == ["Lower", "Upper"]


class TestFlattenStructure:
    """
    The reading order shared by the results table and every export. The
    frontend mirrors this in utils/stratigraphy.flattenStructure, so a
    change here without a change there puts a date under one phase on
    screen and another in the CSV.
    """

    def _tree(self):
        return _sequence("root", "Site", [
            _boundary("b0", "Start"),
            _phase("p1", "Lower"),
            _boundary("b1", "Mid"),
            _phase("p2", "Upper"),
            _boundary("b2", "End"),
        ])

    def test_reads_depth_first_oldest_first(self):
        rows = flatten_structure(self._tree(), [
            {"id": "a", "sample_name": "A", "element_id": "p1"},
            {"id": "b", "sample_name": "B", "element_id": "p2"},
        ])
        assert [(r["kind"], r["name"]) for r in rows] == [
            ("sequence", "Site"),
            ("boundary", "Start"),
            ("phase", "Lower"),
            ("date", "A"),
            ("boundary", "Mid"),
            ("phase", "Upper"),
            ("date", "B"),
            ("boundary", "End"),
        ]

    def test_depth_and_path_describe_the_nesting(self):
        rows = flatten_structure(self._tree(), [
            {"id": "a", "sample_name": "A", "element_id": "p1"},
        ])
        date = next(r for r in rows if r["kind"] == "date")
        assert date["depth"] == 2
        assert date["path"] == "Site > Lower"

    def test_a_phases_own_dates_come_before_a_nested_branch(self):
        tree = _sequence("root", "Site", [
            _phase("p1", "Lower", children=[
                _sequence("inner", "Branch", [_phase("p2", "Deeper")]),
            ]),
        ])
        rows = flatten_structure(tree, [
            {"id": "a", "sample_name": "A", "element_id": "p1"},
            {"id": "b", "sample_name": "B", "element_id": "p2"},
        ])
        assert [r["name"] for r in rows] == ["Site", "Lower", "A", "Branch", "Deeper", "B"]

    def test_a_date_in_no_phase_is_still_reported(self):
        rows = flatten_structure(self._tree(), [
            {"id": "a", "sample_name": "A", "element_id": "p1"},
            {"id": "orphan", "sample_name": "Stray", "element_id": None},
        ])
        assert rows[-2]["kind"] == "unassigned"
        assert rows[-1]["name"] == "Stray"

    def test_group_id_places_a_date_from_a_migrated_tree(self):
        # A migrated tree reuses each context's id as its phase id, and
        # those analyses carry the id on group_id rather than element_id.
        rows = flatten_structure(self._tree(), [
            {"id": "a", "sample_name": "A", "group_id": "p1"},
        ])
        date = next(r for r in rows if r["kind"] == "date")
        assert date["path"] == "Site > Lower"

    def test_no_tree_yields_no_rows(self):
        assert flatten_structure(None, []) == []
        # Even with dates: a calibration has no structure to read, so the
        # caller falls back to a flat list rather than filing every date
        # under "Unassigned".
        assert flatten_structure(None, [{"id": "a", "sample_name": "A"}]) == []


class TestCsvExport:
    def test_csv_follows_the_model_structure(self, client):
        tree = _sequence("croot", "Site", [
            _boundary("cb0", "Start"),
            _phase("cp1", "Lower"),
            _boundary("cb1", "Mid"),
            _phase("cp2", "Upper"),
            _boundary("cb2", "End"),
        ])
        created = client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "CSV structure",
                "measurements": [
                    {"id": "a", "sample_name": "A", "age_bp": 3300, "error_1sigma": 30, "element_id": "cp1"},
                    {"id": "b", "sample_name": "B", "age_bp": 3000, "error_1sigma": 30, "element_id": "cp2"},
                ],
                "model_tree": tree,
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]

        body = client.get(f"/api/results/{analysis_id}/export/csv").text
        rows = list(csv.reader(io.StringIO(body)))
        header, data = rows[0], rows[1:]

        assert header[:5] == ['Order', 'Row Type', 'Depth', 'Structure Path', 'Name']
        assert [(r[1], r[4]) for r in data] == [
            ('Sequence', 'Site'),
            ('Boundary', 'Start'),
            ('Phase', 'Lower'),
            ('Date', 'A'),
            ('Boundary', 'Mid'),
            ('Phase', 'Upper'),
            ('Date', 'B'),
            ('Boundary', 'End'),
        ]
        # Order is written out so re-sorting a spreadsheet can restore the
        # structure the file exists to convey.
        assert [r[0] for r in data] == [str(i) for i in range(1, 9)]

        by_name = {r[4]: r for r in data}
        # A date carries what was measured; a boundary has no measurement
        # but does have a posterior, reported through the same columns.
        assert by_name['A'][5] == '3300.0'
        assert by_name['Start'][5] == ''
        assert by_name['Start'][11] != ''
        assert by_name['A'][11] != ''
        # A heading is structure only.
        assert by_name['Lower'][11] == ''

    def test_csv_without_a_stored_structure_still_lists_dates(self, client):
        created = client.post(
            "/api/analysis/calibrate",
            json={"sample_name": "Solo", "age_bp": 3000, "error_1sigma": 30,
                  "curve_id": "intcal20", "save": True},
        )
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]

        rows = list(csv.reader(io.StringIO(
            client.get(f"/api/results/{analysis_id}/export/csv").text)))
        assert rows[0][:5] == ['Order', 'Row Type', 'Depth', 'Structure Path', 'Name']
        assert [(r[1], r[4]) for r in rows[1:]] == [('Date', 'Solo')]

        # No unmodelled column for a plain calibration: the modelled columns
        # already ARE the unmodelled result, so filling it in would invite
        # the reader to compare a number with itself.
        unmodelled = rows[0].index('Unmodelled 95.4% HPD')
        assert rows[1][unmodelled] == ''

    def test_csv_reports_the_unmodelled_calibration_beside_the_modelled(self, client):
        """
        The column that used to be "Calibrated Age BP (mode)" -- never
        populated for a Bayesian analysis, since that field is only written
        on the calibration path -- now carries what each date calibrates to
        on its own, the comparison the results page shows beside it.
        """
        tree = _sequence("uroot", "Site", [
            _boundary("ub0", "Start"),
            _phase("up1", "Lower"),
            _boundary("ub1", "Mid"),
            _phase("up2", "Upper"),
            _boundary("ub2", "End"),
        ])
        created = client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "Unmodelled column",
                "measurements": [
                    {"id": "ua", "sample_name": "A", "age_bp": 3300, "error_1sigma": 30, "element_id": "up1"},
                    {"id": "ub", "sample_name": "B", "age_bp": 3000, "error_1sigma": 30, "element_id": "up2"},
                ],
                "model_tree": tree,
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]

        rows = list(csv.reader(io.StringIO(
            client.get(f"/api/results/{analysis_id}/export/csv").text)))
        header, data = rows[0], rows[1:]

        assert 'Calibrated Age BP (mode)' not in header
        unmodelled, modelled = (header.index('Unmodelled 95.4% HPD'),
                                header.index('95.4% HPD'))
        # The unmodelled column was repurposed in place rather than appended,
        # so it sits before the modelled ones and nothing downstream shifted.
        assert unmodelled < modelled
        # Agreement joins the modelled block, right after the HPD columns it
        # is judging.
        assert header.index('Agreement %') == modelled + 1
        assert header.index('Outlier %') == modelled + 2
        # The eighteen columns this export has always had, plus Latitude and
        # Longitude, which were APPENDED so that nothing already here moved.
        # Counted so a column duplicated rather than repurposed still shows up.
        assert len(header) == 22
        assert header[-2:] == ['Latitude', 'Longitude']

        by_name = {r[4]: r for r in data}
        # Every date reports one, formatted like the other HPD columns.
        for name in ('A', 'B'):
            assert '(' in by_name[name][unmodelled] and '%' in by_name[name][unmodelled]
            # And it is genuinely the unconstrained calibration, not a copy
            # of the posterior -- constraining the date is what the model
            # does, so the two must not agree.
            assert by_name[name][unmodelled] != by_name[name][modelled]

        # A boundary exists only as a modelled parameter, and a heading has
        # no date of its own, so neither has an unmodelled counterpart.
        assert by_name['Start'][unmodelled] == ''
        assert by_name['Lower'][unmodelled] == ''
        assert by_name['Site'][unmodelled] == ''


class TestModelTreeApi:
    def _analysis(self, client):
        return client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "Tree api",
                "measurements": [
                    {"id": "a", "sample_name": "A", "age_bp": 3300, "error_1sigma": 30, "group_id": "g1"},
                    {"id": "b", "sample_name": "B", "age_bp": 3000, "error_1sigma": 30, "group_id": "g2"},
                ],
                "groups": [{"id": "g1", "name": "Upper"}, {"id": "g2", "name": "Lower"}],
                "relationships": [{"from_sample_id": "g1", "to_sample_id": "g2",
                                   "relationship": "above", "from_type": "group", "to_type": "group"}],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        ).json()["data"]["id"]

    def test_get_returns_a_tree_with_boundary_posteriors(self, client):
        analysis_id = self._analysis(client)
        data = client.get(f"/api/analysis/{analysis_id}").json()["data"]

        tree = data["model_tree"]
        assert tree is not None
        assert tree["element_type"] == "sequence"
        types = [c["element_type"] for c in tree["children"]]
        assert types == ["boundary", "phase", "boundary", "phase", "boundary"]
        boundaries = [c for c in tree["children"] if c["element_type"] == "boundary"]
        assert all(b["posterior"] is not None for b in boundaries)
        # The shared transition is a single element carrying one posterior.
        assert boundaries[1]["posterior"]["posterior_mean"] > 0

    def test_get_strips_raw_samples_from_boundary_posteriors(self, client):
        # The draws stay in the database but never reach the client: with
        # 138 boundaries they were 10 MB of the response, and the edit page
        # re-serialised them to sessionStorage on every keystroke.
        analysis_id = self._analysis(client)
        data = client.get(f"/api/analysis/{analysis_id}").json()["data"]

        def walk(element):
            yield element
            for child in element.get("children") or []:
                yield from walk(child)

        posteriors = [e["posterior"] for e in walk(data["model_tree"]) if e.get("posterior")]
        assert posteriors
        assert all("posterior_samples" not in p for p in posteriors)
        assert all("probability_distribution" in p for p in posteriors)

    def test_put_replaces_the_tree_and_clears_results(self, client):
        analysis_id = self._analysis(client)
        data = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        sample_ids = [m["id"] for m in data["measurements"]]

        # Collapse both contexts into one phase inside a bracketed sequence.
        new_tree = _sequence("root2", "Site", [
            _boundary("nb0", "Start"), _phase("np1", "All one phase"), _boundary("nb1", "End"),
        ])
        response = client.put(
            f"/api/analysis/{analysis_id}/model-tree",
            json={"model_tree": new_tree,
                  "sample_elements": {sid: "np1" for sid in sample_ids}},
        )
        assert response.status_code == 200

        after = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        assert after["status"] == "pending"
        assert after["results"] == []
        assert [c["element_type"] for c in after["model_tree"]["children"]] == [
            "boundary", "phase", "boundary"]

        # And it runs from the new structure.
        assert client.post(f"/api/analysis/{analysis_id}/rerun?wait=true").status_code == 200
        rerun = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        assert len(rerun["results"]) == 2

    def test_put_rejects_an_invalid_tree(self, client):
        analysis_id = self._analysis(client)
        bad = _sequence("r2", "Site", [
            _phase("p1"), {"id": "h", "element_type": "hiatus", "name": "Gap", "children": []},
            _phase("p2"),
        ])
        response = client.put(
            f"/api/analysis/{analysis_id}/model-tree", json={"model_tree": bad})
        assert response.status_code == 422

    def test_create_an_analysis_from_a_tree_directly(self, client):
        """
        A client can author the structure itself rather than sending contexts
        + relationships to be converted -- the tree is used as given.
        """
        tree = _sequence("root", "Site", [
            _boundary("b0", "Site start"),
            _phase("p1", "Lower"),
            _boundary("b1", "Lower/Upper"),
            _phase("p2", "Upper"),
            _boundary("b2", "Site end"),
        ])
        response = client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "Authored as a tree",
                "measurements": [
                    {"id": "a", "sample_name": "A", "age_bp": 3300, "error_1sigma": 30, "element_id": "p1"},
                    {"id": "b", "sample_name": "B", "age_bp": 3330, "error_1sigma": 30, "element_id": "p1"},
                    {"id": "c", "sample_name": "C", "age_bp": 3000, "error_1sigma": 30, "element_id": "p2"},
                    {"id": "d", "sample_name": "D", "age_bp": 3030, "error_1sigma": 30, "element_id": "p2"},
                ],
                "model_tree": tree,
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )
        assert response.status_code == 200, response.text
        analysis_id = response.json()["data"]["id"]

        data = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        stored = data["model_tree"]
        assert [c["name"] for c in stored["children"]] == [
            "Site start", "Lower", "Lower/Upper", "Upper", "Site end"]
        # Contexts are derived from the tree's phases rather than stored twice.
        assert sorted(g["name"] for g in data["groups"]) == ["Lower", "Upper"]
        # Every date kept its phase assignment.
        by_name = {m["sample_name"]: m["element_id"] for m in data["measurements"]}
        assert by_name["A"] == by_name["B"] == "p1"
        assert by_name["C"] == by_name["D"] == "p2"
        # And every boundary got a posterior.
        boundaries = [c for c in stored["children"] if c["element_type"] == "boundary"]
        assert all(b["posterior"] is not None for b in boundaries)

    def test_replacing_inputs_keeps_an_authored_tree(self, client):
        """
        Editing a tree-authored analysis and re-running it must keep the
        structure. PUT /inputs used to drop every ModelElement row and
        ignore the request's model_tree, so the next run rebuilt a tree
        from the (empty) relationships instead: the phase order came back
        arbitrary and every date landed in an "Unassigned dates" phase.
        """
        # Element ids are a global primary key, so these must not collide
        # with the ids any other test in this module persists.
        tree = _sequence("eroot", "Site", [
            _boundary("eb0", "Site start"),
            _phase("ep1", "Lower"),
            _boundary("eb1", "Lower/Upper"),
            _phase("ep2", "Upper"),
            _boundary("eb2", "Site end"),
        ])
        created = client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "Tree edit",
                "measurements": [
                    {"id": "a", "sample_name": "A", "age_bp": 3300, "error_1sigma": 30, "element_id": "ep1"},
                    {"id": "b", "sample_name": "B", "age_bp": 3000, "error_1sigma": 30, "element_id": "ep2"},
                ],
                "model_tree": tree,
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
                "save": True,
            },
        )
        assert created.status_code == 200, created.text
        analysis_id = created.json()["data"]["id"]
        before = client.get(f"/api/analysis/{analysis_id}").json()["data"]

        # Resubmit exactly what the editor sends: the tree plus each date's
        # element_id, with no contexts or pairwise relationships.
        replaced = client.put(
            f"/api/analysis/{analysis_id}/inputs",
            json={
                "name": before["name"],
                "measurements": [
                    {"id": m["id"], "sample_name": m["sample_name"], "age_bp": m["age_bp"],
                     "error_1sigma": m["error_1sigma"], "element_id": m["element_id"]}
                    for m in before["measurements"]
                ],
                "groups": [], "relationships": [],
                "model_tree": before["model_tree"],
                "bayesian_config": {"mcmc_samples": 200, "mcmc_tune": 200, "mcmc_chains": 1},
            },
        )
        assert replaced.status_code == 200, replaced.text
        assert client.post(f"/api/analysis/{analysis_id}/rerun?wait=true").status_code == 200

        after = client.get(f"/api/analysis/{analysis_id}").json()["data"]
        assert [c["name"] for c in after["model_tree"]["children"]] == [
            "Site start", "Lower", "Lower/Upper", "Upper", "Site end"]
        # Each date is still in the phase it was assigned to -- no synthetic
        # "Unassigned dates" phase, and no silent reshuffle.
        phases = {c["id"]: c["name"] for c in after["model_tree"]["children"]
                  if c["element_type"] == "phase"}
        assigned = {m["sample_name"]: phases.get(m["element_id"]) for m in after["measurements"]}
        assert assigned == {"A": "Lower", "B": "Upper"}

    def test_create_rejects_a_tree_with_an_orphan_date(self, client):
        response = client.post(
            "/api/analysis/bayesian?wait=true",
            json={
                "name": "Bad tree",
                "measurements": [
                    {"id": "a", "sample_name": "A", "age_bp": 3000, "error_1sigma": 30,
                     "element_id": "nope"},
                ],
                "model_tree": _sequence("r", "S", [_phase("p1", "P")]),
                "save": False,
            },
        )
        assert response.status_code == 422

    def test_put_unknown_analysis_404s(self, client):
        assert client.put(
            "/api/analysis/ghost/model-tree",
            json={"model_tree": _sequence("r", "S", [])},
        ).status_code == 404
