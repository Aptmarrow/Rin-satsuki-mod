#!/bin/bash
# Script de inicio con detección inteligente de ruta y registro de logs
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f "./th06nc/th06nc.exe" ]; then
    GAME_DIR="./th06nc"
    EXE="th06nc.exe"
elif [ -f "./th06nc.exe" ]; then
    GAME_DIR="."
    EXE="th06nc.exe"
elif [ -f "./th06nc_rin_slot3.exe" ]; then
    GAME_DIR="."
    EXE="th06nc_rin_slot3.exe"
else
    echo "[ERROR] No se encontró el ejecutable del juego en $(pwd)."
    exit 1
fi

cd "$GAME_DIR"
LOG_FILE="crash_log.txt"

echo "========================================================" > "$LOG_FILE"
echo "  Touhou 6: New Classic (Rin Satsuki Mod) - Debug Log   " >> "$LOG_FILE"
echo "  Fecha y hora: $(date)                                 " >> "$LOG_FILE"
echo "  Directorio de juego: $(pwd)                           " >> "$LOG_FILE"
echo "  Ejecutable: $EXE                                      " >> "$LOG_FILE"
echo "========================================================" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

echo "[INFO] Iniciando centinela de telemetría de disparos (trace_bullets.py)..."
python3 "$SCRIPT_DIR/trace_bullets.py" --watch > /dev/null 2>&1 &
WATCH_PID=$!

trap "kill $WATCH_PID 2>/dev/null" EXIT INT TERM

WINEDEBUG=+seh wine "$EXE" 2>&1 | tee -a "$LOG_FILE"
EXIT_STATUS=${PIPESTATUS[0]}

kill $WATCH_PID 2>/dev/null
python3 "$SCRIPT_DIR/trace_bullets.py" > /dev/null 2>&1

echo "" >> "$LOG_FILE"
echo "========================================================" >> "$LOG_FILE"
echo "  Ejecución finalizada con código de salida: $EXIT_STATUS" >> "$LOG_FILE"
echo "  Fecha y hora: $(date)                                 " >> "$LOG_FILE"
echo "========================================================" >> "$LOG_FILE"

if [ $EXIT_STATUS -ne 0 ]; then
    echo ""
    echo "[AVISO] El juego se cerró con código $EXIT_STATUS."
    echo "[AVISO] Los detalles quedaron guardados en: $(pwd)/$LOG_FILE"
else
    echo ""
    echo "[OK] El juego cerró correctamente."
fi
