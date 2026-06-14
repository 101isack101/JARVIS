# Spec: Briefing matutino hablado

Fecha: 2026-06-13 · Estado: aprobado para planificar

JARVIS narra en voz, al arrancar, un briefing matutino con: pendientes del vault
de Obsidian, estado de los repos git, titulares de IA del día y (Fase 2) la
agenda del calendario.

## Objetivo y motivación

Hoy la proactividad **detecta y encola** oportunidades pero es *muda*:
`build_briefing()` solo inyecta un bloque al `system_prompt` con la instrucción
"menciónalo solo si encaja", sin garantía de que JARVIS lo diga; `observe()`
encola pero "NO emite". Este spec cierra el "último kilómetro": que JARVIS
**hable primero** al arrancar y entregue un briefing real y útil para el día.

## Decisiones cerradas (brainstorming)

| Decisión | Valor |
|---|---|
| Disparo | **Hablado garantizado** al arrancar (turno de salida proactivo) |
| Cadencia | **Cada arranque de la app**, nunca en reconexiones de la misma sesión |
| Fuentes | Vault (existe) + repos git + noticias IA + calendario (Fase 2) |
| Acceso calendario | **Google Calendar API directa** (OAuth propio), Fase 2 |
| Noticias IA | **Reutilizar** las notas del AI News Agent en Obsidian (no buscar en web) |
| Degradación | **Fail-safe absoluto**: el briefing nunca rompe el arranque |

## Arquitectura

Principio: **aislar cada fuente** tras una interfaz simple y fail-safe; `jarvis.py`
solo orquesta. Cada recolector devuelve datos (no texto narrado): la narración la
hace Gemini (prompt-first), consistente con `briefing.py` actual.

| Módulo | Estado | Propósito único | Dependencias |
|---|---|---|---|
| `proactivity/git_repos.py` | nuevo | Escanear `PROYECTOS/` y devolver estado git por repo con cambios | `git` CLI |
| `proactivity/ai_news.py` | nuevo | Leer la nota más reciente de `Noticias IA/` y devolver titulares + fecha | filesystem |
| `integrations/google_calendar.py` | nuevo (Fase 2) | OAuth + eventos de hoy | `google-api-python-client` |
| `proactivity/morning_brief.py` | nuevo | Orquestador: junta las fuentes y renderiza el prompt de arranque | los anteriores + `build_project_states` |
| `jarvis.py` | editar | Disparar el briefing en el primer connect del proceso | `morning_brief` |

`morning_brief.py` es un **orquestador puro**: no conoce Gemini ni voz. Recibe
fuentes, devuelve texto. Testeable sin red ni sesión.

## Flujo de disparo

```
arranque app → session conecta → _on_connected
   └─ if not self._briefing_sent and JARVIS_MORNING_BRIEF:
        if not self._gemini_budget_available("[BRIEF]"): return
        data   = collect_morning_brief(vault, cfg)   # fail-safe, nunca lanza
        prompt = render_brief_prompt(data)           # "[ARRANQUE] Saluda a Isaac y..."
        self.session.send_text(prompt)               # → Gemini responde EN VOZ
        self._briefing_sent = True
```

- **Idempotencia:** `self._briefing_sent` se inicializa `False` en `__init__` y se
  pone `True` tras el primer envío. Como vive en la instancia (un proceso), suena
  una vez por arranque y **nunca** en reconexiones — esto evita repetir el briefing
  cuando Gemini reconecta por el error 1007 en sesiones largas.
- El prompt de arranque es **efímero** (va en el turno con `send_text`, no en el
  `system_prompt` permanente) → datos frescos cada día sin inflar el contexto.
- Se respeta `_gemini_budget_available` antes de emitir.
- En modo LIBRE, el envío ocurre tras el reset de VAD/AEC/wakeword que ya hace
  `_on_connected`.

## Contratos de los módulos

### `proactivity/git_repos.py`

```python
@dataclass
class RepoStatus:
    name: str            # nombre de carpeta
    dirty: int           # nº de archivos modificados/untracked (porcelain)
    ahead: int           # commits sin push
    branch: str

def scan_repo_status(root: Path, *, max_repos: int = 40,
                     per_repo_timeout_s: float = 3.0) -> list[RepoStatus]:
    """Subdirs con .git → git status --porcelain + ahead/behind.
    Devuelve SOLO repos con algo que reportar (dirty>0 o ahead>0).
    Fail-safe: cualquier error por repo lo omite; nunca lanza."""
```

- Usa `git -C <repo> status --porcelain` y `git -C <repo> rev-list --count @{u}..HEAD`
  (ahead). Si no hay upstream, ahead=0 sin error.
- Timeout por repo para no colgar el arranque si un repo está en mal estado.

### `proactivity/ai_news.py`

```python
@dataclass
class NewsDigest:
    date: str            # fecha de la nota (frontmatter o nombre de archivo)
    age_days: int        # antigüedad respecto a hoy
    headlines: list[str] # títulos de los `## N.` (sin el "N.")

