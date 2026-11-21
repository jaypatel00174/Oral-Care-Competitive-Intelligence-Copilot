"""The evaluation suite itself: enough questions, the offline assistant passes every gate, and the
new checks (ground truth, retrieval recall, judge) catch what they are meant to catch."""
import json

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.evaluation import judge, retrieval_eval, run_eval, truth

TABLE = truth.load_table()


def test_suite_has_at_least_15_questions_with_unique_ids():
    cases = json.loads(run_eval.QUESTIONS.read_text(encoding="utf-8"))
    assert len(cases) >= 15
    assert len({c["id"] for c in cases}) == len(cases)


def test_offline_assistant_passes_all_quality_gates():
    results, summary = run_eval.run(run_eval.QUESTIONS, llm=None)
    failed = [(r.id, r.failures) for r in results if not r.passed and not r.xfail]
    assert summary["all_gates_passed"], failed


def test_known_gaps_are_reported_but_not_counted(tmp_path):
    cases = json.loads(run_eval.QUESTIONS.read_text(encoding="utf-8"))[:3]
    cases.append({"id": "GAP", "category": "x", "question": "How long does the Oral-B iO7 battery last?",
                  "expect_status": "answered", "known_gap": "demo: the assistant correctly says not found"})
    path = tmp_path / "q.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    results, summary = run_eval.run(path, llm=None)
    assert summary["known_gaps"]["cases"] == ["GAP"] and summary["known_gaps"]["still_failing"] == ["GAP"]
    assert summary["total"] == 3 and summary["all_gates_passed"]


def test_no_case_in_the_shipped_suite_is_marked_as_a_known_gap():
    cases = json.loads(run_eval.QUESTIONS.read_text(encoding="utf-8"))
    assert [c["id"] for c in cases if c.get("known_gap")] == []


# ---- ground truth ------------------------------------------------------------------------
PRICE = {"truth": [{"product_id": "PH-HX9903_15", "field": "price_usd"}], "strict_attribution": True}


def test_truth_accepts_the_right_price():
    assert truth.check(PRICE, "The 9300 costs $199.96.", {"PH-HX9903_15"}, TABLE) == []


def test_truth_rejects_a_wrong_price():
    assert truth.check(PRICE, "The 9300 costs $249.96.", {"PH-HX9903_15"}, TABLE)


def test_truth_rejects_another_products_price_even_next_to_the_right_one():
    problems = truth.check(PRICE, "It costs $199.96 (the iO9 is $329.99).", {"PH-HX9903_15"}, TABLE)
    assert any("different product" in p for p in problems)


def test_truth_accepts_natural_wordings_of_the_mode_count():
    case = {"truth": [{"product_id": "OB-io6", "field": "mode_count"}]}   # 5 modes
    for text in ("It has 5 modes.", "The iO6 has 5 brushing modes: ...", "It offers five smart cleaning modes."):
        assert truth.check(case, text, set(), TABLE) == [], text
    assert truth.check(case, "It has 7 brushing modes.", set(), TABLE)


def test_every_fact_is_cited_in_full_when_it_has_up_to_five_snippets():
    from src.services.assistant import ask

    a = ask("How many brushing modes does the Sonicare 9300 have?", llm=None)
    assert {"PH-HX9903_15#028", "PH-HX9903_15#029", "PH-HX9903_15#030", "PH-HX9903_15#031"} <= {c.id for c in a.citations}


def test_truth_accepts_natural_wordings_of_a_duration():
    case = {"truth": [{"product_id": "PH-HX9903_15", "field": "battery_life"}]}   # "14 days"
    for text in ("The battery lasts 14 days.", "It has a 14-day battery life.", "a 14 day battery"):
        assert truth.check(case, text, set(), TABLE) == [], text
    assert truth.check(case, "It has a 21-day battery.", set(), TABLE)
    assert truth.check(case, "Battery: 114 days.", set(), TABLE)          # a different number is not a match


def test_truth_requires_not_found_for_a_blank_cell():
    case = {"truth": [{"product_id": "OB-io7", "field": "battery_life"}]}
    assert truth.check(case, "not found in sources", set(), TABLE) == []
    assert truth.check(case, "The battery lasts 14 days.", set(), TABLE)


def test_truth_rejects_a_false_not_found_for_a_true_feature():
    case = {"truth": [{"product_id": "PH-HX9903_15", "field": "pressure_sensor"}]}
    assert truth.check(case, "Pressure sensor: not found in sources", {"PH-HX9903_15"}, TABLE)
    assert truth.check(case, "Ring lights up purple", {"PH-HX9903_15"}, TABLE) == []


# ---- retrieval ---------------------------------------------------------------------------
def test_retrieval_meets_its_gates():
    out = retrieval_eval.run()
    assert out["total"] >= 15 and out["all_gates_passed"], out["metrics"]


def test_retrieval_eval_counts_a_miss():
    from src.storage.vector_store import Hit

    def wrong(query, k=5, **_):
        return [Hit("OB-io6#001", "unrelated", 0.9, "OB-io6", "Oral-B", "x", "u")]

    out = retrieval_eval.run(search=wrong)
    assert out["metrics"]["product_recall"] < 0.5 and not out["all_gates_passed"]


# ---- judge -------------------------------------------------------------------------------
def test_judge_parses_json_even_with_extra_text():
    llm = FakeListChatModel(responses=['Sure! {"faithful": false, "unsupported_claims": ["3-minute timer"]}'])
    out = judge.judge_text(llm, "evidence", "answer")
    assert out == {"faithful": False, "unsupported_claims": ["3-minute timer"]}


def test_judge_calibration_passes_for_a_correct_judge_and_fails_for_a_lenient_one():
    right = FakeListChatModel(responses=['{"faithful": true}', '{"faithful": false}', '{"faithful": false}'])
    assert judge.calibrate(right)["reliable"]
    lenient = FakeListChatModel(responses=['{"faithful": true}'])
    assert not judge.calibrate(lenient)["reliable"]


def test_judge_calibration_reports_garbled_output_as_error():
    out = judge.calibrate(FakeListChatModel(responses=["I cannot say"]))
    assert not out["reliable"] and out["error"]


def test_failing_model_calls_fail_the_llm_call_success_gate():
    class Throttled429:
        def invoke(self, *_a, **_k):
            raise RuntimeError("Error code: 429 rate limit")

    results, summary = run_eval.run(run_eval.QUESTIONS, llm=Throttled429())
    assert summary["llm_errors"] > 0 and summary["gates"]["llm_call_success"]["passed"] is False
    assert not summary["all_gates_passed"] and run_eval._warnings(summary)
    assert summary["pass_rate_per_repeat"][0] > 0.9       # answers stay correct: the safe writer covered for the model


def test_judge_failure_does_not_break_the_run():
    class Broken:
        def invoke(self, *_a, **_k):
            raise RuntimeError("down")

    results, summary = run_eval.run(run_eval.QUESTIONS, llm=None, judge=Broken(), judge_reliable=True)
    assert summary["judge"]["errors"] > 0 and "judge_faithfulness" not in summary["metrics"]
