# -*- coding: utf-8 -*-
"""Screenshots of displacement and von Mises from the existing MED result."""
import os
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT = os.environ.get("CONTACT_RESULT_MED", "/tmp/contact_static/result.med")
SHOT = os.path.join(ROOT, "testing", "screenshots")
LOG_PATH = "/tmp/shot_results.log"
DONE_PATH = "/tmp/shot_results.done"
os.makedirs(SHOT, exist_ok=True)


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
    open(LOG_PATH, "w", encoding="utf-8").close()
    os.environ["PV_OFFSCREEN"] = "1"
    from paraview.simple import (
        MEDReader, GetActiveViewOrCreate, Show, ColorBy, ResetCamera, Render,
        SaveScreenshot, Hide, UpdatePipeline, GetColorTransferFunction, GetActiveCamera,
    )
    from paraview.servermanager import Fetch
    reader = MEDReader(FileNames=[RESULT])
    reader.UpdatePipelineInformation()
    log("Properties: %s" % reader.ListProperties())
    status = list(reader.FieldsStatus)
    log("Fields: %s" % status)
    view = GetActiveViewOrCreate("RenderView")
    view.ViewSize = [1400, 900]
    view.Background = [0.90, 0.93, 0.97]
    view.OrientationAxesVisibility = 1

    def enable(token):
        chosen = [item for item in status if token in item]
        if not chosen:
            return False
        # This MED reader lists one string per field. Keeping only the
        # requested field is what activates it.
        reader.FieldsStatus = chosen
        UpdatePipeline(proxy=reader)
        return True

    def leaves(data):
        if data is None:
            return
        if data.IsA("vtkMultiBlockDataSet"):
            for index in range(data.GetNumberOfBlocks()):
                for child in leaves(data.GetBlock(index)):
                    yield child
            return
        yield data

    def ranges_of(array_name):
        try:
            minimum = None
            maximum = None
            mag_min = None
            mag_max = None
            names = None
            seen = 0
            for block in leaves(Fetch(reader)):
                array = None
                if hasattr(block, "GetPointData"):
                    array = block.GetPointData().GetArray(array_name)
                if array is None and hasattr(block, "GetCellData"):
                    array = block.GetCellData().GetArray(array_name)
                if array is None:
                    continue
                components = array.GetNumberOfComponents()
                if names is None:
                    names = [array.GetComponentName(comp) for comp in range(components)]
                    minimum = [1e300] * components
                    maximum = [-1e300] * components
                    mag_min = 1e300
                    mag_max = -1e300
                for index in range(array.GetNumberOfTuples()):
                    value = array.GetTuple(index)
                    square = 0.0
                    for comp in range(components):
                        number = value[comp]
                        square += number * number
                        if number < minimum[comp]:
                            minimum[comp] = number
                        if number > maximum[comp]:
                            maximum[comp] = number
                    magnitude = square ** 0.5
                    if magnitude < mag_min:
                        mag_min = magnitude
                    if magnitude > mag_max:
                        mag_max = magnitude
                    seen += 1
            log("Array %s Punkte %d Komponenten %s" % (array_name, seen, names))
            log("Min %s" % minimum)
            log("Max %s" % maximum)
            if mag_min is not None:
                log("Betrag %.6e .. %.6e" % (mag_min, mag_max))
        except Exception as exc:
            log("Wertebereich nicht lesbar: %s" % exc)

    def shot(token, png_name, component=None):
        if not enable(token):
            log("Feld fehlt: %s" % token)
            return False
        Hide(reader, view)
        display = Show(reader, view)
        display.Representation = "Surface"
        point_arrays = [array.Name for array in reader.PointData]
        cell_arrays = [array.Name for array in reader.CellData]
        log("Punkt %s" % point_arrays)
        log("Zelle %s" % cell_arrays)
        array_name = None
        association = "POINTS"
        for name in point_arrays:
            if token in name:
                array_name = name
                break
        if array_name is None:
            association = "CELLS"
            for name in cell_arrays:
                if token in name:
                    array_name = name
                    break
        if array_name is None:
            log("Kein Array fuer %s" % token)
            return False
        ranges_of(array_name)
        if component:
            ColorBy(display, (association, array_name, component))
        else:
            ColorBy(display, (association, array_name))
        display.SetScalarBarVisibility(view, True)
        display.RescaleTransferFunctionToDataRange(False, True)
        lut = GetColorTransferFunction(array_name)
        points = list(lut.RGBPoints) if lut.RGBPoints else []
        log("Farbskala %s" % points[:8])
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

    if not shot("DEPL", "displacement.png", component="Magnitude"):
        finish(8)
    if not shot("SIEQ", "von_mises.png", component="VMIS"):
        finish(8)
    finish(0)
except SystemExit:
    raise
except Exception:
    log(traceback.format_exc())
    finish(1)
