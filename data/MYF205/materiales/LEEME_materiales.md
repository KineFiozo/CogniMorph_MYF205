# Materiales de apoyo (audio/video) — cómo agregarlos

1. Copia tus archivos de audio o video a esta misma carpeta ("materiales").
   Formatos soportados: .mp3, .m4a, .wav, .ogg (audio) y .mp4, .mov, .webm (video).

2. Abre el archivo "materiales.json" (un nivel arriba, junto a cronograma.json) y agrega una
   entrada por cada archivo, siguiendo este formato (puedes copiar y adaptar este ejemplo):

[
  {
    "id": "podcast_potencial_accion",
    "tipo": "podcast",
    "archivo": "podcast_potencial_accion.m4a",
    "tema": "Potencial de acción",
    "titulo": "Podcast: cómo se genera el potencial de acción",
    "descripcion": "Resumen conversacional de 8 minutos sobre las fases del potencial de acción."
  }
]

Campos:
- "id": identificador único, sin espacios ni tildes (lo usa el tutor internamente; el estudiante
  nunca lo ve). No lo repitas entre materiales.
- "tipo": "cancion", "podcast" o "video" — libre, solo ayuda al tutor a nombrarlo bien al
  ofrecerlo (ej. "tienes una canción..." vs "tienes un video...").
- "archivo": el nombre EXACTO del archivo dentro de esta carpeta "materiales" (no la ruta
  completa, solo el nombre del archivo).
- "tema": palabra o frase clave del contenido al que corresponde. El tutor lo usa para saber
  cuándo ofrecerlo — mientras más se parezca a como nombras los temas en tus clases, mejor.
- "titulo" y "descripcion": lo que el tutor le muestra al estudiante cuando lo ofrece.

El tutor SOLO puede ofrecer materiales que estén en materiales.json — nunca inventa uno. Si un
archivo no existe o el "archivo" no coincide con el nombre real, el material simplemente no se
podrá reproducir (se avisa en el chat, no rompe la app).

Nota de compatibilidad: los .m4a se reproducen bien en Chrome, Edge y Safari; en Firefox pueden
dar problemas según el códec — si buscas máxima compatibilidad de audio, mp3 es más seguro.
