"""C54 tillägg - waiting_status får plats i kolumnen (Postgres håller längden).

Prod: `already_handled_in_bezala` (25 tecken) i varchar(24) -> StringDataRightTruncation.
SQLite bryr sig inte om längden, så testerna här kör mot en riktig Postgres
när binärerna finns (annars hoppas Postgres-testet över), plus ett statiskt
test som alltid körs: varje statusvärde i koden ryms i modellens kolumn.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PG_BIN = next((p for p in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True)
               if (p / "initdb").exists()), None) if Path("/usr/lib/postgresql").exists() else None


def _status_literals() -> set[str]:
    """Alla strängar koden skriver till/jämför med waiting_status, plus
    statuskonstanter (VERSALER = "snake_case")."""
    out: set[str] = set()
    pat_cmp = re.compile(r"waiting_status\s*(?:==|!=|=)\s*\"([a-z_]+)\"")
    pat_const = re.compile(r"^[A-Z_]+\s*=\s*\"([a-z][a-z_]+)\"", re.M)
    for f in (ROOT / "app").rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        out |= set(pat_cmp.findall(text))
        if "waiting_status" in text:
            out |= set(pat_const.findall(text))
    return out


def test_every_waiting_status_fits_the_column():
    from app.models import ProcessedMessage

    length = ProcessedMessage.__table__.c.waiting_status.type.length
    values = _status_literals()
    assert {"already_handled_in_bezala", "probably_handled_elsewhere",
            "waiting_for_transaction", "ignored_private_source"} <= values
    too_long = sorted(v for v in values if len(v) > length)
    assert not too_long, f"waiting_status values longer than varchar({length}): {too_long}"


# ---------------------------------------------------------------- riktig Postgres


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _as_pg(cmd: list[str], **kw):
    if os.geteuid() == 0:
        cmd = ["runuser", "-u", "postgres", "--", *cmd]
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


@pytest.fixture(scope="module")
def pg_url():
    if PG_BIN is None or (os.geteuid() == 0 and not shutil.which("runuser")):
        pytest.skip("no PostgreSQL binaries")
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        pytest.skip("psycopg2 missing")
    tmp = tempfile.mkdtemp(prefix="c54pg-")
    os.chmod(tmp, 0o777)
    data = os.path.join(tmp, "data")
    port = _free_port()
    try:
        _as_pg([str(PG_BIN / "initdb"), "-D", data, "-U", "postgres", "-A", "trust"])
        _as_pg([str(PG_BIN / "pg_ctl"), "-D", data, "-w", "-l", os.path.join(tmp, "log"),
                "-o", f"-p {port} -k {tmp} -c listen_addresses=127.0.0.1", "start"])
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        pytest.skip(f"could not start PostgreSQL: {exc}")
    try:
        yield f"postgresql+psycopg2://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run((["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else [])
                       + [str(PG_BIN / "pg_ctl"), "-D", data, "-m", "immediate", "stop"],
                       capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def test_postgres_old_varchar24_fails_and_migration_widens(pg_url):
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.exc import DataError

    from app.db import widen_varchar_columns

    eng = create_engine(pg_url)
    with eng.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS processed_messages"))
        c.execute(text("CREATE TABLE processed_messages (id serial primary key, "
                       "message_id varchar(255), waiting_status varchar(24))"))
        c.execute(text("INSERT INTO processed_messages (message_id, waiting_status) "
                       "VALUES ('a', 'waiting_for_transaction'), ('b', NULL)"))
    # Felet från prod återskapas med den gamla kolumnen.
    with pytest.raises(DataError):
        with eng.begin() as c:
            c.execute(text("UPDATE processed_messages SET "
                           "waiting_status='already_handled_in_bezala' WHERE message_id='b'"))
    assert widen_varchar_columns(eng) == ["processed_messages.waiting_status"]
    assert widen_varchar_columns(eng) == []  # idempotent
    col = {c["name"]: c for c in inspect(eng).get_columns("processed_messages")}
    assert col["waiting_status"]["type"].length == 64
    with eng.begin() as c:
        c.execute(text("UPDATE processed_messages SET "
                       "waiting_status='already_handled_in_bezala' WHERE message_id='b'"))
        rows = dict(c.execute(text("SELECT message_id, waiting_status FROM processed_messages")).all())
    assert rows == {"a": "waiting_for_transaction", "b": "already_handled_in_bezala"}
    eng.dispose()


def test_postgres_model_schema_takes_every_status(pg_url):
    """Samma kolumntyp som prod (create_all från modellen): alla statusvärden sparas."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import models  # noqa: F401
    from app.db import Base
    from app.models import ProcessedMessage

    eng = create_engine(pg_url)
    with eng.begin() as c:
        c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    for i, v in enumerate(sorted(_status_literals())):
        s.add(ProcessedMessage(message_id=f"m{i}", status="saved", waiting_status=v))
    s.commit()
    assert s.query(ProcessedMessage).filter(
        ProcessedMessage.waiting_status == "already_handled_in_bezala").count() == 1
    s.close()
    eng.dispose()
