#!/usr/bin/env python3
"""
trace_bullets.py
Tracer and Telemetry Analysis Tool for Rin Satsuki's "Flower Bloom" Danmaku (Type B).

Mechanics Modeled:
1. Modo Unfocused (Sin Focus / Sin Shift) - "Flower Bloom" (Cadencia Período 18 frames):
   - Fase 1: Ascenso de la Semilla (| [RIN], Vy = -14.0, Vx = 0.0, age < 12).
             Sube compacto hasta la zona media-superior (Y ~210).
   - Fase 2: Desaceleración suave (Vy *= 0.60, 12 <= age < 18) hasta Y ~190.
   - Fase 3: ¡APERTURA FLORAL EXPANSIVA! (18 <= age < 30, 12 frames):
             * Núcleo ● (speed = 0.0): ¡SE QUEDA QUIETO EXACTAMENTE EN EL CENTRO!
             * 10 Pétalos (speed = 3.8..4.4): Se abren suavemente hasta un radio de 46..53 px (diámetro ~100 px).
             * ¡La flor es grande, visible, magnífica y claramente abierta!
   - Fase 4: Flor Flotando Estática en el Lugar (30 <= age < 65, 35 frames = ~0.6 segundos!):
             LA FLOR ENTERA SE QUEDA FLOTANDO EN EL LUGAR (Vy = -0.3 px/f), cubriendo una zona de 100 px
             y triturando continuamente a cualquier enemigo dentro de su radio.
   - Fase 5: Disipación Limpia (age >= 65, mov word ptr [rbx + 0x10], 0 -> desvanece de la pantalla).
   - Daño Masivo de Ráfaga: > 880 daño por flor (Núcleo 280 + 10 pétalos a 60 c/u).

2. Modo Focused (Con Focus / Con Shift) - Lanza Floral Penetrante (Cadencia Período 18 frames, ¡CERO ametralladora!):
   - Fase 1: Ascenso Rápido Directo (Vy = -18.0, Vx = 0.0, age < 10).
   - Fase 2: Frenado sobre el Hitbox del Boss (Vy *= 0.50, 10 <= age < 15).
   - Fase 3: Apertura en Diamante Floral Concentrado (15 <= age < 23, ancho X in [-12, +12] px).
   - Fase 4: Perforación Estática Continua (23 <= age < 55, 32 frames, Vx = 0.0, Vy = -0.4).
   - Fase 5: Disipación Limpia (age >= 55, mov word ptr [rbx + 0x10], 0).
   - Daño Concentrado Masivo: > 1020 daño por flor directa al boss (Núcleo 380 + 8 agujas a 80 c/u).

Modos de Ejecución:
  python3 trace_bullets.py          -> Genera simulación completa y reporte en bullet_trace.log.
  python3 trace_bullets.py --watch  -> Monitorea el juego en vivo y guarda cada bala disparada en tiempo real.
"""

import math
import struct
import os
import sys
import time
import glob
import json

UP_RAD = -math.pi / 2.0  # -90 degrees

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

# Angles for focused concentrated forward flower
F_C   = UP_RAD                      # Center Lance (-90 deg)
F_IL  = -96.0 * math.pi / 180.0     # Inner Left (-96 deg)
F_IR  = -84.0 * math.pi / 180.0     # Inner Right (-84 deg)
F_OL  = -102.0 * math.pi / 180.0    # Outer Left (-102 deg)
F_OR  = -78.0 * math.pi / 180.0     # Outer Right (-78 deg)
F_CRL = -108.0 * math.pi / 180.0    # Crown Left (-108 deg)
F_CRR = -72.0 * math.pi / 180.0     # Crown Right (-72 deg)

UNFOC_LEVEL7_STREAMS = [
    ('● Celestial Core', 0.0, -12.0, P_N,  0.0, 280), # speed = 0.0 (stays in center!)
    ('O Pétalo Norte',   0.0, -12.0, P_N,  3.8,  60), # speed = 3.8
    ('o Pétalo NorEste', 0.0, -12.0, P_NE, 4.2,  60), # speed = 4.2
    ('O Pétalo Este',    0.0, -12.0, P_E,  3.8,  60), # speed = 3.8
    ('o Pétalo SurEste', 0.0, -12.0, P_SE, 4.2,  60), # speed = 4.2
    ('O Pétalo Sur',     0.0, -12.0, P_S,  3.8,  60), # speed = 3.8
    ('o Pétalo SurOeste',0.0, -12.0, P_SW, 4.2,  60), # speed = 4.2
    ('O Pétalo Oeste',   0.0, -12.0, P_W,  3.8,  60), # speed = 3.8
    ('o Pétalo NorOeste',0.0, -12.0, P_NW, 4.2,  60), # speed = 4.2
    ('o Corona-Der',     0.0, -12.0, P_CR, 4.4,  60), # speed = 4.4
    ('o Corona-Izq',     0.0, -12.0, P_CL, 4.4,  60), # speed = 4.4
]

