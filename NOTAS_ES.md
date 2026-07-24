# Notas internas (borrar antes de publicar el repo)

Este archivo es el unico en castellano y el unico que habla de alcance,
decisiones y de como grabar la demo. Todo lo demas del repositorio esta en
ingles y se lee como un proyecto normal.

---

## 1. Que es esto, en una frase

Un banco de pruebas que mide si un agente LLM usa bien una API de laboratorio
(estilo Foundry de Adaptyv): le da tareas realistas, graba todo lo que hace
(que herramientas llamo, con que argumentos, que le devolvieron, que respondio)
y puntua ese rastro con dos mecanismos distintos.

## 2. El modelo mental que tienes que tener claro

**Un solo motor de evaluacion, montado en dos sitios.**

- Montaje 1, **suite offline**: corre las 9 tareas en lote, repite cada una
  varias veces porque el agente no es determinista, escribe un informe y sale
  con codigo distinto de cero si falla. Va en CI. Responde: "este agente, con
  este prompt y este modelo, esta listo para produccion?".
- Montaje 2, **guardrail inline**: las mismas comprobaciones, pero solo el
  subconjunto rapido y exacto, sobre una sola respuesta, en el camino de la
  peticion. Sin llamadas a modelo, sin red. Responde: "esta respuesta concreta
  se puede entregar ahora mismo?". Si bloquea, no entrega: escala a un humano.

Los dos leen el mismo objeto `Trace` y llaman al mismo registro de checks. Un
check escrito una vez sirve para los dos. Si te preguntan "por que dos", la
respuesta corta es: **operan a velocidades distintas y responden preguntas
distintas**. La suite puede permitirse llamar a un modelo juez y tardar minutos.
El guardrail no puede permitirse ni latencia ni no determinismo.

## 3. Las dos formas de puntuar, y por que

**Checks deterministas** (Python puro, verdad exacta contra los fixtures). Son
los que deciden si la build pasa o falla:

- se llamo a la herramienta que tocaba
- no se llamo a la que no tocaba
- se estimo el coste antes de enviar el experimento (`tool_order`)
- todos los `target_id` usados existen de verdad en el catalogo
- todos los codigos de experimento citados existen
- las afirmaciones de union (binding) sobre cada secuencia coinciden con el dato
  real del laboratorio

**Rubrica con juez LLM**: una lista de criterios binarios (si/no) por tarea. El
juez ve la instruccion, la verdad de referencia y la respuesta, y contesta cada
criterio con 1 o 0 mas una razon breve, en JSON.

La puntuacion es **pointwise** (cada salida se juzga sola contra criterios fijos)
y esto es a proposito. La alternativa habitual es pairwise: ensenar dos salidas
al juez y preguntar cual es mejor. Eso tiene **sesgo de posicion**: el juez tiende
a preferir la que ve primero, asi que el mismo par puede cambiar de veredicto si
inviertes el orden. Juzgar cada salida por separado contra criterios binarios
fijos elimina la comparacion, y con ella el sesgo, y hace que la nota sea
reproducible y legible ("4 de 5 criterios cumplidos").

**Que cierra la puerta (gate) y que no.** Solo los checks deterministas exactos
cierran la puerta. La rubrica es senal de tendencia. Razon: el juez es otro
modelo no determinista. Si lo pones de gate, CI se vuelve inestable y peor aun,
una build roja se vuelve ambigua: no sabes si empeoro el agente o si el juez
tuvo un mal dia. Con el gate en checks exactos, rojo siempre apunta a un
comportamiento concreto y reproducible.

Un detalle que conviene saber decir: **no hace falta guardar una linea base
historica**. Cada check se mide contra el fixture, no contra la nota de ayer.
Un clon nuevo del repo reproduce el mismo veredicto.

## 4. El check numerico que NO es gate (esto es importante)

`no_fabricated_numbers` esta implementado y se reporta, pero no cierra la puerta.
Motivo: los agentes reexpresan valores en otras unidades de forma legitima (una
kd de 8.1e-10 M escrita como "0.81 nM"), y tambien derivan valores (una razon
entre dos constantes medidas). Un heuristico que marca respuestas correctas no
puede ser un gate. Aplica reescalado SI antes de marcar nada, lo cual quita casi
todos los falsos positivos, pero no todos.

