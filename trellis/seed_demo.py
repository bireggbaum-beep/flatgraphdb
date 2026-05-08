"""
Trellis — demo seed.

Populates a fresh Trellis project with a realistic Q&V scenario. Used both
as a manual test bed ("does it actually feel right with real-shaped data?")
and as living documentation of the API.

Domain: qualification of a tablet-press production line with two products,
five equipments, several phases per equipment (FAT/SAT/IQ/OQ, plus one
combined IQ-OQ for a simple equipment), a process validation hanging off
the line PQ, supporting documents, materials, software, training, media,
a milestone, and the usual contract-edge thicket.

Coverage matrix (every case the UI has to handle):
  • all 13 node types from types.example.yaml present
  • Phasen-art variants: FAT, SAT, IQ, OQ, IQ-OQ (combined), PQ, Prozess-
    validierung — discoverable in the inspector
  • Status values: Geplant, Läuft, Abgeschlossen for Phases; Draft / Signed
    / Effective for Documents; one Phase carries a "novel" status (Pausiert)
    that is *not* in types.yaml — proves the Wachstums-Axiom in the data
  • Stubs: one Phase created without its betrifft pflicht-kante to verify
    the stub flow surfaces in the list view and inspector
  • Contract colors:
      green   — many satisfied contracts (most equipment IQ/OQs are done)
      red     — two unsatisfied blocker contracts on the line PQ
                (Granulator OQ "Läuft", TP-01 OQ "Geplant")
      yellow  — one non-blocker contract on the process validation
                (Bediener-Schulung not yet abgeschlossen)
  • Cascade: Prozessvalidierung → PQ Linie A → unsatisfied OQs.
    Prozessvalidierung's *aggregate* readiness should be RED even though
    its own direct contracts are not all blockers.
  • Strukturelle Kanten with anzahl=mindestens_eine fully populated (Linie
    `besteht_aus` 5 equipments) — exercises the multi-target rendering.

Usage:
    python3 -m trellis init /tmp/proj
    python3 -m trellis seed /tmp/proj           # refuses if data exists
    python3 -m trellis seed /tmp/proj --force   # wipes data/ first

Idempotent in spirit: with --force, re-running yields the same dataset.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import TrellisDB, TrellisError
from . import readiness as R


def seed(project_dir: str | Path, *, force: bool = False) -> dict:
    """Build the demo dataset. Returns a stats dict for the CLI to print."""
    project_dir = Path(project_dir).resolve()
    data_dir = project_dir / "data"

    db = TrellisDB(project_dir)
    if any(db.list_nodes(t) for t in db.config.type_names()):
        if not force:
            raise TrellisError(
                f"Project already contains data. Re-run with --force to wipe "
                f"{data_dir} first."
            )
        # wipe and reopen
        if data_dir.exists():
            shutil.rmtree(data_dir)
        db = TrellisDB(project_dir)

    refs: dict[str, str] = {}

    def n(key: str, type_name: str, fields: dict, pflicht=None):
        refs[key] = db.create_node(type_name, fields, pflicht or {})

    # ----------------------------------------------------------- raw assets

    n("prd_ass", "Produkt", {"name": "Tablette ASS 100mg"})
    n("prd_ibu", "Produkt", {"name": "Tablette Ibu 200mg"})

    n("eq_mix",  "Equipment", {"name": "Mischer M-01",          "hersteller": "BohlePharma"})
    n("eq_gran", "Equipment", {"name": "Granulator G-01",       "hersteller": "Glatt"})
    n("eq_tp",   "Equipment", {"name": "Tablettenpresse TP-01", "hersteller": "Korsch"})
    n("eq_vp",   "Equipment", {"name": "Verpackungsmaschine VP-01"})
    n("eq_w",    "Equipment", {"name": "Waage W-01"})  # the simple one (combined IQ-OQ)

    n("sw_tp",       "Software", {"name": "TP-01 Firmware",     "version": "v3.2"})
    n("mat_ass",     "Material", {"name": "ASS Wirkstoff"})
    n("mat_ibu",     "Material", {"name": "Ibu Wirkstoff"})
    n("mat_stearin", "Material", {"name": "Stearinsäure"})
    n("med_air",     "Medium",   {"name": "Druckluft 6 bar"})
    n("med_n2",      "Medium",   {"name": "Stickstoff"})

    n("training_bediener", "Training",
       {"name": "Bediener-Schulung Tablettenpresse"})

    n("doc_urs",  "Dokument", {"name": "URS Tablettenlinie A",        "current_status": "Effective"})
    n("doc_fds",  "Dokument", {"name": "FDS Tablettenpresse TP-01",   "current_status": "Effective"})
    n("doc_iqp",  "Dokument", {"name": "IQ-Protokoll TP-01",          "current_status": "Signed"})
    n("doc_oqp",  "Dokument", {"name": "OQ-Protokoll TP-01",          "current_status": "Draft"})
    n("doc_risk", "Dokument", {"name": "Risk Assessment Pressprozess","current_status": "Signed"})
    n("doc_sop",  "Dokument", {"name": "SOP Pressprozess Tabletten",  "current_status": "Effective"})

    n("taet_kalibrierung", "Taetigkeit",
       {"name": "Wöchentliche Kalibrierung Waage W-01"})

    # ---------------------------------------------------- Linie + Prozess

    n("linie_a", "Linie", {"name": "Tablettenlinie A"},
      pflicht={
          "produziert":  [refs["prd_ass"], refs["prd_ibu"]],
          "besteht_aus": [refs["eq_mix"], refs["eq_gran"], refs["eq_tp"],
                          refs["eq_vp"],  refs["eq_w"]],
      })

    n("prz_pressen", "Prozess", {"name": "Pressprozess Tablette ASS"},
      pflicht={"stellt_her": [refs["prd_ass"]]})

    # ---------------------------------------------------- Phasen per Equipment

    EQUIPMENT_PHASES = [
        # (key prefix, equipment ref key, [(phasen_art, status), ...])
        ("mix",  "eq_mix",  [("FAT", "Abgeschlossen"), ("SAT", "Abgeschlossen"),
                             ("IQ",  "Abgeschlossen"), ("OQ",  "Abgeschlossen")]),
        ("gran", "eq_gran", [("FAT", "Abgeschlossen"), ("SAT", "Abgeschlossen"),
                             ("IQ",  "Abgeschlossen"), ("OQ",  "Läuft")]),       # red later
        ("tp",   "eq_tp",   [("FAT", "Abgeschlossen"), ("SAT", "Abgeschlossen"),
                             ("IQ",  "Abgeschlossen"), ("OQ",  "Geplant")]),     # red later
        ("vp",   "eq_vp",   [("FAT", "Abgeschlossen"), ("SAT", "Abgeschlossen"),
                             ("IQ",  "Abgeschlossen"), ("OQ",  "Abgeschlossen")]),
    ]
    for prefix, eq_key, phases in EQUIPMENT_PHASES:
        eq_name = db.get_node(refs[eq_key])["name"]
        for art, status in phases:
            key = f"ph_{prefix}_{art.lower()}"
            n(key, "Phase",
              {"name": f"{art} {eq_name}", "phasen_art": art, "current_status": status},
              pflicht={"betrifft": [refs[eq_key]]})

    # combined IQ-OQ for the simple equipment — proves phase types are free-form
    n("ph_w_iqoq", "Phase",
      {"name": "IQ-OQ Waage W-01", "phasen_art": "IQ-OQ", "current_status": "Abgeschlossen"},
      pflicht={"betrifft": [refs["eq_w"]]})

    # one phase carrying a status that is NOT in types.yaml (Wachstums-Axiom)
    n("ph_paused", "Phase",
      {"name": "FAT Reservepresse", "phasen_art": "FAT", "current_status": "Pausiert"},
      pflicht={"betrifft": [refs["eq_tp"]]})

    # one stub phase: no betrifft target → list view shows the stub dot
    n("ph_stub", "Phase",
      {"name": "IQ Pumpenmodul (Stub, betrifft fehlt)",
       "phasen_art": "IQ", "current_status": "Geplant"})

    # ---------------------------------------------------- Linie PQ + Meilenstein

    n("ph_pq_linie", "Phase",
      {"name": "PQ Tablettenlinie A", "phasen_art": "PQ", "current_status": "Geplant"},
      pflicht={"betrifft": [refs["linie_a"]]})

    n("mil_spec", "Meilenstein",
      {"name": "Spec Setting: Ready für PQ", "current_status": "Erreicht"},
      pflicht={"gehoert_zu": [refs["ph_pq_linie"]]})

    # ---------------------------------------------------- Prozessvalidierung

    n("ph_pv", "Phase",
      {"name": "Prozessvalidierung Pressprozess ASS",
       "phasen_art": "Prozessvalidierung", "current_status": "Geplant"},
      pflicht={"betrifft": [refs["prz_pressen"]]})

    # ---------------------------------------------------- Verträge

    # PQ Linie A requires every IQ + every OQ + the combined Waage IQ-OQ
    # + the spec milestone. Two of the OQs are not Abgeschlossen — those
    # become RED contracts (blockers, unsatisfied) and drag the PQ aggregate
    # to RED.
    PQ_REQUIREMENTS = [
        ("ph_mix_iq",  "Abgeschlossen", True),
        ("ph_mix_oq",  "Abgeschlossen", True),
        ("ph_gran_iq", "Abgeschlossen", True),
        ("ph_gran_oq", "Abgeschlossen", True),    # Läuft  → red
        ("ph_tp_iq",   "Abgeschlossen", True),
        ("ph_tp_oq",   "Abgeschlossen", True),    # Geplant → red
        ("ph_vp_iq",   "Abgeschlossen", True),
        ("ph_vp_oq",   "Abgeschlossen", True),
        ("ph_w_iqoq",  "Abgeschlossen", True),
        ("mil_spec",   "Erreicht",      True),
    ]
    for key, req_status, blocker in PQ_REQUIREMENTS:
        db.add_contract(refs["ph_pq_linie"], refs[key],
                        required_status=req_status, is_blocker=blocker)

    # Prozessvalidierung requires PQ Linie A + Risk Assessment + SOP +
    # Bediener-Schulung. The Bediener-Schulung carries no current_status,
    # so its contract is unsatisfied — non-blocker → YELLOW.
    PV_REQUIREMENTS = [
        ("ph_pq_linie",        "Abgeschlossen", True),   # PQ is Geplant → red
        ("doc_risk",           "Signed",        True),   # satisfied → green
        ("doc_sop",            "Effective",     True),   # satisfied → green
        ("training_bediener",  "Abgeschlossen", False),  # unsatisfied + non-blocker → yellow
    ]
    for key, req_status, blocker in PV_REQUIREMENTS:
        db.add_contract(refs["ph_pv"], refs[key],
                        required_status=req_status, is_blocker=blocker)

    # ---------------------------------------------------- summary + sanity

    counts = {t: len(db.list_nodes(t)) for t in db.config.type_names()}
    counts = {k: v for k, v in counts.items() if v}

    pq_aggregate  = R.aggregate_readiness(db, refs["ph_pq_linie"])
    pv_aggregate  = R.aggregate_readiness(db, refs["ph_pv"])
    mix_aggregate = R.aggregate_readiness(db, refs["ph_mix_iq"])

    return {
        "counts":         counts,
        "stub_ref":       refs["ph_stub"],
        "pq_ref":         refs["ph_pq_linie"],
        "pq_readiness":   pq_aggregate,
        "pv_ref":         refs["ph_pv"],
        "pv_readiness":   pv_aggregate,
        "leaf_readiness": mix_aggregate,
    }
