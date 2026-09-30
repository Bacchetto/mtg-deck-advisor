import asyncio
import re

from mtg_deck_advisor.observability.tracing import current_trace_id, new_trace_id, traced


def test_new_trace_ids_are_32_hex_characters_and_unique() -> None:
    ids = {new_trace_id() for _ in range(1000)}

    assert len(ids) == 1000
    assert all(re.fullmatch(r"[0-9a-f]{32}", trace_id) for trace_id in ids)


def test_there_is_no_trace_id_outside_a_trace() -> None:
    assert current_trace_id() is None


def test_traced_sets_a_trace_id_and_restores_the_previous_one() -> None:
    with traced() as trace_id:
        assert current_trace_id() == trace_id

    assert current_trace_id() is None


def test_traced_accepts_a_given_trace_id() -> None:
    with traced("0123456789abcdef0123456789abcdef") as trace_id:
        assert trace_id == "0123456789abcdef0123456789abcdef"
        assert current_trace_id() == trace_id


def test_nested_traces_restore_the_outer_trace_id() -> None:
    with traced() as outer:
        with traced() as inner:
            assert current_trace_id() == inner
        assert current_trace_id() == outer


def test_trace_id_is_restored_when_the_block_raises() -> None:
    try:
        with traced():
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert current_trace_id() is None


def test_concurrent_tasks_each_see_only_their_own_trace_id() -> None:
    async def handle_request() -> tuple[str, str | None]:
        with traced() as trace_id:
            # Yield to the event loop so the other task runs in between.
            await asyncio.sleep(0)
            return trace_id, current_trace_id()

    async def main() -> list[tuple[str, str | None]]:
        return await asyncio.gather(handle_request(), handle_request())

    results = asyncio.run(main())

    assert [seen for _, seen in results] == [assigned for assigned, _ in results]
    assert results[0][0] != results[1][0]
