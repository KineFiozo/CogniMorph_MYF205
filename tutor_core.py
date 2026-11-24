import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel
from pypdf import PdfReader

# Cuántos turnos previos (usuario + tutor) se reenvían como contexto de la conversación.
# Suficiente para que el tutor recuerde el hilo de un mismo tema sin disparar el costo/tamaño de la consulta.
MAX_TURNOS_HISTORIAL = 12

# ---------------------------------------------------------------------------------------------
# FECHA ACTUAL + ESTADO DEL CALENDARIO (determinista, calculado en código)
# ---------------------------------------------------------------------------------------------
# Problema que resuelve: un LLM no sabe qué día es hoy. Si solo recibe el cronograma completo,
# responde "la evaluación que corresponde ahora" con la primera que ve (CAT 1, en septiembre).
# Igual que con las clases y los materiales, NO se le deja deducir esto "de memoria": el código
# calcula qué evaluaciones ya pasaron, cuáles son de esta semana y cuáles vienen, y se lo
# entrega ya etiquetado. Se recalcula en CADA consulta (nada cacheado), así que siempre está al día.

_DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_NOMBRE = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                 "septiembre", "octubre", "noviembre", "diciembre"]
_MESES_ABREV = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7,
                "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}

# Qué filas del cronograma cuentan como "evaluación" (el resto: clases, talleres, consolidación).
_PATRON_EVAL = re.compile(
    r"^\s*(c[aá]tedra\s*\d|cat\s+[ivx\d]+|epe?oe|ej\d|ejercicio\s*\d|examen|prueba\s+recuperativa"
    r"|evaluaciones?\s+recuperativas?|avance\s+de\s+p[oó]ster|control|solemne|taller\s+pr[aá]ctico)",
    re.IGNORECASE,
)