FOC_LEVEL7_STREAMS = [
    ('● Celestial Core', 0.0, -12.0, F_C,   0.0, 380),
    ('| Aguja Int-Izq', -1.0, -12.0, F_IL,  1.0,  80),
    ('| Aguja Int-Der',  1.0, -12.0, F_IR,  1.0,  80),
    ('| Aguja Ext-Izq', -2.0, -12.0, F_OL,  1.5,  80),
    ('| Aguja Ext-Der',  2.0, -12.0, F_OR,  1.5,  80),
    ('^ Aguja Frontal',  0.0, -14.0, F_C,   2.5,  80),
    ('v Aguja Trasera',  0.0, -10.0, -F_C,  1.4,  80),
    ('/ Aguja Corona-I',-3.0, -12.0, F_CRL, 2.0,  80),
    ('\\ Aguja Corona-D', 3.0, -12.0, F_CRR, 2.0,  80),
]

CACHE_FILE = 'bullet_telemetry_cache.json'

def simulate_unfocused_flower(streams, max_frames=68):
    """
    Simula la física exacta de VA_RIN_BLOOM_HOOK (Modo Unfocused, b_type == 4):
      - Frames 0..11:  Ascenso vertical compacto (Vx = 0.0, Vy = -14.0) hasta Y ~210
      - Frames 12..17: Desaceleración suave (Vy *= 0.60) hasta Y ~190
      - Frames 18..29: Apertura visible a flor completa (Vx = cos(ang)*spd, Vy = sin(ang)*spd, radio 46..53 px)
      - Frames 30..64: Flor flotando estática en el lugar (Vx = 0.0, Vy = -0.3 px/f, ~0.6 segundos)
      - Frame 65+:     Disipación (bullet active = 0)
    """
    history = []
    states = {}
    for i, (name, ox, oy, ang, spd, dmg) in enumerate(streams):
        states[i] = {'x': ox, 'y': oy, 'vx': 0.0, 'vy': -14.0, 'ang': ang, 'spd': spd, 'dmg': dmg, 'name': name, 'active': True}
        
    for f in range(max_frames + 1):
        frame_snapshot = {'frame': f, 'bullets': []}
        core_x, core_y = 0.0, 0.0
        
        for i in range(len(streams)):
            st = states[i]
            if f < 12:
                st['vx'] = 0.0
                st['vy'] = -14.0
                st['phase'] = 'ASCENT'
            elif 12 <= f < 18:
                st['vx'] = 0.0
                st['vy'] *= 0.60
                st['phase'] = 'DECEL'
            elif 18 <= f < 30:
                if f == 18:
                    st['vx'] = math.cos(st['ang']) * st['spd']
                    st['vy'] = math.sin(st['ang']) * st['spd']
                st['phase'] = 'BLOOM'
            elif 30 <= f < 65:
                st['vx'] = 0.0
                st['vy'] = -0.3
                st['phase'] = 'HOVER'
            else:
                st['active'] = False
                st['phase'] = 'DISSIP'
                st['vx'] = 0.0
                st['vy'] = 0.0
                
            if 'Celestial' in st['name'] or 'Core' in st['name'] or 'Núcleo' in st['name']:
                core_x = st['x']
                core_y = st['y']

        for i in range(len(streams)):
            st = states[i]
            dx = st['x'] - core_x
            dy = st['y'] - core_y
            frame_snapshot['bullets'].append({
                'id': i,
                'name': st['name'],
                'phase': st['phase'],
                'x': st['x'],
                'y': st['y'],
                'vx': st['vx'],
                'vy': st['vy'],
                'dx': dx,
                'dy': dy,
                'dmg': st['dmg'],
                'active': st['active']
            })
            
        history.append(frame_snapshot)
        
        for i in range(len(streams)):
            if states[i]['active']:
                states[i]['x'] += states[i]['vx']
                states[i]['y'] += states[i]['vy']
            
    return history

