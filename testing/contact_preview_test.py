# -*- coding: utf-8 -*-
"""Run ContactWizard on test.hdf and save a preview screenshot."""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "testing", "screenshots")
LOG_PATH = "/tmp/contact_preview_test.log"
DONE_PATH = "/tmp/contact_preview_test.done"
SHOT_PATH = os.path.join(OUT_DIR, "contacts.png")

os.makedirs(OUT_DIR, exist_ok=True)
with open(LOG_PATH, "w", encoding="utf-8") as handle:
    handle.write("")


def log(message):
    line = str(message)
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def finish(code):
    with open(DONE_PATH, "w", encoding="utf-8") as handle:
        handle.write(str(code))
    # Leave the session running only long enough for the view to flush.
    raise SystemExit(code)


try:
    if os.path.exists(DONE_PATH):
        os.remove(DONE_PATH)

    import salome
    salome.salome_init()
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication, QMessageBox

    def noop_box(*args, **kwargs):
        text = args[2] if len(args) > 2 else args
        log("Dialog unterdrueckt: %s" % text)
        return QMessageBox.Ok

    QMessageBox.information = noop_box
    QMessageBox.warning = noop_box
    QMessageBox.critical = noop_box

    def close_dialogs():
        app = QApplication.instance()
        if app is None:
            return
        for widget in app.topLevelWidgets():
            if isinstance(widget, QMessageBox):
                log("Schliesse Dialog: %s" % widget.windowTitle())
                widget.accept()
        QTimer.singleShot(300, close_dialogs)

    QTimer.singleShot(300, close_dialogs)

    sys.path.insert(0, ROOT)
    import contact_wizard
    contact_wizard.QMessageBox.information = noop_box
    contact_wizard.QMessageBox.warning = noop_box
    contact_wizard.QMessageBox.critical = noop_box

    geompy = contact_wizard.geompy
    type_names = {}
    try:
        type_names = {value: key for key, value in geompy.ShapeType.items()}
    except Exception as exc:
        log("ShapeType map: %s" % exc)

    def shape_type_name(value):
        # GetShapeType() may return the geompy enum or the string "SOLID".
        if value in type_names:
            name = type_names[value]
        else:
            name = str(value).upper()
        for token in ("COMPSOLID", "COMPOUND", "SOLID", "SHELL", "FACE", "WIRE", "EDGE", "VERTEX"):
            if name == token or name.endswith("." + token) or name.endswith("::" + token):
                return token
        return name

    deadline = time.time() + 60.0
    component = None
    while time.time() < deadline:
        QApplication.processEvents()
        try:
            component = salome.myStudy.FindComponent("GEOM")
        except Exception as exc:
            log("Studie noch nicht bereit: %s" % exc)
            component = None
        if component is not None:
            break
        time.sleep(0.5)
    if component is None:
        log("Keine GEOM-Komponente in der Studie.")
        finish(2)

    def walk(sobject, parent_name):
        iterator = salome.myStudy.NewChildIterator(sobject)
        while iterator.More():
            child = iterator.Value()
            yield parent_name, child
            for nested in walk(child, child.GetName()):
                yield nested
            iterator.Next()

    published = []
    for parent_name, sobject in walk(component, "GEOM"):
        geom_object = sobject.GetObject()
        if geom_object is None:
            continue
        try:
            shape_type = geom_object.GetShapeType()
        except Exception:
            continue
        name = sobject.GetName()
        kind = shape_type_name(shape_type)
        log("Objekt %s Typ %s Vater %s" % (name, kind, parent_name))
        published.append((name, geom_object, kind, sobject.GetID()))

    solids = []
    seen_entries = set()
    for _name, obj, kind, entry in published:
        if kind != "SOLID" or entry in seen_entries:
            continue
        seen_entries.add(entry)
        solids.append(obj)
    log("Veroeffentlichte Solids: %d" % len(solids))
    if len(solids) < 2:
        for name, obj, kind, _entry in published:
            if kind not in ("COMPOUND", "COMPSOLID"):
                continue
            extracted = geompy.ExtractShapes(obj, geompy.ShapeType["SOLID"], True) or []
            log("Compound %s enthaelt %d Solids" % (name, len(extracted)))
            if len(extracted) >= 2:
                solids = []
                for index, solid in enumerate(extracted, start=1):
                    solid_name = "%s_solid_%d" % (name or "Compound", index)
                    geompy.addToStudy(solid, solid_name)
                    solids.append(solid)
                break

    if len(solids) < 2:
        log("Weniger als 2 Volumenkoerper, Abbruch.")
        finish(3)

    for index, solid in enumerate(solids):
        try:
            props = geompy.BasicProperties(solid)
            log("Solid %d Volumen %s Name %s" % (index, props[2], solid.GetName()))
        except Exception as exc:
            log("Solid %d Eigenschaften fehlgeschlagen: %s" % (index, exc))

    original_progress = contact_wizard.ContactWizard._set_progress

    def logged_progress(self, fraction, text):
        log("Fortschritt %3.0f%% %s" % (100.0 * fraction, text))
        return original_progress(self, fraction, text)

    contact_wizard.ContactWizard._set_progress = logged_progress

    wizard = contact_wizard.ContactWizard()
    wizard.parts = solids
    wizard.gap_spin.setValue(0.001)
    wizard.detectContacts()
    log("Kontaktstellen bei Gap 0.001: %d" % len(wizard.contacts))
    if not wizard.contacts:
        wizard.parts = solids
        wizard.gap_spin.setValue(0.1)
        wizard.detectContacts()
        log("Kontaktstellen bei Gap 0.1: %d" % len(wizard.contacts))

    for index, contact in enumerate(wizard.contacts):
        log(
            "Kontakt %d: %s / %s Flaeche %.6g Gruppen %s %s"
            % (
                index,
                contact["nameA"],
                contact["nameB"],
                contact["area"],
                contact["groupA"],
                contact["groupB"],
            )
        )

    import SalomePyQt
    from PyQt5.QtCore import QMetaObject
    from PyQt5.QtGui import QImage

    pq = SalomePyQt.SalomePyQt
    wizard.hide()

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            QApplication.processEvents()
            time.sleep(0.05)

    def call(label, func):
        try:
            result = func()
            log("%s -> %r" % (label, result))
            return result
        except Exception as exc:
            log("%s fehlgeschlagen: %s" % (label, exc))
            return None

    call("Komponenten", pq.getComponents)
    call("aktive Komponente vorher", pq.getActiveComponent)
    activated = False
    for module_name in ("GEOM", "Geometry"):
        result = call("activateModule %s" % module_name, lambda name=module_name: pq.activateModule(name))
        if result:
            activated = True
            break
    call("aktive Komponente nachher", pq.getActiveComponent)
    if not activated:
        log("Geometriemodul liess sich nicht aktivieren.")

    occ_views = call("OCC-Views", lambda: list(pq.findViews("OCCViewer"))) or []
    if not occ_views:
        call("createView OCCViewer", lambda: pq.createView("OCCViewer"))
        pump(1.0)
        occ_views = call("OCC-Views nach create", lambda: list(pq.findViews("OCCViewer"))) or []
    if occ_views:
        call("activateView", lambda: pq.activateView(int(occ_views[0])))
    desktop = call("Desktop", pq.getDesktop)
    if desktop is not None:
        desktop.show()
        desktop.raise_()
        desktop.activateWindow()
    pump(0.5)

    if not wizard.contacts:
        log("Keine Kontaktstellen, keine Screenshots.")
        finish(6)

    for old_name in os.listdir(OUT_DIR):
        if old_name.startswith("contact_") and old_name.endswith(".png"):
            os.remove(os.path.join(OUT_DIR, old_name))

    def invoke(widget, slot_name):
        index = widget.metaObject().indexOfSlot(slot_name + "()")
        if index < 0:
            return False
        ok = QMetaObject.invokeMethod(widget, slot_name)
        log("Slot %s auf %s -> %s" % (slot_name, widget.metaObject().className(), ok))
        return ok

    app = QApplication.instance()
    for widget in app.allWidgets():
        class_name = widget.metaObject().className()
        if class_name in ("SOCC_ViewWindow", "OCCViewer_ViewWindow", "OCCViewer_ViewFrame"):
            invoke(widget, "onViewAxonometric")
    pump(0.3)

    def image_report(path):
        image = QImage(path)
        if image.isNull():
            return None
        width = image.width()
        height = image.height()
        samples = bright = red = green = 0
        buckets = set()
        for y in range(0, height, 2):
            for x in range(0, width, 2):
                pixel = image.pixel(x, y)
                red_value = (pixel >> 16) & 255
                green_value = (pixel >> 8) & 255
                blue_value = pixel & 255
                samples += 1
                if red_value + green_value + blue_value > 40:
                    bright += 1
                buckets.add((red_value // 32, green_value // 32, blue_value // 32))
                if red_value > 180 and green_value < 70 and blue_value < 70:
                    red += 1
                elif green_value > 180 and red_value < 70 and blue_value < 70:
                    green += 1
        report = {
            "width": width,
            "height": height,
            "samples": samples,
            "bright": bright,
            "buckets": len(buckets),
            "red": red,
            "green": green,
        }
        log(
            "Bild %s %dx%d hell %d/%d farben %d rot %d gruen %d"
            % (path, width, height, bright, samples, len(buckets), red, green)
        )
        return report

    def useful(report):
        if not report:
            return False
        # A plain widget grab of the OCC window is a black viewport plus a toolbar.
        return report["bright"] > max(80, report["samples"] // 20) and report["buckets"] >= 6

    def publish(path):
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            return None
        return image_report(path)

    shot_rows = []
    for index, contact in enumerate(wizard.contacts, start=1):
        area_a = geompy.BasicProperties(contact["facesA"])[1]
        area_b = geompy.BasicProperties(contact["facesB"])[1]
        log(
            "Patch %d Flaeche A %.6g B %.6g gespeichert %.6g"
            % (index, area_a, area_b, contact["area"])
        )
        wizard.previewContact(index - 1)
        pump(0.3)
        # Frame the patch, then put the bodies back without zooming out.
        gg = wizard._geom_gui()
        body_entries = [
            salome.ObjectToID(contact["partA"]),
            salome.ObjectToID(contact["partB"]),
        ]
        for entry in body_entries:
            salome.sg.Erase(entry)
        salome.sg.FitAll()
        pump(0.25)
        wizard._display_body(gg, contact["partA"])
        wizard._display_body(gg, contact["partB"])
        wizard._display_contact_face(gg, contact["facesA"], 255, 0, 0)
        wizard._display_contact_face(gg, contact["facesB"], 0, 255, 0)
        salome.sg.UpdateView()
        pump(0.45)
        path = os.path.join(OUT_DIR, "contact_%02d.png" % index)
        saved = call("dumpView %d" % index, lambda dest=path: pq.dumpView(dest))
        report = publish(path) if saved else None
        if not useful(report):
            log("Screenshot %d ohne Geometrie: %s" % (index, path))
            finish(5)
        shot_rows.append((path, contact, area_a, area_b))
        log("Screenshot %s (%d Bytes)" % (path, os.path.getsize(path)))

    with open(os.path.join(OUT_DIR, "contacts.txt"), "w", encoding="utf-8") as handle:
        handle.write("contacts %d\n" % len(shot_rows))
        for path, contact, area_a, area_b in shot_rows:
            handle.write(
                "%s | %s | %s | area %.6g | patchA %.6g | patchB %.6g | %s | %s\n"
                % (
                    os.path.basename(path),
                    contact["nameA"],
                    contact["nameB"],
                    contact["area"],
                    area_a,
                    area_b,
                    contact["groupA"],
                    contact["groupB"],
                )
            )
    finish(0)
except SystemExit:
    raise
except Exception:
    log(traceback.format_exc())
    finish(1)
