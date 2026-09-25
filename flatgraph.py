"""
=============================================================================
FlatGraphDB — Lightweight, file-based Graph Database for Python
=============================================================================

WAS ZUGESAGT WIRD UND WAS NICHT: siehe VERTRAG.md

Diese Fassung kommt aus bireggbaum-beep/homedms (flatgraph/entwurf/,
Issue #36, "flatgraph 4.0"). Herkunft, Abweichungen und ältere Belege
liegen dort in flatgraph/HERKUNFT.md bzw. flatgraph/basislinie/ — hier
absichtlich nicht dupliziert, um nicht zwei Quellen für dieselbe Frage
zu haben.

WAS DIESE BIBLIOTHEK NICHT GARANTIERT
-------------------------------------
THREADSICHER ab 3.0.0-entwurf: jede oeffentliche Methode nimmt eine
Sperre der Instanz, eine Transaktion haelt sie ueber ihren ganzen Block.
Bis 3.0.0 gab es keine, und zwei Threads zerstoerten den Bestand lautlos —
pDMS hat das am 09.09.2026 mit Datenverlust bezahlt. NICHT geschuetzt ist,
was `readonly=True` herausgibt: das sind Verweise in den Speicher, und wer
sie spaeter liest, liest ohne Sperre.
Ein Bestand hat EINE offene Instanz: beim Oeffnen wird er gesperrt, und
jede weitere — aus einem anderen Prozess oder aus diesem — bekommt
`BestandBelegt` statt still mitzuschreiben. Freigabe mit close() oder
`with FlatGraphDB(...) as db:` (siehe VERTRAG.md 3.2).

Dieser Absatz steht bewusst IM CODE und nicht nur in der Dokumentation:
wer die Datei öffnet, um sie zu benutzen, soll ihn sehen müssen.
=============================================================================
A single-module embedded graph database. No external dependencies.

Architecture:
  root_dir/
    datenbank/
      nodes/      -> one JSON file per collection
      edges/      -> one JSON file per edge type
    vault/        -> binary assets (PDFs, images, etc.)
    vault_archive/-> quarantine for deleted assets

Features:
  - Graph model: nodes reference each other only via edges (no direct FK)
  - Soft-delete with a two-phase, idempotent Garbage Collector
  - Cascade-delete on edge: target node dies with source node
  - Atomic writes via temp+rename
  - Optional schema validation per collection
  - RAM cache for fast reads; lazy field index for fast find_nodes()
  - Breadth-first multi-hop traversal
  - Auto-increment ID helper
  - Optional transaction context manager (batch writes + rollback)
  - Optional audit trail (audit=True)
  - Optional change callback (bei_aenderung=...)
=============================================================================
"""

import contextlib
import copy
import functools
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import types
import urllib.parse
import uuid
import warnings
import weakref
from datetime import datetime, timezone

# Fassung IM CODE, nicht nur im readme. Dort steht seit dem 23.04.2026
# unveraendert "2.1" — ueber 350 Zeilen Entwicklung hinweg, vom Stand
# 3d6f67b bis aa6a993. Eine Nummer, die nichts unterscheidet, ist keine.
# Deshalb hier mit dem Stand als Baumetadaten (semver "+"):
#   3.0.0          ist die uebernommene Fassung in flatgraph/flatgraph.py
#   4.0.0-entwurf  ist dieser Entwurf (Neueinstufung 24.09.2026, Issue #36:
#                  Speicherform 3 und die geaenderten Verhaltensweisen sind
#                  eine neue Hauptversion) — ab hier wird die naechste daraus
class FlatGraphFehler(Exception):
    """Oberklasse aller flatgraph-Fehler. Wer alles fangen will, fängt die."""


class KnotenFehlt(FlatGraphFehler, KeyError):
    """Der angesprochene Knoten gibt es nicht (oder er ist weich geloescht).

    Erbt ZUSAETZLICH von KeyError, damit bestehender Aufrufcode mit
    `except KeyError` weiter funktioniert. Das ist der Grund, warum diese
    Fassung neue Fehlertypen einfuehren kann, ohne etwas zu brechen.
    """


class KnotenExistiert(FlatGraphFehler, KeyError):
    """Unter dieser Kennung gibt es schon einen Knoten."""


class KanteFehlt(FlatGraphFehler, KeyError):
    """Die angesprochene Kante gibt es nicht."""


class UngueltigeReferenz(FlatGraphFehler, ValueError):
    """Eine Knotenreferenz ist nicht `sammlung/kennung`.

    Ein Programmierfehler, kein Normalfall — und deshalb etwas anderes als
    "den Knoten gibt es nicht".
    """


class UngueltigerName(FlatGraphFehler, ValueError):
    """Ein Sammlungsname, eine Kantenart oder eine Kennung, die sich nicht
    sicher auf einen Dateinamen abbilden laesst.

    Sammlungen werden Verzeichnisse, Kantenarten Dateien, Kennungen
    Dateinamen. Vorher lief jeder Name ungeprueft in `os.path.join`:
    `create_node("../../x", ...)` legte ein Verzeichnis AUSSERHALB von
    `datenbank/` an, und eine zu lange Kennung scheiterte erst beim
    Schreiben — nachdem der Knoten schon im Speicher stand.
    """


class NichtSpeicherbar(FlatGraphFehler, TypeError):
    """Ein Wert, der als JSON nicht unveraendert zurueckkaeme.

    Geprueft wird, BEVOR der Speicher geaendert wird. Vorher stand ein
    Knoten mit einem `date` darin schon im Speicher, als das Schreiben
    scheiterte: er war da und doch nicht, liess sich nicht neu anlegen und
    fehlte nach dem Neustart. Eine Kante mit einem solchen Wert machte ihre
    ganze Kantenart unschreibbar, bis zum Neustart.

    Erbt zusaetzlich von TypeError, weil `json` vorher genau den warf.
    """


class BestandBelegt(FlatGraphFehler, RuntimeError):
    """Dieser Bestand ist schon geoeffnet — von einem anderen Prozess oder
    von einer anderen Instanz in diesem.

    Zwei Prozesse halten je einen eigenen Stand im Speicher und
    ueberschreiben einander — lautlos. Bis 3.0.0 hiess der Schutz davor
    `file_lock=True`, und er schuetzte nur den einzelnen Schreibvorgang,
    nicht das Lesen davor und nicht den Speicherstand.
    """

    def __init__(self, pfad, im_selben_prozess=False):
        self.pfad, self.im_selben_prozess = pfad, im_selben_prozess
        if im_selben_prozess:
            text = (f"Der Bestand unter '{pfad}' ist in diesem Prozess schon "
                    f"geoeffnet. Eine Instanz je Bestand: die vorhandene "
                    f"weiterbenutzen oder vorher mit close() schliessen.")
        else:
            text = (f"Der Bestand unter '{pfad}' ist von einem anderen Prozess "
                    f"geoeffnet. Ein Bestand gehoert einem Prozess; den anderen "
                    f"beenden oder dort weiterarbeiten.")
        super().__init__(text)


class BestandGeschlossen(FlatGraphFehler, RuntimeError):
    """Nach `close()` wird nicht mehr geschrieben.

    Ohne diese Pruefung schriebe eine geschlossene Instanz ohne Sperre
    weiter — genau das, was die Sperre verhindern soll.
    """


class NichtInTransaktion(FlatGraphFehler, RuntimeError):
    """Diese Operation laesst sich nicht zuruecknehmen und laeuft deshalb
    nicht in einer Transaktion.

    Der Muellsammler verschiebt Anhaenge und schreibt seine Dateien sofort.
    Ein Rollback koennte das nie rueckgaengig machen; in einer Transaktion
    saehe es nur so aus.
    """


class DateiKaputt(FlatGraphFehler, RuntimeError):
    """Eine Datei des Bestands ist nicht lesbar oder kein gueltiges JSON."""


class AbschlussHaengt(FlatGraphFehler, OSError):
    """Die Transaktion GILT, steht aber noch nicht in allen Faechern.

    Die Absichtsdatei war schon dauerhaft geschrieben, als das Umbenennen
    der Faecher scheiterte. Zuruecknehmen waere falsch: nach einem Neustart
    fuehrt `_absicht_nachholen` sie ohnehin zu Ende. Der Speicher zeigt
    deshalb den neuen Stand; der naechste Schreibvorgang oder das naechste
    Oeffnen holt den Rest nach.
    """


class SpeicherformZuNeu(FlatGraphFehler):
    """Der Bestand ist in einer neueren Speicherform geschrieben.

    Muss ein eigener Typ sein, kein nackter RuntimeError: der Aufrufer soll
    "diese Fassung ist zu alt" von "die Datei ist kaputt" unterscheiden
    koennen, ohne in Fehlertexten zu suchen. Das eine ist ein Update, das
    andere eine Reparatur.
    """

    def __init__(self, gefunden, unterstuetzt, pfad):
        self.gefunden, self.unterstuetzt, self.pfad = gefunden, unterstuetzt, pfad
        super().__init__(
            f"Der Bestand unter '{pfad}' ist in Speicherform {gefunden} "
            f"geschrieben; diese flatgraph-Fassung versteht hoechstens "
            f"{unterstuetzt}. Eine neuere Fassung verwenden — die Daten sind "
            f"in Ordnung, nur zu neu fuer diesen Code."
        )


__version__ = "4.0.0-entwurf"
__grundlage__ = "2.2.0, Uebernahme vom 19.09.2026"

# Fassung der SPEICHERFORM, getrennt von der der Bibliothek. Sie aendert
# sich nur, wenn sich das Format auf der Platte aendert.
#
#   1  eine Sammeldatei je Collection (nodes/<collection>.json) plus eine
#      Deltadatei (<collection>_temp.json), die bei JEDEM Schreibvorgang
#      vollstaendig neu geschrieben wurde und dabei waechst.
#   2  eine Datei je Knoten (nodes/<collection>/<id>.json). Ein Schreib-
#      vorgang beruehrt genau eine Datei, unabhaengig von der Groesse des
#      Bestands. Damit entfaellt das Delta-Konstrukt ersatzlos.
#   3  Faecher: bis zu FACH_GROESSE Knoten je Datei
#      (nodes/<collection>/fach_000001.json), Kanten ebenso je Art
#      (edges/<art>/fach_000001.json). Aufgefuellt, nicht gestreut: ein
#      neuer Knoten kommt ins letzte Fach, bis es voll ist, dann beginnt
#      das naechste. Gemessen bei 100 000 Knoten / 400 000 Kanten (Issue
#      #32): Oeffnen nach Neustart 9.2 s -> 0.98 s, eine Kante schreiben
#      1418 ms -> 0.9 ms, Plattenplatz 475 -> 140 MB, 100 001 -> 8 000
#      Dateien; einen Knoten schreiben unveraendert unter 1 ms.
SPEICHERFORM = 3

# Knoten bzw. Kanten je Fach. 25 ist im Versuch das Optimum gewesen: darunter
# bleiben es zu viele Dateien (6 je Fach: 33 000 Dateien, 2.7 s kalt), darueber
# wird jeder Schreibvorgang teurer, ohne dass das Oeffnen noch viel gewinnt
# (400 je Fach: 4 ms je Knoten, 0.51 s kalt). Auf einen 4-KB-Block
# auszurichten lohnt nicht — Verschnitt entsteht nur im letzten Block.
FACH_GROESSE = 25

_log = logging.getLogger("flatgraph")

_RESERVED_EDGE_FIELDS  = {"source", "target", "type", "created_at", "_cascade_delete"}
_INTERNAL_COLLECTIONS  = {"_audit_log"}

# Die Knotenfelder, die flatgraph SELBST schreibt. Nur sie sind von der
# Schemapruefung ausgenommen. Vorher galt die Ausnahme fuer jedes Feld mit
# fuehrendem Unterstrich, was zwei Dinge kaputt machte — beide am 17.09.
# nachgemessen: ein Schemafeld `_intern: str` liess `{"_intern": 42}`
# durch, UND es machte jede gewoehnliche Aenderung unmoeglich, weil das
# Feld vor der Pruefung herausfiel und danach als "required field missing"
# fehlte. Der Unterstrich gehoert flatgraph, nicht dem Aufrufer.
# _geloescht_durch: mit welchem Knoten dieser in den Papierkorb kam
# (Kaskade). Nur so holt restore_node genau diese zurueck und nicht auch
# solche, die unabhaengig davon geloescht waren.
_INTERNE_KNOTENFELDER  = {"_deletion_flag", "_keep_asset", "_geloescht_durch"}

# Im Undo-Log: „vor der Transaktion gab es das nicht“. Ein eigenes Objekt,
# weil None ein moeglicher Wert waere.
_FEHLTE = object()

# Sammlungen und Kantenarten werden Verzeichnis- bzw. Dateinamen. Erlaubt
# ist deshalb nur, was auf jedem Dateisystem ein harmloser Name ist:
# Buchstaben (auch Umlaute), Ziffern, `_`, `-`, `.` — am Anfang ein
# Buchstabe oder eine Ziffer. Damit sind `..`, Schraegstriche und
# Laufwerksangaben ausgeschlossen, und der fuehrende Unterstrich bleibt
# flatgraph (`_audit_log`). Vorher wurde bei Kantenarten nur `/` und `\`
# durch `_` ersetzt — "teil/von" und "teil_von" landeten in DERSELBEN
# Datei.
_NAMENSREGEL = re.compile(r"[^\W_][\w.-]{0,99}")


# =============================================================================
# FAECHER (Speicherform 3)
# =============================================================================

def _ordnername(name):
    """Sammlung bzw. Kantenart als Verzeichnisname — umkehrbar mit unquote.

    Gueltige Namen (Namensregel) bleiben unveraendert und lesbar, auch mit
    Umlauten. Nur was aus einer aelteren Fassung stammen kann und im
    Dateisystem etwas anderes bedeutet, wird kodiert: `/`, `\\`, `%`
    und ein fuehrender Punkt. Vorher landeten "teil/von" und "teil_von" in
    derselben Datei.
    """
    kodiert = name.replace("%", "%25").replace("/", "%2F").replace("\\", "%5C")
    if kodiert.startswith("."):
        kodiert = "%2E" + kodiert[1:]
    return kodiert


class _Ablage:
    """Welche Kennung in welchem Fach liegt — fuer EINE Sammlung bzw. Kantenart.

    Lebt nur im Arbeitsspeicher und entsteht beim Oeffnen, weil dabei ohnehin
    jedes Fach gelesen wird. Deshalb braucht es keinen Hash und keine feste
    Fachzahl: ein Fach ist ein Ordner auf einem Stapel. Neue Eintraege kommen
    ins letzte Fach, bis es voll ist; dann beginnt das naechste. Es gibt nie
    ein leeres Fach, das auf Inhalt wartet.

    Luecken entstehen nur, wenn der Muellsammler endgueltig loescht — und er
    ist es auch, der sie wieder schliesst (FlatGraphDB._verdichten).
    """

    def __init__(self, verzeichnis):
        self.verzeichnis = verzeichnis
        self.fach_von = {}      # kennung -> fachnummer
        self.inhalt = {}        # fachnummer -> {kennungen}

    def pfad(self, nr):
        return os.path.join(self.verzeichnis, f"fach_{nr:06d}.json")

    def eintragen(self, kennung, nr):
        self.fach_von[kennung] = nr
        self.inhalt.setdefault(nr, set()).add(kennung)

    def zuordnen(self, kennung):
        nr = self.fach_von.get(kennung)
        if nr is not None:
            return nr
        letztes = max(self.inhalt) if self.inhalt else 0
        if letztes == 0 or len(self.inhalt[letztes]) >= FACH_GROESSE:
            letztes += 1
        self.eintragen(kennung, letztes)
        return letztes

    def entfernen(self, kennung):
        nr = self.fach_von.pop(kennung, None)
        if nr is not None:
            self.inhalt[nr].discard(kennung)
        return nr