def fecha_hoy_chile():
    """Fecha de hoy en hora de Chile (el servidor de Streamlit Cloud corre en UTC)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Santiago")).date()
    except Exception:
        # Sin base de zonas horarias (ej. Windows sin 'tzdata'): UTC-3 (horario de verano chileno).
        return datetime.now(timezone(timedelta(hours=-3))).date()


def _fmt_fecha(d):
    return f"{_DIAS_SEMANA[d.weekday()]} {d.day} de {_MESES_NOMBRE[d.month - 1]} de {d.year}"


def _fmt_corta(d):
    return f"{d.day:02d}-{d.month:02d}-{d.year}"


def _parse_dmy(texto):
    """'12-10-2026' -> date. None si no calza."""
    m = re.match(r"^\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})\s*$", str(texto or ""))
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _mes_num(token):
    return _MESES_ABREV.get(str(token or "").strip().lower()[:3])


def _parse_rango(texto, anio):
    """
    Fechas escritas como en los cronogramas por semana: '31 Ago-4 Sep', '3-7 Ago', '13 Dic',
    '12 de Septiembre'. Devuelve (inicio, fin) como date, o None si no se entiende.
    """
    t = str(texto or "").strip()
    try:
        m = re.match(r"^(\d{1,2})\s*(?:de\s+)?([A-Za-zñÑ]+)?\s*[-–]\s*(\d{1,2})\s*(?:de\s+)?([A-Za-zñÑ]+)$", t)
        if m:
            mes_fin = _mes_num(m.group(4))
            mes_ini = _mes_num(m.group(2)) or mes_fin
            if mes_ini and mes_fin:
                return date(anio, mes_ini, int(m.group(1))), date(anio, mes_fin, int(m.group(3)))
        m = re.match(r"^(\d{1,2})\s*(?:de\s+)?([A-Za-zñÑ]+)$", t)
        if m and _mes_num(m.group(2)):
            d = date(anio, _mes_num(m.group(2)), int(m.group(1)))
            return d, d
    except ValueError:
        pass
    return None


def _anio_del_cronograma(cronograma, hoy):
    meta = cronograma.get("meta", {}) if isinstance(cronograma, dict) else {}
    periodo = str(meta.get("periodo", ""))
    if re.match(r"^20\d\d", periodo):
        return int(periodo[:4])
    # Año suelto en el nombre del archivo ("... 2026.xlsx"). OJO: no debe confundirse con el código
    # de la asignatura ("MYF2069"), por eso se exige que no esté pegado a otra letra o dígito.
    m = re.search(r"(?<![A-Za-z0-9])(20\d\d)(?!\d)", str(meta.get("fuente_original", "")))
    anio = int(m.group(1)) if m else hoy.year
    return anio if abs(anio - hoy.year) <= 1 else hoy.year


def extraer_eventos_calendario(cronograma, hoy):
    """
    Recorre el cronograma (cualquiera de los formatos que usa CogniMorph) y devuelve:
      - eventos: lista de {'nombre', 'inicio', 'fin'} SOLO de evaluaciones/entregas, con fechas reales
      - sesiones: lista de {'inicio', 'fin', 'texto'} con lo que toca cada semana (clases/talleres)
    """
    eventos, sesiones = [], []
    anio = _anio_del_cronograma(cronograma, hoy)

    # Formato A (MYF205): {"dias": [{"fecha": "03-08-2026", "catedra": {...}, "taller": {...}}]}
    if isinstance(cronograma, dict) and isinstance(cronograma.get("dias"), list):
        for dia in cronograma["dias"]:
            d = _parse_dmy(dia.get("fecha"))
            if not d:
                continue
            partes = []
            for clave, etiqueta in (("catedra", "Cátedra"), ("taller", "Taller")):
                bloque = dia.get(clave) or {}
                cont = (bloque.get("contenido") or "").strip()
                if cont:
                    partes.append(f"{etiqueta}: {cont}")
                    if _PATRON_EVAL.search(cont):
                        eventos.append({"nombre": f"{cont} ({etiqueta.lower()})", "inicio": d, "fin": d})
                hito = (bloque.get("hito") or "").strip()
                if hito:
                    partes.append(f"Hito de taller: {hito}")
                    eventos.append({"nombre": f"{hito} (taller)", "inicio": d, "fin": d})
            sesiones.append({"inicio": d, "fin": d + timedelta(days=6), "texto": " | ".join(partes)})

    # Formato B (MYF2069): {"semanas": [{"fecha": "31 Ago-4 Sep", "bloque_especial": ..., ...}]}
    elif isinstance(cronograma, dict) and isinstance(cronograma.get("semanas"), list):
        for sem in cronograma["semanas"]:
            rango = _parse_rango(sem.get("fecha"), anio)
            if not rango:
                continue
            ini, fin = rango
            especial = (sem.get("bloque_especial") or "").strip()
            cat = sem.get("catedra") or []
            tal = sem.get("taller") or []
            partes = []
            if especial:
                partes.append(especial)
            if cat:
                partes.append("Cátedra: " + "; ".join(cat))
            if tal:
                partes.append("Taller: " + "; ".join(tal))
            if especial and _PATRON_EVAL.search(especial):
                eventos.append({"nombre": especial, "inicio": ini, "fin": fin})
            for t in tal:
                if _PATRON_EVAL.search(t):
                    eventos.append({"nombre": f"{t} (taller)", "inicio": ini, "fin": fin})
            sesiones.append({"inicio": ini, "fin": fin, "texto": " | ".join(partes)})

    # Formato C (maqueta por defecto): [{"evento": "Control 1: ...", "fecha": "12 de Septiembre"}]
    elif isinstance(cronograma, list):
        for ev in cronograma:
            if not isinstance(ev, dict):
                continue
            rango = _parse_rango(ev.get("fecha"), anio)
            if rango and ev.get("evento"):
                eventos.append({"nombre": ev["evento"], "inicio": rango[0], "fin": rango[1]})

    eventos.sort(key=lambda e: (e["inicio"], e["nombre"]))
    return eventos, sesiones


def _rango_txt(ev):
    if ev["inicio"] == ev["fin"]:
        return _fmt_corta(ev["inicio"])
    return f"semana del {_fmt_corta(ev['inicio'])} al {_fmt_corta(ev['fin'])}"


def bloque_fecha_actual(cronograma=None, hoy=None):
    """
    Texto listo para el system prompt: fecha de hoy + qué evaluaciones ya pasaron, cuáles son
    de esta semana y cuáles vienen, calculado en código a partir del cronograma.
    """
    hoy = hoy or fecha_hoy_chile()
    lineas = [
        "FECHA ACTUAL (autoridad absoluta para 'hoy', 'ahora', 'esta semana', 'la próxima'; "
        "calculada por el sistema, hora de Chile): "
        f"hoy es {_fmt_fecha(hoy)}."
    ]
    if not cronograma:
        return "\n".join(lineas)

    try:
        eventos, sesiones = extraer_eventos_calendario(cronograma, hoy)
    except Exception:
        return "\n".join(lineas)

    if sesiones:
        actual = [s for s in sesiones if s["inicio"] <= hoy <= s["fin"]]
        if actual:
            lineas.append(f"SEMANA EN CURSO según el cronograma: {actual[-1]['texto']}")
        elif hoy < sesiones[0]["inicio"]:
            lineas.append(f"El semestre aún no parte (primera sesión: {_fmt_corta(sesiones[0]['inicio'])}).")
        elif hoy > sesiones[-1]["fin"]:
            lineas.append("El cronograma del semestre ya terminó (no hay más sesiones programadas).")

    if eventos:
        pasadas = [e for e in eventos if e["fin"] < hoy]
        vigentes = [e for e in eventos if e["inicio"] <= hoy <= e["fin"]]
        proximas = [e for e in eventos if e["inicio"] > hoy]
        if pasadas:
            lineas.append("EVALUACIONES YA REALIZADAS (fecha pasada): "
                          + "; ".join(f"{e['nombre']} — {_rango_txt(e)}" for e in pasadas))
        if vigentes:
            lineas.append("EVALUACIONES DE ESTA SEMANA / HOY: "
                          + "; ".join(f"{e['nombre']} — {_rango_txt(e)}" for e in vigentes))
        if proximas:
            lineas.append("PRÓXIMAS EVALUACIONES (en orden cronológico): "
                          + "; ".join(f"{e['nombre']} — {_rango_txt(e)}" for e in proximas))
        elif not vigentes:
            lineas.append("NO quedan evaluaciones futuras en el cronograma.")

    lineas.append(
        "REGLAS DE FECHA: (1) Cuando el estudiante hable de 'la evaluación que toca ahora', 'la "
        "próxima', 'esta semana' u 'hoy', responde SOLO con las listas de arriba; nunca presentes "
        "como próxima ni como vigente una evaluación que figura en 'YA REALIZADAS'. (2) Usa el "
        "nombre de cada evaluación tal como aparece en el cronograma; no lo conviertas en un nombre "
        "de CONTENIDOS_POR_EVALUACION (CAT 1, CAT 2...) salvo que el nombre calce exactamente. "
        "(3) Si no hay ninguna lista aplicable, dilo en vez de adivinar. (4) No inventes fechas."
    )
    return "\n".join(lineas)


def _enriquecer_materiales(materiales, cronograma=None):
    """
    Arma las fichas que ve el modelo agregando, de forma automática, el contexto que a las fichas
    les falta: a qué Hito pertenece cada audio/video, de qué clase es (según el cronograma) y cuál
    es el tema real del Hito. Sin esto, una canción o un video con título corto ("Conexiones",
    "Encéfalo") no "calzaba" con la conversación y solo se ofrecía el podcast, que sí tiene un
    título descriptivo — aunque los tres sean formatos del MISMO tema.
    """
    fichas = [m for m in materiales if m.get("id") and m.get("existe")]

    def _hito(m):
        r = re.match(r"^hito\s*_?\s*(\d+)", str(m.get("id", "")), re.IGNORECASE)
        return int(r.group(1)) if r else None

    # tema descriptivo de cada Hito: el del podcast (suele ser el más explícito), si no el primero.
    temas_hito = {}
    for m in fichas:
        h = _hito(m)
        if h is None:
            continue
        if h not in temas_hito or (m.get("tipo") == "podcast" and m.get("tema")):
            temas_hito[h] = m.get("tema") or temas_hito.get(h)

    def _clase_de(h):
        """'U4:C6: Neuroanatomía I' -> 'Clase 6: Neuroanatomía I' (solo si el cronograma la trae)."""
        if not isinstance(cronograma, dict):
            return None
        patron = re.compile(r"(?<![0-9A-Za-z])C%d\s*[:.]\s*(.+)" % h)
        textos = []
        for dia in cronograma.get("dias", []) or []:
            textos.append(((dia.get("catedra") or {}).get("contenido")) or "")
        for sem in cronograma.get("semanas", []) or []:
            textos.extend(sem.get("catedra") or [])
        for t in textos:
            r = patron.search(str(t))
            if r:
                return f"Clase {h}: {r.group(1).strip()}"
        return None

    resumen = []
    for m in fichas:
        h = _hito(m)
        ficha = {
            "id": m.get("id"),
            "tipo": m.get("tipo"),
            "tema": m.get("tema"),
            "titulo": m.get("titulo"),
            "descripcion": m.get("descripcion"),
        }
        if h is not None:
            ficha["hito"] = h
            ficha["tema_del_hito"] = temas_hito.get(h)
            clase = _clase_de(h)
            if clase:
                ficha["clase_asociada"] = clase
        resumen.append(ficha)
    return resumen


# ---------------------------------------------------------------------------------------------
# PRUEBAS: ALCANCE (clase / unidad) Y CANTIDAD DE PREGUNTAS — decididos en código, no por el modelo
# ---------------------------------------------------------------------------------------------
# Regla del profesor (no depende de que el modelo la recuerde):
#   - Prueba de UNA clase:        10 preguntas de selección múltiple + 10 términos pareados.
#   - Prueba de TODA la unidad:   24 de selección múltiple (repartidas por igual entre las clases
#                                 de la unidad) + 10 términos pareados (con términos de todas las
#                                 clases de la unidad).
#   - Varias clases sueltas (ej. "clase 6 y 7") o una evaluación (ej. "CAT 2"): mismo formato que la
#     unidad (24 + 10), repartido entre las clases involucradas.
PREGUNTAS_PRUEBA_CLASE = 10
PAREADOS_PRUEBA_CLASE = 10
PREGUNTAS_PRUEBA_UNIDAD = 24
PAREADOS_PRUEBA_UNIDAD = 10

_ROMANOS = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8}

# Palabras del pedido en sí (no del tema) que se ignoran al buscar de qué clase habla el estudiante.
_PALABRAS_NO_TEMA = {
    "control", "controles", "quiz", "prueba", "pruebas", "test", "evaluacion", "autoevaluacion",
    "quiero", "quisiera", "hacer", "hazme", "dame", "armame", "arma", "realizar", "sobre", "para",
    "esta", "este", "esto", "todo", "toda", "todos", "todas", "unidad", "unidades", "clase", "clases",
    "vista", "visto", "vistas", "vistos", "ahora", "hasta", "tema", "temas", "una", "uno", "unos",
    "unas", "del", "los", "las", "por", "favor", "porfa", "con", "que", "como", "mas", "pero", "algo",
    "alguna", "alguno", "particular", "completa", "completo", "entera", "entero", "cual", "cuales",
    "mejor", "gustaria", "puedes", "podrias", "podemos", "vamos", "dale", "ahi", "aqui",
}


def _norm(texto):
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _tallos(texto):
    """Conjunto de 'raíces' (primeras 6 letras) de las palabras con contenido de un texto."""
    out = set()
    for w in re.findall(r"[a-z]{4,}", _norm(texto)):
        if w in _PALABRAS_NO_TEMA:
            continue
        out.add(w[:6])
    return out


def estructura_unidades(cronograma):
    """
    Lee el cronograma (formato MYF205 con 'dias' o formato MYF2069 con 'semanas') y devuelve las
    unidades con sus clases: [{'numero': 4, 'etiqueta': 'Unidad 4',
    'clases': [{'n': 6, 'nombre': 'Neuroanatomía I'}, ...]}, ...]. Lista vacía si no se puede leer.
    """
    unidades = {}

    def _agregar(u, n, nombre):
        if u is None or n is None:
            return
        clases = unidades.setdefault(u, {})
        clases.setdefault(n, (nombre or "").strip())

    if isinstance(cronograma, dict) and isinstance(cronograma.get("dias"), list):
        for dia in cronograma["dias"]:
            cont = ((dia.get("catedra") or {}).get("contenido")) or ""
            m = re.search(r"U\s*(\d+)\s*:\s*C\s*(\d+)\s*[:.]\s*(.+)", cont)
            if m:
                _agregar(int(m.group(1)), int(m.group(2)), m.group(3))
    elif isinstance(cronograma, dict) and isinstance(cronograma.get("semanas"), list):
        for sem in cronograma["semanas"]:
            u_txt = str(sem.get("unidad") or "").strip().lower()
            u = _ROMANOS.get(u_txt) or (int(u_txt) if u_txt.isdigit() else None)
            for item in sem.get("catedra") or []:
                m = re.match(r"^\s*C\s*(\d+)\s*[:.]\s*(.+)", str(item))
                if m:
                    _agregar(u, int(m.group(1)), m.group(2))

    return [
        {
            "numero": u,
            "etiqueta": f"Unidad {u}",
            "clases": [{"n": n, "nombre": nom} for n, nom in sorted(unidades[u].items())],
        }
        for u in sorted(unidades)
    ]


def _todas_las_clases(unidades):
    return [c for u in unidades for c in u["clases"]]


def interpretar_alcance(texto, unidades):
    """
    A partir de lo que escribió el estudiante, decide el alcance de la prueba. Devuelve un dict:
      {'modo': 'clase' | 'unidad' | 'varias' | 'ambiguo' | 'unidad_sin_numero' | 'indefinido',
       'clases': [n, ...], 'unidad': numero|None, 'candidatas': [n, ...]}
    No adivina: si no está claro (o hay empate entre clases), devuelve 'ambiguo'/'indefinido' para
    que la app le pregunte al estudiante en vez de elegir por él.
    """
    vacio = {"modo": "indefinido", "clases": [], "unidad": None, "candidatas": []}
    if not unidades:
        return vacio
    t = _norm(texto)
    todas = _todas_las_clases(unidades)
    numeros_validos = {c["n"] for c in todas}

    # 1) "unidad 4" / "unidad IV"
    m = re.search(r"\bunidad(?:es)?\s*(?:n[o°º]?\s*)?(\d+|[ivx]+)\b", t)
    if m:
        tok = m.group(1)
        num = int(tok) if tok.isdigit() else _ROMANOS.get(tok)
        for u in unidades:
            if u["numero"] == num:
                return {"modo": "unidad", "clases": [c["n"] for c in u["clases"]], "unidad": num, "candidatas": []}
        return {"modo": "ambiguo", "clases": [], "unidad": None, "candidatas": []}

    # 2) "clase 6", "clases 6 y 7", "hito 6", "c6"
    nums = []
    for g in re.finditer(r"\b(?:clases?|hitos?|c)\s*(?:n[o°º]?\s*)?((?:\d{1,2}\s*(?:,|y|e|-|a)?\s*)+)", t):
        nums += [int(x) for x in re.findall(r"\d{1,2}", g.group(1))]
    nums = [n for n in dict.fromkeys(nums) if n in numeros_validos]
    if len(nums) == 1:
        return {"modo": "clase", "clases": nums, "unidad": None, "candidatas": []}
    if len(nums) >= 2:
        return {"modo": "varias", "clases": sorted(nums), "unidad": None, "candidatas": []}

    # 3) "la unidad" / "toda la unidad" sin número
    if re.search(r"\bunidad(?:es)?\b", t):
        return {"modo": "unidad_sin_numero", "clases": [], "unidad": None, "candidatas": []}

    # 4) por nombre de clase ("cavidad nasal", "laringe", "fisiología renal")
    palabras = _tallos(texto)
    if palabras:
        puntajes = {c["n"]: len(palabras & _tallos(c["nombre"])) for c in todas}
        mejor = max(puntajes.values())
        if mejor > 0:
            ganadoras = sorted(n for n, p in puntajes.items() if p == mejor)
            if len(ganadoras) == 1:
                return {"modo": "clase", "clases": ganadoras, "unidad": None, "candidatas": []}
            return {"modo": "ambiguo", "clases": [], "unidad": None, "candidatas": ganadoras}
    return vacio


def clases_de_evaluacion(entrada_evaluacion, unidades):
    """
    Dada una entrada de contenidos_evaluaciones.json (ej. CAT 2 → 'U4: C6: Neuroanatomía I', ...),
    devuelve los números de clase involucrados (por 'C6' explícito o, si no, por nombre de clase).
    """
    todas = _todas_las_clases(unidades)
    validos = {c["n"] for c in todas}
    encontradas = []
    for item in (entrada_evaluacion or {}).get("contenidos", []) or []:
        m = re.search(r"\bC\s*(\d+)\b", str(item))
        if m and int(m.group(1)) in validos:
            encontradas.append(int(m.group(1)))
            continue
        palabras = _tallos(item)
        if palabras:
            puntajes = {c["n"]: len(palabras & _tallos(c["nombre"])) for c in todas}
            mejor = max(puntajes.values())
            ganadoras = [n for n, p in puntajes.items() if p == mejor]
            if mejor > 0 and len(ganadoras) == 1:
                encontradas.append(ganadoras[0])
    return sorted(dict.fromkeys(encontradas))


def _repartir(total, partes):
    """Reparte 'total' en 'partes' lo más parejo posible (el resto va a las primeras)."""
    base, resto = divmod(total, partes)
    return [base + (1 if i < resto else 0) for i in range(partes)]


def plan_control(alcance, unidades):
    """
    Convierte un alcance resuelto en el plan de la prueba: cuántas preguntas y términos en total, y
    cuántos le tocan a cada clase. Devuelve None si el alcance no está resuelto.
    """
    if not alcance or alcance.get("modo") not in ("clase", "unidad", "varias"):
        return None
    todas = {c["n"]: c for c in _todas_las_clases(unidades)}
    clases = [todas[n] for n in alcance["clases"] if n in todas]
    if not clases:
        return None
    es_clase = alcance["modo"] == "clase" and len(clases) == 1
    n_preg = PREGUNTAS_PRUEBA_CLASE if es_clase else PREGUNTAS_PRUEBA_UNIDAD
    n_par = PAREADOS_PRUEBA_CLASE if es_clase else PAREADOS_PRUEBA_UNIDAD
    rp, rt = _repartir(n_preg, len(clases)), _repartir(n_par, len(clases))
    detalle = [
        {"n": c["n"], "nombre": c["nombre"], "preguntas": rp[i], "terminos": rt[i]}
        for i, c in enumerate(clases)
    ]
    if es_clase:
        etiqueta = f"la Clase {clases[0]['n']} ({clases[0]['nombre']})"
    elif alcance["modo"] == "unidad":
        etiqueta = f"la Unidad {alcance['unidad']} (" + ", ".join(f"Clase {c['n']}" for c in clases) + ")"
    else:
        etiqueta = "las clases " + ", ".join(str(c["n"]) for c in clases)
    return {
        "modo": "clase" if es_clase else "unidad",
        "n_preguntas": n_preg,
        "n_pareados": n_par,
        "clases": detalle,
        "etiqueta": etiqueta,
    }


def texto_pregunta_alcance(unidades, aclaracion=None):
    """Pregunta (en tono cercano) de qué clase o unidad quiere la prueba, con las opciones reales."""
    lineas = []
    if aclaracion:
        lineas.append(aclaracion)
    else:
        lineas.append("¡Buena idea ponerse a prueba! 🎯 Para armártela justa, cuéntame: ¿la quieres de "
                      "**toda una unidad** o de **una clase en particular**?")
    if unidades:
        lineas.append("\nAsí está la asignatura:")
        for u in unidades:
            cl = "; ".join(f"Clase {c['n']} ({c['nombre']})" for c in u["clases"])
            lineas.append(f"- **{u['etiqueta']}**: {cl}")
    lineas.append(
        f"\nSi es de una clase, serán **{PREGUNTAS_PRUEBA_CLASE} preguntas de selección múltiple + "
        f"{PAREADOS_PRUEBA_CLASE} términos pareados**. Si es de toda la unidad, **{PREGUNTAS_PRUEBA_UNIDAD} "
        f"preguntas de selección múltiple + {PAREADOS_PRUEBA_UNIDAD} términos pareados**. "
        "Respóndeme, por ejemplo, \"Unidad 4\" o \"Clase 6\"."
    )
    return "\n".join(lineas)


def texto_aclaracion_alcance(alcance, unidades):
    """Mensaje para pedirle al estudiante que precise el alcance cuando no quedó claro."""
    modo = (alcance or {}).get("modo")
    if modo == "ambiguo" and alcance.get("candidatas"):
        todas = {c["n"]: c for c in _todas_las_clases(unidades)}
        opciones = "; ".join(
            f"Clase {n} ({todas[n]['nombre']})" for n in alcance["candidatas"] if n in todas
        )
        return (
            f"Con eso me calzan varias clases: {opciones}. ¿Cuál de ellas quieres? "
            "También puedo armarte la prueba de toda la unidad si prefieres (dime \"Unidad N\")."
        )
    if modo == "unidad_sin_numero":
        return texto_pregunta_alcance(
            unidades, aclaracion="¿De qué unidad la quieres? Dime el número, por ejemplo \"Unidad 4\"."
        )
    if modo == "ambiguo":
        return texto_pregunta_alcance(
            unidades, aclaracion="Esa unidad o clase no la encuentro en el cronograma. 🤔 Elige una de estas:"
        )
    return texto_pregunta_alcance(unidades)


PROMPT_PEDAGOGICO = """
Eres 'TutorSocrático', pero pensado como ese compañero de curso que se sabe la materia increíble y
al que todos le preguntan porque explica bien y da gusto conversar con él — no como un profesor que
evalúa cada respuesta. Prioridad número uno: que el estudiante NO pierda las ganas de volver a
preguntarte. Si en algún momento dudas entre "seguir siendo socrático" o "que no se aburra y avance",
gana lo segundo.

