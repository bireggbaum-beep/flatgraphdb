"""
Beispielbestand: Fertigung eines elektrochemischen Teststreifens (Medizintechnik).

    python editor/beispiel.py --bestand editor/bestand_beispiel

Vier Teilprozesse an vier Standorten, so wie sie in der Praxis auseinanderliegen:
Chemlabor (Reagenzansatz) -> Linie 1 (Siebdruck Ag/Carbon, Dispensieren,
Trocknungen) -> Montage & Konfektion (andere Plant, anderer Raum) -> QC.

Die Beziehungen folgen dem Regelwerk des Datenmodells (22 Paare in `modell.json`, Referenz `datenmodell.pdf`); absichtlich
fehlen einzelne Dinge, damit Lücken sichtbar werden: Equipment ohne
Qualifizierungsdokument, Pflichtfelder, ein Parameter ohne Equipment.
Alles läuft durch dieselbe Prüfung wie im Editor (Typen, Auswahlwerte, Arten).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from editor import Editor, Modell, HIER  # noqa: E402

STD, GATE, WARTE, LOOP = ("Standard-Fertigungsschritt", "Gate / In-Process Control (IPC)",
                          "Wartezeit / Haltezeit", "Schleifen-Knoten (Loop)")


def bauen(ed):
    O = {}

    def o(kurz, typ, name, **f):
        O[kurz] = ed.knoten_anlegen(typ, name, f or None)[0]

    def v(a, art, b, **attr):
        # Art und Richtung ergeben sich aus dem Regelwerk des Modells; die Art
        # steht nur zur Lesbarkeit da und wird geprüft
        ed.verknuepfe(O[a], O[b], art, "aus", felder=attr or None)

    # ---- Plants (Plant führt aus Prozess; ihre Räume ergeben sich über die Equipments) ----
    for k, n, i, t in [("pl1", "Sensorfertigung Linie 1", "PL-101", "Fertigungslinie"),
                       ("plm", "Montage und Konfektion", "PL-102", "Fertigungslinie"),
                       ("plc", "Chemlabor Materialvorbereitung", "PL-201", "Funktionsbereich (zB. Labor, QC)"),
                       ("plq", "QC-Labor", "PL-202", "Funktionsbereich (zB. Labor, QC)")]:
        o(k, "Plant", n, PlantID=i, PlantType=t, PlantGXP="GXP")
    for k, n, i, kl in [("rsd", "Siebdruckraum", "R-101", "ISO 8"), ("rdp", "Dispensierraum", "R-102", "ISO 7"),
                        ("rtr", "Trockenraum", "R-103", "ISO 8"), ("rmo", "Montageraum", "R-201", "ISO 7"),
                        ("rko", "Konfektionierraum", "R-202", "ISO 8"), ("rca", "Ansatzraum Chemlabor", "R-301", "ISO 8"),
                        ("rcl", "Reagenzlager Chemlabor", "R-302", "ISO 9"), ("rql", "QC-Messlabor", "R-401", "ISO 9"),
                        ("rqk", "QC-Klimaraum", "R-402", "ISO 9")]:
        o(k, "Raum", n, RoomID=i, RoomClass=kl)

    # ---- Medien ----------------------------------------------------------
    for k, n, i, t, c in [("druckluft", "Druckluft", "M-01", "Process Gases & Compressed Air", "Indirect Impact"),
                          ("stickstoff", "Stickstoff", "M-02", "Process Gases & Compressed Air", "Direct Impact"),
                          ("reinstw", "Reinstwasser", "M-03", "Water Systems", "Direct Impact"),
                          ("hvac7", "Reinraumluft ISO 7", "M-04", "HVAC / Cleanroom Air", "Direct Impact"),
                          ("hvac8", "Reinraumluft ISO 8", "M-05", "HVAC / Cleanroom Air", "Indirect Impact")]:
        o(k, "Medium", n, MediumID=i, MediumType=t, MediumGXP="GXP", MediumProductContact=c)

    # ---- Equipment (ist Teil von Plant, steht in Raum — wie in SAP) -------
    eq = [("sd01", "Siebdruckanlage SD-01", "EQ-101", "Kat 3", "pl1", "rsd"), ("oi01", "Optische Inline-Inspektion OI-01", "EQ-102", "Kat 3", "pl1", "rsd"),
          ("tr01", "Durchlauftrockner TR-01", "EQ-103", "Kat 3", "pl1", "rtr"), ("tr02", "Vakuumtrockner TR-02", "EQ-104", "Kat 3", "pl1", "rtr"),
          ("dp01", "Dispensierroboter DP-01", "EQ-105", "Kat 3", "pl1", "rdp"),
          ("ma01", "Laminierautomat MA-01", "EQ-201", "Kat 3", "plm", "rmo"), ("sn01", "Stanz-/Schneidstation SN-01", "EQ-202", "Kat 3", "plm", "rmo"),
          ("vp01", "Verpackungsmaschine VP-01", "EQ-203", "Kat 3", "plm", "rko"),
          ("w01", "Analysenwaage W-01", "EQ-301", "Kat 2", "plc", "rca"), ("rw01", "Dissolver RW-01", "EQ-302", "Kat 3", "plc", "rca"),
          ("vk01", "Viskosimeter VK-01", "EQ-303", "Kat 2", "plc", "rca"), ("ks01", "Kühlschrank Reagenz KS-01", "EQ-304", "Kat 2", "plc", "rcl"),
          ("ps01", "Potentiostat PS-01", "EQ-401", "Kat 3", "plq", "rql"), ("ms01", "Mikroskop MS-01", "EQ-402", "Kat 2", "plq", "rql"),
          ("kl01", "Klimaschrank Stabilität KL-01", "EQ-403", "Kat 3", "plq", "rqk")]
    for k, n, i, kat, pl, raum in eq:
        o(k, "Equipment", n, EquipmentID=i, EquipmentGXP="GXP", EquipmentCategory=kat)
        v(k, "ist Teil von", pl)
        v(k, "steht in", raum)
    for e_, m_ in [("sd01", "druckluft"), ("dp01", "druckluft"), ("tr02", "stickstoff"), ("rw01", "reinstw"), ("ma01", "druckluft"), ("sn01", "druckluft")]:
        v(e_, "verwendet", m_)

    # ---- Software (steuert Equipment; unterstützt Plant/Prozess/Schritt) --
    for k, n, i, t, sc, kat in [("sps_sd", "SPS Siebdruckanlage", "SW-01", "Embedded / SPS-Software", "Equipment-spezifisch (SPS, Firmware)", "Kat 4"),
                                ("sps_tr", "SPS Trockner", "SW-02", "Embedded / SPS-Software", "Equipment-spezifisch (SPS, Firmware)", "Kat 4"),
                                ("sw_dp", "Dispenser-Steuerung", "SW-03", "Embedded / SPS-Software", "Equipment-spezifisch (SPS, Firmware)", "Kat 4"),
                                ("sw_ma", "Steuerung Laminierautomat", "SW-04", "Embedded / SPS-Software", "Equipment-spezifisch (SPS, Firmware)", "Kat 4"),
                                ("ebr", "eBR Elektronisches Batch Record", "SW-10", "System- / Prozess-Software", "Prozess- / Linienbezogen (eBR, Auswertetools)", "Kat 4"),
                                ("sw_ps", "Potentiostat-Auswertung", "SW-11", "System- / Prozess-Software", "Prozess- / Linienbezogen (eBR, Auswertetools)", "Kat 4"),
                                ("mes", "MES", "SW-20", "Enterprise-Software", "Plant-weit / Enterprise (MES, ERP, LIMS, QMS)", "Kat 4"),
                                ("lims", "LIMS", "SW-21", "Enterprise-Software", "Plant-weit / Enterprise (MES, ERP, LIMS, QMS)", "Kat 4")]:
        o(k, "Software", n, SoftwareID=i, SoftwareGXP="GXP", SoftwareCategory=kat, SoftwareType=t, SoftwareScope=sc)
    for sw, e_ in [("sps_sd", "sd01"), ("sps_tr", "tr01"), ("sps_tr", "tr02"), ("sw_dp", "dp01"), ("sw_ma", "ma01"), ("sw_ps", "ps01")]:
        v(sw, "steuert", e_)

    # ---- Prozesse (Plant führt aus Prozess) ------------------------------
    for k, n, i, pl in [("p_chem", "Materialvorbereitung Reagenz", "PR-110", "plc"), ("p_elek", "Elektrodenfertigung (Siebdruck und Dispensieren)", "PR-120", "pl1"),
                        ("p_mont", "Montage und Konfektionierung", "PR-130", "plm"), ("p_qc", "QC-Prüfung und Chargenfreigabe", "PR-140", "plq")]:
        o(k, "Prozess", n, ProcessID=i)
        v(pl, "führt aus", k)
    for sw, tg in [("mes", "pl1"), ("mes", "plm"), ("lims", "plq"), ("lims", "plc"), ("ebr", "p_elek"), ("ebr", "p_chem"), ("ebr", "p_mont"),
                   ("sw_ps", "p_qc"), ("lims", "p_qc"), ("mes", "p_elek"), ("ebr", "s_ag")]:
        if tg in O:
            v(sw, "unterstützt", tg)
    pending_sw = [("ebr", "s_ag"), ("ebr", "s_disp"), ("sw_ps", "s_mess")]

    # ---- Prozessschritte: (kurz, Name, Prozess, Equipment) ----------------
    st = [("s_waege", "Wägen Rohstoffe", "p_chem", "w01"), ("s_ansatz", "Ansetzen Reagenzlösung", "p_chem", "rw01"),
          ("s_visk", "Viskositätsprüfung (IPC)", "p_chem", "vk01"), ("s_kuehl", "Kühllagerung Reagenz", "p_chem", "ks01"),
          ("s_ag", "Siebdruck Silber (Ag)", "p_elek", "sd01"), ("s_trag", "Trocknung Ag", "p_elek", "tr01"),
          ("s_carb", "Siebdruck Carbon", "p_elek", "sd01"), ("s_trcarb", "Trocknung Carbon", "p_elek", "tr01"),
          ("s_ipc", "Optische Inline-Kontrolle (IPC)", "p_elek", "oi01"), ("s_disp", "Dispensieren Reagenz", "p_elek", "dp01"),
          ("s_vak", "Vakuumtrocknung Reagenz", "p_elek", "tr02"),
          ("s_lam", "Laminieren", "p_mont", "ma01"), ("s_schn", "Schneiden Streifen", "p_mont", "sn01"),
          ("s_kon", "Konfektionieren und Verpacken", "p_mont", "vp01"),
          ("s_sicht", "Sichtprüfung", "p_qc", "ms01"), ("s_mess", "Elektrochemische Messung", "p_qc", "ps01"),
          ("s_stab", "Stabilitätslagerung", "p_qc", "kl01"), ("s_frei", "Chargenfreigabe", "p_qc", None)]
    for i, (k, n, p, e_) in enumerate(st, 1):
        o(k, "Prozessschritt", n, ProcessstepID=f"PS-{i:03d}")
        v(k, "ist Teil von", p)
        if e_:
            v(e_, "führt aus", k)
    for sw, s_ in pending_sw:
        v(sw, "unterstützt", s_)
    for s_, m_ in [("s_ag", "hvac8"), ("s_trag", "hvac8"), ("s_trcarb", "hvac8"), ("s_disp", "hvac7"), ("s_vak", "stickstoff"), ("s_ansatz", "reinstw"), ("s_lam", "hvac7")]:
        v(s_, "verwendet", m_)
    folge = [("s_waege", "s_ansatz", STD), ("s_ansatz", "s_visk", STD), ("s_visk", "s_kuehl", GATE), ("s_kuehl", "s_disp", WARTE),
             ("s_ag", "s_trag", STD), ("s_trag", "s_carb", STD), ("s_carb", "s_trcarb", STD), ("s_trcarb", "s_ipc", STD),
             ("s_ipc", "s_disp", GATE), ("s_ipc", "s_ag", LOOP), ("s_disp", "s_vak", STD), ("s_vak", "s_lam", STD),
             ("s_lam", "s_schn", STD), ("s_schn", "s_kon", STD), ("s_kon", "s_sicht", STD), ("s_sicht", "s_mess", STD),
             ("s_mess", "s_stab", STD), ("s_stab", "s_frei", WARTE)]
    for a, b, t in folge:
        v(a, "führt zu Nachfolger (0:n)", b, ProcessstepType=t)

    # ---- Material (Input/Output am Schritt) ------------------------------
    mat = [("m_ag", "Silberpaste Ag/AgCl", "Raw Material"), ("m_carb", "Carbonpaste", "Raw Material"), ("m_enz", "Glucoseoxidase (Enzym)", "Raw Material"),
           ("m_med", "Mediator Ferricyanid", "Raw Material"), ("m_puf", "Pufferlösung", "Raw Material"),
           ("m_pet", "PET-Trägerfolie", "Component / Purchased Part"), ("m_abd", "Abdeckfolie", "Component / Purchased Part"),
           ("m_kleb", "Klebeband doppelseitig", "Component / Purchased Part"), ("m_dose", "Dose mit Trockenmittel", "Component / Purchased Part"),
           ("m_gebr", "Gebrauchsanweisung", "Component / Purchased Part"),
           ("m_reag", "Reagenzlösung (Ansatz)", "Work in Progress (WIP) / Intermediate Product"), ("m_foag", "Folie mit Ag-Elektrode", "Work in Progress (WIP) / Intermediate Product"),
           ("m_focarb", "Folie mit Ag/Carbon-Elektrode", "Work in Progress (WIP) / Intermediate Product"), ("m_foreag", "Folie mit getrocknetem Reagenz", "Work in Progress (WIP) / Intermediate Product"),
           ("m_bahn", "Laminierte Bahn", "Sub-Assembly"), ("m_strip", "Teststreifen lose", "Sub-Assembly"),
           ("m_pack", "Teststreifen verpackt (Dose à 50)", "Finished Good"), ("m_dev", "Blutzucker-Teststreifen Typ X", "Finished Medical Device")]
    for i, (k, n, t) in enumerate(mat, 1):
        o(k, "Material", n, MaterialID=f"MAT-{i:03d}", MaterialType=t)
    ein = [("s_waege", ["m_enz", "m_med", "m_puf"]), ("s_ansatz", ["m_enz", "m_med", "m_puf"]), ("s_ag", ["m_ag", "m_pet"]), ("s_carb", ["m_carb", "m_foag"]),
           ("s_disp", ["m_reag", "m_focarb"]), ("s_lam", ["m_foreag", "m_abd", "m_kleb"]), ("s_schn", ["m_bahn"]), ("s_kon", ["m_strip", "m_dose", "m_gebr"]), ("s_frei", ["m_pack"])]
    aus = [("s_ansatz", ["m_reag"]), ("s_ag", ["m_foag"]), ("s_carb", ["m_focarb"]), ("s_vak", ["m_foreag"]), ("s_lam", ["m_bahn"]),
           ("s_schn", ["m_strip"]), ("s_kon", ["m_pack"]), ("s_frei", ["m_dev"])]
    for s_, ms in ein:
        for m_ in ms:
            v(s_, "benötigt Input", m_)
    for s_, ms in aus:
        for m_ in ms:
            v(s_, "erzeugt Output", m_)

    # ---- Parameter: (kurz, Name, Schritt, Klasse, Soll, Bereich, Einheit, Regelung, Equipment überwacht/regelt) ----
    par = [("a_rak", "Rakeldruck Ag", "s_ag", "CPP", 2.0, "1.8–2.2", "bar", "Closed Loop", "sd01"),
           ("a_nass", "Nassschichtmasse Ag", "s_ag", "CPP", 120, "110–130", "mg", "Logged", "sd01"),
           ("a_tag", "Temperatur Trocknung Ag", "s_trag", "CPP", 120, "115–125", "°C", "Closed Loop", "tr01"),
           ("a_zag", "Zeit Trocknung Ag", "s_trag", "CPP", 10, "9–11", "min", "Logged", "tr01"),
           ("a_rf", "Raumfeuchte Trockenraum", "s_trag", "PP", 30, "20–40", "%rH", "Logged", None),
           ("a_rakc", "Rakeldruck Carbon", "s_carb", "CPP", 1.8, "1.6–2.0", "bar", "Closed Loop", "sd01"),
           ("a_tc", "Temperatur Trocknung Carbon", "s_trcarb", "CPP", 100, "95–105", "°C", "Closed Loop", "tr01"),
           ("a_zc", "Zeit Trocknung Carbon", "s_trcarb", "CPP", 15, "14–16", "min", "Logged", "tr01"),
           ("a_dos", "Dosiermasse Reagenz", "s_disp", "CPP", 50, "47–53", "mg", "Closed Loop", "dp01"),
           ("a_dd", "Dispensierdruck", "s_disp", "PP", 0.5, "0.4–0.6", "bar", "Closed Loop", "dp01"),
           ("a_vak", "Vakuum Reagenztrocknung", "s_vak", "CPP", 0.2, "0.15–0.25", "bar", "Closed Loop", "tr02"),
           ("a_tv", "Temperatur Vakuumtrocknung", "s_vak", "CPP", 35, "33–37", "°C", "Closed Loop", "tr02"),
           ("a_zv", "Zeit Vakuumtrocknung", "s_vak", "CPP", 60, "55–65", "min", "Logged", "tr02"),
           ("c_ein", "Einwaage Enzym", "s_waege", "CPP", 120, "118–122", "g", "Logged", "w01"),
           ("c_rw", "Rührgeschwindigkeit Ansatz", "s_ansatz", "PP", 500, "450–550", "rpm", "Closed Loop", "rw01"),
           ("c_t", "Ansatztemperatur", "s_ansatz", "PP", 20, "18–22", "°C", "Logged", "rw01"),
           ("c_lag", "Lagertemperatur Reagenz", "s_kuehl", "CPP", 5, "2–8", "°C", "Logged", "ks01"),
           ("c_std", "Standzeit Reagenz", "s_kuehl", "CPP", 24, "max. 24", "h", "Logged", None),
           ("m_lp", "Laminierdruck", "s_lam", "CPP", 3.0, "2.7–3.3", "bar", "Closed Loop", "ma01"),
           ("m_siegel", "Siegeltemperatur", "s_lam", "CPP", 140, "135–145", "°C", "Closed Loop", "ma01"),
           ("m_schn", "Anpressdruck Schneiden", "s_schn", "PP", 4.0, "3.5–4.5", "bar", "Logged", "sn01"),
           ("q_t", "Messtemperatur QC", "s_mess", "PP", 25, "23–27", "°C", "Logged", "ps01"),
           ("q_lag", "Lagertemperatur Stabilität", "s_stab", "CPP", 25, "23–27", "°C", "Logged", "kl01"),
           ("q_rf", "Feuchte Stabilität", "s_stab", "CPP", 60, "55–65", "%rH", "Logged", "kl01")]
    for i, (k, n, s_, kl, soll, ber, ein_, reg, ue) in enumerate(par, 1):
        o(k, "Parameter", n, ParameterID=f"PAR-{i:03d}", ParameterClass=kl, ParameterTargetValue=soll,
          ParameterAcceptanceRange=ber, ParameterUnit=ein_, ParameterControlType=reg)
        v(s_, "definiert", k)
        if ue:
            v(ue, "überwacht/regelt", k)

    # ---- Dokumente (gehört zu Equipment, Prozess, Schritt, Plant, Raum, Software) ----
    dok = [("d_spag", "Spezifikation Silberpaste", "Specification", "SAP P1U", ["s_ag"]),
           ("d_spcarb", "Spezifikation Carbonpaste", "Specification", "SAP P1U", ["s_carb"]),
           ("d_spreag", "Spezifikation Reagenzlösung", "Specification", "SAP P1U", ["s_ansatz", "s_visk"]),
           ("d_spdev", "Spezifikation Teststreifen", "Specification", "SAP P1U", ["p_qc", "s_frei"]),
           ("d_aasd", "Arbeitsanweisung Siebdruck", "Work Instruction", "DiaDoc", ["s_ag", "s_carb"]),
           ("d_aatr", "Arbeitsanweisung Trocknung", "Work Instruction", "DiaDoc", ["s_trag", "s_trcarb", "s_vak"]),
           ("d_aadp", "Arbeitsanweisung Dispensieren", "Work Instruction", "DiaDoc", ["s_disp"]),
           ("d_aaans", "Arbeitsanweisung Reagenzansatz", "Work Instruction", "DiaDoc", ["s_ansatz", "s_waege"]),
           ("d_aalam", "Arbeitsanweisung Laminieren", "Work Instruction", "DiaDoc", ["s_lam", "s_schn"]),
           ("d_aasi", "Arbeitsanweisung Sichtprüfung", "Work Instruction", "DiaDoc", ["s_sicht"]),
           ("d_brel", "Batch Record Elektrodenfertigung", "Batch Record", "SAP P1U", ["p_elek"]),
           ("d_brch", "Batch Record Reagenzansatz", "Batch Record", "SAP P1U", ["p_chem"]),
           ("d_brmo", "Batch Record Montage", "Batch Record", "SAP P1U", ["p_mont"]),
           ("d_brqc", "Batch Record QC", "Batch Record", "SAP P1U", ["p_qc"]),
           ("d_pqtr", "PQ Trocknungsprozess Ag/Carbon", "Validation Documentation", "SAP P1U", ["s_trag", "s_trcarb"]),
           ("d_pqdp", "PQ Dispensierprozess", "Validation Documentation", "SAP P1U", ["s_disp"]),
           ("d_iqsd", "IQ/OQ Siebdruckanlage", "Validation Documentation", "SAP P1U", ["sd01"]),
           ("d_iqtr", "IQ/OQ Durchlauftrockner", "Validation Documentation", "SAP P1U", ["tr01"]),
           ("d_iqdp", "IQ/OQ Dispensierroboter", "Validation Documentation", "SAP P1U", ["dp01"]),
           ("d_iqma", "IQ/OQ Laminierautomat", "Validation Documentation", "SAP P1U", ["ma01"]),
           ("d_iqps", "IQ/OQ Potentiostat", "Validation Documentation", "SAP P1U", ["ps01"]),
           ("d_csv", "CSV-Bericht MES", "Validation Documentation", "SAP P1U", ["mes"]),
           ("d_gsp", "GSP Reinraumverhalten", "GSP", "DiaDoc", ["rsd", "rdp", "rtr", "rmo"]),
           ("d_plan", "Qualifizierungsplan Linie 1", "Validation Documentation", "SAP P1U", ["pl1"]),
           ("d_ruest", "Formular Rüstprotokoll", "Form", "DiaDoc", [])]
    for i, (k, n, t, dms, bez) in enumerate(dok, 1):
        o(k, "Dokument", n, DocumentID=f"DOC-{i:03d}", DocumentDMS=dms, DocumentType=t)
        for ziel in bez:
            v(k, "gehört zu", ziel)

    o2_analysator(O, o, v)

    # ---- Absichtliche Lücken (für die Qualitätsfilter) -------------------
    # keine IQ/OQ für: Vakuumtrockner, Schneidstation, Verpackung, Waage, Dissolver,
    # Viskosimeter, Kühlschrank, Mikroskop, Klimaschrank, Inline-Inspektion;
    # Parameter ohne Equipment: Raumfeuchte, Standzeit
    ed.knoten_aendern(O["ms01"], felder={"EquipmentCategory": ""})
    return O


def o2_analysator(O, o, v):
    """Zweites Produkt: Blutgasanalysator aus drei Komponenten. Der O2-Sensor
    entsteht an der O2-Linie aus einem Zukauf-Gehäuse (BG Oberteil) und einer
    Sensorlösung aus dem Chemlab; die Lösung kommt aus einem Pool, aus dem auch
    die Enzymlösung für den Glukose-Sensor entsteht. Nach der Herstellung zwei
    Pfade: eine Probe wird an einem eigenen Platz kalibriert, das Los wartet im
    Lager; montiert wird erst mit Kalibrierwerten (Gate). Ein neues Material gibt
    es nur, wo die Materialnummer wechselt — „kalibriert“ ist kein eigenes."""
    for k, n, i, t in [("pl_o2", "O2-Linie manuell", "PL-301", "Fertigungslinie"),
                       ("pl_kal", "Kalibrierplatz O2", "PL-302", "Einzelplatz"),
                       ("pl_glu", "Sensorlinie Glukose", "PL-303", "Fertigungslinie"),
                       ("pl_end", "Endmontage Analysator", "PL-304", "Fertigungslinie")]:
        o(k, "Plant", n, PlantID=i, PlantType=t, PlantGXP="GXP")
    for k, n, i, kl in [("r_bge", "06.00.025 BGE Produktion B6", "R-501", "ISO 8"), ("r_kal", "Kalibrierlabor", "R-502", "ISO 9"),
                        ("r_end", "Endmontageraum", "R-503", "ISO 8")]:
        o(k, "Raum", n, RoomID=i, RoomClass=kl)
    for k, n, i, kat, pl, raum in [("e_las", "DMC Laser LB-01", "EQ-501", "Kat 2", "pl_o2", "r_bge"),
                                   ("e_ion", "Stabionisator ST-01", "EQ-502", "Kat 1", "pl_o2", "r_bge"),
                                   ("e_cdu", "Dispensiereinheit CDU-01", "EQ-503", "Kat 3", "pl_o2", "r_bge"),
                                   ("e_vis", "Vision-System VS-01", "EQ-504", "Kat 3", "pl_o2", "r_bge"),
                                   ("e_kal", "Kalibrierstand KS-O2", "EQ-505", "Kat 3", "pl_kal", "r_kal"),
                                   ("e_gd", "Dispenser Glukose GD-01", "EQ-506", "Kat 3", "pl_glu", "r_bge"),
                                   ("e_mp", "Montagearbeitsplatz MP-01", "EQ-507", "Kat 2", "pl_end", "r_end"),
                                   ("e_fp", "Funktionsprüfstand FP-01", "EQ-508", "Kat 3", "pl_end", "r_end")]:
        o(k, "Equipment", n, EquipmentID=i, EquipmentGXP="GXP", EquipmentCategory=kat)
        v(k, "ist Teil von", pl)
        v(k, "steht in", raum)
    for k, n, i, pl in [("p_pool", "Basislösung (Pool) ansetzen", "PR-210", "plc"), ("p_lo2", "Sensorlösung O2 herstellen", "PR-211", "plc"),
                        ("p_lglu", "Enzymlösung Glukose herstellen", "PR-212", "plc"), ("p_o2", "O2-Sensor Herstellung", "PR-220", "pl_o2"),
                        ("p_kal", "O2-Kalibrierung", "PR-221", "pl_kal"), ("p_glu", "Glukose-Sensor Herstellung", "PR-230", "pl_glu"),
                        ("p_end", "Endmontage Analysator", "PR-240", "pl_end")]:
        o(k, "Prozess", n, ProcessID=i)
        v(pl, "führt aus", k)
    W = "Work in Progress (WIP) / Intermediate Product"
    for k, n, i, t in [("o_enz", "Oxidase-Rohstoff", "MAT-101", "Raw Material"), ("o_salz", "Puffersalz", "MAT-102", "Raw Material"),
                       ("o_poly", "Polymer-Matrix", "MAT-103", "Raw Material"), ("o_lumi", "Luminophor O2", "MAT-104", "Raw Material"),
                       ("o_pool", "Basislösung Pool", "MAT-110", W), ("o_lo2", "Sensorlösung O2", "MAT-111", W),
                       ("o_lglu", "Enzymlösung Glukose", "MAT-112", W),
                       ("o_bg", "BG Oberteil (Gehäuse)", "MAT-120", "Component / Purchased Part"),
                       ("o_dicht", "Dichtung O2-Sensor", "MAT-121", "Component / Purchased Part"),
                       ("o_traeg", "Sensorträger Glukose", "MAT-122", "Component / Purchased Part"),
                       ("o_elek", "Elektronikmodul", "MAT-123", "Component / Purchased Part"),
                       ("o_geh", "Gerätegehäuse", "MAT-124", "Component / Purchased Part"),
                       ("o_schr", "Schraubenset", "MAT-125", "Component / Purchased Part"),
                       ("o_o2", "O2-Sensor", "MAT-130", "Sub-Assembly"), ("o_glu", "Glukose-Sensor", "MAT-131", "Sub-Assembly"),
                       ("o_bga", "Blutgasanalysator BG-1", "MAT-140", "Finished Medical Device")]:
        o(k, "Material", n, MaterialID=i, MaterialType=t)
    # (kurz, Name, Prozess, Equipment, Inputs, Outputs)
    st = [("x_pw", "Einwaage Pool", "p_pool", "w01", ["o_enz", "o_salz"], []),
          ("x_pm", "Pool mischen", "p_pool", "rw01", [], ["o_pool"]),
          ("x_lo2", "Sensorlösung O2 ansetzen", "p_lo2", "rw01", ["o_pool", "o_poly", "o_lumi"], ["o_lo2"]),
          ("x_lglu", "Enzymlösung Glukose ansetzen", "p_lglu", "rw01", ["o_pool"], ["o_lglu"]),
          ("x_las", "DMC Code auf BG Oberteil lasern", "p_o2", "e_las", ["o_bg"], []),
          ("x_ent", "BG Oberteil entladen", "p_o2", "e_ion", [], []),
          ("x_spot", "O2-Spot dispensieren", "p_o2", "e_cdu", ["o_lo2"], []),
          ("x_tro", "Trocknung bei RT", "p_o2", None, [], []),
          ("x_aoi", "Optische Kontrolle", "p_o2", "e_vis", [], []),
          ("x_dicht", "Dichtung montieren", "p_o2", None, ["o_dicht"], ["o_o2"]),
          ("x_lag", "Lagerung O2-Los", "p_o2", None, [], []),
          ("x_kal", "Probe kalibrieren", "p_kal", "e_kal", ["o_o2"], []),
          ("x_gd", "Enzym dispensieren Glukose-Sensor", "p_glu", "e_gd", ["o_lglu", "o_traeg"], ["o_glu"]),
          ("x_mon", "Module einsetzen", "p_end", "e_mp", ["o_o2", "o_glu", "o_elek", "o_geh", "o_schr"], []),
          ("x_fp", "Funktionsprüfung", "p_end", "e_fp", [], []),
          ("x_fre", "Freigabe Analysator", "p_end", None, [], ["o_bga"])]
    for i, (k, n, p, e_, ins, outs) in enumerate(st, 101):
        o(k, "Prozessschritt", n, ProcessstepID=f"PS-{i:03d}")
        v(k, "ist Teil von", p)
        if e_:
            v(e_, "führt aus", k)
        for m_ in ins:
            v(k, "benötigt Input", m_)
        for m_ in outs:
            v(k, "erzeugt Output", m_)
    for a, b, t in [("x_pw", "x_pm", STD), ("x_pm", "x_lo2", WARTE), ("x_pm", "x_lglu", WARTE), ("x_lo2", "x_spot", WARTE),
                    ("x_las", "x_ent", STD), ("x_ent", "x_spot", STD), ("x_spot", "x_tro", STD), ("x_tro", "x_aoi", WARTE),
                    ("x_aoi", "x_dicht", GATE), ("x_dicht", "x_kal", STD), ("x_dicht", "x_lag", STD),
                    ("x_kal", "x_mon", GATE), ("x_lag", "x_mon", WARTE), ("x_lglu", "x_gd", WARTE), ("x_gd", "x_mon", WARTE),
                    ("x_mon", "x_fp", STD), ("x_fp", "x_fre", GATE)]:
        v(a, "führt zu Nachfolger (0:n)", b, ProcessstepType=t)
    for i, (k, n, s_, kl, soll, ber, ein_, reg, ue) in enumerate([
            ("y_vol", "Dosiervolumen O2-Spot", "x_spot", "CPP", 4.5, "4.35–4.65", "l", "Closed Loop", "e_cdu"),
            ("y_las", "Laserleistung", "x_las", "PP", 18.5, "18–19", "%", "Closed Loop", "e_las"),
            ("y_tro", "Trocknungszeit RT", "x_tro", "CPP", 45, "40–50", "min", "Logged", None),
            ("y_kal", "Kalibriersteigung O2", "x_kal", "CPP", 1.0, "0.95–1.05", "%", "Logged", "e_kal"),
            ("y_fp", "Funktionsprüfung Messabweichung", "x_fp", "CPP", 0, "±2", "%", "Logged", "e_fp")], 101):
        o(k, "Parameter", n, ParameterID=f"PAR-{i:03d}", ParameterClass=kl, ParameterTargetValue=soll,
          ParameterAcceptanceRange=ber, ParameterUnit=ein_, ParameterControlType=reg)
        v(s_, "definiert", k)
        if ue:
            v(ue, "überwacht/regelt", k)
    for i, (k, n, t, bez) in enumerate([("d_iqcdu", "IQ/OQ Dispensiereinheit CDU", "Validation Documentation", ["e_cdu"]),
                                        ("d_iqkal", "IQ/OQ Kalibrierstand", "Validation Documentation", ["e_kal"]),
                                        ("d_iqfp", "IQ/OQ Funktionsprüfstand", "Validation Documentation", ["e_fp"]),
                                        ("d_aao2", "Arbeitsanweisung O2-Sensor", "Work Instruction", ["p_o2"]),
                                        ("d_spbga", "Spezifikation Blutgasanalysator", "Specification", ["p_end", "x_fre"])], 101):
        o(k, "Dokument", n, DocumentID=f"DOC-{i:03d}", DocumentDMS="SAP P1U", DocumentType=t)
        for ziel in bez:
            v(k, "gehört zu", ziel)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bestand", default=os.path.join(HIER, "bestand_beispiel"))
    a = ap.parse_args()
    m = Modell(os.path.join(HIER, "modell.json"))
    m.lade_wegregeln(os.path.join(HIER, "regeln.json"))
    ed = Editor(a.bestand, m)
    O = bauen(ed)
    z = ed.zustand()
    print(f"{len(z['knoten'])} Objekte, {len(z['kanten'])} Verknüpfungen in {a.bestand}")
    ed.schliessen()


if __name__ == "__main__":
    main()