def simulate_focused_flower(streams, max_frames=58):
    """
    Simula la física exacta de VA_RIN_BLOOM_HOOK (Modo Focused, b_type == 5):
      - Frames 0..9:   Ascenso rápido hacia el boss (Vx = 0.0, Vy = -18.0)
      - Frames 10..14: Frenado sobre el boss (Vy *= 0.50)
      - Frames 15..22: Apertura en diamante floral (Vx = cos(ang)*spd, Vy = sin(ang)*spd)
      - Frames 23..54: Perforación estática sobre el centro del boss (Vx = 0.0, Vy = -0.4 px/f)
      - Frame 55+:     Disipación
    """
    history = []
    states = {}
    for i, (name, ox, oy, ang, spd, dmg) in enumerate(streams):
        states[i] = {'x': ox, 'y': oy, 'vx': 0.0, 'vy': -18.0, 'ang': ang, 'spd': spd, 'dmg': dmg, 'name': name, 'active': True}
        
    for f in range(max_frames + 1):
        frame_snapshot = {'frame': f, 'bullets': []}
        core_x, core_y = 0.0, 0.0
        
        for i in range(len(streams)):
            st = states[i]
            if f < 10:
                st['vx'] = 0.0
                st['vy'] = -18.0
                st['phase'] = 'FAST-ASC'
            elif 10 <= f < 15:
                st['vx'] = 0.0
                st['vy'] *= 0.50
                st['phase'] = 'DECEL'
            elif 15 <= f < 23:
                if f == 15:
                    st['vx'] = math.cos(st['ang']) * st['spd']
                    st['vy'] = math.sin(st['ang']) * st['spd']
                st['phase'] = 'LANCE'
            elif 23 <= f < 55:
                st['vx'] = 0.0
                st['vy'] = -0.4
                st['phase'] = 'DRILL'
            else:
                st['active'] = False
                st['phase'] = 'DISSIP'
                st['vx'] = 0.0
                st['vy'] = 0.0
                
            if 'Celestial' in st['name']:
                core_x = st['x']
                core_y = st['y']

        for i in range(len(streams)):
            st = states[i]
            dx = st['x'] - core_x
            dy = st['y'] - core_y
            frame_snapshot['bullets'].append({
                'id': i,
                'name': st['name'],
                'phase': st['phase'],
                'x': st['x'],
                'y': st['y'],
                'vx': st['vx'],
                'vy': st['vy'],
                'dx': dx,
                'dy': dy,
                'dmg': st['dmg'],
                'active': st['active']
            })
            
        history.append(frame_snapshot)
        
        for i in range(len(streams)):
            if states[i]['active']:
                states[i]['x'] += states[i]['vx']
                states[i]['y'] += states[i]['vy']
            
    return history

def render_ascii_flower(snapshot, width=53, height=23, scale_x=0.45, scale_y=0.22):
    """Genera corte espacial 2D en texto de la flor Danmaku."""
    grid = [[' ' for _ in range(width)] for _ in range(height)]
    cx = width // 2
    cy = height // 2
    
    for b in snapshot['bullets']:
        if not b['active']:
            continue
        gx = int(cx + b['dx'] * scale_x)
        gy = int(cy + b['dy'] * scale_y)
        sym = b['name'][0]
        if 0 <= gx < width and 0 <= gy < height:
            grid[gy][gx] = sym
            
    lines = [''.join(row) for row in grid]
    return '\n'.join(lines)

