"""End-to-end smoke test for trellis core.

Runs against a fresh temp directory; no fixtures, no pytest. Exits non-zero
on any failed assertion so it can be wired into CI later.

Usage:
    python3 -m trellis.smoke_test
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from trellis import TrellisDB, TrellisError


HERE = Path(__file__).resolve().parent
EXAMPLE = HERE / "types.example.yaml"


def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  ok  {label}")


def assert_true(cond: bool, label: str) -> None:
    if not cond:
        raise AssertionError(f"{label}: expected truthy, got falsy")
    print(f"  ok  {label}")


def assert_raises(exc_type, fn, label: str) -> None:
    try:
        fn()
    except exc_type as e:
        print(f"  ok  {label}  ({type(e).__name__}: {str(e)[:80]})")
        return
    raise AssertionError(f"{label}: expected {exc_type.__name__}, no exception raised")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="trellis_smoke_"))
    try:
        shutil.copy(EXAMPLE, tmp / "types.yaml")
        db = TrellisDB(tmp)
        print(f"[opened project at {tmp}]")

        # --- basic creation ----------------------------------------------------
        print("\n# basic creation")
        eq_ref = db.create_node("Equipment", {"name": "Pumpe X-2025",
                                              "hersteller": "Acme"})
        assert_eq(eq_ref, "Equipment/EQ-0001", "Equipment ref")
        eq2 = db.create_node("Equipment", {"name": "Pumpe Y-2025"})
        assert_eq(eq2, "Equipment/EQ-0002", "second Equipment auto-id")

        prd = db.create_node("Produkt", {"name": "Tablette 10mg"})
        assert_eq(prd, "Produkt/PRD-0001", "Produkt ref")

        # --- pflicht-kanten satisfied at creation ------------------------------
        print("\n# pflicht-kanten on creation (Linie needs produziert + besteht_aus)")
        lin = db.create_node(
            "Linie", {"name": "Linie A"},
            pflicht_kanten_targets={
                "produziert":  [prd],
                "besteht_aus": [eq_ref, eq2],
            },
        )
        assert_eq(lin, "Linie/LIN-0001", "Linie ref")
        assert_true(not db.is_stub(lin), "Linie is not a stub when fully wired")

        # --- pflicht-kanten missing → stub -------------------------------------
        print("\n# stub mode (Linie without besteht_aus)")
        lin_stub = db.create_node(
            "Linie", {"name": "Linie B (incomplete)"},
            pflicht_kanten_targets={"produziert": [prd]},
        )
        assert_true(db.is_stub(lin_stub), "Linie B is a stub (no besteht_aus)")
        stubs = db.list_stubs("Linie")
        assert_true(lin_stub in stubs, "list_stubs reports Linie B")
        # complete it
        db.add_structural_edge(lin_stub, eq_ref, "besteht_aus")
        assert_true(not db.is_stub(lin_stub), "Linie B no longer a stub after wiring")

        # --- phase with bezugsobjekt + status ---------------------------------
        print("\n# phase with status and bezugsobjekt")
        pq = db.create_node(
            "Phase",
            {"name": "PQ Linie A", "phasen_art": "PQ", "current_status": "Geplant"},
            pflicht_kanten_targets={"betrifft": [lin]},
        )
        assert_eq(pq, "Phase/PHA-0001", "PQ ref")

        iq = db.create_node(
            "Phase",
            {"name": "IQ Pumpe X-2025", "phasen_art": "IQ", "current_status": "Geplant"},
            pflicht_kanten_targets={"betrifft": [eq_ref]},
        )
        assert_eq(iq, "Phase/PHA-0002", "IQ ref")

        # --- contract edge -----------------------------------------------------
        print("\n# contract: PQ requires IQ in 'Abgeschlossen'")
        eid = db.add_contract(pq, iq, required_status="Abgeschlossen", is_blocker=True)
        contracts = db.list_contracts(pq, direction="out")
        assert_eq(len(contracts), 1, "one outgoing contract on PQ")
        c = contracts[0]
        assert_eq(c["target"], iq, "contract target")
        assert_eq(c["required_status"], "Abgeschlossen", "contract required_status")
        assert_eq(c["is_blocker"], True, "contract is_blocker")

        # incoming contracts on IQ
        in_c = db.list_contracts(iq, direction="in")
        assert_eq(len(in_c), 1, "one incoming contract on IQ")

        # --- error paths -------------------------------------------------------
        print("\n# error paths (Robustheits-Axiom)")
        assert_raises(
            TrellisError,
            lambda: db.create_node("DoesNotExist", {"name": "X"}),
            "unknown type rejected",
        )
        assert_raises(
            TrellisError,
            lambda: db.create_node("Equipment", {"hersteller": "Acme"}),
            "missing required field rejected",
        )
        # Dokument is not a permitted target type for Phase.betrifft — must reject
        # even before checking that the ref does not exist.
        assert_raises(
            TrellisError,
            lambda: db.create_node(
                "Phase", {"name": "Bad", "current_status": "Geplant"},
                pflicht_kanten_targets={"betrifft": ["Dokument/DOC-9999"]},
            ),
            "wrong-type pflicht-kante target rejected",
        )
        assert_raises(
            TrellisError,
            lambda: db.add_contract(pq, iq, "Abgeschlossen", "yes"),
            "non-bool is_blocker rejected",
        )
        assert_raises(
            TrellisError,
            lambda: db.add_structural_edge(pq, iq, "requires"),
            "structural API refuses vertrag edge type",
        )

        # --- wachstums-axiom: free-form statuses go through --------------------
        print("\n# wachstums-axiom (statuses are suggestions, not gates)")
        novel = db.create_node(
            "Phase",
            {"name": "DR Linie A", "phasen_art": "DR", "current_status": "Pausiert"},
            pflicht_kanten_targets={"betrifft": [lin]},
        )
        assert_eq(db.get_node(novel)["current_status"], "Pausiert",
                  "novel status accepted on create")
        ok = db.update_node(novel, {"current_status": "Eingefroren"})
        assert_true(ok, "update_node accepts a brand-new status string")
        novel_eid = db.add_contract(pq, novel, required_status="Beliebig", is_blocker=False)
        assert_true(bool(novel_eid), "add_contract accepts a brand-new required_status")
        # cleanup so the rest of the test sees a stable count
        db.remove_contract(novel_eid)

        # --- search / @-mention -----------------------------------------------
        print("\n# search (for @-mention)")
        hits = db.search("pumpe", type_name="Equipment")
        assert_eq(len(hits), 2, "two equipment hits for 'pumpe'")
        hits = db.search("X-2025")
        assert_true(any(h["ref"] == eq_ref for h in hits), "X-2025 finds Pumpe X")

        # --- inspector --------------------------------------------------------
        print("\n# inspector")
        ins = db.inspector(pq)
        assert_eq(ins["ref"], pq, "inspector ref")
        assert_eq(ins["type"], "Phase", "inspector type")
        assert_true("betrifft" in ins["structural_out"], "betrifft edge listed in structural_out")
        assert_eq(len(ins["contracts_out"]), 1, "one contract_out on PQ")

        # --- update + status change -------------------------------------------
        print("\n# update node")
        ok = db.update_node(iq, {"current_status": "Abgeschlossen"})
        assert_true(ok, "update_node returns True")
        n = db.get_node(iq)
        assert_eq(n["current_status"], "Abgeschlossen", "status persisted")

        print("\nALL SMOKE TESTS PASSED")
        return 0

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
