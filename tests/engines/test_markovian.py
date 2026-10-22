from app.engines.markovian import ChunkManager, MarkovianConfig
from app.errors import InvestigationError


def test_carryover_is_bounded_and_cascades():
    clock = {"now": 0.0}
    manager = ChunkManager(MarkovianConfig(max_chunks=2, max_carryover_chars=12), clock=lambda: clock["now"])

    def step(incoming: str):
        return {"saw": incoming}, "x" * 50

    first = manager.run("one", step)
    second = manager.run("two", step)
    assert first == {"saw": ""}
    assert second == {"saw": "x" * 12}
    assert manager.chunks[1].carryover_in == manager.chunks[0].carryover_out
    assert len(manager.carryover) == 12


def test_a_slow_call_is_not_returned_after_the_step_budget():
    clock = {"now": 0.0}
    manager = ChunkManager(MarkovianConfig(max_chunks=2, chunk_timeout_seconds=1), clock=lambda: clock["now"])

    def slow(incoming: str):
        clock["now"] = 5.0
        return {"finished": True}, "late"

    try:
        manager.run("slow", slow)
    except InvestigationError as exc:
        assert exc.error_type == "StepTimeout"
    else:
        raise AssertionError("expected the step timeout")
    assert manager.chunks == []


def test_chunk_limit_stops_the_loop():
    manager = ChunkManager(MarkovianConfig(max_chunks=1, max_carryover_chars=20))
    manager.run("only", lambda incoming: ("ok", "done"))
    try:
        manager.run("overflow", lambda incoming: ("no", "done"))
    except InvestigationError as exc:
        assert exc.error_type == "IterationLimit"
    else:
        raise AssertionError("expected the chunk limit")
