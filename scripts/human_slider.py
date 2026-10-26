#!/usr/bin/env python3
"""scripts/human_slider.py — Biomechanically Authentic Human Slider Drag Solver.

Eliminates Aliyun Captcha 2.0 / 3007 behavioural bot rejection by modeling:
  1. Minimum Jerk Principle (Flash & Hogan, 1985) for bell-shaped velocity profiles.
  2. Sub-movement overshoot & micro-correction (Fitts's Law reaching dynamics).
  3. Physiological hand tremor (8-12 Hz bandpass Gaussian noise on Y axis).
  4. Zero in-drag DOM probing (no execution pauses that trigger Aliyun SDK timing flags).
  5. Dual-mode execution:
     - OS-Level xdotool (for headless Xvfb or physical X11 display)
     - High-density micro-stepped Playwright mouse dispatch
"""
from __future__ import annotations

import math
import os
import random
import subprocess
import time
from typing import Any


def minimum_jerk_step(u: float) -> float:
    """Minimum jerk polynomial: 10u^3 - 15u^4 + 6u^5 for u in [0, 1]."""
    u = max(0.0, min(1.0, u))
    return u * u * u * (10.0 + u * (-15.0 + 6.0 * u))


def generate_human_trajectory(
    start_x: float,
    start_y: float,
    target_dx: float,
    target_dy: float = 0.0,
    duration_s: float | None = None,
    sample_rate_hz: int = 100,
) -> list[tuple[float, float, float]]:
    """Generate dense, biologically authentic mouse movement points.

    Returns list of (x, y, dt_from_prev_s).
    """
    if duration_s is None:
        # Fitts's law heuristic: distance ~40-250px -> 450-850ms duration
        dist = math.hypot(target_dx, target_dy)
        duration_s = 0.45 + 0.0018 * dist + random.uniform(-0.05, 0.08)
        duration_s = max(0.40, min(1.10, duration_s))

    total_steps = int(duration_s * sample_rate_hz)
    total_steps = max(25, total_steps)

    # 1. Primary movement overshoot parameters
    # Humans typically overshoot fast reaching movements by 1.5 - 4.5% then correct
    has_overshoot = random.random() < 0.75
    overshoot_factor = 1.0 + random.uniform(0.015, 0.045) if has_overshoot else 1.0
    primary_ratio = random.uniform(0.78, 0.88)  # Primary phase ends at 80-85% of time

    # Hand tilt arc: humans naturally bow slightly in Y when dragging horizontally
    arc_height = random.uniform(-3.5, 3.5)
    # Hand tremor parameters (physiological tremor ~10Hz)
    tremor_freq = random.uniform(8.0, 12.0)
    tremor_amp = random.uniform(0.4, 1.2)

    points: list[tuple[float, float, float]] = []
    t_prev = 0.0

    for step in range(total_steps + 1):
        t = (step / total_steps) * duration_s
        dt = t - t_prev
        t_prev = t
        u = t / duration_s

        if has_overshoot:
            if u <= primary_ratio:
                # Primary reaching towards overshoot target
                u_prime = u / primary_ratio
                progress = minimum_jerk_step(u_prime) * overshoot_factor
            else:
                # Corrective sub-movement pulling back from overshoot to 1.0
                u_sub = (u - primary_ratio) / (1.0 - primary_ratio)
                sub_progress = minimum_jerk_step(u_sub)
                progress = overshoot_factor + (1.0 - overshoot_factor) * sub_progress
        else:
            progress = minimum_jerk_step(u)

        # Base smooth coordinates
        cur_x = start_x + target_dx * progress
        cur_y = start_y + target_dy * progress

        # Add physiological hand arc in Y: sin(pi * u)
        arc = math.sin(math.pi * u) * arc_height
        cur_y += arc

        # Add micro-tremor noise (higher in middle of motion, smaller at endpoints)
        tremor_envelope = math.sin(math.pi * u)
        noise_x = random.gauss(0.0, 0.35) * tremor_envelope
        noise_y = (math.sin(2.0 * math.pi * tremor_freq * t) * tremor_amp + random.gauss(0.0, 0.4)) * tremor_envelope

        cur_x += noise_x
        cur_y += noise_y

        points.append((cur_x, cur_y, dt))

    return points


async def drag_humanized_playwright(
    page: Any,
    box: dict[str, float],
    drag_dx: float,
    log=print,
) -> bool:
    """Execute humanized drag using high-density Playwright mouse events."""
    # Start near center of handle with realistic initial offset
    sx = box["x"] + box["width"] * random.uniform(0.35, 0.65)
    sy = box["y"] + box["height"] * random.uniform(0.35, 0.65)

    # 1. Natural hover before click
    await page.mouse.move(sx + random.uniform(-10, 10), sy + random.uniform(-5, 5))
    await page.wait_for_timeout(random.randint(60, 140))
    await page.mouse.move(sx, sy)
    await page.wait_for_timeout(random.randint(80, 160))

    # 2. Press down and hold
    await page.mouse.down()
    await page.wait_for_timeout(random.randint(70, 150))

    # 3. Generate and stream trajectory WITHOUT any page.evaluate() in the loop
    points = generate_human_trajectory(sx, sy, drag_dx)
    for px, py, dt in points:
        await page.mouse.move(px, py)
        if dt > 0.001:
            # Sleep in millisecond fractions
            await page.wait_for_timeout(int(dt * 1000))

    # 4. Settle at target position before release
    await page.wait_for_timeout(random.randint(90, 220))
    await page.mouse.up()
    await page.wait_for_timeout(random.randint(150, 300))
    return True


def drag_humanized_xdotool(
    window_x: int,
    window_y: int,
    box: dict[str, float],
    drag_dx: float,
    display: str = ":0",
) -> bool:
    """Execute humanized drag via OS-level xdotool (bypasses browser CDP entirely)."""
    env = dict(os.environ)
    env["DISPLAY"] = display

    sx = int(window_x + box["x"] + box["width"] * random.uniform(0.4, 0.6))
    sy = int(window_y + box["y"] + box["height"] * random.uniform(0.4, 0.6))

    points = generate_human_trajectory(sx, sy, drag_dx)

    try:
        # Hover & Click
        subprocess.run(["xdotool", "mousemove", str(sx), str(sy)], env=env, check=True)
        time.sleep(random.uniform(0.08, 0.15))
        subprocess.run(["xdotool", "mousedown", "1"], env=env, check=True)
        time.sleep(random.uniform(0.08, 0.14))

        # Stream movement
        for px, py, dt in points:
            subprocess.run(["xdotool", "mousemove", str(int(px)), str(int(py))], env=env, check=True)
            if dt > 0.002:
                time.sleep(dt)

        # Settle & Release
        time.sleep(random.uniform(0.10, 0.22))
        subprocess.run(["xdotool", "mouseup", "1"], env=env, check=True)
        time.sleep(0.15)
        return True
    except Exception as e:
        sys.stderr.write(f"[xdotool] drag error: {e}\n")
        return False