def latest_ai_news(news_folder: Path, *, max_items: int = 3,
                   max_age_days: int = 3) -> NewsDigest | None:
    """Lee la nota .md más reciente de la carpeta (utf-8-sig por el BOM),
    parsea titulares con regex `^##\\s*\\d+\\.\\s*(.+)$`, devuelve top-N.
    Si la más reciente supera max_age_days, igual la devuelve con age_days
    para que el render lo aclare. None si no hay notas."""
```

- Carpeta por defecto: `H:\Obsidian ClaudeCode\Obsidian Claude Code\Noticias IA`
  (override por env).
- Acoplamiento por **artefacto** (la nota Markdown), no por código del AI News
  Agent: mientras el formato `## N. Título` siga, el briefing funciona.

### `integrations/google_calendar.py` (Fase 2)

```python
@dataclass
class CalEvent:
    start: str           # HH:MM local
    summary: str
    all_day: bool

def today_events(*, credentials_path: Path,
                 token_path: Path) -> list[CalEvent]:
    """OAuth con google-api-python-client. Token cacheado en token_path
    (fuera de git). Eventos del día ordenados por hora.
    Fail-safe: sin credenciales/token o sin red → []."""
```

- `token_path` por defecto `data/google_token.json` (añadir a `.gitignore`).
- Scope read-only `calendar.readonly`.

### `proactivity/morning_brief.py`

```python
@dataclass
class BriefData:
    vault_opps: list[str]      # de build_project_states + peek_top
    repos: list[RepoStatus]
    news: NewsDigest | None
    events: list[CalEvent]     # vacío en Fase 1

def collect_morning_brief(vault, cfg: MorningBriefConfig) -> BriefData:
    """Llama a cada fuente envuelta en try/except. Nunca lanza."""

def render_brief_prompt(data: BriefData) -> str:
    """Devuelve el prompt de arranque para send_text. Entrega DATOS
    estructurados + instrucción de tono (prompt-first), no una frase hecha.
    Si todas las fuentes vienen vacías → saludo corto ('Buenos días Isaac')."""
```

**Orden de narración propuesto** (en el prompt, como guía a Gemini): saludo →
agenda del día (Fase 2) → pendientes del vault → estado de repos → titulares de
IA. Cierra invitando a empezar. Instrucción de tono: fluido, una frase por
sección, sin recitar listas; igual que el `briefing.py` actual.

## Errores y degradación

Fail-safe absoluto, igual que `ProactivityEngine`: **ningún recolector propaga
excepciones**. Git falla → sin sección git. Noticias no encontradas → sin
noticias. Calendar no configurado → sin agenda. Todas vacías → saludo simple. El
arranque de JARVIS **nunca** se rompe por el briefing. El wiring en `_on_connected`
va envuelto en try/except con log de WARN, como el resto de la proactividad.

## Configuración (env vars)

| Var | Default | Uso |
|---|---|---|
| `JARVIS_MORNING_BRIEF` | `true` | Activa/desactiva todo el briefing |
| `JARVIS_BRIEF_REPOS_ROOT` | `C:\Users\Isaac\Desktop\PROYECTOS` | Raíz a escanear |
| `JARVIS_BRIEF_NEWS_DIR` | `H:\Obsidian ClaudeCode\Obsidian Claude Code\Noticias IA` | Carpeta de notas IA |
| `JARVIS_BRIEF_NEWS_ITEMS` | `3` | Nº de titulares |
| `JARVIS_BRIEF_NEWS_MAX_AGE` | `3` | Días máx. antes de aclarar antigüedad |
| `JARVIS_BRIEF_CALENDAR` | `false` (Fase 2) | Activa calendario |
| `GOOGLE_CALENDAR_CREDENTIALS` | — | Ruta al client_secret OAuth |

Documentar en `.env.example` y `CHANGELOG.md` (Unreleased).

## Testing

- `git_repos`: repos temporales (limpio / sucio / ahead) → aserciones sobre
  `RepoStatus`. Verifica que repos limpios se excluyen y que un repo corrupto no
  rompe el escaneo.
- `ai_news`: fixtures de notas Markdown (con BOM, varios `## N.`, carpeta vacía,
  nota vieja) → titulares y `age_days` correctos; `None` si vacía.
- `morning_brief`: render con fuentes mockeadas, incluido el caso "todo vacío" →
  saludo simple. Verifica que una fuente que lanza no tumba el resto.
- `google_calendar` (Fase 2): cliente mockeado, sin red real; caso sin token → [].

## Fases de entrega

1. **Fase 1 (local, sin OAuth):** `git_repos` + `ai_news` + `morning_brief`
   (vault + git + noticias) + wiring en `_on_connected` + tests + config + docs.
   Resultado: JARVIS habla un briefing real al arrancar.
2. **Fase 2 (calendario):** `integrations/google_calendar.py` + OAuth setup +
   integración en `morning_brief` + tests. Resultado: agenda del día incluida.

## Fuera de alcance (YAGNI)

- Clima (descartado por el usuario en brainstorming).
- Push por Discord / proactividad durante el día (otro eje del roadmap).
- Scheduler "una vez al día" real: se eligió "cada arranque", que no requiere
  persistencia de fecha.
- Búsqueda web de noticias: se reutiliza el AI News Agent existente.
