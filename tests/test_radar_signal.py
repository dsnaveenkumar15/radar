from radar.signal import SIGNAL_VERSION, formula, score_cluster


def it(source, tier, kind="post", repo=None):
    return {"source": source, "tier": tier, "kind": kind, "repo": repo}


def test_official_release_with_an_api_break_scores_high_and_parts_add_up():
    result = score_cluster([it("gh", "official", "release", "a/x")], velocity=1000, has_api_change=True)
    assert result["parts"] == {"tier": 40.0, "corroboration": 0.0, "velocity": 20.0, "code": 20.0}
    assert result["score"] == 80 and result["version"] == SIGNAL_VERSION


def test_community_single_source_scores_low_and_corroboration_helps():
    alone = score_cluster([it("hn", "community")], velocity=0, has_api_change=False)
    assert alone["score"] == 16 and alone["parts"]["code"] == 0.0
    three = score_cluster([it("hn", "community"), it("blog", "community"), it("arxiv", "community")], 0, False)
    assert three["parts"]["corroboration"] == 13.3 and three["score"] == 29
    many = score_cluster([it(f"s{i}", "community") for i in range(9)], 0, False)
    assert many["parts"]["corroboration"] == 20.0


def test_code_behind_the_claim_without_a_diff_is_partial_credit_and_velocity_is_log_scaled():
    release = score_cluster([it("gh", "official", "release", "a/x")], velocity=0, has_api_change=False)
    assert release["parts"]["code"] == 12.0
    assert score_cluster([it("gh", "official")], velocity=10, has_api_change=False)["parts"]["velocity"] == 6.9
    assert score_cluster([it("gh", "official")], velocity=10**6, has_api_change=False)["parts"]["velocity"] == 20.0
    assert score_cluster([it("gh", "official")], velocity=-5, has_api_change=False)["parts"]["velocity"] == 0.0


def test_formula_is_published_with_its_weights():
    doc = formula()
    assert doc["version"] == SIGNAL_VERSION and doc["weights"] == {"tier": 40, "corroboration": 20, "velocity": 20, "code": 20}
