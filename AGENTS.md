# Instrucciones para agentes

<!-- egm-por-defecto -->
## Diseño con EGM (Elephant-Goldfish Model) por defecto

Antes de implementar un cambio que requiera decisiones de diseño (función, servicio o componente nuevo;
cambio de arquitectura, modelo de datos, API o infraestructura), usa el plugin EGM
(`google/elephant-goldfish-model`; en Claude Code los comandos llevan el prefijo `/egm:`):

1. `egm-brainstorm <idea>`: explorar requisitos y alternativas; deja `concepts_brief.md`.
2. `egm-draft`: redactar la especificación sección por sección (`design_draft.md`) con retroalimentación del dueño.
3. `egm-review <borrador>` (o `egm-review-loop`) y `egm-resolve` con los hallazgos que el dueño elija.
4. Implementar solo cuando el dueño apruebe el diseño. Si el código se aparta de un diseño aprobado: `egm-drift-report`.

No hace falta para correcciones pequeñas y claras (un bug acotado, un texto, un ajuste de configuración, una tarea
operativa). Si hay duda, pregunta en una línea. Los documentos de diseño van en `docs/diseno/<tema>/` y se versionan
con el código; nada de secretos ni datos personales en ellos.
