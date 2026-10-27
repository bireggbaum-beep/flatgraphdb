"""
Beispielbestand: Fertigung eines elektrochemischen Teststreifens (Medizintechnik).

    python editor/beispiel.py --bestand editor/bestand_beispiel

Vier Teilprozesse an vier Standorten, so wie sie in der Praxis auseinanderliegen:
Chemlabor (Reagenzansatz) -> Linie 1 (Siebdruck Ag/Carbon, Dispensieren,
Trocknungen) -> Montage & Konfektion (andere Plant, anderer Raum) -> QC.

Die Beziehungen folgen dem Regelwerk `regeln.json` (Annahmen); absichtlich
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
        ed.kante_anlegen(O[a], O[b], art, felder=attr or None)

    # ---- Plants und Räume ------------------------------------------------
    for k, n, i, t in [("pl1", "Sensorfertigung Linie 1", "PL-101", "Fertigungslinie"),
                       ("plm", "Montage & Konfektion", "PL-102", "Fertigungslinie"),
                       ("plc", "Chemlabor Materialvorbereitung", "PL-201", "Funktionsbereich (zB. Labor, QC)"),
                       ("plq", "QC-Labor", "PL-202", "Funktionsbereich (zB. Labor, QC)")]:
        o(k, "Plant", n, PlantID=i, PlantType=t, PlantGXP="GXP")
    for k, n, i, kl, pl in [("rsd", "Siebdruckraum", "R-101", "ISO 8", "pl1"), ("rdp", "Dispensierraum", "R-102", "ISO 7", "pl1"),
                            ("rtr", "Trockenraum", "R-103", "ISO 8", "pl1"), ("rmo", "Montageraum", "R-201", "ISO 7", "plm"),
                            ("rko", "Konfektionierraum", "R-202", "ISO 8", "plm"), ("rca", "Ansatzraum Chemlabor", "R-301", "ISO 8", "plc"),
                            ("rcl", "Reagenzlager Chemlabor", "R-302", "ISO 9", "plc"), ("rql", "QC-Messlabor", "R-401", "ISO 9", "plq"),
                            ("rqk", "QC-Klimaraum", "R-402", "ISO 9", "plq")]:
        o(k, "Raum", n, RoomID=i, RoomClass=kl)
        v(k, "ist Teil von", pl)

    # ---- Medien ----------------------------------------------------------
    for k, n, i, t, c in [("druckluft", "Druckluft", "M-01", "Process Gases & Compressed Air", "Indirect Impact"),
                          ("stickstoff", "Stickstoff", "M-02", "Process Gases & Compressed Air", "Direct Impact"),
                          ("reinstw", "Reinstwasser", "M-03", "Water Systems", "Direct Impact"),
                          ("hvac7", "Reinraumluft ISO 7", "M-04", "HVAC / Cleanroom Air", "Direct Impact"),
                          ("hvac8", "Reinraumluft ISO 8", "M-05", "HVAC / Cleanroom Air", "Indirect Impact")]:
        o(k, "Medium", n, MediumID=i, MediumType=t, MediumGXP="GXP", MediumProductContact=c)
    for r, m_ in [("rdp", "hvac7"), ("rmo", "hvac7"), ("rsd", "hvac8"), ("rtr", "hvac8"), ("rca", "hvac8"), ("rko", "hvac8")]:
        v(r, "verwendet", m_)

    # ---- Equipment -------------------------------------------------------
    eq = [("sd01", "Siebdruckanlage SD-01", "EQ-101", "Kat 3", "rsd"), ("oi01", "Optische Inline-Inspektion OI-01", "EQ-102", "Kat 3", "rsd"),
          ("tr01", "Durchlauftrockner TR-01", "EQ-103", "Kat 3", "rtr"), ("tr02", "Vakuumtrockner TR-02", "EQ-104", "Kat 3", "rtr"),
          ("dp01", "Dispensierroboter DP-01", "EQ-105", "Kat 3", "rdp"),
          ("ma01", "Laminierautomat MA-01", "EQ-201", "Kat 3", "rmo"), ("sn01", "Stanz-/Schneidstation SN-01", "EQ-202", "Kat 3", "rmo"),
          ("vp01", "Verpackungsmaschine VP-01", "EQ-203", "Kat 3", "rko"),
          ("w01", "Analysenwaage W-01", "EQ-301", "Kat 2", "rca"), ("rw01", "Dissolver RW-01", "EQ-302", "Kat 3", "rca"),
          ("vk01", "Viskosimeter VK-01", "EQ-303", "Kat 2", "rca"), ("ks01", "Kühlschrank Reagenz KS-01", "EQ-304", "Kat 2", "rcl"),
          ("ps01", "Potentiostat PS-01", "EQ-401", "Kat 3", "rql"), ("ms01", "Mikroskop MS-01", "EQ-402", "Kat 2", "rql"),
          ("kl01", "Klimaschrank Stabilität KL-01", "EQ-403", "Kat 3", "rqk")]
    for k, n, i, kat, r in eq:
        o(k, "Equipment", n, EquipmentID=i, EquipmentGXP="GXP", EquipmentCategory=kat)
        v(k, "steht in", r)
    for e_, m_ in [("sd01", "druckluft"), ("dp01", "druckluft"), ("tr02", "stickstoff"), ("rw01", "reinstw"), ("ma01", "druckluft"), ("sn01", "druckluft")]:
        v(e_, "verwendet", m_)

    # ---- Software --------------------------------------------------------
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

    # ---- Prozesse --------------------------------------------------------
    o("p_ges", "Prozess", "Teststreifenfertigung (Gesamtprozess)", ProcessID="PR-100")
    for k, n, i, pl in [("p_chem", "Materialvorbereitung Reagenz", "PR-110", "plc"), ("p_elek", "Elektrodenfertigung (Siebdruck und Dispensieren)", "PR-120", "pl1"),
                        ("p_mont", "Montage und Konfektionierung", "PR-130", "plm"), ("p_qc", "QC-Prüfung und Chargenfreigabe", "PR-140", "plq")]:
        o(k, "Prozess", n, ProcessID=i)
        v(k, "ist Teil von", "p_ges")
        v(k, "gehört zu", pl)
    for sw, p in [("mes", "p_ges"), ("ebr", "p_elek"), ("ebr", "p_chem"), ("ebr", "p_mont"), ("lims", "p_qc"), ("sw_ps", "p_qc")]:
        v(sw, "unterstützt", p)

    # ---- Prozessschritte: (kurz, Name, Prozess, Raum, Equipment) ---------
    st = [("s_waege", "Wägen Rohstoffe", "p_chem", "rca", "w01"), ("s_ansatz", "Ansetzen Reagenzlösung", "p_chem", "rca", "rw01"),
          ("s_visk", "Viskositätsprüfung (IPC)", "p_chem", "rca", "vk01"), ("s_kuehl", "Kühllagerung Reagenz", "p_chem", "rcl", "ks01"),
          ("s_ag", "Siebdruck Silber (Ag)", "p_elek", "rsd", "sd01"), ("s_trag", "Trocknung Ag", "p_elek", "rtr", "tr01"),
          ("s_carb", "Siebdruck Carbon", "p_elek", "rsd", "sd01"), ("s_trcarb", "Trocknung Carbon", "p_elek", "rtr", "tr01"),
          ("s_ipc", "Optische Inline-Kontrolle (IPC)", "p_elek", "rsd", "oi01"), ("s_disp", "Dispensieren Reagenz", "p_elek", "rdp", "dp01"),
          ("s_vak", "Vakuumtrocknung Reagenz", "p_elek", "rtr", "tr02"),
          ("s_lam", "Laminieren", "p_mont", "rmo", "ma01"), ("s_schn", "Schneiden Streifen", "p_mont", "rmo", "sn01"),
          ("s_kon", "Konfektionieren und Verpacken", "p_mont", "rko", "vp01"),
          ("s_sicht", "Sichtprüfung", "p_qc", "rql", "ms01"), ("s_mess", "Elektrochemische Messung", "p_qc", "rql", "ps01"),
          ("s_stab", "Stabilitätslagerung", "p_qc", "rqk", "kl01"), ("s_frei", "Chargenfreigabe", "p_qc", "rql", None)]
    for i, (k, n, p, r, e_) in enumerate(st, 1):
        o(k, "Prozessschritt", n, ProcessstepID=f"PS-{i:03d}")
        v(k, "ist Teil von", p)
        v(k, "steht in", r)
        if e_:
            v(e_, "führt aus", k)
    folge = [("s_waege", "s_ansatz", STD), ("s_ansatz", "s_visk", STD), ("s_visk", "s_kuehl", GATE), ("s_kuehl", "s_disp", WARTE),
             ("s_ag", "s_trag", STD), ("s_trag", "s_carb", STD), ("s_carb", "s_trcarb", STD), ("s_trcarb", "s_ipc", STD),
             ("s_ipc", "s_disp", GATE), ("s_ipc", "s_ag", LOOP), ("s_disp", "s_vak", STD), ("s_vak", "s_lam", STD),
             ("s_lam", "s_schn", STD), ("s_schn", "s_kon", STD), ("s_kon", "s_sicht", STD), ("s_sicht", "s_mess", STD),
             ("s_mess", "s_stab", STD), ("s_stab", "s_frei", WARTE)]
    for a, b, t in folge:
        v(a, "führt zu Nachfolger (0:n)", b, ProcessstepType=t)

    # ---- Material --------------------------------------------------------
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
    for k in ("m_ag", "m_carb", "m_enz", "m_med", "m_puf"):
        v(k, "steht in", "rcl")
    for a, b in [("m_strip", "m_pack"), ("m_dose", "m_pack"), ("m_gebr", "m_pack"), ("m_pack", "m_dev")]:
        v(a, "ist Teil von", b)
    ein = [("s_waege", ["m_enz", "m_med", "m_puf"]), ("s_ansatz", ["m_enz", "m_med", "m_puf"]), ("s_ag", ["m_ag", "m_pet"]), ("s_carb", ["m_carb", "m_foag"]),
           ("s_disp", ["m_reag", "m_focarb"]), ("s_lam", ["m_foreag", "m_abd", "m_kleb"]), ("s_schn", ["m_bahn"]), ("s_kon", ["m_strip", "m_dose", "m_gebr"])]
    aus = [("s_ansatz", ["m_reag"]), ("s_ag", ["m_foag"]), ("s_carb", ["m_focarb"]), ("s_vak", ["m_foreag"]), ("s_lam", ["m_bahn"]),
           ("s_schn", ["m_strip"]), ("s_kon", ["m_pack"]), ("s_frei", ["m_dev"])]
    for s_, ms in ein:
        for m_ in ms:
            v(s_, "benötigt Input", m_)
    for s_, ms in aus:
        for m_ in ms:
            v(s_, "erzeugt Output", m_)

    # ---- Parameter: (kurz, Name, Schritt, Klasse, Soll, Bereich, Einheit, Regelung, Equipment steuert, Equipment/Software überwacht) ----
    par = [("a_rak", "Rakeldruck Ag", "s_ag", "CPP", 2.0, "1.8–2.2", "bar", "Closed Loop", "sd01", "sps_sd"),
           ("a_nass", "Nassschichtmasse Ag", "s_ag", "CPP", 120, "110–130", "mg", "Logged", None, "sps_sd"),
           ("a_tag", "Temperatur Trocknung Ag", "s_trag", "CPP", 120, "115–125", "°C", "Closed Loop", "tr01", "sps_tr"),
           ("a_zag", "Zeit Trocknung Ag", "s_trag", "CPP", 10, "9–11", "min", "Logged", None, "sps_tr"),
           ("a_rf", "Raumfeuchte Trockenraum", "s_trag", "PP", 30, "20–40", "%rH", "Logged", None, "mes"),
           ("a_rakc", "Rakeldruck Carbon", "s_carb", "CPP", 1.8, "1.6–2.0", "bar", "Closed Loop", "sd01", "sps_sd"),
           ("a_tc", "Temperatur Trocknung Carbon", "s_trcarb", "CPP", 100, "95–105", "°C", "Closed Loop", "tr01", "sps_tr"),
           ("a_zc", "Zeit Trocknung Carbon", "s_trcarb", "CPP", 15, "14–16", "min", "Logged", None, "sps_tr"),
           ("a_dos", "Dosiermasse Reagenz", "s_disp", "CPP", 50, "47–53", "mg", "Closed Loop", "dp01", "sw_dp"),
           ("a_dd", "Dispensierdruck", "s_disp", "PP", 0.5, "0.4–0.6", "bar", "Closed Loop", "dp01", "sw_dp"),
           ("a_vak", "Vakuum Reagenztrocknung", "s_vak", "CPP", 0.2, "0.15–0.25", "bar", "Closed Loop", "tr02", "sps_tr"),
           ("a_tv", "Temperatur Vakuumtrocknung", "s_vak", "CPP", 35, "33–37", "°C", "Closed Loop", "tr02", "sps_tr"),
           ("a_zv", "Zeit Vakuumtrocknung", "s_vak", "CPP", 60, "55–65", "min", "Logged", None, "sps_tr"),
           ("c_ein", "Einwaage Enzym", "s_waege", "CPP", 120, "118–122", "g", "Logged", None, "w01"),
           ("c_rw", "Rührgeschwindigkeit Ansatz", "s_ansatz", "PP", 500, "450–550", "rpm", "Closed Loop", "rw01", None),
           ("c_t", "Ansatztemperatur", "s_ansatz", "PP", 20, "18–22", "°C", "Logged", None, "w01"),
           ("c_lag", "Lagertemperatur Reagenz", "s_kuehl", "CPP", 5, "2–8", "°C", "Logged", None, "ks01"),
           ("c_std", "Standzeit Reagenz", "s_kuehl", "CPP", 24, "max. 24", "h", "Logged", None, None),
           ("m_lp", "Laminierdruck", "s_lam", "CPP", 3.0, "2.7–3.3", "bar", "Closed Loop", "ma01", "sw_ma"),
           ("m_siegel", "Siegeltemperatur", "s_lam", "CPP", 140, "135–145", "°C", "Closed Loop", "ma01", "sw_ma"),
           ("m_schn", "Anpressdruck Schneiden", "s_schn", "PP", 4.0, "3.5–4.5", "bar", "Logged", "sn01", None),
           ("q_t", "Messtemperatur QC", "s_mess", "PP", 25, "23–27", "°C", "Logged", None, "sw_ps"),
           ("q_lag", "Lagertemperatur Stabilität", "s_stab", "CPP", 25, "23–27", "°C", "Logged", None, "kl01"),
           ("q_rf", "Feuchte Stabilität", "s_stab", "CPP", 60, "55–65", "%rH", "Logged", None, "kl01")]
    for i, (k, n, s_, kl, soll, ber, ein_, reg, steu, ueb) in enumerate(par, 1):
        o(k, "Parameter", n, ParameterID=f"PAR-{i:03d}", ParameterClass=kl, ParameterTargetValue=soll,
          ParameterAcceptanceRange=ber, ParameterUnit=ein_, ParameterControlType=reg)
        v(k, "gehört zu", s_)
        if steu:
            v(steu, "steuert", k)
        if ueb:
            v(ueb, "überwacht/regelt", k)

    # ---- Dokumente -------------------------------------------------------
    dok = [("d_spag", "Spezifikation Silberpaste", "Specification", "SAP P1U", [("definiert", "m_ag")]),
           ("d_spcarb", "Spezifikation Carbonpaste", "Specification", "SAP P1U", [("definiert", "m_carb")]),
           ("d_spreag", "Spezifikation Reagenzlösung", "Specification", "SAP P1U", [("definiert", "m_reag"), ("definiert", "c_t")]),
           ("d_spdev", "Spezifikation Teststreifen", "Specification", "SAP P1U", [("definiert", "m_dev")]),
           ("d_aasd", "Arbeitsanweisung Siebdruck", "Work Instruction", "DiaDoc", [("definiert", "s_ag"), ("definiert", "s_carb")]),
           ("d_aatr", "Arbeitsanweisung Trocknung", "Work Instruction", "DiaDoc", [("definiert", "s_trag"), ("definiert", "s_trcarb"), ("definiert", "s_vak")]),
           ("d_aadp", "Arbeitsanweisung Dispensieren", "Work Instruction", "DiaDoc", [("definiert", "s_disp")]),
           ("d_aaans", "Arbeitsanweisung Reagenzansatz", "Work Instruction", "DiaDoc", [("definiert", "s_ansatz"), ("definiert", "s_waege")]),
           ("d_aalam", "Arbeitsanweisung Laminieren", "Work Instruction", "DiaDoc", [("definiert", "s_lam"), ("definiert", "s_schn")]),
           ("d_aasi", "Arbeitsanweisung Sichtprüfung", "Work Instruction", "DiaDoc", [("definiert", "s_sicht")]),
           ("d_brel", "Batch Record Elektrodenfertigung", "Batch Record", "SAP P1U", [("definiert", "p_elek")]),
           ("d_brch", "Batch Record Reagenzansatz", "Batch Record", "SAP P1U", [("definiert", "p_chem")]),
           ("d_brmo", "Batch Record Montage", "Batch Record", "SAP P1U", [("definiert", "p_mont")]),
           ("d_brqc", "Batch Record QC", "Batch Record", "SAP P1U", [("definiert", "p_qc")]),
           ("d_pqtr", "PQ Trocknungsprozess Ag/Carbon", "Validation Documentation", "SAP P1U",
            [("definiert", "a_tag"), ("definiert", "a_zag"), ("definiert", "a_tc"), ("definiert", "a_zc")]),
           ("d_pqdp", "PQ Dispensierprozess", "Validation Documentation", "SAP P1U", [("definiert", "a_dos"), ("definiert", "a_dd")]),
           ("d_iqsd", "IQ/OQ Siebdruckanlage", "Validation Documentation", "SAP P1U", [("unterstützt", "sd01")]),
           ("d_iqtr", "IQ/OQ Durchlauftrockner", "Validation Documentation", "SAP P1U", [("unterstützt", "tr01")]),
           ("d_iqdp", "IQ/OQ Dispensierroboter", "Validation Documentation", "SAP P1U", [("unterstützt", "dp01")]),
           ("d_iqma", "IQ/OQ Laminierautomat", "Validation Documentation", "SAP P1U", [("unterstützt", "ma01")]),
           ("d_iqps", "IQ/OQ Potentiostat", "Validation Documentation", "SAP P1U", [("unterstützt", "ps01")]),
           ("d_csv", "CSV-Bericht MES", "Validation Documentation", "SAP P1U", [("unterstützt", "mes")]),
           ("d_gsp", "GSP Reinraumverhalten", "GSP", "DiaDoc", []),
           ("d_ruest", "Formular Rüstprotokoll", "Form", "DiaDoc", [])]
    for i, (k, n, t, dms, bez) in enumerate(dok, 1):
        o(k, "Dokument", n, DocumentID=f"DOC-{i:03d}", DocumentDMS=dms, DocumentType=t)
        for art, ziel in bez:
            v(k, art, ziel)

    # ---- Absichtliche Lücken (für die Qualitätsfilter) -------------------
    # keine IQ/OQ für: Vakuumtrockner, Schneidstation, Verpackung, Waage, Dissolver,
    # Viskosimeter, Kühlschrank, Mikroskop, Klimaschrank, Inline-Inspektion
    ed.knoten_aendern(O["ms01"], felder={"EquipmentCategory": ""})
    return O


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bestand", default=os.path.join(HIER, "bestand_beispiel"))
    a = ap.parse_args()
    m = Modell(os.path.join(HIER, "modell.json"))
    m.lade_regeln(os.path.join(HIER, "regeln.json"))
    ed = Editor(a.bestand, m)
    O = bauen(ed)
    z = ed.zustand()
    print(f"{len(z['knoten'])} Objekte, {len(z['kanten'])} Verknüpfungen in {a.bestand}")
    ed.schliessen()


if __name__ == "__main__":
    main()
