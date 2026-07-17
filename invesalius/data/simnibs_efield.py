# --------------------------------------------------------------------------
# Software:     InVesalius - Software de Reconstrucao 3D de Imagens Medicas
# Copyright:    (C) 2001  Centro de Pesquisas Renato Archer
# Homepage:     http://www.softwarepublico.gov.br
# Contact:      invesalius@cti.gov.br
# License:      GNU - GPL 2 (LICENSE.txt/LICENCA.txt)
# --------------------------------------------------------------------------
#    Este programa e software livre; voce pode redistribui-lo e/ou
#    modifica-lo sob os termos da Licenca Publica Geral GNU, conforme
#    publicada pela Free Software Foundation; de acordo com a versao 2
#    da Licenca.
#
#    Este programa eh distribuido na expectativa de ser util, mas SEM
#    QUALQUER GARANTIA; sem mesmo a garantia implicita de
#    COMERCIALIZACAO ou de ADEQUACAO A QUALQUER PROPOSITO EM
#    PARTICULAR. Consulte a Licenca Publica Geral GNU para obter mais
#    detalhes.
# --------------------------------------------------------------------------

import logging
import os

from vtkmodules.vtkCommonCore import vtkLookupTable
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkFiltersGeneral import vtkTransformPolyDataFilter
from vtkmodules.vtkIOLegacy import vtkPolyDataReader
from vtkmodules.vtkIOXML import vtkXMLPolyDataReader
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

import invesalius.constants as const
from invesalius.pubsub import pub as Publisher

log = logging.getLogger(__name__)


_FIELD_NAME_CANDIDATES = ("magnE", "normE", "magnJ", "E", "magn", "scalars")


class SimnibsEfieldRenderer:
    """Owns a single E-field surface actor and keeps it in sync with the UI.

    A persistent reference must be held by the caller (e.g. the SimNIBS task
    panel), otherwise pubsub's weak references let the subscriptions die.
    """

    def __init__(self) -> None:
        self._actor = None
        self._mapper = None
        self._polydata = None
        self._array_name = None
        self._vmin = 0.0
        self._vmax = 1.0
        self._colormap = "Viridis"
        self._threshold_pct = 0.0
        self._opacity = 1.0
        self._subscribe()

    def _subscribe(self) -> None:
        Publisher.subscribe(self.on_efield_loaded, "SimNIBS efield loaded")
        Publisher.subscribe(self.on_load_result, "Load SimNIBS result")
        Publisher.subscribe(self.on_colormap, "Set SimNIBS colormap")
        Publisher.subscribe(self.on_opacity, "Set SimNIBS surface opacity")
        Publisher.subscribe(self.on_threshold, "Set SimNIBS threshold")
        Publisher.subscribe(self.on_remove, "Remove SimNIBS surfaces")

    def on_efield_loaded(self, result_msh: str) -> None:
        self._load(result_msh)

    def on_load_result(self, filepath: str) -> None:
        self._load(filepath)

    def on_colormap(self, colormap: str) -> None:
        self._colormap = colormap or self._colormap
        self._apply_lut()

    def on_opacity(self, name: str, opacity: float) -> None:
        self._opacity = float(opacity)
        if self._actor is not None:
            self._actor.GetProperty().SetOpacity(self._opacity)
            Publisher.sendMessage("Render volume viewer")

    def on_threshold(self, threshold_pct: float) -> None:
        self._threshold_pct = max(0.0, min(100.0, float(threshold_pct)))
        self._apply_lut()

    def on_remove(self) -> None:
        if self._actor is not None:
            Publisher.sendMessage("Remove surface actor from viewer", actor=self._actor)
        self._actor = None
        self._mapper = None
        self._polydata = None

    def _load(self, path: str) -> None:
        if not path:
            return
        try:
            actor, vmin, vmax = self._build_actor(path)
        except Exception as exc:  # noqa: BLE001 - surface back to the user
            log.exception("SimNIBS E-field surface could not be loaded")
            Publisher.sendMessage(
                "SimNIBS error",
                message=f"Could not load E-field surface '{os.path.basename(path)}':\n{exc}",
            )
            return

        # Replace any previously loaded field.
        self.on_remove()
        self._actor = actor
        self._vmin, self._vmax = vmin, vmax
        Publisher.sendMessage("Load surface actor into viewer", actor=actor)

    def _build_actor(self, path: str):
        polydata = _read_polydata(path)
        array_name, (vmin, vmax) = _select_scalar_array(polydata)
        if array_name is None:
            raise ValueError(
                "surface has no point-data scalar array (expected the E-field "
                "magnitude, e.g. 'magnE')"
            )
        polydata.GetPointData().SetActiveScalars(array_name)

        self._polydata = polydata
        self._array_name = array_name

        mapper = vtkPolyDataMapper()
        mapper.SetInputData(polydata)
        mapper.SetScalarModeToUsePointData()
        mapper.ScalarVisibilityOn()
        mapper.SetColorModeToMapScalars()
        mapper.SetScalarRange(vmin, vmax)
        mapper.SetLookupTable(self._make_lut(vmin, vmax))
        self._mapper = mapper

        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetOpacity(self._opacity)

        actor.ForceOpaqueOn()
        actor.GetProperty().SetInterpolationToFlat()
        return actor, vmin, vmax

    def _apply_lut(self) -> None:
        if self._mapper is None:
            return
        self._mapper.SetLookupTable(self._make_lut(self._vmin, self._vmax))
        self._mapper.Modified()
        Publisher.sendMessage("Render volume viewer")

    def _make_lut(self, vmin: float, vmax: float):
        lut = build_colormap_lut(self._colormap, vmin, vmax)
        _apply_threshold_alpha(lut, self._threshold_pct)
        return lut


