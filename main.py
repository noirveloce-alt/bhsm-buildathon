from __future__ import annotations

import math
import os
import random
import shutil
import sys
import struct
import tempfile
import wave
from pathlib import Path

from ursina import *
from ursina.prefabs.first_person_controller import FirstPersonController


ROOM_HALF = 6.0
LOOP_SECONDS = 120.0
MAX_PARADOX = 3


def cube(parent=scene, position=(0, 0, 0), scale=(1, 1, 1), tint=None,
         rotation=(0, 0, 0), collider=None, name=None):
    """Create a dependable colored primitive using Ursina's built-in texture."""
    tint = readable_tint(tint)
    kwargs = dict(
        parent=parent,
        model="cube",
        texture="white_cube",
        color=tint or color.white,
        position=position,
        scale=scale,
        rotation=rotation,
        collider=collider,
    )
    # Ursina forwards keyword values through setattr; its node-name setter rejects None.
    if name is not None:
        kwargs["name"] = name
    return Entity(**kwargs)


def sphere(parent=scene, position=(0, 0, 0), scale=(1, 1, 1), tint=None,
           name=None):
    tint = readable_tint(tint)
    kwargs = dict(parent=parent, model="sphere", texture="white_cube",
                  color=tint or color.white, position=position, scale=scale)
    if name is not None:
        kwargs["name"] = name
    return Entity(**kwargs)


def readable_tint(tint):
    """Lift the room's darker palette so furniture details read in-game."""
    if tint is None:
        return color.white
    try:
        if tint.a <= 0.01:  # Keep transparent collision proxies transparent.
            return tint
        return tint.tint(0.14)
    except Exception:
        return tint


def hitbox(position, scale, tag, name):
    """Invisible collider used for both aiming and the controller's collision."""
    entity = cube(position=position, scale=scale, tint=color.rgba(0, 0, 0, 0),
                  collider="box", name=name)
    entity.visible = False
    entity.interact_tag = tag
    return entity


def _write_tone(path, seconds, sample_fn, sample_rate=22050):
    count = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        frames = bytearray()
        for i in range(count):
            t = i / sample_rate
            value = max(-1.0, min(1.0, sample_fn(t, seconds)))
            frames.extend(struct.pack("<h", int(value * 32767)))
        out.writeframes(frames)


class AudioManager:
    """Audio is optional: a missing device or decoder never prevents play."""

    def __init__(self):
        self.tick = None
        self.hum = None
        self.effects = {}
        self.enabled = False
        self._temp_dir = None
        try:
            self._temp_dir = tempfile.mkdtemp(prefix="paradox_audio_")
            tick_path = Path(self._temp_dir) / "tick.wav"
            hum_path = Path(self._temp_dir) / "room_tone.wav"
            step_path = Path(self._temp_dir) / "footstep.wav"
            fix_path = Path(self._temp_dir) / "fixed.wav"
            reset_path = Path(self._temp_dir) / "loop_reset.wav"
            collapse_path = Path(self._temp_dir) / "collapse.wav"
            success_path = Path(self._temp_dir) / "stabilized.wav"

            def tick_wave(t, duration):
                envelope = math.exp(-t * 42.0)
                return math.sin(2 * math.pi * 1250 * t) * envelope * 0.58

            def room_tone(t, duration):
                fade = min(1.0, t * 18, (duration - t) * 18)
                return fade * (0.42 * math.sin(2 * math.pi * 48 * t)
                               + 0.18 * math.sin(2 * math.pi * 73 * t))

            def footstep_wave(t, duration):
                envelope = math.exp(-t * 34.0)
                return envelope * (0.48 * math.sin(2 * math.pi * 82 * t)
                                   + 0.23 * math.sin(2 * math.pi * 164 * t))

            def fix_wave(t, duration):
                envelope = max(0.0, math.sin(math.pi * t / duration)) ** 1.2
                return envelope * (0.23 * math.sin(2 * math.pi * 523.25 * t)
                                   + 0.20 * math.sin(2 * math.pi * 659.25 * t)
                                   + 0.17 * math.sin(2 * math.pi * 783.99 * t))

            def reset_wave(t, duration):
                envelope = math.exp(-t * 2.0)
                pulse = 0.5 + 0.5 * math.cos(2 * math.pi * 2.2 * t)
                return envelope * pulse * (0.62 * math.sin(2 * math.pi * 54 * t)
                                           + 0.20 * math.sin(2 * math.pi * 108 * t))

            noise = random.Random(4102)
            collapse_noise = [noise.uniform(-1, 1) for _ in range(int(2.8 * 22050))]

            def collapse_wave(t, duration):
                index = min(len(collapse_noise) - 1, int(t * 22050))
                swell = 0.45 + 0.30 * math.sin(2 * math.pi * 0.55 * t)
                bass = 0.60 * math.sin(2 * math.pi * (39 + 9 * t) * t)
                return swell * (bass + 0.18 * collapse_noise[index])

            def success_wave(t, duration):
                envelope = math.exp(-t * 1.35)
                return envelope * (0.26 * math.sin(2 * math.pi * 523.25 * t)
                                   + 0.22 * math.sin(2 * math.pi * 659.25 * t)
                                   + 0.18 * math.sin(2 * math.pi * 783.99 * t)
                                   + 0.12 * math.sin(2 * math.pi * 1046.5 * t))

            _write_tone(tick_path, 0.11, tick_wave)
            _write_tone(hum_path, 2.4, room_tone)
            _write_tone(step_path, 0.16, footstep_wave)
            _write_tone(fix_path, 0.85, fix_wave)
            _write_tone(reset_path, 0.90, reset_wave)
            _write_tone(collapse_path, 2.8, collapse_wave)
            _write_tone(success_path, 2.1, success_wave)
            self.tick = Audio(str(tick_path), volume=0.46, autoplay=False,
                              loop=False, auto_destroy=False)
            self.hum = Audio(str(hum_path), volume=0.22, autoplay=True,
                             loop=True, auto_destroy=False)
            for name, path, volume in (
                ("footstep", step_path, 0.50),
                ("fix", fix_path, 0.40),
                ("reset", reset_path, 0.52),
                ("collapse", collapse_path, 0.68),
                ("success", success_path, 0.48),
            ):
                self.effects[name] = Audio(str(path), volume=volume,
                                           autoplay=False, loop=False,
                                           auto_destroy=False)
            self.enabled = True
        except Exception:
            self.enabled = False

    def play_tick(self, paradox=0):
        if self.enabled and self.tick:
            try:
                self.tick.pitch = random.uniform(0.94, 1.06) if paradox else 1.0
                self.tick.play()
            except Exception:
                pass

    def play_effect(self, name):
        effect = self.effects.get(name)
        if self.enabled and effect:
            try:
                effect.play()
            except Exception:
                pass

    def stop(self):
        if self.enabled and self.hum:
            try:
                self.hum.stop(destroy=False)
            except Exception:
                pass

    def cleanup(self):
        if self._temp_dir:
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass


