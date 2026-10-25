"""Presentation tests use explicitly labelled fixtures, never dashboard demo data."""
import unittest
from ghelper_linux.viewmodel import format_reading, control_state, current_control_text


class PresentationTests(unittest.TestCase):
    def test_missing_and_non_finite_readings_are_not_zero(self):
        for value in [None, float('nan'), float('inf'), 'N/A', True]:
            with self.subTest(value=value):
                self.assertEqual(format_reading(value, 'W'), 'Not available')
        self.assertEqual(format_reading(0, 'RPM'), '0 RPM')
        self.assertEqual(format_reading(42.5, '°C', 1), '42.5 °C')

    def test_controls_require_both_availability_and_write_access(self):
        for cap in [{}, {'available': True}, {'available': False, 'writable': True}]:
            state = control_state({'capabilities': {'fn_lock': cap}}, 'fn_lock')
            self.assertFalse(state['enabled'])
            self.assertTrue(state['reason'])
        cap = {'available': True, 'writable': True, 'choices': ['quiet'], 'value': 'quiet'}
        state = control_state({'capabilities': {'platform_profile': cap}}, 'platform_profile')
        self.assertTrue(state['enabled'])
        self.assertEqual(state['choices'], ['quiet'])

    def test_current_hardware_value_is_distinct_from_pending_selection(self):
        self.assertEqual(current_control_text({'capabilities': {}}, 'fn_lock'), 'Current: Not available')
        for value, expected in [(False, 'Current: Off'), (True, 'Current: On')]:
            snapshot = {'capabilities': {'fn_lock': {'value': value}}}
            self.assertEqual(current_control_text(snapshot, 'fn_lock'), expected)
        snapshot = {'capabilities': {'keyboard_brightness': {'value': 1}}}
        self.assertEqual(current_control_text(snapshot, 'keyboard_brightness'), 'Current: 1')
        snapshot['capabilities']['keyboard_brightness']['value'] = 2
        self.assertEqual(current_control_text(snapshot, 'keyboard_brightness'), 'Current: 2')
        self.assertEqual(current_control_text({'capabilities': {'charge_limit': {'value': 80}}}, 'charge_limit'), 'Current: 80 %')

    @unittest.skipUnless(__import__('os').environ.get('GHELPER_GUI_TEST') == '1', 'requires a real GTK4 display')
    def test_native_window_maps_and_closes_without_hardware_writes(self):
        from ghelper_linux.gui import run, Application
        from unittest.mock import patch
        class ReadOnlyBackend:
            calls = 0
            def write(self, _value):
                raise AssertionError('startup must not change hardware')
            set_platform_profile = set_charge_limit = set_keyboard_brightness = set_fn_lock = write
            def snapshot(self):
                self.calls += 1
                return {'identity': {'model': 'GUI test fixture', 'architecture': 'test'},
                        'gpu': {}, 'battery': {}, 'sensors': [],
                        'capabilities': {'keyboard_brightness': {'available': False, 'writable': False, 'max': None},
                                         'platform_profile': {'available': True, 'writable': False,
                                                              'choices': ['low-power', 'balanced', 'performance'],
                                                              'value': 'balanced'}},
                        'warnings': []}
        backend = ReadOnlyBackend()
        rendered = []
        original_render = Application.render
        def render_and_check(app, snapshot):
            original_render(app, snapshot)
            rendered.append(app.current_values['platform_profile'].get_text())
            section = app.controls.get_first_child()
            picker = section.get_first_child().get_first_child().get_next_sibling()
            picker.set_selected(1)  # pending low-power selection, never applied
            snapshot['capabilities']['platform_profile']['value'] = 'performance'
            original_render(app, snapshot)
            rendered.append(app.current_values['platform_profile'].get_text())
            self.assertIs(app.controls.get_first_child(), section)
            self.assertEqual(picker.get_selected(), 1)
        with patch.object(Application, 'render', render_and_check):
            self.assertEqual(run(backend, smoke_test=True), 0)
        self.assertEqual(rendered, ['Current: balanced', 'Current: performance'])
        self.assertGreaterEqual(backend.calls, 1)


if __name__ == '__main__':
    unittest.main()