def generate_report():
    lines = []
    lines.append("=" * 88)
    lines.append("  TOUHOU 6: RIN SATSUKI MOD - DANMAKU TRAJECTORY & TELEMETRY REPORT")
    lines.append("  Disparo: Flower Sign (Tipo B) 'Flower Bloom' (Semilla -> Gran Flor Visible -> Disipación)")
    lines.append("=" * 88)
    lines.append("")
    lines.append("1. MEJORAS VISUALES Y FÍSICAS IMPLEMENTADAS:")
    lines.append("   * MODO UNFOCUSED (Sin Shift) - FLOR EXPANSIVA VISIBLE (Diámetro ~100 px):")
    lines.append("     - Altitud de Floración Óptima: La semilla asciende 12 frames hasta Y ~210 (centro de pantalla,")
    lines.append("       en vez de viajar al borde superior Y ~100 donde no se veía).")
    lines.append("     - Núcleo Central Inmóvil (speed = 0.0): El núcleo ● se queda FIJO EN EL CENTRO EXACTO,")
    lines.append("       sirviendo como el estambre brillante de la flor.")
    lines.append("     - Pétalos Expansivos (speed = 3.8..4.4): Durante 12 frames (frames 18..29), los 10 pétalos")
    lines.append("       se abren visiblemente hacia afuera hasta un radio de 46..53 px (¡tres veces más grande!")
    lines.append("     - Flor Flotando Estática (frames 30..64, 35 frames = ~0.60 segundos): La flor completa se queda")
    lines.append("       quieta en el aire flotando visiblemente, permitiendo ver su dibujo floral mientras daña.")
    lines.append("     - Disipación Limpia (frame 65): Se desvanece de inmediato en pantalla.")
    lines.append("     - Daño Total: 880 de daño de ráfaga por flor (Núcleo 280 + 10 pétalos a 60 c/u).")
    lines.append("")
    lines.append("   * MODO FOCUSED (Con Shift) - LANZA FLORAL PENETRANTE (¡CERO AMETRALLADORA!):")
    lines.append("     - Cadencia de 18 frames (~3.33 flores/segundo, IDÉNTICA a modo normal).")
    lines.append("     - Núcleo en el centro del boss con agujas perforantes concentradas en un diamante vertical.")
    lines.append("     - Daño Concentrado: 1020 de daño directo por flor (Núcleo 380 + 8 agujas a 80 c/u).")
    lines.append("")

    hist_unfoc = simulate_unfocused_flower(UNFOC_LEVEL7_STREAMS, max_frames=68)
    
    lines.append("-" * 88)
    lines.append("2. REGISTRO PASO A PASO - MODO UNFOCUSED (MAX Power 128 - Crisantemo Celestial):")
    lines.append("-" * 88)
    lines.append(f"{'Frm':>3} | {'Proyectil / Rol':<18} | {'Fase':<8} | {'X':>6} {'Y':>7} | {'Vx':>6} {'Vy':>6} | {'dX':>5} {'dY':>5} | {'Daño':>4}")
    lines.append("-" * 88)
    
    sample_frames_unfoc = [0, 8, 12, 17, 18, 24, 29, 30, 48, 64, 65]
    for snap in hist_unfoc:
        f = snap['frame']
        if f in sample_frames_unfoc:
            for b in snap['bullets']:
                act_str = f"{b['dmg']:4d}" if b['active'] else " DISS"
                lines.append(
                    f"{f:3d} | {b['name']:<18} | {b['phase']:<8} | "
                    f"{b['x']:6.1f} {b['y']:7.1f} | {b['vx']:6.1f} {b['vy']:6.1f} | "
                    f"{b['dx']:5.1f} {b['dy']:5.1f} | {act_str}"
                )
            lines.append("." * 88)

    lines.append("")
    lines.append("3. EVOLUCIÓN VISUAL ESPACIAL DE LA FLOR (ASCII DANMAKU SLICES):")
    lines.append("")
    
    milestones_unfoc = [
        (8,  "FASE 1: SEMILLA EN ASCENSO (Compacta vertical | [RIN])"),
        (16, "FASE 2: FRENADO EN ALTURA MEDIA (Desacelerando frente al objetivo)"),
        (24, "FASE 3A: APERTURA FLORAL EN PROGRESO (Pétalos abriéndose hacia afuera)"),
        (30, "FASE 3B: FLOR COMPLETAMENTE ABIERTA (Radio ~50 px, Núcleo en el centro)"),
        (48, "FASE 4: FLOR FLOTANDO ESTÁTICA EN EL LUGAR (Triturando al enemigo durante 35 frames)"),
        (65, "FASE 5: DISIPACIÓN LIMPIA (La flor se desvanece de la pantalla)"),
    ]
    
    for f, desc in milestones_unfoc:
        snap = hist_unfoc[f]
        lines.append(f"--- FRAME {f:2d} | {desc} ---")
        lines.append(render_ascii_flower(snap))
        lines.append("")

    hist_foc = simulate_focused_flower(FOC_LEVEL7_STREAMS, max_frames=58)
    
    lines.append("-" * 88)
    lines.append("4. REGISTRO PASO A PASO - MODO FOCUSED (LANZA FLORAL CONCENTRADA):")
    lines.append("-" * 88)
    sample_frames_foc = [0, 6, 10, 14, 15, 20, 23, 40, 54, 55]
    for snap in hist_foc:
        f = snap['frame']
        if f in sample_frames_foc:
            for b in snap['bullets']:
                act_str = f"{b['dmg']:4d}" if b['active'] else " DISS"
                lines.append(
                    f"{f:3d} | {b['name']:<18} | {b['phase']:<8} | "
                    f"{b['x']:6.1f} {b['y']:7.1f} | {b['vx']:6.1f} {b['vy']:6.1f} | "
                    f"{b['dx']:5.1f} {b['dy']:5.1f} | {act_str}"
                )
            lines.append("." * 88)

    lines.append("")
    lines.append("-" * 88)
    lines.append("5. REGISTRO DE DISPAROS REALES EN MEMORIA (VA_RIN_LOG_BUFFER):")
    lines.append("-" * 88)
    
    live_records, source_desc = get_telemetry_records()
    if live_records:
        lines.append(f"  [+] Fuente: {source_desc} | Total proyectiles registrados: {len(live_records)}")
        lines.append(f"  [+] Muestra de balas disparadas in-game por el usuario:")
        for idx, r in enumerate(live_records[:16]):
            lines.append(f"      Bal #{idx:2d}: age={r['age']:2d}, tipo={r['btype']}, Pos=({r['x']:6.1f}, {r['y']:6.1f}), Vel=({r['vx']:6.1f}, {r['vy']:6.1f})")
    else:
        lines.append("  [*] No se registraron disparos activos aún en memoria.")
        lines.append("  [*] Dispara con Z usando './run_game_with_logs.sh' para registrar tus disparos.")

    lines.append("")
    lines.append("=" * 88)
    lines.append("  FIN DEL REPORTE DE TELEMETRÍA")
    lines.append("=" * 88)
    
    return '\n'.join(lines)