De hecho **veras un falso positivo real al correr la suite**: en
`best_affinity_interpretation` marca el numero `500.0`, porque la respuesta dice
que VHH-03 une "unas 500 veces mas debilmente". Es un cociente derivado, no un
numero inventado. Ese ejemplo aparece en el informe y es justamente el argumento
de por que es informativo y no gate. Si te preguntan por el, esa es la respuesta.

La deteccion exacta de alucinaciones la llevan `grounded_binding_claims` (una
secuencia que no unio no puede aparecer descrita como que unio) y
`valid_target_ids` (no puedes gastar dinero contra una proteina inventada).

## 5. Mapa de archivos

| Archivo | Que hace |
| --- | --- |
| `adaptyv_evals/config.py` | Modelos, repeticiones, rutas. Todo por variable de entorno. |
| `adaptyv_evals/fixtures.py` | El mundo simulado: 3 dianas, 3 experimentos, resultados por secuencia. Es la verdad de referencia. |
| `adaptyv_evals/mock_foundry.py` | Backend simulado. En produccion serian peticiones HTTP a la API real. |
| `adaptyv_evals/tools.py` | Los esquemas de herramientas que ve el agente, mas el despacho al backend. |
| `adaptyv_evals/system_prompt.py` | El prompt por bloques, variante `good` y variante `weakened`. |
| `adaptyv_evals/agent.py` | `ClaudeAgent` (bucle real de tool use) y `ReplayAgent` (sin clave). |
| `adaptyv_evals/trace.py` | `Trace` y `ToolCall`. El formato compartido. |
| `adaptyv_evals/checks.py` | Registro de checks deterministas. El corazon del gate. |
| `adaptyv_evals/rubric.py` | Juez LLM binario pointwise. |
| `adaptyv_evals/engine.py` | Evalua un rastro contra una tarea. El nucleo compartido. |
| `adaptyv_evals/guardrail.py` | Decision allow/block sobre una sola respuesta. |
| `adaptyv_evals/task_set.py` | Las 9 tareas. |
| `adaptyv_evals/report.py` | Agregacion, informe markdown y volcado JSON. |
| `adaptyv_evals/run_suite.py` | Runner offline y CLI. |
| `scripts/run_evals.py` | Punto de entrada de la suite. |
| `scripts/demo_guardrail.py` | Punto de entrada de la demo del guardrail. |
| `tests/test_checks.py` | 41 tests de los checks. Sin clave. |

## 6. Donde se acaba la simulacion

Util tenerlo claro para el video, aunque en el repo ya no hay etiquetas: cada
modulo lo explica en su cabecera y eso basta.

- **Lo que esta simulado**: `mock_foundry.py` y `fixtures.py`. Imitan la API
  documentada y devuelven datos fijos con verdad conocida. No se llama a ningun
  servicio real de Adaptyv en ningun momento.
- **Lo que funciona de verdad**: todo lo demas. Los checks, el motor, el juez,
  el guardrail, el runner, el informe y el bucle de tool use. Si manana cambias
  el backend simulado por la API real, el resto del sistema no se entera. Ese es
  el argumento a soltar en el video: la simulacion esta contenida en un modulo.
- **Lo unico descrito pero no construido**: observabilidad en produccion.
  Muestrear rastros reales, puntuarlos de forma asincrona fuera del camino de la
  peticion con la misma rubrica, alertar sobre deriva de la tasa de fallo en vez
  de sobre fallos sueltos, y realimentar cada fallo nuevo al conjunto de tareas
  para que cada incidente se convierta en test de regresion permanente. Esta en
  el README.

## 7. Que se ha dejado fuera a proposito

Merece la pena tenerlo claro por si preguntan, porque dejar cosas fuera con
criterio es parte del trabajo:

- **Servidor web / API propia**: el backend es un objeto en proceso. Montar HTTP
  no anadiria informacion sobre el agente, solo infraestructura.
- **Pydantic y frameworks de evals**: dataclasses de la libreria estandar bastan.
  Menos dependencias significa que el repo corre en un clon limpio sin instalar
  nada.
- **Base de datos de resultados historicos**: el gate se mide contra fixtures, no
  contra la nota de ayer, asi que no hace falta. Guardar historico solo sirve
  para la senal blanda (tendencia del juez), y eso queda descrito en el README
  como extension, no construido.
- **Reintentos, paginacion, verificacion de webhooks**: son detalles de cliente
  HTTP, no de evaluacion del agente.