# =============================================================================
# PROZESSSPERRE
# =============================================================================
# Je Bestand EINE offene Instanz — ueber Prozessgrenzen UND im selben
# Prozess. Entschieden am 24.09.2026.
#
# Zwei Instanzen halten je einen eigenen Stand im Speicher; was die eine
# schreibt, sieht die andere nie. Bis 3.0.0 flickte das Kantenschreiben das
# halb, indem es vor jedem Schreiben die Kantendatei nachlas — die Knoten
# blieben trotzdem auseinander. Nachgewiesen: App-Instanz plus eine zweite
# fuer den Muellsammler, danach schrieb die App eine Kante, und nach dem
# Neustart zeigte eine Kante auf einen laengst geloeschten Knoten. Statt
# das Nachlesen zu behalten, gibt es die zweite Instanz nicht mehr: der
# Muellsammler ist eine Methode jeder Instanz.
#
# `flock` sperrt je geoeffneter Datei; zwischen Prozessen reicht es. Im
# selben Prozess wuerde ein zweites `flock` auf einer neu geoeffneten Datei
# unter Linux ebenfalls scheitern, unter anderen Systemen nicht verlaesslich
# — deshalb fuehrt dieses Modul selbst Buch, welche Bestaende es offen hat.
#
# Freigegeben wird mit `close()` oder wenn die Instanz weggeraeumt wird.
# Stirbt der Prozess, hebt das Betriebssystem die Sperre auf — sie
# ueberlebt ihren Prozess nie, anders als eine Sperrdatei, deren blosse
# Existenz zaehlt.

_SPERREN = {}                      # echter Pfad -> Dateideskriptor
_SPERREN_SCHUTZ = threading.Lock()


def _sperre_nehmen(root_dir):
    schluessel = os.path.realpath(root_dir)
    with _SPERREN_SCHUTZ:
        if schluessel in _SPERREN:
            raise BestandBelegt(root_dir, im_selben_prozess=True)
        fd = os.open(os.path.join(root_dir, ".flatgraph.lock"),
                     os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            raise BestandBelegt(root_dir) from e
        _SPERREN[schluessel] = fd
        return schluessel


def _sperre_freigeben(schluessel):
    with _SPERREN_SCHUTZ:
        fd = _SPERREN.pop(schluessel, None)
        if fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)


# =============================================================================
# MAIN ENGINE
# =============================================================================

