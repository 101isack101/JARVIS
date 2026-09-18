"""Gate de turno hablado en modo LIBRE.

Gemini Live entrega audio a ráfagas. El player vacía la cola ~128ms y dispara
on_underflow; si eso reabre el VAD a mitad de frase, Jarvis se interrumpe a sí
mismo. Este gate solo permite reanudar el mic cuando el SERVIDOR ya mandó
turn_complete (o un interrupt).
"""

from __future__ import annotations


class LibreSpeakGate:
    def __init__(self) -> None:
        self.speaking = False
        self.server_turn_complete = False

    def on_audio(self) -> None:
        self.speaking = True

    def on_turn_complete(self) -> None:
        self.server_turn_complete = True

    def on_underflow(self) -> bool:
        """True = el mic puede reabrirse. False = hueco transitorio, ignorar."""
        if not self.server_turn_complete:
            return False
        self.speaking = False
        self.server_turn_complete = False
        return True

    def on_interrupt(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.speaking = False
        self.server_turn_complete = False

    @property
    def waiting_for_server(self) -> bool:
        return self.speaking and not self.server_turn_complete
