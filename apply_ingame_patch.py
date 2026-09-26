#!/usr/bin/env python3
"""
apply_ingame_patch.py
Applies Phase 2 in-game gameplay patches for Rin Satsuki (char_id == 2):
- Preserves char_id == 2 throughout gameplay and restarts (fixes Esc+R bug).
- Adds dedicated .rin PE section for custom gameplay code, tables, and bullet logic.
- Expands player speed and shot function tables for Rin (Indices 4 and 5).
- Expands option animation and position tables.
- Expands bomb and option update dispatch tables.
- Implements Rin's Flower Sign (Type B) "Flower Bloom" shot mechanics:
    * Unfocused Mode: Progressive blossoming flower petals providing wide screen coverage
      with a high-damage core bullet (●).
    * Focused Mode (Shift): Needle-sharp concentrated forward vertical pillar of high-damage petals
      for melting bosses and tough targets.
    * 8-level Danmaku power progression scaling from 0 to 128 (MAX power).
"""

import struct
import math
import os
import shutil

TARGET_EXE = 'th06nc/th06nc.exe'
BACKUP_EXE = 'th06nc/th06nc.exe.checkpoint_charselect'

UP_RAD = -math.pi / 2.0  # -90 degrees (straight up in Touhou screen space)

def make_stream(period, phase, off_x, off_y, hit_w, hit_h, angle_rad, speed, damage, source=0, b_type=0, anm=0x460, sfx=0):
    """
    Constructs a 36-byte (0x24) BulletStreamDef native struct for Touhou 6:
      int16_t period      (+0x00)
      int16_t phase       (+0x02)
      float   off_x       (+0x04)
      float   off_y       (+0x08)
      float   hit_w       (+0x0c)
      float   hit_h       (+0x10)
      float   angle       (+0x14)
      float   speed       (+0x18)
      uint16_t damage     (+0x1c)
      uint8_t source      (+0x1e: 0=player, 1..4=options)
      uint8_t b_type      (+0x1f: 0=linear, 1=homing, 3=laser)
      int16_t anm_script  (+0x20: 0x460=Reimu amulet)
      int16_t sfx_id      (+0x22: 0=first bullet plays SFX, -1=silent)
    """
    return struct.pack(
        '<hhffffffHBBhh',
        period, phase,
        off_x, off_y,
        hit_w, hit_h,
        angle_rad, speed,
        damage, source, b_type,
        anm, sfx
    )

def build_shot_stub(stub_va, power_table_va, spawner_va=0x14006a580):
    """
    Generates native x86_64 stub for calling Player Shot Spawner (0x14006a580):
      sub rsp, 0x38
      lea rax, [rip + disp_to_power_table]
      mov qword ptr [rsp + 0x20], rax
      call 0x14006a580
      add rsp, 0x38
      ret
    """
    code = bytearray()
    code += bytes.fromhex('48 83 ec 38') # sub rsp, 0x38
    disp_table = power_table_va - (stub_va + 11)
    code += bytes.fromhex('48 8d 05') + struct.pack('<i', disp_table)
    code += bytes.fromhex('48 89 44 24 20') # mov [rsp + 0x20], rax
    disp_spawner = spawner_va - (stub_va + 21)
    code += bytes.fromhex('e8') + struct.pack('<i', disp_spawner)
    code += bytes.fromhex('48 83 c4 38 c3') # add rsp, 0x38; ret
    return code

class MiniAssembler:
    """
    Two-pass x86_64 assembler to eliminate hand-coded relative displacement errors.
    Automatically resolves label addresses, 32-bit relative branches (jcc/jmp),
    and RIP-relative 32-bit float loads.
    """
    def __init__(self, base_va):
        self.base_va = base_va
        self.items = []
        self.labels = {}
        self.floats = {}

    def label(self, name):
        self.items.append(('label', name))

    def raw(self, byte_seq):
        self.items.append(('raw', byte_seq))

    def hex(self, hex_str):
        self.items.append(('raw', bytes.fromhex(hex_str.replace(' ', ''))))

    def jmp_rel32(self, target_label):
        self.items.append(('jmp', target_label))

    def jcc_rel32(self, cond, target_label):
        self.items.append(('jcc', (cond, target_label)))

    def jmp_abs(self, target_va):
        self.items.append(('jmp_abs', target_va))

    def lea_rcx_abs(self, target_va):
        self.items.append(('lea_rcx_abs', target_va))

    def movss_xmm1_rip(self, float_name):
        self.items.append(('movss_rip', float_name))

    def mulss_xmm1_rip(self, float_name):
        self.items.append(('mulss_rip', float_name))

    def define_float(self, name, val):
        self.floats[name] = float(val)

    def assemble(self):
        pc = 0
        for it in self.items:
            t = it[0]
            if t == 'label':
                self.labels[it[1]] = pc
            elif t == 'raw':
                pc += len(it[1])
            elif t == 'jmp':
                pc += 5
            elif t == 'jcc':
                pc += 6
            elif t == 'jmp_abs':
                pc += 5
            elif t == 'lea_rcx_abs':
                pc += 7
            elif t == 'movss_rip':
                pc += 8
            elif t == 'mulss_rip':
                pc += 8

        pad = (16 - (pc % 16)) % 16
        pc += pad

        float_offsets = {}
        for fname, val in self.floats.items():
            float_offsets[fname] = pc
            pc += 4

        out = bytearray()
        for it in self.items:
            t = it[0]
            cur_va = self.base_va + len(out)
            if t == 'label':
                pass
            elif t == 'raw':
                out += it[1]
            elif t == 'jmp':
                target_va = self.base_va + self.labels[it[1]]
                disp = target_va - (cur_va + 5)
                out += b'\xe9' + struct.pack('<i', disp)
            elif t == 'jcc':
                cond, target_label = it[1]
                target_va = self.base_va + self.labels[target_label]
                disp = target_va - (cur_va + 6)
                opcodes = {
                    'je':  b'\x0f\x84',
                    'jne': b'\x0f\x85',
                    'jge': b'\x0f\x8d',
                    'jl':  b'\x0f\x8c',
                }
                out += opcodes[cond] + struct.pack('<i', disp)
            elif t == 'jmp_abs':
                target_va = it[1]
                disp = target_va - (cur_va + 5)
                out += b'\xe9' + struct.pack('<i', disp)
            elif t == 'lea_rcx_abs':
                target_va = it[1]
                disp = target_va - (cur_va + 7)
                out += bytes.fromhex('48 8d 0d') + struct.pack('<i', disp)
            elif t == 'movss_rip':
                fname = it[1]
                target_va = self.base_va + float_offsets[fname]
                disp = target_va - (cur_va + 8)
                out += bytes.fromhex('f3 0f 10 0d') + struct.pack('<i', disp)
            elif t == 'mulss_rip':
                fname = it[1]
                target_va = self.base_va + float_offsets[fname]
                disp = target_va - (cur_va + 8)
                out += bytes.fromhex('f3 0f 59 0d') + struct.pack('<i', disp)

        out += b'\x90' * pad
        for fname, val in self.floats.items():
            out += struct.pack('<f', val)

        return bytes(out)


