# Spec Delta

## Purpose

Define el contrato fail-safe observable del motor de proactividad de JARVIS: sus operaciones nunca interrumpen al llamador, pero cada fallo interno queda registrado en el error journal para poder diagnosticarlo.

## ADDED Requirements

### Requirement: Fallos del motor se registran y no se propagan
Ante cualquier excepción interna en una operación pública del motor de proactividad (briefing de arranque, observación de turno, siguiente oportunidad, descarte de la última oferta), el motor SHALL registrar el fallo en el error journal con source `proactivity.<operación>` y severidad `warning`, y MUST NOT propagar la excepción al llamador, devolviendo el mismo valor neutro que devolvía antes de este cambio.

#### Scenario: Fallo durante el briefing de arranque
- **GIVEN** un motor habilitado y un vault cuyo acceso lanza una excepción
- **WHEN** se solicita el briefing de arranque
- **THEN** el journal contiene un registro con source `proactivity.build_briefing`, severidad `warning` y el tipo de la excepción
- **AND** la operación devuelve una cadena vacía sin lanzar excepción

#### Scenario: Fallo durante la observación de un turno
- **GIVEN** un motor habilitado y un vault cuyo acceso lanza una excepción
- **WHEN** se observa un turno con texto no vacío
- **THEN** el journal contiene un registro con source `proactivity.observe`, severidad `warning` y el tipo de la excepción
- **AND** la operación retorna sin valor y sin lanzar excepción

#### Scenario: Fallo al obtener la siguiente oportunidad
- **GIVEN** un motor habilitado cuya cola de oportunidades lanza una excepción al consultar la oportunidad principal o al marcarla como ofrecida
- **WHEN** se solicita la siguiente oportunidad
- **THEN** el journal contiene un registro con source `proactivity.next_opportunity` y severidad `warning`
- **AND** la operación devuelve `None` sin lanzar excepción

#### Scenario: Fallo al descartar la última oferta
- **GIVEN** un motor con una oportunidad ofrecida previamente y una cola que lanza una excepción al marcarla como descartada
- **WHEN** se descarta la última oferta
- **THEN** el journal contiene un registro con source `proactivity.dismiss_last` y severidad `warning`
- **AND** la operación retorna sin valor y sin lanzar excepción
