# -*- coding: utf-8; -*-

# Copyright (C) 2015 Lionel Ott
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
Main UI of JoystickGremlin.
"""

import ctypes
import importlib
import logging
import os
import sys
import threading
import time

os.environ["PYSDL2_DLL_PATH"] = os.path.dirname(os.path.realpath(sys.argv[0]))
import sdl2
import sdl2.ext
import sdl2.hints

from gremlin.code_generator import CodeGenerator
from gremlin import event_handler, input_devices, util
from ui_about import Ui_About
from ui_gremlin import Ui_Gremlin
import ui_widgets
from ui_widgets import *


class CodeRunner(object):

    """Runs the actual profile code."""

    def __init__(self):
        """Creates a new code runner instance."""
        self.event_handler = event_handler.EventHandler()
        self.event_handler.add_plugin(input_devices.JoystickPlugin())
        self.event_handler.add_plugin(input_devices.VJoyPlugin())
        self.event_handler.add_plugin(input_devices.KeyboardPlugin())

        self._running = False

    def start(self):
        """Starts listening to events and loads all existing callbacks."""
        # Reset states to their default values
        self._reset_state()

        # Load the generated code
        try:
            import gremlin_code
            importlib.reload(gremlin_code)

            # Create callbacks
            callback_count = 0
            for hardware_id, modes in input_devices.callback_registry.items():
                for mode, callbacks in modes.items():
                    for event, callback_list in callbacks.items():
                        for callback in callback_list:
                            self.event_handler.add_callback(
                                hardware_id,
                                mode,
                                event,
                                callback[0],
                                callback[1]
                            )
                            callback_count += 1

            # Connect signals
            el = event_handler.EventListener()
            kb = input_devices.Keyboard()
            el.keyboard_event.connect(self.event_handler.process_event)
            el.joystick_event.connect(self.event_handler.process_event)
            el.keyboard_event.connect(kb.keyboard_event)

            self.event_handler.change_mode("global")
            self.event_handler.resume()
            self._running = True
        except ImportError as e:
            util.display_error(
                "Unable to launch due to missing custom modules: {}"
                .format(str(e))
            )

    def stop(self):
        """Stops listening to events and unloads all callbacks."""
        # Disconnect all signals
        if self._running:
            el = event_handler.EventListener()
            kb = input_devices.Keyboard()
            el.keyboard_event.disconnect(self.event_handler.process_event)
            el.joystick_event.disconnect(self.event_handler.process_event)
            el.keyboard_event.disconnect(kb.keyboard_event)

        # Empty callback registry
        input_devices.callback_registry = {}
        self.event_handler.clear()

    def _reset_state(self):
        """Resets all states to their default values."""
        self.event_handler._active_mode = "global"
        self.event_handler._previous_mode = "global"


class Repeater(QtCore.QObject):

    """Responsible to repeatedly emit a set of given events.

    The class receives a list of events that are to be emitted in
    sequence. The events are emitted in a separate thread and the
    emission cannot be aborted once it started. While events are
    being emitted a change of events is not performed to prevent
    continuous emitting of events.
    """

    def __init__(self, events):
        """Creates a new instance.

        :param events the list of events to emit
        """
        QtCore.QObject.__init__(self)
        self.is_running = False
        self._events = events
        self._thread = threading.Thread(target=self.emit_events)
        self._start_timer = threading.Timer(1.0, self.run)
        self._stop_timer = threading.Timer(5.0, self.stop)

    @property
    def events(self):
        return self._events

    @events.setter
    def events(self, events):
        """Sets the list of events to execute and queues execution.

        Starts emitting the list of events after a short delay. If a
        new list of events is received before the timeout, the old timer
        is destroyed and replaced with a new one for the new list of
        events. Once events are being emitted all change requests will
        be ignored.

        :param events the list of events to emit
        """
        if self.is_running or len(events) == 0:
            return
        self._events = events
        if self._start_timer:
            self._start_timer.cancel()
        self._start_timer = threading.Timer(1.0, self.run)
        self._start_timer.start()

    def stop(self):
        """Stops the event dispatch thread."""
        self.is_running = False

    def run(self):
        """Starts the event dispatch thread."""
        if self._thread.is_alive():
            return
        self.is_running = True
        self._stop_timer = threading.Timer(5.0, self.stop)
        self._stop_timer.start()
        self._thread = threading.Thread(target=self.emit_events)
        self._thread.start()

    def emit_events(self):
        """Emits events until stopped."""
        index = 0
        el = EventListener()
        while self.is_running:
            if self.events[0].event_type == InputType.Keyboard:
                el.keyboard_event.emit(self._events[index])
            else:
                el.joystick_event.emit(self._events[index])
            index = (index + 1) % len(self._events)
            time.sleep(0.5)


class CalibrationUi(QtWidgets.QWidget):

    """Dialog to calibrate joystick axes."""

    def __init__(self, parent=None):
        """Creates the calibration UI.

        :param parent the parent widget of this object
        """
        QtWidgets.QWidget.__init__(self, parent)
        self.devices = [dev for dev in util.joystick_devices() if not dev.is_virtual]
        self.current_hardware_id = 0

        # Create the required layouts
        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.axes_layout = QtWidgets.QVBoxLayout()
        self.button_layout = QtWidgets.QHBoxLayout()

        # Device selection drop down
        self.device_dropdown = QtWidgets.QComboBox()
        self.device_dropdown.currentIndexChanged.connect(self._create_axes)
        for device in self.devices:
            self.device_dropdown.addItem(device.name)

        # Various buttons
        self.button_close = QtWidgets.QPushButton("Close")
        self.button_close.pressed.connect(self.close)
        self.buton_save = QtWidgets.QPushButton("Save")
        self.buton_save.pressed.connect(self._save_calibration)
        self.button_centered = QtWidgets.QPushButton("Centered")
        self.button_centered.pressed.connect(self._calibrate_centers)
        self.button_layout.addWidget(self.buton_save)
        self.button_layout.addWidget(self.button_close)
        self.button_layout.addStretch(0)
        self.button_layout.addWidget(self.button_centered)

        # Axis widget readout headers
        self.label_layout = QtWidgets.QGridLayout()
        label_spacer = QtWidgets.QLabel()
        label_spacer.setMinimumWidth(200)
        label_spacer.setMaximumWidth(200)
        self.label_layout.addWidget(label_spacer, 0, 0, 0, 3)
        label_current = QtWidgets.QLabel("<b>Current</b>")
        label_current.setAlignment(QtCore.Qt.AlignRight)
        self.label_layout.addWidget(label_current, 0, 3)
        label_minimum = QtWidgets.QLabel("<b>Minimum</b>")
        label_minimum.setAlignment(QtCore.Qt.AlignRight)
        self.label_layout.addWidget(label_minimum, 0, 4)
        label_center = QtWidgets.QLabel("<b>Center</b>")
        label_center.setAlignment(QtCore.Qt.AlignRight)
        self.label_layout.addWidget(label_center, 0, 5)
        label_maximum = QtWidgets.QLabel("<b>Maximum</b>")
        label_maximum.setAlignment(QtCore.Qt.AlignRight)
        self.label_layout.addWidget(label_maximum, 0, 6)

        # Organizing everything into the various layouts
        self.main_layout.addWidget(self.device_dropdown)
        self.main_layout.addLayout(self.label_layout)
        self.main_layout.addLayout(self.axes_layout)
        self.main_layout.addStretch(0)
        self.main_layout.addLayout(self.button_layout)

        # Create the axis calibration widgets
        self.axes = []
        self._create_axes(self.current_hardware_id)

        # Connect to the joystick events
        el = EventListener()
        el.joystick_event.connect(self._handle_event)

    def _calibrate_centers(self):
        """Records the centered or neutral position of the current device."""
        for widget in self.axes:
            widget.centered()

    def _save_calibration(self):
        """Saves the current calibration data to the harddrive."""
        cfg = util.Configuration()
        dev_id = self.devices[self.current_hardware_id].hardware_id
        cfg.set_calibration(dev_id, [axis.limits for axis in self.axes])

    def _create_axes(self, hardware_id):
        """Creates the axis calibration widget sfor the current device.

        :param hardware_id the index of the currently selected device
            in the dropdown menu
        """
        ui_widgets._clear_layout(self.axes_layout)
        self.axes = []
        self.current_hardware_id = hardware_id
        config = util.Configuration()
        for i in range(self.devices[hardware_id].axes):
            self.axes.append(AxisCalibrationWidget())
            self.axes_layout.addWidget(self.axes[-1])

    def _handle_event(self, event):
        """Process a single joystick event.

        :param event the event to process
        """
        if event.hardware_id == self.devices[self.current_hardware_id].hardware_id \
                and event.event_type == InputType.JoystickAxis:
            self.axes[event.identifier-1].set_current(event.raw_value)

    def closeEvent(self, event):
        """Closes the calibration window.

        :param event the close event
        """
        el = EventListener()
        el.joystick_event.disconnect(self._handle_event)


class GremlinAboutUi(QtWidgets.QWidget):

    """Widget which displays information about the application."""

    def __init__(self, parent=None):
        """Creates a new about widget.

        This creates a simple widget which shows version information
        and various software licenses.

        :param parent parent of this widget
        """
        QtWidgets.QWidget.__init__(self, parent)
        self.ui = Ui_About()
        self.ui.setupUi(self)

        self.ui.about.setHtml(open("about/about.html").read())

        self.ui.jg_license.setHtml(
            open("about/joystick_gremlin.html").read()
        )

        license_list = [
            "about/third_party_licenses.html",
            "about/modernuiicons.html",
            "about/pyhook.html",
            "about/pyqt.html",
            "about/pysdl2.html",
            "about/pywin32.html",
            "about/qt5.html",
            "about/sdl2.html",
            "about/vjoy.html",
            "about/mako.html",
        ]
        third_party_licenses = ""
        for fname in license_list:
            third_party_licenses += open(fname).read()
        self.ui.third_party_licenses.setHtml(third_party_licenses)


class ModuleManagerUi(QtWidgets.QWidget):

    """UI which allows the user to manage custom python modules to
    be loaded by the program."""

    def __init__(self, profile_data, parent=None):
        """Creates a new instance.

        :param profile_data the profile with which to populate the ui
        :param parent the parent widget
        """
        QtWidgets.QWidget.__init__(self, parent)
        self._profile = profile_data
        self.setWindowTitle("User Module Manager")

        self._create_ui()

    def _create_ui(self):
        """Creates all the UI elements."""
        self.model = QtCore.QStringListModel()
        self.model.setStringList(sorted(self._profile.imports))

        self.view = QtWidgets.QListView()
        self.view.setModel(self.model)
        self.view.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

        # Add widgets which allow modifying the mode list
        self.add = QtWidgets.QPushButton(
            QtGui.QIcon("gfx/macro_add.svg"), "Add"
        )
        self.add.clicked.connect(self._add_cb)
        self.delete = QtWidgets.QPushButton(
            QtGui.QIcon("gfx/macro_delete.svg"), "Delete"
        )
        self.delete.clicked.connect(self._delete_cb)

        self.actions_layout = QtWidgets.QHBoxLayout()
        self.actions_layout.addWidget(self.add)
        self.actions_layout.addWidget(self.delete)

        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.addWidget(self.view)
        self.main_layout.addLayout(self.actions_layout)

    def _add_cb(self):
        """Asks the user for the name of a new module to add to the list
        of imported modules.

        If the name is not a valid python identifier nothing is added.
        """
        new_import, input_ok = QtWidgets.QInputDialog.getText(
            self,
            "Module name",
            "Enter the name of the module to import"
        )
        if input_ok and new_import != "":
            if not util.valid_python_identifier(new_import):
                util.display_error(
                    "\"{}\" is not a valid python module name"
                    .format(new_import)
                )
            else:
                import_list = self.model.stringList()
                import_list.append(new_import)
                self.model.setStringList(sorted(import_list))
                self._profile.imports = list(import_list)

    def _delete_cb(self):
        """Removes the currently selected module from the list."""
        import_list = self.model.stringList()
        index = self.view.currentIndex().row()
        if 0 <= index <= len(import_list):
            del import_list[index]
            self.model.setStringList(import_list)
            self.view.setCurrentIndex(self.model.index(0, 0))
            self._profile.imports = list(import_list)


class GremlinUi(QtWidgets.QMainWindow):

    """Main window of the Joystick Gremlin user interface."""

    def __init__(self, parent=None):
        """Creates a new main ui window.

        :param parent the parent of this window
        """
        QtWidgets.QMainWindow.__init__(self, parent)
        self.ui = Ui_Gremlin()
        self.ui.setupUi(self)

        self.tabs = {}
        self.config = util.Configuration()
        self.devices = util.joystick_devices()
        self.runner = CodeRunner()
        self.repeater = Repeater([])
        self.runner.event_handler.mode_changed.connect(
            self._update_statusbar_mode
        )
        self.runner.event_handler.is_active.connect(
            self._update_statusbar_active
        )

        self.mode_selector = ModeWidget()
        self.ui.toolBar.addWidget(self.mode_selector)

        self._current_mode = "global"
        self._profile = profile.Profile()
        self._profile_fname = None
        if self.config.default_profile:
            self._do_load_profile(self.config.default_profile)
        else:
            self.new_profile()

        self._setup_icons()
        self._connect_actions()
        self._create_tabs()
        self._create_statusbar()
        self._update_statusbar_active(False)

    def load_profile(self):
        """Prompts the user to select a profile file to load."""
        fname, _ = QtWidgets.QFileDialog.getOpenFileName(
            None,
            "Load Profile",
            None,
            "XML files (*.xml)"
        )
        if fname != "":
            self._do_load_profile(fname)
            self.config.default_profile = fname

    def _do_load_profile(self, fname):
        """Load the profile with the given filename.

        :param fname the name of the profile file to load
        """
        new_profile = profile.Profile()
        try:
            new_profile.from_xml(fname)
        except TypeError as e:
            logging.exception("Invalid profile content:\n{}".format(e))
            new_profile = profile.Profile()

        profile_folder = os.path.dirname(fname)
        if profile_folder not in sys.path:
            sys.path.insert(0, profile_folder)

        self._sanitize_profile(new_profile)
        self._profile = new_profile
        self._profile_fname = fname

        # Update device profiles
        self._create_tabs()

    def new_profile(self):
        """Creates a new empty profile."""
        self._profile = profile.Profile()
        # For each connected device create a new empty device entry
        # in the new profile
        for device in [entry for entry in self.devices if not entry.is_virtual]:
            new_device = profile.Device(self._profile)
            new_device.name = device.name
            new_device.hardware_id = device.hardware_id
            new_device.windows_id = device.windows_id
            new_device.type = profile.DeviceType.Joystick
            self._profile.devices[new_device.hardware_id] = new_device
        keyboard_device = profile.Device(self._profile)
        keyboard_device.name = "keyboard"
        keyboard_device.hardware_id = 0
        keyboard_device.windows_id = 0
        keyboard_device.type = profile.DeviceType.Keyboard
        self._profile.devices[keyboard_device.hardware_id] = keyboard_device

        self._create_tabs()

    def save_profile(self):
        """Saves the current profile to the hard drive.

        If the file was loaded from an existing profile that file is
        updated, otherwise the user is prompted for a new file.
        """
        if self._profile_fname:
            self._profile.to_xml(self._profile_fname)
        else:
            self.save_profile_as()

    def save_profile_as(self):
        """Prompts the user for a file to save to profile to."""
        fname, _ = QtWidgets.QFileDialog.getSaveFileName(
            None,
            "Save Profile",
            None,
            "XML files (*.xml)"
        )
        if fname != "":
            self._profile.to_xml(fname)
            self._profile_fname = fname

    def activate(self, checked):
        """Activates and deactivates the code runner.

        :param checked True when the runner is to be activated, False
            otherwise
        """
        if checked:
            self.generate()
            self.runner.start()
            self._update_statusbar_active(True)
        else:
            self.runner.stop()
            self._update_statusbar_active(False)

    def generate(self):
        """Generates python code for the code runner from the current
        profile.
        """
        generator = CodeGenerator(self._profile)
        generator.write_code(
            os.path.join(
                util.appdata_path(),
                "gremlin_code.py"
            )
        )

    def device_information(self):
        """Opens the device information window."""
        self.device_information = DeviceInformationWidget(self.devices)
        geom = self.geometry()
        self.device_information.setGeometry(
            geom.x() + geom.width() / 2 - 150,
            geom.y() + geom.height() / 2 - 75,
            300,
            150
        )
        self.device_information.show()

    def manage_custom_modules(self):
        """Opens the custom module management window."""
        self.module_manager = ModuleManagerUi(self._profile)
        self.module_manager.show()

    def input_repeater(self):
        """Enables or disables the forwarding of events to the repeater."""
        el = EventListener()
        if self.ui.actionInputRepeater.isChecked():
            el.keyboard_event.connect(self._handle_input_repeat)
            el.joystick_event.connect(self._handle_input_repeat)
        else:
            el.keyboard_event.disconnect(self._handle_input_repeat)
            el.joystick_event.disconnect(self._handle_input_repeat)

    def calibration(self):
        """Opens the calibration window."""
        self.calibration_window = CalibrationUi()
        self.calibration_window.show()

    def about(self):
        """Opens the about window."""
        self.about_window = GremlinAboutUi()
        self.about_window.show()

    def _handle_input_repeat(self, event):
        """Performs setup for event repetition.

        :param event the event to repeat
        """
        vjoy_device_id = [dev.hardware_id for dev in self.devices if dev.is_virtual][0]
        # Ignore VJoy events
        if self.repeater.is_running or event.hardware_id == vjoy_device_id:
            return
        # Ignore small joystick movements
        elif event.event_type == InputType.JoystickAxis and abs(event.value) < 0.25:
            return
        # Ignore neutral hat positions
        if event.event_type == InputType.JoystickHat and event.value == (0, 0):
            return

        event_list = []
        if event.event_type in [InputType.Keyboard, InputType.JoystickButton]:
            event_list = [event.clone(), event.clone()]
            event_list[0].is_pressed = False
            event_list[1].is_pressed = True
        elif event.event_type == InputType.JoystickAxis:
            event_list = [
                event.clone(),
                event.clone(),
                event.clone(),
                event.clone()
            ]
            event_list[0].value = -0.75
            event_list[1].value = 0.0
            event_list[2].value = 0.75
            event_list[3].value = 0.0
        elif event.event_type == InputType.JoystickHat:
            event_list = [event.clone(), event.clone()]
            event_list[0].value = (0, 0)

        self.repeater.events = event_list

    def _sanitize_profile(self, profile_data):
        """Validates a profile file before actually loading it.

        :param profile_data the profile to verify
        """
        profile_devices = {}
        for device in profile_data.devices.values():
            # Ignore the keyboard
            if device.hardware_id == 0:
                continue
            profile_devices[device.key()] = device.name

        physical_devices = {}
        for device in self.devices:
            if device.is_virtual:
                continue
            physical_devices[(device.hardware_id, device.windows_id)] = device.name

        for dev_id, dev_name in profile_devices.items():
            if dev_id not in physical_devices:
                logging.warning(
                    "Removing missing device \"{}\" with id {} from profile"
                    .format(dev_name, dev_id)
                )
                del profile_data.devices[dev_id]

    def _create_tabs(self):
        """Creates the tabs of the configuration dialog representing
        the different connected devices.
        """
        # Update mode selector and disconnect any existing signals
        self.mode_selector.populate_selector(
            self._profile,
            self._current_mode
        )
        for tab in self.tabs.values():
            self.mode_selector.mode_changed.disconnect(tab._mode_changed_cb)

        # Create actual tabs
        self.ui.devices.clear()
        self.tabs = {}
        # Create joystick devices
        vjoy_devices = [dev for dev in self.devices if dev.is_virtual]
        phys_devices = [dev for dev in self.devices if not dev.is_virtual]
        for device in phys_devices:
            device_profile = self._profile.get_device_modes(
                (device.hardware_id, device.windows_id), device.name
            )

            widget = DeviceWidget(
                vjoy_devices,
                device,
                device_profile,
                self
            )
            self.mode_selector.mode_changed.connect(widget._mode_changed_cb)
            self.tabs[(device.name, device.windows_id)] = widget
            self.ui.devices.addTab(widget, device.name)

        # Create keyboard tab
        device_profile = self._profile.get_device_modes((0, 0), "keyboard")
        widget = DeviceWidget(
            vjoy_devices,
            None,
            device_profile,
            self
        )
        self.mode_selector.mode_changed.connect(widget._mode_changed_cb)
        self.tabs[("Keyboard", 0)] = widget
        self.ui.devices.addTab(widget, "Keyboard")

    def _create_statusbar(self):
        """Creates the ui widgets used in the status bar."""
        self.status_bar_mode = QtWidgets.QLabel("")
        self.status_bar_mode.setContentsMargins(5, 0, 5, 0)
        self.status_bar_is_active = QtWidgets.QLabel("")
        self.status_bar_is_active.setContentsMargins(5, 0, 5, 0)
        self.ui.statusbar.addWidget(self.status_bar_is_active, )
        self.ui.statusbar.addWidget(self.status_bar_mode, 1)

    def _get_device_profile(self, device):
        """Returns a profile for the given device.

        If no profile exists for the given device a new empty one is
        created.

        :param device the device for which to return the profile
        :return profile for the provided device
        """
        if device.hardware_id in self._profile.devices:
            device_profile = self._profile.devices[device.hardware_id]
        else:
            device_profile = {}

        return device_profile

    def _connect_actions(self):
        """Connects all QAction items to their corresponding callbacks."""
        # Menu actions
        self.ui.actionLoadProfile.triggered.connect(self.load_profile)
        self.ui.actionNewProfile.triggered.connect(self.new_profile)
        self.ui.actionSaveProfile.triggered.connect(self.save_profile)
        self.ui.actionSaveProfileAs.triggered.connect(self.save_profile_as)
        self.ui.actionDeviceInformation.triggered.connect(self.device_information)
        self.ui.actionManageCustomModules.triggered.connect(self.manage_custom_modules)
        self.ui.actionInputRepeater.triggered.connect(self.input_repeater)
        self.ui.actionCalibration.triggered.connect(self.calibration)
        self.ui.actionAbout.triggered.connect(self.about)

        # Toolbar actions
        self.ui.actionActivate.triggered.connect(self.activate)
        self.ui.actionGenerate.triggered.connect(self.generate)
        self.ui.actionOpen.triggered.connect(self.load_profile)

    def _setup_icons(self):
        """Sets the icons of all QAction items."""
        # Menu actions
        self.ui.actionLoadProfile.setIcon(QtGui.QIcon("gfx/profile_open.svg"))
        self.ui.actionNewProfile.setIcon(QtGui.QIcon("gfx/profile_new.svg"))
        self.ui.actionSaveProfile.setIcon(QtGui.QIcon("gfx/profile_save.svg"))
        self.ui.actionSaveProfileAs.setIcon(QtGui.QIcon("gfx/profile_save_as.svg"))
        self.ui.actionDeviceInformation.setIcon(QtGui.QIcon("gfx/device_information.svg"))
        self.ui.actionManageCustomModules.setIcon(QtGui.QIcon("gfx/manage_modules.svg"))
        self.ui.actionAbout.setIcon(QtGui.QIcon("gfx/about.svg"))

        # Toolbar actions
        activate_icon = QtGui.QIcon()
        activate_icon.addPixmap(
            QtGui.QPixmap("gfx/activate.svg"),
            QtGui.QIcon.Normal
        )
        activate_icon.addPixmap(
            QtGui.QPixmap("gfx/activate_on.svg"),
            QtGui.QIcon.Active,
            QtGui.QIcon.On
        )
        self.ui.actionActivate.setIcon(activate_icon)
        self.ui.actionGenerate.setIcon(QtGui.QIcon("gfx/generate.svg"))
        self.ui.actionOpen.setIcon(QtGui.QIcon("gfx/profile_open.svg"))

    def _update_statusbar_mode(self, mode):
        """Updates the status bar display of the current mode.

        :param mode the now current mode
        """
        self.status_bar_mode.setText("<b>Mode:</b> {}".format(mode))

    def _update_statusbar_active(self, is_active):
        """Updates the status bar with the current state of the system.

        :param is_active True if the system is active, False otherwise
        """
        if is_active:
            text_active = "<font color=\"green\">Active</font>"
        else:
            text_active = "<font color=\"red\">Paused</font>"
        if self.ui.actionActivate.isChecked():
            text_running = "Running and {}".format(text_active)
        else:
            text_running = "Not Running"

        self.status_bar_is_active.setText(
            "<b>Status: </b> {}".format(text_running)
        )


if __name__ == "__main__":
    sys.path.insert(0, util.appdata_path())
    util.setup_appdata()
    logging.basicConfig(
        filename=os.path.join(util.appdata_path(), "debug.log"),
        format="%(asctime)s %(levelname)10s %(message)s",
        datefmt="%Y-%m-%d %H:%M",
        level=logging.DEBUG
    )
    logging.debug("Starting Joystick Gremlin R1")

    # Initialize SDL
    sdl2.SDL_Init(sdl2.SDL_INIT_JOYSTICK)
    sdl2.SDL_SetHint(
                sdl2.hints.SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS,
                ctypes.c_char_p(b"1")
    )
    sdl2.ext.init()

    # Create user interface
    app_id = u"joystick.gremlin.r1"
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(QtGui.QIcon("gfx/icon.png"))
    ui = GremlinUi()
    ui.show()

    # Run UI
    app.exec_()

    # Terminate potentially running EventListener loop
    el = event_handler.EventListener()
    el.terminate()

    sys.exit(0)