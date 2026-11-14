# Cosmic Hunter

Prototipo astronómico educativo en español. Un servidor Python local sirve la página y lee las imágenes de la carpeta raíz o de `fotos/`.

## Iniciar

1. Ejecuta `start_cosmic_hunter.bat`.
2. Cuando aparezca el mensaje del servidor, abre `http://127.0.0.1:8765`.
3. Deja la ventana del servidor abierta mientras uses la página.

Python necesita NumPy y Pillow. Para FITS, instala Astropy con `python -m pip install -r requirements.txt`.

## Perfil por píxel y CSV

1. Pulsa **Buscar fotos** y elige la imagen de referencia.
2. Haz clic en el punto exacto; el visor muestra sus coordenadas x/y en píxeles.
3. Elige un píxel o una apertura alrededor y marca las imágenes que quieras comparar.
4. Para un perfil con eje físico, carga longitudes de onda verificadas con la plantilla `archivo;longitud_onda_nm`. Si no tienes esa calibración, usa **Número de foto · sin calibrar**.
5. El botón **Calcular espectro** dibuja los puntos disponibles y escribe un CSV detallado en `resultados_espectros/`.

El CSV incluye valores digitales, píxel seleccionado, apertura, imagen, alineación y longitud de onda solo si fue proporcionada. No interpola los canales ausentes. El resumen compartible describe la medición sin afirmar la identidad del objeto.

## Recorrido y catálogo

- El reproductor avanza por los números de foto del 1 al 102. Los frames ausentes se muestran como huecos; importa `archivo;fecha_utc` si tienes fechas verificadas.
- El buscador SBDB compara nombres/designaciones conocidos con datos JPL, como diámetro reportado, clase orbital, acercamientos registrados, estado PHA y datos de descubrimiento cuando existen. La ficha no identifica automáticamente la imagen elegida.
- CNEOS muestra acercamientos terrestres próximos. Un acercamiento y la etiqueta PHA no significan que se espere un impacto; Sentry es la fuente de evaluación de riesgo.
- El asistente da respuestas breves y educativas. La sección de misterios explica qué evidencia se necesita para evaluar una observación inusual.

## Límites científicos

- PNG/JPEG pueden contener valores transformados para visualización; no necesariamente flujo calibrado. La gráfica los llama **señal digital sin calibrar**.
- Sin datos de banda, el eje usa el número de foto, no nanómetros. Una imagen brillante no determina si lo que hay es una estrella, planeta, asteroide o artefacto.
- La fecha escrita en un CSV es aportada por el usuario y debe corresponder al registro original. Sin fecha, WCS/coordenadas y escala angular no se calcula velocidad física, tamaño, órbita ni riesgo de impacto.
- El registro automático estima traslación; no corrige rotación, distorsión ni diferencias de WCS. Revisa las alertas que genera.
- Para análisis cuantitativo SPHEREx, usa los FITS científicos y sus extensiones de calibración, WCS-WAVE y calidad. Las imágenes PNG de HiPS son para visualización. La longitud de onda de productos Level 2 puede depender de la posición en el detector.

Fuentes: [NASA/JPL Close-Approach API](https://ssd-api.jpl.nasa.gov/doc/cad.html), [NASA/JPL SBDB](https://ssd-api.jpl.nasa.gov/doc/sbdb.html), [SPHEREx Explanatory Supplement](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp.pdf), [SPHEREx Data Access](https://irsa.ipac.caltech.edu/spherex-archive-documentation/spherex-data-access/). No afiliado a NASA.
