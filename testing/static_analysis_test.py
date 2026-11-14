# -*- coding: utf-8 -*-
"""Contact search at 0.02, tet mesh, AsterStudy linear statics, screenshots."""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = "/tmp/contact_static"
SHOT = os.path.join(ROOT, "testing", "screenshots")
LOG_PATH = "/tmp/static_analysis.log"
DONE_PATH = "/tmp/static_analysis.done"
MAX_SIZE = 0.01
TOLERANCE = 0.02

os.makedirs(WORK, exist_ok=True)
os.makedirs(SHOT, exist_ok=True)
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
    raise SystemExit(code)


try:
    if os.path.exists(DONE_PATH):
        os.remove(DONE_PATH)
    os.chdir(WORK)

    import salome
    salome.salome_init()
    from PyQt5.QtWidgets import QApplication, QMessageBox, QDialog

    app = QApplication.instance() or QApplication([])

    def noop_box(*args, **kwargs):
        text = args[2] if len(args) > 2 else args
        log("Dialog unterdrueckt: %s" % text)
        return QMessageBox.Ok

    QMessageBox.information = noop_box
    QMessageBox.warning = noop_box
    QMessageBox.critical = noop_box
    QDialog.exec_ = lambda *args, **kwargs: 0

    sys.path.insert(0, ROOT)
    import contact_wizard
    contact_wizard.QMessageBox.information = noop_box
    contact_wizard.QMessageBox.warning = noop_box
    contact_wizard.QMessageBox.critical = noop_box
    geompy = contact_wizard.geompy

    def shape_kind(value):
        name = str(value).upper()
        for token in ("COMPSOLID", "COMPOUND", "SOLID", "SHELL", "FACE", "WIRE", "EDGE", "VERTEX"):
            if name == token or name.endswith("." + token) or name.endswith("::" + token):
                return token
        return name

    component = None
    deadline = time.time() + 60.0
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
    special = {}
    for sobject in walk(component):
        obj = sobject.GetObject()
        if obj is None or not hasattr(obj, "GetShapeType"):
            continue
        try:
            kind = shape_kind(obj.GetShapeType())
        except Exception:
            continue
        name = sobject.GetName()
        if name in ("fixed", "load"):
            father = salome.myStudy.GetFather(sobject) if hasattr(salome.myStudy, "GetFather") else None
            special[name] = obj
            log("Gruppe %s Typ %s Eintrag %s" % (name, kind, sobject.GetID()))
        if kind == "SOLID" and sobject.GetID() not in seen:
            seen.add(sobject.GetID())
            solids.append(obj)
            log("Solid %d %s" % (len(solids) - 1, name))

    if "fixed" not in special or "load" not in special:
        log("Gruppe fixed oder load fehlt: %s" % sorted(special))
        finish(3)
    if len(solids) < 2:
        log("Zu wenige Solids.")
        finish(3)

    original_progress = contact_wizard.ContactWizard._set_progress

    def logged_progress(self, fraction, text):
        if int(round(fraction * 1000)) % 50 == 0 or "fehlgeschlagen" in text or "Keine" in text:
            log("Fortschritt %3.0f%% %s" % (100.0 * fraction, text))
        return original_progress(self, fraction, text)

    contact_wizard.ContactWizard._set_progress = logged_progress
    wizard = contact_wizard.ContactWizard()
    wizard.parts = solids
    wizard.gap_spin.setValue(TOLERANCE)
    wizard.detectContacts()
    log("Kontaktstellen bei %.3f: %d" % (TOLERANCE, len(wizard.contacts)))
    for index, contact in enumerate(wizard.contacts):
        log(
            "Kontakt %d %s / %s Flaeche %.6g %s %s"
            % (
                index,
                contact["nameA"],
                contact["nameB"],
                contact["area"],
                contact["groupA"],
                contact["groupB"],
            )
        )

    parent = {id(part): id(part) for part in solids}

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for contact in wizard.contacts:
        left = find(id(contact["partA"]))
        right = find(id(contact["partB"]))
        if left != right:
            parent[right] = left
    components = {}
    for index, part in enumerate(solids):
        components.setdefault(find(id(part)), []).append("%d:%s" % (index, part.GetName()))
    log("Zusammenhangskomponenten: %d" % len(components))
    for members in components.values():
        log("  Komponente: %s" % ", ".join(members))
    if len(components) != 1:
        log("Nicht alle Koerper sind verbunden.")
        finish(4)

    wizard.generateCode()
    generated = open(os.path.join(WORK, "aster_contacts.comm"), encoding="utf-8").read()
    log("--- erzeugter Kontaktcode ---")
    log(generated)
    for contact in wizard.contacts:
        master = "GROUP_MA_MAIT = 'VOL_%d'" % contact["indexB"]
        if master not in generated:
            log("Volumen-Master fehlt: %s" % master)
            finish(5)
        if contact["groupA"] not in generated:
            log("Sklavengruppe fehlt: %s" % contact["groupA"])
            finish(5)
        for wrong in (
            "GROUP_MA_MAIT = '%s'" % contact["groupB"],
            "GROUP_MA_MAIT = '%s'" % contact["nameB"],
        ):
            if wrong in generated:
                log("Mastergruppe ist keine Volumengruppe: %s" % wrong)
                finish(5)
        if "DISTANCE_MAX" not in generated:
            log("DISTANCE_MAX fehlt.")
            finish(5)
    start = generated.index("LIAISON_MAIL = (") + len("LIAISON_MAIL = (")
    end = generated.index("),", start)
    liaison_body = generated[start:end].strip()
    if not liaison_body:
        log("LIAISON_MAIL ist leer.")
        finish(5)

    import SMESH
    from salome.smesh import smeshBuilder
    smesh = smeshBuilder.New()
    part_meshes = []
    for index, solid in enumerate(solids):
        log("Vernetzung Solid %d beginnt" % index)
        mesh = smesh.Mesh(solid, "solid_%d" % index)
        algo = mesh.Tetrahedron(algo=smeshBuilder.NETGEN_1D2D3D)
        params = algo.Parameters()
        params.SetMaxSize(MAX_SIZE)
        params.SetMinSize(min(0.002, MAX_SIZE * 0.2))
        params.SetSecondOrder(False)
        if not mesh.Compute():
            log("Netz Solid %d fehlgeschlagen" % index)
            try:
                log(mesh.GetLog())
            except Exception as exc:
                log(exc)
            finish(6)
        log(
            "Netz Solid %d Knoten %d Tetra %d"
            % (index, mesh.NbNodes(), mesh.NbTetras())
        )
        volume = mesh.GroupOnGeom(solid, "VOL_%d" % index, SMESH.VOLUME)
        if not volume.Size():
            mesh.RemoveGroup(volume)
            volume = mesh.CreateEmptyGroup(SMESH.VOLUME, "VOL_%d" % index)
            volume.Add(mesh.GetElementsByType(SMESH.VOLUME))
        log("VOL_%d %d" % (index, volume.Size()))
        if not volume.Size():
            log("Volumengruppe VOL_%d ist leer." % index)
            finish(6)
        part_meshes.append(mesh)

    def face_group(mesh, shape, name):
        existing = mesh.GetGroupByName(name) or []
        for item in existing:
            mesh.RemoveGroup(item)
        try:
            group = mesh.GroupOnGeom(shape, name, SMESH.FACE)
            size = group.Size()
            log("GroupOnGeom %s: %d" % (name, size))
            if size:
                return group
            mesh.RemoveGroup(group)
        except Exception as exc:
            log("GroupOnGeom %s fehlgeschlagen: %s" % (name, exc))
        # Patches smaller than one element need a tolerance on the order of
        # the element size, otherwise no face centre falls on the patch.
        for tolerance in (1e-5, 1e-4, 1e-3, 5e-3, 0.01, 0.02):
            try:
                criterion = smesh.GetCriterion(
                    SMESH.FACE, SMESH.FT_BelongToGeom, SMESH.FT_EqualTo, shape, Tolerance=tolerance
                )
                group = mesh.MakeGroupByCriterion(name, criterion)
                size = group.Size()
                log("BelongToGeom %s tol %s: %d" % (name, tolerance, size))
                if size:
                    return group
                mesh.RemoveGroup(group)
            except Exception as exc:
                log("BelongToGeom %s tol %s fehlgeschlagen: %s" % (name, tolerance, exc))
        return None

    for name, shape in special.items():
        # The face belongs to exactly one of the meshed solids.
        chosen = None
        for mesh in part_meshes:
            group = face_group(mesh, shape, name)
            if group is not None and group.Size():
                chosen = group
                break
            empty = mesh.GetGroupByName(name)
            for item in empty or []:
                mesh.RemoveGroup(item)
        if chosen is None:
            log("Netzgruppe %s ist leer." % name)
            finish(6)

    kept = []
    for contact in wizard.contacts:
        sides = (
            (part_meshes[solids.index(contact["partA"])], contact["partA"], contact["facesA"], contact["groupA"]),
            (part_meshes[solids.index(contact["partB"])], contact["partB"], contact["facesB"], contact["groupB"]),
        )
        usable = True
        for mesh, part, shape, name in sides:
            group = face_group(mesh, shape, name)
            if group is None or not group.Size():
                distance = float(contact_wizard.geompy.MinDistance(shape, part))
                log(
                    "Kontaktgruppe leer: %s Abstand zum Koerper %.6g Flaeche %.6g"
                    % (name, distance, contact["area"])
                )
                usable = False
                break
        if usable:
            kept.append(contact)
        else:
            log("Kontakt %s/%s wird nicht gekoppelt." % (contact["groupA"], contact["groupB"]))
    if not kept:
        log("Kein Kontakt liess sich vernetzen.")
        finish(6)
    mesh_parent = {id(part): id(part) for part in solids}

    def find_mesh(item):
        while mesh_parent[item] != item:
            mesh_parent[item] = mesh_parent[mesh_parent[item]]
            item = mesh_parent[item]
        return item

    for contact in kept:
        left = find_mesh(id(contact["partA"]))
        right = find_mesh(id(contact["partB"]))
        if left != right:
            mesh_parent[right] = left
    mesh_components = {}
    for part in solids:
        mesh_components.setdefault(find_mesh(id(part)), []).append(part.GetName())
    log("Vernetzte Zusammenhangskomponenten: %d" % len(mesh_components))
    for members in mesh_components.values():
        log("  %s" % ", ".join(members))
    if len(mesh_components) != 1:
        log("Die vernetzbaren Kontakte verbinden nicht alle Koerper.")
        finish(6)
    pair_slaves = {}
    for contact in kept:
        pair_slaves.setdefault(contact["indexB"], []).append(contact["groupA"])
        if contact["groupA"] not in generated:
            log("Wizard-Code enthaelt den Kontakt %s nicht." % contact["groupA"])
            finish(5)
    kept_blocks = []
    for index_b, slaves in pair_slaves.items():
        if len(slaves) == 1:
            slave = "'%s'" % slaves[0]
        else:
            slave = "(%s)" % ", ".join("'%s'" % name for name in slaves)
        kept_blocks.append(
            "    _F(GROUP_MA_ESCL = %s,\n"
            "       GROUP_MA_MAIT = 'VOL_%d',\n"
            "       DISTANCE_MAX = %.6g,\n"
            "       TYPE_RACCORD = 'MASSIF')," % (slave, index_b, TOLERANCE)
        )
    liaison_body = "\n".join(kept_blocks)
    log("Gekoppelte Kontakte: %d, ausgelassen: %d" % (len(kept), len(wizard.contacts) - len(kept)))

    compound = smesh.Concatenate(
        [mesh.GetMesh() for mesh in part_meshes],
        True,
        False,
        1e-5,
        True,
        "assembly",
    )
    names = list(compound.GetGroupNames())
    log("Zusammengesetztes Netz Knoten %d Tetra %d Gruppen %d" % (
        compound.NbNodes(), compound.NbTetras(), len(names)
    ))
    log("Gruppen: %s" % ", ".join(names))
    needed = ["fixed", "load"]
    for contact in kept:
        needed.extend([contact["groupA"], "VOL_%d" % contact["indexB"]])
    missing = [name for name in needed if name not in names]
    if missing:
        log("Gruppen fehlen im Gesamtnetz: %s" % ", ".join(missing))
        finish(6)

    mesh_path = os.path.join(WORK, "mesh.med")
    result_path = os.path.join(WORK, "result.med")
    compound.ExportMED(mesh_path, 0, 40, 1)
    log("MED geschrieben %s (%d Bytes)" % (mesh_path, os.path.getsize(mesh_path)))

    comm = """DEBUT(LANG='EN')

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
""" % liaison_body
    comm_path = os.path.join(WORK, "study.comm")
    with open(comm_path, "w", encoding="utf-8") as handle:
        handle.write(comm)
    log("Kommando-Datei %s" % comm_path)

    from asterstudy.api import Calculation, FileAttr, StateOptions
    case = Calculation(WORK)
    case.add_stage_from_string(comm)
    case.add_file(mesh_path, 20, FileAttr.In)
    case.add_file(result_path, 80, FileAttr.Out)
    case.set("memory", 4096)
    case.set("time", 1800)
    try:
        case.set("no_database", True)
    except Exception as exc:
        log("no_database: %s" % exc)
    log("AsterStudy-Lauf startet")
    case.run()
    log("Aster-Status: %s" % case.state_name)
    for path in case.logfiles() or []:
        log("Logdatei %s" % path)
    if not (case.state & StateOptions.Success):
        log("Berechnung fehlgeschlagen.")
        finish(7)
    if not os.path.exists(result_path) or os.path.getsize(result_path) < 1000:
        log("Ergebnisdatei fehlt.")
        finish(7)
    log("Ergebnis %s (%d Bytes)" % (result_path, os.path.getsize(result_path)))

    # Screenshots of displacement magnitude and von Mises stress.
    os.environ["PV_OFFSCREEN"] = "1"
    from paraview.simple import (
        MEDReader, GetActiveViewOrCreate, Show, ColorBy, GetActiveCamera,
        ResetCamera, Render, SaveScreenshot, Hide, UpdatePipeline,
    )
    reader = MEDReader(FileNames=[result_path])
    reader.UpdatePipelineInformation()
    status = list(reader.FieldsStatus)
    log("MED-Felder: %s" % status)
    reader.UpdatePipeline()
    view = GetActiveViewOrCreate("RenderView")
    view.ViewSize = [1400, 900]
    view.Background = [0.90, 0.93, 0.97]
    view.OrientationAxesVisibility = 1

    def shot(field_token, png_name, component):
        chosen = [item for item in status if field_token in item]
        if not chosen:
            log("Feld %s nicht im MED." % field_token)
            return False
        # This MED reader lists one string per field. Assigning the chosen
        # subset is what displays it. The view itself has no Azimuth.
        reader.FieldsStatus = chosen
        UpdatePipeline(proxy=reader)
        Hide(reader, view)
        display = Show(reader, view)
        display.Representation = "Surface"
        point_arrays = [array.Name for array in reader.PointData]
        cell_arrays = [array.Name for array in reader.CellData]
        log("Punktfelder %s" % point_arrays)
        log("Zellfelder %s" % cell_arrays)
        array_name = None
        association = "POINTS"
        for name in point_arrays:
            if field_token in name or name.endswith(field_token):
                array_name = name
                break
        if array_name is None:
            for name in cell_arrays:
                if field_token in name or name.endswith(field_token):
                    array_name = name
                    association = "CELLS"
                    break
        if array_name is None:
            log("Kein Zeichenfeld fuer %s" % field_token)
            return False
        ColorBy(display, (association, array_name, component))
        display.SetScalarBarVisibility(view, True)
        display.RescaleTransferFunctionToDataRange(False, True)
        ResetCamera(view)
        camera = GetActiveCamera()
        camera.Azimuth(30)
        camera.Elevation(20)
        ResetCamera(view)
        Render(view)
        path = os.path.join(SHOT, png_name)
        SaveScreenshot(path, view, ImageResolution=[1400, 900])
        size = os.path.getsize(path) if os.path.exists(path) else 0
        log("Screenshot %s (%d Bytes)" % (path, size))
        return size > 1000

    if not shot("DEPL", "displacement.png", "Magnitude"):
        finish(8)
    if not shot("SIEQ", "von_mises.png", "VMIS"):
        finish(8)
    finish(0)
except SystemExit:
    raise
except Exception:
    log(traceback.format_exc())
    finish(1)
