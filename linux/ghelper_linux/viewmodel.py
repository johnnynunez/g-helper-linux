"""Pure presentation rules shared by GTK and tests."""
import math
import numbers


def format_reading(value, unit='', precision=0):
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value):
        return 'Not available'
    return f'{value:.{precision}f}' + (f' {unit}' if unit else '')


def current_control_text(snapshot, name):
    value = snapshot.get('capabilities', {}).get(name, {}).get('value')
    if value is None:
        text = 'Not available'
    elif isinstance(value, bool):
        text = 'On' if value else 'Off'
    elif isinstance(value, str):
        text = value
    else:
        text = format_reading(value, '%' if name == 'charge_limit' else '')
    return 'Current: ' + text


def control_state(snapshot, name):
    cap = snapshot.get('capabilities', {}).get(name, {})
    enabled = bool(cap.get('available') and cap.get('writable'))
    return {**cap, 'enabled': enabled, 'choices': cap.get('choices') or [],
            'reason': cap.get('reason') or ('' if enabled else 'Not exposed or not writable on this system')}
