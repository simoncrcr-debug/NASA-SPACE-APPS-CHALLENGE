# AerosphereX — el cielo, explicado con calma

Página web educativa para explorar imágenes reales de los telescopios espaciales **SPHEREx** y **NEOWISE** (NASA/IPAC IRSA). Escribes el nombre de un asteroide, un cometa o una estrella y la página busca y descarga las imágenes por ti, marca dónde está el objeto y lo explica en lenguaje sencillo, sin alarmismo.

La idea es ayudar a la gente a entender eventos del cielo (por ejemplo, asteroides que "van a chocar con la Tierra") con datos oficiales en lugar de rumores.

## Qué necesitas

- **Windows, macOS o Linux.**
- **Python 3.9 o más reciente.** No hace falta instalar ninguna librería: solo se usa la biblioteca estándar.
- **Conexión a Internet** (la página consulta IRSA, JPL Horizons y SIMBAD).
- Un navegador actualizado: **Chrome, Edge o Firefox**.

### Instalar Python (si no lo tienes)

- **Windows:** descárgalo de <https://www.python.org/downloads/> y, en el instalador, marca **"Add Python to PATH"**. También sirve:
  ```bash
  winget install --id Python.Python.3.13 -e
  ```
- **macOS:** `brew install python` o el instalador de python.org.
- **Linux:** normalmente ya viene instalado (`python3 --version`).

## Descargar el proyecto

Con Git:

```bash
git clone https://github.com/<usuario>/<repositorio>.git
```

```bash
cd <repositorio>
```

O desde GitHub: botón verde **Code → Download ZIP** y descomprímelo.

## Ejecutar

### Windows

Haz doble clic en **`run.bat`**. Se abre una ventana negra (el servidor) y el navegador en <http://127.0.0.1:8765>.

Para cerrarlo, vuelve a la ventana negra y pulsa **Ctrl + C**.

> `run.bat` primero hace una conexión rápida a IRSA, SIMBAD y JPL con Windows. Así Windows descarga los certificados de seguridad que Python necesita; sin eso, en una PC nueva las búsquedas pueden fallar con `CERTIFICATE_VERIFY_FAILED`.

### macOS / Linux

```bash
python3 server.py
```

El navegador se abre solo en <http://127.0.0.1:8765>. Para cerrarlo: **Ctrl + C** en la terminal.

### ¿Y abrir `index.html` directamente?

Funciona, pero solo para **abrir archivos FITS propios**. Las búsquedas y descargas necesitan el servidor (`server.py`), porque los archivos de NASA no se pueden descargar directamente desde el navegador.

## Cómo se usa

1. Escribe un nombre en el buscador, por ejemplo:
   - **Cometas y asteroides:** `3I Atlas`, `Florence`, `(3122) Florence`, `Apophis`, `2024 YR4`, `1P/Halley`.
   - **Estrellas y galaxias:** `Andrómeda`, `Vega`, `Nebulosa de Orión`.
   - **Coordenadas:** `133.7951, -7.2451` o `08h55m10.831s, −7d14m42.53s`.
2. Elige el archivo de imágenes: **SPHEREx o NEOWISE** (automático), **solo SPHEREx** (2025 en adelante) o **solo NEOWISE** (2013–2024).
3. Para asteroides y cometas, la página descarga hasta 8 imágenes de una misma "visita". Pulsa **▶** para verlas como animación: el objeto queda en el centro y las estrellas del fondo se mueven.
4. Debajo de la imagen verás:
   - La **fecha de la observación** (también aparece sobre la imagen).
   - **Datos oficiales** del objeto (tamaño, tipo de órbita, descubrimiento…) tomados de **NASA/JPL SBDB** para asteroides y cometas, o de **NASA/IPAC NED** para estrellas y galaxias. Se usan el nombre que buscaste o el nombre `OBJECT` escrito en el FITS.
   - **Riesgo para la Tierra**, con datos de **NASA/JPL CNEOS**: si está o estuvo en la lista de vigilancia de impactos *Sentry*, sus próximos pasos cercanos y la distancia en la imagen, explicados con calma.
   - El botón **"Origen del FITS ↗"** abre en el navegador el archivo original en IRSA (o la zona del cielo en IRSA si el archivo es local). En una búsqueda de asteroides, **"Ver esta búsqueda en IRSA ↗"** abre los mismos resultados en la web de IRSA.
5. También puedes abrir tus propios archivos con **＋ Abrir FITS** (`.fits`, `.fit`, `.fts`, `.fits.gz`, hasta 12 a la vez) y escribir debajo de la imagen el nombre del cuerpo que buscas para que se marque su posición.

Más detalles de las funciones y sus límites científicos en [`LEEME.txt`](LEEME.txt).

## Archivos del proyecto

| Archivo | Qué hace |
|---|---|
| `index.html` | Toda la página: diseño, visor FITS y lógica (HTML, CSS y JavaScript en un solo archivo). |
| `server.py` | Servidor local en Python. Sirve la página y hace de intermediario con IRSA (SIA, MOST, descargas FITS), SIMBAD y JPL Horizons. Solo escucha en `127.0.0.1` (tu propio computador). |
| `run.bat` | Arranque fácil en Windows. |
| `LEEME.txt` | Descripción detallada de funciones y límites científicos. |

## Fuentes de datos

- **NASA/IPAC IRSA** — imágenes SPHEREx y NEOWISE, y el buscador de objetos móviles MOST.
- **JPL Horizons** (NASA) — posición de planetas, asteroides y cometas en una fecha exacta.
- **NASA/JPL SBDB, Sentry y CAD** — ficha oficial de asteroides y cometas, vigilancia de impactos y pasos cercanos a la Tierra.
- **NASA/IPAC NED** — ficha oficial de galaxias y estrellas.
- **SIMBAD** (CDS, Estrasburgo) — nombres y posiciones de estrellas y galaxias.

## Problemas comunes

| Mensaje | Qué significa / qué hacer |
|---|---|
| "Inicia la página con run.bat" | Abriste `index.html` directamente. Ejecuta `run.bat` o `python3 server.py`. |
| `CERTIFICATE_VERIFY_FAILED` | En Windows, cierra y vuelve a abrir `run.bat`. En macOS, ejecuta *Install Certificates.command* en la carpeta de Python. |
| "SIMBAD respondió HTTP 404" | El catálogo SIMBAD no está disponible en ese momento. Asteroides, cometas, ejemplos y coordenadas siguen funcionando. |
| "Server busy, retry later" / descargas lentas | IRSA está ocupado. El servidor reintenta solo; si falla, prueba en unos minutos. Las imágenes de SPHEREx pesan ~4 MB cada una. |
| "Address already in use" | Ya hay otra copia del servidor abierta en el puerto 8765. Ciérrala o usa esa. |
| No se encuentra Python | Instálalo (ver arriba) y en Windows marca "Add Python to PATH". |

## Aviso

Herramienta educativa. Una imagen o una posición calculada **no** es una alerta ni una evaluación de riesgo de impacto. Para eso, consulta [NASA/JPL CNEOS Sentry](https://cneos.jpl.nasa.gov/sentry/).
