"""Prompts para analisis visual de Jarvis."""

VISUAL_PRIVACY_GUARD = (
    "Regla de privacidad: no busques, extraigas, confirmes ni menciones numeros "
    "de cuenta, tarjetas, identificaciones, codigos, claves, tokens, saldos, "
    "direcciones u otros datos sensibles salvo que Isaac lo pida explicitamente "
    "en este mismo turno. Si ves datos sensibles de pasada, ignorarlos o "
    "redactarlos; no digas que no encontraste numeros de cuenta si nadie te "
    "pidio buscar eso."
)


def visual_capture_prompt(source: str) -> str:
    """Prompt corto para una captura visual iniciada por Isaac."""
    if source == "region":
        intro = (
            "Isaac recorto esta region especifica para mostrartela. "
            "Trata esto como una nueva referencia visual del momento, no como "
            "continuacion automatica de una tarea previa."
        )
    elif source == "tool":
        intro = (
            "Esta es la captura que Isaac pidio con screen_look. "
            "Trata esto como contexto visual actual para responderle."
        )
    else:
        intro = (
            "Isaac te muestra esta captura de su pantalla. "
            "Trata esto como una nueva referencia visual del momento, no como "
            "continuacion automatica de una tarea previa."
        )

    return (
        f"{intro} Mira lo que hay y responde de forma natural y breve: "
        "describi lo relevante, comenta, ayuda u opina segun el contexto visible. "
        "Si no hay un pedido especifico, no conviertas la captura en una busqueda "
        "de datos. "
        f"{VISUAL_PRIVACY_GUARD}"
    )
