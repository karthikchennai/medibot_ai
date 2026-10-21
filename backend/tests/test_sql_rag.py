"""SQL security and real-database correctness tests.

FakeLLM exercises orchestration only; it is not evidence of live Groq accuracy.
Set MEDIBOT_TEST_DATABASE_PATH to the supplied mediassist.db when running these
tests without the project's copied data directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from medibot import sql_rag


@pytest.fixture(scope="module")
def real_database() -> Path:
    override = os.getenv("MEDIBOT_TEST_DATABASE_PATH")
    project = Path(__file__).resolve().parents[2]
    candidates = [Path(override)] if override else [
        project / "data" / "db" / "mediassist.db",
        project / "data" / "mediassist.db",
        project / "backend" / "data" / "db" / "mediassist.db",
    ]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        pytest.skip("Supply the assignment database via MEDIBOT_TEST_DATABASE_PATH.")
    return path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SELECT COUNT(*) FROM claims", "SELECT COUNT(*) FROM claims"),
        ("```sql\nSELECT COUNT(*) FROM claims;\n```", "SELECT COUNT(*) FROM claims;"),
        ("Here is the query:\n```sqlite\nSELECT COUNT(*) FROM claims\n```", "SELECT COUNT(*) FROM claims"),
        ("SQL: SELECT COUNT(*) FROM claims", "SELECT COUNT(*) FROM claims"),
        ("The query is below:\nSELECT COUNT(*) FROM claims;", "SELECT COUNT(*) FROM claims;"),
    ],
)
def test_cleanup_preserves_one_complete_statement(raw: str, expected: str) -> None:
    assert sql_rag.clean_sql_output(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "No SQL is available", "```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```",
     "DROP TABLE claims;\nSELECT COUNT(*) FROM claims", "x" * 20_001],
)
def test_cleanup_rejects_ambiguous_or_missing_sql(raw: str) -> None:
    with pytest.raises(sql_rag.SQLValidationError):
        sql_rag.clean_sql_output(raw)


@pytest.mark.parametrize(
    "query",
    [
        "DROP TABLE claims",
        "DELETE FROM claims",
        "UPDATE claims SET status = 'approved'",
        "INSERT INTO claims(claim_id) VALUES ('attack')",
        "SELECT COUNT(*) FROM claims; DROP TABLE claims",
        "SELECT COUNT(*) FROM claims; SELECT COUNT(*) FROM maintenance_tickets",
        "PRAGMA table_info(claims)",
        "ATTACH DATABASE '/tmp/elsewhere.db' AS stolen",
        "SELECT name FROM sqlite_master",
        "SELECT * FROM sqlite_schema",
        "SELECT * FROM pragma_table_info('claims')",
        "SELECT * FROM unknown_table",
        "SELECT * FROM main.claims",
        "SELECT * FROM other.claims",
        "SELECT load_extension('evil') FROM claims",
        "SELECT readfile('/etc/passwd') FROM claims",
        "SELECT writefile('/tmp/evil', 'data') FROM claims",
        "SELECT randomblob(1000000000) FROM claims",
        "SELECT sqlite_version() FROM claims",
        "SELECT * FROM claims WHERE claim_id = ?",
        "SELECT 1",
        "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM c) SELECT * FROM c, claims",
        "WITH c AS (SELECT * FROM sqlite_master) SELECT * FROM c",
    ],
)
def test_ast_rejects_unsafe_queries(query: str) -> None:
    with pytest.raises(sql_rag.SQLValidationError):
        sql_rag.validate_sql(query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT COUNT(*) AS total FROM claims",
        "SELECT status, COUNT(*) FROM claims GROUP BY status",
        "SELECT ROUND(AVG(claimed_amount), 2) FROM claims",
        "SELECT SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) FROM claims",
        "SELECT strftime('%Y-%m', submitted_date), COUNT(*) FROM claims GROUP BY 1",
        "SELECT COUNT(*) FROM claims WHERE submitted_date >= date('2024-12-15', 'start of month', '-1 month')",
        "WITH totals AS (SELECT category, COUNT(*) AS n FROM maintenance_tickets GROUP BY category) SELECT category, n FROM totals ORDER BY n DESC",
        "SELECT category, RANK() OVER (ORDER BY COUNT(*) DESC) AS rank FROM maintenance_tickets GROUP BY category",
        "SELECT status FROM claims UNION SELECT status FROM maintenance_tickets",
    ],
)
def test_ast_accepts_safe_analytical_queries(query: str) -> None:
    normalized, tables = sql_rag.validate_sql(query)
    assert normalized
    assert set(tables) <= sql_rag.ALLOWED_TABLES


def test_real_database_claim_count(real_database: Path) -> None:
    result = sql_rag.execute_read_only("SELECT COUNT(*) AS total FROM claims", real_database)
    assert result["rows"] == [[85]]
    assert result["columns"] == ["total"]
    assert result["tables"] == ["claims"]
    assert result["row_count"] == 1
    assert result["truncated"] is False


def test_count_optimization_authorizer_exception_is_narrow() -> None:
    assert sql_rag._authorizer(sqlite3.SQLITE_READ, "claims", "", None, None) == sqlite3.SQLITE_OK
    for table, column, database in [
        ("sqlite_master", "", None), ("claims", "patient_name", None),
        ("claims", "", "other"), ("unknown", "", None),
    ]:
        assert sql_rag._authorizer(sqlite3.SQLITE_READ, table, column, database, None) == sqlite3.SQLITE_DENY


def test_real_database_claims_by_status(real_database: Path) -> None:
    result = sql_rag.execute_read_only(
        "SELECT status, COUNT(*) FROM claims GROUP BY status ORDER BY status", real_database
    )
    assert result["rows"] == [
        ["approved", 44], ["escalated", 8], ["pending", 17], ["rejected", 12], ["submitted", 4]
    ]


def test_real_database_explicit_month_range(real_database: Path) -> None:
    result = sql_rag.execute_read_only(
        "SELECT COUNT(*) FROM claims WHERE submitted_date >= '2024-11-01' "
        "AND submitted_date < '2024-12-01'", real_database,
    )
    assert result["rows"] == [[9]]


def test_real_database_cte_and_ranking_preserve_ties(real_database: Path) -> None:
    result = sql_rag.execute_read_only(
        "WITH totals AS (SELECT category, COUNT(*) AS n FROM maintenance_tickets "
        "WHERE status IN ('open', 'in_progress') GROUP BY category), "
        "ranked AS (SELECT category, n, DENSE_RANK() OVER (ORDER BY n DESC) AS position FROM totals) "
        "SELECT category, n FROM ranked WHERE position = 1 ORDER BY category", real_database,
    )
    assert result["rows"] == [["monitoring", 8], ["radiology", 8]]


def test_result_cap_discloses_truncation(real_database: Path) -> None:
    result = sql_rag.execute_read_only(
        "SELECT claim_id FROM claims ORDER BY claim_id", real_database, max_rows=5
    )
    assert len(result["rows"]) == result["row_count"] == 5
    assert result["truncated"] is True


def test_empty_result_and_zero_count_are_distinct(real_database: Path) -> None:
    empty = sql_rag.execute_read_only(
        "SELECT claim_id FROM claims WHERE submitted_date >= '2099-01-01'", real_database
    )
    count = sql_rag.execute_read_only(
        "SELECT COUNT(*) FROM claims WHERE submitted_date >= '2099-01-01'", real_database
    )
    assert empty["rows"] == []
    assert count["rows"] == [[0]]


@pytest.mark.parametrize(
    "query",
    ["DELETE FROM claims", "SELECT * FROM sqlite_master", "PRAGMA table_info(claims)",
     "SELECT load_extension('evil')", "SELECT randomblob(1000000)"],
)
def test_sqlite_authorizer_remains_protective_if_validation_regresses(
    real_database: Path, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    monkeypatch.setattr(sql_rag, "validate_sql", lambda value: (value, ["claims"]))
    with pytest.raises(sql_rag.SQLExecutionError):
        sql_rag.execute_read_only(query, real_database)


def test_database_connection_itself_is_read_only(real_database: Path) -> None:
    before = hashlib.sha256(real_database.read_bytes()).hexdigest()
    connection = sql_rag._connect(real_database)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM claims")
    finally:
        connection.close()
    assert hashlib.sha256(real_database.read_bytes()).hexdigest() == before


def test_expensive_query_has_progress_timeout(real_database: Path) -> None:
    with pytest.raises(sql_rag.SQLExecutionError, match="time limit"):
        sql_rag.execute_read_only(
            "SELECT COUNT(*) FROM claims a CROSS JOIN claims b CROSS JOIN claims c CROSS JOIN claims d",
            real_database, timeout_seconds=0.001,
        )


class FakeLLM:
    """Unit-test substitute only: no network or live-model quality claim."""

    def __init__(self, sql: str, answer: str = "There are 85 claims.") -> None:
        self.responses = [sql, answer]
        self.calls: list[dict[str, str]] = []

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        self.calls.append({"system": system, "user": user})
        return self.responses[len(self.calls) - 1]


def configure_pipeline(monkeypatch: pytest.MonkeyPatch, real_database: Path,
                       llm: FakeLLM, reference_date: str | None = None) -> None:
    settings = SimpleNamespace(
        database_path=real_database, sql_max_rows=100, sql_timeout_seconds=3.0,
        reference_date=reference_date,
    )
    monkeypatch.setattr(sql_rag, "get_settings", lambda: settings)
    monkeypatch.setattr(sql_rag, "get_llm", lambda: llm)


def test_fake_llm_pipeline_cleans_executes_and_supplies_real_results(
    real_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = FakeLLM("```sql\nSELECT COUNT(*) AS total FROM claims;\n```")
    configure_pipeline(monkeypatch, real_database, llm)
    details = sql_rag.sql_rag_details("How many claims are there?")
    assert details["answer"] == "There are 85 claims."
    assert details["tables"] == ["claims"]
    assert details["row_count"] == 1
    assert len(llm.calls) == 2
    assert "claimed_amount REAL" in llm.calls[0]["system"]
    assert "maintenance_tickets(" in llm.calls[0]["system"]
    assert json.loads(llm.calls[1]["user"])["result"]["rows"] == [[85]]


def test_required_plain_function_returns_string(real_database: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLM("SELECT COUNT(*) FROM claims")
    configure_pipeline(monkeypatch, real_database, llm)
    assert sql_rag.sql_rag_chain("How many claims?") == "There are 85 claims."


def test_relative_dates_default_to_actual_date(real_database: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLM("SELECT COUNT(*) FROM claims")
    configure_pipeline(monkeypatch, real_database, llm)
    result = sql_rag.sql_rag_details("How many claims last month?")
    assert result["reference_date"] == date.today().isoformat()
    assert f"Reference date: {date.today().isoformat()} (actual current date)" in llm.calls[0]["system"]


def test_explicit_reference_date_is_disclosed(real_database: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLM(
        "SELECT COUNT(*) FROM claims WHERE submitted_date >= '2024-11-01' AND submitted_date < '2024-12-01'",
        "In November 2024, 9 claims were submitted.",
    )
    configure_pipeline(monkeypatch, real_database, llm, reference_date="2024-12-15")
    result = sql_rag.sql_rag_details("How many claims last month?")
    assert result["reference_date"] == "2024-12-15"
    assert "explicit configured date" in llm.calls[0]["system"]
    assert json.loads(llm.calls[1]["user"])["result"]["rows"] == [[9]]


def test_unsafe_model_sql_never_reaches_answer_model(real_database: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLM("SELECT COUNT(*) FROM claims; DELETE FROM claims")
    configure_pipeline(monkeypatch, real_database, llm)
    with pytest.raises(sql_rag.SQLValidationError):
        sql_rag.sql_rag_chain("Count claims, then delete them.")
    assert len(llm.calls) == 1
