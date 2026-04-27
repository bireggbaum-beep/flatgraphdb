"""
HomeDMS — Persönliches Dokumenten-Management auf FlatGraphDB
Run: pip install flask && python dms_app.py
"""

import os
import json
import shutil
import urllib.request as _urllib
from datetime import datetime, timezone
from flask import (Flask, render_template_string, request, redirect,
                   url_for, flash, send_file, Response)
from flatgraph import FlatGraphDB, MaintenanceEngine

app = Flask(__name__)
app.secret_key = "dms-secret-2026"
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

DB_ROOT   = os.path.join(os.path.dirname(__file__), "_dms_db")
VAULT_DIR = os.path.join(DB_ROOT, "vault")
INBOX_DIR = os.path.join(DB_ROOT, "inbox")
DEFAULT_USER = "User"

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "docx", "xlsx", "txt", "xml", "csv"}

DOC_CATEGORIES = [
    "Versicherung", "Steuer", "Bank", "Rechnung", "Behörde",
    "Gesundheit", "Wohnen", "Fahrzeug", "Arbeit", "Sonstiges",
]

DOC_TYPES = [
    "Rechnung", "Vertrag", "Police", "Bescheid", "Quittung",
    "Korrespondenz", "Ausweis", "Zertifikat", "Kontoauszug", "Sonstiges",
]

DOC_STATUS = {
    "AKTIV":      "#2a9d60",
    "ARCHIVIERT": "#888",
    "ABGELAUFEN": "#e74c3c",
    "STORNIERT":  "#aaa",
}
DOC_STATUS_FLOW = {
    "AKTIV":      "ARCHIVIERT",
    "ARCHIVIERT": None,
    "ABGELAUFEN": "ARCHIVIERT",
    "STORNIERT":  None,
}

CURRENCIES = ["CHF", "EUR", "USD", "GBP"]
LANGUAGES  = ["DE", "EN", "FR", "IT"]

REMINDER_TYPES = {
    "ABLAUF":     "Ablaufdatum",
    "KUENDIGUNG": "Kündigung",
    "ZAHLUNG":    "Zahlung",
    "CUSTOM":     "Individuell",
}

ALL_DOC_FIELDS = {
    "title":             "Titel",
    "issuer":            "Aussteller",
    "issuer_ref":        "Aussteller (Kontakt-Ref)",
    "category":          "Kategorie",
    "doc_type":          "Dokumenttyp",
    "doc_date":          "Dokumentdatum",
    "amount":            "Betrag",
    "currency":          "Währung",
    "due_date":          "Fälligkeit",
    "expires_at":        "Ablaufdatum",
    "cancellable_until": "Kündbar bis",
    "language":          "Sprache",
    "asn":               "Archivnummer (physisch)",
    "tags":              "Tags",
    "notes":             "Notizen",
}

DEFAULT_FIELD_PROFILES = {
    "Versicherung": ["issuer", "amount", "currency", "expires_at", "cancellable_until", "asn", "notes"],
    "Steuer":       ["doc_date", "amount", "currency", "asn", "notes"],
    "Bank":         ["issuer", "amount", "currency", "doc_date", "asn"],
    "Rechnung":     ["issuer", "amount", "currency", "doc_date", "due_date", "notes"],
    "Behörde":      ["issuer", "doc_date", "expires_at", "asn", "notes"],
    "Gesundheit":   ["issuer", "doc_date", "amount", "currency", "asn", "notes"],
    "Wohnen":       ["issuer", "doc_date", "amount", "currency", "cancellable_until", "asn", "notes"],
    "Fahrzeug":     ["issuer", "doc_date", "amount", "currency", "expires_at", "asn", "notes"],
    "Arbeit":       ["issuer", "doc_date", "expires_at", "asn", "notes"],
    "Sonstiges":    ["issuer", "doc_date", "amount", "currency", "asn", "notes"],
}

CONTACT_CATEGORIES = [
    "Versicherung", "Bank", "Behörde", "Telecom", "Versorger",
    "Gesundheit", "Arbeit", "Sonstiges",
]

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

_db = None


def get_db():
    global _db
    if _db is None:
        os.makedirs(VAULT_DIR, exist_ok=True)
        os.makedirs(INBOX_DIR, exist_ok=True)
        _db = FlatGraphDB(root_dir=DB_ROOT)
    return _db


@app.before_request
def _begin():
    get_db()._transaction_depth += 1


@app.teardown_request
def _end(exc):
    db = get_db()
    if exc is None:
        db._transaction_depth -= 1
        db._flush_pending_writes()
    else:
        db._transaction_depth -= 1
        db._dirty_nodes.clear()
        db._dirty_edges.clear()


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def now():
    return datetime.now(timezone.utc).isoformat()


def today():
    return datetime.now(timezone.utc).date().isoformat()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def safe_vault_path(filename):
    safe = os.path.basename(filename)
    if not safe or safe != filename:
        raise ValueError("Ungültiger Dateiname.")
    return os.path.join(VAULT_DIR, safe)


def get_field_profile(category):
    db = get_db()
    profiles = db.list_nodes("field_profiles")
    for nid, p in profiles.items():
        if p.get("category") == category:
            return p.get("fields", DEFAULT_FIELD_PROFILES.get(category, []))
    return DEFAULT_FIELD_PROFILES.get(category, list(ALL_DOC_FIELDS.keys()))


def fire_webhooks(event, payload):
    db = get_db()
    if "webhooks" not in db.list_collections():
        return
    for wh in db.list_nodes("webhooks").values():
        if not wh.get("active"):
            continue
        if wh.get("event") not in (event, "all"):
            continue
        try:
            data = json.dumps(payload).encode()
            req  = _urllib.Request(wh["url"], data=data,
                       headers={"Content-Type": "application/json"}, method="POST")
            _urllib.urlopen(req, timeout=3)
        except Exception:
            pass


def inbox_files():
    if not os.path.isdir(INBOX_DIR):
        return []
    return sorted(f for f in os.listdir(INBOX_DIR)
                  if os.path.isfile(os.path.join(INBOX_DIR, f))
                  and not f.startswith("."))


if __name__ == "__main__":
    app.run(debug=True, port=5002)