CÓMO CONVERSAR:

0. PREGUNTAS PURAMENTE INFORMATIVAS: si la consulta es administrativa o de dato puntual (fechas,
   definiciones simples, aclaraciones de logística), respóndela directo, sin rodeos ni preguntas de
   vuelta. Hay DOS fuentes distintas para esto, y NO se correlacionan directamente entre sí (usan
   agrupaciones y nombres distintos, así que nunca asumas que "Control 1" del cronograma es lo
   mismo que "CAT 1" de los contenidos, ni ningún otro cruce parecido, salvo que el nombre calce
   exactamente):
   - FECHAS (cuándo es cada evaluación, ponderación general del cronograma): básate ESTRICTAMENTE
     en el CRONOGRAMA OFICIAL que se te entrega más abajo — no inventes ni asumas fechas que no
     estén ahí.
   - "HOY" Y "AHORA": el bloque FECHA ACTUAL (al inicio de estas instrucciones) trae la fecha real de
     hoy y ya clasifica las evaluaciones en realizadas / de esta semana / próximas. Úsalo SIEMPRE
     que el estudiante pregunte por la evaluación que toca ahora, la próxima, lo que viene esta
     semana o cuánto falta. NO recorras el cronograma completo para deducirlo tú, y NO empieces
     por la primera evaluación del semestre: si una evaluación ya pasó, di que ya se realizó.
   - CONTENIDOS POR EVALUACIÓN (qué unidades/temas entran en cada CAT, EPOE, Ejercicio, etc.):
     básate ESTRICTAMENTE en el bloque CONTENIDOS_POR_EVALUACION que se te entrega más abajo (si
     aparece) — es la fuente oficial para esto, MÁS PRECISA que el cronograma para esta pregunta
     puntual. Si el estudiante pregunta por el contenido de una evaluación que no aparece ahí
     (o si el bloque no está disponible para esta asignatura), dilo con franqueza en vez de
     adivinar a partir del cronograma o del temario general.
   - Si el estudiante pide fecha Y contenido de la misma evaluación, responde cada parte con su
     propia fuente, usando el nombre que cada documento usa para esa evaluación (no los fusiones
     en un solo nombre inventado).

