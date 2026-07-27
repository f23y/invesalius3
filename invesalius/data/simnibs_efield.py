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

import os

import numpy as np
from vtkmodules.util import numpy_support
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkFiltersGeneral import vtkTransformPolyDataFilter
from vtkmodules.vtkIOLegacy import vtkPolyDataReader
from vtkmodules.vtkIOXML import vtkXMLPolyDataReader

import invesalius.data.slice_ as sl

_EFIELD_ARRAY_CANDIDATES = ("magnE", "normE", "magnJ", "magn", "E", "scalars")


def ReadEfieldSurface(path):
    polydata = _ReadEfieldPolyData(path)

    point_data = polydata.GetPointData()
    scalars = _SelectEfieldArray(point_data)
    vtk_vectors = point_data.GetVectors()
    vectors = numpy_support.vtk_to_numpy(vtk_vectors) if vtk_vectors is not None else None

    if scalars is not None:
        norms = numpy_support.vtk_to_numpy(scalars)
        if norms.ndim > 1:
            norms = np.linalg.norm(norms, axis=1)
    elif vectors is not None:
        norms = np.linalg.norm(vectors, axis=1)
    else:
        raise ValueError(
            "the surface has no point data to colour by (expected the E-field "
            "magnitude, e.g. 'magnE')"
        )

    return polydata, np.asarray(norms, dtype=float), vectors


def _ReadEfieldPolyData(path):
    extension = os.path.splitext(path)[1].lower()
    if extension == ".vtp":
        reader = vtkXMLPolyDataReader()
    elif extension == ".vtk":
        reader = vtkPolyDataReader()
        reader.ReadAllScalarsOn()
        reader.ReadAllVectorsOn()
    else:
        raise ValueError(
            f"unsupported E-field surface format '{extension}'. The SimNIBS server "
            "must send a VTK surface (.vtk/.vtp), not the raw .msh."
        )
    reader.SetFileName(path)
    reader.Update()

    _affine, affine_vtk, _img_shift = sl.Slice().get_world_to_invesalius_vtk_affine(inverse=False)

    transform = vtkTransform()
    transform.PostMultiply()
    transform.Concatenate(affine_vtk)

    transform_filter = vtkTransformPolyDataFilter()
    transform_filter.SetTransform(transform)
    transform_filter.SetInputData(reader.GetOutput())
    transform_filter.Update()

    polydata = transform_filter.GetOutput()
    if polydata is None or polydata.GetNumberOfPoints() == 0:
        raise ValueError("the E-field surface file is empty or could not be read")
    return polydata


def _SelectEfieldArray(point_data):
    active = point_data.GetScalars()
    if active is not None:
        return active

    for name in _EFIELD_ARRAY_CANDIDATES:
        array = point_data.GetArray(name)
        if array is not None:
            return array

    for i in range(point_data.GetNumberOfArrays()):
        array = point_data.GetArray(i)
        if array is not None and array.GetNumberOfComponents() == 1:
            return array

    return None