def build_bloom_hook(hook_va, log_buf_va):
    """
    Constructs native x86_64 routine for true Blooming and Dissipating Danmaku:
    
    1. Unfocused Mode (b_type == 4):
       - Fase 1 (Ascenso, age < 12): Sube como semilla compacta vertical (Vy = -14.0 px/f) hasta Y ~210.
       - Fase 2 (Frenado, 12 <= age < 18): Desacelera suavemente frente al objetivo (Vy *= 0.60) hasta Y ~190.
       - Fase 3 (Apertura Floral Visible, 18 <= age < 30, 12 frames):
         Abre gradualmente hacia afuera usando [rbx + 0x154] (speed por bala):
         * Núcleo ●: speed = 0.0 -> ¡SE QUEDA QUIETO EN EL CENTRO EXACTO!
         * Pétalos: speed = 3.8..4.4 -> Se abren en 12 frames hasta un radio de 46..53 px (diámetro ~100 px).
       - Fase 4 (Flor Flotando Estática, 30 <= age < 65, 35 frames = ~0.6s):
         LA FLOR ENTERA SE QUEDA FLOTANDO VISIBLE (Vy = -0.3 px/f), cubriendo la pantalla y triturando al jefe.
       - Fase 5 (Disipación, age >= 65): ¡La flor se disipa limpiamente! (mov word ptr [rbx + 0x10], 0).
       
    2. Focused Mode (b_type == 5):
       - Fase 1 (Ascenso Rápido, age < 10): Sube a alta velocidad (Vy = -18.0) directo al enemigo.
       - Fase 2 (Frenado sobre Boss, 10 <= age < 15): Desacelera en el hitbox del boss (Vy *= 0.50).
       - Fase 3 (Lanza Concentrada, 15 <= age < 23): Abre en un diamante floral cerrado (X in [-12, +12]).
       - Fase 4 (Taladrado Estático, 23 <= age < 55, 32 frames = ~0.53s): Daño masivo continuo (Vy = -0.4).
       - Fase 5 (Disipación, age >= 55): Se disipa limpiamente.
    """
    asm = MiniAssembler(hook_va)

    asm.define_float('neg_14_0', -14.0)
    asm.define_float('mul_0_60', 0.60)
    asm.define_float('neg_0_3', -0.3)

    asm.define_float('neg_18_0', -18.0)
    asm.define_float('mul_0_50', 0.50)
    asm.define_float('neg_0_4', -0.4)

    # Check b_type at [rbx + 0x12]
    # movzx eax, word ptr [rbx + 0x12]
    asm.hex('0f b7 43 12')
    # cmp eax, 4
    asm.hex('83 f8 04')
    asm.jcc_rel32('je', 'unfoc_flower')
    # cmp eax, 5
    asm.hex('83 f8 05')
    asm.jcc_rel32('je', 'foc_flower')

    # Fallback for standard bullets (b_type == 0):
    # movss xmm2, dword ptr [rbx + 8]
    asm.hex('f3 0f 10 53 08')
    # jmp 0x140069a3b (resume standard Player::UpdateBullets)
    asm.jmp_abs(0x140069a3b)

    # =========================================================================
    # 1. UNFOCUSED FLOWER (b_type == 4)
    # =========================================================================
    asm.label('unfoc_flower')
    # mov eax, dword ptr [rbx + 4] (age)
    asm.hex('8b 43 04')
    # cmp eax, 12
    asm.hex('83 f8 0c')
    asm.jcc_rel32('jge', 'unfoc_decel')

    # Phase 1: Ascent
    # xorps xmm2, xmm2 (Vx = 0.0)
    asm.hex('0f 57 d2')
    # movss xmm1, [rip + neg_14_0] (Vy = -14.0)
    asm.movss_xmm1_rip('neg_14_0')
    # movss [rbx + 8], xmm2
    asm.hex('f3 0f 11 53 08')
    # movss [rbx + 0xc], xmm1
    asm.hex('f3 0f 11 4b 0c')
    asm.jmp_rel32('do_telemetry')

    # Phase 2: Decel (12 <= age < 18)
    asm.label('unfoc_decel')
    # cmp eax, 18
    asm.hex('83 f8 12')
    asm.jcc_rel32('jge', 'unfoc_opening')
    # xorps xmm2, xmm2
    asm.hex('0f 57 d2')
    # movss xmm1, [rbx + 0xc]
    asm.hex('f3 0f 10 4b 0c')
    # mulss xmm1, [rip + mul_0_60]
    asm.mulss_xmm1_rip('mul_0_60')
    # movss [rbx + 8], xmm2
    asm.hex('f3 0f 11 53 08')
    # movss [rbx + 0xc], xmm1
    asm.hex('f3 0f 11 4b 0c')
    asm.jmp_rel32('do_telemetry')

    # Phase 3: Opening into Full Flower Pattern (18 <= age < 30)
    asm.label('unfoc_opening')
    # cmp eax, 30
    asm.hex('83 f8 1e')
    asm.jcc_rel32('jge', 'unfoc_hovering')
    # cmp eax, 18 (only compute opening velocity on first frame)
    asm.hex('83 f8 12')
    asm.jcc_rel32('jne', 'unfoc_keep_vel')
    # fld dword ptr [rbx + 0x158] (angle)
    asm.hex('d9 83 58 01 00 00')
    # fsincos (ST0=cos, ST1=sin)
    asm.hex('d9 fb')
    # fmul dword ptr [rbx + 0x154] (speed per bullet! Core=0.0, Petals=3.8..4.4)
    asm.hex('d8 8b 54 01 00 00')
    # fstp dword ptr [rbx + 8] (Vx = cos * speed, pops ST0)
    asm.hex('d9 5b 08')
    # fmul dword ptr [rbx + 0x154] (sin * speed)
    asm.hex('d8 8b 54 01 00 00')
    # fstp dword ptr [rbx + 0xc] (Vy = sin * speed, pops ST0)
    asm.hex('d9 5b 0c')

    asm.label('unfoc_keep_vel')
    # movss xmm2, [rbx + 8]
    asm.hex('f3 0f 10 53 08')
    # movss xmm1, [rbx + 0xc]
    asm.hex('f3 0f 10 4b 0c')
    asm.jmp_rel32('do_telemetry')

    # Phase 4: Stationary Hovering Flower (30 <= age < 65, 35 frames = ~0.60s)
    asm.label('unfoc_hovering')
    # cmp eax, 65 (0x41)
    asm.hex('83 f8 41')
    asm.jcc_rel32('jge', 'unfoc_dissipate')
    # xorps xmm2, xmm2 (Vx = 0.0)
    asm.hex('0f 57 d2')
    # movss xmm1, [rip + neg_0_3] (Vy = -0.3)
    asm.movss_xmm1_rip('neg_0_3')
    # movss [rbx + 8], xmm2
    asm.hex('f3 0f 11 53 08')
    # movss [rbx + 0xc], xmm1
    asm.hex('f3 0f 11 4b 0c')
    asm.jmp_rel32('do_telemetry')

    # Phase 5: Dissipation (age >= 65)
    asm.label('unfoc_dissipate')
    # mov word ptr [rbx + 0x10], 0 (bullet dead/dissipated!)
    asm.hex('66 c7 43 10 00 00')
    # xorps xmm2, xmm2
    asm.hex('0f 57 d2')
    # xorps xmm1, xmm1
    asm.hex('0f 57 c9')
    # movss [rbx + 8], xmm2
    asm.hex('f3 0f 11 53 08')
    # movss [rbx + 0xc], xmm1
    asm.hex('f3 0f 11 4b 0c')
    asm.jmp_rel32('do_telemetry')

    # =========================================================================
    # 2. FOCUSED FLOWER (b_type == 5)
    # =========================================================================
    asm.label('foc_flower')
    # mov eax, dword ptr [rbx + 4] (age)
    asm.hex('8b 43 04')
    # cmp eax, 10
    asm.hex('83 f8 0a')
    asm.jcc_rel32('jge', 'foc_decel')

    # Phase 1: Fast Forward Ascent
    # xorps xmm2, xmm2
    asm.hex('0f 57 d2')
    # movss xmm1, [rip + neg_18_0] (Vy = -18.0)
    asm.movss_xmm1_rip('neg_18_0')
    # movss [rbx + 8], xmm2
    asm.hex('f3 0f 11 53 08')
    # movss [rbx + 0xc], xmm1
    asm.hex('f3 0f 11 4b 0c')
    asm.jmp_rel32('do_telemetry')

    # Phase 2: Decel on Boss Hitbox (10 <= age < 15)
    asm.label('foc_decel')
    # cmp eax, 15
    asm.hex('83 f8 0f')
    asm.jcc_rel32('jge', 'foc_opening')
    # xorps xmm2, xmm2
    asm.hex('0f 57 d2')
    # movss xmm1, [rbx + 0xc]
    asm.hex('f3 0f 10 4b 0c')
    # mulss xmm1, [rip + mul_0_50]
    asm.mulss_xmm1_rip('mul_0_50')
    # movss [rbx + 8], xmm2
    asm.hex('f3 0f 11 53 08')
    # movss [rbx + 0xc], xmm1
    asm.hex('f3 0f 11 4b 0c')
    asm.jmp_rel32('do_telemetry')

    # Phase 3: Concentrated Floral Lance Opening (15 <= age < 23, 8 frames)
    asm.label('foc_opening')
    # cmp eax, 23
    asm.hex('83 f8 17')
    asm.jcc_rel32('jge', 'foc_hovering')
    # cmp eax, 15
    asm.hex('83 f8 0f')
    asm.jcc_rel32('jne', 'foc_keep_vel')
    # fld dword ptr [rbx + 0x158] (angle)
    asm.hex('d9 83 58 01 00 00')
    # fsincos
    asm.hex('d9 fb')
    # fmul dword ptr [rbx + 0x154] (speed per needle)
    asm.hex('d8 8b 54 01 00 00')
    # fstp dword ptr [rbx + 8] (Vx)
    asm.hex('d9 5b 08')
    # fmul dword ptr [rbx + 0x154] (speed)
    asm.hex('d8 8b 54 01 00 00')
    # fstp dword ptr [rbx + 0xc] (Vy)
    asm.hex('d9 5b 0c')

    asm.label('foc_keep_vel')
    # movss xmm2, [rbx + 8]
    asm.hex('f3 0f 10 53 08')
    # movss xmm1, [rbx + 0xc]
    asm.hex('f3 0f 10 4b 0c')
    asm.jmp_rel32('do_telemetry')

    # Phase 4: Concentrated Static Drilling (23 <= age < 55, 32 frames = ~0.53s)
    asm.label('foc_hovering')
    # cmp eax, 55 (0x37)
    asm.hex('83 f8 37')
    asm.jcc_rel32('jge', 'foc_dissipate')
    # xorps xmm2, xmm2
    asm.hex('0f 57 d2')
    # movss xmm1, [rip + neg_0_4] (Vy = -0.4)
    asm.movss_xmm1_rip('neg_0_4')
    # movss [rbx + 8], xmm2
    asm.hex('f3 0f 11 53 08')
    # movss [rbx + 0xc], xmm1
    asm.hex('f3 0f 11 4b 0c')
    asm.jmp_rel32('do_telemetry')

    # Phase 5: Dissipation (age >= 55)
    asm.label('foc_dissipate')
    # mov word ptr [rbx + 0x10], 0
    asm.hex('66 c7 43 10 00 00')
    # xorps xmm2, xmm2
    asm.hex('0f 57 d2')
    # xorps xmm1, xmm1
    asm.hex('0f 57 c9')
    # movss [rbx + 8], xmm2
    asm.hex('f3 0f 11 53 08')
    # movss [rbx + 0xc], xmm1
    asm.hex('f3 0f 11 4b 0c')
    asm.jmp_rel32('do_telemetry')

    # =========================================================================
    # 3. TELEMETRY LOGGING BUFFER
    # =========================================================================
    asm.label('do_telemetry')
    # push rax; push rcx; push rdx
    asm.hex('50 51 52')
    # lea rcx, [rip + disp_to_log_buf]
    asm.lea_rcx_abs(log_buf_va)
    # mov eax, [rcx]
    asm.hex('8b 01')
    # cmp eax, 512
    asm.hex('3d 00 02 00 00')
    asm.jcc_rel32('jge', 'telemetry_done')
    # inc dword ptr [rcx]
    asm.hex('ff 01')
    # shl rax, 5
    asm.hex('48 c1 e0 05')
    # lea rdx, [rcx + rax + 8]
    asm.hex('48 8d 54 01 08')
    # mov eax, [rbx + 4]; mov [rdx], eax (age)
    asm.hex('8b 43 04 89 02')
    # movzx eax, word ptr [rbx + 0x12]; mov [rdx + 4], eax (b_type)
    asm.hex('0f b7 43 12 89 42 04')
    # mov eax, [rbx + 0x13c]; mov [rdx + 8], eax (X)
    asm.hex('8b 83 3c 01 00 00 89 42 08')
    # mov eax, [rbx + 0x140]; mov [rdx + 0xc], eax (Y)
    asm.hex('8b 83 40 01 00 00 89 42 0c')
    # mov eax, [rbx + 8]; mov [rdx + 0x10], eax (Vx)
    asm.hex('8b 43 08 89 42 10')
    # mov eax, [rbx + 0xc]; mov [rdx + 0x14], eax (Vy)
    asm.hex('8b 43 0c 89 42 14')
    # mov eax, [rbx + 0x158]; mov [rdx + 0x18], eax (angle)
    asm.hex('8b 83 58 01 00 00 89 42 18')
    # mov eax, [rbx + 0x154]; mov [rdx + 0x1c], eax (speed)
    asm.hex('8b 83 54 01 00 00 89 42 1c')

    asm.label('telemetry_done')
    # pop rdx; pop rcx; pop rax
    asm.hex('5a 59 58')
    # jmp 0x140069a40 (resume position integration in Touhou 6)
    asm.jmp_abs(0x140069a40)

    return asm.assemble()


