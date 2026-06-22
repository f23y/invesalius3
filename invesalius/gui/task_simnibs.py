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
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading

import wx

import invesalius.constants as const
import invesalius.session as ses
import invesalius.utils as utils
from invesalius.i18n import tr as _
from invesalius.pubsub import pub as Publisher

_KEY_M2M_DIR = "simnibs_last_m2m_dir"
_KEY_OUTPUT_DIR = "simnibs_last_output_dir"
_KEY_COIL_FILE = "simnibs_last_coil_file"
_KEY_T1_FILE = "simnibs_last_t1_file"
_KEY_T2_FILE = "simnibs_last_t2_file"
_KEY_SIMNIBS_EXE = "simnibs_executable_path"

# Pubsub topic strings
TOPIC_LOAD_SURFACES = "Load SimNIBS surfaces"
TOPIC_LOAD_RESULT = "Load SimNIBS result"
TOPIC_REMOVE_SURFACES = "Remove SimNIBS surfaces"
TOPIC_SET_VISIBILITY = "Set SimNIBS surface visibility"
TOPIC_SET_OPACITY = "Set SimNIBS surface opacity"
TOPIC_SET_COLORMAP = "Set SimNIBS colormap"
TOPIC_SET_THRESHOLD = "Set SimNIBS threshold"
TOPIC_SURFACES_LOADED = "SimNIBS surfaces loaded"
TOPIC_EFIELD_LOADED = "SimNIBS efield loaded"
TOPIC_PROGRESS = "SimNIBS progress"
TOPIC_ERROR = "SimNIBS error"


# Find SimNIBS

_SIMNIBS_ROOTS = [
    os.path.expanduser("~/SimNIBS-4.6"),
    os.path.expanduser("~/SimNIBS-4.5"),
    os.path.expanduser("~/SimNIBS-4"),
    os.path.expanduser("~/SimNIBS-3.2"),
    os.path.expanduser("~/.local"),
    "/usr/local",
    "/opt/simnibs",
]


def _find_charm() -> str | None:
    path = shutil.which("charm")
    if path:
        return path
    for root in _SIMNIBS_ROOTS:
        candidate = os.path.join(root, "bin", "charm")
        if os.path.isfile(candidate):
            return candidate
    return None


def _simnibs_site_packages() -> str | None:
    charm_exe = _find_charm()
    if not charm_exe:
        return None
    import glob

    simnibs_root = os.path.dirname(os.path.dirname(charm_exe))
    candidates = glob.glob(
        os.path.join(simnibs_root, "simnibs_env", "lib", "python*", "site-packages")
    )
    return candidates[0] if candidates else None


# def _try_import_gmsh():
#     """Import gmsh, falling back to the SimNIBS-bundled copy if needed."""
#     try:
#         import gmsh
#         return gmsh
#     except ImportError:
#         sp = _simnibs_site_packages()
#         if sp and sp not in sys.path:
#             sys.path.insert(0, sp)
#         try:
#             import gmsh
#             return gmsh
#         except ImportError:
#             return None


