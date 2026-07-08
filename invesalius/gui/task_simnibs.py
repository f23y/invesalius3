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
import shutil
import sys
import tempfile

import wx

import invesalius.constants as const
import invesalius.session as ses
import invesalius.utils as utils
from invesalius.i18n import tr as _
from invesalius.pubsub import pub as Publisher

log = logging.getLogger(__name__)

_KEY_M2M_DIR = "simnibs_last_m2m_dir"
_KEY_OUTPUT_DIR = "simnibs_last_output_dir"
_KEY_COIL_FILE = "simnibs_last_coil_file"
_KEY_T1_FILE = "simnibs_last_t1_file"
_KEY_T2_FILE = "simnibs_last_t2_file"

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
TOPIC_CHARM_DONE = "Charm done"

# Navigation module publishes current coil pose on this topic.
TOPIC_COIL_POSE = "From Neuronavigation: Send coil pose"


def _find_charm() -> str | None:
    path = shutil.which("charm")
    if path:
        return path
    for root in [
        os.path.expanduser("~/SimNIBS-4.6"),
        os.path.expanduser("~/SimNIBS-4.5"),
        os.path.expanduser("~/SimNIBS-4"),
        os.path.expanduser("~/SimNIBS-3.2"),
        os.path.expanduser("~/.local"),
        "/usr/local",
        "/opt/simnibs",
    ]:
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


