# -*- coding: utf-8 -*-
"""Button test: require fixed/load, then build a runnable linear case.

Launch with testing/run_salome_test.sh on a copy of test.hdf. The script
does not save the study. At gap 0.02 the fixture has 56 contact sites.
Two of them stay out of the case because they produce no mesh group:
CONT_0_1_7 and CONT_8_9_1. The mesh is 134076 nodes and 436199 tetras.
"""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = "/tmp/linear_study_test.log"
DONE_PATH = "/tmp/linear_study_test.done"


def log(message):
    line = str(message)
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def finish(code):
    with open(DONE_PATH, "w", encoding="utf-8") as handle:
        handle.write(str(code))
    raise SystemExit(code)


try:
    if os.path.exists(DONE_PATH):
        os.remove(DONE_PATH)
    with open(LOG_PATH, "w", encoding="utf-8") as handle:
        handle.write("")
    os.chdir("/tmp")

    import salome
    salome.salome_init()
    from PyQt5.QtWidgets import QApplication, QMessageBox, QInputDialog

    app = QApplication.instance() or QApplication([])
    messages = []

    def capture(kind):
        def box(*args, **kwargs):
            text = args[2] if len(args) > 2 else args
            messages.append("%s: %s" % (kind, text))
            log("Dialog %s: %s" % (kind, text))
            return QMessageBox.Ok
        return box

    QMessageBox.information = capture("info")
    QMessageBox.warning = capture("warn")
    QMessageBox.critical = capture("crit")
    QMessageBox.question = capture("ask")

    def fake_item(*args, **kwargs):
        log("Versiondialog unterdrueckt")
        versions = args[3] if len(args) > 3 else ["stable"]
        return (versions[0], True)

    QInputDialog.getItem = staticmethod(fake_item)

    sys.path.insert(0, ROOT)
    import contact_wizard
    contact_wizard.QMessageBox.information = QMessageBox.information
    contact_wizard.QMessageBox.warning = QMessageBox.warning
    contact_wizard.QMessageBox.critical = QMessageBox.critical
    contact_wizard.QMessageBox.question = QMessageBox.question

    def shape_kind(value):
        name = str(value).upper()
        for token in ("COMPSOLID", "COMPOUND", "SOLID", "SHELL", "FACE", "WIRE", "EDGE", "VERTEX"):
            if name == token or name.endswith("." + token) or name.endswith("::" + token):
                return token
        return name

    component = None
    deadline = time.time() + 90.0
    while time.time() < deadline:
        QApplication.processEvents()
        try:
            component = salome.myStudy.FindComponent("GEOM")
        except Exception:
            component = None
        if component is not None:
            break
        time.sleep(0.3)
    if component is None:
        log("Keine GEOM-Komponente.")
        finish(2)

    def walk(sobject):
        iterator = salome.myStudy.NewChildIterator(sobject)
        while iterator.More():
            child = iterator.Value()
            yield child
            for nested in walk(child):
                yield nested
            iterator.Next()

    solids = []
    seen = set()
    for sobject in walk(component):
        obj = sobject.GetObject()
        if obj is None or not hasattr(obj, "GetShapeType"):
            continue
        try:
            kind = shape_kind(obj.GetShapeType())
        except Exception:
            continue
        if kind == "SOLID" and sobject.GetID() not in seen:
            seen.add(sobject.GetID())
            solids.append(obj)
            log("Solid %d %s" % (len(solids) - 1, sobject.GetName()))
    if len(solids) != 11:
        log("Erwartet 11 Solids, gefunden %d." % len(solids))
        finish(3)

    try:
        from asterstudy.gui.behavior import behavior
        behavior().code_aster_version = "default"
    except Exception as exc:
        log("Versionvorgabe: %s" % exc)

    original_progress = contact_wizard.ContactWizard._set_progress

    def logged_progress(self, fraction, text):
        mark = int(round(fraction * 20))
        if getattr(logged_progress, "last", None) != mark:
            logged_progress.last = mark
            log("Fortschritt %3.0f%% %s" % (100.0 * fraction, text))
        return original_progress(self, fraction, text)

    contact_wizard.ContactWizard._set_progress = logged_progress

    wizard = contact_wizard.ContactWizard()
    found = wizard._named_geom_shapes(("fixed", "load"))
    log("Gruppen %s" % sorted(found))
    if set(found) != {"fixed", "load"}:
        finish(3)

    wizard._named_geom_shapes = lambda names: {}
    messages[:] = []
    wizard.createLinearStudy()
    if not any("Es fehlt" in str(item) for item in messages):
        log("Gruppen-Hinweis fehlt: %s" % messages)
        finish(4)
    log("Gruppen-Hinweis ok")
    wizard._named_geom_shapes = contact_wizard.ContactWizard._named_geom_shapes.__get__(
        wizard, contact_wizard.ContactWizard
    )

    wizard.parts = solids
    wizard.gap_spin.setValue(0.02)
    wizard.table.clearSelection()
    messages[:] = []
    wizard.createLinearStudy()
    if not any("Bitte die Kontakte" in str(item) for item in messages):
        log("Auswahl-Hinweis fehlt: %s" % messages)
        finish(4)
    log("Auswahl-Hinweis ok")

    wizard.detectContacts()
    log("Kontaktstellen: %d" % len(wizard.contacts))
    if len(wizard.contacts) != 56:
        finish(5)
    wizard.table.selectAll()
    messages[:] = []
    wizard.createLinearStudy()
    joined = "\n".join(str(item) for item in messages)
    log("--- Dialoge nach dem Anlegen ---")
    log(joined)
    if "kann in AsterStudy gestartet werden" not in joined:
        finish(6)
    if "Gekoppelte Kontaktstellen: 54." not in joined:
        finish(6)
    for skipped in ("CONT_0_1_7", "CONT_8_9_1"):
        if skipped not in joined:
            log("Ausgelassener Kontakt fehlt im Hinweis: %s" % skipped)
            finish(6)

    import ASTERSTUDYGUI
    gui = ASTERSTUDYGUI.get_aster_gui()
    case = gui.study().history.current_case
    stage = list(case)[0]
    text = stage.get_text()
    log("Fall %s" % case.name)
    from asterstudy.datamodel.general import FileAttr
    mesh_name = stage.handle2info[20].filename
    result_name = stage.handle2info[80].filename
    log("Netz %s" % mesh_name)
    log("Ergebnis %s" % result_name)
    log("memory %s time %s" % (case.job_infos.get("memory"), case.job_infos.get("time")))
    if case.name != "LineareStudie":
        finish(7)
    if "GROUP_MA='fixed'" not in text or "GROUP_MA='load'" not in text:
        finish(7)
    if "FX=1.0" not in text or "E=2.1e11" not in text or "NOM_CMP='VMIS'" not in text:
        finish(7)
    if "LIAISON_MAIL" not in text or "DISTANCE_MAX = 0.02" not in text:
        finish(7)
    if "CONT_0_1_7" in text or "CONT_8_9_1" in text:
        log("Ausgelassener Kontakt steht im Kommando.")
        finish(7)
    if not (stage.handle2info[20].attr & FileAttr.In):
        finish(7)
    if not (stage.handle2info[80].attr & FileAttr.Out):
        finish(7)
    if case.job_infos.get("memory") != 4096:
        finish(7)
    if str(case.job_infos.get("time")) not in ("1800", "00:30:00", "0:30:00"):
        finish(7)
    if not wizard._is_study_entry(mesh_name):
        log("Netz ist keine Studienreferenz: %s" % mesh_name)
        finish(7)
    if not result_name.endswith(os.path.join("linear_study", "result.med")):
        log("Unerwarteter Ergebnispfad: %s" % result_name)
        finish(7)

    import SMESH
    from salome.smesh import smeshBuilder
    smesh = smeshBuilder.New()
    mesh_component = salome.myStudy.FindComponent("SMESH")
    assembly = None
    for sobject in walk(mesh_component):
        if sobject.GetName() != "linear_assembly":
            continue
        obj = sobject.GetObject()
        if obj is not None and isinstance(obj, SMESH._objref_SMESH_Mesh):
            assembly = smesh.Mesh(obj, sobject.GetName())
            break
    if assembly is None:
        log("linear_assembly fehlt.")
        finish(8)
    names = list(assembly.GetGroupNames())
    log("Gruppen %d" % len(names))
    for required in ("fixed", "load"):
        if required not in names:
            log("Netzgruppe fehlt: %s" % required)
            finish(8)
    for skipped in ("CONT_0_1_7_A", "CONT_0_1_7_B", "CONT_8_9_1_A", "CONT_8_9_1_B"):
        if skipped in names:
            log("Ungenutzte Gruppe noch im Netz: %s" % skipped)
            finish(8)
    nodes = assembly.NbNodes()
    tetras = assembly.NbTetras()
    log("Knoten %d Tetra %d" % (nodes, tetras))
    if nodes != 134076 or tetras != 436199:
        finish(8)
    log("OK")
    finish(0)
except SystemExit:
    raise
except Exception:
    log(traceback.format_exc())
    finish(1)