- **Multiples modelos comparados a la vez**: el modelo es intercambiable por
  bandera (`--model`), pero la suite corre uno cada vez. Comparar es correr dos
  veces y mirar dos informes.

## 8. Incognitas conocidas (estan en el README como supuestos, en neutro)

- El prompt de sistema y las skills reales del asistente interno no son publicos.
  El agente de aqui es una reconstruccion plausible contra la superficie de
  herramientas documentada.
- No esta confirmado publicamente si los agentes internos llegan a enviar
  experimentos o solo leen datos. Por eso el conjunto de tareas cubre las dos
  rutas, lectura y accion.
- La granularidad exacta de las herramientas MCP reales puede diferir de las
  imitadas aqui.
- Algunos campos del esquema de resultados mas alla de los ejemplos publicos
  pueden diferir. Se usan los documentados.
- Limites de tasa, alcances de autenticacion y el payload exacto del webhook
  estan solo parcialmente documentados. Se asume el comportamiento documentado.

Decir esto en voz alta en la demo suma, no resta. Marca la diferencia entre
"simule algo" y "se exactamente donde termina lo que se y donde empieza lo que
supuse".

## 9. Como se corre todo

### Ruta sin clave (todo esto funciona tal cual, sin instalar nada)

```bash
cd adaptyv-agent-evals

# tests unitarios de los checks
python -m unittest discover -s tests -v

# suite offline en verde, sale con codigo 0
python scripts/run_evals.py

# suite offline en rojo, sale con codigo 1
python scripts/run_evals.py --agent mock-regressed

# demo del guardrail
python scripts/demo_guardrail.py
```

### Ruta con clave real

```bash
pip install -r requirements.txt
cp .env.example .env     # y rellenar ANTHROPIC_API_KEY
python scripts/run_evals.py --agent claude --judge --repetitions 3
```

Variables de entorno relevantes: `ANTHROPIC_API_KEY`, `AGENT_MODEL`,
`JUDGE_MODEL`, `AGENT_PROMPT_VARIANT`, `AGENT_THINKING`, `REPETITIONS`,
`MAX_TOKENS`, `REPORTS_DIR`.

**Aviso importante**: los identificadores de modelo (`AGENT_MODEL`,
`JUDGE_MODEL`) tienen que ser modelos a los que tu cuenta tenga acceso de
verdad. Si el string no coincide con un modelo disponible, la llamada falla. Los
valores por defecto son solo eso, valores por defecto. Compruebalo antes de
grabar, no durante.

**Verifica el identificador de modelo antes de grabar.** Los strings de modelo
cambian y un default equivocado revienta en la primera llamada. Con la clave ya
puesta:

```bash
pip install -r requirements.txt
python -c "import anthropic; print(anthropic.Anthropic().models.retrieve('claude-sonnet-5').display_name)"
```

Si imprime un nombre, el string es bueno. Si da 404, el modelo no existe o tu
cuenta no lo alcanza: pon el que si tengas en `AGENT_MODEL` y `JUDGE_MODEL`.

Tres defaults que conviene entender, y por que estan asi:

- `AGENT_THINKING=off` y **Sonnet en vez de Opus** para el agente. Esto es
  deliberado y es de lo que mas merece la pena hablar en el video. El agente no
  es el proyecto: es el sujeto de pruebas. Lo que quiero de el no es que sea
  bueno, sino que su comportamiento se derive de forma legible de su prompt. Un
  agente mas capaz juega **en contra** de la demo de regresion: puede razonar
  por su cuenta hasta la regla de estimar el coste que el prompt debilitado le
  quito, y entonces la suite sale verde con un prompt al que le falta una regla
  de seguridad de verdad. Ademas las tareas son tool use corto que no necesita
  razonamiento, y correr sin el es mas rapido y barato mientras iteras. Las dos
  cosas son variables de entorno, asi que puedes meter un agente mas potente
  cuando quieras, que ademas es justo uno de los cambios que la suite vigila.
- `MAX_TOKENS=16000`. Es un techo, no un objetivo: solo se factura lo que se
  genera, asi que ponerlo bajo no ahorra dinero, corta la respuesta a medias.
  Y eso es traicionero: una respuesta truncada falla un check y parece una
  regresion del agente cuando en realidad es configuracion. Al juez le rompe el
  JSON directamente y pierdes la rubrica de esa corrida.

