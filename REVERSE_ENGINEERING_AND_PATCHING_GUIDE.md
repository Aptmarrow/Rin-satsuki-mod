# Guía de Ingeniería Inversa, Parches y Arquitectura: Rin Satsuki Mod (Touhou 6: New Classic)

Documentación técnica detallada del mod de Rin Satsuki para **Touhou 6: New Classic (64-bit)**. Este documento describe la arquitectura interna del ejecutable, los puntos de enganche (hooks), las cuevas de código (code caves), la estructura de memoria, los formatos de textura y las **reglas críticas de bytes y registros que NUNCA deben romperse**.

---

## 1. Arquitectura del Ejecutable y Entorno

- **Binario:** `th06nc.exe` (PE32+ ejecutable nativo x86_64 para Windows).
- **Entorno de ejecución:** Linux vía Wine / Proton con traducción Direct3D 11 -> Vulkan (DXVK).
- **Gestión de gráficos y animación:** Motor ANM propietario (`anmMgr`), donde cada animación, menú y sprite es gestionado por una Máquina Virtual (VM) de animación.

---

## 2. Mapa de Memoria Global y Offsets Críticos

| Dirección Virtual (VA) | Símbolo / Variable | Tamaño / Tipo | Descripción |
|---|---|---|---|
| `0x1404f1e80` | `char_id` | `uint8_t` | ID del personaje seleccionado (`0` = Reimu, `1` = Marisa, `2` = Rin). |
| `0x1404f1e81` | `weapon_type` | `uint8_t` | Tipo de disparo / carta de arma (`0` = Tipo A, `1` = Tipo B). |
| `0x140a6e9b0` | `anmMgr` | `QWORD*` (ptr) | Puntero global al singleton `CAnmManager`. |
| `0x1400020b0` | `Anm_Load` | Función | Carga un archivo `.anm`. Convención x64 Microsoft. |
| `0x140002760` | `Anm_InitVM` | Función | Inicializa una estructura VM con un Script ID. |

### Argumentos de Funciones del Motor:
- **`Anm_Load(rcx, edx, r8, r9d)`**:
  - `rcx` = `*anmMgr` (puntero al gestor ANM desreferenciado desde `0x140a6e9b0`).
  - `edx` = `file_id` (identificador numérico del archivo, e.g. `0x2a` para `slpl02a`, `0x2b` para `slpl02b`, `0x2c` para `card02`).
  - `r8` = Puntero a cadena ASCII con la ruta (e.g. `"data/slpl02a.anm"`).
  - `r9d` = `base_sprite_id` (ID base asignado a los sprites del archivo).

- **`Anm_InitVM(rcx, rdx, r8d)`**:
  - `rcx` = `*anmMgr`.
  - `rdx` = Puntero a la estructura VM en memoria (`VM*`).
  - `r8d` = `script_id` a ejecutar en la VM.

---

## 3. Estructura del Menú de Selección de Personajes (`char_select_ctx`)

En las rutinas del menú de selección de personajes, el registro `rsi` apunta a la estructura de contexto del menú:

- **ID de personaje activo:** `[rsi + 0x28]` (byte / dword).
- **VM 0 (`rsi + 0x122e0`):** Ilustración izquierda de Reimu / Rin.
  - Script ID activo guardado en: `[rsi + 0x12318]`.
  - Flag de interrupción / transición guardado en: `[rsi + 0x1236a]`.
- **VM 1 (`rsi + 0x12400`):** Ilustración derecha de Reimu / Rin.
  - Flag de interrupción en: `[rsi + 0x1248a]`.
- **VM 2 (`rsi + 0x12520`):** Ilustración izquierda de Marisa (`[rsi + 0x125aa]`).
- **VM 3 (`rsi + 0x12640`):** Ilustración derecha de Marisa (`[rsi + 0x126ca]`).
- **VM 12 (`rsi + 0x13060`):** Carta de arma / tipo de disparo A.
  - Sprite ID: `[rsi + 0x13094]` (`offset +0x34`).
  - Sprite struct ptr: `[rsi + 0x13160]` (`offset +0x100`).
- **VM 13 (`rsi + 0x13180`):** Carta de arma / tipo de disparo B.
  - Sprite ID: `[rsi + 0x131b4]` (`offset +0x34`).
  - Sprite struct ptr: `[rsi + 0x13280]` (`offset +0x100`).