0.1 SOLICITUDES DE CONTROL/QUIZ/AUTOEVALUACIÓN — ATAJO PRIORITARIO (revisa esto ANTES de aplicar
   los puntos 1-4 de más abajo): si el estudiante pide practicar con preguntas, ponerse a prueba,
   un "control", "quiz", "prueba corta", "evaluación formativa", o cualquier pedido que suene a
   querer autoevaluarse (aunque no use ninguna de esas palabras exactas — ej. "quiero ver si me sé
   esto", "hazme preguntas para repasar"), NO entres al flujo normal de explicación (puntos 1-4): ve
   directo al punto 7 (MINI CONTROLES INTERACTIVOS) más abajo y sigue SUS reglas, no las de acá.
   - Si no especificó de qué clase(s) o tema(s) quiere el control (por ejemplo, si solo dijo "quiero
     hacer un control" porque venía del botón de la app), NO generes nada todavía: pregúntale de
     forma breve y concreta sobre qué clase o clases lo quiere — puedes ofrecerle 2-3 opciones de
     temas que hayan conversado como sugerencia, pero deja claro que puede elegir cualquier otra. El
     estudiante decide el alcance, no tú.
   - Una vez que el estudiante te confirme el/los tema(s), genera el control usando los bloques
     ```quiz```/```pareados``` del punto 7 — NUNCA como texto plano con numeración tipo "I. Selección
     múltiple", letras "a) b) c) d)" o una tabla "Columna A / Columna B": eso no es clickeable para
     el estudiante en esta app, por muy prolijo que se vea. Si estás armando un control, la única
     forma válida de entregarlo es con esos bloques.

1. SOCRÁTICO SOLO EN LA PRIMERA PREGUNTA DE CADA TEMA NUEVO: cuando el estudiante trae un tema o
   problema por primera vez, respóndele con UNA sola pregunta reflexiva, pista o analogía breve que
   lo haga pensar un segundo antes de la respuesta. Nada de baterías de preguntas ni de "¿y por qué
   crees que pasa eso?" encadenado varias veces — es solo un gesto inicial, no un interrogatorio.

2. DE AHÍ EN ADELANTE, EXPLICA DIRECTO: apenas el estudiante responde a esa primera pregunta —acierte,
   se equivoque, diga "no sé" o simplemente siga con el tema— pasas a explicar de verdad. No vuelvas a
   abrir el ciclo socrático sobre el mismo tema, no le pidas que siga adivinando ni le preguntes si
   quiere que le des la respuesta: la respuesta se la vas a dar sí o sí, lo único que preguntas es CÓMO
   se la explicas (ver punto 3).

3. AL EXPLICAR, PREGUNTA SOLO EL FORMATO — nunca si quiere o no la respuesta: ofrécele 2 o 3 formatos
   que realmente calcen con ESE contenido puntual (no repitas siempre el mismo menú genérico), por
   ejemplo:
   - Contenido que se compara o contrasta → tabla comparativa
   - Sistema con partes y relaciones (ej. una vía, un circuito, una cascada) → mapa conceptual descrito
     en texto/jerarquía
   - Tema amplio que hay que repasar completo → resumen tipo mini-informe
   - Algo que se entiende mejor anclado a la práctica → ejemplo clínico o cotidiano
   - Un proceso con etapas → paso a paso
   Pregúntaselo en tono natural y breve (ej. "¿te lo armo como tabla o prefieres un caso clínico para
   fijarlo?"), y si el estudiante ya adelantó el formato en su mensaje (ej. "explícamelo con un
   ejemplo"), no vuelvas a preguntar: entrega directo en ese formato.

   MATERIAL DE APOYO EN ESTA MISMA PREGUNTA (canal principal — más confiable que esperar al punto 4):
   antes de escribir esta pregunta de formato, revisa MATERIALES_DE_APOYO_DISPONIBLES (ver punto 6). Si
   hay uno o más ítems cuyo "tema" calza con lo que se está viendo Y todavía no los ofreciste en esta
   conversación, súmalo a LA MISMA pregunta, como una opción más de la lista (no como una pregunta
   aparte ni después de explicar) — de forma parecida a: "¿te lo armo como tabla o prefieres un caso
   clínico? y ya que estamos en esto: tengo un podcast, una canción y un video con la letra sobre este
   tema — ¿quieres que te muestre alguno mientras seguimos viendo el resto?". La idea es que el
   estudiante pueda pedir el formato de contenido Y el material en la misma respuesta, y seguir
   conversando contigo mientras el material se reproduce. Usa los "titulo" reales de la ficha si ayuda a
   que suene concreto en vez de genérico. Si no hay ningún ítem que calce (ver regla anti-invención del
   punto 6), simplemente pregunta el formato como siempre, sin mencionar materiales.

   OFERTA DE LA PRUEBA AL INICIO (en esta misma pregunta): justo DESPUÉS de ofrecer el material de apoyo
   (o, si no hay material que ofrecer, justo después de las opciones de formato), suma UNA frase breve y
   natural que avise que al final del estudio se puede hacer una prueba, del estilo: "Y al final, si
   quieres, también podemos hacer una prueba para saber cuánto hemos aprendido." Solo una vez por tema
   (no la repitas en cada mensaje) y sin preguntar todavía el alcance: esa pregunta va después de
   entregar el contenido (ver punto 4).

4. ENTREGA COMPLETA Y CORRECTA: una vez elegido el formato, da la explicación completa ahí mismo, sin
   cortarla ni esconder partes. RESPALDO — solo si el estudiante saltó la pregunta del punto 3 (por
   ejemplo, porque ya venía con el formato decidido en su mensaje, tipo "explícamelo con un ejemplo"),
   entonces no tuviste oportunidad de ofrecer el material ahí: en ese caso, inmediatamente después de
   entregar la explicación, EN ESE MISMO MENSAJE, revisa igual si hay un material de apoyo asociado (ver
   punto 6) y, si lo hay, ofrécelo ahí — no dejes pasar el turno esperando "otra oportunidad" para
   mencionarlo. Si ya lo ofreciste en la pregunta del punto 3, no lo vuelvas a ofrecer acá.

   CIERRE CON OFERTA DE PRUEBA (cuando entregues material que el estudiante pidió: mapa conceptual,
   esquema, resumen o mini-informe, tabla): al terminar la entrega, cierra con una pregunta natural y
   cercana, parecida a: "¿Quieres hacer una prueba para saber si hemos aprendido? ¿De toda la unidad o
   de alguna clase en particular?". Pregunta el alcance ahí mismo: de eso depende el formato (ver punto
   7). En ese mismo mensaje, en una línea sola al final, escribe exactamente [[OFERTA_PRUEBA]] (la app
   la oculta; le sirve para entender la respuesta del estudiante y armar la prueba). Hazlo una vez por
   entrega; si el estudiante ya dijo que no quiere la prueba, no insistas. Cuando el estudiante responda
   con el alcance, la app arma la prueba por su cuenta: en ese turno tú NO escribas preguntas ni bloques.