def read_ram_telemetry():
    records = []
    for maps_path in glob.glob('/proc/[0-9]*/maps'):
        try:
            with open(maps_path, 'r') as f:
                content = f.read()
            if 'th06nc.exe' in content:
                pid = maps_path.split('/')[2]
                mem_path = f"/proc/{pid}/mem"
                if os.path.exists(mem_path):
                    with open(mem_path, 'rb', buffering=0) as m:
                        m.seek(0x140c73000)
                        buf_hdr = m.read(8)
                        if len(buf_hdr) == 8:
                            count = struct.unpack('<I', buf_hdr[:4])[0]
                            if 0 < count <= 512:
                                for _ in range(count):
                                    rec = m.read(32)
                                    age, btype, rx, ry, rvx, rvy, rang, rspd = struct.unpack('<IIffffff', rec)
                                    records.append({'age': age, 'btype': btype, 'x': rx, 'y': ry, 'vx': rvx, 'vy': rvy, 'ang': rang, 'spd': rspd})
                                return records
        except Exception:
            continue
    return records

def get_telemetry_records():
    ram = read_ram_telemetry()
    if ram:
        try:
            with open(CACHE_FILE, 'w') as f:
                json.dump(ram, f)
            with open(os.path.join('th06nc', CACHE_FILE), 'w') as f:
                json.dump(ram, f)
        except Exception:
            pass
        return ram, "Memoria RAM del proceso th06nc.exe activo"
    
    for cf in [CACHE_FILE, os.path.join('th06nc', CACHE_FILE)]:
        if os.path.exists(cf):
            try:
                with open(cf, 'r') as f:
                    cached = json.load(f)
                if cached:
                    return cached, "Caché persistido de la última sesión de juego"
            except Exception:
                pass
    return [], "Ninguna sesión registrada"

def watch_live():
    print("[*] Iniciando centinela de telemetría para th06nc.exe...")
    last_count = -1
    while True:
        recs = read_ram_telemetry()
        if recs and len(recs) != last_count:
            last_count = len(recs)
            try:
                with open(CACHE_FILE, 'w') as f:
                    json.dump(recs, f)
                with open(os.path.join('th06nc', CACHE_FILE), 'w') as f:
                    json.dump(recs, f)
            except Exception:
                pass
            rep = generate_report()
            with open('th06nc/bullet_trace.log', 'w', encoding='utf-8') as f:
                f.write(rep)
            with open('bullet_trace.log', 'w', encoding='utf-8') as f:
                f.write(rep)
            print(f"[+] ¡Capturadas {len(recs)} balas disparadas in-game! bullet_trace.log actualizado.")
        time.sleep(0.1)

if __name__ == '__main__':
    if '--watch' in sys.argv:
        watch_live()
    else:
        rep = generate_report()
        with open('th06nc/bullet_trace.log', 'w', encoding='utf-8') as f:
            f.write(rep)
        with open('bullet_trace.log', 'w', encoding='utf-8') as f:
            f.write(rep)
        print(rep)