- **Estructura de sprites en `anmMgr`:** Cada sprite ocupa `64` bytes (`0x40`) a partir de `anmMgr + 0x608 + (sprite_id * 64)`.

---

## 4. Cuevas de Código (Code Caves) y Modificación de Cabeceras PE

Debido a que el código original no tenía suficiente espacio para añadir la lógica de Rin, se habilitaron dos zonas seguras:

### A. Cueva en Sección `.text` (`0x1402bfb80`)
- **Espacio utilizable:** Máximo **128 bytes** (rango `0x1402bfb80` a `0x1402bfc00`).
- **Uso:**
  1. `VA_LOADER` (`0x1402bfb80`): Rutina iterativa que carga los 3 ANMs (`slpl02a.anm`, `slpl02b.anm`, `card02.anm`) llamando a `Anm_Load`.
  2. `VA_HELPER1` (`GetWeaponVMOffset`): Cálculo de desplazamiento de VM de arma (`((char_id & 1) * 2 + weapon_type) * 0x120`).
  3. `VA_BLINK_HELPER`: Selector de VM para animación de parpadeo (`0x89` para Reimu y Rin, `0x8b` para Marisa).

### B. Cueva en Sección `.rdata` (`0x1403226ba`)
- **Modificación de cabecera PE:** La sección `.rdata` original era de solo lectura y terminaba antes del relleno en disco.
  - `VirtualSize` fue expandido a `0x63000`.
  - Flags de sección modificados a `0x60000020` (`IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_MEM_READ | IMAGE_SCN_CNT_CODE`).
- **Espacio utilizable:** **326 bytes** (rango `0x1403226ba` a `0x140322800`).
- **Uso:**
  1. Cadenas de texto ASCII: `"data/slpl02a.anm"`, `"data/slpl02b.anm"`, `"data/card02.anm"`.
  2. `InitVMPair`: Función auxiliar para inicializar pares de VMs (VM 0 y VM 1) preservando registros.
  3. `UpdatePortrait`: Lógica de intercambio de retratos entre Reimu (0), Marisa (1) y Rin (2).
  4. `TransitionToWeaponSelect`: Lógica de fijación de retratos e inyección directa de sprites de cartas de disparo (`card02`).

---

## 5. ¡ADVERTENCIAS CRÍTICAS! Bytes, Registros y Zonas que NO se deben tocar

### ⚠️ 1. Volatilidad de Registros en `Anm_InitVM` (`0x140002760`)
- En la línea `0x140002764`, `Anm_InitVM` ejecuta internamente:
  ```assembly
  mov r10, qword ptr [rip + 0xa6c245]  ; Carga anmMgr sobre r10 destructivamente
  ```
- **Consecuencia fatal:** `r10` queda totalmente destruido.
- **REGLA ABSOLUTA:** **NUNCA** utilices `r10` para almacenar parámetros, IDs de script ni punteros a través de llamadas a `Anm_InitVM`. Cualquier subrutina que llame a `Anm_InitVM` debe guardar sus variables en el stack o en registros no volátiles (`rbx`, `rbp`, `rdi`, `rsi`).

### ⚠️ 2. Formato de Texturas DirectDraw Surface (DDS): Byte Order BGRA
- Los archivos `.dds` sin compresión utilizados por el juego (`slpl02a.dds`, `slpl02b.dds`, `card02.dds`, `player02.dds`) tienen formato de píxel `D3DFMT_A8R8G8B8`:
  - `r_mask = 0x00ff0000` (Bits 16..23)
  - `g_mask = 0x0000ff00` (Bits 8..15)
  - `b_mask = 0x000000ff` (Bits 0..7)
  - `a_mask = 0xff000000` (Bits 24..31)
- En memoria de bytes (little-endian), los bytes en disco **DEBEN ESTAR EN ORDEN BGRA**:
  - Byte 0 = **Azul (Blue)**
  - Byte 1 = **Verde (Green)**
  - Byte 2 = **Rojo (Red)**
  - Byte 3 = **Alfa (Alpha)**