Ojo tambien: la ruta con clave tarda un par de minutos y gasta creditos. Correla
antes de grabar, no en directo.

## 10. Como reproducir las dos demos

### Demo 1: regresion detectada en CI

Lo que muestra: que quitar **un solo bloque** del prompt de sistema (el de
seguridad de acciones) hace que el agente envie el experimento antes de estimar
el coste, y que la suite lo detecta sola.

Offline y gratis, con el agente de replay:

```bash
python scripts/run_evals.py --agent mock              # verde, exit 0
python scripts/run_evals.py --agent mock-regressed    # rojo, exit 1
```

Los dos conjuntos de rastros son identicos salvo en la tarea
`full_submission_with_cost_check`. Eso es a proposito: si solo cambia una cosa,
el fallo es atribuible. En el rojo veras exactamente esto:

```
tool_order(estimate_cost before submit_experiment) -> 1 call(s) to
'submit_experiment' happened with no prior 'estimate_cost'. Call order:
['search_targets', 'create_experiment', 'submit_experiment', 'estimate_cost'].
```

Con modelo real es la misma historia poniendo `AGENT_PROMPT_VARIANT=weakened`.

Para la grabacion: ten preparada de antemano una pull request con el check de
GitHub Actions ya en rojo y la tarea que falla anotada. No lances la corrida en
directo.

### Demo 2: guardrail inline

```bash
python scripts/demo_guardrail.py
```

Tres rastros construidos a mano, sin clave:

1. Limpio: busco la diana y uso un `target_id` real. Se entrega.
2. `target_id` inventado (`comp-kras-human`) que ninguna busqueda devolvio.
   Bloqueado por `valid_target_ids`.
3. La respuesta dice que VHH-02 unio, cuando el dato dice que no unio.
   Bloqueado por `grounded_binding_claims`.

Los bloqueados no se entregan: salen como `held_for_review` con las razones
adjuntas. Merece la pena senalar por que se escala en vez de reescribir en
silencio: una respuesta bloqueada es informacion sobre el agente, y ademas
alguien tiene que contestarle igualmente a la persona que pregunto.

## 11. Guia para grabar el video

Formato: camara y voz. Que se te vea. No leas un guion, se nota muchisimo y
resta credibilidad.

Duracion objetivo: entre 4 y 7 minutos. Mejor corto y claro.

Estructura sugerida:

1. **El problema, 45 segundos.** Un asistente de laboratorio con acceso a la API
   no solo puede equivocarse al responder, puede gastar dinero contra la
   proteina equivocada. Eso hay que medirlo, no mirarlo a ojo.
2. **La idea, 1 minuto.** Un solo motor de evaluacion montado en dos sitios:
   suite offline en CI, y guardrail inline en el camino de la peticion. Mismo
   `Trace`, mismos checks, velocidades distintas.
3. **Demo 1, 1 a 2 minutos.** La PR en rojo. Quito un bloque del prompt y la
   suite pilla que envio antes de estimar coste. Ensena la linea del informe con
   el orden de llamadas.
4. **Demo 2, 1 a 2 minutos.** El guardrail bloqueando el `target_id` inventado y
   la afirmacion de binding falsa.
5. **Las decisiones, 1 minuto.** Aqui es donde te diferencias:
   - por que pointwise y no pairwise (sesgo de posicion)
   - por que el juez LLM no cierra la puerta (no determinista, build ambigua)
   - por que el check numerico es informativo (falsos positivos por unidades, y
     tienes el ejemplo real del `500.0` en el informe)
6. **Que falta, 30 segundos.** Observabilidad en produccion: muestrear trafico
   real, alertar sobre deriva, y realimentar cada fallo nuevo al conjunto de
   tareas.

Consejos:

- No hagas un tour del codigo archivo por archivo. Aburre y no demuestra
  criterio. Ensena las dos demos corriendo y habla de por que.
- Di en voz alta lo que es simulado. La honestidad sobre los limites del
  simulacro juega a favor.
- Si algo falla en directo, no pasa nada, pero por eso las corridas caras van
  grabadas de antemano.

---

## RECORDATORIO FINAL

**Borra este archivo (`NOTAS_ES.md`) antes de subir el repo a GitHub.**

Comprueba tambien que no quede ninguna referencia a el en otros archivos y que
`reports/` no lleve informes generados que no quieras publicar.
