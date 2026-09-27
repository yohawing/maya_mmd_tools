"""Fit scene samples to quantized VMD curves, rejecting unrepresentable motion.

The acceptance oracle uses a 1/16 VMD-frame grid plus source key times.
This is a numerical check, not a continuous mathematical bound.
"""

import math
import struct
from bisect import bisect_left, bisect_right

POSITION_TOLERANCE = 0.001
ROTATION_TOLERANCE = math.radians(0.1)
MORPH_TOLERANCE = 0.001
LINEAR = (20, 20, 107, 107)


def validation_times(times, time_converter):
    """Plan an ascending 1/16 VMD-frame grid for the native Timeline sampler."""
    if not times:
        return []
    offset = float(time_converter(0.))
    rate = float(time_converter(1.)) - offset
    start = math.floor(float(time_converter(min(times))))
    end = math.ceil(float(time_converter(max(times))))
    return sorted(set(times) | {(i/16. - offset)/rate for i in range(start*16, end*16+1)})


def fit_step_track(key_times, sample):
    """Validate an IK step track on the integer VMD grid and at source events."""
    times = sorted(set(float(t) for t in key_times))
    if not times:
        return []
    start, end = math.floor(times[0]), math.ceil(times[-1])
    if start < 0 or end > 4294967295:
        raise ValueError("VMD IK frame range is invalid")
    witnesses = {i/16. for i in range(start*16, end*16+1)}
    witnesses.update(times)
    current = None
    result = []
    for t in sorted(witnesses):
        states = tuple(sample(t))
        if t == math.floor(t):
            if current != states:
                result.append({"frame_number": int(t), "visible": True, "ik_states": list(states)})
            current = states
        elif states != current:
            changed = [name for (name, a), (_, b) in zip(states, current or states) if a != b]
            raise ValueError(f"VMD IK step cannot be represented: {', '.join(changed)}, frame {t:g}")
    return result


def _float32(value):
    result = struct.unpack("<f", struct.pack("<f", float(value)))[0]
    if not math.isfinite(result):
        raise ValueError("VMD export encountered a non-finite value")
    return result


def _normalize(q):
    length = math.sqrt(sum(v * v for v in q))
    if not math.isfinite(length) or length < 1e-12:
        raise ValueError("VMD export encountered an invalid quaternion")
    return tuple(v / length for v in q)


def _angle(a, b):
    return 2 * math.acos(min(1., abs(sum(x * y for x, y in zip(_normalize(a), _normalize(b))))))


def _slerp(a, b, t):
    a, b = _normalize(a), _normalize(b)
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0:
        b, dot = tuple(-x for x in b), -dot
    if dot > .9995:
        return _normalize(tuple(x + (y - x) * t for x, y in zip(a, b)))
    angle = math.acos(min(1., dot))
    left, right = math.sin((1 - t) * angle), math.sin(t * angle)
    return tuple((left * x + right * y) / math.sin(angle) for x, y in zip(a, b))


def _parameter(x, x1, x2):
    low, high = 0., 1.
    for _ in range(28):
        t = (low + high) * .5
        value = 3 * (1-t)**2 * t * x1 + 3 * (1-t) * t*t * x2 + t**3
        if value < x:
            low = t
        else:
            high = t
    return (low + high) * .5


def bezier_value(controls, x):
    """Evaluate the stored seven-bit controls, including the X inversion."""
    x1, y1, x2, y2 = (v / 127. for v in controls)
    if x1 == y1 and x2 == y2:
        return x
    t = _parameter(x, x1, x2)
    return 3*(1-t)**2*t*y1 + 3*(1-t)*t*t*y2 + t**3