def build_flower_bloom_data(base_stream_unfoc_va, base_stream_foc_va):
    """
    Constructs power tables and bullet streams for Flower Sign (Type B).
    
    Unfocused (b_type = 4):
    - Cadence: Period 18 frames (~3.33 flowers/sec).
    - Compact seed ascends to mid-upper screen (Y ~210).
    - Decelerates softly to Y ~190.
    - Blooms visibly outward into a full ~100 px diameter flower:
      * Core (speed = 0.0) stays directly at center!
      * 10 Petals expand outward to radius 46..53 px!
    - Stays hovering in place for ~0.6 seconds shredding enemies.
    - Dissipates cleanly at frame 65.
    
    Focused (b_type = 5):
    - Cadence: Period 18 frames (~3.33 flowers/sec). NO machine gun!
    - High-speed direct ascent (Vy = -18 px/f).
    - Opens in a tight, concentrated forward floral spear directly on the boss's center.
    - Hovers drilling damage into the boss, then dissipates cleanly at frame 55.
    """
    thresholds = [8, 16, 32, 48, 64, 80, 96, 999]

    # Angles for radial flower explosion (Unfocused)
    P_N  = UP_RAD                       # Top North (-90 deg)
    P_NE = -math.pi / 4.0               # Top-Right (-45 deg)
    P_E  = 0.0                          # Right East (0 deg)
    P_SE = math.pi / 4.0                # Bot-Right (+45 deg)
    P_S  = math.pi / 2.0                # Bot South (+90 deg)
    P_SW = 3.0 * math.pi / 4.0          # Bot-Left (+135 deg)
    P_W  = math.pi                      # Left West (180 deg)
    P_NW = -3.0 * math.pi / 4.0         # Top-Left (-135 deg)
    P_CR = -3.0 * math.pi / 8.0         # Crown-Right (-67.5 deg)
    P_CL = -5.0 * math.pi / 8.0         # Crown-Left (-112.5 deg)

    # Angles for focused concentrated forward flower (tight cone around -90 deg)
    F_C   = UP_RAD                      # Center Lance (-90 deg)
    F_IL  = -96.0 * math.pi / 180.0     # Inner Left (-96 deg)
    F_IR  = -84.0 * math.pi / 180.0     # Inner Right (-84 deg)
    F_OL  = -102.0 * math.pi / 180.0    # Outer Left (-102 deg)
    F_OR  = -78.0 * math.pi / 180.0     # Outer Right (-78 deg)
    F_CRL = -108.0 * math.pi / 180.0    # Crown Left (-108 deg)
    F_CRR = -72.0 * math.pi / 180.0     # Crown Right (-72 deg)

    # Cadence for both modes: period 18 frames (~3.33 flowers/sec)
    BLOOM_PER = 18

    # =========================================================================
    # 1. UNFOCUSED MODE: EXPANSIVE FLOWER BLOOM (Radius ~50px, Core in center)
    # =========================================================================
    unfoc_levels = []

    # Level 0 (Power 0..7): 1 Bullet (Seed ●, speed=0.0)
    unfoc_levels.append([
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N, 0.0, 80, b_type=4, sfx=0)
    ])
    # Level 1 (Power 8..15): 3 Bullets (Core ● in center + Vertical Petals N & S)
    unfoc_levels.append([
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N, 0.0, 100, b_type=4, sfx=0), # Core ●
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N, 3.8,  40, b_type=4, sfx=-1), # Top N (opens to -45 px)
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_S, 3.8,  40, b_type=4, sfx=-1), # Bot S (opens to +45 px)
    ])
    # Level 2 (Power 16..31): 5 Bullets (Core ● in center + 4-Petal Cross N, E, S, W)
    unfoc_levels.append([
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N, 0.0, 130, b_type=4, sfx=0), # Core ●
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N, 3.8,  42, b_type=4, sfx=-1), # Top N
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_E, 3.8,  42, b_type=4, sfx=-1), # Right E
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_S, 3.8,  42, b_type=4, sfx=-1), # Bot S
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_W, 3.8,  42, b_type=4, sfx=-1), # Left W
    ])
    # Level 3 (Power 32..47): 7 Bullets (Core ● in center + 6-Petal Star)
    unfoc_levels.append([
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N,  0.0, 160, b_type=4, sfx=0), # Core ●
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N,  3.8,  45, b_type=4, sfx=-1), # Top N
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_NE, 4.2,  45, b_type=4, sfx=-1), # Top-Right NE
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_E,  3.8,  45, b_type=4, sfx=-1), # Right E
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_S,  3.8,  45, b_type=4, sfx=-1), # Bot S
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_W,  3.8,  45, b_type=4, sfx=-1), # Left W
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_NW, 4.2,  45, b_type=4, sfx=-1), # Top-Left NW
    ])
    # Level 4 (Power 48..63): Full 8-Petal Radial Flower (9 Bullets)
    unfoc_levels.append([
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N,  0.0, 180, b_type=4, sfx=0), # Core ●
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N,  3.8,  48, b_type=4, sfx=-1), # Top N
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_NE, 4.2,  48, b_type=4, sfx=-1), # Top-Right NE
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_E,  3.8,  48, b_type=4, sfx=-1), # Right E
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_SE, 4.2,  48, b_type=4, sfx=-1), # Bot-Right SE
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_S,  3.8,  48, b_type=4, sfx=-1), # Bot S
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_SW, 4.2,  48, b_type=4, sfx=-1), # Bot-Left SW
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_W,  3.8,  48, b_type=4, sfx=-1), # Left W
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_NW, 4.2,  48, b_type=4, sfx=-1), # Top-Left NW
    ])
    # Level 5 (Power 64..79): Enhanced 8-Petal Radial Flower (9 Bullets, boosted damage)
    unfoc_levels.append([
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N,  0.0, 210, b_type=4, sfx=0), # Core ●
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N,  3.8,  52, b_type=4, sfx=-1), # Top N
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_NE, 4.2,  52, b_type=4, sfx=-1), # Top-Right NE
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_E,  3.8,  52, b_type=4, sfx=-1), # Right E
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_SE, 4.2,  52, b_type=4, sfx=-1), # Bot-Right SE
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_S,  3.8,  52, b_type=4, sfx=-1), # Bot S
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_SW, 4.2,  52, b_type=4, sfx=-1), # Bot-Left SW
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_W,  3.8,  52, b_type=4, sfx=-1), # Left W
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_NW, 4.2,  52, b_type=4, sfx=-1), # Top-Left NW
    ])
    # Level 6 (Power 80..95): Grand 10-Petal Chrysanthemum (11 Bullets)
    unfoc_levels.append([
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N,  0.0, 240, b_type=4, sfx=0), # Core ●
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N,  3.8,  55, b_type=4, sfx=-1), # Top N
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_NE, 4.2,  55, b_type=4, sfx=-1), # Top-Right NE
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_E,  3.8,  55, b_type=4, sfx=-1), # Right E
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_SE, 4.2,  55, b_type=4, sfx=-1), # Bot-Right SE
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_S,  3.8,  55, b_type=4, sfx=-1), # Bot S
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_SW, 4.2,  55, b_type=4, sfx=-1), # Bot-Left SW
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_W,  3.8,  55, b_type=4, sfx=-1), # Left W
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_NW, 4.2,  55, b_type=4, sfx=-1), # Top-Left NW
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_CR, 4.4,  55, b_type=4, sfx=-1), # Crown-Right
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_CL, 4.4,  55, b_type=4, sfx=-1), # Crown-Left
    ])
    # Level 7 (Power 96..128 MAX): Celestial 10-Petal Chrysanthemum (11 Bullets, MAX Power)
    unfoc_levels.append([
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N,  0.0, 280, b_type=4, sfx=0), # High-damage Core ● in center!
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_N,  3.8,  60, b_type=4, sfx=-1), # Top N (radius 46 px)
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_NE, 4.2,  60, b_type=4, sfx=-1), # Top-Right NE (radius 50 px)
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_E,  3.8,  60, b_type=4, sfx=-1), # Right E (radius 46 px)
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_SE, 4.2,  60, b_type=4, sfx=-1), # Bot-Right SE (radius 50 px)
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_S,  3.8,  60, b_type=4, sfx=-1), # Bot S (radius 46 px)
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_SW, 4.2,  60, b_type=4, sfx=-1), # Bot-Left SW (radius 50 px)
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_W,  3.8,  60, b_type=4, sfx=-1), # Left W (radius 46 px)
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_NW, 4.2,  60, b_type=4, sfx=-1), # Top-Left NW (radius 50 px)
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_CR, 4.4,  60, b_type=4, sfx=-1), # Crown-Right (radius 53 px)
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, P_CL, 4.4,  60, b_type=4, sfx=-1), # Crown-Left (radius 53 px)
    ])

    # Pack unfocused streams and power table
    unfoc_streams_blob = bytearray()
    unfoc_pow_table_blob = bytearray()
    curr_unfoc_va = base_stream_unfoc_va

    for i in range(8):
        streams = unfoc_levels[i]
        num_s = len(streams)
        max_p = thresholds[i]
        unfoc_pow_table_blob += struct.pack('<IIQ', num_s, max_p, curr_unfoc_va)
        for s in streams:
            unfoc_streams_blob += s
        curr_unfoc_va += num_s * 36
    unfoc_pow_table_blob += struct.pack('<IIQ', 0, 99999, 0)

    # =========================================================================
    # 2. FOCUSED MODE: CONCENTRATED FORWARD FLOWER (Tight Forward Bloom, NO machine gun)
    # =========================================================================
    foc_levels = []

    # Level 0 (Power 0..7): Concentrated Seed ●
    foc_levels.append([
        make_stream(BLOOM_PER, 0, 0.0, -12.0, 12.0, 12.0, F_C, 0.0, 100, b_type=5, sfx=0)
    ])
    # Level 1 (Power 8..15): 3-Petal Concentrated Lance
    foc_levels.append([
        make_stream(BLOOM_PER, 0,  0.0, -12.0, 12.0, 12.0, F_C,  0.0, 140, b_type=5, sfx=0), # Center Lance ●
        make_stream(BLOOM_PER, 0, -1.0, -12.0, 12.0, 12.0, F_IL, 1.0,  55, b_type=5, sfx=-1), # Inner Left
        make_stream(BLOOM_PER, 0,  1.0, -12.0, 12.0, 12.0, F_IR, 1.0,  55, b_type=5, sfx=-1), # Inner Right
    ])
    # Level 2 (Power 16..31): 5-Petal Concentrated Lance
    foc_levels.append([
        make_stream(BLOOM_PER, 0,  0.0, -12.0, 12.0, 12.0, F_C,  0.0, 180, b_type=5, sfx=0), # Center Lance ●
        make_stream(BLOOM_PER, 0, -1.0, -12.0, 12.0, 12.0, F_IL, 1.0,  58, b_type=5, sfx=-1), # Inner Left
        make_stream(BLOOM_PER, 0,  1.0, -12.0, 12.0, 12.0, F_IR, 1.0,  58, b_type=5, sfx=-1), # Inner Right
        make_stream(BLOOM_PER, 0, -2.0, -12.0, 12.0, 12.0, F_OL, 1.5,  58, b_type=5, sfx=-1), # Outer Left
        make_stream(BLOOM_PER, 0,  2.0, -12.0, 12.0, 12.0, F_OR, 1.5,  58, b_type=5, sfx=-1), # Outer Right
    ])
    # Level 3 (Power 32..47): 7-Petal Heavy Spear
    foc_levels.append([
        make_stream(BLOOM_PER, 0,  0.0, -12.0, 12.0, 12.0, F_C,   0.0, 220, b_type=5, sfx=0), # Center Lance ●
        make_stream(BLOOM_PER, 0, -1.0, -12.0, 12.0, 12.0, F_IL,  1.0,  62, b_type=5, sfx=-1), # Inner Left
        make_stream(BLOOM_PER, 0,  1.0, -12.0, 12.0, 12.0, F_IR,  1.0,  62, b_type=5, sfx=-1), # Inner Right
        make_stream(BLOOM_PER, 0, -2.0, -12.0, 12.0, 12.0, F_OL,  1.5,  62, b_type=5, sfx=-1), # Outer Left
        make_stream(BLOOM_PER, 0,  2.0, -12.0, 12.0, 12.0, F_OR,  1.5,  62, b_type=5, sfx=-1), # Outer Right
        make_stream(BLOOM_PER, 0,  0.0, -14.0, 12.0, 12.0, F_C,   2.2,  62, b_type=5, sfx=-1), # Front Needle
        make_stream(BLOOM_PER, 0,  0.0, -10.0, 12.0, 12.0, -F_C,  1.2,  62, b_type=5, sfx=-1), # Rear Needle
    ])
    # Level 4 (Power 48..63): 7-Petal Reinforced Spear
    foc_levels.append([
        make_stream(BLOOM_PER, 0,  0.0, -12.0, 12.0, 12.0, F_C,   0.0, 260, b_type=5, sfx=0), # Center Lance ●
        make_stream(BLOOM_PER, 0, -1.0, -12.0, 12.0, 12.0, F_IL,  1.0,  68, b_type=5, sfx=-1), # Inner Left
        make_stream(BLOOM_PER, 0,  1.0, -12.0, 12.0, 12.0, F_IR,  1.0,  68, b_type=5, sfx=-1), # Inner Right
        make_stream(BLOOM_PER, 0, -2.0, -12.0, 12.0, 12.0, F_OL,  1.5,  68, b_type=5, sfx=-1), # Outer Left
        make_stream(BLOOM_PER, 0,  2.0, -12.0, 12.0, 12.0, F_OR,  1.5,  68, b_type=5, sfx=-1), # Outer Right
        make_stream(BLOOM_PER, 0,  0.0, -14.0, 12.0, 12.0, F_C,   2.2,  68, b_type=5, sfx=-1), # Front Needle
        make_stream(BLOOM_PER, 0,  0.0, -10.0, 12.0, 12.0, -F_C,  1.2,  68, b_type=5, sfx=-1), # Rear Needle
    ])
    # Level 5 (Power 64..79): 9-Petal Concentrated Floral Spear
    foc_levels.append([
        make_stream(BLOOM_PER, 0,  0.0, -12.0, 12.0, 12.0, F_C,   0.0, 300, b_type=5, sfx=0), # Center Lance ●
        make_stream(BLOOM_PER, 0, -1.0, -12.0, 12.0, 12.0, F_IL,  1.0,  72, b_type=5, sfx=-1), # Inner Left
        make_stream(BLOOM_PER, 0,  1.0, -12.0, 12.0, 12.0, F_IR,  1.0,  72, b_type=5, sfx=-1), # Inner Right
        make_stream(BLOOM_PER, 0, -2.0, -12.0, 12.0, 12.0, F_OL,  1.5,  72, b_type=5, sfx=-1), # Outer Left
        make_stream(BLOOM_PER, 0,  2.0, -12.0, 12.0, 12.0, F_OR,  1.5,  72, b_type=5, sfx=-1), # Outer Right
        make_stream(BLOOM_PER, 0,  0.0, -14.0, 12.0, 12.0, F_C,   2.4,  72, b_type=5, sfx=-1), # Front Needle
        make_stream(BLOOM_PER, 0,  0.0, -10.0, 12.0, 12.0, -F_C,  1.4,  72, b_type=5, sfx=-1), # Rear Needle
        make_stream(BLOOM_PER, 0, -3.0, -12.0, 12.0, 12.0, F_CRL, 2.0,  72, b_type=5, sfx=-1), # Crown Left
        make_stream(BLOOM_PER, 0,  3.0, -12.0, 12.0, 12.0, F_CRR, 2.0,  72, b_type=5, sfx=-1), # Crown Right
    ])
    # Level 6 (Power 80..95): 9-Petal Grand Floral Lance
    foc_levels.append([
        make_stream(BLOOM_PER, 0,  0.0, -12.0, 12.0, 12.0, F_C,   0.0, 340, b_type=5, sfx=0), # Center Lance ●
        make_stream(BLOOM_PER, 0, -1.0, -12.0, 12.0, 12.0, F_IL,  1.0,  76, b_type=5, sfx=-1), # Inner Left
        make_stream(BLOOM_PER, 0,  1.0, -12.0, 12.0, 12.0, F_IR,  1.0,  76, b_type=5, sfx=-1), # Inner Right
        make_stream(BLOOM_PER, 0, -2.0, -12.0, 12.0, 12.0, F_OL,  1.5,  76, b_type=5, sfx=-1), # Outer Left
        make_stream(BLOOM_PER, 0,  2.0, -12.0, 12.0, 12.0, F_OR,  1.5,  76, b_type=5, sfx=-1), # Outer Right
        make_stream(BLOOM_PER, 0,  0.0, -14.0, 12.0, 12.0, F_C,   2.4,  76, b_type=5, sfx=-1), # Front Needle
        make_stream(BLOOM_PER, 0,  0.0, -10.0, 12.0, 12.0, -F_C,  1.4,  76, b_type=5, sfx=-1), # Rear Needle
        make_stream(BLOOM_PER, 0, -3.0, -12.0, 12.0, 12.0, F_CRL, 2.0,  76, b_type=5, sfx=-1), # Crown Left
        make_stream(BLOOM_PER, 0,  3.0, -12.0, 12.0, 12.0, F_CRR, 2.0,  76, b_type=5, sfx=-1), # Crown Right
    ])
    # Level 7 (Power 96..128 MAX): Celestial Concentrated Floral Lance (MAX Power)
    foc_levels.append([
        make_stream(BLOOM_PER, 0,  0.0, -12.0, 12.0, 12.0, F_C,   0.0, 380, b_type=5, sfx=0), # Celestial Core ● in center
        make_stream(BLOOM_PER, 0, -1.0, -12.0, 12.0, 12.0, F_IL,  1.0,  80, b_type=5, sfx=-1), # Inner Left
        make_stream(BLOOM_PER, 0,  1.0, -12.0, 12.0, 12.0, F_IR,  1.0,  80, b_type=5, sfx=-1), # Inner Right
        make_stream(BLOOM_PER, 0, -2.0, -12.0, 12.0, 12.0, F_OL,  1.5,  80, b_type=5, sfx=-1), # Outer Left
        make_stream(BLOOM_PER, 0,  2.0, -12.0, 12.0, 12.0, F_OR,  1.5,  80, b_type=5, sfx=-1), # Outer Right
        make_stream(BLOOM_PER, 0,  0.0, -14.0, 12.0, 12.0, F_C,   2.5,  80, b_type=5, sfx=-1), # Front Needle
        make_stream(BLOOM_PER, 0,  0.0, -10.0, 12.0, 12.0, -F_C,  1.4,  80, b_type=5, sfx=-1), # Rear Needle
        make_stream(BLOOM_PER, 0, -3.0, -12.0, 12.0, 12.0, F_CRL, 2.0,  80, b_type=5, sfx=-1), # Crown Left
        make_stream(BLOOM_PER, 0,  3.0, -12.0, 12.0, 12.0, F_CRR, 2.0,  80, b_type=5, sfx=-1), # Crown Right
    ])

    # Pack focused streams and power table
    foc_streams_blob = bytearray()
    foc_pow_table_blob = bytearray()
    curr_foc_va = base_stream_foc_va

    for i in range(8):
        streams = foc_levels[i]
        num_s = len(streams)
        max_p = thresholds[i]
        foc_pow_table_blob += struct.pack('<IIQ', num_s, max_p, curr_foc_va)
        for s in streams:
            foc_streams_blob += s
        curr_foc_va += num_s * 36
    foc_pow_table_blob += struct.pack('<IIQ', 0, 99999, 0)

    return unfoc_pow_table_blob, unfoc_streams_blob, foc_pow_table_blob, foc_streams_blob

