## ADDED Requirements

### Requirement: Report skipped skill files in the error journal
When `SkillRegistry.reload()` skips a file in `skills/*.json`, it SHALL record one error journal entry with `source` `skills.registry.load`, `severity` `warning`, and the file name in `context`. The registry MUST NOT raise because of a skipped file.

#### Scenario: Broken JSON is reported and the valid skill still loads
- **GIVEN** a skills directory with `broken.json` containing invalid JSON and `good.json` containing a valid skill named `good_skill`
- **AND** `JARVIS_ERROR_JOURNAL_PATH` points to a file in a temporary directory
- **WHEN** a `SkillRegistry` is created or `reload()` is called
- **THEN** `good_skill` SHALL be available in `list()` and `get()`
- **AND** the error journal SHALL contain an entry with `source` `skills.registry.load` and `severity` `warning` whose `context` names `broken.json` and whose `error_type` is `JSONDecodeError`

#### Scenario: Valid JSON without a name is reported and skipped
- **GIVEN** a skills directory with `noname.json` containing a valid JSON object that has no `name` field (or an empty `name`)
- **AND** `JARVIS_ERROR_JOURNAL_PATH` points to a file in a temporary directory
- **WHEN** a `SkillRegistry` is created or `reload()` is called
- **THEN** no skill SHALL be registered from `noname.json`
- **AND** the error journal SHALL contain an entry with `source` `skills.registry.load` and `severity` `warning` whose `context` names `noname.json` and whose `message` states that the skill has no name
- **AND** the builtin skills SHALL remain available

#### Scenario: JSON that is not an object is reported
- **GIVEN** a skills directory with `list.json` containing a valid JSON array (not an object)
- **WHEN** a `SkillRegistry` is created or `reload()` is called
- **THEN** the error journal SHALL contain an entry with `source` `skills.registry.load` whose `context` names `list.json`
- **AND** `reload()` SHALL complete without raising

### Requirement: Continue loading after a skipped skill file
`SkillRegistry.reload()` SHALL keep loading all remaining skill files and the builtin skills after skipping an invalid file, regardless of file order.

#### Scenario: Invalid file sorted before valid files does not stop the load
- **GIVEN** a skills directory with `a_broken.json` (invalid JSON) sorted before `z_valid.json` (valid skill named `z_skill`)
- **WHEN** a `SkillRegistry` is created
- **THEN** `z_skill` and every builtin skill SHALL be available
- **AND** exactly one journal entry SHALL be recorded for `a_broken.json`

#### Scenario: Valid skills directory produces no journal entries
- **GIVEN** a skills directory containing only valid skill files
- **AND** `JARVIS_ERROR_JOURNAL_PATH` points to a non-existent file in a temporary directory
- **WHEN** a `SkillRegistry` is created
- **THEN** no entry with `source` `skills.registry.load` SHALL be written to the journal
