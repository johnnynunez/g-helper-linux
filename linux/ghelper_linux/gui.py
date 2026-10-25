"""GTK4 frontend. Startup and periodic collection never change hardware state."""
from concurrent.futures import ThreadPoolExecutor
import json
import sys

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk

from .viewmodel import control_state, format_reading, current_control_text


class Application(Gtk.Application):
    def __init__(self, backend, smoke_test=False):
        super().__init__(application_id='io.github.johnnynunez.GHelperLinux',
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.backend = backend
        self.smoke_test = smoke_test
        self.result = 1 if smoke_test else 0
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hardware')
        self.busy = False
        self.closing = False
        self.timer = None
        self.control_signature = None
        self.connect('activate', self.activate_window)
        self.connect('shutdown', self.shutdown_window)

    @staticmethod
    def label(text='', style=None):
        widget = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
        if style:
            widget.add_css_class(style)
        return widget

    def activate_window(self, _app):
        self.window = Gtk.ApplicationWindow(application=self, title='G-Helper Linux')
        self.window.set_default_size(780, 780)
        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label='G-Helper Linux · Experimental'))
        self.window.set_titlebar(header)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.window.set_child(scroll)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for side in ['top', 'bottom', 'start', 'end']:
            getattr(content, 'set_margin_' + side)(24)
        scroll.set_child(content)
        self.model = self.label('Detecting hardware…', 'title-1')
        content.append(self.model)
        self.identity = self.label('', 'dim-label')
        content.append(self.identity)
        self.gpu_name = self.label('', 'heading')
        content.append(self.gpu_name)
        self.metrics = Gtk.Grid(column_spacing=36, row_spacing=10, column_homogeneous=True)
        content.append(self.metrics)
        self.values = {}
        for index, title in enumerate(['GPU temperature', 'GPU utilization', 'GPU power', 'GPU memory used', 'Battery', 'Battery power']):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            cell.append(self.label(title, 'dim-label'))
            value = self.label('Not available', 'title-2')
            cell.append(value)
            self.values[title] = value
            self.metrics.attach(cell, index % 3, index // 3, 1, 1)
        content.append(self.label('Thermals & fans', 'heading'))
        self.sensors = self.label('Collecting…')
        content.append(self.sensors)
        content.append(Gtk.Separator())
        content.append(self.label('Controls', 'heading'))
        content.append(self.label('Editable fields are pending requests. “Current” is refreshed from hardware; press Apply to send a change.', 'dim-label'))
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.append(self.controls)
        self.notes = self.label('', 'dim-label')
        content.append(self.notes)
        self.status = self.label('Reading hardware…')
        content.append(self.status)
        self.window.present()
        self.collect()
        self.timer = GLib.timeout_add_seconds(3, self.collect)
        if self.smoke_test:
            GLib.timeout_add_seconds(12, self.smoke_timeout)

    def shutdown_window(self, *_):
        self.closing = True
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        self.pool.shutdown(wait=False, cancel_futures=True)

    def collect(self):
        if self.closing:
            return False
        if not self.busy:
            self.busy = True
            future = self.pool.submit(self.backend.snapshot)
            future.add_done_callback(lambda done: GLib.idle_add(self.collected, done, None))
        return True

    def collected(self, future, action_message):
        self.busy = False
        if self.closing:
            return False
        self.controls.set_sensitive(True)
        try:
            snapshot = future.result()
            self.render(snapshot)
            self.status.set_text(action_message or 'Live readings · refreshed every 3 seconds · no automatic hardware changes')
            if self.smoke_test:
                GLib.timeout_add(500, self.smoke_finish)
        except Exception as exc:
            self.status.set_text(f'Error: {exc}')
            if self.smoke_test:
                print(f'GTK4 smoke test failed: {exc}', file=sys.stderr)
                self.quit()
        return False

    def render(self, snapshot):
        identity = snapshot.get('identity', {})
        self.model.set_text(identity.get('model') or 'Unknown model')
        self.identity.set_text(' · '.join(str(identity.get(key) or 'Unknown') for key in ['vendor', 'architecture']))
        gpu = snapshot.get('gpu', {})
        battery = snapshot.get('battery', {})
        self.gpu_name.set_text(gpu.get('name') or 'GPU information unavailable')
        metrics = {
            'GPU temperature': format_reading(gpu.get('temperature_c'), '°C', 1),
            'GPU utilization': format_reading(gpu.get('utilization_percent'), '%'),
            'GPU power': format_reading(gpu.get('power_w'), 'W', 1),
            'GPU memory used': format_reading(gpu.get('memory_used_mib'), 'MiB'),
            'Battery': format_reading(battery.get('capacity_percent'), '%') + ' · ' + str(battery.get('status') or 'Unknown'),
            'Battery power': format_reading(battery.get('power_w'), 'W', 1),
        }
        for key, value in metrics.items():
            self.values[key].set_text(value)
        self.sensors.set_text('\n'.join(
            f"{row.get('label', 'Sensor')}: {format_reading(row.get('value'), row.get('unit', ''), 1 if row.get('unit') == '°C' else 0)}"
            for row in snapshot.get('sensors', [])) or 'No thermal or fan readings exposed by the kernel')
        signature = json.dumps({name: {key: cap.get(key) for key in ['available', 'writable', 'choices', 'max', 'reason']}
                                for name, cap in snapshot.get('capabilities', {}).items()}, sort_keys=True)
        if signature != self.control_signature:
            self.control_signature = signature
            self.build_controls(snapshot)
        for name, label in self.current_values.items():
            label.set_text(current_control_text(snapshot, name))
        notes = list(snapshot.get('warnings', []))
        notes.append('Manual fan control, CPU power limits and undervolting are not enabled in this experimental port.')
        self.notes.set_text('\n'.join(notes))

    def build_controls(self, snapshot):
        self.current_values = {}
        while child := self.controls.get_first_child():
            self.controls.remove(child)
        specs = [('platform_profile', 'Performance profile', self.backend.set_platform_profile),
                 ('charge_limit', 'Battery charge limit', self.backend.set_charge_limit),
                 ('keyboard_brightness', 'Keyboard brightness', self.backend.set_keyboard_brightness),
                 ('fn_lock', 'Fn Lock', self.backend.set_fn_lock)]
        for name, title, method in specs:
            cap = control_state(snapshot, name)
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            row = Gtk.Box(spacing=12)
            title_widget = self.label(title)
            title_widget.set_hexpand(True)
            row.append(title_widget)
            if name in ['platform_profile', 'fn_lock']:
                options = list(cap['choices']) if name == 'platform_profile' else ['off', 'on']
                selected = cap.get('value')
                if isinstance(selected, bool):
                    selected = 'on' if selected else 'off'
                choices = ['Choose…'] + options
                widget = Gtk.DropDown.new_from_strings(choices)
                widget.set_selected(choices.index(selected) if selected in choices else 0)
                def getter(widget=widget, options=options, name=name):
                    index = widget.get_selected() - 1
                    if index < 0 or index >= len(options):
                        raise ValueError('Choose a value before applying')
                    return options[index] == 'on' if name == 'fn_lock' else options[index]
            else:
                minimum = cap.get('min', 1) if name == 'charge_limit' else 0
                maximum = 100 if name == 'charge_limit' else (cap.get('max') or 3)
                widget = Gtk.SpinButton.new_with_range(minimum, maximum, 1)
                value = cap.get('value')
                widget.set_value(value if isinstance(value, (int, float)) else minimum)
                getter = widget.get_value_as_int
            widget.set_sensitive(cap['enabled'])
            row.append(widget)
            button = Gtk.Button(label='Apply')
            button.set_sensitive(cap['enabled'])
            button.connect('clicked', lambda _, method=method, getter=getter: self.apply_control(method, getter))
            row.append(button)
            section.append(row)
            current = self.label(current_control_text(snapshot, name), 'dim-label')
            self.current_values[name] = current
            section.append(current)
            if cap['reason']:
                section.append(self.label(cap['reason'], 'dim-label'))
            self.controls.append(section)

    def apply_control(self, method, getter):
        if self.busy:
            self.status.set_text('Hardware read in progress; please try again in a moment.')
            return
        try:
            value = getter()
        except ValueError as exc:
            self.status.set_text(str(exc))
            return
        self.busy = True
        self.controls.set_sensitive(False)
        self.status.set_text('Applying explicit change…')
        def operation():
            method(value)
            return self.backend.snapshot()
        future = self.pool.submit(operation)
        future.add_done_callback(lambda done: GLib.idle_add(self.collected, done, 'Command completed; see current readings and capability notes.'))

    def smoke_finish(self):
        if not self.closing:
            self.result = 0 if self.window.get_mapped() else 1
            if self.result == 0:
                print('GTK4 smoke test: window mapped, snapshot rendered, no hardware writes')
            self.quit()
        return False

    def smoke_timeout(self):
        if not self.closing:
            print('GTK4 smoke test: collection timed out', file=sys.stderr)
            self.quit()
        return False


def run(backend, smoke_test=False):
    app = Application(backend, smoke_test)
    code = app.run([])
    return app.result if code == 0 else code
