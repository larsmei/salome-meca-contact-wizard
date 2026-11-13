# -*- coding: utf-8 -*-
"""
Salome-Meca Contact Wizard v0.0.1
Automatic contact detection + Code_Aster constraint generation

Based on the original 3DContact.py by Lucio Gomez (psicofil)
"""

import salome
salome.salome_init()
from salome.geom import geomBuilder
import GEOM

try:
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
        QDoubleSpinBox, QTableWidget, QTableWidgetItem, QComboBox,
        QAbstractItemView, QDialog, QTextEdit, QMessageBox
    )
    from PyQt5.QtCore import Qt
except ImportError:
    from PyQt4.QtGui import (
        QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
        QDoubleSpinBox, QTableWidget, QTableWidgetItem, QComboBox,
        QAbstractItemView, QDialog, QTextEdit, QMessageBox
    )
    from PyQt4.QtCore import Qt

geompy = geomBuilder.New(salome.myStudy)


class ContactWizard(QWidget):
    def __init__(self):
        super(ContactWizard, self).__init__()
        self.parts = []
        self.contacts = []
        self.initUI()
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

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Teil A", "Teil B", "Fläche", "Typ", "Aktion"
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemSelectionChanged.connect(self.onRowSelected)
        layout.addWidget(self.table)

        # Bottom
        bottom = QHBoxLayout()
        self.btn_generate = QPushButton("Code für AsterStudy generieren")
        self.btn_generate.clicked.connect(self.generateCode)
        self.btn_close = QPushButton("Schließen")
        self.btn_close.clicked.connect(self.close)
        bottom.addWidget(self.btn_generate)
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

    def detectContacts(self):
        if len(self.parts) < 2:
            QMessageBox.warning(self, "Warnung", "Bitte mindestens 2 Volumenkörper auswählen!")
            return

        self.table.setRowCount(0)
        self.contacts = []
        gap = self.gap_spin.value()

        for i in range(len(self.parts)):
            for j in range(i + 1, len(self.parts)):
                try:
                    isOk, res1, res2 = geompy.FastIntersect(
                        self.parts[i], self.parts[j], gap
                    )
                    if isOk > 0 and res1 and res2:
                        faces_i = geompy.SubShapes(self.parts[i], res1)
                        faces_j = geompy.SubShapes(self.parts[j], res2)

                        valid = []
                        n = min(len(faces_i), len(faces_j))
                        for k in range(n):
                            common = geompy.MakeCommon(faces_i[k], faces_j[k])
                            area = geompy.BasicProperties(common)[1]
                            if area > 1e-8:
                                valid.append((faces_i[k], faces_j[k], area))

                        if valid:
                            comp_i = geompy.MakeCompound([v[0] for v in valid])
                            comp_j = geompy.MakeCompound([v[1] for v in valid])
                            name_i = "CONT_%d_%d_A" % (i, j)
                            name_j = "CONT_%d_%d_B" % (i, j)
                            geompy.addToStudyInFather(self.parts[i], comp_i, name_i)
                            geompy.addToStudyInFather(self.parts[j], comp_j, name_j)

                            total_area = sum(v[2] for v in valid)
                            self.contacts.append({
                                "partA": self.parts[i],
                                "partB": self.parts[j],
                                "nameA": self.parts[i].GetName() or ("Part_%d" % i),
                                "nameB": self.parts[j].GetName() or ("Part_%d" % j),
                                "groupA": name_i,
                                "groupB": name_j,
                                "facesA": comp_i,
                                "facesB": comp_j,
                                "area": total_area
                            })
                except Exception as e:
                    print("Fehler bei Paar %d-%d: %s" % (i, j, e))

        self.fillTable()
        salome.sg.updateObjBrowser()
        QMessageBox.information(self, "Ergebnis", "%d Kontaktpaare gefunden." % len(self.contacts))

    def fillTable(self):
        self.table.setRowCount(len(self.contacts))
        for row, c in enumerate(self.contacts):
            self.table.setItem(row, 0, QTableWidgetItem(c["nameA"]))
            self.table.setItem(row, 1, QTableWidgetItem(c["nameB"]))
            self.table.setItem(row, 2, QTableWidgetItem("%.4f" % c["area"]))

            combo = QComboBox()
            combo.addItems([
                "Tie-Constraint (LIAISON_MAIL)",
                "Contact",
                "Contact with Friction",
                "Ignorieren"
            ])
            combo.setCurrentIndex(0)
            self.table.setCellWidget(row, 3, combo)

            btn = QPushButton("Vorschau")
            btn.clicked.connect(lambda checked, r=row: self.previewContact(r))
            self.table.setCellWidget(row, 4, btn)

    def onRowSelected(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.previewContact(rows[0].row())

    def previewContact(self, row):
        if row >= len(self.contacts):
            return
        c = self.contacts[row]
        try:
            salome.sg.EraseAll()
            for part in (c["partA"], c["partB"]):
                entry = salome.ObjectToID(part)
                salome.sg.Display(entry)
            entryA = salome.ObjectToID(c["facesA"])
            entryB = salome.ObjectToID(c["facesB"])
            salome.sg.Display(entryA)
            salome.sg.Display(entryB)
            salome.sg.FitAll()
        except Exception as e:
            print("Vorschau-Fehler:", e)

    def generateCode(self):
        liaison = []
        contact = []
        friction = []

        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, 3)
            typ = combo.currentText()
            c = self.contacts[row]

            if "Tie" in typ:
                liaison.append(
                    "    _F(GROUP_MA_ESCL = '%s',\n"
                    "       GROUP_MA_MAIT = '%s',\n"
                    "       TYPE_RACCORD = 'MASSIF')," % (c["groupA"], c["nameB"])
                )
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
        lines.append("# Hinweis: Gruppen müssen nach dem Meshing mit 'Create Groups from Geometry' übernommen werden.")
        return "\n".join(lines)


if __name__ == "__main__":
    wizard = ContactWizard()
    wizard.show()