def _fit_progress(xs, ys, *, exhaustive=False):
    # Different X seeds can converge to different quantized minima. Refine
    # several seeds, and compare neighbouring Y integers after the LS solve.
    cache = {}

    def candidate(x1, x2):
        if (x1, x2) in cache:
            return cache[x1, x2]
        basis = []
        for x, y in zip(xs, ys):
            t = _parameter(x, x1 / 127., x2 / 127.)
            basis.append((3*(1-t)**2*t, 3*(1-t)*t*t, y - t**3))
        aa = sum(a*a for a, b, y in basis)
        ab = sum(a*b for a, b, y in basis)
        bb = sum(b*b for a, b, y in basis)
        ay = sum(a*y for a, b, y in basis)
        by = sum(b*y for a, b, y in basis)
        determinant = aa*bb - ab*ab
        best = (float("inf"), (x1, 0, x2, 127))
        if determinant > 1e-15:
            y1 = round(127*(ay*bb-by*ab)/determinant)
            y2 = round(127*(by*aa-ay*ab)/determinant)
            y1, y2 = max(0, min(127, y1)), max(0, min(127, y2))
            for a1 in range(max(0, y1-1), min(127, y1+1)+1):
                for a2 in range(max(0, y2-1), min(127, y2+1)+1):
                    error = max(abs(a*a1/127.+b*a2/127.-y) for a, b, y in basis)
                    best = min(best, (error, (x1, a1, x2, a2)))
        cache[x1, x2] = best
        return best

    seeds = sorted(candidate(x1, x2) for x1 in (0, 32, 64, 96, 127)
                   for x2 in (0, 32, 64, 96, 127))
    best = (max(abs(x-y) for x, y in zip(xs, ys)), LINEAR)
    for seed in seeds:
        current = seed
        for step in (16, 8, 4, 2, 1):
            center = current[1]
            for x1 in (center[0]-step, center[0], center[0]+step):
                for x2 in (center[2]-step, center[2], center[2]+step):
                    if 0 <= x1 <= 127 and 0 <= x2 <= 127:
                        current = min(current, candidate(x1, x2))
        best = min(best, current)
    if exhaustive:
        for x1 in range(128):
            for x2 in range(128):
                best = min(best, candidate(x1, x2))
    return best[1]


def _interpolation_bytes(controls):
    # VMD repeats the first 16-byte control block, shifted by each row.
    block = bytes(controls[channel][handle] for handle in range(4) for channel in range(4))
    return b"".join(block[row:] + bytes(row) for row in range(4))


