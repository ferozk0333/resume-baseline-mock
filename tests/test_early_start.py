"""Early start (specs/18 §2 item 7): the roles picked before Review are judged as soon as the documents are read;
Confirm only finishes what's left; an item edited in Review is judged again. FakeJudge, offline."""
from graph.pipeline import Pipeline
from tests.test_candidate import env, new_profile, roles  # noqa: F401  (fixture)

DS = "baseline-data-scientist-entry"


def read_with_roles(env, picks):  # noqa: F811
    api = env.api
    cid, read = new_profile(api)
    api.put(f"/candidates/{cid}/targets", json={"baseline_ids": picks})       # picked while reading
    api.tasks.wait(read)
    pipe = api.app.state.rs.pipeline
    done = pipe.wait_for_prejudge(cid)
    return cid, pipe, done


def confirm(env, cid):  # noqa: F811
    t = env.api.post(f"/candidates/{cid}/baseline-scores").json()["task_id"]
    task = env.api.tasks.wait(t)
    assert task["status"] == "done", task
    for run_id in task["result"]["runs"].values():
        Pipeline.wait_for_plan(run_id)
    return task


def test_selected_roles_are_judged_before_confirm_and_confirm_only_finishes(env):  # noqa: F811
    picks = roles(env.api)[:2]
    cid, pipe, done = read_with_roles(env, picks)
    assert set(done) == set(picks) and all(n > 0 for n in done.values())      # both roles started on their own
    before = len(env.judge.calls)
    assert before > 0
    task = confirm(env, cid)
    assert set(task["result"]["runs"]) == set(picks)
    new = env.judge.calls[before:]
    assert new == [], [k for k, _ in new]                                       # nothing left to ask


def test_an_item_edited_in_review_is_judged_again(env):  # noqa: F811
    cid, pipe, _ = read_with_roles(env, [DS])
    profile = env.api.get(f"/candidates/{cid}/profile").json()["profile"]
    role = profile["roles"][0]
    bullets = [b["text"] for b in role["bullets"]]
    bullets[0] += " Shipped to 3 stores."
    r = env.api.patch(f"/candidates/{cid}/profile", json={"op": "edit", "item": "role", "id": role["id"], "data": {
        "title": role["title"], "employer": role["employer"], "start_date": role["start_date"][:7],
        "end_date": role["end_date"][:7] if role["end_date"] else None, "is_current": role["is_current"],
        "bullets": bullets}})
    assert r.status_code == 200, r.text
    before = len(env.judge.calls)
    confirm(env, cid)
    asked = env.judge.calls[before:]
    assert asked and any("Shipped to 3 stores" in str(ctx) for _, ctx in asked)


def test_roles_picked_after_reading_start_at_once(env):  # noqa: F811
    api = env.api
    cid, read = new_profile(api)
    api.tasks.wait(read)                                                       # no role picked yet
    pipe = api.app.state.rs.pipeline
    assert pipe.wait_for_prejudge(cid) == {}
    api.put(f"/candidates/{cid}/targets", json={"baseline_ids": [DS]})
    assert set(pipe.wait_for_prejudge(cid)) == {DS}