4.1 CÓMO ENTREGAR CADA FORMATO (importante para que se vea bien en la app):
   - "Tabla comparativa": usa una tabla en markdown normal (filas con | y una fila separadora con ---).
   - "Mapa conceptual", "esquema" o "diagrama de flujo": primero UNA frase breve de contexto (nada de
     relleno conversacional antes o después), y luego el diagrama como código Mermaid dentro de un
     bloque de código con la etiqueta mermaid, así:
     ```mermaid
     flowchart TD
         A["Concepto A"] --> B["Concepto B<br/>en dos líneas"]
     ```
     Reglas para que el diagrama se vea legible y no termine con letra microscópica:
     * Etiquetas CORTAS: si un texto de nodo supera ~4-5 palabras, córtalo con `<br/>` cada 3-4 palabras
       (ver ejemplo arriba) en vez de dejarlo como una sola línea larga.
     * MANTÉN EL DIAGRAMA COMPACTO A LO ANCHO: usa `flowchart TD` (vertical) como opción por defecto,
       incluso cuando una categoría tenga varios hijos directos. Como el color ya identifica a qué
       nivel pertenece cada nodo (ver COLOR POR NIVEL más abajo), NO necesitas que todos los nodos de
       un mismo nivel queden alineados en una sola fila perfecta ni muy separados entre sí para que se
       entiendan como del mismo rango — Mermaid ya los acomoda de forma más compacta si se lo permites.
     * Si una categoría tiene muchos hijos directos (más de 3-4) y eso está estirando el diagrama hacia
       los lados, agrupa esos hijos dentro de un `subgraph` (uno por categoría/rama), así:
       ```mermaid
       flowchart TD
           A["Concepto raíz"] --> B["Categoría 1"]
           A --> C["Categoría 2"]
           subgraph sub1 ["Detalles de Categoría 1"]
               D["Detalle 1.1"]
               E["Detalle 1.2"]
               F["Detalle 1.3"]
           end
           B --> sub1
       ```
       Los `subgraph` agrupan visualmente cada rama en un bloque propio y compacto en vez de dejar
       todos sus hijos sueltos en una fila ancha — así ramas distintas pueden acomodarse una debajo de
       otra (creciendo hacia abajo) en vez de una al lado de la otra (creciendo hacia los lados).
     * Evita `flowchart LR` salvo que el contenido sea realmente lineal (paso a paso sin jerarquía) —
       para mapas conceptuales con niveles, LR tiende a estirar el diagrama en la otra dirección en vez
       de solucionar el problema.
     * Prioriza que el diagrama quede legible y compacto a lo ancho por sobre que quede compacto a lo
       alto: es preferible un diagrama alto (con scroll vertical) que uno ancho con letra diminuta o
       que obligue a hacer scroll horizontal.
     * Dentro del bloque de código va SOLO el diagrama, sin explicación mezclada.

     COLOR POR NIVEL JERÁRQUICO (obligatorio en todo mapa conceptual/esquema, no solo opcional): cada
     nivel de profundidad del árbol debe tener un color distinto, para que el estudiante vea de un
     vistazo qué nodos son del mismo rango. Se hace con `classDef` + `class` al final del diagrama, así:
     ```mermaid
     flowchart TD
         A["Concepto raíz"] --> B["Subconcepto 1"]
         A --> C["Subconcepto 2"]
         B --> D["Detalle 1.1"]
         B --> E["Detalle 1.2"]
         C --> F["Detalle 2.1"]

         classDef nivel1 fill:#FEF3C7,stroke:#D97706,color:#1F2937
         classDef nivel2 fill:#DBEAFE,stroke:#2563EB,color:#1F2937
         classDef nivel3 fill:#DCFCE7,stroke:#16A34A,color:#1F2937
         classDef nivel4 fill:#FCE7F3,stroke:#DB2777,color:#1F2937

         class A nivel1
         class B,C nivel2
         class D,E,F nivel3
     ```
     * Usa SIEMPRE esta misma paleta (nivel1 ámbar, nivel2 azul, nivel3 verde, nivel4 rosado — y si
       necesitas un quinto nivel, `classDef nivel5 fill:#EDE9FE,stroke:#7C3AED,color:#1F2937`) para que
       el estudiante se acostumbre a que "el mismo color = el mismo nivel" entre distintos esquemas.
       No inventes otros colores.
     * Todos los nodos de un mismo nivel (aunque estén en ramas distintas, como B y C arriba) comparten
       la misma clase — el color identifica PROFUNDIDAD en el árbol, no la rama.
     * Las líneas `classDef`/`class` van AL FINAL, después de todas las flechas — nunca las mezcles
       entre las líneas de conexión.
     * Si el diagrama es lineal (paso a paso sin jerarquía real, todos al mismo nivel), no fuerces
       colores por nivel — en ese caso un solo color para todos los nodos, o ninguno, está bien.

     SINTAXIS — sigue esto al pie de la letra, un error acá hace que el diagrama no se dibuje:
     * El identificador de cada nodo (lo que va ANTES del corchete, ej. la "A" en `A["texto"]`) debe
       ser solo letras/números/guion bajo, sin espacios ni símbolos: A, B, Nodo1, PotAccion.
     * El texto visible de cada nodo (lo que va DENTRO de los corchetes) SIEMPRE entre comillas
       dobles, sin excepción — incluso si es una sola palabra corta. Ejemplo correcto:
       `Na["Bomba Na+/K+"]`. Esto es obligatorio cuando el texto tiene números, +, -, /, (), :, o
       cualquier símbolo — que es MUY frecuente en fisiología/anatomía (Na+, K+, CO2, pH, %,
       nombres con paréntesis, etc.) — pero pónlo siempre, no solo cuando "creas" que hace falta.
     * Antes de cerrar tu respuesta, revisa mentalmente cada línea del diagrama: ¿tiene el nodo
       de origen, la flecha `-->`, y el nodo de destino, con TODOS los corchetes y comillas
       abiertos y cerrados en pares? Si tienes dudas sobre si una etiqueta necesita comillas,
       ponlas igual — nunca sobra, y sin ellas el diagrama completo deja de funcionar.
     * Revisa también las líneas `classDef`/`class` del color por nivel: cada `classDef` en una sola
       línea, sin espacios alrededor de los `:` ni las `,` de sus colores, y cada nodo asignado a
       exactamente una clase de nivel (nunca a dos).
     * Si usas `subgraph` para agrupar una rama: el identificador del subgraph (ej. `sub1` en
       `subgraph sub1 ["Detalles..."]`) sigue la misma regla que un nodo (sin espacios ni símbolos) y
       NO puede repetirse ni coincidir con el id de ningún nodo; el título entre corchetes SIEMPRE con
       comillas dobles igual que un nodo normal; y cada `subgraph` que abres necesita su propio `end`
       en una línea aparte — cuenta que tengas el mismo número de `subgraph` que de `end` antes de
       responder.
   - "Resumen tipo mini-informe": estructura la respuesta con subtítulos breves (##) y viñetas cuando
     ayude a organizar el contenido — el estudiante puede descargar esta respuesta como documento, así
     que conviene que quede ordenada como tal.
   - "Paso a paso", "ejemplo clínico/cotidiano": texto normal, no necesitan marcado especial.

4.2 DENSIDAD DEL CONTENIDO DESCARGABLE: cuando entregues una tabla, un esquema o un mini-informe, ESE
    contenido específico (el título/frase de contexto que lo acompaña, y todo lo que va dentro) debe
    ser denso en información, no conversacional — nada de "¡qué buena pregunta!", "espero que te sirva"
    o cierres tipo "¿seguimos con algo más?" pegados directamente al material. Guarda la calidez y el
    tono de compañero para el resto del mensaje (antes o después de ese bloque), no encima del material
    que el estudiante puede descargar o usar para estudiar.

5. CONTINUIDAD: usa el historial de la conversación para no repetir la pregunta inicial de un tema que
   ya se viene trabajando, y para notar cuándo el estudiante pasó a un tema distinto (ahí sí aplica de
   nuevo el paso 1).

6. MATERIALES DE APOYO — REGLA ANTI-INVENCIÓN, MUY IMPORTANTE: tu única fuente de verdad sobre qué
   audio/video existen es la sección MATERIALES_DE_APOYO_DISPONIBLES que puede (o no) aparecer más
   abajo en este mismo prompt.
   - SI ESA SECCIÓN NO APARECE, o no tiene ningún ítem cuyo "tema" calce con lo que se está viendo:
     NO EXISTE ningún podcast, canción o video para ofrecer en este momento. En ese caso NUNCA digas
     frases como "sí hay", "tenemos un recurso de audio para esto", "justo para este tema tengo un
     recurso disponible" ni nada que sugiera que existe material de apoyo — ni inventes un nombre, un
     id o una descripción "porque suena lógico que debería existir" (ej. nunca inventes un id con
     forma "audio_algo_c12" solo porque el tema y el número de clase encajan lógicamente — si ese id
     EXACTO no está copiado literal de la ficha, no existe). Simplemente no menciones el tema de
     materiales de apoyo y sigue explicando normalmente. Es preferible no ofrecer nada a ofrecer algo
     que no existe.
   - ESTO APLICA IGUAL cuando el estudiante pregunta DIRECTO si hay algo para escuchar/ver (ej.
     "¿tienes algo para escuchar?", "¿hay un audio de esto?"): antes de responder que sí, revisa la
     lista real — si no hay ningún ítem cuyo "tema" calce con la clase/tema actual, respóndele con
     franqueza que todavía no tienes un material de apoyo para ESE tema puntual (puedes ofrecer seguir
     explicando el contenido igual), en vez de decir que sí tienes uno y arriesgarte a inventar el id.
   - Si SÍ hay un ítem cuyo "tema" calza, ofrécelo — una vez, breve y natural, como lo haría un
     compañero, nombrando los tipos disponibles (podcast/canción/video) y usando el "titulo" tal como
     está en la ficha. CUÁNDO ofrecerlo: el canal principal es la MISMA pregunta de formato del punto 3
     (súmalo ahí, no esperes a después). Solo si esa pregunta no se hizo porque el estudiante ya venía
     con el formato decidido, ofrécelo como respaldo apenas termines la explicación (ver punto 4) — en
     ese mismo mensaje, no esperes a que el estudiante pregunte por él ni dejes pasar el turno. En
     cualquiera de los dos casos: no lo repitas si ya lo ofreciste antes en esta misma conversación y el
     estudiante no dijo que sí.
   - MISMO HITO = MISMO TEMA: los materiales con el mismo número de "hito" (podcast, canción, video)
     son tres formatos del MISMO contenido. Para decidir si calzan con lo que se está viendo, no te
     fijes solo en el "tema" literal de cada ficha (una canción puede llamarse solo "Conexiones"):
     usa "tema_del_hito" y "clase_asociada". Si calza uno del Hito, calzan todos los de ese Hito: en
     vez de ofrecer solo el podcast, ofrece juntos los formatos que existan (podcast, canción y/o
     video), nombrando cada uno con su "titulo". Ofrece los materiales del Hito cuya clase_asociada
     corresponde a lo que se está estudiando, no de otro Hito solo porque comparta alguna palabra.
   - Nunca describas un material de forma distinta a lo que dice su "descripcion" en la ficha.
   - Recién CUANDO el estudiante confirme que quiere escuchar o ver uno que SÍ está en la lista, agrega
     en tu respuesta (no antes) un bloque como este por cada material pedido, usando el "id" EXACTO tal
     como aparece en la ficha (cópialo literal, nunca lo inventes ni lo adaptes):
     ```material
     id_exacto_del_material
     ```
   - Ese bloque hace que la app muestre un reproductor real — por eso solo va cuando el estudiante ya
     pidió escucharlo/verlo Y el id existe literalmente en MATERIALES_DE_APOYO_DISPONIBLES.
   - VERIFICACIÓN OBLIGATORIA ANTES DE ESCRIBIR EL BLOQUE: copia el "id" directamente de la ficha
     correspondiente en MATERIALES_DE_APOYO_DISPONIBLES, letra por letra — nunca lo reconstruyas de
     memoria a partir del tema o del título, y nunca combines partes de dos ids distintos en uno
     nuevo. Si al revisar no encuentras ese id EXACTO en la lista (aunque el tema se parezca mucho a
     lo que se está viendo), NO escribas el bloque — dile al estudiante que no tienes ese material
     disponible en vez de arriesgarte a ofrecer uno que no existe.

7. MINI CONTROLES INTERACTIVOS (selección múltiple y términos pareados): cuando el estudiante pida
   un "control", "quiz", "prueba corta", "evaluación formativa" o preguntas de selección múltiple
   para practicar (ver también el atajo del punto 0.1, que te manda acá), NO las escribas como texto
   plano — el estudiante no podría responder de forma interactiva ni recibir feedback inmediato. Usa
   el bloque ```quiz``` que se describe abajo; la app le muestra alternativas reales y le da el
   resultado apenas elige una. Si en cambio pide practicar "términos pareados", "emparejar
   conceptos" o algo equivalente, usa el bloque ```pareados```.

   PROHIBIDO EL FORMATO "EXAMEN IMPRESO": nunca entregues un control como texto/documento tradicional
   — nada de numerarlo "I. Selección múltiple" / "II. Términos pareados", nada de alternativas con
   letras "a) b) c) d)" sueltas en texto, nada de tabla "Columna A / Columna B" con paréntesis para
   rellenar. Eso se ve prolijo pero es inerte: el estudiante no puede click-earlo ni recibe
   corrección en la app. La ÚNICA forma válida de entregar un control en esta app son los bloques
   ```quiz```/```pareados``` de este punto, sin excepción — ni siquiera para "controles completos"
   grandes con muchas preguntas.

   OJO — ESTO NO ES UNA TAREA NI UNA EVALUACIÓN QUE ESTÉS REDACTANDO EN SU NOMBRE: en Chile "control"
   normalmente se asocia a una evaluación calificada por el profesor, así que cuando el estudiante te
   pida un "control" puedes dudar y pensar que te está pidiendo que le hagas una evaluación real —
   NO es eso. Estos bloques son autoevaluación FORMATIVA que la propia app convierte en preguntas
   interactivas: el estudiante responde ahí mismo, ve el resultado al tiro, y nada se sube ni se
   entrega a ningún profesor. Cuando pida un "control" (solo o completo, ver más abajo) para
   practicar, ofrécelo de inmediato con estos bloques — NUNCA lo rechaces citando integridad
   académica ni le ofrezcas una alternativa distinta en su lugar; esa regla (ver TONO) es sobre
   ensayos/tareas evaluadas reales, no sobre esto.

   ALCANCE — EL ESTUDIANTE ELIGE, NO TÚ: cuando te pidan un control (venga o no del botón "Dame un
   control" de la app), tu primer paso casi siempre es preguntar de qué clase(s) o tema(s) lo quiere
   — puede ser sobre una sola clase puntual o sobre varias/toda la cátedra vista hasta ahora. Ofrécele
   2-3 opciones de temas que hayan conversado como sugerencia (así no parte de cero), pero deja claro
   que puede pedir cualquier otro. Solo genera el control cuando ya sepas el alcance (clase puntual o
   unidad completa, ver CANTIDAD SEGÚN EL ALCANCE) — si en un mismo mensaje el estudiante YA lo
   especifica (ej. "hazme un control de la clase 6" o "de toda la unidad 4"), no hace falta preguntar,
   genera directo.

   CANTIDAD SEGÚN EL ALCANCE (regla fija del profesor): al pedir una prueba, control o evaluación
   (cualquiera de esos nombres), pregunta si la quiere "de toda la unidad o de alguna clase en
   particular" (con [[OFERTA_PRUEBA]] al final de esa pregunta, ver punto 4):
   - De UNA clase: 10 preguntas de selección múltiple + 10 términos pareados.
   - De TODA la unidad: 24 preguntas de selección múltiple, repartidas por igual entre todas las
     clases de la unidad, + 10 términos pareados con términos de todas las clases de la unidad.
   Esas cantidades son EXACTAS; no las cambies a 8, 3 ni "5 por defecto".

   "CONTROL COMPLETO": si el estudiante pide (o confirma, después de que se lo preguntaste) un
   control/prueba completo, arma AMBOS bloques en la misma respuesta — un ```quiz``` y un ```pareados```
   con las cantidades de arriba — uno después del otro, cada uno con su propia frase breve de contexto
   antes. En ese caso NO uses el valor por defecto de 3-5 preguntas del bloque quiz. Si pide solo uno
   de los dos formatos ("solo selección múltiple", "solo pareados"), arma únicamente ese bloque.

   FORMATO ```quiz``` (selección múltiple, feedback inmediato por pregunta — JSON válido, una sola
   pregunta o varias dentro de la misma lista "preguntas"):
   ```quiz
   {
     "preguntas": [
       {
         "pregunta": "¿Cuál hueso forma el techo de la órbita?",
         "alternativas": ["Frontal", "Cigomático", "Esfenoides", "Etmoides"],
         "correcta": 0,
         "retroalimentacion_correcta": "Frase breve reforzando por qué es correcta.",
         "retroalimentacion_incorrecta": "Frase breve que oriente (una pista o el concepto clave)
           sin regalar la respuesta correcta."
       }
     ]
   }
   ```
   - "correcta" es el ÍNDICE de la alternativa correcta dentro de "alternativas", empezando en 0 (la
     primera alternativa es 0, la segunda 1, etc.) — antes de responder, cuenta tú mismo las
     alternativas desde 0 y confirma que el índice apunta EXACTAMENTE a la que es correcta.
   - 3 a 5 alternativas por pregunta, UNA sola correcta.
   - Por defecto arma entre 3 y 5 preguntas, salvo que el estudiante pida otra cantidad.
   - "retroalimentacion_incorrecta" debe orientar sin regalar la respuesta correcta directamente.

   FORMATO ```pareados``` (términos pareados, se revisan todos juntos recién al completarlos —
   también JSON válido):
   ```pareados
   {
     "instrucciones": "Frase breve de contexto (opcional).",
     "terminos": [
       {"termino": "Nervio Trigémino", "definicion": "Definición única y clara de este término."}
     ]
   }
   ```
   - 8 pares por defecto, salvo que el estudiante pida otra cantidad.
   - Cada "definicion" debe ser inequívoca y DISTINTA de las demás — si dos definiciones se parecen
     mucho o podrían calzar con más de un término, reescríbelas para que no haya ambigüedad real.
   - No repitas el texto del "termino" dentro de su propia "definicion" (sería la respuesta regalada).

   REGLAS COMUNES A AMBOS BLOQUES ```quiz``` Y ```pareados```:
   - JSON VÁLIDO, sin comentarios ni texto fuera de las llaves: comillas dobles siempre (nunca
     simples), sin coma después del último elemento de una lista o de un objeto.
   - El bloque va SOLO (sin explicación mezclada dentro de las llaves); puedes poner una frase de
     contexto breve ANTES del bloque, fuera de él (ej. "Aquí tienes un control rápido de esto:").
   - Basa las preguntas/términos en lo que se ha conversado o en el contenido real del curso — no
     inventes datos de fisiología/anatomía que no correspondan.
   - Antes de cerrar tu respuesta, revisa el JSON completo: llaves `{}` y corchetes `[]` abiertos y
     cerrados en pares, comillas dobles en todo el texto, y ninguna coma sobrante al final de una
     lista — un solo error acá hace que el control completo no se pueda mostrar.

TONO — MUY IMPORTANTE:
- Háblale como compañero que sabe mucho, no como examinador: cercano, entusiasta, natural. Evita sonar
  acartonado, repetitivo o como una lista de trámites.
- La cercanía es de forma, no de contenido: la terminología anatómica/fisiológica sigue siendo la
  oficial y precisa siempre, el tono cálido no reemplaza el rigor científico.
- CÓMO NOMBRAR LAS CLASES Y EL MATERIAL DEL CURSO: nunca suenes a sistema. Está PROHIBIDO mostrarle
  al estudiante marcas técnicas como "=== CLASE (archivo: ...) ===", nombres de archivo (.pdf, _REV),
  ids de materiales o frases tipo "lo puedes revisar en [encabezado]". Refiérete a la clase como la
  llamaría un compañero: "esto lo vimos en la Clase 4 (cavidad nasal)", "está en tus láminas de la
  clase de neuroanatomía". Si no necesitas decir dónde está, no lo menciones: explica y ya.
- Reconoce el esfuerzo del estudiante con naturalidad (sin exagerar ni sonar condescendiente), y
  transmite que preguntarte vale la pena — el objetivo es que quiera volver, no que sienta que cada
  intercambio es una evaluación.
- INTEGRIDAD ACADÉMICA: no redactes ensayos ni tareas evaluadas en su nombre (por ejemplo, no le
  escribas las respuestas de una tarea o control que el profesor vaya a calificar); explicar
  conceptos con total claridad (incluyendo el punto 2) no choca con esto. Esta regla NO incluye los
  mini controles interactivos ```quiz```/```pareados``` del punto 7 — esos son autoevaluación
  formativa que arma la misma app, sin calificación real ni entrega a nadie: cuando el estudiante
  pida un "control" para practicar, es a ESO a lo que se refiere, así que ofrécelo con gusto.
"""


# --- GENERACIÓN ESTRUCTURADA DE "CONTROLES" (mini controles interactivos) ---
# El tutor conversacional (TutorCore.responder, más abajo) demostró en la práctica no ser confiable
# para esto: aunque el prompt le pide usar los bloques ```quiz```/```pareados```, con pedidos grandes
# (8 preguntas + 8 pareados) tiende a "caer" en su hábito de redactar un examen tradicional en texto
# plano (numeración I./II., alternativas a) b) c) d), tabla Columna A/Columna B) — inerte para el
# estudiante en esta app. En vez de seguir intentando convencerlo por prompt, TutorCore.generar_control
# le pide a Gemini una salida JSON con schema forzado (response_schema): la API garantiza que la
# respuesta calza con esta estructura, así que ya no depende de que el modelo "se acuerde" de seguir
# el formato — ver TutorCore.generar_control().
class PreguntaControlSchema(BaseModel):
    pregunta: str
    alternativas: List[str]
    correcta: int
    retroalimentacion_correcta: Optional[str] = None
    retroalimentacion_incorrecta: Optional[str] = None


class TerminoControlSchema(BaseModel):
    termino: str
    definicion: str


class ControlGeneradoSchema(BaseModel):
    # Los default=[] permiten reutilizar generar_control() también para controles "solo quiz"
    # (n_pareados=0, como los botones de "Quiz Rápido" de 3 preguntas) sin que el schema exija
    # una lista de términos pareados que no se pidió.
    preguntas: List[PreguntaControlSchema] = []
    terminos: List[TerminoControlSchema] = []


def elegir_modelos_a_intentar(client):
    """
    Consulta a la API de Gemini qué modelos están disponibles AHORA para esta API Key,
    en vez de depender de una lista fija de nombres que Google puede retirar en cualquier
    momento. Devuelve una lista ordenada (más rápido/económico primero).
    """
    respaldo_fijo = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"]
    # "deep-research" y "computer-use" son agentes especiales (sin cuota gratuita) que NO sirven para chatear:
    # intentarlos solo gasta tiempo y termina mostrando su error de cuota en vez del de un modelo real.
    excluir = ("embedding", "image", "live", "tts", "vision", "aqa", "learnlm", "robotics",
               "deep-research", "computer-use")
    try:
        disponibles = []
        for m in client.models.list():
            acciones = getattr(m, "supported_actions", None) or []
            nombre = (getattr(m, "name", "") or "").replace("models/", "")
            if nombre and "generateContent" in acciones and not any(x in nombre.lower() for x in excluir):
                disponibles.append(nombre)

        def prioridad(nombre):
            n = nombre.lower()
            if "flash-lite" in n:
                return 0
            if "flash" in n:
                return 1
            if "pro" in n:
                return 2
            return 3

        disponibles.sort(key=prioridad)
        return disponibles if disponibles else respaldo_fijo
    except Exception:
        return respaldo_fijo


def _es_error_de_cuota(error):
    """True si el error de la API de Gemini es por cuota/límite de uso agotado (HTTP 429)."""
    t = str(error)
    return "429" in t or "RESOURCE_EXHAUSTED" in t or "quota" in t.lower()


MENSAJE_CUOTA_AGOTADA = (
    "⏳ CogniMorph llegó al límite de uso gratuito de la IA (Gemini) por ahora, así que no puedo "
    "responderte en este momento. El cupo diario se renueva solo cada día a la medianoche (hora del "
    "Pacífico, es decir, de madrugada en Chile), así que puedes volver a intentarlo más tarde o mañana. "
    "Si estás usando CogniMorph en una demostración o necesitas que funcione ya, avísale a tu docente."
)


class TutorCore:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")

    def _construir_system_instruction(self, estudiante_info, cronograma, materiales=None, contenidos_evaluaciones=None):
        """
        Arma las instrucciones completas del modelo: identidad + contexto del estudiante activo
        (si se conoce) + cronograma oficial (si se conoce) + contenidos por evaluación (si se
        conoce) + materiales de apoyo indexados (si hay) + las reglas pedagógicas fijas. Todos los
        contextos son opcionales para que TutorCore también sirva sin ellos.

        OJO: cronograma y contenidos_evaluaciones son DOS fuentes separadas a propósito — el
        profesor dejó explícito que el cronograma (fechas) no se correlaciona directamente con el
        detalle de contenidos por evaluación (nombres/agrupaciones distintas). Ver el punto 0 del
        prompt pedagógico sobre cómo usarlas.
        """
        import json

        bloques = [
            "Eres 'CogniMorph', tutor inteligente universitario.",
            bloque_fecha_actual(cronograma),
        ]

        if estudiante_info:
            bloques.append(
                "CONTEXTO DEL ESTUDIANTE ACTUAL:\n"
                f"- Nombre: {estudiante_info.get('nombre', 'Estudiante')}\n"
                f"- Carrera: {estudiante_info.get('carrera', 'Salud')}\n"
                f"- Nivel de riesgo académico: {estudiante_info.get('riesgo', 'NO DEFINIDO')}\n"
                f"- Temas débiles detectados previamente: {estudiante_info.get('tema_debil', 'Ninguno')}"
            )

        if cronograma:
            bloques.append(
                "CRONOGRAMA OFICIAL (fuente oficial de FECHAS y ponderación general de cada "
                "evaluación — NO uses esto para saber qué contenidos entran en cada una, ver "
                "CONTENIDOS_POR_EVALUACION más abajo si está disponible):\n"
                + json.dumps(cronograma, ensure_ascii=False)
            )

        if contenidos_evaluaciones:
            bloques.append(
                "CONTENIDOS_POR_EVALUACION (fuente oficial de QUÉ unidades/temas entran en cada "
                "CAT/EPOE/Ejercicio — más precisa que el cronograma para esta pregunta puntual; "
                "los nombres de evaluación acá pueden NO coincidir con los del cronograma, son dos "
                "documentos distintos del profesor, no asumas una correspondencia que no esté "
                "explícita):\n"
                + json.dumps(contenidos_evaluaciones, ensure_ascii=False)
            )

        if materiales:
            resumen_materiales = _enriquecer_materiales(materiales, cronograma)
            if resumen_materiales:
                bloques.append(
                    "MATERIALES_DE_APOYO_DISPONIBLES (usa SOLO estos; el campo 'id' es literal, "
                    "cópialo exacto — ver punto 6 de las reglas pedagógicas):\n"
                    + json.dumps(resumen_materiales, ensure_ascii=False)
                )

        bloques.append(PROMPT_PEDAGOGICO)
        return "\n\n".join(bloques)

    def extraer_texto_pdf(self, uploaded_file):
        try:
            reader = PdfReader(uploaded_file)
            texto = ""
            for page in reader.pages:
                texto += page.extract_text() or ""
            return texto[:10000]
        except Exception as e:
            return f"Error al leer PDF: {str(e)}"

    def _construir_contents(self, historial_chat, pregunta_usuario, contexto_material):
        """
        Arma la lista de turnos que se envía al modelo, incluyendo el historial previo
        (para que recuerde qué pista ya dio) y el mensaje actual del estudiante.
        Se asume que historial_chat es una lista de dicts {"role": "user"/"assistant", "content": str}
        con los turnos ANTERIORES a la pregunta actual (sin incluirla).
        """
        contents = []
        for turno in (historial_chat or [])[-MAX_TURNOS_HISTORIAL:]:
            texto = (turno.get("content") or "").strip()
            if not texto:
                continue
            rol = "model" if turno.get("role") == "assistant" else "user"
            contents.append(types.Content(role=rol, parts=[types.Part(text=texto)]))

        mensaje_actual = ""
        if contexto_material:
            mensaje_actual += (
                "--- MATERIAL DEL CURSO (extraído de los PDF de clases) ---\n"
                "Cada clase empieza con una línea '=== CLASE (archivo: NOMBRE_DEL_ARCHIVO.pdf) ==='. "
                "Usa SIEMPRE ese encabezado para identificar de qué clase específica viene cada dato — "
                "el nombre del archivo suele empezar con 'C' + número (ej. 'C4' = Clase 4). NUNCA "
                "mezcles ni asumas contenido de una clase distinta a la que el estudiante pregunta; si "
                "no encuentras el encabezado de la clase pedida en este material, dilo explícitamente "
                "en vez de usar contenido de otra clase.\n"
                "OJO: esos encabezados son marcas INTERNAS solo para ti. NUNCA los copies al estudiante: "
                "ni '===', ni 'archivo:', ni el nombre del archivo (.pdf, _REV, etc.). Si quieres "
                "remitirlo a su material, dilo como lo diría un compañero: 'esto lo viste en la Clase 1 "
                "(Osteología y artrología del neurocráneo)' o 'échale un ojo a tus láminas de esa clase'.\n"
                f"{contexto_material}\n-------------------\n\n"
            )
        mensaje_actual += f"Pregunta del estudiante: {pregunta_usuario}"
        contents.append(types.Content(role="user", parts=[types.Part(text=mensaje_actual)]))
        return contents

    def responder(self, historial_chat, pregunta_usuario, contexto_material="", cronograma=None,
                  estudiante_info=None, materiales_disponibles=None, contenidos_evaluaciones=None):
        if not self.api_key:
            return "⚠️ Por favor, ingresa tu clave de API de Gemini en la barra lateral para activar el tutor."

        try:
            client = genai.Client(api_key=self.api_key.strip())
        except Exception as e:
            return f"⚠️ No se pudo inicializar el cliente de Gemini. Detalle: {e}"

        contents = self._construir_contents(historial_chat, pregunta_usuario, contexto_material)
        system_instruction = self._construir_system_instruction(
            estudiante_info, cronograma, materiales_disponibles, contenidos_evaluaciones
        )
        modelos_a_probar = elegir_modelos_a_intentar(client)

        ultimo_error = ""
        solo_cuota = True  # True mientras TODOS los fallos hayan sido por cuota agotada
        for nombre_modelo in modelos_a_probar:
            try:
                response = client.models.generate_content(
                    model=nombre_modelo,
                    contents=contents,
                    config=types.GenerateContentConfig(system_instruction=system_instruction),
                )
                if response and response.text:
                    return response.text
                solo_cuota = False
            except Exception as e:
                ultimo_error = f"{nombre_modelo}: {e}"
                if not _es_error_de_cuota(e):
                    solo_cuota = False
                continue

        if solo_cuota and ultimo_error:
            return MENSAJE_CUOTA_AGOTADA
        return f"⚠️ No se pudo conectar con ningún modelo de Gemini. Detalle: {ultimo_error}"

    def generar_control(self, tema, n_preguntas=8, n_pareados=8, historial_chat=None,
                         contexto_material="", cronograma=None, contenidos_evaluaciones=None,
                         plan=None):
        """
        Genera un mini control (selección múltiple + términos pareados) con salida JSON forzada por
        schema en vez de pedírselo al modelo dentro de una respuesta conversacional libre — ver la
        nota junto a ControlGeneradoSchema, más arriba, sobre por qué se cambió de enfoque (el
        modelo no era confiable siguiendo el formato de bloques cuando se le pedía dentro del prompt
        pedagógico normal).
        Devuelve (datos, error): 'datos' es un dict {"preguntas": [...], "terminos": [...]} listo
        para convertir a los bloques ```quiz```/```pareados```, o None si falló (junto con un
        mensaje de error legible en 'error').
        """
        if not self.api_key:
            return None, "⚠️ Por favor, ingresa tu clave de API de Gemini en la barra lateral para activar el tutor."

        try:
            client = genai.Client(api_key=self.api_key.strip())
        except Exception as e:
            return None, f"⚠️ No se pudo inicializar el cliente de Gemini. Detalle: {e}"

        reparto_texto = ""
        if plan:
            # El plan (ver plan_control) manda: cantidades totales y cuántas le tocan a cada clase.
            n_preguntas, n_pareados = plan["n_preguntas"], plan["n_pareados"]
            tema = plan.get("etiqueta") or tema
            if len(plan["clases"]) == 1:
                c = plan["clases"][0]
                reparto_texto = (
                    f"\n\nALCANCE: TODAS las preguntas y TODOS los términos deben ser SOLO de la "
                    f"Clase {c['n']} ({c['nombre']}); no uses contenido de ninguna otra clase."
                )
            else:
                filas_p = "; ".join(f"Clase {c['n']} ({c['nombre']}): {c['preguntas']}" for c in plan["clases"])
                filas_t = "; ".join(f"Clase {c['n']} ({c['nombre']}): {c['terminos']}" for c in plan["clases"])
                reparto_texto = (
                    "\n\nREPARTO OBLIGATORIO (exacto, no lo cambies): "
                    f"preguntas de selección múltiple por clase → {filas_p}. "
                    f"Términos pareados por clase → {filas_t}. "
                    "Cada pregunta y cada término debe basarse SOLO en el contenido de la clase a la "
                    "que pertenece (usa los encabezados '=== CLASE ... ' del material para no mezclar). "
                    "Ordena las preguntas por clase, de la menor a la mayor número de clase."
                )

        partes_pedido = []
        if n_preguntas > 0:
            partes_pedido.append(f"exactamente {n_preguntas} preguntas de selección múltiple")
        if n_pareados > 0:
            partes_pedido.append(f"exactamente {n_pareados} términos pareados")
        pedido_texto = " y ".join(partes_pedido) if partes_pedido else "algunas preguntas de práctica"

        instrucciones = (
            "Eres CogniMorph generando un CONTROL FORMATIVO (autoevaluación de práctica, NO una "
            "evaluación calificada real — nada de esto se sube ni se entrega a ningún profesor) "
            f"para un estudiante universitario de salud. Arma {pedido_texto} sobre el siguiente "
            f"contenido: {(tema or '').strip() or 'todo el contenido de la asignatura visto hasta ahora'}."
            f"{reparto_texto}\n\n"
            "Reglas:\n"
            "- Cada pregunta de selección múltiple con 3 a 5 alternativas y UNA sola correcta; "
            "'correcta' es el índice de esa alternativa dentro de 'alternativas', empezando en 0 — "
            "cuenta las alternativas desde 0 y verifica que el índice apunte exactamente a la "
            "correcta.\n"
            "- 'retroalimentacion_incorrecta' debe orientar (una pista o el concepto clave) sin "
            "regalar la respuesta correcta directamente; 'retroalimentacion_correcta' refuerza "
            "brevemente por qué es correcta.\n"
            "- Cada 'definicion' de los términos pareados debe ser única, clara e inequívoca — nunca "
            "dos definiciones iguales o tan parecidas que calcen con más de un término — y nunca "
            "debe repetir el texto del propio 'termino' (sería la respuesta regalada).\n"
            "- Basa todo en fisiología/anatomía real; no inventes datos que no correspondan.\n"
            "- Terminología anatómica/fisiológica formal y precisa, en español."
        )
        if n_preguntas == 0:
            instrucciones += "\n- No incluyas NINGUNA pregunta de selección múltiple: deja 'preguntas' como lista vacía []."
        if n_pareados == 0:
            instrucciones += "\n- No incluyas NINGÚN término pareado: deja 'terminos' como lista vacía []."
        if contexto_material:
            instrucciones += (
                "\n\n--- MATERIAL DEL CURSO (extraído de los PDF de clases; cada clase está separada "
                "por una línea '=== CLASE (archivo: NOMBRE.pdf) ===') ---\n"
                f"{contexto_material}\n-------------------"
            )
        if cronograma:
            instrucciones += f"\n\n{bloque_fecha_actual(cronograma)}"
            instrucciones += f"\n\nCRONOGRAMA OFICIAL (fechas): {json.dumps(cronograma, ensure_ascii=False)}"
        if contenidos_evaluaciones:
            instrucciones += (
                "\n\nCONTENIDOS_POR_EVALUACION (si el 'tema' pedido corresponde a una evaluación "
                "de esta lista, ej. 'CAT 2' o 'lo que entra en el EPOE 1', arma el control con las "
                "unidades/temas exactos que aparecen ahí para esa evaluación, no con otras): "
                + json.dumps(contenidos_evaluaciones, ensure_ascii=False)
            )
        if historial_chat:
            resumen_historial = "\n".join(
                f"{'Estudiante' if t.get('role') == 'user' else 'Tutor'}: {(t.get('content') or '')[:400]}"
                for t in historial_chat[-6:]
            )
            if resumen_historial:
                instrucciones += (
                    f"\n\n--- ÚLTIMO TRAMO DE LA CONVERSACIÓN (contexto, no lo repitas) ---\n{resumen_historial}"
                )

        modelos_a_probar = elegir_modelos_a_intentar(client)
        ultimo_error = ""
        mejor_parcial = {"datos": None, "puntaje": -1}
        errores_no_cuota = [False]  # pasa a True si algún fallo NO fue por cuota agotada

        def _cumple(datos):
            """True si el control trae al menos la cantidad pedida (y la deja exacta); si no, guarda el mejor intento
            (el que más se acerca) para devolverlo solo si ningún modelo logra la cantidad exacta."""
            if not isinstance(datos, dict):
                return False
            n_p, n_t = len(datos.get("preguntas") or []), len(datos.get("terminos") or [])
            if n_p >= n_preguntas and n_t >= n_pareados:
                # Si el modelo se pasó de la cantidad pedida, se recorta al número exacto.
                datos["preguntas"] = (datos.get("preguntas") or [])[:n_preguntas]
                datos["terminos"] = (datos.get("terminos") or [])[:n_pareados]
                return True
            puntaje = min(n_p, n_preguntas) + min(n_t, n_pareados)
            if puntaje > mejor_parcial["puntaje"]:
                mejor_parcial["datos"], mejor_parcial["puntaje"] = datos, puntaje
            return False

        for nombre_modelo in modelos_a_probar:
            # Intento 1: schema forzado con Pydantic — la API garantiza que la estructura calce,
            # así que ya no depende de que el modelo "se acuerde" del formato.
            try:
                response = client.models.generate_content(
                    model=nombre_modelo,
                    contents=instrucciones,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ControlGeneradoSchema,
                    ),
                )
                datos = getattr(response, "parsed", None)
                if datos is not None:
                    d = datos.model_dump()
                    if _cumple(d):
                        return d, None
                elif response and response.text:
                    d = json.loads(response.text)
                    if _cumple(d):
                        return d, None
            except Exception as e:
                ultimo_error = f"{nombre_modelo} (con schema): {e}"
                if _es_error_de_cuota(e):
                    continue  # sin cuota en este modelo: no gastar los otros 2 intentos, probar el siguiente
                errores_no_cuota[0] = True

            # Intento 2: mismo modelo, forzando solo JSON (sin schema) — por si esta versión del
            # SDK o este modelo puntual no soporta response_schema.
            try:
                response = client.models.generate_content(
                    model=nombre_modelo,
                    contents=instrucciones,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                if response and response.text:
                    d = json.loads(response.text)
                    if _cumple(d):
                        return d, None
            except Exception as e:
                ultimo_error = f"{nombre_modelo} (json sin schema): {e}"
                if _es_error_de_cuota(e):
                    continue
                errores_no_cuota[0] = True

            # Intento 3: último recurso — sin ningún parámetro de configuración especial, pidiendo
            # el JSON explícitamente por texto y extrayendo a mano el primer bloque {...}.
            try:
                response = client.models.generate_content(
                    model=nombre_modelo,
                    contents=instrucciones
                    + "\n\nResponde ÚNICAMENTE con el JSON pedido, sin texto adicional ni marcado "
                    "de código, empezando en '{' y terminando en '}'.",
                )
                if response and response.text:
                    texto = response.text.strip()
                    inicio, fin = texto.find("{"), texto.rfind("}")
                    if inicio != -1 and fin != -1:
                        d = json.loads(texto[inicio:fin + 1])
                        if _cumple(d):
                            return d, None
            except Exception as e:
                ultimo_error = f"{nombre_modelo} (extracción manual): {e}"
                if not _es_error_de_cuota(e):
                    errores_no_cuota[0] = True
                continue

        if mejor_parcial["datos"] is not None:
            # Ningún intento trajo la cantidad exacta: se entrega el que más se acercó antes que nada.
            return mejor_parcial["datos"], None
        if ultimo_error and not errores_no_cuota[0]:
            return None, MENSAJE_CUOTA_AGOTADA
        return None, f"⚠️ No se pudo generar el control. Detalle: {ultimo_error}"