- **REGLA ABSOLUTA:** Si guardas bytes en orden RGBA directamente en el archivo `.dds`, el motor Direct3D intercambia el rojo y el azul, resultando en el **"filtro azul"** (vestido azul, pelo celeste). En Python/PIL siempre convertir con:
  ```python
  r, g, b, a = im.split()
  bgra = Image.merge('RGBA', (b, g, r, a)).tobytes()
  ```

### ⚠️ 3. Límites de las Cuevas de Código
- La cueva `.text` tiene un límite físico estricto de **128 bytes** (`0x1402bfb80` a `0x1402bfc00`). Exceder este tamaño sobrescribirá código de la siguiente función del juego (`0x1402bfc00`), causando un crash inmediato.
- El script [`apply_portrait_patch.py`](file:///home/RodriFumo/Descargas/Rin_Mod/apply_portrait_patch.py) tiene asserts automáticos que impiden compilar si se supera este límite.

### ⚠️ 4. Recarga del ID de Personaje tras Llamadas a Función
- `UpdatePortrait` utiliza internamente `rax`, `rdx`, `rcx`. Al retornar a `TransitionToWeaponSelect`, el registro `al` ya no contiene el ID de personaje.
- **REGLA:** Siempre recargar explícitamente el ID con `mov al, [rsi + 0x28]` antes de bifurcar condicionalmente.

---

## 6. Estado de Builds y Checkpoints

| Archivo | Estado / Rol | Descripción |
|---|---|---|
| `th06nc/th06nc.exe` | **ACTUAL / ACTIVO** | Build donde se continúa el desarrollo. Contiene el slot 3, portraits corregidos, selección de disparo y texturas BGRA. |
| `th06nc/th06nc.exe.checkpoint_charselect` | **CHECKPOINT SEGURO** | Copia de respaldo inmutable de la build actual funcional. |
| `th06nc_checkpoint_charselect.exe` | **CHECKPOINT RAÍZ** | Idéntica al checkpoint anterior en el directorio raíz. |
| `th06nc/th06nc_original.exe` | **ORIGINAL INTACTO** | Binario original de Touhou 6: New Classic sin ningún parche. |
| `th06nc/th06nc.exe.pre_slpl` | **BASE INTERMEDIA** | Binario con slot 3 habilitado, antes de inyectar las cuevas de retratos (insumo de `apply_portrait_patch.py`). |

---

## 7. Cómo Reproducir o Re-aplicar los Parches

Para reconstruir el ejecutable desde la base limpia en cualquier momento:

```bash
# Fase 1: Aplica todos los parches de selección de personaje (NO TOCAR ESTE SCRIPT)
python3 apply_portrait_patch.py

# Fase 2: Aplica todos los parches in-game, sección .rin y tipos de disparo
python3 apply_ingame_patch.py
```

---

## 8. Fase 2: Arquitectura del Motor de Disparo In-Game y Danmaku

A partir de la Fase 2, se incorporó una sección PE dedicada de 64 KB (`.rin`) con permisos RWX (`0xe0000060`) para albergar lógica personalizada, tablas expandidas y definiciones de proyectiles.

### A. Despacho de Disparo del Jugador (`Player::Shoot`)
En `th06nc.exe`, el disparo del jugador opera mediante una arquitectura desacoplada basada en tablas:

1. **Inicialización (`0x140068350`):**
   - El juego indexa por `(char_id * 2 + weapon_type)` en una tabla de 32 bytes por entrada.
   - Bytes 0..15: 4 floats (`NormalSpeed`, `FocusSpeed`, `DiagNormalSpeed`, `DiagFocusSpeed`).
   - Bytes 16..23: Puntero a función de disparo **Unfocused** (`UnfocusShotFunction`).
   - Bytes 24..31: Puntero a función de disparo **Focused** (`FocusShotFunction`).
   - Estos punteros se almacenan en `[rbx + 0x7720]` (Unfocus) y `[rbx + 0x7728]` (Focus).

2. **Bucle de Disparo (`0x140069be0` - `0x140069c64`):**
   - Itera por los 80 slots de balas del jugador (`0x50` slots de `0x170` bytes cada uno, empezando en `[rdi + 0x410]`).
   - Si Focus mode (`[rdi + 0x785c] != 0`), llama a `[rdi + 0x7728]`.
   - Si Unfocused mode (`[rdi + 0x785c] == 0`), llama a `[rdi + 0x7720]`.
   - La función llamada retorna `-1` si restan balas por instanciar en la ráfaga actual, continuando el bucle. Retorna `>= 0` al finalizar la ráfaga.

3. **Generador Genérico de Disparo Danmaku (`0x14006a580`):**
   - Lee el nivel de poder del jugador (`[rip + 0x4878e0]`, de `0` a `128`).
   - Busca en la tabla de poder de 8 niveles (`PowerTable`) el rango correspondiente.
   - Lee la definición del flujo de balas (`BulletStreamDef`) e inicializa el proyectil nativo en el slot del jugador:
     - $V_x = \text{speed} \times \cos(\text{angle})$
     - $V_y = \text{speed} \times \sin(\text{angle})$
     - Daño nativo, hitbox y sprite asignados automáticamente.

### B. Estructura de Datos Nativa de Proyectil (`BulletStreamDef`, 36 bytes / `0x24`)
```c
struct BulletStreamDef {
    int16_t  period;       // Período de disparo en frames (e.g. 5)
    int16_t  phase;        // Fase de disparo (frame % period == phase)
    float    off_x;        // Offset X de salida respecto al jugador/opción
    float    off_y;        // Offset Y de salida respecto al jugador/opción
    float    hitbox_w;     // Ancho de la hitbox (e.g. 12.0)
    float    hitbox_h;     // Alto de la hitbox (e.g. 12.0)
    float    angle_rad;    // Ángulo en radianes (-PI/2 = -1.5707963 es vertical arriba)
    float    speed;        // Velocidad en píxeles/frame (e.g. 16.0 - 21.0)
    uint16_t damage;       // Daño infligido por proyectil
    uint8_t  source;       // 0 = Centro del jugador, 1..4 = Opciones
    uint8_t  b_type;       // 0 = Lineal, 1 = Teledirigido, 3 = Láser
    int16_t  anm_script;   // ID del script ANM (0x460 = Amuleto Reimu)
    int16_t  sfx_id;       // ID de SFX (0 = primera bala suena, -1 = silenciosa)
};
```

### C. Implementación de Flower Sign (Type B) "Flower Bloom"
- **Identidad Danmaku:** "Cada disparo comienza como una pequeña semilla y se abre progresivamente en pétalos."
- **Salida vs. Apertura:**
  - **Salida (`| [RIN]`):** Todos los proyectiles emergen agrupados a $\le \pm 2.5$ px de Rin, pareciendo una única semilla compacta.
  - **Apertura (`\ | /`):** A medida que ascienden, los proyectiles divergen suavemente en $X$ e $Y$, formando una flor geométrica completa de 8 a 10 pétalos:
    ```
           o       o
              O
           o  ●  o
              O
           o       o

              [RIN]
    ```
   - **Núcleo Central Estambre (`●`):** Proyectil de alta potencia ($V_y = -14.0$ px/f en ascenso, daño hasta 280). Gracias al hook dinámico con `speed = 0.0`, **permanece permanentemente anclado en el centro exacto $(0, 0)$** de la flor sirviendo como estambre brillante.
   - **Pétalos (`O, o`):**
     - Ascienden como semilla compacta vertical (ancho $0.0$ px) durante 12 frames hasta $Y \approx 190-210$ (área activa de combate en pantalla, en vez de subir al techo donde se salían de pantalla).
     - Frenan suavemente frente al objetivo durante 6 frames.
     - Abren durante 12 frames (frames 18 a 29) a velocidad radial de $3.8$ a $4.4$ px/f leída de `[rbx + 0x154]`, formando un **patrón de flor expansivo y claramente visible** (radio ~50 px, diámetro total de ~100 px).
     - **Flor Flotando Estática en el Lugar:** Se queda quieta flotando ($V_x = 0.0, V_y = -0.3$ px/f) durante **35 frames (~0.60 segundos)**, permitiendo apreciar el dibujo de la flor en pantalla mientras tritura continuamente al enemigo.
     - **Disipación Limpia:** Al frame 65, la flor completa se desvanece de inmediato en pantalla (`mov word ptr [rbx + 0x10], 0`).
   - **Cadencia:** Período de 18 frames (~3.33 flores/segundo), ritmo floral armónico y cadencioso.
- **Modo Focused (Lanza Floral Concentrada hacia Adelante):**
   - Activado al mantener `Shift`.
   - **CERO AMETRALLADORA:** Cadencia de 18 frames (idéntica a modo normal, ritmo limpio y no ametralladora).
   - Semilla a alta velocidad ($V_y = -18.0$ px/f) sube directo al objetivo.
   - Frena sobre el hitbox del boss durante 6 frames.
   - Abre en un diamante floral penetrante vertical ($X \in [-6, +6]$ px).
   - Taladra de manera estática el centro del boss por 35 frames ($V_x = 0, V_y = -0.4$ px/f).
   - Se disipa limpiamente al frame 55.
   - Daño concentrado masivo: hasta 1020 por flor en MAX Power (Núcleo 380 + 8 agujas a 80 c/u).
- **Progresión de Poder (0 a 128 MAX):**
   - Nivel 0 (0..7): Semilla única (1 proyectil ●, 70 daño unfocused / 90 focused).
   - Nivel 1 (8..15): Tallo en brote (1 núcleo ● + 2 pétalos/agujas, 170 daño unfoc / 230 foc).
   - Nivel 2 (16..31): Flor en cruz (1 núcleo ● + 4 pétalos/agujas, 280 daño unfoc / 390 foc).
   - Nivel 3 (32..47): Flor de 6 pétalos (7 proyectiles, 420 daño unfoc / 560 foc).
   - Nivel 4 (48..63): Flor completa de 8 pétalos (9 proyectiles, 560 daño unfoc / 680 foc).
   - Nivel 5 (64..79): Flor reforzada de 8 pétalos (9 proyectiles, 640 daño unfoc / 840 foc).
   - Nivel 6 (80..95): Flor mayor de 10 pétalos (11 proyectiles, 780 daño unfoc / 940 foc).
   - Nivel 7 (96..128 MAX): Crisantemo Celestial (11 proyectiles, 880 daño unfocused / 1020 focused).

### D. Mapa de Memoria de la Sección `.rin` (`0x140c6b000`)
| Offset RVA | Offset VA | Descripción |
|---|---|---|
| `+0x0000` | `0x140c6b000` | Tabla expandida de 6 entradas de velocidad y punteros de disparo (192 bytes). |
| `+0x0100` | `0x140c6b100` | Tabla expandida de strings de animación de opciones (48 bytes). |
| `+0x0180` | `0x140c6b180` | Tabla expandida de despacho de Bombas y actualización de Opciones (96 bytes). |
| `+0x0200` | `0x140c6b200` | Hook de acotamiento seguro de índice de opciones para evitar overflows. |
| `+0x0280` | `0x140c6b280` | Stub de ensamblador para disparo de Rin Wind Sign (Type A). |
| `+0x0300` | `0x140c6b300` | Stub de ensamblador para disparo Unfocused de Flower Bloom (Type B). |
| `+0x0340` | `0x140c6b340` | Stub de ensamblador para disparo Focused de Flower Bloom (Type B). |
| `+0x0400` | `0x140c6b400` | Tabla de Poder Unfocused de Flower Bloom (8 niveles, 128 bytes). |
| `+0x0500` | `0x140c6b500` | Tabla de Poder Focused de Flower Bloom (8 niveles, 128 bytes). |
| `+0x0600` | `0x140c6b600` | Definición de corrientes de proyectiles Unfocused (2016 bytes). |
| `+0x1000` | `0x140c6c000` | Definición de corrientes de proyectiles Focused (1800 bytes). |
| `+0x2000` | `0x140c6d000` | `VA_RIN_BLOOM_HOOK`: Rutina de floración demorada, suspensión estática y disipación (580 bytes). |
| `+0x8000` | `0x140c73000` | `VA_RIN_LOG_BUFFER`: Buffer de telemetría de trayectorias en memoria (hasta 512 registros). |

### E. Hook de Explosión Danmaku y Floración Estática (`VA_RIN_BLOOM_HOOK` en `0x140c6d000`)
Para evitar comportamientos de escopeta o ametralladora y lograr la identidad de flor flotante y disipación, se diseñó la rutina x86_64 con `MiniAssembler`:
- **Punto de Enganche:** `0x140069a36` en `Player::UpdateBullets` (`jmp 0x140c6d000`).
- **Discriminadores:** Proyectiles con `b_type == 4` (Unfocused) y `b_type == 5` (Focused). Balas normales (`b_type == 0`) vuelven inmediatamente a `0x140069a3b`.
- **Fase 1: Ascenso Rápido de la Semilla (`age < 12` frames en unfoc, `< 10` en foc):**
  - Fuerza $V_x = 0.0$ y $V_y = -14.0$ px/f (-18.0 en foc).
  - Los proyectiles viajan como una sola semilla vertical compacta ($| \text{ [RIN]}$) hasta el centro-alto de la pantalla ($Y \approx 190-210$).
- **Fase 2: Desaceleración en Altura Media (`12 <= age < 18` frames en unfoc, `10 <= age < 15` en foc):**
  - Desacelera suavemente frente al objetivo ($V_y \leftarrow V_y \times 0.60$).
- **Fase 3: Apertura en Gran Flor Visible (`18 <= age < 30` frames en unfoc, `15 <= age < 23` en foc):**
  - Calcula $V_x = \cos(\text{ang}) \times \text{speed}$ y $V_y = \sin(\text{ang}) \times \text{speed}$ multiplicando por `[rbx + 0x154]`.
  - Para el núcleo ● (`speed = 0.0`), el desplazamiento es $0.0$, manteniéndose inmóvil en el centro exacto como estambre.
  - Para los pétalos (`speed = 3.8..4.4`), durante 12 frames se abren visiblemente hasta un radio de ~50 px (diámetro ~100 px), revelando el dibujo de flor completo.
- **Fase 4: Flor Flotando Estática en el Lugar (`30 <= age < 65` frames en unfoc, `23 <= age < 55` en foc):**
  - Se fija $V_x = 0.0$ y $V_y = -0.3$ px/f (-0.4 en foc).
  - **La flor NO sale volando:** se queda flotando en el lugar durante **35 frames (~0.60 segundos)**. Todos sus proyectiles permanecen suspendidos en formación floral sobre el enemigo descargando daño sostenido continuo.
- **Fase 5: Disipación Limpia (`age >= 65` frames en unfoc, `>= 55` en foc):**
  - Ejecuta `mov word ptr [rbx + 0x10], 0`.
  - En el motor de Touhou 6, `[rbx + 0x10]` es el indicador de bala activa. Escribir 0 desactiva la ranura de inmediato, haciendo que la flor se desvanezca limpiamente sin crasheos ni proyectiles residuales.
- **Fase 6: Registro de Telemetría:**
  - Guarda `[age, b_type, X, Y, Vx, Vy, angle, speed]` en `VA_RIN_LOG_BUFFER` (`0x140c73000`).

### F. Lecciones Críticas de Depuración y Ensamblado x86_64
1. **Ensamblado Automatizado de Dos Pasadas (`MiniAssembler`):**
   - Calcular saltos condicionales relativos (`jge`, `jne`, `je`) y desplazamientos `[rip + disp32]` a mano en código de más de 100 bytes inevitablemente introduce desalineaciones por bytes de prefijos o desplazamientos respecto al final de la instrucción.
   - `MiniAssembler` realiza una primera pasada para determinar el tamaño exacto de cada opcode (`jcc` rel32 = 6 bytes, `jmp` rel32 = 5 bytes, `movss [rip + ...]` = 8 bytes) y una segunda pasada para emitir desplazamientos exactos a nivel de byte.
2. **Mecánica de Desaparición de Balas en Touhou 6:**
   - La estructura de proyectil del jugador mide `0x170` (368 bytes).
   - El bucle `Player::UpdateBullets` comprueba `movzx edx, word ptr [rbx + 0x10]; test dx, dx; je skip_dead_bullet`.
   - Modificar `word ptr [rbx + 0x10] = 0` libera el proyectil limpiamente tanto para movimiento como para colisión.
3. **Condición de Salida del Bucle de Poder (`Player::SpawnShot` en `0x14006a5b7`):**
   - El motor del juego avanza por las filas de la tabla de poder con `cmp eax, [rbx + 4]; jge advance_row`.
   - El nivel final debe tener un umbral superior a 128 (e.g. `999`) y una fila centinela `(0, 99999, 0)`.
