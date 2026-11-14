# -*- coding: utf-8 -*-
"""
Salome-Meca Contact Wizard v0.0.1
Automatic contact detection + Code_Aster constraint generation

Based on the original 3DContact.py by Lucio Gomez (psicofil)
"""

import math
import os
from urllib.parse import unquote, urlparse

import salome
salome.salome_init()
from salome.geom import geomBuilder
import GEOM

try:
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
        QDoubleSpinBox, QTableWidget, QTableWidgetItem, QComboBox,
        QAbstractItemView, QDialog, QTextEdit, QMessageBox,
        QProgressBar, QApplication, QCheckBox
    )
    from PyQt5.QtCore import Qt
except ImportError:
    from PyQt4.QtGui import (
        QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
        QDoubleSpinBox, QTableWidget, QTableWidgetItem, QComboBox,
        QAbstractItemView, QDialog, QTextEdit, QMessageBox,
        QProgressBar, QApplication, QCheckBox
    )
    from PyQt4.QtCore import Qt

geompy = geomBuilder.New(salome.myStudy)


class ContactWizard(QWidget):
    def __init__(self):
        super(ContactWizard, self).__init__()
        self.parts = []
        self.contacts = []
        self._detecting = False
        self._building_study = False
        self._aborted = False
        self._preview_bodies = []
        self._plane_cache = {}
        self.initUI()
        if self._abort_without_boundary_groups():
            return
        self.loadParts()

    def initUI(self):
        self.setWindowTitle("Contact Wizard v0.0.1 - Salome-Meca")
        self.resize(950, 620)
        layout = QVBoxLayout()

        # Controls
        top = QHBoxLayout()
        top.addWidget(QLabel("Gap / Toleranz:"))
        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setDecimals(4)
        self.gap_spin.setRange(0.0, 100.0)
        self.gap_spin.setValue(0.001)
        self.gap_spin.setSingleStep(0.001)
        top.addWidget(self.gap_spin)

        self.btn_detect = QPushButton("Kontakte automatisch suchen")
        self.btn_detect.clicked.connect(self.detectContacts)
        top.addWidget(self.btn_detect)
        top.addStretch()
        layout.addLayout(top)

        self.status_label = QLabel("Bereit.")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Übernehmen", "Teil A", "Teil B", "Fläche", "Typ", "Aktion"
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemSelectionChanged.connect(self.onRowSelected)
        layout.addWidget(self.table)

        # Bottom
        bottom = QHBoxLayout()
        self.btn_generate = QPushButton("Code für AsterStudy generieren")
        self.btn_generate.clicked.connect(self.generateCode)
        self.btn_study = QPushButton("Lineare Studie anlegen")
        self.btn_study.clicked.connect(self.createLinearStudy)
        self.btn_close = QPushButton("Schließen")
        self.btn_close.clicked.connect(self.close)
        bottom.addWidget(self.btn_generate)
        bottom.addWidget(self.btn_study)
        bottom.addWidget(self.btn_close)
        layout.addLayout(bottom)

        self.setLayout(layout)

    def loadParts(self):
        self.parts = []
        try:
            count = salome.sg.SelectedCount()
            if count > 0:
                for i in range(count):
                    entry = salome.sg.getSelected(i)
                    obj = salome.myStudy.FindObjectID(entry).GetObject()
                    if obj:
                        self.parts.append(obj)
            else:
                QMessageBox.information(
                    self, "Hinweis",
                    "Keine Teile ausgewählt. Bitte Solids im Object Browser markieren und neu starten."
                )
        except Exception as e:
            QMessageBox.warning(self, "Fehler beim Laden", str(e))

    def _unique_subshapes(self, part, sub_ids):
        """Return each localized sub-shape once.

        FastIntersect repeats a face id once per hitting triangle. Those
        repeats are not extra contact sites.
        """
        faces = geompy.SubShapes(part, sub_ids)
        unique = []
        seen = set()
        for face in faces:
            try:
                face_id = geompy.GetSubShapeID(part, face)
            except Exception:
                face_id = id(face)
            if face_id in seen:
                continue
            seen.add(face_id)
            unique.append(face)
        return unique

    def _shape_area(self, shape):
        try:
            props = geompy.BasicProperties(shape)
        except Exception:
            return 0.0
        if not props or len(props) < 2 or props[1] is None:
            return 0.0
        return float(props[1])

    def _sub_shapes(self, shape, kind):
        try:
            found = geompy.SubShapeAll(shape, geompy.ShapeType[kind])
        except Exception:
            return []
        if not found:
            return []
        return list(found)

    def _faces_with_area(self, shape):
        if shape is None:
            return []
        faces = self._sub_shapes(shape, "FACE")
        if not faces:
            faces = [shape]
        return [face for face in faces if self._shape_area(face) > 1e-8]

    def _pack_faces(self, faces):
        if not faces:
            return None
        if len(faces) == 1:
            return faces[0]
        return geompy.MakeCompound(faces)

    def _overlap_within_limit(self, area, area_a, area_b):
        """An overlap cannot be larger than the smaller of the two faces."""
        if area <= 1e-8:
            return False
        smaller = min(area_a, area_b)
        if smaller <= 1e-8:
            return True
        return area <= smaller * 1.05 + 1e-8

    def _collect_overlap(self, shape, area_a, area_b):
        faces = []
        for face in self._faces_with_area(shape):
            area = self._shape_area(face)
            if self._overlap_within_limit(area, area_a, area_b):
                faces.append(face)
        if not faces:
            return None
        area = sum(self._shape_area(face) for face in faces)
        if not self._overlap_within_limit(area, area_a, area_b):
            return None
        return faces, area

    def _exact_overlap(self, face_a, face_b, area_a, area_b):
        common = geompy.MakeCommon(face_a, face_b)
        collected = self._collect_overlap(common, area_a, area_b)
        if collected is None:
            return None
        faces, area = collected
        patch = self._pack_faces(faces)
        # A second object, so the two groups do not share one study entry.
        patch_b = geompy.MakeTranslation(patch, 0.0, 0.0, 0.0)
        return patch, patch_b, area

    def _point(self, vertex):
        coords = geompy.PointCoordinates(vertex)
        return (float(coords[0]), float(coords[1]), float(coords[2]))

    def _unit_vector(self, components):
        length = math.sqrt(
            components[0] ** 2 + components[1] ** 2 + components[2] ** 2
        )
        if length < 1e-12:
            return None
        return (
            components[0] / length,
            components[1] / length,
            components[2] / length,
        )

    def _dot(self, left, right):
        return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]

    def _face_plane(self, face):
        """Plane of a planar face as (origin, unit normal), or None.

        The cache holds the face. Keying on id() alone reused the id of a
        collected face and returned that earlier plane for a new face.
        """
        key = id(face)
        cached = self._plane_cache.get(key)
        if cached is not None and cached[0] is face:
            return cached[1]
        result = None
        try:
            vertices = self._sub_shapes(geompy.GetNormal(face), "VERTEX")
            if len(vertices) >= 2:
                origin = self._point(vertices[0])
                end = self._point(vertices[-1])
                normal = self._unit_vector((
                    end[0] - origin[0],
                    end[1] - origin[1],
                    end[2] - origin[2],
                ))
                if normal is not None:
                    planar = True
                    for vertex in self._sub_shapes(face, "VERTEX"):
                        point = self._point(vertex)
                        distance = self._dot((
                            point[0] - origin[0],
                            point[1] - origin[1],
                            point[2] - origin[2],
                        ), normal)
                        if abs(distance) > 1e-5:
                            planar = False
                            break
                    if planar:
                        result = (origin, normal)
        except Exception:
            result = None
        self._plane_cache[key] = (face, result)
        return result

    def _on_plane(self, face, origin, normal, limit):
        plane = self._face_plane(face)
        if plane is None:
            return False
        face_origin, face_normal = plane
        if abs(self._dot(face_normal, normal)) < 0.996:
            return False
        distance = self._dot((
            face_origin[0] - origin[0],
            face_origin[1] - origin[1],
            face_origin[2] - origin[2],
        ), normal)
        return abs(distance) <= limit

    def _tolerance_overlap(self, face_a, face_b, tolerance, area_a, area_b):
        """Shared footprint of two parallel faces at most `tolerance` apart.

        The slab only extends along the normal. The patch stays inside the
        outline of both faces, so a near edge does not grow into a strip.
        """
        if tolerance <= 0.0:
            return None
        plane_a = self._face_plane(face_a)
        plane_b = self._face_plane(face_b)
        if plane_a is None or plane_b is None:
            return None
        origin_a, normal_a = plane_a
        origin_b, normal_b = plane_b
        if abs(self._dot(normal_a, normal_b)) < 0.996:
            return None
        distance = self._dot((
            origin_b[0] - origin_a[0],
            origin_b[1] - origin_a[1],
            origin_b[2] - origin_a[2],
        ), normal_a)
        if abs(distance) > tolerance + 1e-8:
            return None
        direction = geompy.MakeVectorDXDYDZ(normal_a[0], normal_a[1], normal_a[2])
        # Extra depth keeps the mating face inside the solid, not on its skin.
        half_height = abs(distance) + 1e-4
        slab = geompy.MakePrismVecH2Ways(face_a, direction, half_height)
        collected = self._collect_overlap(geompy.MakeCommon(slab, face_b), area_a, area_b)
        if collected is None:
            return None
        faces_b = [
            face for face in collected[0]
            if self._on_plane(face, origin_b, normal_b, 1e-5)
        ]
        if not faces_b:
            return None
        area = sum(self._shape_area(face) for face in faces_b)
        if not self._overlap_within_limit(area, area_a, area_b):
            return None
        patch_b = self._pack_faces(faces_b)
        patch_a = geompy.MakeTranslationVectorDistance(patch_b, direction, -distance)
        return patch_a, patch_b, area

    def _surface_alignment(self, face, normal):
        """Absolute cosine between `normal` and the normal at `face`."""
        plane = self._face_plane(face)
        if plane is not None:
            return abs(self._dot(plane[1], normal))
        try:
            vertices = self._sub_shapes(geompy.GetNormal(face), "VERTEX")
            if len(vertices) < 2:
                return None
            start = self._point(vertices[0])
            end = self._point(vertices[-1])
            got = self._unit_vector((
                end[0] - start[0],
                end[1] - start[1],
                end[2] - start[2],
            ))
        except Exception:
            return None
        if got is None:
            return None
        return abs(self._dot(got, normal))

    def _band_overlap(self, planar, other, tolerance, area_planar, area_other):
        """Portion of `other` that lies in the tolerance slab of a planar face.

        Used when the mate is curved, so the parallel-face path does not apply.
        The piece must lie along the plane. A face that only cuts across the
        slab is a near edge, not a contact patch.
        """
        if tolerance <= 0.0:
            return None
        plane = self._face_plane(planar)
        if plane is None:
            return None
        try:
            if float(geompy.MinDistance(planar, other)) > tolerance + 1e-8:
                return None
        except Exception:
            return None
        normal = plane[1]
        direction = geompy.MakeVectorDXDYDZ(normal[0], normal[1], normal[2])
        try:
            slab = geompy.MakePrismVecH2Ways(planar, direction, tolerance)
            common = geompy.MakeCommon(slab, other)
        except Exception:
            return None
        collected = self._collect_overlap(common, area_planar, area_other)
        if collected is None:
            return None
        kept = []
        for face in collected[0]:
            alignment = self._surface_alignment(face, normal)
            if alignment is None or alignment < 0.996:
                continue
            kept.append(face)
        if not kept:
            return None
        area = sum(self._shape_area(face) for face in kept)
        if not self._overlap_within_limit(area, area_planar, area_other):
            return None
        patch_other = self._pack_faces(kept)
        # MakeProjection rejects faces. Sweep the curved patch through the
        # plane and keep the imprint on the planar face.
        try:
            punch = geompy.MakePrismVecH2Ways(patch_other, direction, tolerance)
            on_planar = self._collect_overlap(
                geompy.MakeCommon(punch, planar), area_planar, area_other
            )
        except Exception:
            return None
        if on_planar is None:
            return None
        patch_planar = geompy.MakeTranslation(
            self._pack_faces(on_planar[0]), 0.0, 0.0, 0.0
        )
        return patch_planar, patch_other, area

    def _overlap_patches(self, face_a, face_b, tolerance):
        area_a = self._shape_area(face_a)
        area_b = self._shape_area(face_b)
        try:
            exact = self._exact_overlap(face_a, face_b, area_a, area_b)
        except Exception:
            exact = None
        if exact is not None:
            return exact
        try:
            parallel = self._tolerance_overlap(face_a, face_b, tolerance, area_a, area_b)
        except Exception:
            parallel = None
        if parallel is not None:
            return parallel
        try:
            if self._face_plane(face_a) is not None:
                band = self._band_overlap(face_a, face_b, tolerance, area_a, area_b)
                if band is not None:
                    return band
            if self._face_plane(face_b) is not None:
                band = self._band_overlap(face_b, face_a, tolerance, area_b, area_a)
                if band is not None:
                    return band[1], band[0], band[2]
        except Exception:
            return None
        return None

    def _part_label(self, part, index):
        name = part.GetName() if part is not None else ""
        return name or ("Part_%d" % index)

    def _set_progress(self, fraction, text):
        """Paint the bar before the next blocking geometry call.

        FastIntersect and MakeCommon run on the GUI thread. Without
        processEvents the bar stays at its old value until the search ends.
        """
        if fraction < 0.0:
            fraction = 0.0
        elif fraction > 1.0:
            fraction = 1.0
        self.progress.setValue(int(round(fraction * 1000)))
        self.status_label.setText(text)
        QApplication.processEvents()

    def _contact_face_pairs(self, part_a, part_b, ids_a, ids_b, tolerance, on_step=None):
        """Overlap of every candidate face, not the whole face.

        The two id lists from FastIntersect are independent. Each id list only
        says which faces come within the tolerance. The contact patch is the
        common footprint, including a gap up to that tolerance.
        """
        faces_a = self._unique_subshapes(part_a, ids_a)
        faces_b = self._unique_subshapes(part_b, ids_b)
        total = len(faces_a) * len(faces_b)
        pairs = []
        step = 0
        for face_a in faces_a:
            for face_b in faces_b:
                step += 1
                if on_step is not None:
                    on_step(step, total, len(pairs))
                try:
                    overlap = self._overlap_patches(face_a, face_b, tolerance)
                except Exception as e:
                    print("Flaechenpaar uebersprungen: %s" % e)
                    continue
                if overlap is not None:
                    pairs.append(overlap)
        return pairs

    def detectContacts(self):
        if self._detecting:
            return
        if len(self.parts) < 2:
            QMessageBox.warning(self, "Warnung", "Bitte mindestens 2 Volumenkörper auswählen!")
            return

        body_pairs = [
            (i, j)
            for i in range(len(self.parts))
            for j in range(i + 1, len(self.parts))
        ]
        n_body = len(body_pairs)

        self._detecting = True
        self.btn_detect.setEnabled(False)
        self.btn_generate.setEnabled(False)
        self.btn_study.setEnabled(False)
        self.table.setRowCount(0)
        self.contacts = []
        gap = self.gap_spin.value()
        self._plane_cache = {}

        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._set_progress(0.0, "Kontaktsuche gestartet, %d Körperpaare …" % n_body)
            for pair_index, (i, j) in enumerate(body_pairs):
                name_a = self._part_label(self.parts[i], i)
                name_b = self._part_label(self.parts[j], j)
                base = float(pair_index) / n_body
                self._set_progress(
                    base,
                    "Körperpaar %d/%d: %s gegen %s (Schnittprüfung)" % (
                        pair_index + 1, n_body, name_a, name_b
                    )
                )
                try:
                    isOk, res1, res2 = geompy.FastIntersect(
                        self.parts[i], self.parts[j], gap
                    )
                except Exception as e:
                    print("Fehler bei Paar %d-%d: %s" % (i, j, e))
                    self._set_progress(
                        float(pair_index + 1) / n_body,
                        "Körperpaar %d/%d: %s gegen %s fehlgeschlagen" % (
                            pair_index + 1, n_body, name_a, name_b
                        )
                    )
                    continue

                if not (isOk > 0 and res1 and res2):
                    self._set_progress(
                        float(pair_index + 1) / n_body,
                        "Körperpaar %d/%d: %s gegen %s, keine Berührung" % (
                            pair_index + 1, n_body, name_a, name_b
                        )
                    )
                    continue

                def on_step(step, total, found, _base=base, _index=pair_index):
                    done = float(step) / total if total else 1.0
                    self._set_progress(
                        _base + done / n_body,
                        "Körperpaar %d/%d: %s gegen %s, Fläche %d/%d (%d Treffer)" % (
                            _index + 1, n_body, name_a, name_b, step, total, found
                        )
                    )

                try:
                    pairs = self._contact_face_pairs(
                        self.parts[i], self.parts[j], res1, res2, gap, on_step
                    )
                except Exception as e:
                    print("Fehler bei Paar %d-%d: %s" % (i, j, e))
                    self._set_progress(
                        float(pair_index + 1) / n_body,
                        "Körperpaar %d/%d: %s gegen %s fehlgeschlagen" % (
                            pair_index + 1, n_body, name_a, name_b
                        )
                    )
                    continue

                for n, (face_a, face_b, area) in enumerate(pairs, start=1):
                    self._set_progress(
                        float(pair_index + 1) / n_body,
                        "Körperpaar %d/%d: Gruppe %d/%d anlegen" % (
                            pair_index + 1, n_body, n, len(pairs)
                        )
                    )
                    name_i = "CONT_%d_%d_%d_A" % (i, j, n)
                    name_j = "CONT_%d_%d_%d_B" % (i, j, n)
                    geompy.addToStudyInFather(self.parts[i], face_a, name_i)
                    geompy.addToStudyInFather(self.parts[j], face_b, name_j)
                    self.contacts.append({
                        "partA": self.parts[i],
                        "partB": self.parts[j],
                        "indexA": i,
                        "indexB": j,
                        "nameA": name_a,
                        "nameB": name_b,
                        "groupA": name_i,
                        "groupB": name_j,
                        "facesA": face_a,
                        "facesB": face_b,
                        "area": area
                    })
        finally:
            self._plane_cache = {}
            self._detecting = False
            self.btn_detect.setEnabled(True)
            self.btn_generate.setEnabled(True)
            self.btn_study.setEnabled(True)
            QApplication.restoreOverrideCursor()

        found = len(self.contacts)
        self._set_progress(1.0, "Browser wird aktualisiert, %d Kontaktstellen …" % found)
        self.fillTable()
        salome.sg.updateObjBrowser()
        self.status_label.setText("Fertig. %d Kontaktstellen gefunden." % found)
        QMessageBox.information(
            self, "Ergebnis",
            "%d Kontaktstellen gefunden." % found
        )

    def _checked_rows(self):
        rows = []
        for row in range(self.table.rowCount()):
            box = self.table.cellWidget(row, 0)
            if isinstance(box, QCheckBox) and box.isChecked():
                rows.append(row)
        return rows

    def fillTable(self):
        self.table.setRowCount(len(self.contacts))
        for row, c in enumerate(self.contacts):
            box = QCheckBox()
            box.setChecked(True)
            self.table.setCellWidget(row, 0, box)
            self.table.setItem(row, 1, QTableWidgetItem(c["nameA"]))
            self.table.setItem(row, 2, QTableWidgetItem(c["nameB"]))
            self.table.setItem(row, 3, QTableWidgetItem("%.4f" % c["area"]))

            combo = QComboBox()
            combo.addItems([
                "Tie-Constraint (LIAISON_MAIL)",
                "Contact",
                "Contact with Friction",
                "Ignorieren"
            ])
            combo.setCurrentIndex(0)
            self.table.setCellWidget(row, 4, combo)

            btn = QPushButton("Vorschau")
            btn.clicked.connect(lambda checked, r=row: self.previewContact(r))
            self.table.setCellWidget(row, 5, btn)

    def onRowSelected(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.previewContact(rows[0].row())

    def _geom_gui(self):
        return salome.ImportComponentGUI("GEOM")

    def _restore_preview_bodies(self):
        """Drop the preview transparency so solids are not left see-through."""
        bodies = self._preview_bodies
        self._preview_bodies = []
        if not bodies:
            return
        try:
            gg = self._geom_gui()
        except Exception as e:
            print("Vorschau-Fehler:", e)
            return
        for part in bodies:
            try:
                entry = salome.ObjectToID(part)
                try:
                    # Keep erased solids erased; only the stored transparency changes.
                    gg.setTransparency(entry, 0.0, False)
                except TypeError:
                    gg.setTransparency(entry, 0.0)
            except Exception as e:
                print("Vorschau-Fehler:", e)

    def _display_body(self, gg, part):
        entry = salome.ObjectToID(part)
        salome.sg.Display(entry)
        # Shading with edges, half transparent, original color unchanged.
        gg.setDisplayMode(entry, 2)
        gg.setTransparency(entry, 0.5)
        return entry

    def _display_contact_face(self, gg, face, red, green, blue):
        entry = salome.ObjectToID(face)
        salome.sg.Display(entry)
        gg.setDisplayMode(entry, 2)
        gg.setTransparency(entry, 0.0)
        gg.setColor(entry, red, green, blue)
        return entry

    def previewContact(self, row):
        if row >= len(self.contacts):
            return
        c = self.contacts[row]
        try:
            gg = self._geom_gui()
            salome.sg.EraseAll()
            self._restore_preview_bodies()
            self._display_body(gg, c["partA"])
            self._display_body(gg, c["partB"])
            # Draw the contact faces after the solids so the colors stay visible.
            self._display_contact_face(gg, c["facesA"], 255, 0, 0)
            self._display_contact_face(gg, c["facesB"], 0, 255, 0)
            self._preview_bodies = [c["partA"], c["partB"]]
            salome.sg.UpdateView()
            salome.sg.FitAll()
        except Exception as e:
            print("Vorschau-Fehler:", e)

    def closeEvent(self, event):
        self._restore_preview_bodies()
        super(ContactWizard, self).closeEvent(event)

    def _missing_boundary_groups(self):
        """Names among fixed and load that are absent from the geometry."""
        import time
        missing = ["fixed", "load"]
        deadline = time.time() + 15.0
        while True:
            found = self._named_geom_shapes(("fixed", "load"))
            missing = [name for name in ("fixed", "load") if name not in found]
            if not missing:
                return []
            try:
                component = salome.myStudy.FindComponent("GEOM")
            except Exception:
                component = None
            if component is not None or time.time() >= deadline:
                return missing
            QApplication.processEvents()
            time.sleep(0.2)

    def _abort_without_boundary_groups(self):
        """Stop the wizard until the geometry contains fixed and load."""
        missing = self._missing_boundary_groups()
        if not missing:
            return False
        self._aborted = True
        QMessageBox.warning(
            self, "Gruppen fehlen",
            "Die Gruppen fixed und load müssen in der Geometrie vorhanden sein.\n"
            "Es fehlt: %s.\n"
            "Lege die Gruppen an und starte den Wizard erneut." % ", ".join(missing)
        )
        return True

    def _named_geom_shapes(self, names):
        """GEOM objects whose study name is exactly one of `names`."""
        wanted = set(names)
        found = {}
        try:
            component = salome.myStudy.FindComponent("GEOM")
        except Exception:
            component = None
        if component is None:
            return found

        def walk(sobject):
            iterator = salome.myStudy.NewChildIterator(sobject)
            while iterator.More():
                child = iterator.Value()
                yield child
                for nested in walk(child):
                    yield nested
                iterator.Next()

        for sobject in walk(component):
            name = sobject.GetName()
            if name not in wanted or name in found:
                continue
            obj = sobject.GetObject()
            if obj is None or not hasattr(obj, "GetShapeType"):
                continue
            found[name] = obj
            if len(found) == len(wanted):
                break
        return found

    def _output_directory(self):
        """Folder for the result MED, next to the study file when that path exists."""
        raw = ""
        study = salome.myStudy
        for reader in (
            lambda: study.URL,
            lambda: study._get_URL(),
            lambda: study.GetURL(),
        ):
            try:
                value = reader()
            except Exception:
                continue
            if value:
                raw = str(value)
                break
        path = ""
        if raw.startswith("file:"):
            path = unquote(urlparse(raw).path)
        else:
            path = raw
        if path and os.path.isdir(path):
            folder = os.path.join(path, "linear_study")
        elif path and os.path.isfile(path):
            folder = os.path.join(os.path.dirname(os.path.abspath(path)), "linear_study")
        else:
            folder = os.path.abspath(os.path.join(os.getcwd(), "linear_study"))
        os.makedirs(folder, exist_ok=True)
        return folder

    def _is_study_entry(self, name):
        if not name or not str(name).startswith("0:"):
            return False
        parts = str(name).split(":")
        return len(parts) >= 2 and all(part.isdigit() for part in parts)

    def _mesh_face_group(self, smesh, mesh, shape, name):
        import SMESH
        existing = mesh.GetGroupByName(name) or []
        for item in existing:
            mesh.RemoveGroup(item)
        try:
            group = mesh.GroupOnGeom(shape, name, SMESH.FACE)
            if group.Size():
                return group
            mesh.RemoveGroup(group)
        except Exception as exc:
            print("GroupOnGeom %s: %s" % (name, exc))
        # Patches smaller than one element need a tolerance on the order of
        # the element size, otherwise no face centre falls on the patch.
        for tolerance in (1e-5, 1e-4, 1e-3, 5e-3, 0.01, 0.02):
            try:
                criterion = smesh.GetCriterion(
                    SMESH.FACE, SMESH.FT_BelongToGeom, SMESH.FT_EqualTo,
                    shape, Tolerance=tolerance
                )
                group = mesh.MakeGroupByCriterion(name, criterion)
                if group.Size():
                    return group
                mesh.RemoveGroup(group)
            except Exception as exc:
                print("BelongToGeom %s tol %s: %s" % (name, tolerance, exc))
        return None

    def _mesh_volume_group(self, mesh, solid, name):
        import SMESH
        try:
            volume = mesh.GroupOnGeom(solid, name, SMESH.VOLUME)
            if volume.Size():
                return volume
            mesh.RemoveGroup(volume)
        except Exception as exc:
            print("Volumengruppe %s: %s" % (name, exc))
        volume = mesh.CreateEmptyGroup(SMESH.VOLUME, name)
        volume.Add(mesh.GetElementsByType(SMESH.VOLUME))
        if volume.Size():
            return volume
        mesh.RemoveGroup(volume)
        return None

    def _linear_comm(self, liaison_lines):
        body = "\n".join(liaison_lines)
        return """DEBUT(LANG='EN')

mesh = LIRE_MAILLAGE(UNITE=20, FORMAT='MED')

mesh = DEFI_GROUP(
    reuse=mesh,
    MAILLAGE=mesh,
    CREA_GROUP_NO=_F(NOM='fixed_n', GROUP_MA='fixed'),
)

model = AFFE_MODELE(
    MAILLAGE=mesh,
    AFFE=_F(TOUT='OUI', PHENOMENE='MECANIQUE', MODELISATION='3D'),
)

steel = DEFI_MATERIAU(ELAS=_F(E=2.1e11, NU=0.3))

material = AFFE_MATERIAU(MAILLAGE=mesh, AFFE=_F(TOUT='OUI', MATER=steel))

load = AFFE_CHAR_MECA(
    MODELE=model,
    FORCE_FACE=_F(GROUP_MA='load', FX=1.0),
)

boundary = AFFE_CHAR_MECA(
    MODELE=model,
    DDL_IMPO=_F(GROUP_MA='fixed', DX=0.0, DY=0.0, DZ=0.0),
)

ties = AFFE_CHAR_MECA(
    MODELE=model,
    LIAISON_MAIL=(
%s
    ),
)

result = MECA_STATIQUE(
    MODELE=model,
    CHAM_MATER=material,
    EXCIT=(
        _F(CHARGE=load),
        _F(CHARGE=boundary),
        _F(CHARGE=ties),
    ),
)

result = CALC_CHAMP(
    reuse=result,
    RESULTAT=result,
    CRITERES=('SIEQ_NOEU',),
    FORCE=('REAC_NODA',),
)

depl_table = POST_RELEVE_T(
    ACTION=_F(OPERATION='EXTREMA', INTITULE='depl', RESULTAT=result,
              NOM_CHAM='DEPL', TOUT='OUI'),
)
vmis_table = POST_RELEVE_T(
    ACTION=_F(OPERATION='EXTREMA', INTITULE='vmis', RESULTAT=result,
              NOM_CHAM='SIEQ_NOEU', NOM_CMP='VMIS', TOUT='OUI'),
)
reac_table = POST_RELEVE_T(
    ACTION=_F(OPERATION='EXTRACTION', INTITULE='reaction', RESULTAT=result,
              NOM_CHAM='REAC_NODA', GROUP_NO='fixed_n',
              RESULTANTE=('DX', 'DY', 'DZ'),
              MOMENT=('DRX', 'DRY', 'DRZ'),
              POINT=(0.0, 0.0, 0.0)),
)
IMPR_TABLE(TABLE=depl_table)
IMPR_TABLE(TABLE=vmis_table)
IMPR_TABLE(TABLE=reac_table)

IMPR_RESU(
    FORMAT='MED',
    UNITE=80,
    RESU=(
        _F(RESULTAT=result, NOM_CHAM='DEPL'),
        _F(RESULTAT=result, NOM_CHAM='SIEQ_NOEU', NOM_CMP='VMIS'),
    ),
)

FIN()
""" % body

    def _liaison_lines(self, contacts):
        distance = self.gap_spin.value()
        ties = {}
        for contact in contacts:
            ties.setdefault((contact["indexA"], contact["indexB"]), []).append(contact["groupA"])
        lines = []
        for (_index_a, index_b), slaves in ties.items():
            if len(slaves) == 1:
                slave = "'%s'" % slaves[0]
            else:
                slave = "(%s)" % ", ".join("'%s'" % name for name in slaves)
            lines.append(
                "    _F(GROUP_MA_ESCL = %s,\n"
                "       GROUP_MA_MAIT = 'VOL_%d',\n"
                "       DISTANCE_MAX = %.6g,\n"
                "       TYPE_RACCORD = 'MASSIF')," % (slave, index_b, distance)
            )
        return lines

    def _open_aster_study(self):
        """Switch to AsterStudy through Salome and return its GUI object.

        Calling ASTERSTUDYGUI.initialize() directly builds the toolbars
        outside the module loader and crashes. activateModule is the path
        AsterStudy itself uses to open a module.
        """
        import SalomePyQt
        desktop = SalomePyQt.SalomePyQt()
        module_name = "AsterStudy"
        try:
            import libSALOME_Swig
            looked_up = libSALOME_Swig.SALOMEGUI_Swig().getComponentUserName("ASTERSTUDY")
            if looked_up:
                module_name = looked_up
        except Exception as exc:
            print("Modulname: %s" % exc)
        try:
            desktop.activateModule(module_name)
        except Exception as exc:
            print("AsterStudy öffnen: %s" % exc)
            return None
        QApplication.processEvents()
        import ASTERSTUDYGUI
        gui = ASTERSTUDYGUI.get_aster_gui()
        for _ in range(50):
            if gui.study() is not None:
                break
            QApplication.processEvents()
            import time
            time.sleep(0.1)
        if gui.study() is None:
            return None
        return gui

    def _install_aster_case(self, comm, mesh_ref, result_path):
        gui = self._open_aster_study()
        if gui is None:
            raise RuntimeError("AsterStudy ist nicht geöffnet.")
        study = gui.study()
        history = study.history
        case = history.current_case
        if case is None or case.nb_stages:
            case = history.create_case(name="LineareStudie", replace=False)
        else:
            try:
                names = [node.name for node in history.cases]
                if case.name != "LineareStudie" and "LineareStudie" not in names:
                    case.rename("LineareStudie")
            except Exception as exc:
                print("Fallname: %s" % exc)
        stage = study.importStageFromText(
            comm, "linear", force_text=True,
            files={20: mesh_ref, 80: result_path}
        )
        if stage is None:
            raise RuntimeError("Die Stufe wurde nicht angelegt.")
        from asterstudy.datamodel.general import FileAttr
        info_mesh = stage.handle2info[20]
        info_mesh.filename = mesh_ref
        if not (info_mesh.attr & FileAttr.In):
            info_mesh.attr = FileAttr.In
        info_result = stage.handle2info[80]
        info_result.filename = result_path
        if not (info_result.attr & FileAttr.Out):
            info_result.attr = FileAttr.Out
        current = history.current_case
        current.job_infos.set("memory", 4096)
        current.job_infos.set("time", 1800)
        try:
            from asterstudy.gui import Context
            gui.update(autoSelect=stage, context=Context.DataSettings, autoExpand=True)
        except Exception as exc:
            print("Ansicht: %s" % exc)
        return current

    def _write_linear_files(self, folder, comm, compound):
        comm_path = os.path.join(folder, "linear.comm")
        mesh_path = os.path.join(folder, "mesh.med")
        with open(comm_path, "w", encoding="utf-8") as handle:
            handle.write(comm)
        compound.ExportMED(mesh_path, 0, 40, 1)
        return comm_path, mesh_path

    def createLinearStudy(self):
        """Mesh the parts and add a runnable linear static case for the selected ties."""
        if self._detecting or self._building_study:
            return
        groups = self._named_geom_shapes(("fixed", "load"))
        missing = [name for name in ("fixed", "load") if name not in groups]
        if missing:
            QMessageBox.warning(
                self, "Gruppen fehlen",
                "Die lineare Studie braucht die Geometriegruppen fixed und load.\n"
                "Es fehlt: %s." % ", ".join(missing)
            )
            return
        if not self.parts:
            QMessageBox.warning(
                self, "Keine Körper",
                "Es sind keine Volumenkörper geladen."
            )
            return

        rows = self._checked_rows()
        if not rows:
            QMessageBox.information(
                self, "Keine Auswahl",
                "Bitte die Kontakte in der Spalte Übernehmen anhaken, die in die lineare Studie sollen."
            )
            return

        ties = []
        ignored = []
        for row in rows:
            if row < 0 or row >= len(self.contacts):
                continue
            combo = self.table.cellWidget(row, 4)
            typ = combo.currentText() if combo is not None else ""
            contact = self.contacts[row]
            if "Tie" in typ:
                ties.append(contact)
            else:
                ignored.append("%s/%s (%s)" % (contact["groupA"], contact["groupB"], typ))
        if not ties:
            QMessageBox.warning(
                self, "Kein Tie-Constraint",
                "Die Auswahl enthält keinen Tie-Constraint.\n"
                "Die lineare Studie koppelt die Körper mit LIAISON_MAIL."
            )
            return

        self._building_study = True
        self.btn_detect.setEnabled(False)
        self.btn_generate.setEnabled(False)
        self.btn_study.setEnabled(False)
        cursor = False
        try:
            from salome.smesh import smeshBuilder
            QApplication.setOverrideCursor(Qt.WaitCursor)
            cursor = True
            smesh = smeshBuilder.New()
            max_size = 0.01
            min_size = min(0.002, max_size * 0.2)
            part_meshes = []
            n_parts = len(self.parts)
            for index, solid in enumerate(self.parts):
                self._set_progress(
                    0.7 * index / n_parts,
                    "Vernetzung Körper %d/%d, max. Größe %.3g" % (index + 1, n_parts, max_size)
                )
                mesh = smesh.Mesh(solid, "wizard_solid_%d" % index)
                algo = mesh.Tetrahedron(algo=smeshBuilder.NETGEN_1D2D3D)
                params = algo.Parameters()
                params.SetMaxSize(max_size)
                params.SetMinSize(min_size)
                params.SetSecondOrder(False)
                if not mesh.Compute():
                    detail = ""
                    try:
                        detail = "\n" + str(mesh.GetLog())
                    except Exception:
                        pass
                    QMessageBox.critical(
                        self, "Vernetzung fehlgeschlagen",
                        "Körper %d (%s) ließ sich nicht vernetzen.%s" % (
                            index, self._part_label(solid, index), detail
                        )
                    )
                    return
                volume = self._mesh_volume_group(mesh, solid, "VOL_%d" % index)
                if volume is None:
                    QMessageBox.critical(
                        self, "Volumengruppe leer",
                        "Die Volumengruppe VOL_%d ist leer." % index
                    )
                    return
                part_meshes.append(mesh)

            self._set_progress(0.72, "Gruppen fixed und load übernehmen")
            for name in ("fixed", "load"):
                chosen = None
                for mesh in part_meshes:
                    group = self._mesh_face_group(smesh, mesh, groups[name], name)
                    if group is not None and group.Size():
                        chosen = group
                        break
                    for item in mesh.GetGroupByName(name) or []:
                        mesh.RemoveGroup(item)
                if chosen is None:
                    QMessageBox.critical(
                        self, "Netzgruppe leer",
                        "Die Geometriegruppe %s ist vorhanden, das Netz enthält davon keine Flächen." % name
                    )
                    return

            kept = []
            skipped = []
            n_ties = len(ties)
            for tie_index, contact in enumerate(ties):
                self._set_progress(
                    0.75 + 0.15 * tie_index / n_ties,
                    "Kontaktgruppe %d/%d: %s" % (tie_index + 1, n_ties, contact["groupA"])
                )
                sides = (
                    (part_meshes[contact["indexA"]], contact["facesA"], contact["groupA"]),
                    (part_meshes[contact["indexB"]], contact["facesB"], contact["groupB"]),
                )
                usable = True
                for mesh, shape, name in sides:
                    group = self._mesh_face_group(smesh, mesh, shape, name)
                    if group is None or not group.Size():
                        usable = False
                        break
                if usable:
                    kept.append(contact)
                else:
                    skipped.append("%s/%s" % (contact["groupA"], contact["groupB"]))
                    # Drop a one-sided group. It is not used in LIAISON_MAIL.
                    for mesh, _shape, name in sides:
                        for item in mesh.GetGroupByName(name) or []:
                            mesh.RemoveGroup(item)
            if not kept:
                QMessageBox.warning(
                    self, "Keine koppelbare Kontaktstelle",
                    "Keine ausgewählte Kontaktstelle hat eine Netzgruppe bekommen.\n"
                    "Ausgelassen: %s" % ", ".join(skipped)
                )
                return

            self._set_progress(0.92, "Netze werden zusammengefügt")
            compound = smesh.Concatenate(
                part_meshes, True, False, 1e-5, True, "linear_assembly"
            )
            names = list(compound.GetGroupNames())
            needed = ["fixed", "load"]
            for contact in kept:
                needed.extend([contact["groupA"], "VOL_%d" % contact["indexB"]])
            absent = [name for name in needed if name not in names]
            if absent:
                QMessageBox.critical(
                    self, "Gruppen fehlen im Gesamtnetz",
                    "Im zusammengefügten Netz fehlen: %s." % ", ".join(absent)
                )
                return

            folder = self._output_directory()
            result_path = os.path.join(folder, "result.med")
            comm = self._linear_comm(self._liaison_lines(kept))
            entry = ""
            try:
                entry = salome.ObjectToID(compound.GetMesh())
            except Exception as exc:
                print("Netzeintrag: %s" % exc)
            if not self._is_study_entry(entry):
                try:
                    sobject = salome.myStudy.FindObject(compound.GetMesh())
                    if sobject is not None:
                        entry = sobject.GetID()
                except Exception as exc:
                    print("Netzeintrag: %s" % exc)
            mesh_ref = entry if self._is_study_entry(entry) else ""
            if not mesh_ref:
                _comm_path, mesh_ref = self._write_linear_files(folder, comm, compound)
            else:
                comm_path = os.path.join(folder, "linear.comm")
                with open(comm_path, "w", encoding="utf-8") as handle:
                    handle.write(comm)

            if cursor:
                QApplication.restoreOverrideCursor()
                cursor = False
            self._set_progress(0.97, "AsterStudy-Fall wird angelegt")
            try:
                case = self._install_aster_case(comm, mesh_ref, result_path)
                case_name = case.name
            except Exception as exc:
                comm_path, mesh_path = self._write_linear_files(folder, comm, compound)
                QMessageBox.warning(
                    self, "AsterStudy",
                    "Der Fall ließ sich in AsterStudy nicht anlegen (%s).\n"
                    "Kommando-Datei: %s\n"
                    "Netz: %s" % (exc, comm_path, mesh_path)
                )
                self._set_progress(1.0, "Kommando-Datei und Netz geschrieben.")
                return

            salome.sg.updateObjBrowser()
            lines = [
                "Der Fall %s ist angelegt und kann in AsterStudy gestartet werden." % case_name,
                "Netz: %s" % mesh_ref,
                "Ergebnisdatei: %s" % result_path,
                "Speicher 4096 MB, Zeitlimit 1800 s.",
                "Gekoppelte Kontaktstellen: %d." % len(kept),
            ]
            if skipped:
                lines.append(
                    "Ohne Netzgruppe und deshalb ohne Kopplung: %s." % ", ".join(skipped)
                )
            if ignored:
                lines.append(
                    "Ausgewählt, aber kein Tie-Constraint: %s." % ", ".join(ignored)
                )
            self._set_progress(1.0, "Lineare Studie %s angelegt." % case_name)
            QMessageBox.information(self, "Lineare Studie", "\n".join(lines))
        except Exception as exc:
            QMessageBox.critical(self, "Lineare Studie", str(exc))
        finally:
            self._building_study = False
            self.btn_detect.setEnabled(True)
            self.btn_generate.setEnabled(True)
            self.btn_study.setEnabled(True)
            if cursor:
                QApplication.restoreOverrideCursor()

    def generateCode(self):
        liaison = []
        contact = []
        friction = []

        ties = {}
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, 4)
            typ = combo.currentText()
            c = self.contacts[row]

            if "Tie" in typ:
                # One relation per body pair. In 3D the master has to be the
                # volume next to the contact face. A repeated slave node in a
                # second relation makes the stiffness matrix singular.
                ties.setdefault((c["indexA"], c["indexB"]), []).append(c["groupA"])
            elif typ == "Contact":
                contact.append(
                    "    _F(GROUP_MA_ESCL = '%s',\n"
                    "       GROUP_MA_MAIT = '%s',\n"
                    "       CONTACT_INIT = 'OUI')," % (c["groupA"], c["groupB"])
                )
            elif "Friction" in typ:
                friction.append(
                    "    _F(GROUP_MA_ESCL = '%s',\n"
                    "       GROUP_MA_MAIT = '%s',\n"
                    "       CONTACT_INIT = 'OUI',\n"
                    "       COULOMB = 0.2)," % (c["groupA"], c["groupB"])
                )

        distance = self.gap_spin.value()
        for (_index_a, index_b), slaves in ties.items():
            if len(slaves) == 1:
                slave = "'%s'" % slaves[0]
            else:
                slave = "(%s)" % ", ".join("'%s'" % name for name in slaves)
            liaison.append(
                "    _F(GROUP_MA_ESCL = %s,\n"
                "       GROUP_MA_MAIT = 'VOL_%d',\n"
                "       DISTANCE_MAX = %.6g,\n"
                "       TYPE_RACCORD = 'MASSIF')," % (slave, index_b, distance)
            )

        code = self.build_comm(liaison, contact, friction)

        dlg = QDialog(self)
        dlg.setWindowTitle("Generierter Code für AsterStudy")
        dlg.resize(820, 580)
        v = QVBoxLayout(dlg)
        txt = QTextEdit()
        txt.setPlainText(code)
        txt.setReadOnly(True)
        v.addWidget(txt)
        btn = QPushButton("Schließen")
        btn.clicked.connect(dlg.accept)
        v.addWidget(btn)
        dlg.exec_()

        try:
            with open("aster_contacts.comm", "w", encoding="utf-8") as f:
                f.write(code)
            QMessageBox.information(
                self, "Gespeichert",
                "Code wurde als aster_contacts.comm gespeichert.\nIn AsterStudy einfügen."
            )
        except Exception as e:
            QMessageBox.warning(self, "Speichern fehlgeschlagen", str(e))

    def build_comm(self, liaison, contact, friction):
        lines = ["# === Automatisch generierte Kontaktdefinitionen (v0.0.1) ===", ""]
        if liaison:
            lines.append("# Tie-Constraints (in AFFE_CHAR_MECA einfügen)")
            lines.append("LIAISON_MAIL = (")
            lines.extend(liaison)
            lines.append("),")
            lines.append("")
        if contact or friction:
            lines.append("# DEFI_CONTACT")
            lines.append("contact = DEFI_CONTACT(")
            lines.append("    FORMULATION = 'CONTINUE',")
            lines.append("    ZONE = (")
            lines.extend(contact)
            lines.extend(friction)
            lines.append("    ),")
            lines.append("),")
            lines.append("")
        lines.append("# Hinweis: Sklavengruppen sind die Kontaktflächen.")
        lines.append("# VOL_<i> muss die Volumengruppe des Körpers i sein (3D-Master von LIAISON_MAIL).")
        lines.append("# Gruppen nach dem Meshing mit 'Create Groups from Geometry' übernehmen.")
        return "\n".join(lines)


if __name__ == "__main__":
    wizard = ContactWizard()
    if not wizard._aborted:
        wizard.show()