def _read_polydata(path: str):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".vtp":
        reader = vtkXMLPolyDataReader()
    elif ext == ".vtk":
        reader = vtkPolyDataReader()
        reader.ReadAllScalarsOn()
    else:
        raise ValueError(
            f"unsupported E-field surface format '{ext}'. The SimNIBS server "
            "must send a VTK surface (.vtp/.vtk), not the raw .msh."
        )
    reader.SetFileName(path)
    reader.Update()
    polydata = reader.GetOutput()
    import invesalius.data.slice_ as sl

    slic = sl.Slice()
    affine, affine_vtk, _ = slic.get_world_to_invesalius_vtk_affine(inverse=False)

    polydata_transform = vtkTransform()
    polydata_transform.PostMultiply()
    polydata_transform.Concatenate(affine_vtk)

    transformFilter = vtkTransformPolyDataFilter()
    transformFilter.SetTransform(polydata_transform)
    transformFilter.SetInputData(polydata)
    transformFilter.Update()

    out = transformFilter.GetOutput()
    if out is None or out.GetNumberOfPoints() == 0:
        raise ValueError("surface file is empty or could not be read")
    return out


def _select_scalar_array(polydata):
    """Return (array_name, (vmin, vmax)) for the field to colour by, or (None, ...)."""
    point_data = polydata.GetPointData()

    active = point_data.GetScalars()
    if active is not None and active.GetName():
        return active.GetName(), active.GetRange()

    for name in _FIELD_NAME_CANDIDATES:
        arr = point_data.GetArray(name)
        if arr is not None:
            return name, arr.GetRange()

    if point_data.GetNumberOfArrays() > 0:
        arr = point_data.GetArray(0)
        return arr.GetName(), arr.GetRange()

    return None, (0.0, 1.0)


def build_colormap_lut(colormap_name: str, vmin: float, vmax: float, n: int = 256):
    """
    Reuses the existing colormap
    ``LinearSegmentedColormap`` interpolation (positions 0, 0.25, 0.5, 1.0) that
    ``preferences.py`` uses for the MEP gradient, so colours match the rest of
    InVesalius.
    """
    from matplotlib.colors import LinearSegmentedColormap

    definitions = const.MEP_COLORMAP_DEFINITIONS
    definition = definitions.get(colormap_name) or definitions["Viridis"]
    colors = list(definition.values())  # [min, low, mid, max] RGB
    positions = [0.0, 0.25, 0.5, 1.0]
    cmap = LinearSegmentedColormap.from_list(colormap_name, list(zip(positions, colors)))

    lut = vtkLookupTable()
    lut.SetTableRange(vmin, vmax)
    lut.SetNumberOfTableValues(n)
    for i in range(n):
        r, g, b, a = cmap(i / (n - 1))
        lut.SetTableValue(i, r, g, b, a)
    lut.Build()
    return lut


def _apply_threshold_alpha(lut, threshold_pct: float) -> None:
    """
    Hide values below ``threshold_pct`` of the range by zeroing their alpha.

    """
    n = lut.GetNumberOfTableValues()
    if threshold_pct and threshold_pct > 0.0 and n > 1:
        cutoff = threshold_pct / 100.0
        for i in range(n):
            if i / (n - 1) < cutoff:
                r, g, b, _a = lut.GetTableValue(i)
                lut.SetTableValue(i, r, g, b, 0.0)
    lut.SetUseBelowRangeColor(True)
    lut.SetBelowRangeColor(0.0, 0.0, 0.0, 0.0)