class Room:
    """The single hotel room and its resettable timeline fragments."""

    def __init__(self):
        self.dynamic_entities = []
        self.base_wall_color = color.rgb(139, 119, 96)
        self.base_light_color = color.rgb(255, 224, 176)
        self.fx_time = 0.0
        self.flicker_remaining = 0.0
        self.random_motion = 0.0
        self._build_shell()
        self._build_bed_and_bedside()
        self._build_desk_and_books()
        self._build_wardrobe()
        self._build_window()
        self._build_painting()
        self._build_clock()
        self._build_door()
        self._build_time_machine()
        self._build_decor()
        self.reset_to_normal()

    def _build_shell(self):
        # Thick box colliders make the room reliably solid for the FPS controller.
        cube(position=(0, -0.14, 0), scale=(12.2, 0.28, 12.2),
             tint=color.rgb(67, 46, 32), collider="box", name="wood floor")
        cube(position=(0, 4.32, 0), scale=(12.2, 0.22, 12.2),
             tint=color.rgb(89, 76, 61), collider="box", name="ceiling")
        self.walls = []
        self.walls.append(cube(position=(-6.08, 2.08, 0), scale=(0.22, 4.3, 12.2),
                               tint=self.base_wall_color, collider="box", name="west wall"))
        self.walls.append(cube(position=(6.08, 2.08, 0), scale=(0.22, 4.3, 12.2),
                               tint=self.base_wall_color, collider="box", name="east wall"))
        self.walls.append(cube(position=(0, 2.08, 6.08), scale=(12.2, 4.3, 0.22),
                               tint=self.base_wall_color, collider="box", name="north wall"))
        # The south wall has a door opening near its right corner.
        self.walls.append(cube(position=(-1.60, 2.08, -6.08), scale=(8.8, 4.3, 0.22),
                               tint=self.base_wall_color, collider="box", name="south wall left"))
        self.walls.append(cube(position=(5.40, 2.08, -6.08), scale=(1.2, 4.3, 0.22),
                               tint=self.base_wall_color, collider="box", name="south wall right"))

        # Wood planks add direction and scale to the floor without external textures.
        for row in range(12):
            z = -5.45 + row * 0.98
            offset = 0.45 if row % 2 else 0.0
            for col in range(6):
                x = -5.1 + col * 2.0 + offset
                if x > 5.25:
                    continue
                tint = color.rgb(79 + (row % 3) * 4, 52 + (col % 2) * 4, 37)
                cube(position=(x, 0.008, z), scale=(1.91, 0.025, 0.91), tint=tint)

        # Aged lower wall trim gives the room a finished edge.
        for x in (-5.91, 5.91):
            cube(position=(x, 0.18, 0), scale=(0.13, 0.32, 11.9),
                 tint=color.rgb(65, 43, 31))
        cube(position=(0, 0.18, 5.91), scale=(11.9, 0.32, 0.13),
             tint=color.rgb(65, 43, 31))
        for x, width in ((-1.60, 8.8), (5.40, 1.2)):
            cube(position=(x, 0.18, -5.91), scale=(width, 0.32, 0.13),
                 tint=color.rgb(65, 43, 31))

        cube(position=(0, 0.045, 0.15), scale=(4.4, 0.05, 3.4),
             tint=color.rgb(76, 25, 31))
        # Fine rug border, a restrained detail visible in the warm light.
        for z in (-1.47, 1.77):
            cube(position=(0, 0.078, z), scale=(4.06, 0.018, 0.035),
                 tint=color.rgb(146, 103, 66))
        for x in (-2.03, 2.03):
            cube(position=(x, 0.078, 0.15), scale=(0.035, 0.018, 3.24),
                 tint=color.rgb(146, 103, 66))

        # Built-in lights only; shadows stay disabled for broad hardware support.
        try:
            AmbientLight(color=color.rgba(132, 122, 108, 255))
            self.room_light = PointLight(parent=scene, position=(-1.25, 2.65, 3.75),
                                         color=self.base_light_color)
        except Exception:
            self.room_light = None
        self.lamp_glow = sphere(position=(-1.25, 2.52, 3.75), scale=(0.20, 0.18, 0.20),
                                tint=color.rgb(255, 218, 147), name="lamp glow")
        self.room_light_source = cube(position=(-1.25, 2.50, 3.75), scale=(0.04, 0.04, 0.04),
                                      tint=color.rgb(255, 226, 160))

    def _build_bed_and_bedside(self):
        bx, bz = -3.55, 1.30
        # Raised frame, mattress, blanket, two pillows and headboard.
        cube(position=(bx, 0.36, bz), scale=(2.68, 0.40, 4.10),
             tint=color.rgb(67, 42, 29), collider="box", name="bed frame")
        cube(position=(bx, 0.72, bz - 0.02), scale=(2.48, 0.36, 3.90),
             tint=color.rgb(188, 173, 146), collider="box", name="mattress")
        cube(position=(bx, 0.94, bz - 0.38), scale=(2.46, 0.18, 2.48),
             tint=color.rgb(80, 48, 47), name="bed blanket")
        for dx in (-0.62, 0.62):
            cube(position=(bx + dx, 1.00, bz + 1.49), scale=(0.90, 0.22, 0.70),
                 tint=color.rgb(218, 207, 181), name="pillow")
        cube(position=(bx, 1.24, bz + 2.08), scale=(2.78, 1.55, 0.22),
             tint=color.rgb(72, 46, 32), collider="box", name="headboard")
        # Brass nail heads on the headboard.
        for dx in (-1.12, 0, 1.12):
            sphere(position=(bx + dx, 1.78, bz + 1.94), scale=(0.055, 0.055, 0.035),
                   tint=color.rgb(178, 132, 72))
        hitbox((bx, 0.60, bz), (2.7, 1.15, 4.15), "bed", "bed collision")

        # Bedside table, one drawer, small pull, and a warm table lamp.
        tx, tz = -1.40, 3.73
        cube(position=(tx, 0.62, tz), scale=(1.18, 1.16, 0.92),
             tint=color.rgb(74, 47, 33), collider="box", name="bedside table")
        cube(position=(tx, 1.24, tz), scale=(1.30, 0.15, 1.04),
             tint=color.rgb(104, 67, 43))
        cube(position=(tx, 0.81, tz - 0.49), scale=(0.74, 0.34, 0.035),
             tint=color.rgb(91, 58, 39))
        sphere(position=(tx, 0.82, tz - 0.535), scale=(0.09, 0.09, 0.05),
               tint=color.rgb(192, 145, 79))
        cube(position=(tx, 1.38, tz), scale=(0.24, 0.10, 0.24),
             tint=color.rgb(132, 92, 56))
        cube(position=(tx, 1.74, tz), scale=(0.09, 0.66, 0.09),
             tint=color.rgb(132, 92, 56))
        sphere(position=(tx, 2.10, tz), scale=(0.54, 0.56, 0.48),
               tint=color.rgb(218, 190, 142))
        hitbox((tx, 0.72, tz), (1.3, 1.45, 1.1), "telephone", "bedside inspect area")

    def _build_desk_and_books(self):
        dx, dz = 2.65, 4.40
        # The desk has a solid, waist-high body collider with a thick top.
        cube(position=(dx, 0.66, dz), scale=(3.25, 1.24, 1.28),
             tint=color.rgb(76, 48, 32), collider="box", name="desk body")
        cube(position=(dx, 1.36, dz), scale=(3.48, 0.16, 1.52),
             tint=color.rgb(104, 68, 43), name="desk top")
        for side in (-1, 1):
            cube(position=(dx + side * 0.90, 0.86, dz - 0.67),
                 scale=(0.16, 0.16, 0.035), tint=color.rgb(50, 34, 26))
            cube(position=(dx + side * 0.90, 0.86, dz - 0.71),
                 scale=(0.07, 0.045, 0.045), tint=color.rgb(191, 147, 78))
        # Four short tapered-looking legs beneath the drawer body.
        for xoff in (-1.35, 1.35):
            for zoff in (-0.48, 0.48):
                cube(position=(dx + xoff, 0.23, dz + zoff),
                     scale=(0.18, 0.48, 0.18), tint=color.rgb(61, 39, 27))

        self.book_roots = []
        covers = [color.rgb(105, 49, 45), color.rgb(47, 68, 65), color.rgb(125, 93, 52)]
        for index, x in enumerate((1.95, 2.55, 3.15)):
            root = Entity(parent=scene, position=(x, 1.46, 4.26), name=f"field book {index + 1}")
            cube(parent=root, position=(0, 0.09, 0), scale=(0.48, 0.18, 0.36),
                 tint=color.rgb(206, 193, 162))
            cube(parent=root, position=(0, 0.19, -0.005), scale=(0.50, 0.07, 0.38),
                 tint=covers[index])
            cube(parent=root, position=(-0.21, 0.19, -0.005), scale=(0.045, 0.072, 0.39),
                 tint=color.rgb(199, 165, 102))
            self.book_roots.append(root)
        self.book_target = hitbox((2.56, 1.68, 4.24), (1.95, 0.60, 0.65),
                                  "books", "book area")

        # Desk chair is one assembled object on a rotatable root.
        # Local chair back is toward +Z; yaw 180 makes it face north, at the desk.
        self.chair_root = Entity(parent=scene, position=(2.58, 0, 2.52),
                                 rotation_y=180, name="chair root")
        self.chair_hitbox = hitbox((0, 0, 0), (0.96, 1.34, 0.92), "chair", "chair body")
        self.chair_hitbox.parent = self.chair_root
        self.chair_hitbox.position = (0, 0.67, 0)
        cube(parent=self.chair_root, position=(0, 0.62, 0), scale=(0.94, 0.18, 0.88),
             tint=color.rgb(79, 51, 35))
        cube(parent=self.chair_root, position=(0, 1.17, 0.34), scale=(0.94, 1.04, 0.18),
             tint=color.rgb(73, 46, 33))
        for xoff in (-0.36, 0.36):
            for zoff in (-0.30, 0.30):
                cube(parent=self.chair_root, position=(xoff, 0.30, zoff),
                     scale=(0.12, 0.62, 0.12), tint=color.rgb(56, 38, 28))
        self.chair_target_yaw = 180.0
        self.chair_fixing = False

        # A folded note, telephone and desk lamp provide period detail.
        cube(position=(2.20, 1.47, 4.86), scale=(0.70, 0.035, 0.44),
             tint=color.rgb(210, 198, 167))
        cube(position=(2.20, 1.49, 4.86), scale=(0.38, 0.018, 0.30),
             tint=color.rgb(226, 216, 190))
        self.note_target = hitbox((2.20, 1.62, 4.86), (0.82, 0.48, 0.54),
                                  "note", "experiment note target")
        cube(position=(1.37, 1.52, 4.56), scale=(0.43, 0.25, 0.36),
             tint=color.rgb(75, 69, 59), name="desk telephone")
        for i in range(6):
            sphere(position=(1.24 + (i % 3) * 0.10, 1.66, 4.44 + (i // 3) * 0.10),
                   scale=(0.035, 0.025, 0.035), tint=color.rgb(181, 166, 133))
        self.telephone_target = hitbox((1.37, 1.56, 4.54), (0.65, 0.65, 0.55),
                                       "telephone", "telephone target")

        # Chair collision geometry follows its root, so the anomalous turn stays solid.
        self.chair_target = self.chair_hitbox

    def _build_wardrobe(self):
        x, z = -5.10, -1.70
        cube(position=(x, 1.40, z), scale=(1.35, 2.78, 1.52),
             tint=color.rgb(65, 42, 31), collider="box", name="wardrobe")
        for side in (-1, 1):
            cube(position=(x + side * 0.34, 1.42, z - 0.78),
                 scale=(0.64, 2.56, 0.055), tint=color.rgb(86, 56, 40))
            sphere(position=(x + side * 0.07, 1.39, z - 0.83), scale=(0.075, 0.13, 0.06),
                   tint=color.rgb(183, 139, 76))
        cube(position=(x, 2.94, z), scale=(1.53, 0.16, 1.70), tint=color.rgb(78, 50, 36))
        hitbox((x, 1.38, z), (1.45, 2.8, 1.60), "wardrobe", "wardrobe collision")

    def _build_window(self):
        x, y, z = 3.62, 2.52, 5.80
        self.window_root = Entity(parent=scene, position=(x, y, z), name="window")
        # Night glass is a single dark blue plane; slim bars create a recognizable sash.
        cube(parent=self.window_root, position=(0, 0, 0), scale=(2.42, 1.72, 0.08),
             tint=color.rgb(13, 23, 39))
        for px in (-1.25, 1.25):
            cube(parent=self.window_root, position=(px, 0, -0.06), scale=(0.13, 2.05, 0.16),
                 tint=color.rgb(92, 61, 43))
        for py in (-0.96, 0.96):
            cube(parent=self.window_root, position=(0, py, -0.06), scale=(2.62, 0.13, 0.16),
                 tint=color.rgb(92, 61, 43))
        cube(parent=self.window_root, position=(0, 0, -0.07), scale=(0.10, 1.78, 0.10),
             tint=color.rgb(92, 61, 43))
        cube(parent=self.window_root, position=(0, 0, -0.075), scale=(2.48, 0.09, 0.10),
             tint=color.rgb(92, 61, 43))
        # Quiet night details.
        for px, py, size in ((-0.86, 0.56, 0.04), (0.62, 0.62, 0.03),
                             (0.91, -0.24, 0.035), (-0.38, -0.48, 0.025)):
            sphere(parent=self.window_root, position=(px, py, -0.08),
                   scale=(size, size, 0.025), tint=color.rgb(184, 190, 198))
        self.window_impossible = Entity(parent=self.window_root, enabled=False,
                                        name="impossible reflection")
        # A complete miniature room seems to continue beyond the glass.
        cube(parent=self.window_impossible, position=(0, 0, -0.12), scale=(2.22, 1.52, 0.03),
             tint=color.rgb(62, 56, 53))
        cube(parent=self.window_impossible, position=(0, -0.46, -0.15), scale=(1.90, 0.46, 0.04),
             tint=color.rgb(87, 65, 55))
        cube(parent=self.window_impossible, position=(-0.44, -0.17, -0.18), scale=(0.78, 0.34, 0.05),
             tint=color.rgb(150, 137, 117))
        cube(parent=self.window_impossible, position=(-0.44, -0.31, -0.20), scale=(0.73, 0.12, 0.05),
             tint=color.rgb(88, 45, 48))
        cube(parent=self.window_impossible, position=(-0.44, 0.10, -0.18), scale=(0.82, 0.18, 0.07),
             tint=color.rgb(74, 49, 36))
        cube(parent=self.window_impossible, position=(0.54, 0.20, -0.18), scale=(0.50, 0.55, 0.07),
             tint=color.rgb(94, 68, 51))
        cube(parent=self.window_impossible, position=(0.54, 0.20, -0.23), scale=(0.39, 0.46, 0.03),
             tint=color.rgb(133, 91, 57))
        cube(parent=self.window_impossible, position=(0.04, 0.55, -0.17), scale=(0.64, 0.13, 0.06),
             tint=color.rgb(89, 58, 40))
        self.window_target = hitbox((x, y, 5.58), (2.65, 2.05, 0.36), "window", "window target")

        # Thick curtains, a rail, and small brass tiebacks.
        for side in (-1, 1):
            cube(position=(x + side * 1.43, 2.52, 5.38), scale=(0.44, 2.75, 0.35),
                 tint=color.rgb(47, 55, 54))
            cube(position=(x + side * 1.43, 2.52, 5.18), scale=(0.10, 2.72, 0.11),
                 tint=color.rgb(59, 65, 61))
            sphere(position=(x + side * 1.43, 2.02, 5.16), scale=(0.09, 0.08, 0.07),
                   tint=color.rgb(175, 133, 77))
        cube(position=(x, 3.58, 5.39), scale=(3.28, 0.10, 0.16),
             tint=color.rgb(91, 63, 43))

    def _build_painting(self):
        self.paint_root = Entity(parent=scene, position=(-2.30, 2.62, 5.79),
                                 name="framed painting")
        # Carved frame from four pieces.
        frame = color.rgb(91, 60, 38)
        for pos, size in (((0, 0.69, 0), (2.05, 0.15, 0.18)),
                          ((0, -0.69, 0), (2.05, 0.15, 0.18)),
                          ((-0.95, 0, 0), (0.15, 1.30, 0.18)),
                          ((0.95, 0, 0), (0.15, 1.30, 0.18))):
            cube(parent=self.paint_root, position=pos, scale=size, tint=frame)
        cube(parent=self.paint_root, position=(0, 0, -0.015), scale=(1.83, 1.18, 0.08),
             tint=color.rgb(33, 48, 52))
        # A distant mountain landscape built from calm horizontal layers.
        cube(parent=self.paint_root, position=(0, -0.36, -0.07), scale=(1.77, 0.40, 0.035),
             tint=color.rgb(54, 81, 75))
        cube(parent=self.paint_root, position=(-0.36, -0.07, -0.09), scale=(0.80, 0.50, 0.035),
             tint=color.rgb(84, 105, 98), rotation=(0, 0, -20))
        cube(parent=self.paint_root, position=(0.42, -0.05, -0.10), scale=(0.92, 0.58, 0.035),
             tint=color.rgb(102, 115, 101), rotation=(0, 0, 20))
        sphere(parent=self.paint_root, position=(0.55, 0.34, -0.11), scale=(0.25, 0.25, 0.035),
               tint=color.rgb(207, 169, 107))
        cube(parent=self.paint_root, position=(0, -0.50, -0.12), scale=(1.73, 0.12, 0.025),
             tint=color.rgb(126, 105, 75))
        self.painting_room_image = Entity(parent=self.paint_root, enabled=False,
                                          name="room in painting")
        cube(parent=self.painting_room_image, position=(0, 0, -0.15), scale=(1.78, 1.12, 0.04),
             tint=color.rgb(71, 64, 58))
        cube(parent=self.painting_room_image, position=(0, -0.34, -0.19), scale=(1.60, 0.35, 0.04),
             tint=color.rgb(99, 68, 51))
        cube(parent=self.painting_room_image, position=(-0.42, -0.08, -0.21), scale=(0.68, 0.30, 0.04),
             tint=color.rgb(171, 154, 126))
        cube(parent=self.painting_room_image, position=(-0.42, -0.20, -0.23), scale=(0.64, 0.11, 0.03),
             tint=color.rgb(93, 45, 49))
        cube(parent=self.painting_room_image, position=(-0.42, 0.21, -0.22), scale=(0.73, 0.15, 0.04),
             tint=color.rgb(80, 51, 37))
        cube(parent=self.painting_room_image, position=(0.52, 0.16, -0.21), scale=(0.43, 0.57, 0.04),
             tint=color.rgb(87, 62, 47))
        cube(parent=self.painting_room_image, position=(0.52, 0.16, -0.24), scale=(0.33, 0.46, 0.03),
             tint=color.rgb(142, 101, 61))
        self.painting_target = hitbox((-2.30, 2.62, 5.55), (2.30, 1.70, 0.38),
                                      "painting", "painting target")

    def _build_clock(self):
        self.clock_root = Entity(parent=scene, position=(0.0, 3.24, 5.77), name="wall clock")
        sphere(parent=self.clock_root, position=(0, 0, 0), scale=(0.96, 0.96, 0.18),
               tint=color.rgb(55, 38, 29))
        sphere(parent=self.clock_root, position=(0, 0, -0.08), scale=(0.82, 0.82, 0.12),
               tint=color.rgb(205, 190, 158))
        for index in range(12):
            angle = math.radians(index * 30)
            sphere(parent=self.clock_root,
                   position=(math.sin(angle) * 0.65, math.cos(angle) * 0.65, -0.16),
                   scale=(0.045, 0.045, 0.025), tint=color.rgb(68, 49, 34))
        self.minute_pivot = Entity(parent=self.clock_root, position=(0, 0, -0.20))
        cube(parent=self.minute_pivot, position=(0, 0.29, 0), scale=(0.045, 0.60, 0.025),
             tint=color.rgb(54, 44, 37))
        self.hour_pivot = Entity(parent=self.clock_root, position=(0, 0, -0.22))
        cube(parent=self.hour_pivot, position=(0, 0.20, 0), scale=(0.075, 0.42, 0.035),
             tint=color.rgb(54, 44, 37))
        sphere(parent=self.clock_root, position=(0, 0, -0.26), scale=(0.10, 0.10, 0.04),
               tint=color.rgb(161, 114, 59))
        self.clock_target = hitbox((0, 3.24, 5.53), (1.25, 1.25, 0.35), "clock", "clock target")

    def _build_door(self):
        # South entrance: closed and locked until the timeline is stabilized.
        self.door_hinge = Entity(parent=scene, position=(2.80, 0.0, -5.90),
                                 rotation_y=0, name="door hinge")
        cube(parent=self.door_hinge, position=(0.90, 1.20, 0), scale=(1.80, 2.40, 0.13),
             tint=color.rgb(67, 42, 30), name="door")
        cube(parent=self.door_hinge, position=(0.90, 1.20, -0.08), scale=(1.48, 2.05, 0.04),
             tint=color.rgb(85, 54, 37))
        for y in (0.56, 1.88):
            cube(parent=self.door_hinge, position=(0.90, y, -0.11), scale=(1.35, 0.035, 0.025),
                 tint=color.rgb(130, 90, 57))
        sphere(parent=self.door_hinge, position=(1.54, 1.13, -0.14),
               scale=(0.11, 0.11, 0.07), tint=color.rgb(190, 146, 76))
        for x in (2.80, 4.64):
            cube(position=(x, 1.22, -5.87), scale=(0.13, 2.58, 0.30),
                 tint=color.rgb(89, 58, 39))
        cube(position=(3.72, 2.55, -5.87), scale=(2.0, 0.16, 0.30),
             tint=color.rgb(89, 58, 39))
        self.door_hitbox = hitbox((3.72, 1.18, -5.88), (1.82, 2.38, 0.22),
                                  "door", "door collision")
        self.door_open_amount = 0.0

    def _build_time_machine(self):
        self.machine_root = Entity(parent=scene, position=(2.68, 1.42, 4.06),
                                   enabled=False, name="time displacement unit")
        cube(parent=self.machine_root, position=(0, 0.08, 0), scale=(1.55, 0.18, 0.74),
             tint=color.rgb(63, 75, 77))
        cube(parent=self.machine_root, position=(0, 0.19, 0), scale=(1.23, 0.10, 0.60),
             tint=color.rgb(112, 126, 124))
        self.machine_core = sphere(parent=self.machine_root, position=(0, 0.80, 0),
                                   scale=(0.40, 0.40, 0.40), tint=color.rgb(64, 224, 214),
                                   name="chronal core")
        self.machine_ring_a = Entity(parent=self.machine_root, position=(0, 0.80, -0.03))
        self.machine_ring_b = Entity(parent=self.machine_root, position=(0, 0.80, 0.06))
        self.machine_segments = []
        silver = color.rgb(147, 157, 150)
        for ring, radius, count, zoff in ((self.machine_ring_a, 0.73, 14, -0.03),
                                          (self.machine_ring_b, 0.51, 12, 0.06)):
            for i in range(count):
                angle = 360 * i / count
                radians = math.radians(angle)
                segment = cube(parent=ring,
                               position=(math.cos(radians) * radius,
                                         math.sin(radians) * radius, zoff),
                               scale=(0.09, 0.34, 0.09), tint=silver,
                               rotation=(0, 0, angle))
                self.machine_segments.append(segment)
        for x in (-0.56, 0.56):
            cube(parent=self.machine_root, position=(x, 0.32, 0),
                 scale=(0.09, 0.36, 0.10), tint=color.rgb(77, 86, 85))
            sphere(parent=self.machine_root, position=(x, 0.54, 0),
                   scale=(0.11, 0.11, 0.11), tint=color.rgb(213, 118, 59))
        for z in (-0.27, 0.27):
            cube(parent=self.machine_root, position=(0, 0.29, z),
                 scale=(0.95, 0.07, 0.08), tint=color.rgb(71, 80, 79))
        self.machine_target = hitbox((2.68, 2.25, 3.89), (1.95, 1.95, 0.95),
                                      "machine", "machine target")
        self.machine_target.enabled = False
        self.machine_rotation = 0.0

    def _build_decor(self):
        # Ceiling pendant, trim, and a few small room details.
        cube(position=(0, 4.15, 0.25), scale=(0.10, 0.27, 0.10),
             tint=color.rgb(87, 68, 51))
        sphere(position=(0, 3.94, 0.25), scale=(0.35, 0.18, 0.35),
               tint=color.rgb(199, 176, 137))
        for x in (-5.91, 5.91):
            cube(position=(x, 3.91, 0), scale=(0.14, 0.13, 11.8),
                 tint=color.rgb(67, 49, 38))
        # A small framed room number plaque near the door.
        cube(position=(1.62, 2.32, -5.90), scale=(0.58, 0.44, 0.10),
             tint=color.rgb(69, 46, 34))
        cube(position=(1.62, 2.32, -5.83), scale=(0.48, 0.34, 0.035),
             tint=color.rgb(194, 173, 137))
        self.room_number = Text(parent=scene, text="12", position=(1.55, 2.24, -5.79),
                                rotation=(0, 180, 0), scale=5, color=color.rgb(54, 44, 35))

    def reset_to_normal(self):
        self.chair_root.rotation_y = 180
        self.chair_target_yaw = 180.0
        self.chair_fixing = False
        for root in self.book_roots:
            root.enabled = True
            root.scale = (1, 1, 1)
        self.painting_room_image.enabled = False
        self.window_impossible.enabled = False
        self.machine_root.enabled = False
        self.machine_target.enabled = False
        self.minute_pivot.rotation_z = 0
        self.hour_pivot.rotation_z = 0
        self.door_hinge.rotation_y = 0
        self.door_hitbox.enabled = True
        self.door_open_amount = 0.0

    def apply_anomaly(self, loop_number):
        self.reset_to_normal()
        if loop_number == 1:
            # This is activated after the calm first half-minute of loop one.
            return
        if loop_number == 2:
            self.book_roots[2].enabled = False
        elif loop_number == 3:
            self.painting_room_image.enabled = True
        elif loop_number == 4:
            self.window_impossible.enabled = True
        elif loop_number == 5:
            self.machine_root.enabled = True
            self.machine_target.enabled = True

    def activate_first_anomaly(self):
        # Turn the seat toward the bed on the room's west side.
        self.chair_target_yaw = 90.0
        self.chair_fixing = True

    def fix_anomaly(self, loop_number):
        if loop_number == 1:
            self.chair_target_yaw = 0.0
            self.chair_fixing = True
        elif loop_number == 2:
            root = self.book_roots[2]
            root.enabled = True
            root.scale = (0.03, 0.03, 0.03)
            root.scale_target = 1.0
            root.reveal_started = True
        elif loop_number == 3:
            self.painting_room_image.enabled = False
        elif loop_number == 4:
            self.window_impossible.enabled = False
        elif loop_number == 5:
            self.machine_root.enabled = True
            self.machine_target.enabled = True
            self.machine_stabilizing = True
            self.machine_stabilize_time = 0.0

    def open_door(self, amount):
        self.door_open_amount = max(0.0, min(1.0, amount))
        self.door_hinge.rotation_y = -96.0 * self.door_open_amount
        if self.door_open_amount > 0.93:
            self.door_hitbox.enabled = False

    def update(self, dt, paradox, elapsed, state, success_elapsed=0.0,
               collapse_elapsed=0.0):
        self.fx_time += dt
        # Chair repair and book restoration have short, visible settling motions.
        if self.chair_fixing:
            delta = (self.chair_target_yaw - self.chair_root.rotation_y + 180) % 360 - 180
            step = max(-210 * dt, min(210 * dt, delta))
            self.chair_root.rotation_y += step
            if abs(delta) < 1.2:
                self.chair_root.rotation_y = self.chair_target_yaw
                self.chair_fixing = False
        for root in self.book_roots:
            if getattr(root, "reveal_started", False):
                root.scale += Vec3(1, 1, 1) * dt * 2.9
                if root.scale_x >= 1.0:
                    root.scale = (1, 1, 1)
                    root.reveal_started = False

        if self.machine_root.enabled:
            if getattr(self, "machine_stabilizing", False):
                self.machine_stabilize_time += dt
                ratio = max(0.0, 1.0 - self.machine_stabilize_time / 1.4)
                self.machine_ring_a.rotation_z += dt * 200 * ratio
                self.machine_ring_b.rotation_z -= dt * 290 * ratio
                if self.machine_stabilize_time > 1.4:
                    self.machine_ring_a.rotation_z = 0
                    self.machine_ring_b.rotation_z = 0
            elif state == "playing":
                speed = 26 + paradox * 25
                self.machine_ring_a.rotation_z += dt * speed
                self.machine_ring_b.rotation_z -= dt * speed * 1.42
                pulse = 0.92 + 0.10 * math.sin(self.fx_time * 4.0)
                if paradox >= 2:
                    pulse += 0.10 * math.sin(self.fx_time * 12.0)
                self.machine_core.scale = (0.40 * pulse, 0.40 * pulse, 0.40 * pulse)

        if state == "success_transition":
            self.open_door(min(1.0, success_elapsed / 2.8))

        if state == "playing":
            clock_minutes = min(2.0, elapsed / 60.0)
            self.minute_pivot.rotation_z = -clock_minutes * 6.0
            self.hour_pivot.rotation_z = -clock_minutes * 0.5
        elif state in ("success_transition", "success"):
            start = getattr(self, "success_clock_start_minute", 0.0)
            progress = 1.0 if state == "success" else min(1.0, success_elapsed / 7.0)
            clock_minutes = start + progress * (3.0 - start)
            self.minute_pivot.rotation_z = -clock_minutes * 6.0
            self.hour_pivot.rotation_z = -clock_minutes * 0.5

        if state == "collapsing":
            shake = min(1.0, collapse_elapsed / 3.2)
            for entity in (self.chair_root, self.machine_root, self.door_hinge):
                if entity.enabled:
                    entity.x += random.uniform(-0.028, 0.028) * shake
                    entity.z += random.uniform(-0.025, 0.025) * shake
            spin = collapse_elapsed * (330 + 140 * shake)
            self.minute_pivot.rotation_z = spin
            self.hour_pivot.rotation_z = -spin * 1.4

        # Flicker is occasional early, then increasingly hard to ignore.
        if state == "playing" and paradox > 0 and random.random() < dt * (0.34 + paradox * 0.75):
            self.flicker_remaining = random.uniform(0.045, 0.14 + paradox * 0.06)
        if self.flicker_remaining > 0:
            self.flicker_remaining = max(0, self.flicker_remaining - dt)
        if self.room_light:
            if state in ("success_transition", "success"):
                self.room_light.color = self.base_light_color
            elif state == "collapsing":
                phase = math.sin(collapse_elapsed * 25.0)
                strength = 0.35 if phase > -0.15 else 0.08
                self.room_light.color = color.rgba(int(255 * strength), int(224 * strength),
                                                   int(176 * strength), 255)
            elif self.flicker_remaining > 0:
                strength = random.uniform(0.12, 0.48) if paradox >= 2 else 0.60
                self.room_light.color = color.rgba(int(255 * strength), int(224 * strength),
                                                   int(176 * strength), 255)
            else:
                self.room_light.color = self.base_light_color

        if paradox >= 2 and state == "playing":
            # Small clock tremors are visible but never obscure the real reading.
            jitter = math.sin(self.fx_time * 15) * 1.3
            self.hour_pivot.rotation_z += jitter * dt


class AnomalyManager:
    """Owns the one active anomaly and its one-key repair action."""

    TAGS = {1: "chair", 2: "books", 3: "painting", 4: "window", 5: "machine"}

    def __init__(self, room):
        self.room = room
        self.loop_number = 1
        self.active = False
        self.fixed = False
        self.delay = 35.0

    @property
    def tag(self):
        return self.TAGS.get(self.loop_number)

    def start_loop(self, loop_number):
        self.loop_number = loop_number
        self.fixed = False
        self.active = loop_number != 1
        self.room.apply_anomaly(loop_number)

    def update(self, elapsed):
        if self.loop_number == 1 and not self.active and elapsed >= self.delay:
            self.active = True
            self.room.activate_first_anomaly()

    def fix(self, target_tag):
        if self.fixed or not self.active or target_tag != self.tag:
            return False
        self.fixed = True
        self.room.fix_anomaly(self.loop_number)
        return True


class LoopManager:
    """Countdown, loop transitions, and the persistent paradox counter."""

    def __init__(self, room, anomaly_manager, on_failure, on_success):
        self.room = room
        self.anomalies = anomaly_manager
        self.on_failure = on_failure
        self.on_success = on_success
        self.loop_number = 1
        self.elapsed = 0.0
        self.time_remaining = LOOP_SECONDS
        self.paradox = 0
        self.started = False

    def start(self):
        self.started = True
        self._start_loop(1)

    def _start_loop(self, loop_number):
        self.loop_number = loop_number
        self.elapsed = 0.0
        self.time_remaining = LOOP_SECONDS
        self.anomalies.start_loop(loop_number)

    def interact(self, tag):
        if not self.anomalies.fix(tag):
            return False
        if self.loop_number == 5:
            self.on_success()
        return True

    def update(self, dt):
        if not self.started:
            return
        self.elapsed += dt
        self.time_remaining = max(0.0, LOOP_SECONDS - self.elapsed)
        self.anomalies.update(self.elapsed)
        if self.time_remaining <= 0:
            self._expire_loop()

    def _expire_loop(self):
        if not self.anomalies.fixed:
            self.paradox += 1
        if self.loop_number == 5:
            # The machine is the final chance: missing it always collapses the loop.
            self.on_failure()
            return
        if self.paradox >= MAX_PARADOX:
            self.on_failure()
            return
        self._start_loop(self.loop_number + 1)


class Interface:
    """Minimal cream-and-black HUD, plus the one-time thought and end cards."""

    def __init__(self):
        # Keep panels behind their labels in camera space; equal-depth UI quads
        # can cover the text on some graphics drivers.
        panel = color.rgba(211, 195, 164, 245)
        text_ink = color.black
        self.loop_panel = Entity(parent=camera.ui, model="quad", position=(-0.75, 0.445),
                                 scale=(0.28, 0.075), color=panel, z=0.5)
        self.loop_text = Text(parent=camera.ui, text="LOOP 1", position=(-0.87, 0.425),
                              origin=(-0.5, 0), scale=1.0, color=text_ink, z=-0.1)
        self.stats_panel = Entity(parent=camera.ui, model="quad", position=(0.70, 0.415),
                                  scale=(0.38, 0.14), color=panel, z=0.5)
        self.stats_text = Text(parent=camera.ui, text="TIME 02:00\nCLOCK 12:00\nPARADOX 0 / 3",
                               position=(0.86, 0.46), origin=(0.5, 0.5), scale=0.77,
                               color=text_ink, z=-0.1)
        self.crosshair = Entity(parent=camera.ui, model="quad", position=(0, 0),
                                scale=(0.008, 0.008), color=color.rgba(246, 241, 226, 225))

        self.prompt_panel = Entity(parent=camera.ui, model="quad", position=(0, -0.385),
                                   scale=(0.28, 0.065), color=panel, z=0.5, enabled=False)
        self.prompt_text = Text(parent=camera.ui, text="", position=(0, -0.401),
                                origin=(0, 0), scale=0.95, color=text_ink,
                                z=-0.1, enabled=False)
        self.message_panel = Entity(parent=camera.ui, model="quad", position=(0, -0.385),
                                    scale=(0.54, 0.085), color=panel, z=0.5, enabled=False)
        self.message_text = Text(parent=camera.ui, text="", position=(0, -0.401),
                                 origin=(0, 0), scale=0.82, color=text_ink,
                                 z=-0.1, enabled=False)

        self.thought_panel = Entity(parent=camera.ui, model="quad", position=(0, -0.405),
                                    scale=(0.77, 0.085), color=panel, z=0.5)
        self.thought = Text(parent=camera.ui,
                            text="I need to fix whatever changed before the clock resets... or it'll create a paradox.",
                            position=(0, -0.419), origin=(0, 0), scale=0.70,
                            color=text_ink, z=-0.1)
        self.thought_elapsed = 0.0
        self.thought_duration = 4.7
        self.thought_done = False
        self.failure_overlay = Entity(parent=camera.ui, model="quad", z=0.8,
                                      scale=(2, 2), color=color.rgba(0, 0, 0, 0),
                                      enabled=False)
        self.end_title = Text(parent=camera.ui, text="", position=(0, 0.08), origin=(0, 0),
                              scale=1.75, color=color.rgb(226, 214, 190), enabled=False)
        self.end_subtitle = Text(parent=camera.ui, text="", position=(0, -0.02), origin=(0, 0),
                                 scale=0.86, color=color.rgb(226, 214, 190), enabled=False)
        self.restart_text = Text(parent=camera.ui, text="R  RESTART", position=(0, -0.16),
                                 origin=(0, 0), scale=0.68, color=color.rgb(226, 214, 190),
                                 enabled=False)
        self.current_message = ""
        self.message_time = 0.0
        self.last_loop_number = 1
        self.reset_flash_remaining = 0.0

    def show_message(self, message, duration=2.2):
        self.current_message = message
        self.message_time = duration
        self.message_text.text = message
        self.message_panel.enabled = True
        self.message_text.enabled = True

    def set_prompt(self, text):
        visible = bool(text)
        self.prompt_panel.enabled = visible
        self.prompt_text.enabled = visible
        if visible:
            self.prompt_text.text = text

    def update(self, dt, loop_number, remaining, paradox, elapsed, state,
               success_elapsed=0.0):
        if loop_number != self.last_loop_number:
            self.reset_flash_remaining = 0.8
            self.last_loop_number = loop_number
        else:
            self.reset_flash_remaining = max(0.0, self.reset_flash_remaining - dt)
        if not self.thought_done:
            self.thought_elapsed += dt
            if self.thought_elapsed >= self.thought_duration:
                self.thought_done = True
                self.thought_panel.enabled = False
                self.thought.enabled = False
            else:
                fade_start = 3.1
                alpha = 1.0 if self.thought_elapsed <= fade_start else max(
                    0.0, 1.0 - (self.thought_elapsed - fade_start) / (self.thought_duration - fade_start))
                self.thought_panel.color = color.rgba(226, 214, 190, int(225 * alpha))
                self.thought.color = color.rgba(24, 21, 18, int(255 * alpha))
        if self.message_time > 0:
            self.message_time = max(0.0, self.message_time - dt)
            if self.message_time == 0:
                self.message_panel.enabled = False
                self.message_text.enabled = False

        mins, secs = divmod(max(0, int(math.ceil(remaining))), 60)
        clock_minute = min(2, int(elapsed // 60))
        clock_text = f"12:{clock_minute:02d}"
        if self.reset_flash_remaining > 0 and state == "playing":
            clock_text = "12:02"
        if state in ("success_transition", "success"):
            start_minute = getattr(self, "success_clock_start", 0.0)
            progress = 1.0 if state == "success" else min(1.0, success_elapsed / 7.0)
            progressed = start_minute + (progress * (3.0 - start_minute))
            clock_text = f"12:{min(3, int(progressed)):02d}"
            mins, secs = 0, 0
        self.loop_text.text = "LOOP 5  /  FINAL" if loop_number == 5 else f"LOOP {loop_number}"
        self.stats_text.text = (f"TIME {mins:02d}:{secs:02d}\n"
                                f"CLOCK {clock_text}\n"
                                f"PARADOX {paradox} / {MAX_PARADOX}")

    def begin_failure(self):
        self.failure_overlay.enabled = True
        self.end_title.enabled = False
        self.end_subtitle.enabled = False
        self.restart_text.enabled = False

    def finish_failure(self):
        self.end_title.text = "TEMPORAL COLLAPSE"
        self.end_subtitle.text = "THE TIMELINE COULD NOT BE STABILIZED."
        self.end_title.enabled = True
        self.end_subtitle.enabled = True
        self.restart_text.enabled = True

    def begin_success(self, clock_minutes):
        self.success_clock_start = clock_minutes

    def finish_success(self):
        self.end_title.text = "TIMELINE STABILIZED"
        self.end_subtitle.text = "EXPERIMENT TERMINATED"
        self.end_title.enabled = True
        self.end_subtitle.enabled = True
        self.restart_text.enabled = True


class ParadoxGame(Entity):
    def __init__(self):
        super().__init__(name="paradox game controller")
        window.title = "PARADOX"
        window.color = color.rgb(11, 11, 15)
        window.vsync = True
        camera.fov = 88
        camera.clip_plane_far = 60
        camera.clip_plane_near = 0.05
        mouse.locked = True
        mouse.visible = False

        self.room = Room()
        self.player = FirstPersonController(position=(0, 0, -3.75), speed=3.65,
                                            gravity=1.0, jump_height=0.85)
        # The prefab stops on feet/head ray hits. Explicitly include the room's
        # wall, floor, and furniture colliders in those rays.
        self.player.traverse_target = scene
        self.player.ignore_list = [self.player]
        self.player.mouse_sensitivity = Vec2(38, 38)
        self.player.cursor.visible = False
        self.player.camera_pivot.y = 1.62
        self.player.position = (0, 0, -3.75)

        self.ui = Interface()
        self.audio = AudioManager()
        self.anomalies = AnomalyManager(self.room)
        self.state = "playing"
        self.collapse_elapsed = 0.0
        self.success_elapsed = 0.0
        self.camera_shake = 0.0
        self.tick_timer = 0.0
        self.footstep_timer = 0.0
        self.look_target_tag = None
        self.look_target_entity = None
        self.suppressed_prompt_tag = None
        self.loop_manager = LoopManager(self.room, self.anomalies,
                                        self.begin_failure, self.begin_success)
        self.loop_manager.start()
        self.update_interaction()

    def begin_failure(self):
        if self.state in ("collapsing", "failed", "success", "success_transition"):
            return
        self.state = "collapsing"
        self.collapse_elapsed = 0.0
        self.player.speed = 0
        self.player.gravity = 0
        self.ui.set_prompt("")
        self.ui.begin_failure()
        self.audio.stop()
        self.audio.play_effect("collapse")

    def begin_success(self):
        if self.state != "playing":
            return
        self.state = "success_transition"
        self.success_elapsed = 0.0
        start_minute = self.loop_manager.elapsed / 60.0
        self.room.success_clock_start_minute = start_minute
        self.ui.begin_success(start_minute)
        self.ui.set_prompt("")
        self.audio.stop()
        self.audio.play_effect("success")
        self.player.speed = 2.2

    def _get_interactable(self):
        origin = camera.world_position
        result = raycast(origin, camera.forward, distance=3.65,
                         ignore=[self.player])
        if not result.hit or result.entity is None:
            return None, None
        target = result.entity
        tag = getattr(target, "interact_tag", None)
        if not tag:
            return None, None
        return target, tag

    def update_interaction(self):
        if self.state != "playing":
            self.look_target_tag = None
            self.look_target_entity = None
            self.ui.set_prompt("")
            return
        target, tag = self._get_interactable()
        self.look_target_entity = target
        self.look_target_tag = tag
        if tag is None:
            self.suppressed_prompt_tag = None
            self.ui.set_prompt("")
            return
        if self.suppressed_prompt_tag is not None and tag != self.suppressed_prompt_tag:
            self.suppressed_prompt_tag = None
        if tag == self.suppressed_prompt_tag:
            self.ui.set_prompt("")
            return
        current = self.anomalies.tag == tag and self.anomalies.active and not self.anomalies.fixed
        if current:
            self.ui.set_prompt("[E] Stabilize" if tag == "machine" else "[E] Fix")
        else:
            self.ui.set_prompt("[E] Inspect")

    def _inspect(self, tag):
        notes = {
            "chair": "A cheap chair, angled toward the desk.",
            "books": "Three field books. The handwriting stops mid-sentence.",
            "painting": "A faded landscape. The horizon feels oddly familiar.",
            "window": "A dark night presses against the glass.",
            "clock": "The clock is stuck just before twelve.",
            "telephone": "The line is silent.",
            "note": "TRIAL 04 — controlled temporal displacement. Subject: you.",
            "bed": "The sheets smell of dust and old smoke.",
            "wardrobe": "The wardrobe door is swollen shut.",
            "door": "The handle will not turn. The lock is cold.",
            "machine": "Cold metal sits where there was only empty desk.",
        }
        self.ui.show_message(notes.get(tag, "Nothing unusual."), 2.3)

    def input(self, key):
        if key == "r" and self.state in ("failed", "success"):
            self.audio.cleanup()
            os.execv(sys.executable, [sys.executable, *sys.argv])
            return
        if key.lower() == "e" and self.state == "playing" and self.look_target_tag:
            tag = self.look_target_tag
            self.suppressed_prompt_tag = tag
            self.ui.set_prompt("")
            if self.loop_manager.interact(tag):
                if self.loop_manager.loop_number == 5:
                    self.ui.show_message("The core steadies. The ticking stops.", 3.2)
                else:
                    self.ui.show_message("Fixed.", 2.0)
                    self.audio.play_effect("fix")
            else:
                self._inspect(tag)

    def update(self):
        dt = time.dt
        if self.state == "playing":
            previous_loop = self.loop_manager.loop_number
            self.loop_manager.update(dt)
            if self.loop_manager.loop_number != previous_loop:
                self.audio.play_effect("reset")
            self.tick_timer += dt
            if self.tick_timer >= 1.0:
                self.tick_timer -= 1.0
                self.audio.play_tick(self.loop_manager.paradox)
            moving = any(held_keys[key] for key in ("w", "a", "s", "d"))
            if moving:
                self.footstep_timer += dt
                if self.footstep_timer >= 0.46:
                    self.footstep_timer = 0.0
                    self.audio.play_effect("footstep")
            else:
                self.footstep_timer = 0.0
            self.room.update(dt, self.loop_manager.paradox, self.loop_manager.elapsed,
                            self.state)
            self.update_interaction()
        elif self.state == "collapsing":
            self.collapse_elapsed += dt
            self.room.update(dt, MAX_PARADOX, self.loop_manager.elapsed, self.state,
                            collapse_elapsed=self.collapse_elapsed)
            self.camera_shake = min(0.055, self.collapse_elapsed * 0.009)
            self.player.camera_pivot.position = (
                random.uniform(-self.camera_shake, self.camera_shake),
                1.62 + random.uniform(-self.camera_shake, self.camera_shake), 0)
            alpha = int(235 * max(0.0, min(1.0, (self.collapse_elapsed - 1.1) / 5.2)))
            self.ui.failure_overlay.color = color.rgba(0, 0, 0, alpha)
            if self.collapse_elapsed >= 7.0:
                self.state = "failed"
                self.ui.finish_failure()
                self.player.camera_pivot.position = (0, 1.62, 0)
        elif self.state == "success_transition":
            self.success_elapsed += dt
            self.room.update(dt, self.loop_manager.paradox, self.loop_manager.elapsed,
                            self.state, success_elapsed=self.success_elapsed)
            if self.success_elapsed >= 7.2 and not self.ui.end_title.enabled:
                self.state = "success"
                self.ui.finish_success()
            self.update_interaction()

        self.ui.update(dt, self.loop_manager.loop_number, self.loop_manager.time_remaining,
                       self.loop_manager.paradox, self.loop_manager.elapsed, self.state,
                       self.success_elapsed)


def main():
    # Development mode adds Ursina's editor, counters, and render-mode toggles;
    # those overlays obscure the playable scene and can leave colliders visible.
    app = Ursina(title="PARADOX", development_mode=False,
                 editor_ui_enabled=False, fullscreen=False,
                 render_mode="default", show_ursina_splash=False)
    game = ParadoxGame()
    try:
        app.run()
    finally:
        game.audio.cleanup()


if __name__ == "__main__":
    main()
