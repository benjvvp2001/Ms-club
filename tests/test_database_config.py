from __future__ import annotations

import pytest
from psycopg.conninfo import conninfo_to_dict

from app.core.config import get_database_settings
from app.database.session import check_connection

DB_ENV = {
    "DB_HOST": "db.internal",
    "DB_PORT": "5433",
    "DB_USER": "sportmatch_clubs",
    "DB_PASSWORD": "p@ss word'with=chars",
    "DB_NAME": "sportmatch_clubs",
}


@pytest.fixture
def db_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name, value in DB_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_conninfo_is_built_from_db_variables_and_quotes_the_password(db_env):
    params = conninfo_to_dict(get_database_settings().conninfo())
    assert params["host"] == "db.internal"
    assert params["port"] == "5433"
    assert params["user"] == "sportmatch_clubs"
    assert params["password"] == "p@ss word'with=chars"
    assert params["dbname"] == "sportmatch_clubs"


def test_host_port_and_name_have_defaults(db_env):
    for name in ("DB_HOST", "DB_PORT", "DB_NAME"):
        db_env.delenv(name)
    settings = get_database_settings()
    assert (settings.host, settings.port, settings.name) == ("localhost", 5432, "sportmatch_clubs")


@pytest.mark.parametrize("name", ["DB_USER", "DB_PASSWORD"])
def test_credentials_are_required(db_env, name):
    db_env.setenv(name, "  ")
    with pytest.raises(RuntimeError, match=name):
        get_database_settings()


@pytest.mark.parametrize("port", ["abc", "0", "70000"])
def test_invalid_port_is_rejected(db_env, port):
    db_env.setenv("DB_PORT", port)
    with pytest.raises(RuntimeError, match="DB_PORT"):
        get_database_settings()


def test_startup_check_reports_the_target_without_the_password(db_env):
    db_env.setenv("DB_HOST", "127.0.0.1")
    db_env.setenv("DB_PORT", "1")  # nothing listens there
    with pytest.raises(RuntimeError) as error:
        check_connection(get_database_settings())
    message = str(error.value)
    assert "sportmatch_clubs@127.0.0.1:1/sportmatch_clubs" in message
    assert DB_ENV["DB_PASSWORD"] not in message
