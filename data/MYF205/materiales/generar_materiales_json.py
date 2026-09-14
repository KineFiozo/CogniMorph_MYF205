#!/usr/bin/env python3
"""
Genera (o completa) materiales.json a partir de los archivos de audio/video que ya están en
ESTA carpeta ("materiales"). Pensado para correr cada vez que agregues archivos nuevos, para no
tener que escribir a mano cada nombre de archivo en el JSON — que es justo la fuente más probable
de errores de tipeo (y por eso el tutor no encuentra el material).

FORMATO DE NOMBRE RECOMENDADO (léelo antes de renombrar tus archivos)
----------------------------------------------------------------------
    HitoN - Tipo - Tema.ext

- N: número de hito/clase (1, 2, 3...), sin ceros ni espacios.
- Tipo: exactamente una de estas tres palabras, sin tildes: Cancion | Podcast | Video.
- Tema: el tema real de esa clase, escrito tal como quieres que el tutor lo mencione (con tildes
  está bien; evita usar " - " dentro del tema para no confundir el separador).

Ejemplo:
    Hito1 - Cancion - Neurocraneo (foramenes y nervio trigemino).mp3
    Hito1 - Podcast - Arquitectura osea del craneo.m4a
    Hito1 - Video - Neurocraneo (foramenes y nervio trigemino).mp4

Con este formato el script toma el "tema" TAL CUAL lo escribiste en el nombre — cero adivinanza
de mi parte, así que es la vía más confiable. Si todavía tienes archivos con el nombre viejo (sin
este formato), el script los sigue aceptando con una heurística de respaldo (ver más abajo), pero
te va a avisar cuáles deberías revisar a mano.

CÓMO USARLO
-----------
1. Copia este archivo DENTRO de la carpeta "materiales" de la asignatura que quieras procesar
   (junto a tus mp3/m4a/mp4 y al LEEME_materiales.md), por ejemplo:
   data/MYF205/materiales/generar_materiales_json.py

2. Desde una terminal, parado en esa carpeta, corre:
       python generar_materiales_json.py
   (si "python" no se reconoce en PowerShell, invoca con la ruta completa al archivo, ej.:
       python "C:\\ruta\\a\\data\\MYF205\\materiales\\generar_materiales_json.py")

QUÉ HACE
--------
- Lee todos los .mp3/.m4a/.wav/.ogg/.mp4/.mov/.webm que encuentra en esta misma carpeta.
- Si un archivo YA tiene una entrada en materiales.json (por nombre de archivo), NO LA TOCA —
  así no se pisan ediciones tuyas de tema/titulo/descripcion.
- Si un archivo sigue el formato "HitoN - Tipo - Tema.ext": arma la entrada completa (id, tipo,
  tema, título) directamente del nombre, sin adivinar nada.
- Si un archivo NO sigue ese formato: intenta una heurística de respaldo (busca "Clase N" en el
  nombre y palabras clave "podcast"/"video"/"canción"), y dejará el "tema" vacío si no logra
  deducirlo — revísalo tú después.
- Guarda materiales.json (un nivel arriba de esta carpeta, junto a cronograma.json) y muestra un
  resumen: cuántas entradas nuevas se generaron con el formato estándar (confiables) y cuántas con
  la heurística de respaldo (para revisar a mano).

DESPUÉS DE CORRERLO: abre materiales.json. Las entradas generadas con el formato estándar deberían
estar listas; las de la heurística de respaldo, revísalas (sobre todo "tema" y "descripcion").
"""
import json
import os
import re
import unicodedata

EXTENSIONES_AUDIO = (".mp3", ".m4a", ".wav", ".ogg")
EXTENSIONES_VIDEO = (".mp4", ".mov", ".webm")
EXTENSIONES_VALIDAS = EXTENSIONES_AUDIO + EXTENSIONES_VIDEO

