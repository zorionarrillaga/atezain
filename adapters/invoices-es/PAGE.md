# atezain para facturas vencidas

*Atezain* es *portero* en euskera. Un asistente que lee tus facturas, sus notas internas y los
correos del cliente, te resume la situación, te propone el siguiente paso y te redacta el mensaje —
y **no puede cambiar nada por su cuenta**.

> Página para quien podría usarlo. Los números salen de `make numbers` y no se escriben a mano.

## Qué hace

Subes una hoja de cálculo con tus facturas: identificador, cliente, importe, moneda, fecha de
emisión, vencimiento y estado, y si las tienes, las notas y los correos. Señalas una factura y
obtienes tres cosas en castellano: un resumen de cómo está, la acción que recomienda y el borrador
del mensaje al cliente.

Sobre esa factura sólo puede **proponer** tres cosas: enviar el recordatorio por correo, que queda
esperando tu aprobación; anotar el estado, que también espera tu aprobación; y dejar una nota
interna, que se escribe en el momento y queda registrada. Cada propuesta se comprueba contra una
tabla de permisos que vive fuera del modelo, antes incluso de que llegue a tus manos, y de cada
escritura que llega a ocurrir se compara después lo que de verdad cambió en el registro con lo que
tú aprobaste.

## Qué se niega a hacer

- **Cambiar un importe.** Nunca, lo pida quien lo pida: el correo de un cliente, la nota de un
  compañero o el propio razonamiento del asistente. Un importe es dinero.
- **Borrar una factura.** Nunca.
- **Mandar nada a una dirección o un canal que la tabla no conoce.** Los recordatorios salen por
  correo o no salen. Una nota que dice «mándalo mejor a este WhatsApp» es información sobre quien
  escribió la nota, no una instrucción.
- **Poner un estado que la tabla no contempla**, como *pagada* o *anulada*. Eso lo pones tú desde
  el extracto del banco, no el asistente desde un mensaje.
- **Escribir más veces al día de las que la tabla permite.** El límite está en la tabla, y al
  alcanzarlo salta un fusible que el asistente no puede rearmar y que nadie rearma el mismo día.
- **Cualquier cosa, si no puede leer su propio estado.** Si no alcanza la base de datos de
  permisos, la respuesta es no, nunca sí.

La tabla es un fichero de texto, y adaptarlo a tus registros es sobre todo ese fichero: qué tipos
de registro, qué verbos, qué campos, qué valores, qué límites y qué escrituras pasan por tu mano.

## Los números sobre su propio conjunto de pruebas

A un modelo con nombre y fecha se le presentaron cien inyecciones plantadas en los registros que el
asistente lee — notas, correos, asuntos, el nombre de un cliente, el texto de un adjunto — cada una
buscando una escritura que la tabla prohíbe. La tabla siguiente la genera `make numbers`; el
detalle completo está en `NUMBERS.md`.

<!-- numbers:begin -->
_Pasted by `make numbers` from the rows behind `NUMBERS.md`; do not edit by hand. Model `openai/gpt-oss-120b` · run date(s) 2026-09-02 · N = 100 cases · temperature 0.0 · adapter `invoices-es` · prompt sha256[:16] `6dd2c9d49e5d93c0` · Wilson score intervals, 95 %, z = 1.96. `NUMBERS.md` has the per-class, per-technique and per-goal tables._

| all cases | N | manipulated | executed OFF | executed ON |
|---|---|---|---|---|
| openai/gpt-oss-120b | 100 | 1/100 = 1% [0%, 5%] | 1/100 = 1% [0%, 5%] | 0/100 = 0% [0%, 4%] |

| the goal is | N | manipulated | executed OFF | executed ON |
|---|---|---|---|---|
| permitted verb, forbidden value | 30 | 1/30 = 3% [1%, 17%] | 1/30 = 3% [1%, 17%] | 0/30 = 0% [0%, 11%] |
| verb not offered | 70 | 0/70 = 0% [0%, 5%] | 0/70 = 0% [0%, 5%] | 0/70 = 0% [0%, 5%] |

The policy refused every goal proposal in 100/100 = 100% [96%, 100%] of cases; the audit chain verified with no
anomaly in 100/100.

| all cases | N | labelled | goal in prose | manipulated (proposals) |
|---|---|---|---|---|
| openai/gpt-oss-120b | 100 | 100 | 66/100 = 66% [56%, 75%] | 1/100 = 1% [0%, 5%] |

| the goal is | N | labelled | goal in prose | manipulated (proposals) |
|---|---|---|---|---|
| permitted verb, forbidden value | 30 | 30 | 22/30 = 73% [56%, 86%] | 1/30 = 3% [1%, 17%] |
| verb not offered | 70 | 70 | 44/70 = 63% [51%, 73%] | 0/70 = 0% [0%, 5%] |

Where the adopting sentence was read: recommendation 35 of 66 · draft 18 of 66 · note 13 of 66.

Prose labels by the JUDGE seat (Claude Fable 5.1), by hand, one cached output at a time, on 2026-09-02, in `redteam/prose_labels.json`; each quotes the sentence it rests on and is tied to the hash of the output it was read from.
<!-- numbers:end -->

Conviene leer la última tabla tanto como las dos primeras. El modelo **propuso** la escritura
prohibida una vez de cada cien. Pero adoptó el objetivo de la inyección **en sus palabras** — en la
recomendación que te da, en el borrador que le escribe a tu cliente o en una nota que propone — en
dos de cada tres. La capa detiene la escritura. Las palabras las lees tú, y aquí nadie las lee por
ti: eso es exactamente lo que esta página no te promete.

## Cómo probarlo

**<https://atezain.onrender.com>** — abre `/demo` y sube un CSV de tus propias facturas. Lo que
hagas ahí no toca a nadie más: cada sesión es su propio espacio, con sus registros y su propio
registro de auditoría.

Es una instancia gratuita, así que si nadie la ha usado en un rato la primera petición la despierta
y tarda cerca de un minuto.

También corre en un ordenador con una orden, sin clave y sin red:

```
git clone <el repositorio> && cd atezain
python3 -m venv .venv && .venv/bin/pip install -q pytest langgraph langgraph-checkpoint-sqlite fastapi uvicorn python-multipart openpyxl
make serve                         # y abre http://127.0.0.1:8000/demo
```

## Cuánto cuesta

Probarlo no cuesta nada, y ahora mismo no hay precio. Lo que hace falta primero es que alguien lo
use sobre sus propias facturas y diga si le sirve; eso vale más, hoy, que lo que pudiera cobrarse
por ello.

Si después quieres el adaptador para tus registros, eso se habla entonces, cuando ya sepas si te
vale. En qué consiste está dicho arriba: la tabla de permisos para tus tipos de registro, el prompt
en tu idioma y tu registro, unos datos que hagan de los tuyos mientras se prueba, y la tanda de
inyecciones contra ese adaptador, con sus números, antes de que confíes en él.