def _read_lut() -> dict:
    """
    Parse final_tissues_FreeSurferColorLUT.txt bundled with SimNIBS.
    Returns {label: (name, (r, g, b))}.
    """
    sp = _simnibs_site_packages()
    if not sp:
        return {}
    lut_path = os.path.join(sp, "simnibs", "resources", "final_tissues_FreeSurferColorLUT.txt")
    if not os.path.isfile(lut_path):
        return {}
    result: dict = {}
    with open(lut_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                label = int(parts[0])
                name = parts[1]
                r, g, b = int(parts[2]), int(parts[3]), int(parts[4])
                result[label] = (name, (r, g, b))
            except ValueError:
                continue
    return result


# def _discover_tissues(m2m_dir: str) -> list:
#     """
#     Return [(name, nifti_label, (r, g, b)), ...] for each tissue surface.

#     Strategy — first succeeding method wins:
#       1. gmsh Python API on the subject .msh file (reads actual physical-group
#          names, adapts automatically to any SimNIBS tissue configuration).
#       2. SimNIBS FreeSurfer colour LUT + standard fallback labels.
#     """
#     import logging
#     log = logging.getLogger(__name__)

#     subject = os.path.basename(m2m_dir)
#     if subject.startswith("m2m_"):
#         subject = subject[4:]
#     msh_file = os.path.join(m2m_dir, f"{subject}.msh")

#     lut = _read_lut()

#     gmsh = _try_import_gmsh()
#     if gmsh and os.path.isfile(msh_file):
#         try:
#             gmsh.initialize()
#             gmsh.model.open(msh_file)
#             entities = gmsh.model.getEntities()
#             log.info("SimNIBS mesh entities: %s", entities)
#             seen: dict = {}
#             for dim, tag in entities:
#                 if dim != 2:
#                     continue
#                 name = gmsh.model.getPhysicalName(dim, tag)
#                 if not name:
#                     continue
#                 # SimNIBS convention: surface tag = 1000 + volume label
#                 label = tag - 1000 if tag >= 1000 else tag
#                 if label > 0:
#                     seen[label] = name
#             gmsh.finalize()
#             if seen:
#                 result = []
#                 for label, name in sorted(seen.items()):
#                     colour = lut.get(label, (None, (200, 200, 200)))[1]
#                     result.append((name, label, colour))
#                 log.info("Tissues from gmsh: %s", result)
#                 return result
#         except Exception as exc:
#             log.warning("gmsh tissue discovery failed: %s", exc)
#             try:
#                 gmsh.finalize()
#             except Exception:
#                 pass

#     # Fallback: standard SimNIBS labels augmented by the LUT where available
#     defaults = [
#         (1, "WM",    (230, 230, 230)),
#         (2, "GM",    (129, 129, 129)),
#         (3, "CSF",   (104, 163, 255)),
#         (4, "Bone",  (255, 239, 179)),
#         (5, "Scalp", (255, 166, 133)),
#     ]
#     result = []
#     for label, default_name, default_colour in defaults:
#         name, colour = lut.get(label, (default_name, default_colour))
#         result.append((name, label, colour))
#     log.info("Tissues from fallback: %s", result)
#     return result


def _label_info(present_labels: list) -> dict:
    """
    Return {label: (name, (r, g, b))} for each integer label found in a
    NIfTI file.  Names come from the SimNIBS FreeSurfer colour LUT when
    available; unknown labels receive a generic ``tissue_N`` name.
    """
    lut = _read_lut()
    result: dict = {}
    for label in present_labels:
        if label in lut:
            result[label] = lut[label]
        else:
            result[label] = (f"tissue_{label}", (200, 200, 200))
    return result


# runs SimNIBS charm in a background thread
class CharmRunner:
    """
    Runs ``charm [--forcerun] subid T1 [T2]`` in a background daemon thread.

    Callbacks are delivered on the wx main thread via wx.CallAfter:
      progress_cb(message: str, percent: int)
      done_cb(success: bool, error: str | None, m2m_dir: str | None)
    """

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    def start(
        self,
        subject: str,
        t1: str,
        t2: str | None,
        outdir: str,
        forcerun: bool,
        progress_cb,
        done_cb,
    ) -> None:
        self._thread = threading.Thread(
            target=self._run,
            args=(subject, t1, t2, outdir, forcerun, progress_cb, done_cb),
            daemon=True,
        )
        self._thread.start()

    def cancel(self) -> None:
        if self._proc and self._proc.poll() is None:
            if sys.platform == "win32":
                subprocess.call(["taskkill", "/F", "/T", "/PID", str(self._proc.pid)])
            else:
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def _run(self, subject, t1, t2, outdir, forcerun, progress_cb, done_cb):
        charm_exe = _find_charm()
        if not charm_exe:
            wx.CallAfter(
                done_cb,
                False,
                _(
                    "charm executable not found.\n"
                    "Install SimNIBS and ensure it is on PATH or a standard location."
                ),
                None,
            )
            return

        cmd = [charm_exe]
        if forcerun:
            cmd.append("--forcerun")
        cmd.append(subject)
        cmd.append(t1)
        if t2:
            cmd.append(t2)

        pg_kwargs: dict = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if sys.platform == "win32"
            else {"start_new_session": True}
        )
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=outdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                **pg_kwargs,
            )
            percent = 0
            for raw in self._proc.stdout:
                line = raw.rstrip()
                if not line:
                    continue
                m = re.search(r"(\d+)\s*%", line)
                if m:
                    percent = min(int(m.group(1)), 99)
                else:
                    percent = min(percent + 1, 99)
                wx.CallAfter(progress_cb, line, percent)
            self._proc.wait()
            rc = self._proc.returncode
        except Exception as exc:
            wx.CallAfter(done_cb, False, str(exc), None)
            return

        if rc != 0:
            wx.CallAfter(done_cb, False, _("charm exited with code {}").format(rc), None)
            return

        m2m_dir = os.path.join(outdir, f"m2m_{subject}")
        if not os.path.isdir(m2m_dir):
            wx.CallAfter(
                done_cb,
                False,
                _("Expected output folder not found:\n{}").format(m2m_dir),
                None,
            )
            return

        wx.CallAfter(done_cb, True, None, m2m_dir)


class TaskPanel(wx.ScrolledWindow):
    def __init__(self, parent):
        wx.ScrolledWindow.__init__(self, parent, style=wx.TAB_TRAVERSAL)

        self.SetSize(wx.Size(400, 300))
        self.SetScrollRate(5, 5)

        inner_panel = InnerTaskPanel(self)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(inner_panel, 1, wx.EXPAND | wx.GROW | wx.ALL, 0)
        self.SetSizer(sizer)
        self.Layout()
        self.Update()
        self.SetAutoLayout(1)