PATRON_ESTANDAR = re.compile(
    # "Hito[cualquier letra opcional, ej. la 'N' de la plantilla]NÚMERO - Tipo - Tema"
    # (tolera que alguien haya escrito literalmente "HitoN1" en vez de "Hito1" — la "N" de mi
    # plantilla "HitoN - Tipo - Tema" se prestaba para esa confusión).
    r"^\s*Hito[a-zA-Z]*\s*(\d+)\s*-\s*(Cancion|Canci[oó]n|Podcast|V[ií]deo|Video)\s*-\s*(.+?)\s*$",
    re.IGNORECASE,
)

# Heurística de respaldo SOLO para archivos que todavía no siguen el formato estándar de arriba.
# Los dos últimos (6 y 7) son los menos seguros porque nombres como "Conexiones"/"Encéfalo" no
# calzan literal con el cronograma — mejor renombrar esos archivos al formato estándar que confiar
# en esta tabla. Ajústala si la necesitas para otro caso.
TEMA_POR_CLASE_MYF205_RESPALDO = {
    1: "Osteología y artrología del neurocráneo — forámenes de la base del cráneo y nervio trigémino",
    2: "Osteología y artrología del viscerocráneo",
    3: "Cavidad oral",
    4: "Cavidad nasal y fosas nasales",
    5: "Miología de cabeza — músculos masticatorios",
}


def quitar_acentos(texto):
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def normalizar_tipo(palabra):
    p = quitar_acentos(palabra).lower()
    if p.startswith("cancion"):
        return "cancion"
    if p.startswith("podcast"):
        return "podcast"
    if p.startswith("video"):
        return "video"
    return "cancion"


def slug(texto):
    base = quitar_acentos(texto).lower()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return base or "material"


def etiqueta_tipo(tipo):
    return {"cancion": "Canción", "podcast": "Podcast", "video": "Video"}.get(tipo, tipo.capitalize())


def analizar_formato_estandar(nombre_archivo):
    """Si el archivo sigue 'HitoN - Tipo - Tema.ext', devuelve (numero_hito, tipo, tema);
    si no, devuelve None."""
    base = os.path.splitext(nombre_archivo)[0]
    m = PATRON_ESTANDAR.match(base)
    if not m:
        return None
    numero_hito = int(m.group(1))
    tipo = normalizar_tipo(m.group(2))
    tema = m.group(3).strip()
    return numero_hito, tipo, tema


def analizar_formato_heredado(nombre_archivo, extension, cod_materia):
    """Heurística de respaldo para archivos que no siguen el formato estándar todavía."""
    n = nombre_archivo.lower()
    if "podcast" in n:
        tipo = "podcast"
    elif "video" in n or extension in EXTENSIONES_VIDEO:
        tipo = "video"
    elif "cancion" in n or "canto" in n or "canción" in nombre_archivo.lower():
        tipo = "cancion"
    else:
        tipo = "cancion"

    tema = ""
    m = re.search(r"clase\s*(\d+)", nombre_archivo, re.IGNORECASE)
    if m and cod_materia == "MYF205":
        tema = TEMA_POR_CLASE_MYF205_RESPALDO.get(int(m.group(1)), "")

    titulo = os.path.splitext(nombre_archivo)[0].replace("_", " ").strip()
    return tipo, tema, titulo


