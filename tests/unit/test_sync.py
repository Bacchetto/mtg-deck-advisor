from mtg_deck_advisor.ingestion.sync import Existing, diff


def test_new_keys_are_added() -> None:
    result = diff({}, {"a": "h1", "b": "h2"})

    assert sorted(result.added) == ["a", "b"]
    assert result.updated == result.unchanged == result.removed == result.restored == []


def test_same_hash_is_unchanged_and_different_hash_is_updated() -> None:
    existing = {"a": Existing("h1", removed=False), "b": Existing("h2", removed=False)}

    result = diff(existing, {"a": "h1", "b": "h2-changed"})

    assert result.unchanged == ["a"]
    assert result.updated == ["b"]
    assert result.added == result.removed == result.restored == []


def test_keys_missing_from_the_incoming_data_are_removed() -> None:
    existing = {"a": Existing("h1", removed=False), "b": Existing("h2", removed=False)}

    result = diff(existing, {"a": "h1"})

    assert result.removed == ["b"]


def test_an_already_removed_key_that_stays_missing_is_left_alone() -> None:
    existing = {"a": Existing("h1", removed=True)}

    result = diff(existing, {})

    assert result.removed == []


def test_a_removed_key_that_returns_is_restored_whatever_its_hash() -> None:
    existing = {"a": Existing("h1", removed=True), "b": Existing("h2", removed=True)}

    result = diff(existing, {"a": "h1", "b": "h2-changed"})

    assert sorted(result.restored) == ["a", "b"]
    assert result.added == result.updated == result.unchanged == []


def test_report_counts_restored_keys_as_added() -> None:
    existing = {"a": Existing("h1", removed=True), "b": Existing("h2", removed=False)}

    report = diff(existing, {"a": "h1", "c": "h3"}).report()

    assert (report.added, report.updated, report.unchanged, report.removed) == (2, 0, 0, 1)