def apply_ingame_patches():
    print(f"Reading base executable from {BACKUP_EXE}...")
    if not os.path.exists(BACKUP_EXE):
        raise FileNotFoundError(f"Checkpoint {BACKUP_EXE} not found! Run apply_portrait_patch.py first.")

    with open(BACKUP_EXE, 'rb') as f:
        exe = bytearray(f.read())

    # -------------------------------------------------------------
    # 1. Parse PE Headers
    # -------------------------------------------------------------
    pe_off = struct.unpack('<I', exe[0x3c:0x40])[0]
    opt_hdr_size = struct.unpack('<H', exe[pe_off + 20 : pe_off + 22])[0]
    sec_table_off = pe_off + 24 + opt_hdr_size
    num_secs = struct.unpack('<H', exe[pe_off + 6 : pe_off + 8])[0]

    sections = []
    for i in range(num_secs):
        s = sec_table_off + i * 40
        name = exe[s:s+8].rstrip(bytes([0])).decode('latin1')
        vsize, rva, rsize, rptr, flags = struct.unpack('<IIIII', exe[s+8:s+28])
        sections.append({'name': name, 'rva': rva, 'vsize': vsize, 'rsize': rsize, 'rptr': rptr, 'hdr': s})

    def va_to_raw(va):
        rva = va - 0x140000000
        for sec in sections:
            if sec['rva'] <= rva < sec['rva'] + max(sec['vsize'], sec['rsize']):
                return rva - sec['rva'] + sec['rptr']
        raise ValueError(f"Invalid VA: {hex(va)}")

    def patch(va, code, desc=""):
        raw = va_to_raw(va)
        exe[raw : raw + len(code)] = code
        print(f"  [+] Patched {len(code):>2} bytes at VA {hex(va)}: {desc}")

    # -------------------------------------------------------------
    # 2. Add .rin PE Section (64KB at RVA 0xc6b000, VA 0x140c6b000)
    # -------------------------------------------------------------
    SEC_RIN_NAME = b'.rin\x00\x00\x00\x00'
    SEC_RIN_RVA = 0xc6b000
    SEC_RIN_SIZE = 0x10000 # 64 KB
    SEC_RIN_VA = 0x140000000 + SEC_RIN_RVA
    SEC_RIN_RPTR = len(exe)

    has_rin_sec = any(s['name'] == '.rin' for s in sections)
    if not has_rin_sec:
        exe[pe_off + 6 : pe_off + 8] = struct.pack('<H', num_secs + 1)
        sec_flags = 0xe0000060  # EXECUTE | READ | WRITE | CODE | INITIALIZED_DATA
        sec_hdr = struct.pack(
            '<8sIIIIIIHHI',
            SEC_RIN_NAME,
            SEC_RIN_SIZE,
            SEC_RIN_RVA,
            SEC_RIN_SIZE,
            SEC_RIN_RPTR,
            0, 0, 0, 0,
            sec_flags
        )
        
        new_sec_offset = sec_table_off + num_secs * 40
        exe[new_sec_offset : new_sec_offset + 40] = sec_hdr
        exe[pe_off + 24 + 56 : pe_off + 24 + 60] = struct.pack('<I', SEC_RIN_RVA + SEC_RIN_SIZE)
        exe.extend(bytes(SEC_RIN_SIZE))
        
        sections.append({
            'name': '.rin',
            'rva': SEC_RIN_RVA,
            'vsize': SEC_RIN_SIZE,
            'rsize': SEC_RIN_SIZE,
            'rptr': SEC_RIN_RPTR,
            'hdr': new_sec_offset
        })
        print(f"  [+] Added .rin section: RVA {hex(SEC_RIN_RVA)}, VA {hex(SEC_RIN_VA)}, Size {hex(SEC_RIN_SIZE)}")

    # -------------------------------------------------------------
    # 3. Step A: Preserve char_id == 2 (Fix Esc+R respawn bug)
    # -------------------------------------------------------------
    # At 0x1402bfad6, replace:
    #   mov byte ptr [rip + 0x2323a3], 0   (c6 05 a3 23 23 00 00)
    # with 7 NOPs so char_id remains 2!
    patch(0x1402bfad6, bytes([0x90] * 7), "Keep char_id == 2 (NOP out Reimu override on restart)")

    # -------------------------------------------------------------
    # 4. Step B: Define Memory Map within .rin Section
    # -------------------------------------------------------------
    VA_RIN_SPEED_TABLE           = SEC_RIN_VA + 0x0000
    VA_RIN_OPT_ANIM_TABLE        = SEC_RIN_VA + 0x0100
    VA_RIN_BOMB_OPT_DISPATCH     = SEC_RIN_VA + 0x0180
    VA_RIN_CLAMP_HOOK            = SEC_RIN_VA + 0x0200
    VA_RIN_WIND_SHOT_STUB        = SEC_RIN_VA + 0x0280
    VA_RIN_FLOWER_UNFOC_STUB     = SEC_RIN_VA + 0x0300
    VA_RIN_FLOWER_FOC_STUB       = SEC_RIN_VA + 0x0340
    VA_RIN_FLOWER_UNFOC_POW_TBL  = SEC_RIN_VA + 0x0400
    VA_RIN_FLOWER_FOC_POW_TBL    = SEC_RIN_VA + 0x0500
    VA_RIN_FLOWER_UNFOC_STREAMS  = SEC_RIN_VA + 0x0600
    VA_RIN_FLOWER_FOC_STREAMS    = SEC_RIN_VA + 0x1000
    VA_RIN_BLOOM_HOOK            = SEC_RIN_VA + 0x2000
    VA_RIN_LOG_BUFFER            = SEC_RIN_VA + 0x8000

    # -------------------------------------------------------------
    # 5. Step C: Generate Flower Bloom Danmaku Tables & Stubs
    # -------------------------------------------------------------
    unfoc_pow, unfoc_str, foc_pow, foc_str = build_flower_bloom_data(
        base_stream_unfoc_va=VA_RIN_FLOWER_UNFOC_STREAMS,
        base_stream_foc_va=VA_RIN_FLOWER_FOC_STREAMS
    )

    patch(VA_RIN_FLOWER_UNFOC_POW_TBL, unfoc_pow, "Flower Bloom Unfocused Power Table (8 levels)")
    patch(VA_RIN_FLOWER_UNFOC_STREAMS, unfoc_str, f"Flower Bloom Unfocused Stream Definitions ({len(unfoc_str)} bytes)")

    patch(VA_RIN_FLOWER_FOC_POW_TBL, foc_pow, "Flower Bloom Focused Power Table (8 levels)")
    patch(VA_RIN_FLOWER_FOC_STREAMS, foc_str, f"Flower Bloom Focused Stream Definitions ({len(foc_str)} bytes)")

    # Shot Function Calling Stubs
    # Reimu A shot table VA is 0x1403dcdb0 (used as safe placeholder for Rin A Wind Sign)
    stub_wind = build_shot_stub(VA_RIN_WIND_SHOT_STUB, 0x1403dcdb0)
    stub_flower_unfoc = build_shot_stub(VA_RIN_FLOWER_UNFOC_STUB, VA_RIN_FLOWER_UNFOC_POW_TBL)
    stub_flower_foc = build_shot_stub(VA_RIN_FLOWER_FOC_STUB, VA_RIN_FLOWER_FOC_POW_TBL)

    patch(VA_RIN_WIND_SHOT_STUB, stub_wind, "Rin Wind Sign Shot Stub")
    patch(VA_RIN_FLOWER_UNFOC_STUB, stub_flower_unfoc, "Rin Flower Sign Unfocused Shot Stub")
    patch(VA_RIN_FLOWER_FOC_STUB, stub_flower_foc, "Rin Flower Sign Focused Shot Stub")

    # -------------------------------------------------------------
    # 6. Step D: Expanded 6-Entry Player Speed & Shot Function Table
    # -------------------------------------------------------------
    # Table layout: 32 bytes per character/shot combination
    #   [0..15] : 4 floats: NormalSpeed, FocusSpeed, DiagNormalSpeed, DiagFocusSpeed
    #   [16..23]: uint64_t UnfocusShotFunctionPointer
    #   [24..31]: uint64_t FocusShotFunctionPointer
    orig_speed_entries = [
        # Reimu A (0):
        (4.0, 2.0, 4.0, 2.0, 0x14006a840, 0x14006a840),
        # Reimu B (1):
        (4.0, 2.0, 4.0, 2.0, 0x14006a860, 0x14006a860),
        # Marisa A (2):
        (5.0, 2.5, 5.0, 2.5, 0x14006a880, 0x14006a880),
        # Marisa B (3):
        (5.0, 2.5, 5.0, 2.5, 0x14006a8a0, 0x14006a8a0),
        # Rin A - Wind (4):
        (4.4, 2.1, 4.4, 2.1, VA_RIN_WIND_SHOT_STUB, VA_RIN_WIND_SHOT_STUB),
        # Rin B - Flower (5):
        (4.2, 2.0, 4.2, 2.0, VA_RIN_FLOWER_UNFOC_STUB, VA_RIN_FLOWER_FOC_STUB),
    ]

    rin_speed_table = bytearray()
    for norm, foc, dnorm, dfoc, unfoc_fn, foc_fn in orig_speed_entries:
        rin_speed_table += struct.pack('<ffffQQ', norm, foc, dnorm, dfoc, unfoc_fn, foc_fn)

    patch(VA_RIN_SPEED_TABLE, rin_speed_table, "Expanded 6-entry player speed & shot function table")

    # In Player::Init (0x140068350):
    # Redirect speed and shot loads from original table to .rin
    disp_xmm0 = SEC_RIN_RVA
    disp_xmm1 = SEC_RIN_RVA + 0x10
    patch(0x14006835e + 4, struct.pack('<I', disp_xmm0), "Redirect xmm0 speed load to .rin")
    patch(0x14006836d + 4, struct.pack('<I', disp_xmm1), "Redirect xmm1 shot function load to .rin")

    # -------------------------------------------------------------
    # 7. Step E: Expanded 6-Entry Option Animation String Table
    # -------------------------------------------------------------
    rin_opt_table = struct.pack(
        '<QQQQQQ',
        0x14030b3b8, # ReimuA
        0x14030b3b0, # ReimuB
        0x14030b3c8, # MarisaA
        0x14030b3c0, # MarisaB
        0x14030b3b0, # Rin A (reuses ReimuB option animation structure)
        0x14030b3b0  # Rin B (reuses ReimuB option animation structure)
    )
    patch(VA_RIN_OPT_ANIM_TABLE, rin_opt_table, "Expanded 6-entry option animation table")

    disp_opt_rva = SEC_RIN_RVA + 0x100
    patch(0x14007360e + 4, struct.pack('<I', disp_opt_rva), "Redirect option anim table lookup 1 to .rin")
    patch(0x140073644 + 4, struct.pack('<I', disp_opt_rva), "Redirect option anim table lookup 2 to .rin")

    # -------------------------------------------------------------
    # 8. Step F: Expanded 6-Entry Bomb & Option Dispatch Table
    # -------------------------------------------------------------
    # Table layout: 16 bytes per entry:
    #   [0..7] : uint64_t BombFunction
    #   [8..15]: uint64_t OptionUpdateFunction
    rin_bomb_opt_table = struct.pack(
        '<QQQQQQQQQQQQ',
        0x14000d1a0, 0x14000d9c0, # Reimu A
        0x14000db50, 0x14000e130, # Reimu B
        0x14000e270, 0x14000e650, # Marisa A
        0x14000e7f0, 0x14000ebe0, # Marisa B
        0x14000d1a0, 0x14000d9c0, # Rin A (Wind Sign bomb fallback)
        0x14000db50, 0x14000e130  # Rin B (Flower Sign bomb fallback)
    )
    patch(VA_RIN_BOMB_OPT_DISPATCH, rin_bomb_opt_table, "Expanded 6-entry bomb/option dispatch table")

    # In Player::Init (0x140068755 and 0x140068778):
    #   0x140068755: mov rax, [rbp + rax*8 + 0x3dc260] -> bomb function
    #   0x140068778: mov rax, [rbp + rax*8 + 0x3dc268] -> option function
    disp_bomb_rva = SEC_RIN_RVA + 0x180
    disp_opt_fn_rva = SEC_RIN_RVA + 0x188
    patch(0x140068755 + 4, struct.pack('<I', disp_bomb_rva), "Redirect bomb dispatch table to .rin")
    patch(0x140068778 + 4, struct.pack('<I', disp_opt_fn_rva), "Redirect option dispatch table to .rin")

    # -------------------------------------------------------------
    # 9. Step G: Safe Option Queue Array Bounds Clamp
    # -------------------------------------------------------------
    disp_to_rin = VA_RIN_CLAMP_HOOK - (0x140070c54 + 5)
    hook_jmp_rin = b'\xe9' + struct.pack('<i', disp_to_rin)
    patch(0x140070c54, hook_jmp_rin, "Hook 0x140070c54 to clamp option queue slot in .rin")

    rin_clamp_code = bytearray()
    rin_clamp_code += bytes.fromhex('8d 04 48')       # lea eax, [rax + rcx*2]
    rin_clamp_code += bytes.fromhex('83 f8 04')       # cmp eax, 4
    rin_clamp_code += bytes.fromhex('72 05')          # jb +5
    rin_clamp_code += bytes.fromhex('b8 01 00 00 00') # mov eax, 1 (fallback slot)
    rin_clamp_code += bytes.fromhex('48 98')          # cdqe
    disp_back = 0x140070c59 - (VA_RIN_CLAMP_HOOK + len(rin_clamp_code) + 5)
    rin_clamp_code += b'\xe9' + struct.pack('<i', disp_back)
    patch(VA_RIN_CLAMP_HOOK, rin_clamp_code, "Clamp option queue slot routine in .rin")

    # -------------------------------------------------------------
    # 10. Step H: Rin Delayed Mid-Air Bloom & Telemetry Hook
    # -------------------------------------------------------------
    bloom_hook_code = build_bloom_hook(VA_RIN_BLOOM_HOOK, VA_RIN_LOG_BUFFER)
    patch(VA_RIN_BLOOM_HOOK, bloom_hook_code, f"Rin Delayed Mid-Air Bloom Hook ({len(bloom_hook_code)} bytes)")

    # Hook linear bullet update at 0x140069a36:
    # Original: movss xmm2, [rbx + 8] (5 bytes: f3 0f 10 53 08)
    disp_to_bloom = VA_RIN_BLOOM_HOOK - (0x140069a36 + 5)
    hook_jmp_bloom = b'\xe9' + struct.pack('<i', disp_to_bloom)
    patch(0x140069a36, hook_jmp_bloom, "Hook 0x140069a36 in Player::UpdateBullets -> VA_RIN_BLOOM_HOOK")

    # Initialize log buffer in .rin (32KB zeroed)
    patch(VA_RIN_LOG_BUFFER, bytes(1024), "Initialize bullet telemetry buffer in .rin")

    # -------------------------------------------------------------
    # 11. Save Patched Executable
    # -------------------------------------------------------------
    with open(TARGET_EXE, 'wb') as f:
        f.write(exe)
    print(f"Successfully saved in-game gameplay patches to {TARGET_EXE}!")

    # Synchronize root executable
    with open('th06nc.exe', 'wb') as f:
        f.write(exe)
    print("Synchronized root th06nc.exe.")

if __name__ == '__main__':
    apply_ingame_patches()
