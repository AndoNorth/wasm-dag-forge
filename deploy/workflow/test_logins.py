"""The service logins against a migrated app database: two applies, the second changing nothing,
each login in only its group role and able to log in, drift found and repaired, and the two
Secrets of the module, whose store Secret holds the three store identities of the service only."""

import importlib.util
import json
from pathlib import Path

import psycopg
import pytest
from wdf_storecheck.s3 import Creds, dump_credentials

_spec = importlib.util.spec_from_file_location(
    "workflow_logins", Path(__file__).with_name("logins.py")
)
logins = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(logins)

ITEMS = [f"login: {login} in {group}" for login, group in logins.LOGINS.values()]


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("WDF_STATE_DIR", str(tmp_path))
    return tmp_path


def run(mode, dsn, capsys) -> tuple[int, list[str], list[str]]:
    code = logins.main(mode, dsn)
    out, err = capsys.readouterr()
    return code, out.splitlines(), err.splitlines()


def memberships(dsn: str) -> dict[str, list[str]]:
    with psycopg.connect(dsn) as conn:
        return {
            login: [g for g, *_ in conn.execute(logins.MEMBER_OF, (login,))]
            for login, _ in logins.LOGINS.values()
        }


@pytest.fixture
def server(appdb):
    """The migrated database, on a server that holds none of the logins, which are server wide
    and so outlive the database of an earlier test."""
    with psycopg.connect(appdb, autocommit=True) as conn:
        for login, _ in logins.LOGINS.values():
            conn.execute(f"drop role if exists {login}")
    return appdb


@pytest.mark.integration
def test_apply_twice_then_check(server, capsys):
    appdb = server
    assert run("check", appdb, capsys) == (
        1,
        [f"{item} missing" for item in ITEMS],
        [f"differs: {item}" for item in ITEMS],
    )
    assert run("apply", appdb, capsys) == (
        0,
        [f"{item} created" for item in ITEMS] + ["changed: 3"],
        [],
    )
    assert run("apply", appdb, capsys) == (
        0,
        [f"{item} unchanged" for item in ITEMS] + ["changed: 0"],
        [],
    )
    assert run("check", appdb, capsys) == (0, [f"{item} unchanged" for item in ITEMS], [])
    assert memberships(appdb) == {login: [group] for login, group in logins.LOGINS.values()}


@pytest.mark.integration
def test_drift_found_and_repaired(server, state, capsys):
    appdb = server
    logins.main("apply", appdb)
    workflow, artifacts, _ = (login for login, _ in logins.LOGINS.values())
    reaper = logins.LOGINS["WDF_REAPER_DSN"][0]
    with psycopg.connect(appdb, autocommit=True) as conn:
        conn.execute(f"grant wdf_reader to {workflow}")
        conn.execute(f"alter role {reaper} createrole")
    known = json.loads(logins.state_file().read_text())
    known[artifacts] = "rotated"
    logins.state_file().write_text(json.dumps(known))
    capsys.readouterr()

    code, out, err = run("check", appdb, capsys)
    assert code == 1
    assert f"{ITEMS[0]} differs: member of wdf_reader, wdf_workflow" in out
    assert f"{ITEMS[1]} differs: password" in out
    assert f"{ITEMS[2]} differs: attributes" in out
    assert err == [f"differs: {item}" for item in ITEMS]

    code, out, _ = run("apply", appdb, capsys)
    assert (code, out[-1]) == (0, "changed: 3")
    assert run("check", appdb, capsys)[0] == 0
    assert memberships(appdb)[workflow] == ["wdf_workflow"]


def test_render_secrets(state, tmp_path):
    store = tmp_path / "store.json"
    roles = ("admin", "submit", "workflow", "indexer", "reaper", "compile")
    dump_credentials(store, {role: Creds(f"{role}-id", f"{role}-key") for role in roles})
    identities = json.loads(store.read_text())
    first = logins.render(str(store))
    assert logins.render(str(store)) == first
    assert oct(logins.state_file().stat().st_mode & 0o777) == "0o600"

    dsns, credentials = first
    assert [s["metadata"] for s in first] == [
        {"name": "workflow-worker-logins", "namespace": "wdf-control"},
        {"name": "workflow-worker-store", "namespace": "wdf-control"},
    ]
    known = json.loads(logins.state_file().read_text())
    assert dsns["stringData"] == {
        env: f"postgresql://{login}:{known[login]}@appdb.wdf-infra.svc.cluster.local:5432/postgres"
        for env, (login, _) in logins.LOGINS.items()
    }
    held = json.loads(credentials["stringData"]["credentials.json"])
    assert held == {role: identities[role] for role in ("workflow", "indexer", "reaper")}
