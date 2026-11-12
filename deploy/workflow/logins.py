"""The service logins of the workflow worker, and the Secrets of its logins and store identities.

The group roles of the app database cannot log in, so each writer the service runs gets a login in
its group: `emit` in `wdf_workflow`, the artifact activities in `wdf_artifacts` and the reaper in
`wdf_reaper`. Their passwords are generated on first use into the state directory of the cluster
scripts and never committed.
Usage: logins.py render STORE_CREDENTIALS | apply DSN | check DSN
`render` prints the two Secrets of the module, the DSNs of the logins and the store identities of
the service read from the credentials file of the store, as a JSON list.
`apply` creates or converges each login as the superuser of DSN and prints one line per login with
what it did, ending with `changed: <n>`, and `check` exits 1 when a login differs or cannot log in.
"""

import json
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

# Each login and the one group role it is a member of, keyed by the environment variable of its DSN.
LOGINS = {
    "WDF_WORKFLOW_DSN": ("workflow_worker_workflow", "wdf_workflow"),
    "WDF_ARTIFACTS_DSN": ("workflow_worker_artifacts", "wdf_artifacts"),
    "WDF_REAPER_DSN": ("workflow_worker_reaper", "wdf_reaper"),
}
STORE_ROLES = ("workflow", "indexer", "reaper")
NAMESPACE = "wdf-control"
DATABASE = "appdb.wdf-infra.svc.cluster.local:5432/postgres"
CREDENTIALS_FILE = "credentials.json"


def state_file() -> Path:
    state = os.environ.get("WDF_STATE_DIR") or str(
        Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "wasm-dag-forge"
    )
    return Path(state) / "workflow-worker.logins.json"


def secrets_of_logins() -> dict[str, str]:
    """The password of each login, generated into the state file on first use."""
    path = state_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        generated = {login: secrets.token_hex(24) for login, _ in LOGINS.values()}
        tmp = path.with_suffix(".tmp")
        tmp.touch(mode=0o600)
        tmp.write_text(json.dumps(generated, indent=2) + "\n")
        tmp.replace(path)
    return json.loads(path.read_text())


def secret(name: str, data: dict[str, str]) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "type": "Opaque",
        "stringData": data,
    }


def render(store_credentials: str) -> list[dict]:
    known = secrets_of_logins()
    identities = json.loads(Path(store_credentials).read_text())
    return [
        secret(
            "workflow-worker-logins",
            {
                env: f"postgresql://{login}:{known[login]}@{DATABASE}"
                for env, (login, _) in LOGINS.items()
            },
        ),
        secret(
            "workflow-worker-store",
            {CREDENTIALS_FILE: json.dumps({role: identities[role] for role in STORE_ROLES})},
        ),
    ]


MEMBER_OF = """
select g.rolname from pg_auth_members m
join pg_roles g on g.oid = m.roleid
join pg_roles l on l.oid = m.member
where l.rolname = %s
order by 1
"""


def state(conn: psycopg.Connection, login: str, group: str) -> str:
    row = conn.execute(
        "select rolcanlogin, rolinherit from pg_roles where rolname = %s", (login,)
    ).fetchone()
    if row is None:
        return "missing"
    groups = [g for (g,) in conn.execute(MEMBER_OF, (login,))]
    wrong = [] if row == (True, True) else ["cannot log in or inherit"]
    if groups != [group]:
        wrong.append(f"member of {', '.join(groups) or 'nothing'}")
    return f"differs: {'; '.join(wrong)}" if wrong else "unchanged"


def converge(conn: psycopg.Connection, login: str, group: str, phrase: str) -> None:
    role = sql.Identifier(login)
    if conn.execute("select from pg_roles where rolname = %s", (login,)).fetchone() is None:
        conn.execute(sql.SQL("create role {} login inherit").format(role))
    conn.execute(
        sql.SQL("alter role {} login inherit password {}").format(role, sql.Literal(phrase))
    )
    for (other,) in conn.execute(MEMBER_OF, (login,)).fetchall():
        if other != group:
            conn.execute(sql.SQL("revoke {} from {}").format(sql.Identifier(other), role))
    conn.execute(sql.SQL("grant {} to {}").format(sql.Identifier(group), role))


def logs_in(dsn: str, login: str, phrase: str) -> bool:
    """Whether `login` reaches the database of the superuser `dsn` with the password `phrase`."""
    parts = urlsplit(dsn)
    host = parts.netloc.rsplit("@", 1)[-1]
    try:
        psycopg.connect(
            urlunsplit(parts._replace(netloc=f"{login}:{phrase}@{host}")), connect_timeout=5
        ).close()
    except psycopg.OperationalError:
        return False
    return True


def main(mode: str, dsn: str) -> int:
    apply = mode == "apply"
    known = secrets_of_logins()
    lines = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        for login, group in LOGINS.values():
            current = state(conn, login, group)
            if current == "unchanged" and not logs_in(dsn, login, known[login]):
                current = "differs: password"
            if apply and current != "unchanged":
                converge(conn, login, group, known[login])
                current = "created" if current == "missing" else "updated"
            lines.append((f"login: {login} in {group}", current))
    for item, current in lines:
        print(f"{item} {current}")
    wrong = [item for item, current in lines if current.startswith(("missing", "differs"))]
    if apply:
        print(f"changed: {sum(current in ('created', 'updated') for _, current in lines)}")
    for item in wrong:
        print(f"differs: {item}", file=sys.stderr)
    return 1 if wrong else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "render":
        print(json.dumps(render(sys.argv[2])))
        sys.exit(0)
    if len(sys.argv) != 3 or sys.argv[1] not in ("apply", "check"):
        print("usage: logins.py render STORE_CREDENTIALS | apply DSN | check DSN", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
