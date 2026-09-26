"""PARADOX — a small first-person observation horror game made with Ursina.

Everything visible is assembled from Ursina primitives. Optional sounds are
short WAV files synthesized with Python's standard library when the game runs.
Install Ursina, then launch with: python main.py
"""

from __future__ import annotations

import math
import os
import random
import sys
import struct
import wave
from pathlib import Path

from ursina import *
from ursina.shaders.unlit_shader import unlit_shader
from ursina.prefabs.first_person_controller import FirstPersonController


ROOM_HALF = 6.0
LOOP_SECONDS = 120.0
MAX_PARADOX = 3
FINAL_LOOP = 5
NON_SOLID_HITBOXES = []
SOLID_COLLISION_BOXES = []
CLOCK_LABELS = ("12:00", "12:01", "12:02")


def cube(parent=scene, position=(0, 0, 0), scale=(1, 1, 1), tint=None,
         rotation=(0, 0, 0), collider=None, name=None):
    """Create a dependable colored primitive using Ursina's built-in texture."""
    tint = readable_tint(tint)
    kwargs = dict(
        parent=parent,
        model="cube",
        texture="white_cube",
        shader=unlit_shader,
        color=tint or color.white,
        position=position,
        scale=scale,
        rotation=rotation,
        collider=collider,
    )
    # Ursina forwards keyword values through setattr; its node-name setter rejects None.
    if name is not None:
        kwargs["name"] = name
    entity = Entity(**kwargs)
    if collider == "box":
        SOLID_COLLISION_BOXES.append(entity)
    return entity


def sphere(parent=scene, position=(0, 0, 0), scale=(1, 1, 1), tint=None,
           name=None):
    tint = readable_tint(tint)
    kwargs = dict(parent=parent, model="sphere", texture="white_cube",
                  shader=unlit_shader, color=tint or color.white,
                  position=position, scale=scale)
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
        return tint.tint(0.12)
    except Exception:
        return tint


