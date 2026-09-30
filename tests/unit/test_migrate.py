import pytest
from alembic.script import ScriptDirectory

from mtg_deck_advisor.config import Settings
from mtg_deck_advisor.db.migrate import alembic_config, sqlalchemy_url


@pytest.mark.parametrize(
    ("database_url", "expected"),
    [
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgresql+psycopg://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
    ],
)
def test_sqlalchemy_url_selects_the_psycopg_3_driver(database_url: str, expected: str) -> None:
    assert sqlalchemy_url(database_url) == expected


def test_a_password_containing_percent_survives_the_alembic_config() -> None:
    # Alembic's options pass through ConfigParser, which treats % as
    # interpolation syntax. A URL-encoded password such as p%40ss must reach
    # the engine unchanged.
    settings = Settings(_env_file=None, database_url="postgresql://u:p%40ss@h/db")

    config = alembic_config(settings)

    assert config.attributes["database_url"] == "postgresql+psycopg://u:p%40ss@h/db"


def test_migration_history_has_exactly_one_head() -> None:
    # Two heads means two migrations claim the same parent, usually from
    # branches merged without rebasing. `upgrade head` then refuses to run.
    settings = Settings(_env_file=None, database_url="postgresql://u:p@h/db")

    heads = ScriptDirectory.from_config(alembic_config(settings)).get_heads()

    assert len(heads) == 1
