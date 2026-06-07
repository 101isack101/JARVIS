"""runtime_modes.py - Modos de trabajo livianos para Jarvis."""

from __future__ import annotations

from dataclasses import dataclass


MODES = {
    "general": "Conversacion normal, respuestas rapidas y memoria cuando ayude.",
    "coding": "Prioriza codigo, debugging, arquitectura y delegacion a Claude para razonamiento profundo.",
    "debugging": "Prioriza logs, errores, pantalla y pasos de reproduccion.",
    "meeting": "Prioriza escucha, resumen, decisiones y tareas pendientes.",
    "planning": "Prioriza pasos, riesgos, dependencias y memoria durable.",
    "study": "Observa lecturas/cursos, captura evidencia y construye notas en Obsidian.",
    "english": "Practica conversacion en ingles, corrige errores y entrena fluidez.",
}


@dataclass
class ModeManager:
    mode: str = "general"

    def set_mode(self, mode: str) -> dict:
        normalized = (mode or "general").strip().lower()
        if normalized not in MODES:
            return {
                "changed": False,
                "mode": self.mode,
                "error": f"modo invalido: {mode}",
                "valid_modes": list(MODES),
            }
        self.mode = normalized
        return {
            "changed": True,
            "mode": self.mode,
            "description": MODES[self.mode],
        }

    def get_mode(self) -> dict:
        return {
            "mode": self.mode,
            "description": MODES[self.mode],
            "valid_modes": list(MODES),
        }
