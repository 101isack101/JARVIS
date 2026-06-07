from vision.prompts import visual_capture_prompt


def test_visual_prompt_blocks_unrequested_sensitive_data_searches():
    prompt = visual_capture_prompt("region").lower()

    assert "no busques" in prompt
    assert "no digas que no encontraste numeros de cuenta" in prompt
    assert "salvo que isaac lo pida explicitamente" in prompt


def test_visual_prompt_treats_hotkey_capture_as_new_visual_context():
    prompt = visual_capture_prompt("screen").lower()

    assert "nueva referencia visual" in prompt
    assert "no conviertas la captura en una busqueda de datos" in prompt


def test_visual_prompt_camera_source_is_live_visual_context():
    prompt = visual_capture_prompt("camera").lower()
    assert "camara" in prompt
    assert "no busques" in prompt  # mantiene el privacy guard