def main():
    carpeta_materiales = os.path.dirname(os.path.abspath(__file__))
    carpeta_materia = os.path.dirname(carpeta_materiales)
    cod_materia = os.path.basename(carpeta_materia)
    ruta_json = os.path.join(carpeta_materia, "materiales.json")

    archivos_en_esta_carpeta = [
        f for f in os.listdir(carpeta_materiales) if f.lower().endswith(EXTENSIONES_VALIDAS)
    ]
    if not archivos_en_esta_carpeta:
        subcarpeta_materiales = os.path.join(carpeta_materiales, "materiales")
        print(f"⚠️  No encontré ningún .mp3/.m4a/.mp4/etc. en esta carpeta:\n    {carpeta_materiales}")
        if os.path.isdir(subcarpeta_materiales):
            print(
                "\nOjo: hay una carpeta llamada 'materiales' AQUÍ MISMO "
                f"({subcarpeta_materiales}) — es muy probable que este script haya quedado guardado\n"
                "un nivel más arriba de lo que corresponde (junto a cronograma.json, en vez de junto\n"
                "a tus archivos de audio/video). Mueve este .py DENTRO de esa carpeta 'materiales' y\n"
                "vuelve a correrlo — si sigues así, no se genera ningún materiales.json para no dejar\n"
                "un archivo vacío o mal ubicado."
            )
        else:
            print(
                "\nRevisa que hayas guardado este script en la misma carpeta donde están tus mp3/mp4\n"
                "(la carpeta 'materiales', junto al LEEME_materiales.md) y que los archivos ya estén\n"
                "copiados ahí. No se generó ningún materiales.json para no dejar un archivo vacío."
            )
        return

    if os.path.exists(ruta_json):
        try:
            with open(ruta_json, "r", encoding="utf-8") as f:
                entradas = json.load(f)
            if not isinstance(entradas, list):
                raise ValueError("materiales.json no contiene una lista")
        except Exception as e:
            respaldo = ruta_json + ".respaldo"
            os.replace(ruta_json, respaldo)
            print(f"⚠️  materiales.json no se pudo leer ({e}); se guardó una copia en {respaldo} y se parte de una lista vacía.")
            entradas = []
    else:
        entradas = []

    archivos_ya_registrados = {e.get("archivo") for e in entradas if isinstance(e, dict)}
    ids_ya_usados = {e.get("id") for e in entradas if isinstance(e, dict)}

    archivos_en_carpeta = sorted(archivos_en_esta_carpeta)

    generadas_formato_estandar = []
    generadas_formato_heredado = []

    for archivo in archivos_en_carpeta:
        if archivo in archivos_ya_registrados:
            continue
        extension = os.path.splitext(archivo)[1].lower()

        resultado_estandar = analizar_formato_estandar(archivo)
        if resultado_estandar:
            numero_hito, tipo, tema = resultado_estandar
            id_generado = f"hito{numero_hito}_{tipo}"
            titulo = f"{etiqueta_tipo(tipo)}: {tema}"
        else:
            tipo, tema, titulo = analizar_formato_heredado(archivo, extension, cod_materia)
            id_generado = slug(os.path.splitext(archivo)[0])

        sufijo = 2
        id_final = id_generado
        while id_final in ids_ya_usados:
            id_final = f"{id_generado}_{sufijo}"
            sufijo += 1
        ids_ya_usados.add(id_final)

        entradas.append({
            "id": id_final,
            "tipo": tipo,
            "archivo": archivo,
            "tema": tema,
            "titulo": titulo,
            "descripcion": "",
        })

        if resultado_estandar:
            generadas_formato_estandar.append(archivo)
        else:
            generadas_formato_heredado.append(archivo)

    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump(entradas, f, indent=4, ensure_ascii=False)

    total_nuevas = len(generadas_formato_estandar) + len(generadas_formato_heredado)
    print(f"Listo. {total_nuevas} entrada(s) nueva(s) agregada(s) a materiales.json "
          f"(de {len(archivos_en_carpeta)} archivos encontrados en esta carpeta).")

    if generadas_formato_estandar:
        print(f"\n✅ {len(generadas_formato_estandar)} generada(s) desde el formato estándar "
              "(HitoN - Tipo - Tema) — el 'tema' se tomó tal cual del nombre, deberían estar listas:")
        for a in generadas_formato_estandar:
            print(f"  - {a}")

    if generadas_formato_heredado:
        print(f"\n⚠️  {len(generadas_formato_heredado)} generada(s) con la heurística de respaldo "
              "(no seguían el formato estándar) — revisa 'tema' y 'descripcion' a mano, o mejor "
              "renombra el archivo al formato HitoN - Tipo - Tema y vuelve a correr el script:")
        for a in generadas_formato_heredado:
            print(f"  - {a}")

    faltan_tema = [e["archivo"] for e in entradas if isinstance(e, dict) and not e.get("tema")]
    if faltan_tema:
        print("\nEstos archivos quedaron con 'tema' vacío (complétalo para que el tutor sepa cuándo ofrecerlos):")
        for a in faltan_tema:
            print(f"  - {a}")


if __name__ == "__main__":
    main()
