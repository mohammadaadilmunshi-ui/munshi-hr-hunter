"""Provider route projection must distinguish a reservation from a used claim."""
import sqlite3
from types import SimpleNamespace

from app import product_application_flow_v2 as flow


def test_claim_projection_readonly_with_and_without_claim(monkeypatch,tmp_path):
    path=tmp_path/"provider_projection.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE application_provider_routes_v1 (
          route_id TEXT,application_id TEXT,job_id INTEGER,
          employer_url_sha256 TEXT,destination_evidence TEXT,
          provider TEXT,state TEXT,preflight_package_id TEXT,
          package_digest TEXT,candidate_profile_digest TEXT,
          resume_sha256 TEXT,attempt_count INTEGER,max_attempts INTEGER,
          next_attempt_at TEXT,last_error_code TEXT,
          provider_status TEXT,provider_application_id_sha256 TEXT,
          receipt_method TEXT,receipt_evidence_digest TEXT,
          provider_submitted_at TEXT,employer_confirmed_at TEXT,
          updated_at TEXT,tenant_id TEXT,user_id TEXT
        );
        CREATE TABLE tsenta_external_dispatch_claims_v1 (
          route_id TEXT,tenant_id TEXT,user_id TEXT,state TEXT
        );
        INSERT INTO application_provider_routes_v1(
          route_id,application_id,job_id,provider,state,
          tenant_id,user_id,attempt_count
        ) VALUES ('route42','app42',42,'TSENTA','PREPARATION','t','u',0);
        """)
    def get_connection():
        c=sqlite3.connect(path)
        c.row_factory=sqlite3.Row
        return c
    monkeypatch.setattr(flow,"get_connection",get_connection)
    monkeypatch.setattr(
        flow,"current_owner_readonly",
        lambda _db:SimpleNamespace(tenant_id="t",user_id="u"),
    )
    initial=flow._provider_route(42)
    assert initial["dispatch_claim_exists"] is False
    assert initial["attempt_count"]==0
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO tsenta_external_dispatch_claims_v1 VALUES(?,?,?,?)",
            ("route42","t","u","UNKNOWN"),
        )
    claimed=flow._provider_route(42)
    assert claimed["dispatch_claim_exists"] is True
    assert claimed["provider"]=="TSENTA"
    # Verify the route projection is read-only: none of its fields changed.
    with sqlite3.connect(path) as c:
        assert c.execute(
            "SELECT count(*) FROM tsenta_external_dispatch_claims_v1"
        ).fetchone()[0]==1