def _read_lut() -> dict:
    """Parse final_tissues_FreeSurferColorLUT.txt bundled with SimNIBS.
    Returns {label: (name, (r, g, b))}. Returns {} if SimNIBS is not installed locally.
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


def _label_info(present_labels: list) -> dict:
    """Return {label: (name, (r, g, b))} for each label.
    Names come from the SimNIBS LUT when available; unknown labels get tissue_N.
    """
    lut = _read_lut()
    result: dict = {}
    for label in present_labels:
        if label in lut:
            result[label] = lut[label]
        else:
            result[label] = (f"tissue_{label}", (200, 200, 200))
    return result


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
    def __init__(self, parent):
        wx.Panel.__init__(self, parent)

        self.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_MENUBAR))

        self.session = ses.Session()
        self._m2m_path = None
        self._pose_locked = False
        self._matsimnibs = None
        self._running = None
        self._surface_names: list[str] = []
        self._mask_index_by_name: dict[str, int] = {}

        self._subscribe()
        self._build_ui()
        self._restore_paths()

    def _subscribe(self):
        Publisher.subscribe(self._on_surfaces_loaded, TOPIC_SURFACES_LOADED)
        Publisher.subscribe(self._on_efield_loaded, TOPIC_EFIELD_LOADED)
        Publisher.subscribe(self._on_progress, TOPIC_PROGRESS)
        Publisher.subscribe(self._on_error, TOPIC_ERROR)
        Publisher.subscribe(self._on_charm_done, TOPIC_CHARM_DONE)
        Publisher.subscribe(self._on_coil_pose, TOPIC_COIL_POSE)

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

        self.chk_force_qform = wx.CheckBox(self, -1, _("Force qform (--forceqform)"))
        self.chk_force_qform.SetValue(True)
        self.chk_force_qform.SetToolTip(
            _(
                "Replace sform with qform in the T1 NIfTI header.\n"
                "This is what charm itself recommends when it reports a\n"
                "qform/sform mismatch (the common case).\n\n"
                "Important: this overwrites the original sform. Neuronavigation\n"
                "software must then be set up with the SimNIBS-processed T1.nii.gz\n"
                "from the m2m_<subjectID> folder, not the original input MRI —\n"
                "otherwise coil pose import/export will be misaligned."
            )
        )
        self.chk_force_qform.Bind(wx.EVT_CHECKBOX, self.OnForceQform)
        sz_hm.Add(self.chk_force_qform, 0, wx.LEFT | wx.BOTTOM, 2)

        self.chk_force_sform = wx.CheckBox(self, -1, _("Force sform (--forcesform)"))
        self.chk_force_sform.SetToolTip(
            _(
                "Replace qform with sform in the T1 NIfTI header (strips shears).\n"
                "Only needed if the qform code is unknown/invalid — charm\n"
                "recommends Force qform for the common mismatch case instead."
            )
        )
        self.chk_force_sform.Bind(wx.EVT_CHECKBOX, self.OnForceSform)
        sz_hm.Add(self.chk_force_sform, 0, wx.LEFT | wx.BOTTOM, 2)

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
        self.lbl_charm = wx.StaticText(self, -1, _("When ready: "))
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
            _("No pose — navigation must send a coil pose first"),
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

        row_surf = wx.BoxSizer(wx.HORIZONTAL)
        row_surf.Add(
            wx.StaticText(self, -1, _("Surface:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4
        )
        self.combo_surface = wx.ComboBox(self, -1, style=wx.CB_DROPDOWN | wx.CB_READONLY)
        self.combo_surface.SetToolTip(
            _(
                "Choose which tissue surface the E-field is overlaid on.\n"
                "Populated after loading tissue surfaces from the m2m folder."
            )
        )
        self.combo_surface.Bind(wx.EVT_COMBOBOX, self.OnSurfaceSelect)
        row_surf.Add(self.combo_surface, 1)
        sz_ef.Add(row_surf, 0, wx.EXPAND | wx.ALL, 2)

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

    def OnForceQform(self, _evt):
        if self.chk_force_qform.GetValue():
            self.chk_force_sform.SetValue(False)

    def OnForceSform(self, _evt):
        if self.chk_force_sform.GetValue():
            self.chk_force_qform.SetValue(False)

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

        subject_dir = os.path.join(outdir, f"m2m_{subject}")
        forcerun = self.chk_forcerun.GetValue()
        force_qform = self.chk_force_qform.GetValue()
        force_sform = self.chk_force_sform.GetValue()

        msg = _(
            "charm will create the following folder on the SimNIBS server:\n\n"
            "  {}\n\n"
            "This may take 30–60 minutes and requires several GB of free disk space."
            "{}"
            "{}"
            "\n\nProceed?"
        ).format(
            subject_dir,
            _("\n\nThe existing folder will be overwritten (--forcerun is checked).")
            if forcerun
            else "",
            _(
                "\n\nNote: --forceqform will be used, which overwrites the sform in the "
                "NIfTI header.\nFor neuronavigation, use the SimNIBS-processed T1.nii.gz "
                "from the m2m folder,\nnot the original input MRI."
            )
            if force_qform
            else "",
        )

        if wx.MessageBox(msg, _("Run SimNIBS charm"), wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
            return

        mri_files = [t1] + ([t2] if t2 else [])
        Publisher.sendMessage(
            "SimNIBS: Run charm",
            subject_dir=subject_dir,
            mri_files=mri_files,
            forcerun=forcerun,
            force_qform=force_qform,
            force_sform=force_sform,
        )

        self._running = "charm"
        self.btn_run_charm.Enable(False)
        self.btn_cancel_charm.Enable(True)
        self.gauge_charm.SetValue(0)
        self.lbl_charm.SetLabel(_("Sent to SimNIBS server…"))

    def OnCancelCharm(self, _evt):
        Publisher.sendMessage("SimNIBS: Cancel charm")
        self._running = None
        self.btn_run_charm.Enable(True)
        self.btn_cancel_charm.Enable(False)
        self.lbl_charm.SetLabel(_("Cancel requested."))

    def _on_charm_done(self, m2m_dir):
        self._running = None
        self.btn_run_charm.Enable(True)
        self.btn_cancel_charm.Enable(False)
        self.gauge_charm.SetValue(100)
        self.lbl_charm.SetLabel(_("Head model complete."))
        if m2m_dir:
            self._m2m_path = m2m_dir
            self.txt_m2m.SetValue(m2m_dir)
            self._save_path(_KEY_M2M_DIR, m2m_dir)
            self.btn_run_sim.Enable(True)

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
            binary = (data == label).astype(np.uint8) * 255
            mask_img = nib.Nifti1Image(binary, nii.affine, nii.header)

            with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as fh:
                tmp = fh.name
            try:
                nib.save(mask_img, tmp)
                Publisher.sendMessage("Import Nifti mask", filepath=tmp, mask_name=name)
                proj = prj.Project()
                idx = max(proj.mask_dict.keys())
                created.append((idx, name))
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

        self._surface_names = [name for _, name in created]
        self._mask_index_by_name = {name: mask_idx for mask_idx, name in created}
        self._refresh_surface_dropdown()

    def _on_coil_pose(self, coord):
        """Store the latest coil pose broadcast by the navigation module."""
        import numpy as np
        from scipy.spatial.transform import Rotation

        if not coord or len(coord) < 6:
            return
        x, y, z, rx, ry, rz = coord
        R = Rotation.from_euler("xyz", [rx, ry, rz], degrees=True).as_matrix()
        mat = np.eye(4)
        mat[:3, :3] = R
        mat[:3, 3] = [x, y, z]
        self._matsimnibs = mat.tolist()

    def OnLockPose(self, _evt):
        import numpy as np

        if self._matsimnibs is None:
            wx.MessageBox(
                _("No coil pose received yet.\nThe navigation module must send a coil pose first."),
                _("No pose"),
                wx.ICON_WARNING,
            )
            return
        self._pose_locked = True
        self._refresh_mat_display(np.array(self._matsimnibs))

    def _refresh_mat_display(self, mat=None):
        import numpy as np

        m = mat if mat is not None else np.eye(4)
        lines = [
            f"[ {m[r, 0]:7.3f}  {m[r, 1]:7.3f}  {m[r, 2]:7.3f}  {m[r, 3]:8.2f} ]" for r in range(4)
        ]
        self.txt_mat.SetValue("\n".join(lines))

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

        payload: dict = {
            "m2m_dir": m2m_path,
            "output_dir": out_dir,
            "coil": coil,
            "didt": didt,
        }
        if self._matsimnibs is not None:
            payload["matsimnibs"] = self._matsimnibs

        Publisher.sendMessage("SimNIBS: Run simulation", **payload)

        self._running = "sim"
        self.btn_run_sim.Enable(False)
        self.btn_cancel_sim.Enable(True)
        self.gauge_sim.SetValue(0)
        self.lbl_sim.SetLabel(_("Sent to SimNIBS server…"))

    def OnCancelSimulation(self, _evt):
        Publisher.sendMessage("SimNIBS: Cancel simulation")
        self._running = None
        self.btn_run_sim.Enable(True)
        self.btn_cancel_sim.Enable(False)
        self.lbl_sim.SetLabel(_("Cancel requested."))

    def _on_surfaces_loaded(self, surfaces):
        self.btn_run_sim.Enable(True)
        self.lbl_sim.SetLabel(_("Head surfaces loaded."))

    def _on_efield_loaded(self, stats):
        self._running = None
        result_msh = stats.get("result_msh", "")
        label = os.path.basename(result_msh) if result_msh else _("Simulation complete.")
        self.lbl_sim.SetLabel(label)
        self.gauge_sim.SetValue(100)
        self.btn_run_sim.Enable(True)
        self.btn_cancel_sim.Enable(False)

    def _on_progress(self, message, percent):
        if self._running == "charm":
            self.gauge_charm.SetValue(int(percent or 0))
            self.lbl_charm.SetLabel(message)
        else:
            self.gauge_sim.SetValue(int(percent or 0))
            self.lbl_sim.SetLabel(message)

    def _on_error(self, message):
        self._running = None
        self.gauge_charm.SetValue(0)
        self.gauge_sim.SetValue(0)
        self.lbl_charm.SetLabel(_("Error."))
        self.lbl_sim.SetLabel(f"Error: {message}")
        self.btn_run_charm.Enable(True)
        self.btn_cancel_charm.Enable(False)
        self.btn_run_sim.Enable(True)
        self.btn_cancel_sim.Enable(False)
        wx.MessageBox(message, _("SimNIBS error"), wx.ICON_ERROR | wx.OK)

    def OnSurfaceSelect(self, _evt):
        import invesalius.project as prj

        selected = self.combo_surface.GetStringSelection()
        surface_dict = prj.Project().surface_dict
        index_by_name = {surface.name: index for index, surface in surface_dict.items()}

        for name in self._surface_names:
            index = index_by_name.get(name)
            if index is not None:
                Publisher.sendMessage("Show surface", index=index, visibility=(name == selected))

        for name in self._surface_names:
            if name == selected:
                continue
            mask_index = self._mask_index_by_name.get(name)
            if mask_index is not None:
                Publisher.sendMessage("Show mask", index=mask_index, value=False)

        selected_mask_index = self._mask_index_by_name.get(selected)
        if selected_mask_index is not None:
            Publisher.sendMessage("Change mask selected", index=selected_mask_index)
            Publisher.sendMessage("Show mask", index=selected_mask_index, value=True)

    def _refresh_surface_dropdown(self) -> None:
        self.combo_surface.Clear()
        for name in self._surface_names:
            self.combo_surface.Append(name)
        if self._surface_names:
            self.combo_surface.SetSelection(0)

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