class InnerTaskPanel(wx.Panel):
    """
    Three collapsible sections:
      1. Head Model   — charm segmentation inputs
      2. Simulation   — coil / pose / run controls
      3. E-field View — overlay / colormap / threshold
    """

    def __init__(self, parent):
        wx.Panel.__init__(self, parent)

        self.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_MENUBAR))

        self.session = ses.Session()
        self._m2m_path = None
        self._pose_locked = False

        self._subscribe()
        self._build_ui()
        self._restore_paths()

    def _subscribe(self):
        Publisher.subscribe(self._on_surfaces_loaded, TOPIC_SURFACES_LOADED)
        Publisher.subscribe(self._on_efield_loaded, TOPIC_EFIELD_LOADED)
        Publisher.subscribe(self._on_progress, TOPIC_PROGRESS)
        Publisher.subscribe(self._on_error, TOPIC_ERROR)

    def _build_ui(self):
        # HEAD MODEL
        box_hm = wx.StaticBox(self, -1, _("Head Model (charm)"))
        sz_hm = wx.StaticBoxSizer(box_hm, wx.VERTICAL)

        self.txt_subject = wx.TextCtrl(self, -1, "")
        self.txt_t1 = wx.TextCtrl(self, -1, "")
        self.txt_t2 = wx.TextCtrl(self, -1, "")
        self.txt_hm_out = wx.TextCtrl(self, -1, "", style=wx.TE_READONLY)

        btn_t1 = wx.Button(self, -1, _("…"), size=wx.Size(28, -1))
        btn_t2 = wx.Button(self, -1, _("…"), size=wx.Size(28, -1))
        btn_hm_out = wx.Button(self, -1, _("…"), size=wx.Size(28, -1))

        btn_t1.Bind(wx.EVT_BUTTON, self.OnBrowseT1)
        btn_t2.Bind(wx.EVT_BUTTON, self.OnBrowseT2)
        btn_hm_out.Bind(wx.EVT_BUTTON, self.OnBrowseHMOutput)

        g1 = wx.FlexGridSizer(4, 3, 2, 2)
        g1.AddGrowableCol(1)
        g1.Add(wx.StaticText(self, -1, _("Subject ID:")), 0, wx.ALIGN_CENTER_VERTICAL)
        g1.Add(self.txt_subject, 1, wx.EXPAND)
        g1.AddSpacer(0)
        g1.Add(wx.StaticText(self, -1, _("MRI file 1:")), 0, wx.ALIGN_CENTER_VERTICAL)
        g1.Add(self.txt_t1, 1, wx.EXPAND)
        g1.Add(btn_t1, 0)
        g1.Add(wx.StaticText(self, -1, _("MRI file 2:")), 0, wx.ALIGN_CENTER_VERTICAL)
        g1.Add(self.txt_t2, 1, wx.EXPAND)
        g1.Add(btn_t2, 0)
        g1.Add(wx.StaticText(self, -1, _("Output dir:")), 0, wx.ALIGN_CENTER_VERTICAL)
        g1.Add(self.txt_hm_out, 1, wx.EXPAND)
        g1.Add(btn_hm_out, 0)
        sz_hm.Add(g1, 0, wx.EXPAND | wx.ALL, 2)

        self.chk_forcerun = wx.CheckBox(self, -1, _("Force re-run (--forcerun)"))
        self.chk_forcerun.SetToolTip(
            _(
                "Overwrite an existing m2m_<subjectID> folder.\n"
                "Required if you want to re-run charm for the same subject."
            )
        )
        sz_hm.Add(self.chk_forcerun, 0, wx.LEFT | wx.BOTTOM, 2)

        row_hm = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_run_charm = wx.Button(self, -1, _("Run head model"), size=wx.Size(110, -1))
        self.btn_cancel_charm = wx.Button(self, -1, _("Cancel"), size=wx.Size(60, -1))
        self.btn_cancel_charm.Enable(False)
        self.btn_run_charm.Bind(wx.EVT_BUTTON, self.OnRunCharm)
        self.btn_cancel_charm.Bind(wx.EVT_BUTTON, self.OnCancelCharm)
        row_hm.Add(self.btn_run_charm, 1, wx.RIGHT, 2)
        row_hm.Add(self.btn_cancel_charm, 0)
        sz_hm.Add(row_hm, 0, wx.EXPAND | wx.ALL, 2)

        self.gauge_charm = wx.Gauge(self, -1, 100)
        self.lbl_charm = wx.StaticText(self, -1, _("Ready."))
        sz_hm.Add(self.gauge_charm, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 2)
        sz_hm.Add(self.lbl_charm, 0, wx.LEFT | wx.BOTTOM, 2)

        self.btn_load_tissues = wx.Button(self, -1, _("Load tissue surfaces…"))
        self.btn_load_tissues.SetToolTip(
            _(
                "Select a tissue-label NIfTI from the m2m folder,\n"
                "create one InVesalius mask per label and generate VTK surfaces."
            )
        )
        self.btn_load_tissues.Bind(wx.EVT_BUTTON, self.OnLoadTissueSurfaces)
        sz_hm.Add(self.btn_load_tissues, 0, wx.ALL, 2)

        # TMS SIMULATION
        box_sim = wx.StaticBox(self, -1, _("TMS Simulation"))
        sz_sim = wx.StaticBoxSizer(box_sim, wx.VERTICAL)

        self.txt_m2m = wx.TextCtrl(self, -1, "", style=wx.TE_READONLY)
        self.txt_sim_out = wx.TextCtrl(self, -1, "", style=wx.TE_READONLY)
        self.txt_coil = wx.TextCtrl(self, -1, "", style=wx.TE_READONLY)
        self.txt_didt = wx.TextCtrl(self, -1, "1000000.0")

        btn_m2m = wx.Button(self, -1, _("…"), size=wx.Size(28, -1))
        btn_sim_out = wx.Button(self, -1, _("…"), size=wx.Size(28, -1))
        btn_coil = wx.Button(self, -1, _("…"), size=wx.Size(28, -1))

        btn_m2m.Bind(wx.EVT_BUTTON, self.OnBrowseM2M)
        btn_sim_out.Bind(wx.EVT_BUTTON, self.OnBrowseSimOutput)
        btn_coil.Bind(wx.EVT_BUTTON, self.OnBrowseCoil)

        g2 = wx.FlexGridSizer(4, 3, 2, 2)
        g2.AddGrowableCol(1)
        g2.Add(wx.StaticText(self, -1, _("m2m path:")), 0, wx.ALIGN_CENTER_VERTICAL)
        g2.Add(self.txt_m2m, 1, wx.EXPAND)
        g2.Add(btn_m2m, 0)
        g2.Add(wx.StaticText(self, -1, _("Output dir:")), 0, wx.ALIGN_CENTER_VERTICAL)
        g2.Add(self.txt_sim_out, 1, wx.EXPAND)
        g2.Add(btn_sim_out, 0)
        g2.Add(wx.StaticText(self, -1, _("Coil file:")), 0, wx.ALIGN_CENTER_VERTICAL)
        g2.Add(self.txt_coil, 1, wx.EXPAND)
        g2.Add(btn_coil, 0)
        g2.Add(wx.StaticText(self, -1, _("dI/dt (A/s):")), 0, wx.ALIGN_CENTER_VERTICAL)
        g2.Add(self.txt_didt, 1, wx.EXPAND)
        g2.AddSpacer(0)
        sz_sim.Add(g2, 0, wx.EXPAND | wx.ALL, 2)

        # matsimnibs display
        sz_sim.Add(wx.StaticText(self, -1, _("Coil pose (matsimnibs):")), 0, wx.LEFT | wx.TOP, 2)
        self.txt_mat = wx.TextCtrl(
            self,
            -1,
            _("Identity — lock a pose first"),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL,
            size=wx.Size(-1, 72),
        )
        sz_sim.Add(self.txt_mat, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 2)

        self.btn_lock = wx.Button(self, -1, _("Lock current coil pose"), size=wx.Size(160, -1))
        self.btn_lock.Bind(wx.EVT_BUTTON, self.OnLockPose)
        sz_sim.Add(self.btn_lock, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 2)

        row_sim = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_run_sim = wx.Button(self, -1, _("Run simulation"), size=wx.Size(110, -1))
        self.btn_cancel_sim = wx.Button(self, -1, _("Cancel"), size=wx.Size(60, -1))
        self.btn_run_sim.Enable(False)
        self.btn_cancel_sim.Enable(False)
        self.btn_run_sim.Bind(wx.EVT_BUTTON, self.OnRunSimulation)
        self.btn_cancel_sim.Bind(wx.EVT_BUTTON, self.OnCancelSimulation)
        row_sim.Add(self.btn_run_sim, 1, wx.RIGHT, 2)
        row_sim.Add(self.btn_cancel_sim, 0)
        sz_sim.Add(row_sim, 0, wx.EXPAND | wx.ALL, 2)

        self.gauge_sim = wx.Gauge(self, -1, 100)
        self.lbl_sim = wx.StaticText(self, -1, _("Load head surfaces first."))
        sz_sim.Add(self.gauge_sim, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 2)
        sz_sim.Add(self.lbl_sim, 0, wx.LEFT | wx.BOTTOM, 2)

        # E-FIELD VISUALIZATION
        box_ef = wx.StaticBox(self, -1, _("E-field Visualization"))
        sz_ef = wx.StaticBoxSizer(box_ef, wx.VERTICAL)

        self.chk_gm = wx.CheckBox(self, -1, _("Grey matter (GM)"))
        self.chk_skin = wx.CheckBox(self, -1, _("Skin"))
        self.chk_gm.SetValue(True)
        self.chk_skin.SetValue(True)
        self.chk_gm.Bind(wx.EVT_CHECKBOX, self.OnToggleGM)
        self.chk_skin.Bind(wx.EVT_CHECKBOX, self.OnToggleSkin)
        sz_ef.Add(self.chk_gm, 0, wx.ALL, 2)
        sz_ef.Add(self.chk_skin, 0, wx.LEFT | wx.BOTTOM, 2)

        row_cmap = wx.BoxSizer(wx.HORIZONTAL)
        row_cmap.Add(
            wx.StaticText(self, -1, _("Colormap:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4
        )
        self.combo_cmap = wx.ComboBox(
            self,
            -1,
            size=wx.Size(90, -1),
            choices=["hot", "jet", "cool", "rainbow"],
            style=wx.CB_DROPDOWN | wx.CB_READONLY,
        )
        self.combo_cmap.SetSelection(0)
        self.combo_cmap.Bind(wx.EVT_COMBOBOX, self.OnColormap)
        row_cmap.Add(self.combo_cmap, 0)
        sz_ef.Add(row_cmap, 0, wx.ALL, 2)

        row_op = wx.BoxSizer(wx.HORIZONTAL)
        row_op.Add(
            wx.StaticText(self, -1, _("Skin opacity:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4
        )
        self.spin_opacity = wx.SpinCtrlDouble(self, -1, "", size=wx.Size(60, -1), inc=0.05)
        self.spin_opacity.SetRange(0.0, 1.0)
        self.spin_opacity.SetValue(0.4)
        self.spin_opacity.Bind(wx.EVT_TEXT, self.OnOpacity)
        self.spin_opacity.Bind(wx.EVT_SPINCTRL, self.OnOpacity)
        row_op.Add(self.spin_opacity, 0)
        sz_ef.Add(row_op, 0, wx.ALL, 2)

        row_th = wx.BoxSizer(wx.HORIZONTAL)
        row_th.Add(
            wx.StaticText(self, -1, _("Threshold %:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4
        )
        self.spin_threshold = wx.SpinCtrlDouble(self, -1, "", size=wx.Size(60, -1), inc=1.0)
        self.spin_threshold.SetRange(0.0, 100.0)
        self.spin_threshold.SetValue(90.0)
        self.spin_threshold.Bind(wx.EVT_TEXT, self.OnThreshold)
        self.spin_threshold.Bind(wx.EVT_SPINCTRL, self.OnThreshold)
        row_th.Add(self.spin_threshold, 0)
        sz_ef.Add(row_th, 0, wx.ALL, 2)

        self.btn_remove = wx.Button(self, -1, _("Remove all actors"), size=wx.Size(140, -1))
        self.btn_remove.Bind(wx.EVT_BUTTON, self.OnRemove)
        sz_ef.Add(self.btn_remove, 0, wx.ALL, 2)

        # outer sizer
        main = wx.BoxSizer(wx.VERTICAL)
        main.Add(sz_hm, 0, wx.EXPAND | wx.ALL, 5)
        main.Add(sz_sim, 0, wx.EXPAND | wx.ALL, 5)
        main.Add(sz_ef, 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(main)
        self.Layout()

    def _restore_paths(self):
        self.txt_m2m.SetValue(self.session.GetConfig(_KEY_M2M_DIR, ""))
        self.txt_sim_out.SetValue(self.session.GetConfig(_KEY_OUTPUT_DIR, ""))
        self.txt_coil.SetValue(self.session.GetConfig(_KEY_COIL_FILE, ""))
        self.txt_t1.SetValue(self.session.GetConfig(_KEY_T1_FILE, ""))
        self.txt_t2.SetValue(self.session.GetConfig(_KEY_T2_FILE, ""))
        if self.session.GetConfig(_KEY_M2M_DIR, ""):
            self.btn_run_sim.Enable(True)

    def _save_path(self, key, value):
        self.session.SetConfig(key, value)

    def _browse_file(self, wildcard, session_key, msg=""):
        last_dir = self.session.GetConfig(session_key, "")
        dialog = wx.FileDialog(
            self,
            message=msg or _("Select file"),
            defaultDir=last_dir,
            defaultFile="",
            wildcard=wildcard,
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_CHANGE_DIR,
        )
        path = None
        try:
            if dialog.ShowModal() == wx.ID_OK:
                path = (
                    dialog.GetPath()
                    if sys.platform == "win32"
                    else dialog.GetPath().encode("utf-8")
                )
        except wx.PyAssertionError:
            if dialog.GetPath():
                path = dialog.GetPath()
        dialog.Destroy()
        if path:
            path = utils.decode(path, const.FS_ENCODE)
            self._save_path(session_key, os.path.dirname(path))
        return path

    def _browse_dir(self, session_key, msg=""):
        current_dir = os.path.abspath(".")
        last_dir = self.session.GetConfig(session_key, "")
        dialog = wx.DirDialog(
            self,
            message=msg or _("Choose a folder:"),
            defaultPath=last_dir,
            style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST | wx.DD_CHANGE_DIR,
        )
        path = None
        try:
            if dialog.ShowModal() == wx.ID_OK:
                path = (
                    dialog.GetPath()
                    if sys.platform == "win32"
                    else dialog.GetPath().encode("utf-8")
                )
        except wx.PyAssertionError:
            if dialog.GetPath():
                path = dialog.GetPath()
        dialog.Destroy()
        os.chdir(current_dir)
        if path:
            path = utils.decode(path, const.FS_ENCODE)
            self._save_path(session_key, path)
        return path

    def OnBrowseT1(self, _evt):
        path = self._browse_file(
            _("NIfTI (*.nii;*.nii.gz)|*.nii;*.nii.gz|All files (*.*)|*.*"),
            _KEY_T1_FILE,
            _("Select MRI file 1"),
        )
        if path:
            self.txt_t1.SetValue(path)

    def OnBrowseT2(self, _evt):
        path = self._browse_file(
            _("NIfTI (*.nii;*.nii.gz)|*.nii;*.nii.gz|All files (*.*)|*.*"),
            _KEY_T2_FILE,
            _("Select MRI file 2"),
        )
        if path:
            self.txt_t2.SetValue(path)

    def OnBrowseHMOutput(self, _evt):
        path = self._browse_dir(_KEY_OUTPUT_DIR, _("Choose subjects root folder"))
        if path:
            self.txt_hm_out.SetValue(path)

    def OnBrowseM2M(self, _evt):
        path = self._browse_dir(_KEY_M2M_DIR, _("Choose m2m_subjectID folder"))
        if path:
            self.txt_m2m.SetValue(path)
            self._m2m_path = path
            self._save_path(_KEY_M2M_DIR, path)
            self.btn_run_sim.Enable(True)
            # TODO: fire TOPIC_LOAD_SURFACES once simnibs_handler is ready
            # head_msh = self._head_msh_from_m2m(path)
            # Publisher.sendMessage(TOPIC_LOAD_SURFACES, msh_path=head_msh, tags=[1002, 1005])

    def OnBrowseSimOutput(self, _evt):
        path = self._browse_dir(_KEY_OUTPUT_DIR, _("Choose simulation output folder"))
        if path:
            self.txt_sim_out.SetValue(path)

    def OnBrowseCoil(self, _evt):
        path = self._browse_file(
            _("SimNIBS coil (*.tcd;*.ccd)|*.tcd;*.ccd|All files (*.*)|*.*"),
            _KEY_COIL_FILE,
            _("Select SimNIBS coil file"),
        )
        if path:
            self.txt_coil.SetValue(path)

    def OnLockPose(self, _evt):
        """Read live coil pose from the navigation module via pubsub.

        TODO: wire up when navigation integration is confirmed.
        replace with real pubsub request to navigation module
        """
        wx.MessageBox(
            _(
                "Navigation integration not yet connected.\n"
                "This will read the live coil pose once simnibs_handler is wired up."
            ),
            _("TODO"),
            wx.ICON_INFORMATION,
        )

    def _refresh_mat_display(self, mat=None):
        """Update the 4×4 matsimnibs text display."""
        import numpy as np

        m = mat if mat is not None else np.eye(4)
        lines = [
            f"[ {m[r, 0]:7.3f}  {m[r, 1]:7.3f}  {m[r, 2]:7.3f}  {m[r, 3]:8.2f} ]" for r in range(4)
        ]
        self.txt_mat.SetValue("\n".join(lines))

    def OnRunCharm(self, _evt):
        subject = self.txt_subject.GetValue().strip()
        t1 = self.txt_t1.GetValue().strip()
        t2 = self.txt_t2.GetValue().strip() or None
        outdir = self.txt_hm_out.GetValue().strip()

        if not subject or not t1 or not outdir:
            wx.MessageBox(
                _("Please fill in Subject ID, MRI file 1 path, and output folder."),
                _("Missing input"),
                wx.ICON_WARNING,
            )
            return

        if not os.path.isfile(t1):
            wx.MessageBox(
                _("MRI file 1 not found:\n{}").format(t1),
                _("Missing input"),
                wx.ICON_WARNING,
            )
            return

        m2m_preview = os.path.join(outdir, f"m2m_{subject}")
        forcerun = self.chk_forcerun.GetValue()

        msg = _(
            "charm will create the following folder on your computer:\n\n"
            "  {}\n\n"
            "This may take 30–60 minutes and requires several GB of free disk space."
            "{}"
            "\n\nProceed?"
        ).format(
            m2m_preview,
            _("\n\nThe existing folder will be overwritten (--forcerun is checked).")
            if forcerun and os.path.isdir(m2m_preview)
            else "",
        )

        if wx.MessageBox(msg, _("Run SimNIBS charm"), wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
            return

        os.makedirs(outdir, exist_ok=True)

        self._charm_runner = CharmRunner()
        self._charm_runner.start(
            subject,
            t1,
            t2,
            outdir,
            forcerun,
            self._charm_progress,
            self._charm_done,
        )

        self.btn_run_charm.Enable(False)
        self.btn_cancel_charm.Enable(True)
        self.gauge_charm.SetValue(0)
        self.lbl_charm.SetLabel(_("Starting charm…"))

    def OnCancelCharm(self, _evt):
        if hasattr(self, "_charm_runner"):
            self._charm_runner.cancel()
        self.btn_run_charm.Enable(True)
        self.btn_cancel_charm.Enable(False)
        self.lbl_charm.SetLabel(_("Cancelled."))

    def _charm_progress(self, message, percent):
        self.gauge_charm.SetValue(int(percent or 0))
        self.lbl_charm.SetLabel(message)

    def _charm_done(self, success, error, m2m_dir):
        self.btn_run_charm.Enable(True)
        self.btn_cancel_charm.Enable(False)

        if not success:
            self.gauge_charm.SetValue(0)
            self.lbl_charm.SetLabel(_("charm failed."))
            wx.MessageBox(error or _("charm failed."), _("SimNIBS error"), wx.ICON_ERROR)
            return

        self.gauge_charm.SetValue(100)
        self.lbl_charm.SetLabel(_("Head model complete."))

        # Update m2m path so the Simulation section can use it
        self._m2m_path = m2m_dir
        self.txt_m2m.SetValue(m2m_dir)
        self._save_path(_KEY_M2M_DIR, m2m_dir)
        self.btn_run_sim.Enable(True)

        # Let the user choose which NIfTI to open in the volume viewer
        dlg = wx.FileDialog(
            self,
            message=_("Select the NIfTI file to open in the volume viewer"),
            defaultDir=m2m_dir,
            wildcard=_("NIfTI (*.nii;*.nii.gz)|*.nii;*.nii.gz|All files (*.*)|*.*"),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            filepath = utils.decode(dlg.GetPath(), const.FS_ENCODE)
            Publisher.sendMessage("Open other files", filepath=filepath)
        dlg.Destroy()

    def OnLoadTissueSurfaces(self, _evt):
        """
        Browse for a tissue-label NIfTI, then create one
        InVesalius mask per label and generate a VTK surface for each.
        """
        start_dir = self._m2m_path or self.session.GetConfig(_KEY_M2M_DIR, "")
        dlg = wx.FileDialog(
            self,
            message=_("Select tissue-label NIfTI from the m2m folder"),
            defaultDir=start_dir,
            wildcard=_("NIfTI (*.nii;*.nii.gz)|*.nii;*.nii.gz|All files (*.*)|*.*"),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            filepath = utils.decode(dlg.GetPath(), const.FS_ENCODE)
            self._load_tissue_surfaces(filepath)
        dlg.Destroy()

    def _load_tissue_surfaces(self, labels_nii: str) -> None:
        """
        NIfTI pipeline:
          1. Read the selected tissue-label NIfTI.
          2. Discover unique non-zero integer labels present in the volume.
          3. Look up each label's name via the SimNIBS colour LUT.
          4. For each label: save a binary mask to a temp file, import it
             into InVesalius via pubsub ("Import Nifti mask"), then create
             a VTK surface via pubsub ("Create surface from index").
        """
        import nibabel as nib
        import numpy as np

        import invesalius.project as prj

        try:
            nii = nib.load(labels_nii)
            data = np.asarray(nii.dataobj)
        except Exception as exc:
            wx.MessageBox(
                _("Could not read NIfTI file:\n{}").format(exc),
                _("SimNIBS"),
                wx.ICON_ERROR,
            )
            return

        present = sorted(int(v) for v in np.unique(data) if v > 0)
        if not present:
            wx.MessageBox(
                _("No tissue labels found in the selected file."), _("SimNIBS"), wx.ICON_WARNING
            )
            return

        info = _label_info(present)
        created: list[tuple[int, str]] = []

        for label in present:
            name, _colour = info[label]
            mask_name = f"{name}"

            binary = (data == label).astype(np.uint8) * 255
            mask_img = nib.Nifti1Image(binary, nii.affine, nii.header)

            with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as fh:
                tmp = fh.name
            try:
                nib.save(mask_img, tmp)
                Publisher.sendMessage("Import Nifti mask", filepath=tmp, mask_name=mask_name)
                proj = prj.Project()
                idx = max(proj.mask_dict.keys())
                created.append((idx, mask_name))
            except Exception as exc:
                wx.MessageBox(
                    _("Could not import mask for label {} ({}):\n{}").format(label, name, exc),
                    _("SimNIBS"),
                    wx.ICON_WARNING,
                )
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

        for mask_idx, mask_name in created:
            surface_params = {
                "method": {
                    "algorithm": "ca_smoothing",
                    "options": {
                        "angle": 0.7,
                        "max distance": 3.0,
                        "min weight": 0.5,
                        "steps": 10,
                    },
                },
                "options": {
                    "index": mask_idx,
                    "name": mask_name,
                    "quality": _("Optimal *"),
                    "fill": False,
                    "fill_border_holes": False,
                    "keep_largest": True,
                    "overwrite": False,
                },
            }
            Publisher.sendMessage("Create surface from index", surface_parameters=surface_params)

    def OnRunSimulation(self, _evt):
        m2m_path = self.txt_m2m.GetValue().strip()
        out_dir = self.txt_sim_out.GetValue().strip()
        coil = self.txt_coil.GetValue().strip()

        try:
            didt = float(self.txt_didt.GetValue())
        except ValueError:
            wx.MessageBox(_("dI/dt must be a number."), _("Input error"), wx.ICON_WARNING)
            return

        if not m2m_path or not out_dir or not coil:
            wx.MessageBox(
                _("Please fill in m2m path, output folder, and coil file."),
                _("Missing input"),
                wx.ICON_WARNING,
            )
            return

        print(f"[SimNIBS] TODO: run simulation m2m={m2m_path!r}, coil={coil!r}, didt={didt}")
        self.btn_run_sim.Enable(False)
        self.btn_cancel_sim.Enable(True)
        self.gauge_sim.SetValue(0)
        self.lbl_sim.SetLabel(_("Simulation not yet connected — see TODO in OnRunSimulation."))

    def OnCancelSimulation(self, _evt):
        # TODO: call self._runner.cancel()
        self.btn_run_sim.Enable(True)
        self.btn_cancel_sim.Enable(False)
        self.lbl_sim.SetLabel(_("Cancelled."))

    def _sim_progress(self, message, percent):
        self.gauge_sim.SetValue(int(percent or 0))
        self.lbl_sim.SetLabel(message)

    def _sim_done(self, success, error, output_msh):
        self.btn_run_sim.Enable(True)
        self.btn_cancel_sim.Enable(False)
        if success and output_msh:
            self.gauge_sim.SetValue(100)
            self.lbl_sim.SetLabel(_("Simulation done. Loading E-field …"))
            Publisher.sendMessage(TOPIC_LOAD_RESULT, result_msh=output_msh)
        else:
            self.gauge_sim.SetValue(0)
            self.lbl_sim.SetLabel(_("Simulation failed."))
            wx.MessageBox(error or _("SimNIBS failed."), _("Simulation error"), wx.ICON_ERROR)

    # pubsub callbacks (fired by simnibs_handler)

    def _on_surfaces_loaded(self, surfaces):
        self.btn_run_sim.Enable(True)
        self.lbl_sim.SetLabel(_("Head surfaces loaded."))

    def _on_efield_loaded(self, stats):
        max_E = stats.get("max_E_Vm", 0.0)
        self.lbl_sim.SetLabel(f"E-field loaded. Peak: {max_E:.1f} V/m")
        self.gauge_sim.SetValue(0)

    def _on_progress(self, message, percent):
        self.gauge_sim.SetValue(int(percent or 0))
        self.lbl_sim.SetLabel(message)

    def _on_error(self, message):
        self.gauge_sim.SetValue(0)
        self.lbl_sim.SetLabel(f"Error: {message}")
        wx.MessageBox(message, _("SimNIBS error"), wx.ICON_ERROR | wx.OK)

    def OnToggleGM(self, evt):
        Publisher.sendMessage(TOPIC_SET_VISIBILITY, name="gm", visible=evt.IsChecked())

    def OnToggleSkin(self, evt):
        Publisher.sendMessage(TOPIC_SET_VISIBILITY, name="skin", visible=evt.IsChecked())

    def OnColormap(self, _evt):
        Publisher.sendMessage(TOPIC_SET_COLORMAP, colormap=self.combo_cmap.GetValue())

    def OnOpacity(self, _evt):
        Publisher.sendMessage(TOPIC_SET_OPACITY, name="skin", opacity=self.spin_opacity.GetValue())

    def OnThreshold(self, _evt):
        Publisher.sendMessage(TOPIC_SET_THRESHOLD, threshold_pct=self.spin_threshold.GetValue())

    def OnRemove(self, _evt):
        Publisher.sendMessage(TOPIC_REMOVE_SURFACES)

    @staticmethod
    def _head_msh_from_m2m(m2m_path: str) -> str:
        """m2m_ernie/ → m2m_ernie/ernie.msh"""
        folder = os.path.basename(os.path.normpath(m2m_path))
        subj = folder[4:] if folder.startswith("m2m_") else folder
        return os.path.join(m2m_path, f"{subj}.msh")

    @staticmethod
    def _next_session_dir(output_dir: str) -> str:
        """Return (and create) the next simulations/session_NNN/ folder."""
        sim_root = os.path.join(output_dir, "simulations")
        os.makedirs(sim_root, exist_ok=True)
        n = (
            len(
                [
                    d
                    for d in os.listdir(sim_root)
                    if d.startswith("session_") and os.path.isdir(os.path.join(sim_root, d))
                ]
            )
            + 1
        )
        path = os.path.join(sim_root, f"session_{n:03d}")
        os.makedirs(path, exist_ok=True)
        return path