class FlatGraphDB:
    def __init__(self, root_dir, schemas=None, edge_constraints=None,
                 file_lock=False, audit=False, webhooks=None, bei_aenderung=None,
                 longtext_threshold=None):
        """
        Initialize the graph engine.

        :param root_dir:   Root directory of the database (created if it does not exist).
        :param schemas:    Optional dict {collection_name: {field: spec}} for type-checked
                           collections. Three spec forms are supported:
                             str / int / float / bool / list
                               Plain Python type — field is required, value must be that type.
                             {"type": str|list, "options": [...]}
                               Enum — field is required; for str the value must be one of the
                               options; for list every element must be in options.
                             {"type": "link", "target": "collection_name"}
                               Node reference — field is optional; when present the value must
                               be a valid, non-deleted node ref in the target collection.
        :param file_lock:  Ohne Wirkung, nur noch angenommen, damit alter Aufrufcode
                           startet. Jeder Bestand wird beim Oeffnen fuer den
                           Prozess gesperrt (BestandBelegt).
        :param audit:      Automatically write audit entries to _audit_log on every write.
        :param bei_aenderung: Rueckruf, der nach jeder Aenderung mit einem Dict aufgerufen
                           wird: {"ereignis", "ref", "sammlung"/"kantenart", "zeit"}. In einer
                           Transaktion erst nach dem erfolgreichen Abschluss, bei einem
                           Rollback gar nicht. Ein Fehler darin wird protokolliert
                           (logging "flatgraph") und macht die Aenderung NICHT rueckgaengig.
        :param webhooks:   Entfernt in 4.0 — wirft TypeError. Stattdessen bei_aenderung.
        :param edge_constraints: Optional dict {rel_type: [(source_collection, target_collection), ...]}
                                 restricting which collection pairs are valid for each edge type.
                                 Example: {"gehört-zu-plant": [("equipment", "plant"),
                                                               ("software",   "plant")]}
        :param longtext_threshold: If set (int), string fields longer than this many characters
                                   are automatically offloaded to vault_text/ as plain-text files.
                                   The node field stores an "@vault_text/..." reference instead.
                                   Use get_node_full() to resolve references back to full text.
        """
        # Die Sperre der Instanz, VOR allem anderen. Wiedereintrittsfaehig,
        # weil oeffentliche Methoden einander aufrufen (soft_delete ruft
        # update_node, traverse ruft get_connected) und weil ein Thread in
        # seiner eigenen Transaktion weiter schreiben koennen muss.
        self._sperre = threading.RLock()
        if file_lock:
            warnings.warn(
                "file_lock hat keine Wirkung mehr: jeder Bestand wird beim "
                "Oeffnen fuer den Prozess gesperrt.", DeprecationWarning, stacklevel=2)
        self.root = root_dir
        self.schemas = schemas or {}
        self.edge_constraints = edge_constraints or {}
        self.audit = audit
        if webhooks:
            # Laut statt still: ein angenommener, aber wirkungsloser Parameter
            # liesse Benachrichtigungen verloren gehen, ohne dass es jemand merkt.
            raise TypeError(
                "webhooks gibt es ab flatgraph 4.0 nicht mehr: flatgraph "
                "verschickt kein HTTP. Stattdessen bei_aenderung=<funktion> "
                "uebergeben und dort selbst versenden.")
        self.bei_aenderung = bei_aenderung
        self._meldungen = []         # in einer Transaktion gepufferte Meldungen
        self.longtext_threshold = longtext_threshold
        self._audit_writing = False  # prevents recursive audit entries
        self._geschlossen = False

        # Die Sperre ZUERST — vor dem Umzug einer alten Speicherform, der
        # schreibt, und vor dem Einlesen, das sonst einen Stand laedt, den
        # ein anderer Prozess gerade aendert.
        os.makedirs(root_dir, exist_ok=True)
        schluessel = _sperre_nehmen(root_dir)
        # Kein Verweis auf self im Finalizer, sonst hielte er die Instanz am
        # Leben und die Sperre wuerde nie frei.
        self._freigeben = weakref.finalize(self, _sperre_freigeben, schluessel)
        try:
            self._oeffnen()
        except BaseException:
            # Scheitert das Oeffnen (DateiKaputt, SpeicherformZuNeu), gibt es
            # keine Instanz, die man schliessen koennte — die Sperre muss
            # hier frei werden, nicht irgendwann bei der Muellabfuhr.
            self._freigeben()
            raise

    def _oeffnen(self):
        root_dir = self.root

        self.dirs = {
            "nodes":        os.path.join(root_dir, "datenbank", "nodes"),
            "edges":        os.path.join(root_dir, "datenbank", "edges"),
            "vault":        os.path.join(root_dir, "vault"),
            "vault_archive":os.path.join(root_dir, "vault_archive"),
            "vault_text":   os.path.join(root_dir, "vault_text"),
        }
        for path in self.dirs.values():
            os.makedirs(path, exist_ok=True)

        # RAM cache: edges = {rel_type: {edge_id: edge_data}}
        self._cache = {"nodes": {}, "edges": {}}
        self._edge_type_index = {}   # {edge_id: rel_type} — reverse index, RAM only
        # Nachbarschaftsindizes: {knoten_ref: {kantenart: {kanten_id: kante}}}
        # Ohne sie ist jede Nachbarschaftsfrage ein Durchgang durch ALLE
        # Kanten — bei einer Graphdatenbank ausgerechnet die zentrale
        # Operation. Gemessen vorher: 0.5 ms je get_connected bei 8000
        # Kanten, und das waechst linear weiter. Nur im Arbeitsspeicher,
        # nie auf der Platte: sie sind ABGELEITET und werden beim Oeffnen
        # aus den Kanten gebaut.
        self._out_index = {}
        self._in_index = {}
        self._index_cache = {}       # {collection: {field: {wert_klein: {kennungen}}}} — nur RAM
        self._dirty_nodes  = {}      # {collection: set(node_ids)} — pending temp-file writes
        self._dirty_edges  = {}      # {kantenart: {kanten_ids}} — in einer Transaktion gepuffert
        # Kennung -> Fach, je Sammlung bzw. Kantenart; entsteht beim Oeffnen.
        self._ablagen = {"nodes": {}, "edges": {}}
        self._transaction_depth = 0  # >0 = active transaction, writes are buffered
        # Undo-Log der laufenden Transaktion, sonst None. Schluessel:
        #   ("knoten", sammlung, id)  -> Knoten vor der ersten Aenderung (Kopie) oder _FEHLTE
        #   ("kante", art, id)        -> Kante vor der ersten Aenderung oder _FEHLTE
        #   ("sammlung", sammlung)    -> die Sammlung gab es vorher nicht
        #   ("kantenart", art)        -> die Kantenart gab es vorher nicht
        self._undo = None
        self._purged_nodes   = {}    # {collection: set(node_ids)} — vom Muellsammler endgueltig geloescht
        # Vor dem Einlesen, nicht danach: ein Bestand in neuerer Form soll
        # gar nicht erst halb geladen werden.
        self._speicherform_pruefen()
        # Vor dem Lesen: eine abgebrochene Transaktion, deren Absicht schon
        # feststand, gilt — der Bestand wird erst fertig gemacht, dann gelesen.
        self._absicht_haengt = False
        self._plan = None
        self._absicht_nachholen()
        self._initialize_cache()

    def close(self):
        """Die Instanz schliessen und ihren Anteil an der Prozesssperre abgeben.

        Offene Schreibvorgaenge ausserhalb einer Transaktion stehen schon auf
        der Platte; `close()` schreibt nichts nach. Danach wirft jeder
        Schreibvorgang `BestandGeschlossen`. Mehrfaches Schliessen schadet
        nicht.
        """
        self._geschlossen = True
        self._freigeben()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _offen_pruefen(self):
        if self._geschlossen:
            raise BestandGeschlossen(
                f"Die Instanz fuer '{self.root}' ist geschlossen; es wird "
                f"nicht mehr geschrieben.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _edges_file(self, rel_type):
        safe = rel_type.replace("/", "_").replace("\\", "_")
        return os.path.join(self.dirs["edges"], f"{safe}.json")

    def _persist_kanten(self, rel_type, edge_ids):
        """Geaenderte Kanten auf die Platte — oder puffern in einer Transaktion."""
        if self._transaction_depth > 0:
            self._dirty_edges.setdefault(rel_type, set()).update(edge_ids)
            return
        self._flush_kanten(rel_type, edge_ids)

    def _flush_kanten(self, rel_type, edge_ids):
        """Die Faecher schreiben, in denen diese Kanten liegen bzw. lagen.

        Bis Speicherform 2 lagen alle Kanten einer Art in EINER Datei, und
        jede neue Kante schrieb sie ganz: bei 400 000 Kanten 1.4 s fuer eine
        Kante. Jetzt schreibt sie nur ihr Fach (0.9 ms). Bis 3.0.0 wurde
        vorher ausserdem die ganze Datei gelesen und der Nachbarschaftsindex
        der Art neu gebaut, um Aenderungen anderer Prozesse mitzunehmen —
        seit der Prozesssperre gibt es keine.
        """
        self._ablage_schreiben("edges", rel_type, edge_ids,
                               self._cache["edges"].get(rel_type, {}))

    # --- Nachbarschaftsindizes -----------------------------------------
    # Diese drei sind die einzigen Stellen, die _out_index und _in_index
    # veraendern. Wer woanders an den Kanten dreht, ohne hier vorbeizukommen,
    # erzeugt einen Index, der etwas anderes behauptet als der Bestand — und
    # das faellt erst auf, wenn eine Ansicht Falsches zeigt.

    def _index_edge(self, edge_id, edge_data):
        rel_type = edge_data.get("type", "_unknown")
        self._out_index.setdefault(edge_data["source"], {}) \
                       .setdefault(rel_type, {})[edge_id] = edge_data
        self._in_index.setdefault(edge_data["target"], {}) \
                      .setdefault(rel_type, {})[edge_id] = edge_data

    def _unindex_edge(self, edge_id, edge_data):
        rel_type = edge_data.get("type", "_unknown")
        for index, ref in ((self._out_index, edge_data["source"]),
                           (self._in_index, edge_data["target"])):
            eimer = index.get(ref, {}).get(rel_type)
            if eimer is not None:
                eimer.pop(edge_id, None)
                # Leere Eimer wegraeumen, sonst waechst der Index mit jedem
                # jemals dagewesenen Knoten weiter, auch wenn er laengst
                # keine Kanten mehr hat.
                if not eimer:
                    del index[ref][rel_type]
                    if not index[ref]:
                        del index[ref]

    def _rebuild_edge_indexes(self, rel_type=None):
        """Aus den Kanten neu bauen. Ohne Argument alles, sonst eine Art."""
        if rel_type is None:
            self._out_index, self._in_index = {}, {}
            eimer = self._cache["edges"].items()
        else:
            for index in (self._out_index, self._in_index):
                for ref in list(index):
                    index[ref].pop(rel_type, None)
                    if not index[ref]:
                        del index[ref]
            eimer = [(rel_type, self._cache["edges"].get(rel_type, {}))]
        for art, bucket in eimer:
            for edge_id, edge in bucket.items():
                self._index_edge(edge_id, edge)

    @staticmethod
    def _translate_legacy_edge(edge):
        """Translate v0.9 German field names to English in-place. Returns the edge."""
        if "quelle" in edge:
            edge["source"]     = edge.pop("quelle")
            edge["target"]     = edge.pop("ziel",  edge.get("target", ""))
            edge["type"]       = edge.pop("typ",   edge.get("type",   "_unknown"))
            edge["created_at"] = edge.pop("erstellt_am", edge.get("created_at", ""))
        return edge

    def _migrate_legacy_edges(self):
        """Migrate old single-file objektlinks.json to per-type files."""
        legacy = os.path.join(self.dirs["edges"], "objektlinks.json")
        if not os.path.exists(legacy):
            return
        all_edges = self._load_json_from_disk(legacy)
        by_type = {}
        for edge_id, edge_data in all_edges.items():
            self._translate_legacy_edge(edge_data)
            t = edge_data.get("type", "_unknown")
            by_type.setdefault(t, {})[edge_id] = edge_data
        for t, edges in by_type.items():
            self._save_json_atomic(self._edges_file(t), edges)
        os.remove(legacy)

    def _meta_datei(self):
        return os.path.join(self.root, "datenbank", "_meta.json")

    def _speicherform_pruefen(self):
        """Verweigern, statt kommentarlos einen leeren Bestand zu laden.

        Ohne diese Pruefung las eine aeltere Fassung einen Bestand in einer
        neueren Speicherform als LEER ein — die Dateien hiessen anders, also
        fand sie nichts, und meldete auch nichts. Genau das ist am
        16.09.2026 beim Umbau auf "eine Datei je Knoten" aufgefallen und war
        der Grund, ihn zurueckzunehmen.

        Fehlt die Marke, ist der Bestand aelter als diese Pruefung. Das ist
        kein Fehler: er wird als aktuelle Form gelesen und die Marke
        nachgetragen.
        """
        pfad = self._meta_datei()
        gefunden = None
        if os.path.exists(pfad):
            daten = self._load_json_from_disk(pfad)
            gefunden = daten.get("speicherform")
            if isinstance(gefunden, int) and gefunden > SPEICHERFORM:
                raise SpeicherformZuNeu(gefunden, SPEICHERFORM, self.root)
            if gefunden == SPEICHERFORM:
                self._umzugsreste_wegraeumen()
                return
        # Marke fehlt oder nennt eine aeltere Form: umziehen, Stufe fuer
        # Stufe. Fehlt sie, ist der Bestand aelter als die Pruefung und liegt
        # damit in Form 1.
        if gefunden in (None, 1):
            self._migriere_auf_eine_datei_je_knoten()
        self._migriere_auf_faecher()
        self._marke_schreiben()
        self._umzugsreste_wegraeumen()

    def _marke_schreiben(self):
        self._save_json_atomic(self._meta_datei(), {
            "speicherform": SPEICHERFORM,
            "langtext_schwelle": self.longtext_threshold,
            "geschrieben_von": __version__,
            "geaendert": datetime.now(timezone.utc).isoformat(),
        })

    def _langtexte_nachziehen(self):
        """Langtexte auslagern, die noch im Knoten stehen.

        Noetig, weil die Auslagerung sonst nur NEUE und GEAENDERTE Knoten
        erwischt. Ein Bestand, der schon in der aktuellen Speicherform
        liegt, behielte seinen Volltext fuer immer im Knoten — und der
        Start bliebe genauso teuer wie ohne Auslagerung. Genau das ist am
        20.09.2026 im Betrieb aufgefallen: der Umzug war durch, das
        Verzeichnis daneben blieb leer.

        Die Schwelle steht deshalb in der Formatmarke. Weicht sie von der
        eingestellten ab — oder fehlt sie, weil der Bestand aelter ist —,
        laeuft dieser Durchgang einmal. Danach nicht wieder. Wer die
        Schwelle SENKT, loest ihn damit absichtlich erneut aus.
        """
        if not self.longtext_threshold:
            return
        marke = self._load_json_from_disk(self._meta_datei())
        if marke.get("langtext_schwelle") == self.longtext_threshold:
            return
        for collection, knoten in self._cache["nodes"].items():
            geaendert = []
            for node_id, daten in list(knoten.items()):
                vorher = dict(daten)
                self._offload_longtexts(collection, node_id, daten)
                if daten != vorher:
                    geaendert.append(node_id)
            if geaendert:
                self._knoten_schreiben(collection, geaendert)
        # Die Marke ZULETZT, aus demselben Grund wie beim Umzug: bricht der
        # Durchgang ab, faengt der naechste Start von vorn an.
        self._marke_schreiben()

    def _migriere_auf_eine_datei_je_knoten(self):
        """Form 1 -> 2: Sammeldatei plus Delta werden zu einer Datei je Knoten.

        **Die Reihenfolge ist der ganze Punkt.** Erst werden alle neuen
        Dateien geschrieben, dann die alten entfernt, und die Marke setzt
        der Aufrufer ZULETZT. Bricht der Vorgang irgendwo ab, steht die
        Marke noch nicht — der naechste Start faengt von vorn an und findet
        die Sammeldatei unveraendert vor. Umgekehrt waere ein halb
        umgezogener Bestand mit gesetzter Marke nicht mehr zu retten.

        Das ist die Lehre vom 16.09.2026, an der dieser Umbau schon einmal
        gescheitert ist: damals zog eine Fassung den Bestand um, und eine
        aeltere las ihn danach kommentarlos als LEER. Die Marke verhindert
        das zweite; diese Reihenfolge das erste.
        """
        wurzel = self.dirs["nodes"]
        if not os.path.isdir(wurzel):
            return
        sammeldateien = sorted(
            d for d in os.listdir(wurzel)
            if d.endswith(".json") and not d.endswith("_temp.json"))
        for dateiname in sammeldateien:
            collection = dateiname[:-5]
            basis = self._load_json_from_disk(self._sammeldatei(collection))
            delta = self._load_json_from_disk(self._temp_file(collection))
            zusammen = {**basis, **delta}
            verzeichnis = self._knoten_verzeichnis(collection)
            os.makedirs(verzeichnis, exist_ok=True)
            for node_id, knoten in zusammen.items():
                # Langtexte AUCH hier auslagern. Ohne das kaeme ein
                # umgezogener Bestand zwar in Form 2 an, behielte aber den
                # gesamten Volltext in den Knoten — und der Start bliebe
                # genauso teuer wie vorher. Der Umzug ist die einzige
                # Gelegenheit, an der ein ALTER Bestand das nachholt: danach
                # wird ein Knoten erst wieder angefasst, wenn ihn jemand
                # aendert.
                self._offload_longtexts(collection, node_id, knoten)
                self._save_json_atomic(
                    self._knoten_datei(collection, node_id), knoten)
            # Erst jetzt das Alte weg. Vorher waeren die Daten kurzzeitig
            # nirgends vollstaendig.
            for alt in (self._sammeldatei(collection), self._temp_file(collection)):
                if os.path.exists(alt):
                    os.remove(alt)

    def _umzugsorte(self):
        db = os.path.join(self.root, "datenbank")
        return {
            "alt_nodes": os.path.join(db, "nodes_form2"),
            "alt_edges": os.path.join(db, "edges_form2"),
            "neu_nodes": os.path.join(db, "nodes_form3"),
            "neu_edges": os.path.join(db, "edges_form3"),
        }

    def _umzugsreste_wegraeumen(self):
        """Die Form-2-Verzeichnisse, die der Umzug beiseitegelegt hat.

        Erst NACH der Marke — vorher sind sie der Rueckfall, falls der Umzug
        abbricht. Bricht es hier ab, raeumt der naechste Start auf.
        """
        orte = self._umzugsorte()
        for schluessel in ("alt_nodes", "alt_edges"):
            shutil.rmtree(orte[schluessel], ignore_errors=True)

    def _migriere_auf_faecher(self):
        """Form 2 -> 3: aus einer Datei je Knoten bzw. je Kantenart werden Faecher.

        Wieder gilt: die Reihenfolge ist der ganze Punkt, und die Marke setzt
        der Aufrufer ZULETZT. Jeder Schritt ist so gebaut, dass ein Abbruch
        an jeder Stelle beim naechsten Start zu Ende gefuehrt wird:

          1. Das Neue vollstaendig in nodes_form3/ und edges_form3/ aufbauen.
             Das Alte bleibt unberuehrt; ein Abbruch hier baut beim naechsten
             Mal von vorn.
          2. Das Alte mit `os.rename` beiseitelegen (nodes -> nodes_form2,
             edges -> edges_form2). Umbenennen ist atomar.
          3. Das Neue an seinen Platz umbenennen.
          4. (Aufrufer) Marke schreiben, dann das Beiseitegelegte loeschen.

        Beim Oeffnen legt `_oeffnen` leere nodes/ und edges/ an, bevor der
        Umzug laeuft — ein nach Schritt 2 abgebrochener Umzug findet dort
        deshalb ein LEERES Verzeichnis vor, das Schritt 3 ersetzen darf.
        """
        orte = self._umzugsorte()
        nodes, edges = self.dirs["nodes"], self.dirs["edges"]

        if not os.path.exists(orte["alt_nodes"]):
            for ort in (orte["neu_nodes"], orte["neu_edges"]):
                shutil.rmtree(ort, ignore_errors=True)
                os.makedirs(ort)
            self._migrate_legacy_edges()
            for eintrag in sorted(os.listdir(nodes)):
                verzeichnis = os.path.join(nodes, eintrag)
                if not os.path.isdir(verzeichnis):
                    continue
                knoten = {
                    urllib.parse.unquote(d[:-5]): self._load_json_from_disk(
                        os.path.join(verzeichnis, d))
                    for d in os.listdir(verzeichnis) if d.endswith(".json")}
                self._ablage_befuellen(
                    os.path.join(orte["neu_nodes"], _ordnername(eintrag)), knoten)
            for dateiname in sorted(os.listdir(edges)):
                if not dateiname.endswith(".json"):
                    continue
                kanten = self._load_json_from_disk(os.path.join(edges, dateiname))
                for kante in kanten.values():
                    self._translate_legacy_edge(kante)
                self._ablage_befuellen(
                    os.path.join(orte["neu_edges"], _ordnername(dateiname[:-5])), kanten)
            os.rename(nodes, orte["alt_nodes"])

        if (not os.path.exists(orte["alt_edges"]) and os.path.exists(edges)
                and os.path.exists(orte["neu_edges"])):
            os.rename(edges, orte["alt_edges"])

        for neu, ziel in ((orte["neu_nodes"], nodes), (orte["neu_edges"], edges)):
            if not os.path.exists(neu):
                continue
            # Unter Linux ersetzt rename ein LEERES Zielverzeichnis von selbst,
            # unter Windows nicht. Die Zeile ist ein Schutz fuer Windows und
            # deshalb in der Suite unter Linux nicht pruefbar.
            if os.path.isdir(ziel) and not os.listdir(ziel):
                os.rmdir(ziel)
            os.rename(neu, ziel)
        for pfad in (nodes, edges):
            os.makedirs(pfad, exist_ok=True)

    def _ablage_befuellen(self, verzeichnis, daten):
        """Eintraege der Reihe nach in Faecher fuellen und schreiben (Umzug)."""
        ablage = _Ablage(verzeichnis)
        for k in sorted(daten):
            ablage.zuordnen(k)
        # Direkt, ohne Absichtsdatei: der Umzug baut in einem eigenen
        # Verzeichnis und ist durch seine Reihenfolge abbruchfest.
        for pfad, inhalt in self._faecher_planen(ablage, set(ablage.inhalt), daten):
            self._save_json_atomic(pfad, inhalt)

    def _initialize_cache(self):
        """Jedes Fach einmal lesen; nebenbei entsteht die Zuordnung Kennung -> Fach."""
        self._ablagen = {"nodes": {}, "edges": {}}
        zu_reparieren = []
        for art in ("nodes", "edges"):
            wurzel = self.dirs[art]
            if not os.path.isdir(wurzel):
                continue
            for eintrag in sorted(os.listdir(wurzel)):
                verzeichnis = os.path.join(wurzel, eintrag)
                if not os.path.isdir(verzeichnis):
                    continue
                name = urllib.parse.unquote(eintrag)
                ablage = _Ablage(verzeichnis)
                self._ablagen[art][name] = ablage
                daten, reparieren = self._ablage_laden(ablage)
                if art == "nodes":
                    self._cache["nodes"][name] = daten
                else:
                    # Keine Uebersetzung alter Feldnamen mehr hier: die gibt
                    # es nur in Bestaenden bis Form 2, und der Umzug auf
                    # Form 3 uebersetzt sie einmal. Bis dahin lief sie bei
                    # jedem Oeffnen fuer jede Kante.
                    for edge_id in daten:
                        self._edge_type_index[edge_id] = name
                    self._cache["edges"][name] = daten
                if reparieren:
                    zu_reparieren.append((ablage, reparieren, daten))
        self._rebuild_edge_indexes()
        # Erst jetzt, wo alles gelesen ist: Reste eines abgebrochenen
        # Verdichtens wegschreiben, bevor irgendwer etwas aendert.
        for ablage, reparieren, daten in zu_reparieren:
            self._faecher_schreiben(ablage, reparieren, daten)
        self._langtexte_nachziehen()

    def _load_json_from_disk(self, filepath):
        """Load a JSON file from disk. Missing file → empty dict. Corrupted file → RuntimeError."""
        if not os.path.exists(filepath):
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise DateiKaputt(f"File '{filepath}' is not valid JSON: {e}") from e
        except IOError as e:
            raise DateiKaputt(f"Could not read file '{filepath}': {e}") from e

    def _save_json_atomic(self, filepath, data):
        """Atomic write: write to .tmp first, fsync, then os.replace().

        Ohne fsync ist der Austausch zwar in der Reihenfolge atomar, die
        Daten stehen aber womöglich noch im Schreibpuffer des Systems.
        Faellt in diesem Moment der Strom aus, zeigt der neue Name auf eine
        leere oder halbe Datei — der Austausch war dann atomar, der Inhalt
        trotzdem weg.

        Die Reihenfolge ist der Punkt: erst die Daten dauerhaft machen,
        dann den Namen tauschen. Andersherum zeigt der neue Name auf einen
        Puffer. Festgenagelt in tests/test_flatgraph_schreibweg.py.
        """
        # Verzeichnis sicherstellen. Klingt nach Gürtel und Hosentraeger,
        # ist aber eine Vertragszusage: der Feldindex ist ABGELEITET und
        # darf jederzeit geloescht werden. Ohne diese Zeile stuerzt der
        # naechste Indexschreibvorgang danach mit FileNotFoundError ab —
        # gefunden von tests/test_flatgraph_graph.py, bevor es jemand im
        # Betrieb gefunden hat.
        self._offen_pruefen()
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        temp_file = filepath + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            # allow_nan=False: `NaN` ist kein JSON. Python liest es zurueck,
            # jedes andere Werkzeug nicht — und der Bestand soll ohne diese
            # Bibliothek lesbar bleiben.
            json.dump(data, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, filepath)
        # Auch der Verzeichniseintrag selbst muss dauerhaft sein.
        try:
            dir_fd = os.open(os.path.dirname(filepath) or ".", os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (OSError, AttributeError):
            pass

    def _temp_file(self, collection_name):
        """Nur noch fuer die Migration von Form 1: dort lag hier das Delta."""
        return os.path.join(self.dirs["nodes"], f"{collection_name}_temp.json")

    def _sammeldatei(self, collection_name):
        """Nur noch fuer die Migration von Form 1: die alte Sammeldatei."""
        return os.path.join(self.dirs["nodes"], f"{collection_name}.json")

    def _knoten_verzeichnis(self, collection_name):
        return os.path.join(self.dirs["nodes"], collection_name)

    def _knoten_datei(self, collection_name, node_id):
        """Ein Knoten, eine Datei.

        Der Dateiname ist die prozentkodierte Id. `quote` und nicht ein
        Ersetzen durch "_": das waere nicht umkehrbar, und zwei Ids, die
        sich nur in einem Sonderzeichen unterscheiden, lagen danach in
        derselben Datei. Uebliche Ids (`d_000001`, `c_steuer`) enthalten
        nichts zu Kodierendes und bleiben lesbar — ein Verzeichnis mit
        diesen Dateien erzaehlt die Geschichte auch ohne die Bibliothek.

        NICHT geloest, sondern zugesagt (siehe VERTRAG.md): Ids, die sich
        nur in der Gross-/Kleinschreibung unterscheiden, kollidieren auf
        Dateisystemen, die das nicht trennen. Und die Id muss kodiert
        unter die Namenslaenge des Dateisystems passen.
        """
        name = urllib.parse.quote(str(node_id), safe="")
        return os.path.join(self._knoten_verzeichnis(collection_name), f"{name}.json")

    def _mark_node_dirty(self, collection_name, node_id):
        self._dirty_nodes.setdefault(collection_name, set()).add(node_id)

    def _persist_collection(self, collection_name):
        """Write only changed nodes to the temp-file — or buffer when inside a transaction."""
        dirty_ids = self._dirty_nodes.get(collection_name, set())
        if not dirty_ids:
            return
        if self._transaction_depth > 0:
            return  # buffered until commit
        self._dirty_nodes.pop(collection_name, None)
        self._knoten_schreiben(collection_name, dirty_ids)

    def _knoten_schreiben(self, collection_name, node_ids):
        """Die EINZIGE Stelle, an der Knoten auf die Platte gehen.

        Geschrieben wird je betroffenem Fach, jedes mit Arbeitsdatei, fsync
        und `os.replace` (Invariante der pDMS-Kopie, siehe Kopf). Es wird nie
        eine Datei an Ort und Stelle veraendert. Ein endgueltig geloeschter
        Knoten verschwindet aus seinem Fach; ein Fach ohne Knoten wird
        geloescht.
        """
        self._ablage_schreiben("nodes", collection_name, node_ids,
                               self._cache["nodes"].get(collection_name, {}))

    def _ablage(self, art, name):
        ablage = self._ablagen[art].get(name)
        if ablage is None:
            ablage = _Ablage(os.path.join(self.dirs[art], _ordnername(name)))
            self._ablagen[art][name] = ablage
        return ablage

    def _ablage_schreiben(self, art, name, kennungen, daten):
        ablage = self._ablage(art, name)
        nummern = set()
        # Sortiert: eine Transaktion bringt ihre Kennungen als Menge, und die
        # Reihenfolge einer Menge wechselt von Lauf zu Lauf. Ohne Sortieren
        # landete derselbe Knoten mal im einen, mal im anderen Fach.
        for k in sorted(kennungen):
            nr = ablage.zuordnen(k) if k in daten else ablage.entfernen(k)
            if nr is not None:
                nummern.add(nr)
        self._faecher_schreiben(ablage, nummern, daten)

    def _faecher_schreiben(self, ablage, nummern, daten):
        schritte = self._faecher_planen(ablage, nummern, daten)
        if self._plan is not None:
            self._plan.extend(schritte)       # Abschluss einer Transaktion
        else:
            self._schritte_ausfuehren(schritte)

    def _faecher_planen(self, ablage, nummern, daten):
        """Welche Faecher mit welchem Inhalt geschrieben (None: geloescht) werden."""
        schritte = []
        for nr in sorted(nummern):
            mitglieder = ablage.inhalt.get(nr, set())
            # Was nicht mehr im Speicher steht, gehoert nicht mehr ins Fach —
            # etwa nach einem Rollback, der eine schon zugeordnete Kennung
            # zuruecknahm.
            for k in [k for k in mitglieder if k not in daten]:
                ablage.entfernen(k)
            inhalt = {k: daten[k] for k in sorted(mitglieder)}
            if not inhalt:
                ablage.inhalt.pop(nr, None)
            schritte.append((ablage.pfad(nr), inhalt or None))
        return schritte

    # --- Absichtsdatei -------------------------------------------------
    # Bis hierher war eine Transaktion nur im SPEICHER unteilbar: beim
    # Abschluss wurde Fach fuer Fach geschrieben, und ein Absturz dazwischen
    # liess die Haelfte auf der Platte (VERTRAG.md 2.3, alte Fassung). Jetzt:
    #
    #   1. jedes neue Fach als <fach>.neu dauerhaft schreiben — das Alte
    #      bleibt unberuehrt; ein Absturz hier hinterlaesst nur Abfall,
    #      den das naechste Oeffnen wegraeumt
    #   2. die Absichtsdatei datenbank/_absicht.json dauerhaft schreiben:
    #      AB HIER GILT DIE TRANSAKTION
    #   3. die .neu an ihren Platz umbenennen, Leergewordenes loeschen
    #   4. die Absichtsdatei loeschen
    #
    # Beim Oeffnen fuehrt `_absicht_nachholen` eine gefundene Absicht zu
    # Ende; jeder Schritt darin laesst sich wiederholen. Ein einzelnes Fach
    # braucht das alles nicht: `os.replace` ist fuer eine Datei schon atomar.
    # Deshalb kostet ein Schreibvorgang ausserhalb einer Transaktion (fast
    # immer ein Fach) genau so viel wie vorher.

    def _absicht_datei(self):
        return os.path.join(os.path.dirname(self.dirs["nodes"]), "_absicht.json")

    def _schritte_ausfuehren(self, schritte):
        self._offen_pruefen()
        if self._absicht_haengt:
            # Erst den Rest der vorigen Transaktion. Sonst ueberschriebe
            # deren Nachholen beim naechsten Oeffnen, was jetzt geschrieben
            # wird.
            self._absicht_nachholen()
        if not schritte:
            return
        if len(schritte) == 1:
            pfad, inhalt = schritte[0]
            if inhalt is not None:
                self._save_json_atomic(pfad, inhalt)
            elif os.path.exists(pfad):
                os.remove(pfad)
            return

        neue = []
        try:
            for pfad, inhalt in schritte:
                if inhalt is None:
                    continue
                os.makedirs(os.path.dirname(pfad), exist_ok=True)
                neue.append(pfad + ".neu")
                with open(pfad + ".neu", "w", encoding="utf-8") as f:
                    json.dump(inhalt, f, indent=2, ensure_ascii=False, allow_nan=False)
                    f.flush()
                    os.fsync(f.fileno())
            for verzeichnis in {os.path.dirname(p) for p, _ in schritte}:
                self._verzeichnis_sichern(verzeichnis)
            absicht = {
                "absicht": 1,
                "ersetzen": [self._relativ(p) for p, i in schritte if i is not None],
                "entfernen": [self._relativ(p) for p, i in schritte if i is None],
            }
            self._save_json_atomic(self._absicht_datei(), absicht)
        except BaseException:
            # Vor dem Festschreiben: nichts ist geschehen, ausser Abfall.
            for neu in neue:
                try:
                    os.remove(neu)
                except OSError:
                    pass
            raise
        # Ab hier gilt die Transaktion.
        try:
            self._absicht_nachholen()
        except Exception as e:
            self._absicht_haengt = True
            raise AbschlussHaengt(
                f"Die Transaktion ist festgeschrieben, aber nicht alle Faecher "
                f"stehen schon an ihrem Platz ({e}). Der naechste "
                f"Schreibvorgang oder das naechste Oeffnen holt es nach.") from e

    def _relativ(self, pfad):
        return os.path.relpath(pfad, self.root).replace(os.sep, "/")

    def _verzeichnis_sichern(self, verzeichnis):
        try:
            fd = os.open(verzeichnis, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except (OSError, AttributeError):
            pass

    def _absicht_nachholen(self):
        """Eine festgeschriebene Absicht zu Ende fuehren. Wiederholbar."""
        datei = self._absicht_datei()
        if os.path.exists(datei + ".tmp"):
            # Eine Absicht, die nie an ihren Platz kam, galt nie.
            os.remove(datei + ".tmp")
        if not os.path.exists(datei):
            self._absicht_haengt = False
            return
        absicht = self._load_json_from_disk(datei)
        ersetzen = [self._absicht_pfad(r, datei) for r in self._absicht_liste(absicht, "ersetzen", datei)]
        entfernen = [self._absicht_pfad(r, datei) for r in self._absicht_liste(absicht, "entfernen", datei)]
        for pfad in ersetzen:
            # Fehlt die .neu, ist dieser Schritt schon getan.
            if os.path.exists(pfad + ".neu"):
                os.replace(pfad + ".neu", pfad)
        for pfad in entfernen:
            if os.path.exists(pfad):
                os.remove(pfad)
        for verzeichnis in {os.path.dirname(p) for p in ersetzen + entfernen}:
            self._verzeichnis_sichern(verzeichnis)
        os.remove(datei)
        # Auch das Loeschen muss dauerhaft sein: eine wiederauferstandene
        # alte Absicht fuehrte sonst .neu-Dateien einer SPAETEREN, nie
        # festgeschriebenen Transaktion aus.
        self._verzeichnis_sichern(os.path.dirname(datei))
        self._absicht_haengt = False

    @staticmethod
    def _absicht_liste(absicht, schluessel, datei):
        if not isinstance(absicht, dict) or absicht.get("absicht") != 1:
            raise DateiKaputt(f"'{datei}' ist keine Absichtsdatei dieser Fassung.")
        liste = absicht.get(schluessel, [])
        if not isinstance(liste, list) or not all(isinstance(r, str) for r in liste):
            raise DateiKaputt(f"'{datei}': '{schluessel}' ist keine Liste von Pfaden.")
        return liste

    def _absicht_pfad(self, relativ, datei):
        """Nur Faecher unter nodes/ und edges/ — eine Absichtsdatei ist
        Eingabe von der Platte und darf nichts anderes umbenennen oder
        loeschen."""
        pfad = os.path.normpath(os.path.join(self.root, relativ))
        erlaubt = any(os.path.dirname(os.path.dirname(pfad)) == os.path.normpath(self.dirs[art])
                      for art in ("nodes", "edges"))
        if not erlaubt or not re.fullmatch(r"fach_\d+\.json", os.path.basename(pfad)):
            raise DateiKaputt(f"'{datei}' nennt '{relativ}' — das ist kein Fach dieses Bestands.")
        return pfad

    def _ablage_laden(self, ablage, aufraeumen=True):
        """Alle Faecher einer Ablage lesen. Gibt (daten, zu_reparieren) zurueck.

        Eine Kennung in ZWEI Faechern kann nur ein abgebrochenes Verdichten
        hinterlassen: es schreibt erst die neuen Faecher, dann die alten ohne
        diese Kennung. Beide Kopien sind dann gleich; die im hoeheren Fach
        (dem neuen) gilt, die andere wird gleich nach dem Laden entfernt.
        Sind sie NICHT gleich, ist es kein Abbruch, sondern ein Schaden —
        dann wird nicht geraten.
        """
        daten, reparieren = {}, set()
        if not os.path.isdir(ablage.verzeichnis):
            return daten, reparieren
        for dateiname in sorted(os.listdir(ablage.verzeichnis)):
            if dateiname.endswith(".json.neu"):
                # Ohne Absichtsdatei (die ist vorher nachgeholt) gehoert
                # eine .neu zu einer Transaktion, die nie festgeschrieben
                # wurde. Sie gilt nicht.
                if aufraeumen:
                    os.remove(os.path.join(ablage.verzeichnis, dateiname))
                continue
            treffer = re.fullmatch(r"fach_(\d+)\.json", dateiname)
            if not treffer:
                continue
            nr = int(treffer.group(1))
            pfad = ablage.pfad(nr)
            for k, v in self._load_json_from_disk(pfad).items():
                if k in daten:
                    if daten[k] != v:
                        raise DateiKaputt(
                            f"'{k}' steht in zwei Faechern mit verschiedenem "
                            f"Inhalt ({ablage.pfad(ablage.fach_von[k])} und {pfad}).")
                    reparieren.add(ablage.entfernen(k))
                daten[k] = v
                ablage.eintragen(k, nr)
        return daten, reparieren

    def _flush_pending_writes(self):
        """Flush all buffered node and edge writes to disk (transaction commit).

        Alle Faecher als EIN Plan: betrifft er mehr als eines, geht er ueber
        die Absichtsdatei und steht nach einem Absturz ganz oder gar nicht
        auf der Platte.
        """
        beruehrt = [("nodes", n) for n in self._dirty_nodes] + [("edges", a) for a in self._dirty_edges]
        self._plan = []
        try:
            for collection_name in list(self._dirty_nodes.keys()):
                dirty_ids = self._dirty_nodes.pop(collection_name, set())
                if not dirty_ids:
                    continue
                self._knoten_schreiben(collection_name, dirty_ids)
            for rel_type, edge_ids in list(self._dirty_edges.items()):
                self._flush_kanten(rel_type, edge_ids)
            self._dirty_edges.clear()
            plan = self._plan
        finally:
            self._plan = None
        try:
            self._schritte_ausfuehren(plan)
        except AbschlussHaengt:
            raise
        except BaseException:
            # Nicht festgeschrieben: auf der Platte steht der alte Stand. Das
            # Planen hat aber schon Kennungen Faechern zugeordnet oder aus
            # ihnen genommen; bliebe das stehen, landete eine Kennung beim
            # naechsten Schreiben in einem zweiten Fach. Also die Zuordnung
            # von der Platte neu lesen — nur im Fehlerfall, nur fuer das
            # Beruehrte.
            for art, name in beruehrt:
                alt = self._ablagen[art].get(name)
                if alt is not None:
                    frisch = _Ablage(alt.verzeichnis)
                    # Ohne Aufraeumen: der Fehler, der hierher fuehrte,
                    # soll beim Aufrufer ankommen, nicht ein Folgefehler
                    # beim Loeschen einer .neu. Die raeumt das Oeffnen weg.
                    self._ablage_laden(frisch, aufraeumen=False)
                    self._ablagen[art][name] = frisch
            raise

    def flush(self):
        """Write all buffered writes to disk immediately. Useful outside transaction()."""
        self._flush_pending_writes()

    @contextlib.contextmanager
    def transaction(self):
        """
        Atomic transaction: all writes are buffered in RAM and flushed to disk as a
        batch on exit. On exception: full rollback. Nested transactions join the outer one.

        Haelt die Sperre der Instanz ueber den GANZEN Block. Sonst schriebe
        ein anderer Thread mitten in diese Transaktion hinein — sein
        Schreibvorgang landete im Puffer dieser Transaktion und verschwaende
        mit ihrem Rollback, ohne dass er davon je erfaehrt. Damit ist eine
        Transaktion zugleich der Weg, mehrere Aufrufe gegen andere Threads
        unteilbar zu machen (etwa next_id und create_node).
        """
        with self._sperre:
            yield from self._transaktion_ohne_sperre()

    def _transaktion_ohne_sperre(self):
        if self._transaction_depth > 0:
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1
            return

        # Bis 3.0.0 wurde hier der GANZE Bestand tief kopiert, als
        # Rueckfallstand fuer einen Rollback — gemessen bei 10 000 Knoten
        # (bench Block 4) 156 ms, auch fuer eine Transaktion mit einer
        # einzigen Aenderung, und ein Rollback kostete 232 ms, weil er
        # danach den Nachbarschaftsindex ueber ALLE Kanten neu baute.
        # Jetzt merkt sich jede Aenderung vor ihrer ersten Beruehrung den
        # alten Zustand (_vormerken_*), und ein Rollback setzt nur diese
        # Eintraege zurueck. Kosten: was die Transaktion beruehrt, nicht
        # was der Bestand enthaelt.
        self._undo = {}
        self._transaction_depth = 1
        try:
            yield
            self._transaction_depth = 0
            self._flush_pending_writes()
            self._undo = None
        except AbschlussHaengt:
            # Festgeschrieben: der Speicher zeigt, was nach dem Nachholen auf
            # der Platte steht. Zuruecksetzen hiesse, ihm zu widersprechen.
            self._undo = None
            meldungen, self._meldungen = self._meldungen, []
            self._zustellen(meldungen)
            raise
        except Exception:
            self._meldungen = []
            # Auch ein Fehler beim Schreiben selbst landet hier. Er kam vor
            # dem Festschreiben der Absicht (sonst AbschlussHaengt): auf der
            # Platte steht noch der alte Stand, also auch im Speicher.
            self._transaction_depth = 0
            self._rueckgaengig()
            raise
        # Erst jetzt, wo alles auf der Platte steht, und ausserhalb des
        # try: ein Fehler im Rueckruf ist kein Fehler der Transaktion.
        meldungen, self._meldungen = self._meldungen, []
        self._zustellen(meldungen)

    # --- Undo-Log ----------------------------------------------------
    # Jede Stelle, die innerhalb einer Transaktion Knoten oder Kanten im
    # Speicher aendert, ruft VORHER eine dieser Methoden. Eine Stelle, die
    # das vergisst, wird beim Rollback nicht zurueckgesetzt — deshalb
    # vergleicht tests/test_flatgraph_transaktion.py nach jedem Rollback den
    # vollstaendigen Zustand, im Speicher und auf der Platte.

    def _vormerken_knoten(self, collection, node_id):
        if self._undo is None:
            return
        if collection not in self._cache["nodes"]:
            self._undo.setdefault(("sammlung", collection), True)
        schluessel = ("knoten", collection, node_id)
        if schluessel in self._undo:
            return      # zaehlt nur der Stand VOR der ersten Aenderung
        alt = self._cache["nodes"].get(collection, {}).get(node_id, _FEHLTE)
        # Eine Kopie: update_node aendert den Knoten an Ort und Stelle.
        self._undo[schluessel] = alt if alt is _FEHLTE else copy.deepcopy(alt)

    def _vormerken_kante(self, rel_type, edge_id):
        if self._undo is None:
            return
        if rel_type not in self._cache["edges"]:
            self._undo.setdefault(("kantenart", rel_type), True)
        schluessel = ("kante", rel_type, edge_id)
        if schluessel in self._undo:
            return
        # Keine Kopie: Kanten werden nie an Ort und Stelle geaendert, nur
        # angelegt und geloescht. Das Objekt selbst zurueckzulegen haelt
        # ausserdem den Nachbarschaftsindex gueltig, der darauf zeigt.
        self._undo[schluessel] = self._cache["edges"].get(rel_type, {}).get(edge_id, _FEHLTE)

    def _rueckgaengig(self):
        undo, self._undo = self._undo or {}, None
        for schluessel, alt in undo.items():
            if schluessel[0] == "knoten":
                _, col, nid = schluessel
                self._index_austragen(col, nid)
                knoten = self._cache["nodes"].setdefault(col, {})
                if alt is _FEHLTE:
                    knoten.pop(nid, None)
                else:
                    knoten[nid] = alt
                self._index_eintragen(col, nid)
            elif schluessel[0] == "kante":
                _, art, eid = schluessel
                eimer = self._cache["edges"].setdefault(art, {})
                jetzt = eimer.pop(eid, None)
                if jetzt is not None:
                    self._unindex_edge(eid, jetzt)
                    self._edge_type_index.pop(eid, None)
                if alt is not _FEHLTE:
                    eimer[eid] = alt
                    self._edge_type_index[eid] = art
                    self._index_edge(eid, alt)
        # Sammlungen und Kantenarten, die erst in der Transaktion entstanden,
        # wieder entfernen — erst jetzt, wo ihre Eintraege zurueckgesetzt sind.
        for schluessel in undo:
            if schluessel[0] == "sammlung" and not self._cache["nodes"].get(schluessel[1]):
                self._cache["nodes"].pop(schluessel[1], None)
                self._index_cache.pop(schluessel[1], None)
            elif schluessel[0] == "kantenart" and not self._cache["edges"].get(schluessel[1]):
                self._cache["edges"].pop(schluessel[1], None)
        self._dirty_nodes.clear()
        self._dirty_edges.clear()

    def _persist_collection_full(self, collection_name):
        """In Form 2 bleibt nur das Aufraeumen nach dem Muellsammler.

        Das Verdichten selbst ist weggefallen. Es gab es nur, weil in Form 1
        neben der Sammeldatei ein Delta lag, das irgendwann zusammengefuehrt
        werden musste — und weil ein endgueltig geloeschter Knoten in der
        Sammeldatei stehenblieb, bis das geschah. Eine Datei je Knoten
        kennt beides nicht: geloescht ist geloescht, sobald die Datei weg
        ist.
        """
        purged = self._purged_nodes.pop(collection_name, set())
        if purged:
            self._knoten_schreiben(collection_name, purged)
        self._dirty_nodes.pop(collection_name, None)

    def _validate_node(self, collection_name, data):
        """Validate data against the schema for this collection (if one is defined)."""
        if collection_name not in self.schemas:
            return
        schema = self.schemas[collection_name]
        for field, spec in schema.items():
            # --- plain Python type (existing behaviour, fully backward-compatible) ---
            if isinstance(spec, type):
                if field not in data:
                    raise ValueError(
                        f"Required field '{field}' missing in collection '{collection_name}'."
                    )
                if not isinstance(data[field], spec):
                    raise TypeError(
                        f"Field '{field}' must be of type {spec.__name__}."
                    )
                continue

            if not isinstance(spec, dict):
                raise ValueError(f"Invalid schema spec for field '{field}': {spec!r}")

            field_type = spec.get("type")

            # --- enum: {"type": str|list, "options": [...]} ---
            if field_type in (str, list):
                if field not in data:
                    raise ValueError(
                        f"Required field '{field}' missing in collection '{collection_name}'."
                    )
                value = data[field]
                if not isinstance(value, field_type):
                    name = field_type.__name__
                    raise TypeError(f"Field '{field}' must be of type {name}.")
                options = spec.get("options")
                if options is not None:
                    items = value if isinstance(value, list) else [value]
                    for item in items:
                        if item not in options:
                            raise ValueError(
                                f"Field '{field}': '{item}' is not an allowed value. "
                                f"Allowed: {options}"
                            )
                continue

            # --- link: {"type": "link", "target": "collection_name"} ---
            if field_type == "link":
                value = data.get(field)
                if value is None:
                    continue  # link fields are optional
                target_col = spec.get("target")
                if not self._node_ref_exists(value, expected_collection=target_col):
                    hint = f" in collection '{target_col}'" if target_col else ""
                    raise ValueError(
                        f"Field '{field}': referenced node '{value}' does not exist{hint}."
                    )
                continue

            raise ValueError(f"Unknown schema type '{field_type}' for field '{field}'.")

    def _node_ref_exists(self, ref, expected_collection=None):
        """Return True if ref points to an existing, non-deleted node."""
        if not isinstance(ref, str) or "/" not in ref:
            return False
        col, nid = ref.split("/", 1)
        if expected_collection and col != expected_collection:
            return False
        node = self._cache["nodes"].get(col, {}).get(nid)
        return node is not None and not self._is_deleted(node)

    def _validate_edge(self, source_ref, target_ref, rel_type):
        """Check source/target collections against declared edge_constraints (if any)."""
        allowed = self.edge_constraints.get(rel_type)
        if allowed is None:
            return  # no constraint declared for this rel_type
        src_col = source_ref.split("/", 1)[0] if "/" in source_ref else source_ref
        tgt_col = target_ref.split("/", 1)[0] if "/" in target_ref else target_ref
        if (src_col, tgt_col) not in [tuple(p) for p in allowed]:
            raise ValueError(
                f"Edge type '{rel_type}' does not allow "
                f"'{src_col}' → '{tgt_col}'. "
                f"Allowed pairs: {[list(p) for p in allowed]}"
            )

    # --- Pruefungen VOR jeder Aenderung am Speicher ---------------------
    # Die Regel dahinter: der Speicher aendert sich nur, wenn feststeht,
    # dass die Platte die Aenderung aufnehmen kann. Jeder Fehler dieser
    # Klasse hatte dieselbe Form — erst Speicher, dann Platte, und wenn die
    # Platte ablehnte, widersprachen sich beide.

    @staticmethod
    def _name_pruefen(name, was):
        if not isinstance(name, str) or not _NAMENSREGEL.fullmatch(name):
            raise UngueltigerName(
                f"{was} {name!r} ist kein zulaessiger Name: erlaubt sind "
                f"Buchstaben, Ziffern, '_', '-', '.', am Anfang ein Buchstabe "
                f"oder eine Ziffer, hoechstens 100 Zeichen.")

    @staticmethod
    def _kennung_pruefen(node_id):
        # Nur Zeichenketten: eine Zahl als Kennung stand im Speicher unter 5
        # und nach dem Neustart unter "5" — `get_node("a/5")` fand sie
        # vorher nicht und nachher schon.
        if not isinstance(node_id, str) or not node_id:
            raise UngueltigerName(
                f"Eine Kennung muss eine nicht leere Zeichenkette sein; "
                f"erhalten {node_id!r}.")
        # Eine Laengengrenze gab es bis Speicherform 2, weil die Kennung dort
        # ein Dateiname war (255 Bytes). Seit den Faechern ist sie ein
        # Schluessel in einer Datei — die Grenze hat keinen Grund mehr.

    @staticmethod
    def _speicherbar_pruefen(daten, wo):
        """Kommt `daten` als JSON unveraendert zurueck?

        Nicht nur „laesst es sich schreiben": ein Tupel wird zur Liste, ein
        Zahlenschluessel zum Text. Beides liesse sich schreiben, stuende
        danach aber im Speicher anders als auf der Platte — und nach dem
        Neustart gaelte die Platte. Der Vergleich faengt genau das.
        """
        try:
            text = json.dumps(daten, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as e:
            raise NichtSpeicherbar(f"{wo}: {e}") from e
        if json.loads(text) != daten:
            raise NichtSpeicherbar(
                f"{wo}: kaeme als JSON veraendert zurueck (Tupel werden zu "
                f"Listen, Zahlenschluessel zu Text).")

    def _is_deleted(self, node_data):
        return node_data is not None and "_deletion_flag" in node_data

    # ------------------------------------------------------------------
    # Internal helpers — vault_text
    # ------------------------------------------------------------------

    _VAULT_TEXT_PREFIX = "@vault_text/"

    # Eine Datei je INHALT, nie ueberschrieben. Bis 3.0.0 hiess die Datei nur
    # nach Knoten und Feld und wurde an Ort und Stelle neu geschrieben:
    # ein Rollback liess den Knoten auf dieselbe Datei zeigen, die aber schon
    # den neuen Text enthielt; ein Absturz mitten im Schreiben hinterliess
    # eine halbe; und weil jedes Sonderzeichen zu "_" wurde, teilten sich
    # `DOC.1` und `DOC_1` eine Datei und ueberschrieben einander lautlos.
    # Mit dem Hash im Namen zeigt ein Verweis immer auf genau den Text, mit
    # dem er entstand; alte Fassungen raeumt der Muellsammler als Waisen weg.
    def _vt_path(self, collection_name, node_id, field, text):
        """Pfad der Datei fuer genau diesen Text dieses Felds."""
        safe = re.sub(r"[^\w\-]", "_", f"{collection_name}__{node_id}__{field}")
        pruef = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        return os.path.join(self.dirs["vault_text"], f"{safe}__{pruef}.txt")

    def _vt_schreiben(self, pfad, text):
        # Liegt die Datei schon da, hat sie denselben Inhalt (Hash im Namen)
        # und ist vollstaendig (sie entstand ueber os.replace).
        if os.path.exists(pfad):
            return
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        arbeit = pfad + ".tmp"
        with open(arbeit, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(arbeit, pfad)
        self._verzeichnis_sichern(os.path.dirname(pfad))

    def _offload_longtexts(self, collection_name, node_id, data):
        """
        If longtext_threshold is set, replace any string value that exceeds
        the threshold with an @vault_text/ reference. Modifies data in-place.
        """
        if not self.longtext_threshold:
            return
        for field, value in list(data.items()):
            if field.startswith("_"):
                continue
            if isinstance(value, str) and len(value) > self.longtext_threshold:
                path = self._vt_path(collection_name, node_id, field, value)
                self._vt_schreiben(path, value)
                data[field] = f"{self._VAULT_TEXT_PREFIX}{os.path.relpath(path, self.root)}"

    def _resolve_longtexts(self, data):
        """Return a copy of data with all @vault_text/ references resolved to their text."""
        resolved = {}
        for field, value in data.items():
            if isinstance(value, str) and value.startswith(self._VAULT_TEXT_PREFIX):
                rel = value[len(self._VAULT_TEXT_PREFIX):]
                abs_path = os.path.join(self.root, rel)
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        resolved[field] = f.read()
                except OSError:
                    resolved[field] = value  # keep reference if file missing
            else:
                resolved[field] = value
        return resolved

    # ------------------------------------------------------------------
    # Internal helpers — field index
    # ------------------------------------------------------------------

    # Der Feldindex liegt NUR im Arbeitsspeicher und wird bei jedem
    # Schreibvorgang fuer genau den einen Knoten nachgefuehrt.
    #
    # Bis 3.0.0 wurde er bei JEDER Aenderung fuer die ganze Sammlung
    # verworfen und zusaetzlich als Datei unter datenbank/index/ abgelegt,
    # gesichert durch eine Pruefsumme ueber den Inhalt JEDES Knotens
    # (json.dumps + MD5). Gemessen am 24.09.2026 bei 10 000 Knoten
    # (flatgraph/bench, Block 6): eine Aenderung 0.8 ms, dieselbe Aenderung
    # mit anschliessender Suche 159 ms — zweimal die Pruefsumme zu je 76 ms
    # plus fsync der Indexdatei. Den Index aus dem Speicher zu bauen
    # kostet 3.6 ms; die Datei zu PRUEFEN kostete zwanzigmal so viel, wie
    # sie ersparte. Deshalb gibt es sie nicht mehr, und mit ihr die
    # Pruefsumme, die nur ihretwegen da war.
    #
    # Warum Mengen statt Listen: das Austragen eines Knotens aus einem
    # Wert, den tausend Knoten teilen, waere mit einer Liste ein Durchgang
    # durch alle tausend.

    @staticmethod
    def _index_schluessel(val):
        return str(val).lower()

    def _index_austragen(self, collection, node_id):
        """VOR einer Aenderung: den Knoten mit seinen bisherigen Werten
        aus jedem gebauten Feldindex seiner Sammlung nehmen."""
        felder = self._index_cache.get(collection)
        if not felder:
            return
        daten = self._cache["nodes"].get(collection, {}).get(node_id)
        if daten is None or self._is_deleted(daten):
            return
        for field, idx in felder.items():
            val = daten.get(field)
            if val is None:
                continue
            schluessel = self._index_schluessel(val)
            ids = idx.get(schluessel)
            if ids is not None:
                ids.discard(node_id)
                # Leere Eintraege wegraeumen: die Teilstringsuche geht ueber
                # alle Schluessel, und ein toter Schluessel kostete sie bei
                # jeder Suche, fuer immer.
                if not ids:
                    del idx[schluessel]

    def _index_eintragen(self, collection, node_id):
        """NACH einer Aenderung: den Knoten mit seinen neuen Werten
        eintragen. Weich Geloeschte werden nicht eingetragen — genau wie
        beim Neuaufbau."""
        felder = self._index_cache.get(collection)
        if not felder:
            return
        daten = self._cache["nodes"].get(collection, {}).get(node_id)
        if daten is None or self._is_deleted(daten):
            return
        for field, idx in felder.items():
            val = daten.get(field)
            if val is not None:
                idx.setdefault(self._index_schluessel(val), set()).add(node_id)

    def _mark_index_dirty(self, collection):
        """Den Index einer Sammlung verwerfen. Nur noch fuer seltene
        Massenwege (Muellsammler), wo Nachfuehren je Knoten mehr Code als
        Nutzen waere; der Neuaufbau aus dem Speicher kostet Millisekunden."""
        self._index_cache.pop(collection, None)

    def _build_field_index(self, collection, field):
        idx = {}
        for node_id, data in self._cache["nodes"].get(collection, {}).items():
            if self._is_deleted(data):
                continue
            val = data.get(field)
            if val is not None:
                idx.setdefault(self._index_schluessel(val), set()).add(node_id)
        return idx

    def _get_field_index(self, collection, field):
        """Den Index eines Feldes; beim ersten Bedarf aus dem Speicher gebaut."""
        felder = self._index_cache.setdefault(collection, {})
        if field not in felder:
            felder[field] = self._build_field_index(collection, field)
        return felder[field]

    # ------------------------------------------------------------------
    # OEFFENTLICHE API - NODES
    # ------------------------------------------------------------------

    def create_node(self, collection_name, node_id, data):
        """
        Create a node.
        :return: node reference as string "collection/id"
        :raises KeyError: if node_id already exists in this collection
        """
        self._offen_pruefen()
        # Alle Pruefungen vor der ersten Zeile, die den Speicher anfasst —
        # auch vor dem Anlegen der leeren Sammlung weiter unten.
        self._name_pruefen(collection_name, "Sammlung")
        self._kennung_pruefen(node_id)
        self._validate_node(collection_name, data)
        self._speicherbar_pruefen(data, f"Knoten {collection_name}/{node_id}")

        if node_id in self._cache["nodes"].get(collection_name, {}):
            raise KnotenExistiert(
                f"Node '{node_id}' already exists in collection '{collection_name}'. "
                f"Use update_node() to modify existing nodes."
            )

        stored = copy.deepcopy(data)
        self._offload_longtexts(collection_name, node_id, stored)
        self._vormerken_knoten(collection_name, node_id)
        self._cache["nodes"].setdefault(collection_name, {})[node_id] = stored
        # Direkt nach der Aenderung am Speicher, VOR dem Schreiben: scheitert
        # das Schreiben, muss der Index trotzdem zum Speicher passen.
        self._index_eintragen(collection_name, node_id)
        self._mark_node_dirty(collection_name, node_id)
        self._persist_collection(collection_name)
        self._log_audit("create", collection_name, node_id)
        self._melden_knoten("create_node", collection_name, node_id)
        return f"{collection_name}/{node_id}"

    def get_node(self, node_ref, readonly=False):
        """
        Read a node from the RAM cache.
        Returns None if the node does not exist or is soft-deleted.

        :param readonly: If True, return a direct reference to the cached dict instead of a
                         deep copy. Faster for display-only use — caller must never mutate
                         the returned dict.
        """
        if not isinstance(node_ref, str) or "/" not in node_ref:
            # Vorher `return None` — damit war ein Tippfehler im Aufruf von
            # "den Knoten gibt es nicht" ununterscheidbar.
            raise UngueltigeReferenz(
                f"Node reference must be 'collection/id'; got {node_ref!r}.")
        col, n_id = node_ref.split("/", 1)

        node_data = self._cache["nodes"].get(col, {}).get(n_id)
        # `is not None`, nicht `if node_data`: ein Knoten OHNE Felder ist ein
        # leeres Dict und damit falsy — er waere sonst von "gibt es nicht"
        # ununterscheidbar. Genau diesen Fall beschreibt das readme als
        # EMPTY-Infosatz (ein Datensatz, dessen Nutzlast noch fehlt), und
        # `create_edge` lehnte es deshalb ab, ihn zu verknuepfen.
        # get_node_raw und _node_ref_exists machten es schon richtig; nur
        # get_node nicht — dieselbe Frage, drei Antworten.
        if node_data is not None and not self._is_deleted(node_data):
            return node_data if readonly else copy.deepcopy(node_data)
        return None

    def get_node_raw(self, node_ref):
        """
        Like get_node, but also returns soft-deleted nodes.
        Useful for maintenance / admin views.
        """
        try:
            col, n_id = node_ref.split("/", 1)
        except ValueError:
            return None
        raw = self._cache["nodes"].get(col, {}).get(n_id)
        return copy.deepcopy(raw) if raw is not None else None

    def get_node_full(self, node_ref):
        """
        Like get_node, but resolves all @vault_text/ references in the returned dict,
        replacing them with the full text content from disk.
        Returns None if the node does not exist or is soft-deleted.
        """
        data = self.get_node(node_ref)
        if data is None:
            return None
        return self._resolve_longtexts(data)

    def update_node(self, collection_name, node_id, update_data):
        """
        Update fields of an existing node.
        Validates the merged state against the schema (if one is defined).
        """
        self._offen_pruefen()
        col_cache = self._cache["nodes"].get(collection_name, {})
        if node_id not in col_cache:
            # Vorher: `return False`. Ein Schreibvorgang, der ins Leere
            # geht, meldete sich mit einem Rueckgabewert, den Aufrufer
            # routinemaessig ignorieren — nachgezaehlt in pDMS: 22
            # Aufrufstellen, KEINE davon liest ihn. Eine vertippte Kennung
            # hiess damit: nichts passiert, niemand merkt es.
            raise KnotenFehlt(f"Node '{node_id}' does not exist in "
                              f"collection '{collection_name}'.")

        # Simulate merge to check schema before applying
        merged = {**col_cache[node_id], **update_data}
        self._speicherbar_pruefen(merged, f"Knoten {collection_name}/{node_id}")
        schema_check_data = {k: v for k, v in merged.items()
                             if k not in _INTERNE_KNOTENFELDER}
        try:
            self._validate_node(collection_name, schema_check_data)
        except (ValueError, TypeError):
            # soft_delete setzt nur flatgraphs eigene Flaggen — ein Knoten,
            # der schon vorher nicht zum Schema passte, darf sich trotzdem
            # loeschen lassen. Ein Aufrufer-Feld ist das nie.
            if not all(k in _INTERNE_KNOTENFELDER for k in update_data):
                raise

        self._offload_longtexts(collection_name, node_id, update_data)
        self._vormerken_knoten(collection_name, node_id)
        self._index_austragen(collection_name, node_id)
        col_cache[node_id].update(update_data)
        self._index_eintragen(collection_name, node_id)
        self._mark_node_dirty(collection_name, node_id)
        self._persist_collection(collection_name)
        # Dieselbe Grenze wie bei der Schemapruefung: was flatgraph selbst
        # schreibt, ist keine inhaltliche Aenderung und kommt nicht ins
        # Protokoll — alles andere schon, auch mit Unterstrich davor.
        public_fields = {k: v for k, v in update_data.items()
                         if k not in _INTERNE_KNOTENFELDER}
        if public_fields:
            self._log_audit("update", collection_name, node_id, str(list(public_fields.keys())))
            self._melden_knoten("update_node", collection_name, node_id)
        return True

    def list_nodes(self, collection_name, include_deleted=False, readonly=False):
        """
        Return all nodes of a collection as dict {node_id: data}.
        Soft-deleted entries are excluded by default.

        :param readonly: If True, values are direct cache references (no deep copy).
                         Faster for display/reporting — caller must never mutate the dicts.
        """
        col = self._cache["nodes"].get(collection_name, {})
        _copy = (lambda d: d) if readonly else copy.deepcopy
        if include_deleted:
            return {nid: _copy(data) for nid, data in col.items()}
        return {nid: _copy(data) for nid, data in col.items() if not self._is_deleted(data)}

    def list_collections(self):
        """Return names of all known node collections (internal collections excluded)."""
        return [c for c in self._cache["nodes"] if c not in _INTERNAL_COLLECTIONS]

    def _log_audit(self, action, collection, node_id, details=None):
        """Write an audit entry to _audit_log (only when audit=True)."""
        if not self.audit or self._audit_writing:
            return
        if collection in _INTERNAL_COLLECTIONS:
            return
        self._audit_writing = True
        try:
            entry_id = uuid.uuid4().hex
            entry = {
                "ref":        f"{collection}/{node_id}",
                "action":     action,
                "changed_at": datetime.now(timezone.utc).isoformat(),
            }
            if details:
                entry["details"] = details
            # Auch das Protokoll gehoert zur Transaktion: eine
            # zurueckgenommene Aenderung hinterlaesst keinen Eintrag.
            self._vormerken_knoten("_audit_log", entry_id)
            self._cache["nodes"].setdefault("_audit_log", {})[entry_id] = entry
            self._mark_node_dirty("_audit_log", entry_id)
            self._persist_collection("_audit_log")
        finally:
            self._audit_writing = False

    # --- Meldungen bei Aenderungen ---------------------------------------
    # Bis 3.0.0 verschickte flatgraph selbst HTTP (Webhooks): je Ereignis
    # ein neuer Thread, ohne Obergrenze, jeder Fehler still verschluckt,
    # beliebige URLs — und in einer Transaktion SOFORT, also auch fuer
    # Aenderungen, die ein Rollback danach zuruecknahm. Eine Speicher-
    # bibliothek verschickt nichts; sie sagt Bescheid, und der Anwender
    # entscheidet, was er damit tut.

    def _melden(self, ereignis, ref, **mehr):
        if self.bei_aenderung is None:
            return
        meldung = {"ereignis": ereignis, "ref": ref,
                   "zeit": datetime.now(timezone.utc).isoformat(), **mehr}
        if self._transaction_depth > 0:
            self._meldungen.append(meldung)
        else:
            self._zustellen([meldung])

    def _zustellen(self, meldungen):
        for meldung in meldungen:
            try:
                self.bei_aenderung(meldung)
            except Exception:
                # Die Aenderung steht schon auf der Platte. Den Fehler zu
                # werfen hiesse, dem Aufrufer ein Scheitern vorzuspielen,
                # das keins war; ihn zu verschlucken, wie die Webhooks es
                # taten, hiesse, ihn nie zu sehen.
                _log.exception("bei_aenderung scheiterte an %s %s",
                               meldung["ereignis"], meldung["ref"])

    def _melden_knoten(self, ereignis, collection, node_id):
        if collection in _INTERNAL_COLLECTIONS:
            return
        self._melden(ereignis, f"{collection}/{node_id}", sammlung=collection)

    def find_nodes(self, collection_name, match, readonly=False):
        """
        Search nodes by field values. Supports three matching modes per field:

          Substring   {"title": "pump"}           -> case-insensitive substring
          Wildcard    {"title": "LH*Pump*"}        -> * as wildcard
          Predicate   {"title": lambda v: ...}    -> arbitrary logic, linear scan

        Multiple fields are AND-combined.

        :param readonly: If True, values are direct cache references (no deep copy).
                         Faster for display/reporting — caller must never mutate the dicts.
        :return: {node_id: node_data}
        """
        if not match:
            return self.list_nodes(collection_name, readonly=readonly)

        result_ids = None

        for field, criterion in match.items():
            if callable(criterion):
                nodes = self._cache["nodes"].get(collection_name, {})
                matching = {
                    nid for nid, data in nodes.items()
                    if not self._is_deleted(data) and criterion(data.get(field))
                }
            elif isinstance(criterion, str) and "*" in criterion:
                pattern = re.compile(
                    "^" + re.escape(criterion.lower()).replace(r"\*", ".*") + "$"
                )
                field_idx = self._get_field_index(collection_name, field)
                matching = {nid for val, ids in field_idx.items()
                            if pattern.match(val) for nid in ids}
            else:
                needle = str(criterion).lower()
                field_idx = self._get_field_index(collection_name, field)
                matching = {nid for val, ids in field_idx.items()
                            if needle in val for nid in ids}

            result_ids = matching if result_ids is None else result_ids & matching

        if result_ids is None:
            return {}

        nodes = self._cache["nodes"].get(collection_name, {})
        _copy = (lambda d: d) if readonly else copy.deepcopy
        return {
            nid: _copy(nodes[nid])
            for nid in result_ids
            if nid in nodes and not self._is_deleted(nodes[nid])
        }

    def next_id(self, collection_name, prefix="", padding=0):
        """
        Return the next available ID in a collection.
        Scans existing IDs with the given prefix and returns max+1.

        :param collection_name: collection to scan
        :param prefix: ID prefix, e.g. 'DOC-' or 'EQ-'
        :param padding: zero-pad the number, e.g. 4 → 'DOC-0001'
        :return: Neue ID als String

        Beispiel:
            db.next_id('documents', prefix='DOC-', padding=4)
            -> 'DOC-0001' wenn leer, 'DOC-0042' wenn DOC-0041 existiert
        """
        existing = self._cache["nodes"].get(collection_name, {})
        max_num = 0
        for nid in existing:
            if prefix and not nid.startswith(prefix):
                continue
            num_part = nid[len(prefix):]
            try:
                num = int(num_part)
                if num > max_num:
                    max_num = num
            except ValueError:
                continue
        next_num = max_num + 1
        if padding > 0:
            return f"{prefix}{str(next_num).zfill(padding)}"
        return f"{prefix}{next_num}"

    # ------------------------------------------------------------------
    # OEFFENTLICHE API - IMPORT / EXPORT
    # ------------------------------------------------------------------

    def _import_records(self, collection_name, records, id_field, field_map, on_conflict):
        """Shared core for import_json / import_csv."""
        if on_conflict not in ("error", "skip", "overwrite"):
            raise ValueError(f"on_conflict must be 'error', 'skip', or 'overwrite'; got '{on_conflict}'")
        imported = skipped = 0
        with self.transaction():
            for raw in records:
                # Ein Paar (kennung, datensatz) kommt aus der Dict-Form, wo die
                # Kennung der Schluessel ist und nicht im Datensatz steht.
                vorgabe_id = None
                if isinstance(raw, tuple):
                    vorgabe_id, raw = raw
                # apply field_map (rename keys)
                if field_map:
                    row = {field_map.get(k, k): v for k, v in raw.items()}
                    src_id_key = field_map.get(id_field, id_field)
                else:
                    row = dict(raw)
                    src_id_key = id_field
                node_id = (str(vorgabe_id) if vorgabe_id is not None
                           else str(row.get(src_id_key) or raw.get(id_field, "")))
                if not node_id:
                    raise ValueError(f"id_field '{id_field}' missing or empty in record: {raw}")
                exists = node_id in self._cache["nodes"].get(collection_name, {})
                if exists:
                    if on_conflict == "error":
                        raise KeyError(f"Node '{node_id}' already exists in '{collection_name}'.")
                    if on_conflict == "skip":
                        skipped += 1
                        continue
                    self.update_node(collection_name, node_id, row)
                else:
                    self.create_node(collection_name, node_id, row)
                imported += 1
        return {"imported": imported, "skipped": skipped}

    def import_json(self, collection_name, filepath, id_field=None,
                    field_map=None, on_conflict="error"):
        """
        Import nodes from a JSON file into a collection.

        The file may contain either a list of dicts or a dict of dicts
        (values are treated as records).

        :param id_field:    Field name in the source data whose value becomes the node ID.
        :param field_map:   Optional {source_field: target_field} rename map.
        :param on_conflict: 'error' (default) | 'skip' | 'overwrite'
        :return: {"imported": n, "skipped": n}
        """
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Bei der Dict-Form IST der Schluessel die Kennung — genau so schreibt
        # export_json. `list(data.values())` warf ihn weg, und danach verlangte
        # der Import ein id_field, das im Datensatz gar nicht steht: Export und
        # Import passten nicht zusammen, obwohl beide aus Issue #16 stammen.
        # Gefunden von tests/test_flatgraph_neuerungen.py.
        if isinstance(data, dict) and id_field is None:
            records = list(data.items())
        else:
            records = data if isinstance(data, list) else list(data.values())
        return self._import_records(collection_name, records, id_field, field_map, on_conflict)

    def import_csv(self, collection_name, filepath, id_field,
                   field_map=None, on_conflict="error"):
        """
        Import nodes from a CSV file into a collection.

        :param id_field:    Column name whose value becomes the node ID.
        :param field_map:   Optional {source_column: target_field} rename map.
        :param on_conflict: 'error' (default) | 'skip' | 'overwrite'
        :return: {"imported": n, "skipped": n}
        """
        import csv
        with open(filepath, "r", encoding="utf-8", newline="") as f:
            records = list(csv.DictReader(f))
        return self._import_records(collection_name, records, id_field, field_map, on_conflict)

    def export_json(self, collection_name, filepath):
        """
        Export all nodes of a collection to a JSON file.
        Format: {"node_id": {fields...}, ...}
        """
        nodes = self.list_nodes(collection_name)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(nodes, f, indent=2, ensure_ascii=False)

    def export_csv(self, collection_name, filepath, fields=None):
        """
        Export all nodes of a collection to a CSV file.

        :param fields: Optional list of field names to include (all fields if None).
                       The node ID is always written as the first column '_id'.
        """
        import csv
        nodes = self.list_nodes(collection_name)
        if not nodes:
            with open(filepath, "w", encoding="utf-8", newline="") as f:
                pass
            return
        if fields is None:
            seen = {}
            for data in nodes.values():
                for k in data:
                    seen.setdefault(k, None)
            fields = [k for k in seen if not k.startswith("_")]
        with open(filepath, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["_id"] + list(fields),
                                    extrasaction="ignore")
            writer.writeheader()
            for node_id, data in nodes.items():
                writer.writerow({"_id": node_id, **{k: data.get(k, "") for k in fields}})

    def soft_delete(self, collection_name, node_id, keep_asset=True):
        """Den Knoten in den Papierkorb legen — und mit ihm alles, was er per
        Kaskade mitnimmt (siehe `loeschfolgen`).

        Bis 3.0.0 markierte erst der Muellsammler die Kaskaden-Ziele und
        loeschte sie im SELBEN Lauf endgueltig: man hat sie nie im Papierkorb
        gesehen und konnte es sich nicht mehr anders ueberlegen. Jetzt landen
        sie sofort dort, markiert mit `_geloescht_durch`, und `restore_node`
        auf diesen Knoten holt sie zusammen zurueck.

        Laeuft als eine Transaktion: auf der Platte steht danach alles oder
        nichts davon.
        """
        ref = f"{collection_name}/{node_id}"
        with self.transaction():
            # Wirft KnotenFehlt, wenn es ihn nicht gibt — update_node tut das.
            result = self._weich_loeschen(collection_name, node_id, keep_asset)
            for folge in self._kaskade(ref):
                col, _, nid = folge.partition("/")
                self._weich_loeschen(col, nid, True, durch=ref)
        return result

    def _weich_loeschen(self, collection_name, node_id, keep_asset, durch=None):
        felder = {"_deletion_flag": datetime.now(timezone.utc).isoformat(),
                  "_keep_asset": keep_asset}
        if durch is not None:
            felder["_geloescht_durch"] = durch
        result = self.update_node(collection_name, node_id, felder)
        self._log_audit("soft_delete", collection_name, node_id)
        self._melden_knoten("soft_delete", collection_name, node_id)
        return result

    def _kaskade(self, ref):
        """Was ein Loeschen von `ref` mitnimmt: ueber Kanten mit
        Kaskadenloeschen erreichbar, beliebig tief, nur Knoten, die noch
        nicht im Papierkorb liegen. An einem schon geloeschten Knoten endet
        die Kaskade — was hinter ihm liegt, gehoert zu SEINEM Loeschen."""
        return self.traverse(ref, direction="out",
                             kantenfilter=lambda k: k.get("_cascade_delete") is True)

    def restore_node(self, collection_name, node_id):
        """Aus dem Papierkorb zurueckholen (solange der Muellsammler nicht lief).

        Holt auch zurueck, was mit DIESEM Knoten per Kaskade in den Papierkorb
        kam — aber nichts, was unabhaengig davon geloescht war. Holt man
        einen mitgeloeschten Knoten einzeln zurueck, verliert er seine
        Markierung; die anderen bleiben im Papierkorb.
        """
        self._offen_pruefen()
        col_cache = self._cache["nodes"].get(collection_name, {})
        if node_id not in col_cache:
            raise KnotenFehlt(f"Node '{node_id}' does not exist in "
                              f"collection '{collection_name}'.")
        ref = f"{collection_name}/{node_id}"
        with self.transaction():
            self._zurueckholen(collection_name, node_id)
            # Ueber alle Kanten, auch zu Geloeschten: die Mitgeloeschten
            # liegen ja im Papierkorb. Die Markierung entscheidet.
            erreichbar = self.traverse(ref, direction="out", include_deleted=True,
                                       kantenfilter=lambda k: k.get("_cascade_delete") is True)
            for folge in erreichbar:
                col, _, nid = folge.partition("/")
                if self._cache["nodes"].get(col, {}).get(nid, {}).get("_geloescht_durch") == ref:
                    self._zurueckholen(col, nid)
        return True

    def _zurueckholen(self, collection_name, node_id):
        col_cache = self._cache["nodes"][collection_name]
        self._vormerken_knoten(collection_name, node_id)
        self._index_austragen(collection_name, node_id)
        for feld in _INTERNE_KNOTENFELDER:
            col_cache[node_id].pop(feld, None)
        self._index_eintragen(collection_name, node_id)
        self._mark_node_dirty(collection_name, node_id)
        self._persist_collection(collection_name)
        self._log_audit("restore", collection_name, node_id)
        self._melden_knoten("restore_node", collection_name, node_id)

    def verwendungen(self, node_ref, direction="in"):
        """„Wo wird das noch verwendet?“ — fuer einen Loeschdialog.

        Alle Verknuepfungen dieses Knotens, nach Kantenart gruppiert, mit dem
        Knoten am anderen Ende: {kantenart: [(kanten_id, andere_ref), ...]}.
        Vorgabe sind die EINGEHENDEN (wer zeigt hierher); `direction="both"`
        fuer Anwender, die Kanten ungerichtet benutzen. Enden im Papierkorb
        zaehlen nicht — dort wird nichts mehr verwendet.
        """
        if direction not in ("in", "out", "both"):
            raise ValueError("direction muss 'in', 'out' oder 'both' sein.")
        ergebnis = {}
        for richtung in (("in", "out") if direction == "both" else (direction,)):
            index = self._in_index if richtung == "in" else self._out_index
            for art, eimer in index.get(node_ref, {}).items():
                for eid, kante in eimer.items():
                    andere = kante["source"] if richtung == "in" else kante["target"]
                    if self.get_node(andere, readonly=True) is None:
                        continue
                    ergebnis.setdefault(art, []).append((eid, andere))
        return ergebnis

    def loeschfolgen(self, node_ref):
        """„Was verschwindet alles mit, wenn ich das ueberall loesche?“ —
        ohne etwas zu veraendern.

        {"knoten": [...], "kanten": [...]}: die Knoten, die `soft_delete`
        per Kaskade mit in den Papierkorb legt, und die Kanten, die der
        Muellsammler danach mit entfernt (alle an diesem Knoten und an den
        mitgenommenen). Eine duenne Huelle um `traverse` mit Kantenfilter,
        damit niemand das interne Feld `_cascade_delete` kennen muss.
        """
        if self.get_node_raw(node_ref) is None:
            raise KnotenFehlt(f"Node '{node_ref}' does not exist.")
        knoten = self._kaskade(node_ref)
        kanten = []
        gesehen = set()
        for ref in [node_ref] + knoten:
            for index in (self._out_index, self._in_index):
                for eimer in index.get(ref, {}).values():
                    for eid in eimer:
                        if eid not in gesehen:
                            gesehen.add(eid)
                            kanten.append(eid)
        return {"knoten": knoten, "kanten": kanten}

    # ------------------------------------------------------------------
    # OEFFENTLICHE API - EDGES
    # ------------------------------------------------------------------

    def create_edge(self, source_ref, target_ref, rel_type, meta=None, cascade_delete=False):
        """
        Create a directed edge.
        :param cascade_delete: if True the target node is deleted together with the source.
                               Use for: logs belonging to an object, sub-processes of a
                               main process, attached files, etc.
        :return: edge_id
        """
        self._offen_pruefen()
        if self.get_node(source_ref) is None:
            raise ValueError(f"Source node '{source_ref}' does not exist or is soft-deleted.")
        if self.get_node(target_ref) is None:
            raise ValueError(f"Target node '{target_ref}' does not exist or is soft-deleted.")
        self._name_pruefen(rel_type, "Kantenart")
        self._validate_edge(source_ref, target_ref, rel_type)

        edge_id = f"link_{uuid.uuid4().hex[:12]}"
        edge_data = {
            "source":     source_ref,
            "target":     target_ref,
            "type":       rel_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if cascade_delete:
            edge_data["_cascade_delete"] = True
        if meta:
            safe_meta = {k: v for k, v in meta.items() if k not in _RESERVED_EDGE_FIELDS}
            edge_data.update(safe_meta)
        # Vor dem Eintragen: eine Kante, die sich nicht schreiben laesst,
        # blieb vorher in den Aenderungen ihrer Kantenart haengen, und jeder
        # weitere Schreibvorgang dieser Art scheiterte an ihr.
        self._speicherbar_pruefen(edge_data, f"Kante {source_ref} -> {target_ref}")

        self._vormerken_kante(rel_type, edge_id)
        self._cache["edges"].setdefault(rel_type, {})[edge_id] = edge_data
        self._edge_type_index[edge_id] = rel_type
        self._index_edge(edge_id, edge_data)
        self._persist_kanten(rel_type, [edge_id])
        self._melden("create_edge", edge_id, kantenart=rel_type,
                     quelle=source_ref, ziel=target_ref)
        return edge_id

    def get_edge(self, edge_id):
        """Return a copy of the edge with the given ID, or None."""
        rel_type = self._edge_type_index.get(edge_id)
        if rel_type is None:
            return None
        edge = self._cache["edges"].get(rel_type, {}).get(edge_id)
        return copy.deepcopy(edge) if edge is not None else None

    def delete_edge(self, edge_id):
        """Delete an edge permanently (edges have no soft-delete)."""
        self._offen_pruefen()
        rel_type = self._edge_type_index.get(edge_id)
        if not (rel_type and edge_id in self._cache["edges"].get(rel_type, {})):
            raise KanteFehlt(f"Edge '{edge_id}' does not exist.")
        self._vormerken_kante(rel_type, edge_id)
        alt = self._cache["edges"][rel_type][edge_id]
        self._unindex_edge(edge_id, alt)
        del self._cache["edges"][rel_type][edge_id]
        del self._edge_type_index[edge_id]
        self._persist_kanten(rel_type, [edge_id])
        self._melden("delete_edge", edge_id, kantenart=rel_type,
                     quelle=alt["source"], ziel=alt["target"])
        return True

    def list_edges(self, rel_type=None):
        """
        Return all edges, optionally filtered by type.
        Format: {edge_id: edge_data}
        """
        if rel_type is not None:
            return {
                eid: copy.deepcopy(e)
                for eid, e in self._cache["edges"].get(rel_type, {}).items()
            }
        return {
            eid: copy.deepcopy(e)
            for bucket in self._cache["edges"].values()
            for eid, e in bucket.items()
        }

    def get_connected(self, node_ref, direction="out", rel_type=None,
                      target_collection=None, include_deleted=False):
        """
        Find connected node references.
        :param direction: 'out' (outgoing) or 'in' (incoming)
        :param rel_type:  optional filter by relationship type
        :param target_collection: optional filter by target collection
                                  (e.g. 'equipments' returns only refs starting with 'equipments/')
        :param include_deleted: include soft-deleted targets
        :return: Liste von Node-Refs
        """
        # Ueber den Nachbarschaftsindex statt ueber alle Kanten: die Kosten
        # haengen jetzt an der Zahl der Nachbarn DIESES Knotens, nicht an der
        # Gesamtzahl der Kanten. Das ist der Unterschied zwischen einer
        # Graphdatenbank und einer Liste von Kanten.
        index = self._out_index if direction == "out" else self._in_index
        beim_knoten = index.get(node_ref)
        if not beim_knoten:
            return []
        if rel_type is not None:
            kanten = beim_knoten.get(rel_type, {}).values()
        else:
            kanten = [e for eimer in beim_knoten.values() for e in eimer.values()]

        # Die Reihenfolge bleibt die Einfuegereihenfolge und Doppelte bleiben
        # doppelt — beides war vorher so, und ein Aufrufer koennte sich
        # darauf eingerichtet haben.
        results = []
        for edge in kanten:
            target = edge["target"] if direction == "out" else edge["source"]

            if target_collection and not target.startswith(f"{target_collection}/"):
                continue

            if not include_deleted:
                col, _, nid = target.partition("/")
                raw = self._cache["nodes"].get(col, {}).get(nid)
                if raw is None or self._is_deleted(raw):
                    continue

            results.append(target)
        return results

    def get_connected_edges(self, node_ref, direction="out", rel_type=None):
        """
        Like get_connected, but returns the full edge objects (including metadata).
        Format: [(edge_id, edge_data), ...]
        """
        index = self._out_index if direction == "out" else self._in_index
        beim_knoten = index.get(node_ref)
        if not beim_knoten:
            return []
        if rel_type is not None:
            paare = beim_knoten.get(rel_type, {}).items()
        else:
            paare = [(eid, e) for eimer in beim_knoten.values()
                     for eid, e in eimer.items()]
        return [(eid, copy.deepcopy(e)) for eid, e in paare]

    def traverse(self, start_ref, rel_type=None, direction="out",
                 max_depth=None, target_collection=None, include_deleted=False,
                 include_start=False, kantenfilter=None):
        """
        Multi-hop traversal (breadth-first).
        Find all nodes reachable from start_ref via rel_type.

        :param start_ref: starting node
        :param rel_type: filter by relationship type (None = follow all types)
        :param direction: 'out' follows edges forward, 'in' follows backward
        :param max_depth: maximum depth (None = unlimited)
        :param target_collection: optional filter by target collection
        :param include_start: include start node in result (default False)
        :param kantenfilter: Bedingung an die Kante selbst, `kantenfilter(kante)
                        -> bool`; nur Kanten, fuer die sie wahr ist, werden
                        gegangen (etwa „nur Vertraege, die noch laufen“). Die
                        Kante kommt schreibgeschuetzt; verschachtelte Werte
                        darin nicht veraendern.
        :return: list of node refs in BFS order (no duplicates)

        Beispiel:
            # All sub-processes and sub-sub-processes of a main process:
            db.traverse('processes/PROC-MAIN', rel_type='has_subprocess')

            # All equipments needed by any sub-process:
            subs = db.traverse('processes/PROC-MAIN', rel_type='has_subprocess')
            all_procs = [start] + subs
            equipments = set()
            for p in all_procs:
                equipments.update(db.get_connected(p, 'out', 'needs_equipment'))
        """
        visited = {start_ref}
        current_level = [start_ref]
        results = [start_ref] if include_start else []
        depth = 0

        while current_level:
            if max_depth is not None and depth >= max_depth:
                break
            next_level = []
            for node in current_level:
                if kantenfilter is None:
                    connected = self.get_connected(
                        node, direction=direction, rel_type=rel_type,
                        target_collection=target_collection, include_deleted=include_deleted,
                    )
                else:
                    connected = self._nachbarn_gefiltert(
                        node, direction, rel_type, target_collection,
                        include_deleted, kantenfilter)
                for c in connected:
                    if c not in visited:
                        visited.add(c)
                        next_level.append(c)
                        results.append(c)
            current_level = next_level
            depth += 1

        return results

    def _nachbarn_gefiltert(self, node_ref, direction, rel_type, target_collection,
                            include_deleted, kantenfilter):
        """Wie get_connected, aber nur ueber Kanten, die `kantenfilter` erfuellen."""
        index = self._out_index if direction == "out" else self._in_index
        beim_knoten = index.get(node_ref)
        if not beim_knoten:
            return []
        if rel_type is not None:
            kanten = beim_knoten.get(rel_type, {}).values()
        else:
            kanten = [e for eimer in beim_knoten.values() for e in eimer.values()]
        results = []
        for edge in kanten:
            # MappingProxyType statt Kopie: kostet nichts und verhindert,
            # dass ein Filter die Kante im Zwischenspeicher veraendert.
            if not kantenfilter(types.MappingProxyType(edge)):
                continue
            target = edge["target"] if direction == "out" else edge["source"]
            if target_collection and not target.startswith(f"{target_collection}/"):
                continue
            if not include_deleted:
                col, _, nid = target.partition("/")
                raw = self._cache["nodes"].get(col, {}).get(nid)
                if raw is None or self._is_deleted(raw):
                    continue
            results.append(target)
        return results

    def collect_related(self, start_ref, rel_type_path, direction="out"):
        """
        Collect nodes along a chain of different relationship types.
        Useful for mixed traversals like:
        MainProcess --has_subprocess--> SubProcesses --needs_equipment--> Equipments

        :param rel_type_path: list of rel_types to follow per level;
                              the last level provides the results.
        :return: Liste von Node-Refs am Ende der Kette (ohne Duplikate)

        Beispiel:
            # All equipments of all sub-processes (and the main process itself):
            db.collect_related('processes/PROC-MAIN',
                               ['has_subprocess', 'needs_equipment'])
        """
        if not rel_type_path:
            return []

        # Collect all intermediate nodes (first to second-to-last level)
        current = {start_ref}
        for rel_type in rel_type_path[:-1]:
            next_set = set()
            for node in current:
                next_set.update(self.get_connected(node, direction, rel_type))
            # Keep start level too, in case there are direct connections at that level
            current = current | next_set

        # Final level: collect target nodes
        results = set()
        final_rel = rel_type_path[-1]
        for node in current:
            results.update(self.get_connected(node, direction, final_rel))
        return sorted(results)


# =============================================================================
# MAINTENANCE MODULE: GARBAGE COLLECTOR
# =============================================================================

    # Bis 3.0.0 stand der Muellsammler in einer eigenen Unterklasse
    # `MaintenanceEngine`, die als ZWEITE Instanz neben der laufenden
    # geoeffnet wurde. Seit eine Instanz je Bestand gilt, gehoert er zu
    # jeder Instanz; die Methoden unten sind Teil von FlatGraphDB (die
    # Zeilen dazwischen sind nur Kommentar).
    #
    # Idempotent: darf nach einem Abbruch jederzeit neu laufen.
    #   A - Scanner:  Knoten mit _deletion_flag finden
    #   B - Cascader: zugehoerige Kanten entfernen
    #   C - Purger:   Anhaenge archivieren, Knoten endgueltig loeschen

    def run_garbage_collection(self, verbose=False):
        """
        Run the full garbage collection cycle.
        :return: statistics dict
        """
        # Vor der ersten Aenderung, nicht erst beim ersten Schreiben: der
        # Muellsammler verschiebt auch Anhaenge, und das soll eine
        # geschlossene Instanz gar nicht erst anfangen.
        self._offen_pruefen()
        if self._transaction_depth > 0:
            raise NichtInTransaktion(
                "run_garbage_collection laeuft nicht in einer Transaktion: es "
                "verschiebt Anhaenge und schreibt sofort, ein Rollback koennte "
                "das nicht zuruecknehmen.")
        stats = {"scanned": 0, "edges_removed": 0, "nodes_purged": 0,
                 "assets_archived": 0, "assets_kept": 0, "vault_text_orphans": 0,
                 "faecher_verdichtet": 0}

        # --- PHASE A: Scanner ---
        to_delete = self._scan_for_deletions()
        stats["scanned"] = len(to_delete)

        if not to_delete:
            if verbose:
                print("[GC] No objects marked for deletion.")
            stats["vault_text_orphans"] = self._collect_vault_text_orphans(verbose)
            self._write_maintenance_log(stats)
            return stats

        if verbose:
            print(f"[GC] {len(to_delete)} object(s) marked for deletion.")

        # --- PHASE B: Cascader ---
        refs_to_delete = {f"{col}/{nid}" for col, nid, _ in to_delete}
        stats["edges_removed"] = self._cascade_delete_edges(refs_to_delete)

        if verbose:
            print(f"[GC] {stats['edges_removed']} dangling edge(s) removed.")

        # --- PHASE C: Purger ---
        for col, nid, node_data in to_delete:
            archived = self._purge_node(col, nid, node_data)
            stats["nodes_purged"] += 1
            if archived is True:
                stats["assets_archived"] += 1
            elif archived is False:
                stats["assets_kept"] += 1

        if verbose:
            print(f"[GC] {stats['nodes_purged']} node(s) permanently deleted.")
            print(f"[GC] {stats['assets_archived']} asset(s) archived, {stats['assets_kept']} kept.")

        # Die geloeschten Knoten von der Platte nehmen
        purged_collections = {col for col, _, _ in to_delete}
        for col in purged_collections:
            self._persist_collection_full(col)

        # Die Luecken, die das Loeschen gerade gerissen hat, wieder schliessen.
        stats["faecher_verdichtet"] = self._verdichten()


        # vault_text orphan cleanup: remove .txt files with no live node reference
        stats["vault_text_orphans"] = self._collect_vault_text_orphans(verbose)

        self._write_maintenance_log(stats)
        return stats

    def _verdichten(self):
        """Duenne Faecher zusammenlegen. Gibt die Zahl der aufgeloesten zurueck.

        Duenn heisst: hoechstens halb voll. Erst ab zwei duennen Faechern in
        einer Ablage lohnt es sich. Ihr Inhalt wandert in NEUE Faecher hinter
        allen bestehenden; erst wenn die geschrieben sind, werden die alten
        geleert und geloescht. Bricht es dazwischen ab, steht ein Eintrag in
        zwei Faechern mit gleichem Inhalt — das repariert das naechste
        Oeffnen (_ablage_laden). Umgekehrt waere er zwischendurch nirgends.
        """
        aufgeloest = 0
        for art in ("nodes", "edges"):
            for name, ablage in list(self._ablagen[art].items()):
                daten = self._cache[art].get(name, {})
                duenn = sorted(nr for nr, m in ablage.inhalt.items()
                               if 0 < len(m) <= FACH_GROESSE // 2)
                if len(duenn) < 2:
                    continue
                umzug = sorted(k for nr in duenn for k in ablage.inhalt[nr])
                erstes = max(ablage.inhalt) + 1
                neue = set()
                for i, k in enumerate(umzug):
                    ablage.entfernen(k)
                    nr = erstes + i // FACH_GROESSE
                    ablage.eintragen(k, nr)
                    neue.add(nr)
                self._faecher_schreiben(ablage, neue, daten)
                self._faecher_schreiben(ablage, set(duenn), daten)
                aufgeloest += len(duenn)
        return aufgeloest

    def _collect_vault_text_orphans(self, verbose=False):
        """vault_text-Dateien entfernen, auf die kein Knoten mehr zeigt.

        Ein Knoten im Papierkorb zaehlt mit: sein Text geht erst, wenn er
        selbst endgueltig geht. Bis 3.0.0 zaehlte er nicht, und wer einen
        Knoten nach einem Lauf des Muellsammlers zurueckholte, fand statt
        seines Texts den Verweis auf eine Datei, die es nicht mehr gab.
        """
        vt_dir = self.dirs["vault_text"]
        if not os.path.isdir(vt_dir):
            return 0
        # Build set of all @vault_text/ references currently in the cache
        live_refs = set()
        prefix = FlatGraphDB._VAULT_TEXT_PREFIX
        for col_data in self._cache["nodes"].values():
            for node_data in col_data.values():
                for value in node_data.values():
                    if isinstance(value, str) and value.startswith(prefix):
                        rel = value[len(prefix):]
                        live_refs.add(os.path.normpath(os.path.join(self.root, rel)))
        removed = 0
        for filename in os.listdir(vt_dir):
            # .tmp: von einem Schreiben, das abbrach; auf sie zeigt nie ein Knoten.
            if not filename.endswith((".txt", ".tmp")):
                continue
            filepath = os.path.normpath(os.path.join(vt_dir, filename))
            if filepath not in live_refs:
                os.remove(filepath)
                removed += 1
        if verbose and removed:
            print(f"[GC] {removed} vault_text orphan(s) removed.")
        return removed

    # ------------------------------------------------------------------
    # Interne Phasen
    # ------------------------------------------------------------------

    def _scan_for_deletions(self):
        """
        Phase A: find all nodes with _deletion_flag.
        Expand the set with cascade_delete targets (transitive closure).
        """
        # Step 1: find directly flagged nodes
        direct = []
        for col_name, col_data in self._cache["nodes"].items():
            for node_id, data in col_data.items():
                if self._is_deleted(data):
                    direct.append((col_name, node_id, data))

        if not direct:
            return []

        # Step 2: cascade expansion — follow all cascade_delete edges recursively.
        # Snapshot taken once before the loop: edges never change during expansion
        # (only node _deletion_flag is written), so one pass through the snapshot
        # is sufficient per outer iteration.
        # All affected collections are flushed only after the full expansion so
        # the on-disk state is never partially consistent mid-GC.
        # Seit soft_delete die Kaskade selbst mitnimmt, findet der Muellsammler
        # hier nur noch Reste: Bestaende aus der Zeit davor, oder ein Knoten,
        # der geloescht wurde, bevor seine Kaskadenkante entstand. Auch die
        # loescht er NICHT in diesem Lauf, sondern legt sie in den Papierkorb
        # (mit _geloescht_durch) — endgueltig weg sind sie erst beim
        # naechsten Lauf. Nichts verschwindet, ohne im Papierkorb gewesen zu
        # sein.
        to_delete_refs = {f"{col}/{nid}" for col, nid, _ in direct}
        dirty_collections = set()
        edges_snapshot = [e for b in self._cache["edges"].values() for e in b.values()]

        changed = True
        while changed:
            changed = False
            for edge in edges_snapshot:
                if not edge.get("_cascade_delete"):
                    continue
                if edge["source"] in to_delete_refs and edge["target"] not in to_delete_refs:
                    to_delete_refs.add(edge["target"])
                    try:
                        col, nid = edge["target"].split("/", 1)
                        target_node = self._cache["nodes"].get(col, {}).get(nid)
                        if target_node and not self._is_deleted(target_node):
                            target_node["_deletion_flag"] = datetime.now(timezone.utc).isoformat()
                            target_node.setdefault("_keep_asset", True)
                            target_node["_geloescht_durch"] = edge["source"]
                            self._mark_node_dirty(col, nid)
                            dirty_collections.add(col)
                    except ValueError:
                        pass
                    changed = True

        # Expansion done — flush all marked collections to disk
        for col in dirty_collections:
            # Die Kaskade setzt Loeschmarken direkt, ohne update_node — der
            # Feldindex dieser Sammlungen wird deshalb verworfen statt
            # nachgefuehrt. Seltener Weg, Neuaufbau kostet Millisekunden.
            self._mark_index_dirty(col)
            self._persist_collection(col)

        # Endgueltig geloescht wird nur, was VOR diesem Lauf im Papierkorb lag.
        return direct

    def _cascade_delete_edges(self, refs_to_delete):
        """Phase B: remove all edges pointing to or from nodes being deleted."""
        dirty_types = {}
        count = 0
        for rel_type, bucket in self._cache["edges"].items():
            to_remove = [
                eid for eid, e in bucket.items()
                if e["source"] in refs_to_delete or e["target"] in refs_to_delete
            ]
            for eid in to_remove:
                self._unindex_edge(eid, bucket[eid])
                del bucket[eid]
                self._edge_type_index.pop(eid, None)
                dirty_types.setdefault(rel_type, set()).add(eid)
                count += 1
        for rel_type, eids in dirty_types.items():
            self._persist_kanten(rel_type, eids)
        return count

    def _purge_node(self, collection_name, node_id, node_data):
        """
        Phase C: handle asset archiving and permanently delete the node.
        :return: True if asset archived, False if kept, None if no asset
        """
        keep_asset = node_data.get("_keep_asset", True)
        asset_path = node_data.get("datei")  # convention: 'datei' field points to a vault-relative path
        asset_status = None

        if asset_path:
            vault_real = os.path.realpath(self.dirs["vault"])
            candidate = os.path.realpath(os.path.join(self.root, asset_path))
            # path outside vault is ignored (prevents path traversal)
            if candidate.startswith(vault_real + os.sep) and os.path.exists(candidate):
                if keep_asset:
                    # file stays in vault (becomes an orphan)
                    asset_status = False
                else:
                    # move to vault_archive (safety net)
                    target_path = os.path.join(
                        self.dirs["vault_archive"],
                        os.path.basename(candidate)
                    )
                    # avoid name collision in archive
                    if os.path.exists(target_path):
                        base, ext = os.path.splitext(target_path)
                        target_path = f"{base}_{uuid.uuid4().hex[:6]}{ext}"
                    shutil.move(candidate, target_path)
                    asset_status = True

        # remove node permanently (LAST step → idempotency)
        del self._cache["nodes"][collection_name][node_id]
        self._purged_nodes.setdefault(collection_name, set()).add(node_id)
        self._dirty_nodes.pop(collection_name, None)  # no temp-write for deleted nodes
        self._mark_index_dirty(collection_name)
        return asset_status

    def _write_maintenance_log(self, stats):
        """Append an entry to the maintenance log."""
        log_path = os.path.join(self.root, "datenbank", "maintenance.log")
        timestamp = datetime.now(timezone.utc).isoformat()
        line = f"[{timestamp}] {json.dumps(stats, ensure_ascii=False)}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)


# =============================================================================
# THREADSICHERHEIT
# =============================================================================
# Jede oeffentliche Methode laeuft unter der Sperre der Instanz. Bis 3.0.0
# gab es keine; im Vertrag stand „keine Luecke, sondern eine Entscheidung“,
# und jeder Anwender musste selbst serialisieren. pDMS tat es erst, nachdem
# ein Lasttest mit drei Threads 1195 Fehler ergab, darunter halb
# geschriebenes JSON (Commit 31dc756, 09.09.2026).
#
# Hier in einer Schleife statt als Dekorator an jeder Methode: eine neue
# oeffentliche Methode ist damit geschuetzt, ohne dass jemand daran denken
# muss. Ausgenommen ist nur `transaction`, die die Sperre ueber ihren
# ganzen Block selbst haelt.

def _gesperrt(methode):
    @functools.wraps(methode)
    def unter_sperre(self, *args, **kwargs):
        with self._sperre:
            return methode(self, *args, **kwargs)
    unter_sperre._unter_sperre = True
    return unter_sperre


for _name, _methode in list(vars(FlatGraphDB).items()):
    if (not _name.startswith("_") and _name != "transaction"
            and callable(_methode)):
        setattr(FlatGraphDB, _name, _gesperrt(_methode))
del _name, _methode


class MaintenanceEngine(FlatGraphDB):
    """Frueherer Name fuer eine Instanz mit Muellsammler.

    Bleibt, damit `from flatgraph import MaintenanceEngine` und
    `MaintenanceEngine(wurzel).run_garbage_collection()` weiter
    funktionieren — aber nur als EINZIGE Instanz des Bestands. Neben einer
    offenen FlatGraphDB bekommt sie `BestandBelegt`; dort ist
    `db.run_garbage_collection()` der Weg.
    """