def fit_scene_track(name, key_times, sample, *, morph=False, authored_times=None):
    """Return integer VMD keys from a callable evaluating CURRENT scene values.

    ``sample`` accepts fractional VMD frames and returns a scalar morph weight
    or ``(position_xyz, quaternion_xyzw)``. Insert integer keys when needed;
    reject a failing adjacent-frame interval instead of silently degrading it.
    ``key_times`` are witnesses; optional ``authored_times`` select the initial
    output keys so dense sampling does not force unnecessary requantization.
    """
    evaluate = sample

    def sample(t):
        value = evaluate(t)
        numbers = (value,) if morph else (*value[0], *value[1])
        if not all(math.isfinite(float(v)) for v in numbers):
            raise ValueError(f"VMD export encountered a non-finite value: {name}, frame {t}")
        if not morph:
            return tuple(value[0]), _normalize(value[1])
        return float(value)

    source_times = sorted(set(float(t) for t in key_times))
    if not source_times:
        return []
    if not all(math.isfinite(t) and 0 <= t <= 4294967295 for t in source_times):
        raise ValueError(f"VMD frame range is invalid: {name}")
    # Dense sampling times are witnesses, not mandatory output keys. Keeping
    # every dense key would re-quantize one Bezier into many less accurate ones.
    authored = source_times if authored_times is None else [float(t) for t in authored_times]
    selected = [source_times[0], source_times[-1], *authored]
    times = sorted({int(math.floor(t)) for t in selected} | {int(math.ceil(t)) for t in selected})
    all_witnesses = sorted(set(source_times) | set(authored))

    def endpoint(t):
        value = sample(t)
        if morph:
            encoded = _float32(value)
            error = abs(value-encoded) / MORPH_TOLERANCE
        else:
            p, q = value
            encoded = tuple(_float32(v) for v in p), tuple(_float32(v) for v in q)
            error = max(math.sqrt(sum((a-b)**2 for a,b in zip(p, encoded[0]))) / POSITION_TOLERANCE,
                        _angle(q, encoded[1]) / ROTATION_TOLERANCE)
        if error > 1:
            raise ValueError(f"VMD key precision exceeds tolerance: {name}, frame {t}, error/tolerance={error:g}")
        return encoded

    def payload(t, value, controls=None):
        if morph:
            return {"morph_name": name, "frame_number": t, "weight": value}
        return {"bone_name": name, "frame_number": t, "position": value[0], "rotation": value[1],
                "interpolation": _interpolation_bytes(controls or (LINEAR,) * 4)}

    first = endpoint(times[0])
    result = [payload(times[0], first)]

    def interval(start, end, left, right):
        witnesses = {i/16. for i in range(start*16+1, end*16)}
        witnesses.update(all_witnesses[bisect_right(all_witnesses, start):bisect_left(all_witnesses, end)])
        witnesses = sorted(witnesses)
        xs = [(t-start)/(end-start) for t in witnesses]
        values = [sample(t) for t in witnesses]
        sample_values = dict(zip(witnesses, values))
        fit_xs = [i/16. for i in range(1, 16)]
        fit_values = [sample_values[start+(end-start)*x] for x in fit_xs]
        controls = [LINEAR] * 4

        def errors():
            if morph:
                return max(abs(float(v) - (left+(right-left)*x)) / MORPH_TOLERANCE
                           for x, v in zip(xs, values))
            worst = 0.
            for x, (p, q) in zip(xs, values):
                predicted = [left[0][i] + (right[0][i]-left[0][i])*bezier_value(controls[i], x) for i in range(3)]
                distance = math.sqrt(sum((a-b)**2 for a, b in zip(p, predicted)))
                rotation = _slerp(left[1], right[1], bezier_value(controls[3], x))
                worst = max(worst, distance / POSITION_TOLERANCE, _angle(q, rotation) / ROTATION_TOLERANCE)
            return worst

        error = errors()
        if not morph and error > 1:
            for axis in range(3):
                delta = right[0][axis] - left[0][axis]
                if abs(delta) > 1e-12:
                    controls[axis] = _fit_progress(fit_xs, [(v[0][axis]-left[0][axis])/delta for v in fit_values])
            angle = _angle(left[1], right[1])
            if angle > 1e-12:
                controls[3] = _fit_progress(fit_xs, [_angle(left[1], v[1])/angle for v in fit_values])
            error = errors()
        if error > 1 and not morph and end-start <= 1:
            # Before rejecting an indivisible interval, search every seven-bit
            # X pair. Local minima must not reject a representable source curve.
            for axis in range(3):
                delta = right[0][axis] - left[0][axis]
                axis_error = max(abs(v[0][axis] - (left[0][axis] + delta * bezier_value(controls[axis], x)))
                                 for x, v in zip(xs, values))
                if abs(delta) > 1e-12 and axis_error > POSITION_TOLERANCE / math.sqrt(3):
                    controls[axis] = _fit_progress(fit_xs, [(v[0][axis]-left[0][axis])/delta for v in fit_values], exhaustive=True)
            angle = _angle(left[1], right[1])
            if angle > 1e-12:
                controls[3] = _fit_progress(fit_xs, [_angle(left[1], v[1])/angle for v in fit_values], exhaustive=True)
            error = errors()
        if not math.isfinite(error) or error > 1:
            if end-start <= 1:
                raise ValueError(f"VMD curve tolerance exceeded: {name}, frames {start}..{end}, "
                                 f"error/tolerance={error:.6g}")
            middle = (start+end)//2
            mid = endpoint(middle)
            interval(start, middle, left, mid)
            interval(middle, end, mid, right)
            return
        result.append(payload(end, right, controls))

    for start, end in zip(times, times[1:]):
        right = endpoint(end)
        interval(start, end, first, right)
        first = right
    return result