def hitbox(position, scale, tag, name, solid=True, parent=scene):
    """Create an invisible aim target, optionally solid to the player."""
    entity = cube(parent=parent, position=position, scale=scale, tint=color.rgba(0, 0, 0, 0),
                  collider="box", name=name)
    entity.visible = False
    entity.interact_tag = tag
    entity.solid_for_player = solid
    if not solid:
        NON_SOLID_HITBOXES.append(entity)
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
        self.glitch_hum = None
        self.glitch_active = False
        self.effects = {}
        self.enabled = False
        try:
            # Ursina resolves sounds relative to its asset folder. Keeping the
            # generated WAVs in ./audio avoids its loader rejecting temp paths.
            audio_dir = Path(__file__).resolve().parent / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            audio_paths = {
                name: audio_dir / f"{name}.wav"
                for name in ("tick", "room_tone", "footstep", "fixed",
                             "loop_reset", "collapse", "crack", "fall", "stabilized",
                             "glitch")
            }

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
            collapse_noise = [noise.uniform(-1, 1) for _ in range(int(9.0 * 22050))]
            crack_noise = [noise.uniform(-1, 1) for _ in range(int(0.62 * 22050))]
            fall_noise = [noise.uniform(-1, 1) for _ in range(int(4.2 * 22050))]
            glitch_raw = [noise.uniform(-1, 1) for _ in range(int(2.4 * 22050))]
            glitch_noise = []
            filtered_glitch = 0.0
            for sample in glitch_raw:
                filtered_glitch = filtered_glitch * 0.92 + sample * 0.08
                glitch_noise.append(filtered_glitch)

            def collapse_wave(t, duration):
                index = min(len(collapse_noise) - 1, int(t * 22050))
                swell = 0.58 + 0.34 * math.sin(2 * math.pi * 0.55 * t)
                bass = 0.82 * math.sin(2 * math.pi * (39 + 9 * t) * t)
                return swell * (bass + 0.34 * collapse_noise[index])

            def crack_wave(t, duration):
                index = min(len(crack_noise) - 1, int(t * 22050))
                decay = math.exp(-t * 8.5)
                snap = 0.55 * crack_noise[index] + 0.34 * math.sin(2 * math.pi * 92 * t)
                return decay * snap

            def fall_wave(t, duration):
                index = min(len(fall_noise) - 1, int(t * 22050))
                rise = min(1.0, t * 1.8)
                fade = math.exp(-max(0.0, t - 2.4) * 0.6)
                rumble = 0.68 * math.sin(2 * math.pi * (54 - 7 * t) * t)
                return rise * fade * (rumble + 0.27 * fall_noise[index])

            def glitch_wave(t, duration):
                index = min(len(glitch_noise) - 1, int(t * 22050))
                edge_fade = min(1.0, t * 8.0, (duration - t) * 8.0)
                slow_phase = 2 * math.pi * 0.23 * t
                bass_phase = 2 * math.pi * 49 * t + 0.42 * math.sin(slow_phase)
                mid_phase = 2 * math.pi * 73 * t + 0.26 * math.sin(slow_phase * 0.61 + 1.1)
                low_warp = 0.74 * math.sin(bass_phase) + 0.26 * math.sin(mid_phase)
                soft_static = glitch_noise[index]
                digital_stutter = 0.0
                # Short, stepped signal drops give it a deliberate digital
                # stutter, without the harsh hiss and random crackle of the old loop.
                for pulse_at in (0.29, 0.91, 1.48, 2.06):
                    age = t - pulse_at
                    if 0.0 <= age < 0.10:
                        held_time = math.floor(age * 115.0) / 115.0
                        pitch = 260.0 + 360.0 * age / 0.10
                        digital_stutter += (
                            0.20 * math.exp(-age * 18.0)
                            * math.sin(2 * math.pi * pitch * held_time))
                return edge_fade * (0.34 * low_warp + 0.035 * soft_static
                                    + digital_stutter)

            def success_wave(t, duration):
                envelope = math.exp(-t * 1.35)
                return envelope * (0.26 * math.sin(2 * math.pi * 523.25 * t)
                                   + 0.22 * math.sin(2 * math.pi * 659.25 * t)
                                   + 0.18 * math.sin(2 * math.pi * 783.99 * t)
                                   + 0.12 * math.sin(2 * math.pi * 1046.5 * t))

            _write_tone(audio_paths["tick"], 0.11, tick_wave)
            _write_tone(audio_paths["room_tone"], 2.4, room_tone)
            _write_tone(audio_paths["footstep"], 0.16, footstep_wave)
            _write_tone(audio_paths["fixed"], 0.85, fix_wave)
            _write_tone(audio_paths["loop_reset"], 0.90, reset_wave)
            _write_tone(audio_paths["collapse"], 9.0, collapse_wave)
            _write_tone(audio_paths["crack"], 0.62, crack_wave)
            _write_tone(audio_paths["fall"], 4.2, fall_wave)
            _write_tone(audio_paths["stabilized"], 2.1, success_wave)
            _write_tone(audio_paths["glitch"], 2.4, glitch_wave)
            self.tick = Audio("audio/tick.wav", volume=0.46, autoplay=False,
                              loop=False, auto_destroy=False)
            self.hum = Audio("audio/room_tone.wav", volume=0.22, autoplay=True,
                             loop=True, auto_destroy=False)
            self.glitch_hum = Audio("audio/glitch.wav", volume=0.22, autoplay=False,
                                    loop=True, auto_destroy=False)
            for name, filename, volume in (
                ("footstep", "footstep", 0.50),
                ("fix", "fixed", 0.40),
                ("reset", "loop_reset", 0.52),
                ("collapse", "collapse", 1.45),
                ("crack", "crack", 1.55),
                ("fall", "fall", 1.40),
                ("success", "stabilized", 0.48),
            ):
                self.effects[name] = Audio(f"audio/{filename}.wav", volume=volume,
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

    def set_glitch(self, active):
        if not self.enabled or not self.glitch_hum or active == self.glitch_active:
            return
        try:
            if active:
                self.glitch_hum.play()
            else:
                self.glitch_hum.stop(destroy=False)
            self.glitch_active = active
        except Exception:
            pass

    def stop(self):
        if self.enabled and self.hum:
            try:
                self.hum.stop(destroy=False)
            except Exception:
                pass
        if self.enabled and self.glitch_hum:
            try:
                self.glitch_hum.stop(destroy=False)
            except Exception:
                pass
        self.glitch_active = False

    def cleanup(self):
        # Keep generated clips beside the game: Ursina's loader may still hold
        # them until shutdown, and the next launch can safely overwrite them.
        pass


class Room:
    """The single hotel room and its resettable timeline fragments."""

    def __init__(self):
        self.dynamic_entities = []
        self.base_wall_color = color.rgb(148, 130, 108)
        self.base_light_color = color.rgb(118, 92, 66)
        self.fx_time = 0.0
        self.flicker_remaining = 0.0
        self.random_motion = 0.0
        self.last_paradox = 0
        self.distortion_pulse = 0.0
        self.clock_reset_flash_remaining = 0.0
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
        self._build_collapse_fx()
        self.reset_to_normal()

    def _build_shell(self):
        # Thick box colliders make the room reliably solid for the FPS controller.
        self.floor = cube(position=(0, -0.14, 0), scale=(12.2, 0.28, 12.2),
                          tint=color.rgb(88, 65, 48), collider="box", name="wood floor")
        self.ceiling = cube(position=(0, 4.32, 0), scale=(12.2, 0.22, 12.2),
                            tint=color.rgb(118, 103, 83), collider="box", name="ceiling")
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
        self.floor_fragments = []
        for row in range(12):
            z = -5.45 + row * 0.98
            offset = 0.45 if row % 2 else 0.0
            for col in range(6):
                x = -5.1 + col * 2.0 + offset
                if x > 5.25:
                    continue
                tint = color.rgb(94 + (row % 3) * 5, 67 + (col % 2) * 5, 48)
                plank = cube(position=(x, 0.008, z), scale=(1.91, 0.025, 0.91), tint=tint)
                self.floor_fragments.append({
                    "entity": plank,
                    "origin": Vec3(x, 0.008, z),
                    "delay": random.uniform(0.0, 0.75),
                    "drift": Vec3(random.uniform(-0.35, 0.35), 0,
                                  random.uniform(-0.35, 0.35)),
                    "spin": random.uniform(-1, 1),
                })

        # Aged lower wall trim gives the room a finished edge.
        for x in (-5.91, 5.91):
            cube(position=(x, 0.18, 0), scale=(0.13, 0.32, 11.9),
                 tint=color.rgb(65, 43, 31))
        cube(position=(0, 0.18, 5.91), scale=(11.9, 0.32, 0.13),
             tint=color.rgb(65, 43, 31))
        for x, width in ((-1.60, 8.8), (5.40, 1.2)):
            cube(position=(x, 0.18, -5.91), scale=(width, 0.32, 0.13),
                 tint=color.rgb(65, 43, 31))

        rug = cube(position=(0, 0.045, 0.15), scale=(4.4, 0.05, 3.4),
                   tint=color.rgb(112, 28, 53))
        self.floor_fragments.append({"entity": rug, "origin": Vec3(0, 0.045, 0.15),
                                     "delay": 0.25, "drift": Vec3(0.12, 0, -0.16),
                                     "spin": 0.35})
        # Fine rug border, a restrained detail visible in the warm light.
        for z in (-1.47, 1.77):
            border = cube(position=(0, 0.078, z), scale=(4.06, 0.018, 0.035),
                          tint=color.rgb(146, 103, 66))
            self.floor_fragments.append({"entity": border, "origin": Vec3(0, 0.078, z),
                                         "delay": 0.18, "drift": Vec3(0.08, 0, 0),
                                         "spin": -0.4})
        for x in (-2.03, 2.03):
            border = cube(position=(x, 0.078, 0.15), scale=(0.035, 0.018, 3.24),
                          tint=color.rgb(146, 103, 66))
            self.floor_fragments.append({"entity": border, "origin": Vec3(x, 0.078, 0.15),
                                         "delay": 0.35, "drift": Vec3(0, 0, 0.1),
                                         "spin": 0.5})

        # Built-in lights only; shadows stay disabled for broad hardware support.
        try:
            AmbientLight(color=color.rgba(90, 82, 72, 255))
            self.room_light = PointLight(parent=scene, position=(-1.25, 2.65, 3.75),
                                         color=self.base_light_color)
            self.cool_fill = PointLight(parent=scene, position=(4.1, 2.55, -2.8),
                                        color=color.rgb(32, 54, 91))
        except Exception:
            self.room_light = None
            self.cool_fill = None

    def _build_bed_and_bedside(self):
        self.bed_root = Entity(parent=scene, position=(0, 0, 0), name="bed group")
        bx, bz = -3.55, 1.30
        # Raised frame, mattress, blanket, two pillows and headboard.
        cube(parent=self.bed_root, position=(bx, 0.36, bz), scale=(2.68, 0.40, 4.10),
             tint=color.rgb(67, 42, 29), collider="box", name="bed frame")
        cube(parent=self.bed_root, position=(bx, 0.72, bz - 0.02), scale=(2.48, 0.36, 3.90),
             tint=color.rgb(188, 173, 146), collider="box", name="mattress")
        cube(parent=self.bed_root, position=(bx, 0.94, bz - 0.38), scale=(2.46, 0.18, 2.48),
             tint=color.rgb(132, 39, 58), collider="box", name="bed blanket")
        for dx in (-0.62, 0.62):
            cube(parent=self.bed_root, position=(bx + dx, 1.00, bz + 1.49), scale=(0.90, 0.22, 0.70),
                 tint=color.rgb(218, 207, 181), collider="box", name="pillow")
        cube(parent=self.bed_root, position=(bx, 1.24, bz + 2.08), scale=(2.78, 1.55, 0.22),
             tint=color.rgb(72, 46, 32), collider="box", name="headboard")
        # Brass nail heads on the headboard.
        for dx in (-1.12, 0, 1.12):
            sphere(parent=self.bed_root, position=(bx + dx, 1.78, bz + 1.94), scale=(0.055, 0.055, 0.035),
                   tint=color.rgb(178, 132, 72))
        hitbox((bx, 0.60, bz), (2.7, 1.15, 4.15), "bed", "bed collision",
               solid=False, parent=self.bed_root)

        # Bedside table, one drawer, small pull, and a warm table lamp.
        self.bedside_root = Entity(parent=scene, position=(0, 0, 0), name="bedside group")
        tx, tz = -1.40, 3.73
        cube(parent=self.bedside_root, position=(tx, 0.62, tz), scale=(1.18, 1.16, 0.92),
             tint=color.rgb(74, 47, 33), collider="box", name="bedside table")
        self.bedside_tabletop = cube(
            parent=self.bedside_root, position=(tx, 1.24, tz),
            scale=(1.30, 0.15, 1.04), tint=color.rgb(104, 67, 43),
            collider="box", name="bedside tabletop")
        cube(parent=self.bedside_root, position=(tx, 0.81, tz - 0.49), scale=(0.74, 0.34, 0.035),
             tint=color.rgb(91, 58, 39))
        sphere(parent=self.bedside_root, position=(tx, 0.82, tz - 0.535), scale=(0.09, 0.09, 0.05),
               tint=color.rgb(192, 145, 79))

    def _build_desk_and_books(self):
        self.desk_root = Entity(parent=scene, position=(0, 0, 0), name="desk group")
        dx, dz = 2.65, 4.40
        # The desk has a solid, waist-high body collider with a thick top.
        cube(parent=self.desk_root, position=(dx, 0.66, dz), scale=(3.25, 1.24, 1.28),
             tint=color.rgb(76, 48, 32), collider="box", name="desk body")
        self.desk_top = cube(parent=self.desk_root, position=(dx, 1.36, dz),
                             scale=(3.48, 0.16, 1.52),
                             tint=color.rgb(104, 68, 43), collider="box",
                             name="desk top")
        for side in (-1, 1):
            cube(parent=self.desk_root, position=(dx + side * 0.90, 0.86, dz - 0.67),
                 scale=(0.16, 0.16, 0.035), tint=color.rgb(50, 34, 26))
            cube(parent=self.desk_root, position=(dx + side * 0.90, 0.86, dz - 0.71),
                 scale=(0.07, 0.045, 0.045), tint=color.rgb(191, 147, 78))
        # Four short tapered-looking legs beneath the drawer body.
        for xoff in (-1.35, 1.35):
            for zoff in (-0.48, 0.48):
                cube(parent=self.desk_root, position=(dx + xoff, 0.23, dz + zoff),
                     scale=(0.18, 0.48, 0.18), tint=color.rgb(61, 39, 27))

        self.book_roots = []
        covers = [color.rgb(105, 49, 45), color.rgb(47, 68, 65), color.rgb(125, 93, 52)]
        for index, x in enumerate((1.95, 2.55, 3.15)):
            root = Entity(parent=self.desk_root, position=(x, 1.46, 4.26), name=f"field book {index + 1}")
            cube(parent=root, position=(0, 0.09, 0), scale=(0.48, 0.18, 0.36),
                 tint=color.rgb(206, 193, 162))
            cube(parent=root, position=(0, 0.19, -0.005), scale=(0.50, 0.07, 0.38),
                 tint=covers[index])
            cube(parent=root, position=(-0.21, 0.19, -0.005), scale=(0.045, 0.072, 0.39),
                 tint=color.rgb(199, 165, 102))
            self.book_roots.append(root)
        self.book_target = hitbox((2.56, 1.68, 4.24), (1.95, 0.60, 0.65),
                                  "books", "book area", solid=False, parent=self.desk_root)

        # Desk chair is one assembled object on a rotatable root.
        # Local chair back is toward +Z; yaw 180 makes it face north, at the desk.
        self.chair_root = Entity(parent=self.desk_root, position=(2.58, 0, 2.52),
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

        # A folded note and desk details provide period detail.
        cube(parent=self.desk_root, position=(2.20, 1.47, 4.86), scale=(0.70, 0.035, 0.44),
             tint=color.rgb(210, 198, 167))
        cube(parent=self.desk_root, position=(2.20, 1.49, 4.86), scale=(0.38, 0.018, 0.30),
             tint=color.rgb(226, 216, 190))
        self.note_target = hitbox((2.20, 1.62, 4.86), (0.82, 0.48, 0.54),
                                  "note", "experiment note target", solid=False,
                                  parent=self.desk_root)
        # Chair collision geometry follows its root, so the anomalous turn stays solid.
        self.chair_target = self.chair_hitbox

    def _build_wardrobe(self):
        x, z = -5.10, -1.70
        self.wardrobe_root = Entity(parent=scene, position=(x, 0, z),
                                    name="wardrobe group")
        cube(parent=self.wardrobe_root, position=(0, 1.40, 0), scale=(1.35, 2.78, 1.52),
             tint=color.rgb(65, 42, 31), collider="box", name="wardrobe")
        for side in (-1, 1):
            cube(parent=self.wardrobe_root, position=(side * 0.34, 1.42, -0.78),
                 scale=(0.64, 2.56, 0.055), tint=color.rgb(86, 56, 40))
            sphere(parent=self.wardrobe_root, position=(side * 0.07, 1.39, -0.83), scale=(0.075, 0.13, 0.06),
                   tint=color.rgb(183, 139, 76))
        cube(parent=self.wardrobe_root, position=(0, 2.94, 0), scale=(1.53, 0.16, 1.70), tint=color.rgb(78, 50, 36))
        self.wardrobe_target = hitbox((0, 1.38, 0), (1.45, 2.8, 1.60), "wardrobe",
                                      "wardrobe collision", solid=False,
                                      parent=self.wardrobe_root)

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
        self.window_target = hitbox((x, y, 5.58), (2.65, 2.05, 0.36),
                                    "window", "window target", solid=False)

        # Thick curtains, a rail, and small brass tiebacks.
        for side in (-1, 1):
            cube(position=(x + side * 1.43, 2.52, 5.38), scale=(0.44, 2.75, 0.35),
                 tint=color.rgb(25, 94, 119))
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
                                      "painting", "painting target", solid=False)

    def _build_clock(self):
        # Keep the entire dial below the ceiling line and use bold, unlit
        # colors so it stays readable even in the room's pale lighting.
        # The dial sits just inside the room from the north wall's inner face
        # (z=5.97), so its rim reads as mounted instead of floating forward.
        self.clock_root = Entity(parent=scene, position=(0.0, 3.00, 6.14), name="wall clock")
        # Flat meshes keep the circular rim fully visible from oblique angles.
        segments = 96
        rim_vertices = []
        rim_triangles = []
        for index in range(segments):
            angle = math.tau * index / segments
            cosine, sine = math.cos(angle), math.sin(angle)
            rim_vertices.extend((Vec3(cosine * 0.55, sine * 0.55, 0),
                                 Vec3(cosine * 0.68, sine * 0.68, 0)))
        for index in range(segments):
            inner = index * 2
            outer = inner + 1
            next_inner = ((index + 1) % segments) * 2
            next_outer = next_inner + 1
            rim_triangles.extend(((inner, outer, next_inner),
                                  (outer, next_outer, next_inner)))
        rim_mesh = Mesh(vertices=rim_vertices, triangles=rim_triangles,
                        mode='triangle', static=True)
        rim = Entity(parent=self.clock_root, model=rim_mesh,
                     position=(0, 0, -0.18), color=color.rgb(3, 7, 15),
                     shader=unlit_shader, double_sided=True, name="clock rim")
        rim.unlit = True

        face_vertices = [Vec3(0, 0, 0)]
        face_triangles = []
        face_radius = 0.535
        for index in range(segments):
            angle = math.tau * index / segments
            face_vertices.append(Vec3(math.cos(angle) * face_radius,
                                      math.sin(angle) * face_radius, 0))
        for index in range(segments):
            face_triangles.append((0, index + 1, (index + 1) % segments + 1))
        face_mesh = Mesh(vertices=face_vertices, triangles=face_triangles,
                         mode='triangle', static=True)
        face = Entity(parent=self.clock_root, model=face_mesh,
                      position=(0, 0, -0.19), color=color.rgb(255, 239, 157),
                      shader=unlit_shader, double_sided=True, name="clock face")
        face.unlit = True
        for index in range(12):
            angle = math.radians(index * 30)
            cardinal = index % 3 == 0
            marker = cube(parent=self.clock_root,
                          position=(math.sin(angle) * 0.40, math.cos(angle) * 0.40, -0.22),
                          scale=(0.06 if cardinal else 0.043,
                                 0.13 if cardinal else 0.085, 0.065),
                          tint=color.rgb(10, 25, 43), rotation=(0, 0, -index * 30))
            marker.unlit = True
        self.minute_pivot = Entity(parent=self.clock_root, position=(0, 0, -0.25))
        minute_hand = cube(parent=self.minute_pivot, position=(0, 0.17, 0),
                           scale=(0.065, 0.36, 0.07), tint=color.rgb(220, 28, 43))
        minute_hand.unlit = True
        self.hour_pivot = Entity(parent=self.clock_root, position=(0, 0, -0.29))
        hour_hand = cube(parent=self.hour_pivot, position=(0, 0.12, 0),
                         scale=(0.09, 0.25, 0.07), tint=color.rgb(12, 30, 55))
        hour_hand.unlit = True
        clock_pin = sphere(parent=self.clock_root, position=(0, 0, -0.30),
                           scale=(0.09, 0.09, 0.07), tint=color.rgb(255, 91, 24))
        clock_pin.unlit = True
    def _build_door(self):
        # South entrance: closed and locked until the timeline is stabilized.
        self.door_hinge = Entity(parent=scene, position=(2.80, 0.0, -5.90),
                                 rotation_y=0, name="door hinge")
        cube(parent=self.door_hinge, position=(1.00, 1.20, 0), scale=(2.00, 2.40, 0.16),
             tint=color.rgb(67, 42, 30), name="door")
        cube(parent=self.door_hinge, position=(1.00, 1.20, -0.10), scale=(1.70, 2.05, 0.045),
             tint=color.rgb(85, 54, 37))
        for y in (0.56, 1.88):
            cube(parent=self.door_hinge, position=(1.00, y, -0.13), scale=(1.55, 0.045, 0.03),
                 tint=color.rgb(130, 90, 57))
        sphere(parent=self.door_hinge, position=(1.72, 1.13, -0.17),
               scale=(0.11, 0.11, 0.07), tint=color.rgb(190, 146, 76))
        for x in (2.80, 4.80):
            cube(position=(x, 1.22, -5.87), scale=(0.13, 2.58, 0.30),
                 tint=color.rgb(89, 58, 39))
        cube(position=(3.80, 2.55, -5.87), scale=(2.0, 0.16, 0.30),
             tint=color.rgb(89, 58, 39))
        # Close the open wall section above the doorway; this is solid too.
        self.door_header_wall = cube(position=(3.80, 3.32, -6.08), scale=(2.20, 1.84, 0.24),
                                     tint=self.base_wall_color, collider="box",
                                     name="wall above door")
        self.walls.append(self.door_header_wall)
        # This overlaps the opening's side jambs slightly so the player cannot
        # squeeze through the thin seams beside a closed door.
        self.door_hitbox = hitbox((3.80, 1.18, -5.88), (2.16, 2.38, 0.42),
                                  "door", "door collision")
        self.door_open_amount = 0.0

    def _build_collapse_fx(self):
        """Add hidden cracks and wall pieces for the escalating collapse."""
        self.crack_roots = []
        self.crack_lines = []
        wall_faces = (
            ("north", (0, 0, 5.90), 0),
            ("south", (0, 0, -5.90), 180),
            ("east", (5.90, 0, 0), 90),
            ("west", (-5.90, 0, 0), -90),
        )
        # Each wall gets branching, jagged seams. They stay hidden until the
        # first paradox, then pulse cyan, magenta, and red as the room worsens.
        for face, origin, yaw in wall_faces:
            root = Entity(parent=scene, position=origin, rotation_y=yaw, enabled=False,
                          name=f"{face} wall fracture layer")
            self.crack_roots.append(root)
            paths = (
                [(-3.7, 0.15), (-3.35, 0.86), (-3.58, 1.42), (-2.96, 2.08),
                 (-3.12, 2.82), (-2.55, 3.65), (-2.72, 4.0)],
                [(0.1, 0.15), (-0.24, 0.76), (0.18, 1.28), (-0.36, 1.94),
                 (0.13, 2.47), (-0.18, 3.12), (0.42, 4.0)],
                [(3.7, 0.15), (3.30, 0.78), (3.66, 1.38), (3.08, 2.04),
                 (3.46, 2.72), (2.90, 3.35), (3.22, 4.0)],
            )
            for path_index, points in enumerate(paths):
                for a, b in zip(points, points[1:]):
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    length = math.hypot(dx, dy)
                    crack = cube(parent=root,
                                 position=((a[0] + b[0]) * 0.5,
                                           (a[1] + b[1]) * 0.5, 0),
                                 scale=(length, 0.12, 0.08),
                                 tint=color.white,
                                 rotation=(0, 0, math.degrees(math.atan2(dy, dx))))
                    crack.unlit = True
                    self.crack_lines.append(crack)
                # Short side branches give the seams a broken, lightning shape.
                pivot = points[2 + path_index]
                branch_end = (pivot[0] + (0.85 if path_index % 2 else -0.82),
                              pivot[1] + 0.62)
                dx, dy = branch_end[0] - pivot[0], branch_end[1] - pivot[1]
                branch = cube(parent=root,
                              position=((pivot[0] + branch_end[0]) * 0.5,
                                        (pivot[1] + branch_end[1]) * 0.5, 0),
                              scale=(math.hypot(dx, dy), 0.10, 0.08),
                              tint=color.white,
                              rotation=(0, 0, math.degrees(math.atan2(dy, dx))))
                branch.unlit = True
                self.crack_lines.append(branch)

        # Branching fractures across the underside of the ceiling. These use
        # the same flicker and color cycle as the wall cracks above.
        ceiling_root = Entity(parent=scene, position=(0, 4.16, 0), enabled=False,
                              name="ceiling fracture layer")
        self.crack_roots.append(ceiling_root)
        ceiling_paths = (
            [(-4.9, -3.8), (-3.6, -2.9), (-3.9, -1.9), (-2.4, -0.9),
             (-2.7, 0.2), (-1.3, 1.2), (-1.0, 2.4), (0.1, 3.4)],
            [(4.8, -3.9), (3.7, -2.8), (4.0, -1.8), (2.6, -0.9),
             (2.9, 0.3), (1.6, 1.4), (1.2, 2.5), (0.1, 3.7)],
            [(-4.5, 2.8), (-3.1, 2.1), (-2.2, 2.7), (-1.0, 2.4)],
        )
        for path_index, points in enumerate(ceiling_paths):
            for a, b in zip(points, points[1:]):
                dx, dz = b[0] - a[0], b[1] - a[1]
                crack = cube(parent=ceiling_root,
                             position=((a[0] + b[0]) * 0.5, 0,
                                       (a[1] + b[1]) * 0.5),
                             scale=(math.hypot(dx, dz), 0.075, 0.12),
                             tint=color.white,
                             rotation=(0, -math.degrees(math.atan2(dz, dx)), 0))
                crack.unlit = True
                self.crack_lines.append(crack)
            pivot = points[min(2 + path_index, len(points) - 2)]
            branch_end = (pivot[0] + (0.85 if path_index % 2 else -0.82),
                          pivot[1] + 0.62)
            dx, dz = branch_end[0] - pivot[0], branch_end[1] - pivot[1]
            branch = cube(parent=ceiling_root,
                          position=((pivot[0] + branch_end[0]) * 0.5, 0,
                                    (pivot[1] + branch_end[1]) * 0.5),
                          scale=(math.hypot(dx, dz), 0.075, 0.12),
                          tint=color.white,
                          rotation=(0, -math.degrees(math.atan2(dz, dx)), 0))
            branch.unlit = True
            self.crack_lines.append(branch)

        self.floor_cracks = []
        for start, end, tint in (
            ((-2.4, -1.2), (0.1, 0.6), color.white),
            ((1.9, -1.7), (0.2, 0.5), color.white),
            ((-0.2, 0.5), (1.45, 1.8), color.white),
        ):
            dx, dz = end[0] - start[0], end[1] - start[1]
            seam = cube(position=((start[0] + end[0]) * 0.5, 0.035,
                                  (start[1] + end[1]) * 0.5),
                        scale=(math.hypot(dx, dz), 0.065, 0.085), tint=tint,
                        rotation=(0, math.degrees(math.atan2(dz, dx)), 0))
            seam.unlit = True
            seam.enabled = False
            self.floor_cracks.append(seam)

        # The open space beyond the room becomes a dark void once the walls
        # shatter. The intact room hides it during ordinary play.
        self.void_sky = Entity(parent=scene, model="sphere", scale=(260, 260, 260),
                               color=color.rgb(9, 5, 20), double_sided=True,
                               collider=None, name="outside void")
        self.void_sky.unlit = True

        # Thick inset panels read as pieces of plaster when the solid walls
        # vanish. They fly inward and downward at different times.
        self.wall_shards = []
        for face, _, _ in wall_faces:
            for u in (-4.0, 0.0, 4.0):
                for y in (0.88, 2.62, 3.85):
                    if face in ("north", "south"):
                        position = (u, y, 5.88 if face == "north" else -5.88)
                        scale = (3.75, 1.66, 0.15)
                        direction = Vec3(random.uniform(-0.22, 0.22), 0,
                                         -1 if face == "north" else 1)
                    else:
                        position = (5.88 if face == "east" else -5.88, y, u)
                        scale = (0.15, 1.66, 3.75)
                        direction = Vec3(-1 if face == "east" else 1, 0,
                                         random.uniform(-0.22, 0.22))
                    shard = cube(position=position, scale=scale,
                                 tint=color.rgb(random.randint(111, 157),
                                                random.randint(94, 137),
                                                random.randint(74, 111)))
                    shard.enabled = False
                    self.wall_shards.append({
                        "entity": shard,
                        "origin": Vec3(*position),
                        "direction": direction,
                        "delay": random.uniform(0.0, 1.4),
                        "spin": random.uniform(-1, 1),
                    })

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
        self.machine_core.unlit = True
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
                                      "machine", "machine target", solid=False)
        self.machine_target.enabled = False
        self.machine_rotation = 0.0

    def _build_decor(self):
        # Keep the ceiling clear so the clock and its distortion effects stand out.
        for x in (-5.91, 5.91):
            cube(position=(x, 3.91, 0), scale=(0.14, 0.13, 11.8),
                 tint=color.rgb(67, 49, 38))
    def set_clock_step(self, step):
        """Move the wall hands from 12:00 through 12:02 in sync with the HUD."""
        self.clock_step = max(0, min(2, int(step)))
        self.minute_pivot.rotation_z = self.clock_step * 6.0
        self.hour_pivot.rotation_z = self.clock_step * 0.5

    def reset_to_normal(self):
        self.bed_root.position = (0, 0, 0)
        self.bedside_root.position = (0, 0, 0)
        self.desk_root.position = (0, 0, 0)
        self.wardrobe_root.position = (-5.10, 0, -1.70)
        self.wardrobe_root.rotation_y = 0
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
        self.set_clock_step(0)
        self.door_hinge.rotation_y = 0
        self.door_hitbox.enabled = True
        self.door_open_amount = 0.0

    def enter_ending_layout(self):
        """Rearrange familiar furniture while the success fade is black."""
        self.bed_root.position = (6.55, 0, 0)
        self.bedside_root.position = (6.45, 0, -0.53)
        self.desk_root.position = (-5.30, 0, 0)
        self.wardrobe_root.position = (-4.90, 0, -1.20)
        self.wardrobe_root.rotation_y = -90
        self.chair_root.rotation_y = 180
        self.door_hinge.rotation_y = 0
        self.door_open_amount = 0.0
        self.machine_root.enabled = False
        self.machine_target.enabled = False
        self.painting_room_image.enabled = False
        self.window_impossible.enabled = False

    def apply_anomaly(self, loop_number):
        self.reset_to_normal()
        self.clock_reset_flash_remaining = 0.65 if loop_number > 1 else 0.0
        if loop_number == 2:
            self.activate_first_anomaly()
        elif loop_number == 3:
            self.book_roots[2].enabled = False
            self.painting_room_image.enabled = True
        elif loop_number == 4:
            # The fourth loop brings back every room anomaly at once.
            self.activate_first_anomaly()
            self.book_roots[2].enabled = False
            self.painting_room_image.enabled = True
            self.window_impossible.enabled = True
        elif loop_number == FINAL_LOOP:
            self.machine_root.enabled = True
            self.machine_target.enabled = True

    def activate_first_anomaly(self):
        # Turn the seat toward the bed on the room's west side.
        self.chair_target_yaw = 90.0
        self.chair_fixing = True

    def fix_anomaly(self, target_tag):
        if target_tag == "chair":
            # Normal pose (180 degrees) faces the chair toward the desk.
            self.chair_target_yaw = 180.0
            self.chair_fixing = True
        elif target_tag == "books":
            root = self.book_roots[2]
            root.enabled = True
            root.scale = (0.03, 0.03, 0.03)
            root.scale_target = 1.0
            root.reveal_started = True
        elif target_tag == "painting":
            self.painting_room_image.enabled = False
        elif target_tag == "window":
            self.window_impossible.enabled = False
        elif target_tag == "machine":
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
        if state == "playing":
            if self.clock_reset_flash_remaining > 0:
                self.clock_reset_flash_remaining = max(
                    0.0, self.clock_reset_flash_remaining - dt)
                clock_step = 2
            else:
                clock_step = min(2, int(max(0.0, elapsed) // 60.0))
            self.set_clock_step(clock_step)
        elif state == "collapsing":
            self.set_clock_step(2)
        if paradox > self.last_paradox:
            self.last_paradox = paradox
            self.distortion_pulse = 1.25
        self.distortion_pulse = max(0.0, self.distortion_pulse - dt * 0.72)
        distortion = 0 if state in ("success_transition", "success") else min(3, paradox)
        pulse = self.distortion_pulse

        fractures_active = distortion > 0 or state == "collapsing"
        for root in self.crack_roots:
            root.enabled = fractures_active
        flicker_rate = 14.0 + distortion * 3.5
        for index, line in enumerate(self.crack_lines):
            line.enabled = (fractures_active and
                            math.sin(self.fx_time * flicker_rate + index * 1.71) > -0.15)
            line.color = color.white
        for index, seam in enumerate(self.floor_cracks):
            seams_active = state == "collapsing" or distortion >= 2
            seam.enabled = (seams_active and
                            math.sin(self.fx_time * flicker_rate + index * 2.13) > -0.15)
            seam.color = color.white

        # Each missed anomaly stains the walls further into violet and makes
        # the fixed wall props visibly slip out of alignment.
        if distortion:
            sway = math.sin(self.fx_time * (2.4 + distortion * 0.8))
            shade = int(8 * sway + 10 * pulse)
            shifted_wall = readable_tint(color.rgb(
                max(60, 148 - 11 * distortion - shade),
                max(52, 130 - 10 * distortion - shade),
                min(225, 108 + 24 * distortion + shade * 2)))
            for wall in self.walls:
                wall.color = shifted_wall
            warp = distortion * 0.36 + pulse * 0.45
            self.paint_root.rotation_z = sway * warp
            self.paint_root.scale_x = 1.0 + math.sin(self.fx_time * 3.1) * 0.004 * distortion
            self.window_root.rotation_y = math.sin(self.fx_time * 2.8 + 1.1) * warp
            self.clock_root.rotation_z = math.sin(self.fx_time * 4.2) * warp * 1.8
        else:
            for wall in self.walls:
                wall.color = readable_tint(self.base_wall_color)
            self.paint_root.rotation_z = 0
            self.paint_root.scale_x = 1
            self.window_root.rotation_y = 0
            self.clock_root.rotation_z = 0

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

        if state == "collapsing":
            shake = min(1.0, collapse_elapsed / 3.2)
            for entity in (self.chair_root, self.machine_root, self.door_hinge):
                if entity.enabled:
                    entity.x += random.uniform(-0.028, 0.028) * shake
                    entity.z += random.uniform(-0.025, 0.025) * shake
            if collapse_elapsed >= 4.7:
                for wall in self.walls:
                    wall.enabled = False
            if collapse_elapsed >= 5.4:
                self.ceiling.enabled = False
            for shard in self.wall_shards:
                shard_time = collapse_elapsed - 4.35 - shard["delay"]
                piece = shard["entity"]
                if shard_time >= 0:
                    piece.enabled = True
                    flight = min(7.0, shard_time)
                    piece.position = (shard["origin"]
                                      + shard["direction"] * (0.9 * flight + 0.12 * flight ** 2)
                                      + Vec3(0, -0.30 * flight ** 2, 0))
                    spin_rate = shard["spin"]
                    piece.rotation = (spin_rate * flight * 33,
                                      spin_rate * flight * 48,
                                      spin_rate * flight * 59)
            floor_break_time = collapse_elapsed - 6.35
            if floor_break_time > 0:
                for fragment in self.floor_fragments:
                    fragment_time = max(0.0, floor_break_time - fragment["delay"])
                    if fragment_time:
                        piece = fragment["entity"]
                        drift = fragment["drift"]
                        piece.position = (fragment["origin"]
                                          + drift * fragment_time
                                          + Vec3(0, -0.34 * fragment_time ** 2, 0))
                        spin_rate = fragment["spin"]
                        piece.rotation = (spin_rate * fragment_time * 30,
                                          spin_rate * fragment_time * 50,
                                          spin_rate * fragment_time * 75)

        # Flicker is occasional early, then increasingly hard to ignore.
        if state == "playing" and paradox > 0 and random.random() < dt * (0.34 + paradox * 0.75):
            self.flicker_remaining = random.uniform(0.045, 0.14 + paradox * 0.06)
        if self.flicker_remaining > 0:
            self.flicker_remaining = max(0, self.flicker_remaining - dt)
        if self.room_light:
            if state in ("success_transition", "success"):
                self.room_light.color = self.base_light_color
            elif state == "collapsing":
                phase = math.sin(collapse_elapsed * 30.0)
                if phase > 0.25:
                    self.room_light.color = color.rgb(255, 36, 112)
                elif phase < -0.35:
                    self.room_light.color = color.rgb(42, 210, 255)
                else:
                    self.room_light.color = color.rgb(255, 230, 205)
            elif self.flicker_remaining > 0:
                strength = random.uniform(0.12, 0.48) if paradox >= 2 else 0.60
                self.room_light.color = color.rgb(
                    int(max(0, 118 - 12 * distortion) * strength),
                    int(max(0, 92 - 10 * distortion) * strength),
                    int(min(180, 66 + 20 * distortion) * strength))
            else:
                self.room_light.color = color.rgb(
                    max(0, 118 - 12 * distortion),
                    max(0, 92 - 10 * distortion),
                    min(180, 66 + 20 * distortion))

        if self.cool_fill:
            if state == "collapsing":
                self.cool_fill.color = (color.rgb(255, 38, 143)
                                        if math.sin(collapse_elapsed * 30.0) > 0
                                        else color.rgb(48, 215, 255))
            else:
                self.cool_fill.color = color.rgb(
                    min(115, 32 + 12 * distortion),
                    max(0, 54 - 5 * distortion),
                    min(150, 91 + 12 * distortion))

        if paradox >= 2 and state == "playing":
            # Small clock tremors are visible but never obscure the real reading.
            jitter = math.sin(self.fx_time * 15) * 1.3
            self.hour_pivot.rotation_z += jitter * dt


class AnomalyManager:
    """Tracks this loop's anomalies and their independent repair states."""

    TAGS = {
        1: (),
        2: ("chair",),
        3: ("books", "painting"),
        4: ("chair", "books", "painting", "window"),
        FINAL_LOOP: ("machine",),
    }

    def __init__(self, room):
        self.room = room
        self.loop_number = 1
        self.active_tags = ()
        self.fixed_tags = set()

    @property
    def tag(self):
        return self.active_tags[0] if self.active_tags else None

    @property
    def active(self):
        return bool(self.active_tags)

    @property
    def fixed(self):
        return self.active and all(tag in self.fixed_tags for tag in self.active_tags)

    def start_loop(self, loop_number):
        self.loop_number = loop_number
        self.active_tags = self.TAGS.get(loop_number, ())
        self.fixed_tags.clear()
        self.room.apply_anomaly(loop_number)

    def fix(self, target_tag):
        if target_tag not in self.active_tags or target_tag in self.fixed_tags:
            return False
        self.fixed_tags.add(target_tag)
        self.room.fix_anomaly(target_tag)
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
        if self.loop_number == FINAL_LOOP and self.anomalies.fixed:
            self.on_success()
        return True

    def update(self, dt):
        if not self.started:
            return
        self.elapsed += dt
        self.time_remaining = max(0.0, LOOP_SECONDS - self.elapsed)
        if self.time_remaining <= 0:
            self._expire_loop()

    def _expire_loop(self):
        # Loop 1 is the full-length normal-room introduction, so it cannot
        # create a paradox. Each anomaly starts at the following reset.
        if self.anomalies.active and not self.anomalies.fixed:
            self.paradox += 1
        if self.loop_number == FINAL_LOOP:
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
        text_ink = color.rgb(0, 0, 0)
        self.stats_panel = Entity(parent=camera.ui, model="quad", position=(0.805, 0.43),
                                  scale=(0.17, 0.09), color=panel, z=0.5)
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
        self.thought_duration = 5.0
        self.thought_done = False
        self.failure_overlay = Entity(parent=camera.ui, model="quad", z=0.8,
                                      scale=(2, 2), color=color.rgba(0, 0, 0, 0),
                                      enabled=False)
        self.success_overlay = Entity(parent=camera.ui, model="quad", z=0.79,
                                      scale=(2, 2), color=color.rgba(0, 0, 0, 0),
                                      enabled=False)
        self.end_card_panel = Entity(parent=camera.ui, model="quad",
                                     position=(0, -0.02, 0.5), scale=(0.86, 0.42),
                                     color=color.rgba(235, 229, 216, 245),
                                     enabled=False)
        self.end_title = Text(parent=camera.ui, text="", position=(0, 0.08), origin=(0, 0),
                              scale=1.75, color=color.rgb(0, 0, 0), enabled=False)
        self.end_subtitle = Text(parent=camera.ui, text="", position=(0, -0.02), origin=(0, 0),
                                 scale=0.86, color=color.rgb(0, 0, 0), enabled=False)
        self.restart_text = Text(parent=camera.ui, text="R  RESTART", position=(0, -0.16),
                                 origin=(0, 0), scale=0.68, color=color.rgb(0, 0, 0),
                                 enabled=False)
        self.final_black = Entity(parent=camera.ui, model="quad",
                                  position=(0, 0, 0.4), scale=(2, 2),
                                  color=color.rgba(0, 0, 0, 0), enabled=False)
        self.final_title = Text(parent=camera.ui, text="THE END", position=(0, 0.06),
                                origin=(0, 0), scale=1.8,
                                color=color.rgb(255, 255, 255), z=-0.1,
                                enabled=False)
        self.final_restart = Text(parent=camera.ui, text="PRESS R TO RESTART",
                                  position=(0, -0.08), origin=(0, 0), scale=0.8,
                                  color=color.rgb(255, 255, 255), z=-0.1,
                                  enabled=False)
        self.end_screen_ready = False
        self.menu_backdrop = Entity(parent=camera.ui, model="quad", position=(0, 0, 0.9),
                                    scale=(2, 2), color=color.rgb(0, 0, 0),
                                    enabled=False)
        self.menu_title = Text(parent=camera.ui, text="PARADOX", position=(0, 0.14),
                               origin=(0.5, 0.5), scale=2.2,
                               color=color.rgb(255, 255, 255), enabled=False)
        self.menu_play = Button(parent=camera.ui, text="PLAY", position=(0, -0.08),
                                scale=(0.22, 0.085), color=color.rgb(230, 220, 195),
                                text_color=color.rgb(0, 0, 0), enabled=False)
        self.current_message = ""
        self.message_time = 0.0

    def show_menu(self):
        self.end_card_panel.enabled = False
        self.menu_backdrop.enabled = True
        self.menu_title.enabled = True
        self.menu_play.enabled = True
        self.stats_panel.enabled = False
        self.stats_text.enabled = False
        self.crosshair.enabled = False
        self.prompt_panel.enabled = False
        self.prompt_text.enabled = False
        self.message_panel.enabled = False
        self.message_text.enabled = False
        self.thought_panel.enabled = False
        self.thought.enabled = False

    def hide_menu(self):
        self.end_card_panel.enabled = False
        self.menu_backdrop.enabled = False
        self.menu_title.enabled = False
        self.menu_play.enabled = False
        self.stats_panel.enabled = True
        self.stats_text.enabled = True
        self.crosshair.enabled = True
        if not self.thought_done:
            self.thought_panel.enabled = True
            self.thought.enabled = True

    def show_message(self, message, duration=2.2):
        self.set_prompt("")
        self.current_message = message
        self.message_time = duration
        self.message_text.text = message
        self.message_panel.enabled = True
        self.message_text.enabled = True

    def set_prompt(self, text):
        visible = bool(text) and self.message_time <= 0
        self.prompt_panel.enabled = visible
        self.prompt_text.enabled = visible
        if visible:
            self.prompt_text.text = text

    def update(self, dt, remaining, paradox, state, clock_step,
               success_elapsed=0.0):
        if state != "menu" and not self.thought_done:
            self.thought_elapsed += dt
            if self.thought_elapsed >= self.thought_duration:
                self.thought_done = True
                self.thought_panel.enabled = False
                self.thought.enabled = False
            else:
                fade_start = 4.0
                alpha = 1.0 if self.thought_elapsed <= fade_start else max(
                    0.0, 1.0 - (self.thought_elapsed - fade_start) / (self.thought_duration - fade_start))
                self.thought_panel.color = color.rgba(226, 214, 190, int(225 * alpha))
                self.thought.color = color.rgba(0, 0, 0, int(255 * alpha))
        if self.message_time > 0:
            self.message_time = max(0.0, self.message_time - dt)
            if self.message_time == 0:
                self.message_panel.enabled = False
                self.message_text.enabled = False

        mins, secs = divmod(max(0, int(math.ceil(remaining))), 60)
        clock_text = CLOCK_LABELS[max(0, min(2, int(clock_step)))]
        if state in ("success_transition", "success"):
            mins, secs = 0, 0
        self.stats_text.text = (f"TIME {mins:02d}:{secs:02d}\n"
                                f"CLOCK {clock_text}\n"
                                f"PARADOX {paradox} / {MAX_PARADOX}")

    def begin_failure(self):
        self.failure_overlay.enabled = True
        self.stats_panel.enabled = False
        self.stats_text.enabled = False
        self.end_card_panel.enabled = False
        self.end_title.enabled = False
        self.end_subtitle.enabled = False
        self.restart_text.enabled = False
        self.final_black.enabled = False
        self.final_title.enabled = False
        self.final_restart.enabled = False
        self.end_screen_ready = False

    def finish_failure(self):
        self.final_black.color = color.rgba(0, 0, 0, 0)
        self.final_black.enabled = False
        self.final_title.enabled = False
        self.final_restart.enabled = False
        self.end_card_panel.position = (0, -0.02, 0.5)
        self.end_card_panel.scale = (0.86, 0.42)
        self.end_title.position = (0, 0.08)
        self.end_title.scale = 1.75
        self.end_card_panel.enabled = True
        self.end_title.text = "TEMPORAL COLLAPSE"
        self.end_subtitle.text = "THE TIMELINE COULD NOT BE STABILIZED."
        self.end_title.enabled = True
        self.end_subtitle.position = (0, -0.02)
        self.end_subtitle.scale = 0.86
        self.end_subtitle.enabled = True
        self.restart_text.enabled = False
        self.end_screen_ready = False

    def begin_success(self, clock_minutes):
        self.success_clock_start = clock_minutes
        self.end_card_panel.enabled = False
        self.stats_panel.enabled = False
        self.stats_text.enabled = False
        self.crosshair.enabled = False
        self.set_prompt("")
        self.message_panel.enabled = False
        self.message_text.enabled = False
        self.success_overlay.enabled = True
        self.success_overlay.color = color.rgba(0, 0, 0, 0)

    def set_success_fade(self, alpha):
        alpha = max(0, min(255, int(alpha)))
        self.success_overlay.enabled = True
        self.success_overlay.color = color.rgba(0, 0, 0, alpha)

    def finish_success(self):
        self.success_overlay.color = color.rgba(0, 0, 0, 0)
        self.success_overlay.enabled = False
        self.final_black.color = color.rgba(0, 0, 0, 0)
        self.final_black.enabled = False
        self.final_title.enabled = False
        self.final_restart.enabled = False
        self.end_card_panel.position = (0, 0.42, 0.5)
        self.end_card_panel.scale = (0.48, 0.105)
        self.end_card_panel.enabled = True
        self.message_panel.enabled = False
        self.message_text.enabled = False
        self.end_title.text = "YOU'RE HOME NOW"
        self.end_title.position = (0, 0.405)
        self.end_title.scale = 0.92
        self.end_title.enabled = True
        self.end_subtitle.enabled = False
        self.restart_text.enabled = False
        self.end_screen_ready = False

    def update_end_screen(self, elapsed):
        """Hold the ending card, then fade to the restart screen."""
        card_duration = 5.0
        fade_duration = 1.35
        if elapsed < card_duration:
            return

        fade = max(0.0, min(1.0, (elapsed - card_duration) / fade_duration))
        self.final_black.enabled = True
        self.final_black.color = color.rgba(0, 0, 0, int(255 * fade))
        if fade >= 1.0:
            self.end_card_panel.enabled = False
            self.end_title.enabled = False
            self.end_subtitle.enabled = False
            self.final_title.enabled = True
            self.final_restart.enabled = True
            self.end_screen_ready = True


class ParadoxGame(Entity):
    def __init__(self):
        super().__init__(name="paradox game controller")
        window.title = "PARADOX"
        window.color = color.rgb(11, 11, 15)
        window.vsync = True
        camera.fov = 88
        self.base_camera_fov = 88
        camera.clip_plane_far = 60
        camera.clip_plane_near = 0.05
        self.room = Room()
        self.player = FirstPersonController(position=(0, 0, -3.75), speed=0,
                                            gravity=0, jump_height=0.72)
        # Use custom vertical physics so jumps land on solid furniture and the
        # player falls naturally after walking off an edge.
        self.player.jump = lambda: None
        self.jump_height = 0.72
        self.player_gravity = 7.0
        self.player_vertical_speed = 0.0
        self.player_on_ground = True
        # The prefab stops on feet/head ray hits. Explicitly include the room's
        # wall, floor, and furniture colliders in those rays.
        self.player.traverse_target = scene
        # Large invisible inspect volumes are ray targets only. Excluding them
        # from the controller prevents invisible snags while preserving E prompts.
        self.player.ignore_list = [self.player, *NON_SOLID_HITBOXES]
        self.player.mouse_sensitivity = Vec2(38, 38)
        self.player.cursor.visible = False
        self.player.camera_pivot.y = 1.62
        self.player.position = (0, 0, -3.75)
        # The room ceiling is at y=4.21. This is a safety threshold for
        # canceling upward movement before the controller can clip through it.
        self.max_player_y = 2.10

        self.ui = Interface()
        self.audio = AudioManager()
        self.anomalies = AnomalyManager(self.room)
        self.state = "menu"
        self.collapse_elapsed = 0.0
        self.success_elapsed = 0.0
        self.ending_elapsed = 0.0
        self.success_room_rearranged = False
        self.camera_shake = 0.0
        self.tick_timer = 0.0
        self.footstep_timer = 0.0
        self.look_target_tag = None
        self.look_target_entity = None
        self.suppressed_prompt_tag = None
        self.loop_manager = LoopManager(self.room, self.anomalies,
                                        self.begin_failure, self.begin_success)
        self.ui.menu_play.on_click = self.start_game
        self.ui.show_menu()
        # FirstPersonController locks the cursor during its own initialization,
        # so release it after the controller and menu have both been created.
        mouse.locked = False
        mouse.visible = True

    def start_game(self):
        if self.state != "menu":
            return
        self.state = "playing"
        self.ui.hide_menu()
        mouse.locked = True
        mouse.visible = False
        self.player.speed = 3.65
        self.loop_manager.start()
        self.update_interaction()

    def begin_failure(self):
        if self.state in ("collapsing", "failed", "success", "success_transition"):
            return
        self.state = "collapsing"
        self.collapse_elapsed = 0.0
        self.failure_cues_played = set()
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
        self.success_room_rearranged = False
        start_minute = self.loop_manager.elapsed / 60.0
        self.room.success_clock_start_minute = start_minute
        self.ui.begin_success(start_minute)
        self.ui.set_prompt("")
        self.audio.stop()
        self.audio.play_effect("success")
        self.player.speed = 0

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
        if not self.ui.thought_done:
            self.look_target_tag = None
            self.look_target_entity = None
            self.ui.set_prompt("")
            return
        if self.ui.message_time > 0:
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
        current = (tag in self.anomalies.active_tags
                   and tag not in self.anomalies.fixed_tags)
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
            "note": "TRIAL 04 — controlled temporal displacement. Subject: you.",
            "bed": "The sheets smell of dust and old smoke.",
            "wardrobe": "The wardrobe door is swollen shut.",
            "door": "The handle will not turn. The lock is cold.",
            "machine": "Cold metal sits where there was only empty desk.",
        }
        self.ui.show_message(notes.get(tag, "Nothing unusual."), 2.3)

    def _solid_surface_heights_under_player(self):
        """Find every solid box top overlapping the player's footprint."""
        player_position = self.player.world_position
        heights = []
        platforms = list(SOLID_COLLISION_BOXES)
        # Keep these key walkable tops explicit as well as in the general
        # collider registry, so a loader/collider quirk cannot omit them.
        platforms.extend((self.room.bedside_tabletop, self.room.desk_top))
        for platform in platforms:
            if (not platform.enabled
                    or not getattr(platform, "solid_for_player", True)):
                continue
            center = platform.world_position
            # A forgiving footprint accounts for the player's body extending
            # beyond the point at its feet, especially on narrow furniture.
            half_x = abs(platform.scale_x) * 0.5 + 0.36
            half_z = abs(platform.scale_z) * 0.5 + 0.36
            if (abs(player_position.x - center.x) > half_x
                    or abs(player_position.z - center.z) > half_z):
                continue
            top = center.y + abs(platform.scale_y) * 0.5
            # The player cannot reach the wall tops; skipping them avoids
            # treating the room shell as a walkable platform.
            if 0.0 <= top <= self.max_player_y + 0.08:
                heights.append(top)
        return heights

    def _update_player_vertical(self, dt):
        """Apply gravity and sweep the player against room platforms."""
        previous_y = self.player.y
        ignore = [self.player, *NON_SOLID_HITBOXES]
        support_heights = self._solid_surface_heights_under_player()

        if self.player_on_ground and self.player_vertical_speed <= 0:
            nearby_supports = [height for height in support_heights
                               if abs(height - previous_y) <= 0.12]
            if nearby_supports:
                # Prefer the highest nearby top when colliders overlap (the
                # table body sits just below its visible tabletop).
                self.player.y = max(nearby_supports)
                self.player_vertical_speed = 0.0
                self.player.grounded = True
                return

        if self.player_on_ground and self.player_vertical_speed <= 0:
            support = raycast(self.player.world_position + Vec3(0, 0.08, 0),
                              Vec3(0, -1, 0), distance=0.20, ignore=ignore)
            if support.hit and abs(support.world_point.y - previous_y) <= 0.12:
                self.player.y = support.world_point.y
                self.player_vertical_speed = 0.0
                self.player.grounded = True
                return
            self.player_on_ground = False

        self.player_vertical_speed -= self.player_gravity * dt
        next_y = previous_y + self.player_vertical_speed * dt

        if self.player_vertical_speed < 0:
            crossed_surfaces = [height for height in support_heights
                                if next_y <= height <= previous_y + 0.08]
            if not crossed_surfaces:
                # Recover cleanly if a low frame rate or edge movement put the
                # feet a little below a surface before this update ran.
                crossed_surfaces = [height for height in support_heights
                                    if height - 0.40 <= previous_y < height]
            if crossed_surfaces:
                self.player.y = max(crossed_surfaces)
                self.player_vertical_speed = 0.0
                self.player_on_ground = True
                self.player.grounded = True
                return
            # Keep ray sweeps as a fallback for non-box and unusual colliders.
            ground = raycast(self.player.world_position + Vec3(0, 0.08, 0),
                             Vec3(0, -1, 0),
                             distance=previous_y - next_y + 0.16,
                             ignore=ignore)
            if (ground.hit and next_y <= ground.world_point.y
                    <= previous_y + 0.08):
                self.player.y = ground.world_point.y
                self.player_vertical_speed = 0.0
                self.player_on_ground = True
                self.player.grounded = True
                return
        elif self.player_vertical_speed > 0:
            # Stop an ascent at the underside of the ceiling or furniture.
            ceiling = raycast(self.player.world_position + Vec3(0, 1.88, 0),
                              Vec3(0, 1, 0),
                              distance=next_y - previous_y + 0.12,
                              ignore=ignore)
            if ceiling.hit:
                next_y = min(next_y, ceiling.world_point.y - 1.93)
                self.player_vertical_speed = 0.0

        if self.player_vertical_speed > 0 and next_y > self.max_player_y:
            next_y = self.max_player_y
            self.player_vertical_speed = 0.0
        if next_y <= 0:
            next_y = 0
            self.player_vertical_speed = 0.0
            self.player_on_ground = True
            self.player.grounded = True
        self.player.y = next_y
        self.player.grounded = self.player_on_ground

    def input(self, key):
        if self.state == "menu":
            return
        if key == "space" and self.state == "playing":
            if self.player_on_ground:
                self.player_on_ground = False
                self.player.grounded = False
                self.player_vertical_speed = math.sqrt(
                    2.0 * self.player_gravity * self.jump_height)
            return
        if key == "r" and self.state in ("failed", "success"):
            if not self.ui.end_screen_ready:
                return
            self.audio.cleanup()
            os.execv(sys.executable, [sys.executable, *sys.argv])
            return
        if (key.lower() == "e" and self.state == "playing"
                and self.ui.message_time <= 0 and self.look_target_tag):
            tag = self.look_target_tag
            self.suppressed_prompt_tag = tag
            self.ui.set_prompt("")
            if self.loop_manager.interact(tag):
                if self.loop_manager.loop_number == FINAL_LOOP:
                    if self.state == "playing":
                        self.ui.show_message("The core steadies. The ticking stops.", 3.2)
                else:
                    self.ui.show_message("Fixed.", 2.0)
                    self.audio.play_effect("fix")
            else:
                self._inspect(tag)

    def update(self):
        dt = time.dt
        # The glitch ambience belongs to normal play; begin_failure stops it
        # before the collapse cutscene and it must stay off until restart.
        cracks_active = self.state == "playing" and self.loop_manager.paradox > 0
        self.audio.set_glitch(cracks_active)
        if self.state == "playing":
            self._update_player_vertical(dt)
        if self.state == "playing" and self.loop_manager.paradox:
            level = min(3, self.loop_manager.paradox)
            phase = self.room.fx_time
            pulse = self.room.distortion_pulse
            camera.fov = self.base_camera_fov + math.sin(phase * 1.45) * level * 0.85
            self.player.camera_pivot.rotation_z = (
                math.sin(phase * 2.1) * level * 0.22 + pulse * level * 0.18)
        else:
            camera.fov = self.base_camera_fov
            self.player.camera_pivot.rotation_z = 0

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
            self.camera_shake = min(0.16, self.collapse_elapsed * 0.024)
            self.player.camera_pivot.rotation_z = (
                math.sin(self.collapse_elapsed * 17.0)
                * min(14.0, self.collapse_elapsed * 1.8))
            camera.fov = self.base_camera_fov + min(16.0, self.collapse_elapsed * 1.3)
            self.player.camera_pivot.position = (
                random.uniform(-self.camera_shake, self.camera_shake),
                1.62 + random.uniform(-self.camera_shake, self.camera_shake), 0)
            for cue, cue_time in (("crack_a", 0.55), ("crack_b", 2.65),
                                  ("crack_c", 4.7), ("fall", 7.05)):
                if self.collapse_elapsed >= cue_time and cue not in self.failure_cues_played:
                    self.failure_cues_played.add(cue)
                    self.audio.play_effect("fall" if cue == "fall" else "crack")
            if self.collapse_elapsed >= 7.05:
                self.room.floor.enabled = False
                fall_time = self.collapse_elapsed - 7.05
                self.player.speed = 0
                self.player.gravity = 0
                self.player.y -= dt * (1.6 + fall_time * 3.4)
            # Let the player see the fractures, flying wall slabs, and first
            # part of the fall before the dark overlay begins to close in.
            alpha = int(245 * max(0.0, min(1.0, (self.collapse_elapsed - 10.1) / 4.0)))
            self.ui.failure_overlay.color = color.rgba(0, 0, 0, alpha)
            if self.collapse_elapsed >= 14.7:
                self.state = "failed"
                self.ending_elapsed = 0.0
                self.ui.finish_failure()
                self.player.camera_pivot.position = (0, 1.62, 0)
                self.player.camera_pivot.rotation_z = 0
        elif self.state == "success_transition":
            self.success_elapsed += dt
            self.room.update(dt, self.loop_manager.paradox, self.loop_manager.elapsed,
                            self.state, success_elapsed=self.success_elapsed)
            fade_out_start = 2.8
            fade_out_duration = 1.2
            black_hold = 0.55
            fade_in_duration = 1.7
            black_time = fade_out_start + fade_out_duration
            fade_in_start = black_time + black_hold
            end_time = fade_in_start + fade_in_duration
            if self.success_elapsed < fade_out_start:
                fade_alpha = 0
            elif self.success_elapsed < black_time:
                fade_alpha = 255 * (self.success_elapsed - fade_out_start) / fade_out_duration
            elif self.success_elapsed < fade_in_start:
                fade_alpha = 255
            else:
                fade_alpha = 255 * (1.0 - (self.success_elapsed - fade_in_start) / fade_in_duration)
            self.ui.set_success_fade(fade_alpha)
            if self.success_elapsed >= black_time and not self.success_room_rearranged:
                self.success_room_rearranged = True
                self.room.enter_ending_layout()
                self.player.position = (0, 0, -3.75)
                self.player.rotation_y = 0
                self.player.camera_pivot.rotation_x = 0
                self.player.camera_pivot.rotation_z = 0
                self.player_vertical_speed = 0
                self.player_on_ground = True
                self.player.grounded = True
            if self.success_elapsed >= end_time and not self.ui.end_title.enabled:
                self.state = "success"
                self.ending_elapsed = 0.0
                self.ui.finish_success()
            self.update_interaction()

        if self.state in ("failed", "success"):
            self.ending_elapsed += dt
            self.ui.update_end_screen(self.ending_elapsed)

        self.ui.update(dt, self.loop_manager.time_remaining,
                       self.loop_manager.paradox, self.state, self.room.clock_step,
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
