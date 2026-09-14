import streamlit as st
import streamlit.components.v1 as components
from pypdf import PdfReader
from tutor_core import TutorCore
import json
import os
import re
import base64
import zlib
import hashlib
import html
import random
import requests
from io import BytesIO
from datetime import datetime
from PIL import Image as PILImage
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage

# Configuración de página
st.set_page_config(
    page_title="CogniMorph - Tutor IA DMYF UDLA",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

EXTENSIONES_AUDIO = (".mp3", ".m4a", ".wav", ".ogg")
EXTENSIONES_VIDEO = (".mp4", ".mov", ".webm")

LEEME_MATERIALES_TEXTO = """# Materiales de apoyo (audio/video) — cómo agregarlos

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
"""

# --- AUTO-CREACIÓN DE ESTRUCTURA DE DATOS INICIAL ---
def inicializar_archivos_datos():
    os.makedirs(DATA_DIR, exist_ok=True)
    
    materias_setup = {
        "MYF205": {
            "nombre": "MYF205 - Anatomía de Cabeza y Cuello",
            "cronograma": [
                {"evento": "Control 1: Osteología Craneal y Forámenes", "fecha": "12 de Septiembre", "ponderacion": "15%"},
                {"evento": "Taller Práctico 1: Músculos Mímica y Masticación", "fecha": "26 de Septiembre", "ponderacion": "10%"},
                {"evento": "Solemne 1: Pares Craneales V, VII y Vascularización", "fecha": "10 de Octubre", "ponderacion": "35%"},
                {"evento": "Examen Final Integrado", "fecha": "28 de Noviembre", "ponderacion": "40%"}
            ],
            "estudiantes": [
                {"id": "21045892", "nombre": "Juan Pérez", "carrera": "Fonoaudiología", "promedio_previo": 3.4, "riesgo": "ALTO", "tema_debil": "Forámenes de la base del cráneo y Nervio Trigémino"},
                {"id": "20874519", "nombre": "Camila Soto", "carrera": "Fonoaudiología", "promedio_previo": 4.5, "riesgo": "MEDIO", "tema_debil": "Músculos suprahioideos e infrahioideos"},
                {"id": "21980341", "nombre": "María Silva", "carrera": "Fonoaudiología", "promedio_previo": 6.2, "riesgo": "BAJO", "tema_debil": "Ninguno"}
            ]
        },
        "MYF2069": {
            "nombre": "MYF2069 - Fisiología General",
            "cronograma": [
                {"evento": "Control 1: Homeostasis y Transporte de Membrana", "fecha": "15 de Septiembre", "ponderacion": "15%"},
                {"evento": "Taller 1: Potencial de Acción y Sinapsis", "fecha": "29 de Septiembre", "ponderacion": "10%"},
                {"evento": "Solemne 1: Fisiología Muscular y Cardiovascular", "fecha": "14 de Octubre", "ponderacion": "35%"},
                {"evento": "Examen Final Integrado", "fecha": "02 de Diciembre", "ponderacion": "40%"}
            ],
            "estudiantes": [
                {"id": "21045892", "nombre": "Juan Pérez", "carrera": "Licenciatura en Ciencias de la Actividad Física", "promedio_previo": 3.6, "riesgo": "ALTO", "tema_debil": "Biofísica del Potencial de Acción y Bomba Na+/K+"},
                {"id": "20874519", "nombre": "Camila Soto", "carrera": "Licenciatura en Ciencias de la Actividad Física", "promedio_previo": 4.8, "riesgo": "MEDIO", "tema_debil": "Transmisión Sináptica y Neurotransmisores"},
                {"id": "21980341", "nombre": "María Silva", "carrera": "Licenciatura en Ciencias de la Actividad Física", "promedio_previo": 6.0, "riesgo": "BAJO", "tema_debil": "Ninguno"}
            ]
        }
    }

    for cod, info in materias_setup.items():
        mat_folder = os.path.join(DATA_DIR, cod)
        pdf_folder = os.path.join(mat_folder, "clases_pdf")
        os.makedirs(pdf_folder, exist_ok=True)

        cron_file = os.path.join(mat_folder, "cronograma.json")
        if not os.path.exists(cron_file):
            with open(cron_file, "w", encoding="utf-8") as f:
                json.dump(info["cronograma"], f, indent=4, ensure_ascii=False)

        alumn_file = os.path.join(mat_folder, "alumnos_riesgo.json")
        if not os.path.exists(alumn_file):
            with open(alumn_file, "w", encoding="utf-8") as f:
                json.dump(info["estudiantes"], f, indent=4, ensure_ascii=False)

        # Carpeta + índice para material de apoyo (audio/video). Se crea vacía: el tutor no
        # ofrece nada hasta que el profesor agregue archivos y sus entradas en materiales.json.
        materiales_folder = os.path.join(mat_folder, "materiales")
        os.makedirs(materiales_folder, exist_ok=True)

        materiales_file = os.path.join(mat_folder, "materiales.json")
        if not os.path.exists(materiales_file):
            with open(materiales_file, "w", encoding="utf-8") as f:
                json.dump([], f, indent=4, ensure_ascii=False)

        leeme_file = os.path.join(materiales_folder, "LEEME_materiales.md")
        if not os.path.exists(leeme_file):
            with open(leeme_file, "w", encoding="utf-8") as f:
                f.write(LEEME_MATERIALES_TEXTO)

inicializar_archivos_datos()

# --- FUNCIONES DE IMÁGENES ---
def buscar_imagen_inteligente(palabras_clave):
    directorios = [BASE_DIR, os.getcwd()]
    extensiones = ('.png', '.jpg', '.jpeg', '.webp', '.svg')
    for d in directorios:
        if os.path.exists(d):
            try:
                for archivo in os.listdir(d):
                    nombre_min = archivo.lower()
                    if nombre_min.endswith(extensiones):
                        for kw in palabras_clave:
                            if kw.lower() in nombre_min:
                                return os.path.join(d, archivo)
            except Exception:
                pass
    return None

def get_base64_image(image_path):
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode()
    return None

ruta_udla = buscar_imagen_inteligente(["udla", "universidad"])
ruta_cognimorph = buscar_imagen_inteligente(["cogni", "morph", "banner"]) or buscar_imagen_inteligente(["dmyf"])
ruta_avatar = buscar_imagen_inteligente(["avatar", "icono", "hex", "dmyf", "cogni"]) or "🧠"

logo_udla_b64 = get_base64_image(ruta_udla)
udla_img_html = f'<img src="data:image/png;base64,{logo_udla_b64}" style="height: 38px;">' if logo_udla_b64 else '<span style="color:#EA580C; font-weight:bold; font-size:1.4rem;">UDLA</span>'

# --- ESTILOS VISUALES INSTITUCIONALES (CSS UDLA CORREGIDO) ---
st.markdown(f"""
<style>
    /* Ocultar menú superior pero mantener visible el botón de abrir sidebar */
    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}
    
    header[data-testid="stHeader"] {{
        background: transparent !important;
        height: 0px !important;
    }}
    
    /* Botón flotante para recuperar/abrir la barra lateral */
    [data-testid="stSidebarCollapsedControl"] {{
        display: block !important;
        visibility: visible !important;
        color: #EA580C !important;
        background-color: white !important;
        border: 1.5px solid #EA580C !important;
        border-radius: 8px !important;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15) !important;
        top: 12px !important;
        left: 12px !important;
        z-index: 1000000 !important;
    }}
    
    .block-container {{
        padding-top: 0rem;
        padding-bottom: 6rem;
        padding-left: 2rem;
        padding-right: 2rem;
    }}

    .udla-header {{
        background-color: #2D2D2D;
        color: white;
        padding: 12px 25px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-left: -2rem;
        margin-right: -2rem;
        margin-top: -1rem;
        margin-bottom: 25px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.15);
    }}
    .udla-header h1 {{
        font-size: 1.45rem;
        font-weight: 700;
        margin: 0;
        color: #FFFFFF !important;
    }}
    .udla-logo-box {{
        background-color: white;
        padding: 6px 16px;
        border-radius: 6px;
        display: flex;
        align-items: center;
    }}

    section[data-testid="stSidebar"] {{
        background-color: #FAFAFA;
        border-right: 3px solid #EA580C;
    }}
    .sidebar-card-title {{
        font-size: 1.05rem;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: 8px;
        display: flex;
        align-items: center;
        gap: 8px;
    }}

    .active-subject-badge {{
        background-color: #EBF5FF;
        border: 1px solid #D0E7FF;
        color: #1E40AF;
        padding: 10px 18px;
        border-radius: 8px;
        font-size: 0.92rem;
        font-weight: 600;
        margin-bottom: 15px;
        display: inline-block;
        width: 100%;
    }}

    .alert-card-riesgo {{
        background-color: #FEF2F2;
        border: 1.5px solid #EF4444;
        border-radius: 10px;
        padding: 14px 18px;
        margin-bottom: 20px;
        color: #991B1B;
    }}

    /* Banner llamativo invitando al "control completo" (8 SM + 8 pareados) — ver quick actions */
    .cognimorph-banner-control {{
        background: linear-gradient(135deg, #7C2D12 0%, #EA580C 55%, #F97316 100%);
        border-radius: 14px;
        padding: 16px 22px;
        margin-bottom: 18px;
        color: #FFFFFF;
        box-shadow: 0 6px 18px rgba(234,88,12,0.30);
    }}
    .cognimorph-banner-control .titulo-banner {{
        font-size: 1.05rem;
        font-weight: 800;
        margin-bottom: 4px;
    }}
    .cognimorph-banner-control .texto-banner {{
        font-size: 0.9rem;
        color: #FFEDD5;
    }}
    .st-key-cognimorph_btn_control_completo .stButton>button {{
        background-color: #FFFFFF !important;
        color: #C2410C !important;
        border: none !important;
        font-weight: 800 !important;
        font-size: 0.95rem !important;
    }}
    .st-key-cognimorph_btn_control_completo .stButton>button:hover {{
        background-color: #FFEDD5 !important;
        box-shadow: none !important;
    }}

    /* Términos pareados (render_pareados): por defecto el selectbox de Streamlit corta con "..."
       cualquier definición que no quepa en una sola línea — con definiciones largas, el estudiante
       no alcanza a leer la alternativa completa. Esto fuerza que tanto el valor ya elegido (cerrado)
       como cada opción de la lista desplegable (abierta) puedan ocupar más de una línea. */
    div[data-baseweb="select"] > div {{
        height: auto !important;
        min-height: 42px !important;
    }}
    div[data-baseweb="select"] > div > div {{
        white-space: normal !important;
        word-break: break-word !important;
        line-height: 1.3 !important;
    }}
    div[data-baseweb="popover"] li,
    div[data-baseweb="popover"] div[role="option"] {{
        white-space: normal !important;
        word-break: break-word !important;
        line-height: 1.3 !important;
        min-height: 38px !important;
    }}

    .stButton>button {{
        border: 1.5px solid #EA580C;
        border-radius: 8px;
        color: #EA580C;
        background-color: white;
        font-weight: 600;
        font-size: 0.85rem;
        padding: 8px 12px;
        width: 100%;
        transition: all 0.25s ease;
    }}
    .stButton>button:hover {{
        background-color: #EA580C;
        color: white;
        border-color: #EA580C;
        box-shadow: 0 4px 10px rgba(234, 88, 12, 0.25);
    }}

    .stChatInput {{
        margin-bottom: 40px !important;
    }}

    .udla-footer {{
        position: fixed;
        left: 0;
        bottom: 0;
        width: 100%;
        background-color: #2D2D2D;
        color: #FFFFFF;
        padding: 12px 35px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.92rem;
        font-weight: 600;
        z-index: 999999;
        box-shadow: 0 -2px 6px rgba(0,0,0,0.25);
    }}

    /* Reproductor flotante de materiales de apoyo (audio/video) — ver render_material() */
    .st-key-cognimorph_reproductor_flotante {{
        position: fixed !important;
        bottom: 190px;
        right: 22px;
        width: 350px;
        max-width: 90vw;
        max-height: 52vh;
        overflow-y: auto;
        background-color: #FFFFFF;
        border: 2px solid #EA580C;
        border-radius: 14px;
        padding: 14px 16px 6px 16px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.28);
        z-index: 1000010;
    }}
    .cognimorph-flotante-titulo {{
        font-weight: 800;
        color: #7C2D12;
        font-size: 0.95rem;
        display: flex;
        align-items: center;
        gap: 6px;
        margin-bottom: 2px;
    }}
    .cognimorph-flotante-item-titulo {{
        font-weight: 700;
        color: #1E293B;
        font-size: 0.95rem;
    }}
    .cognimorph-flotante-item-desc {{
        color: #57534E;
        font-size: 0.82rem;
        margin-bottom: 4px;
    }}
    .cognimorph-flotante-hint {{
        color: #C2410C;
        font-size: 0.78rem;
        font-weight: 700;
        margin: 4px 0 10px 0;
    }}
    .st-key-cognimorph_reproductor_flotante .stButton>button {{
        border: none;
        color: #9A3412;
        background-color: #FFEDD5;
        font-weight: 700;
        padding: 2px 10px;
        width: auto;
        font-size: 0.85rem;
    }}
    .st-key-cognimorph_reproductor_flotante .stButton>button:hover {{
        background-color: #EA580C;
        color: white;
        box-shadow: none;
    }}

    /* Tarjeta llamativa dentro del chat cuando se ofrece/abre un material de apoyo */
    .cognimorph-tarjeta-material {{
        background: linear-gradient(135deg, #FFF7ED 0%, #FFEDD5 100%);
        border: 2.5px solid #EA580C;
        border-radius: 14px;
        padding: 16px 20px;
        margin: 12px 0;
        box-shadow: 0 4px 14px rgba(234,88,12,0.22);
        animation: cognimorph-pulso-material 1.6s ease-in-out 3;
    }}
    @keyframes cognimorph-pulso-material {{
        0%, 100% {{ box-shadow: 0 4px 14px rgba(234,88,12,0.22); }}
        50% {{ box-shadow: 0 4px 26px rgba(234,88,12,0.55); }}
    }}
    .cognimorph-tarjeta-material .badge-material {{
        display: inline-block;
        background-color: #EA580C;
        color: #FFFFFF;
        font-size: 0.7rem;
        font-weight: 800;
        letter-spacing: 0.04em;
        padding: 3px 11px;
        border-radius: 999px;
        margin-bottom: 10px;
        text-transform: uppercase;
    }}
    .cognimorph-tarjeta-material .fila-titulo-material {{
        display: flex;
        align-items: center;
        gap: 10px;
    }}
    .cognimorph-tarjeta-material .icono-material {{
        font-size: 2.1rem;
        line-height: 1;
    }}
    .cognimorph-tarjeta-material .titulo-material {{
        font-weight: 800;
        color: #7C2D12;
        font-size: 1.15rem;
    }}
    .cognimorph-tarjeta-material .desc-material {{
        color: #9A3412;
        font-size: 0.92rem;
        margin-top: 6px;
    }}
    .cognimorph-tarjeta-material .cta-material {{
        display: inline-block;
        margin-top: 12px;
        font-size: 0.88rem;
        color: #FFFFFF;
        background-color: #EA580C;
        font-weight: 700;
        padding: 8px 14px;
        border-radius: 8px;
    }}
</style>
""", unsafe_allow_html=True)

# --- CARGAR RECURSOS DEL CURSO AUTOMÁTICAMENTE ---
def cargar_cronograma(cod_materia):
    ruta = os.path.join(DATA_DIR, cod_materia, "cronograma.json")
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def cargar_contenidos_evaluaciones(cod_materia):
    """
    Lee data/{cod_materia}/contenidos_evaluaciones.json: el detalle de QUÉ contenidos (unidades/
    temas) entran en cada instrumento de evaluación (CAT, EPOE, Ejercicios, etc.). Esto es
    DISTINTO del cronograma.json (que trae las FECHAS) — el profesor dejó explícito que un
    documento no se correlaciona directamente con el otro (nombres/agrupaciones distintas), así
    que se cargan y se le entregan al tutor como dos fuentes separadas: cronograma.json para
    fechas, este archivo para contenidos. Si no existe (como en MYF2069 por ahora), devuelve []
    y el tutor simplemente no tiene esa fuente disponible para esa asignatura.
    """
    ruta = os.path.join(DATA_DIR, cod_materia, "contenidos_evaluaciones.json")
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _alias_evaluacion(nombre):
    """
    A partir de un nombre de evaluación como "CAT 4 (EPOE 1)", genera las formas alternativas con
    las que un estudiante podría referirse a ella en el chat: el nombre completo, lo que está
    entre paréntesis ("EPOE 1"), lo que está antes del paréntesis ("CAT 4"), y lo que está antes
    de ":" si lo hay (ej. "Ejercicio 1" en "Ejercicio 1: MiniEpoe").
    """
    alias = {nombre}
    m = re.search(r"^(.*?)\s*\(([^)]+)\)\s*$", nombre)
    if m:
        alias.add(m.group(1).strip())
        alias.add(m.group(2).strip())
    if ":" in nombre:
        alias.add(nombre.split(":")[0].strip())
    return {a for a in alias if a}


def _buscar_evaluacion_contenidos(contenidos_evaluaciones, texto):
    """
    Busca, dentro de un mensaje de chat, si el estudiante mencionó el nombre (o algún alias, ver
    _alias_evaluacion) de alguna evaluación de contenidos_evaluaciones.json. Devuelve la entrada
    completa {"evaluacion", "ponderacion", "contenidos"} si hay una coincidencia de nombre COMPLETA
    (como palabra/frase exacta, con límites de palabra — así "CAT 1" nunca matchea dentro de
    "CAT 10"), o None si no hay ninguna. Nunca "adivina" por parecido parcial, para no mezclar el
    contenido de una evaluación con el de otra.
    """
    if not contenidos_evaluaciones or not texto:
        return None
    texto_norm = re.sub(r"\s+", " ", texto)
    for entrada in contenidos_evaluaciones:
        nombre = entrada.get("evaluacion") or ""
        if not nombre:
            continue
        for alias in _alias_evaluacion(nombre):
            tokens = alias.split()
            if not tokens:
                continue
            patron = r"\b" + r"\s+".join(re.escape(t) for t in tokens) + r"\b"
            if re.search(patron, texto_norm, re.IGNORECASE):
                return entrada
    return None


def cargar_estudiantes(cod_materia):
    ruta = os.path.join(DATA_DIR, cod_materia, "alumnos_riesgo.json")
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def cargar_materiales(cod_materia):
    """
    Lee data/{cod_materia}/materiales.json (índice de audio/video de apoyo) y resuelve la ruta
    completa de cada archivo dentro de la carpeta materiales/. Nunca revienta la app: un JSON mal
    formado, una entrada incompleta o un archivo que todavía no existe simplemente no se ofrece
    (o se avisa al reproducirlo), no interrumpen el resto del tutor.

    Devuelve (lista_materiales, aviso): "aviso" es None si todo está en orden, o un mensaje breve
    para mostrar en la barra lateral si algo necesita revisión (JSON corrupto, entradas incompletas,
    o archivos cuyo nombre en "archivo" no coincide con ningún archivo real en la carpeta). Antes
    este tipo de problema se tragaba en silencio (devolvía [] o ignoraba la entrada sin avisar),
    lo que hacía muy difícil notar por qué el tutor "no encontraba" un material que sí existía.
    """
    ruta_json = os.path.join(DATA_DIR, cod_materia, "materiales.json")
    ruta_carpeta = os.path.join(DATA_DIR, cod_materia, "materiales")
    if not os.path.exists(ruta_json):
        return [], None

    try:
        with open(ruta_json, "r", encoding="utf-8") as f:
            entradas = json.load(f)
    except Exception as e:
        return [], f"materiales.json tiene un error de formato y no se pudo leer ({e})."

    if not isinstance(entradas, list):
        return [], "materiales.json debería contener una lista [ ... ] de materiales; revisa su estructura."

    materiales = []
    incompletas = 0
    for entrada in entradas:
        if not isinstance(entrada, dict) or not entrada.get("id") or not entrada.get("archivo"):
            incompletas += 1
            continue
        ruta_completa = os.path.join(ruta_carpeta, entrada["archivo"])
        materiales.append({**entrada, "ruta_completa": ruta_completa, "existe": os.path.exists(ruta_completa)})

    avisos = []
    if incompletas:
        avisos.append(f"{incompletas} entrada(s) sin 'id' y/o 'archivo' válidos (se ignoraron).")

    faltantes = [m["archivo"] for m in materiales if not m["existe"]]
    if faltantes:
        muestra = ", ".join(faltantes[:3]) + ("…" if len(faltantes) > 3 else "")
        avisos.append(
            f"{len(faltantes)} archivo(s) listado(s) en materiales.json no se encontraron en la "
            f"carpeta 'materiales' (revisa que el nombre coincida EXACTO, mayúsculas/tildes incluidas): {muestra}"
        )

    return materiales, ("; ".join(avisos) if avisos else None)


def buscar_material_por_id(materiales_disponibles, material_id):
    for m in materiales_disponibles or []:
        if m.get("id") == material_id:
            return m
    return None


def cargar_textos_pdfs_materia(cod_materia):
    carpeta_pdfs = os.path.join(DATA_DIR, cod_materia, "clases_pdf")
    texto_total = ""
    if os.path.exists(carpeta_pdfs):
        for arch in os.listdir(carpeta_pdfs):
            if arch.lower().endswith(".pdf"):
                try:
                    reader = PdfReader(os.path.join(carpeta_pdfs, arch))
                    for p in reader.pages:
                        texto_total += (p.extract_text() or "") + "\n"
                except Exception:
                    pass
    return texto_total[:15000]

# --- SEGUIMIENTO DE PROGRESO Y QUIZZES ---
class StudentTracker:
    def __init__(self, storage_file="progreso_estudiante.json"):
        self.storage_file = os.path.join(BASE_DIR, storage_file)
        self.data = self._load_data()

    def _load_data(self):
        default = {"sesiones_chat": 0, "quizzes_intentos": 0, "ultimo_puntaje": None}
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in default.items():
                        if k not in data:
                            data[k] = v
                    return data
            except Exception:
                pass
        return default

    def save(self):
        with open(self.storage_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4, ensure_ascii=False)

    def registrar_consulta(self):
        self.data["sesiones_chat"] = self.data.get("sesiones_chat", 0) + 1
        self.save()

    def registrar_quiz(self, nota):
        self.data["quizzes_intentos"] = self.data.get("quizzes_intentos", 0) + 1
        self.data["ultimo_puntaje"] = nota
        self.save()

# --- PRESENTACIÓN DE RESPUESTAS: separar texto normal de bloques especiales ---
# (Mermaid / material de apoyo / mini controles interactivos)
_PATRON_BLOQUES_ESPECIALES = re.compile(
    r"```\s*(mermaid|material|quiz|pareados)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE
)

# --- DETECCIÓN DE PEDIDOS DE "CONTROL" ESCRITOS DIRECTO EN EL CHAT ---
# Antes, si el estudiante escribía "quiero hacer un control" en el chat (en vez de usar el botón),
# el mensaje se iba por el flujo conversacional normal (tutor.responder), que demostró no ser
# confiable armando controles grandes en el formato correcto (ver la nota junto a
# ControlGeneradoSchema en tutor_core.py). Con este patrón, un pedido así se detecta ANTES de
# mandarlo al tutor conversacional y se redirige al mismo camino determinístico
# (TutorCore.generar_control) que usa el botón del banner — ver más abajo, junto a "control_pendiente".
_PATRON_SOLICITUD_CONTROL = re.compile(
    r"\b(control(?:es)?|quiz(?:zes)?|prueba\s+corta|autoevaluaci[oó]n|evaluaci[oó]n\s+formativa)\b",
    re.IGNORECASE,
)

# En Chile "control" también significa "examen calificado", así que alguien puede preguntar "¿cuándo
# es el próximo control?" (quiere la FECHA, no que se lo generemos). Ese caso ya lo cubre el botón
# "¿Cuándo son las próximas evaluaciones?"/el punto 0 del prompt (cronograma), así que si el mensaje
# suena a pregunta de fecha/calendario, NO lo tratamos como pedido de generar un control nuevo.
_PATRON_PREGUNTA_FECHA_CONTROL = re.compile(
    r"\b(cu[aá]ndo|qu[eé]\s+d[ií]a|fecha|fechas|calendario)\b",
    re.IGNORECASE,
)

# --- DETECCIÓN DE PREGUNTAS POR EL CONTENIDO DE UNA EVALUACIÓN PUNTUAL (ej. "CAT 1", "EPOE 2") ---
# Cuando el estudiante pregunta qué contenidos entran en una evaluación puntual, la respuesta es un
# dato exacto que ya está en contenidos_evaluaciones.json (ver cargar_contenidos_evaluaciones) — no
# algo que requiera que el modelo "razone" ni redacte. Pedirle al tutor conversacional que copie esa
# lista completa desde el contexto demostró no ser 100% confiable (se comió un ítem de la lista de
# "CAT 1" en la práctica), así que estas preguntas se resuelven directo en código — ver
# _buscar_evaluacion_contenidos más arriba y su uso junto a "control_pendiente" más abajo — igual
# que se hizo con la generación de controles (ver la nota junto a ControlGeneradoSchema en
# tutor_core.py): un dato exacto se busca en código, no se le pide de memoria al modelo.
_PATRON_PREGUNTA_CONTENIDO_EVALUACION = re.compile(
    r"\b(contenidos?|temas?|unidades?|materias?|entra|entran|incluye|incluyen|abarca|abarcan|"
    r"comprende|comprenden)\b",
    re.IGNORECASE,
)

# Palabras genéricas del pedido en sí ("quiero hacer un control de...") que se descartan al tratar
# de rescatar SOLO el tema/tópico desde un mensaje de chat — ver _extraer_tema_control.
_PALABRAS_VACIAS_CONTROL = re.compile(
    r"\b(quiero|quisiera|hazme|h[aá]cerme|hacer|realizar|armar|arma|dame|puedes|podr[ií]as|hacer|"
    r"un|una|unos|unas|el|la|los|las|de|del|al|para|sobre|acerca|con|en|"
    r"practicar|preguntas|pregunta|control|controles|quiz|quizzes|autoevaluaci[oó]n|autoevaluarme|"
    r"prueba|corta|evaluaci[oó]n|formativa|me|te|se|gustar[ií]a|por\s+favor|porfa|si|s[ií])\b",
    re.IGNORECASE,
)


def _extraer_tema_control(texto):
    """
    A partir de un mensaje de chat que pide un control (ver _PATRON_SOLICITUD_CONTROL), intenta
    quedarse solo con el tema/tópico mencionado, quitando las palabras genéricas del pedido en sí
    ("quiero hacer un control de...", "hazme un quiz sobre..."). Si después de eso no queda nada
    reconocible, devuelve "" — TutorCore.generar_control interpreta eso como "todo el contenido de
    la asignatura visto hasta ahora".
    """
    limpio = _PALABRAS_VACIAS_CONTROL.sub(" ", texto or "")
    limpio = re.sub(r"\s+", " ", limpio).strip(" ,.;:¿?¡!")
    return limpio if len(limpio) >= 4 else ""


def extraer_bloques(texto):
    """
    Divide la respuesta del tutor en bloques ('texto' | 'mermaid' | 'material' | 'quiz' |
    'pareados'), detectando los ```mermaid ... ``` (esquemas/mapas conceptuales), ```material ...
    ``` (id de un audio/video de apoyo, ver materiales.json) y ```quiz ... ```/```pareados ... ```
    (mini controles interactivos, ver render_quiz/render_pareados) que TutorCore puede incluir
    dentro de la respuesta.
    """
    partes = []
    ultimo = 0
    for m in _PATRON_BLOQUES_ESPECIALES.finditer(texto):
        antes = texto[ultimo:m.start()].strip()
        if antes:
            partes.append(("texto", antes))
        tipo_bloque = m.group(1).strip().lower()
        partes.append((tipo_bloque, m.group(2).strip()))
        ultimo = m.end()
    resto = texto[ultimo:].strip()
    if resto:
        partes.append(("texto", resto))
    if not partes:
        partes.append(("texto", texto))
    return partes


def render_mermaid(codigo_mermaid, height=480):
    """
    Renderiza un diagrama Mermaid en el navegador vía CDN (sin instalar nada localmente).
    useMaxWidth=False evita que Mermaid achique el texto para que el diagrama "quepa" en el
    ancho disponible (eso es lo que lo hacía ilegible en esquemas grandes); en vez de achicar
    letra, el diagrama se dibuja a tamaño real y el contenedor scrollea si es más ancho o alto
    que el espacio visible.

    Si el código que generó el modelo tiene un error de sintaxis, Mermaid por defecto muestra un
    ícono de "bomba" sin más contexto. Acá se captura ese error (con mermaid.render + try/catch en
    JS) y en su lugar se muestra el código del diagrama en texto plano, para poder ver qué salió
    mal en vez de solo un ícono roto.
    """
    codigo_js = json.dumps(codigo_mermaid)
    html = f"""
    <div style="width:100%; height:{height-20}px; overflow:auto; border:1px solid #eee;
                border-radius:6px; padding:8px; box-sizing:border-box; font-family:sans-serif;">
      <div id="cognimorph-mermaid-target">Dibujando diagrama…</div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <script>
      mermaid.initialize({{
        startOnLoad: false,
        flowchart: {{ useMaxWidth: false }},
        themeVariables: {{ fontSize: '16px' }}
      }});
      const codigoDiagrama = {codigo_js};
      const objetivo = document.getElementById('cognimorph-mermaid-target');
      mermaid.render('cognimorph-svg', codigoDiagrama).then(function(resultado) {{
        objetivo.innerHTML = resultado.svg;
      }}).catch(function(err) {{
        const escapado = codigoDiagrama.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        objetivo.innerHTML =
          '<div style="color:#991B1B; margin-bottom:8px;">⚠️ Este diagrama tiene un error de ' +
          'sintaxis y no se pudo dibujar. Código del diagrama (cópialo si necesitas reportarlo):</div>' +
          '<pre style="white-space:pre-wrap; background:#FEF2F2; border:1px solid #EF4444; ' +
          'padding:10px; border-radius:6px; font-size:13px;">' + escapado + '</pre>';
      }});
    </script>
    """
    components.html(html, height=height, scrolling=True)


def _icono_material(entrada):
    tipo = entrada.get("tipo")
    if tipo == "video":
        return "🎬"
    if tipo == "podcast":
        return "🎙️"
    return "🎵"


def abrir_material_flotante(material_id, forzar=False):
    """
    Marca un material como "abierto" en el reproductor flotante (ver mostrar_reproductor_flotante).
    forzar=True se usa solo para una respuesta RECIÉN generada (el estudiante lo acaba de pedir):
    eso anula un cierre previo, porque un pedido explícito nuevo debe volver a abrirlo aunque el
    estudiante haya cerrado ese mismo material antes en la conversación.
    """
    if forzar:
        st.session_state.materiales_cerrados.discard(material_id)
    if material_id in st.session_state.materiales_cerrados:
        return
    if material_id not in st.session_state.materiales_flotantes:
        st.session_state.materiales_flotantes.append(material_id)


def cerrar_material_flotante(material_id):
    st.session_state.materiales_cerrados.add(material_id)
    if material_id in st.session_state.materiales_flotantes:
        st.session_state.materiales_flotantes.remove(material_id)


def render_material(material_id, materiales_disponibles, es_nuevo=False):
    """
    Cuando el tutor incluye un bloque ```material``` en su respuesta (solo debería pasar cuando el
    estudiante ya pidió escuchar/ver ese material puntual — ver punto 6 del prompt en
    tutor_core.py), esto NO reproduce el audio/video aquí mismo dentro del chat: deja una tarjeta
    llamativa confirmando qué se abrió, y el reproductor real aparece en la ventana flotante fija
    (mostrar_reproductor_flotante) para que el estudiante pueda seguir escribiéndole al tutor sin
    perder de vista el reproductor ni tener que buscarlo entre los mensajes.
    Si el id no está en el índice, o el archivo físico todavía no existe en la carpeta
    "materiales" (el profesor aún no lo subió, o el modelo se equivocó de id), avisa con un
    mensaje breve en vez de romper la app.
    """
    entrada = buscar_material_por_id(materiales_disponibles, material_id)
    if not entrada:
        st.info(f"🎧 Se mencionó un material de apoyo (`{material_id}`) que no está en el índice todavía.")
        return
    if not entrada.get("existe"):
        st.info(
            f"🎧 **{entrada.get('titulo', material_id)}** — el archivo todavía no está cargado en "
            "la carpeta de materiales; avísale al profesor si esperabas poder escucharlo/verlo."
        )
        return

    abrir_material_flotante(material_id, forzar=es_nuevo)

    titulo = html.escape(entrada.get("titulo") or material_id)
    descripcion = entrada.get("descripcion")
    icono = _icono_material(entrada)
    etiqueta_tipo = {"cancion": "Canción", "podcast": "Podcast", "video": "Video"}.get(
        entrada.get("tipo"), "Material"
    )

    # OJO: se arma como UNA sola cadena sin saltos de línea ni indentación entre etiquetas. Un
    # bloque de HTML "bonito" con indentación (como antes) hace que Streamlit/Markdown interprete
    # una línea en blanco en medio (ej. cuando no hay "descripcion") como el FIN del bloque HTML, y
    # el resto se muestra como texto plano con las etiquetas visibles — por eso se une todo así.
    piezas = [
        '<div class="cognimorph-tarjeta-material">',
        f'<span class="badge-material">🎉 Material de apoyo · {etiqueta_tipo}</span>',
        '<div class="fila-titulo-material">',
        f'<span class="icono-material">{icono}</span>',
        f'<span class="titulo-material">{titulo}</span>',
        "</div>",
    ]
    if descripcion:
        piezas.append(f'<div class="desc-material">{html.escape(descripcion)}</div>')
    piezas.append(
        '<div class="cta-material">▶️ Dale play abajo a la derecha y sigue estudiando '
        "conmigo mientras suena — puedes pausarlo o cerrarlo (✕) cuando quieras.</div>"
    )
    piezas.append("</div>")
    st.markdown("".join(piezas), unsafe_allow_html=True)


def mostrar_reproductor_flotante(materiales_disponibles):
    """
    Dibuja la ventana flotante fija (position:fixed, ver CSS '.st-key-cognimorph_reproductor_flotante')
    con los materiales que el estudiante ha pedido escuchar/ver y todavía no ha cerrado. Se llama una
    sola vez por ejecución del script, en un punto fijo del cuerpo principal (después de resolver la
    asignatura activa en la barra lateral) — así, mientras la lista de materiales abiertos no cambie,
    Streamlit reutiliza el mismo reproductor de audio/video entre reruns (por ejemplo, cuando el
    estudiante manda un mensaje nuevo) en vez de recrearlo, que es lo que le permite seguir sonando
    sin reiniciarse mientras la conversación sigue.
    No dibuja nada si no hay ningún material abierto (para no dejar un recuadro vacío flotando).
    """
    ids_activos = list(st.session_state.get("materiales_flotantes", []))
    if not ids_activos:
        return

    contenedor = st.container(key="cognimorph_reproductor_flotante")
    with contenedor:
        st.markdown(
            '<div class="cognimorph-flotante-titulo">🎧 Escuchando/viendo ahora</div>',
            unsafe_allow_html=True,
        )
        for material_id in ids_activos:
            entrada = buscar_material_por_id(materiales_disponibles, material_id)
            if not entrada or not entrada.get("existe"):
                # El archivo se movió/borró después de abrirse: lo sacamos en silencio.
                cerrar_material_flotante(material_id)
                continue

            col_titulo, col_cerrar = st.columns([5, 1])
            with col_titulo:
                st.markdown(
                    f'<div class="cognimorph-flotante-item-titulo">{_icono_material(entrada)} '
                    f'{entrada.get("titulo") or material_id}</div>',
                    unsafe_allow_html=True,
                )
            with col_cerrar:
                if st.button("✕", key=f"cerrar_flot_{material_id}", help="Cerrar este reproductor"):
                    cerrar_material_flotante(material_id)
                    st.rerun()

            if entrada.get("descripcion"):
                st.markdown(
                    f'<div class="cognimorph-flotante-item-desc">{entrada["descripcion"]}</div>',
                    unsafe_allow_html=True,
                )

            extension = os.path.splitext(entrada["archivo"])[1].lower()
            try:
                if extension in EXTENSIONES_VIDEO:
                    st.video(entrada["ruta_completa"])
                else:
                    st.audio(entrada["ruta_completa"])
            except Exception:
                st.warning("No se pudo reproducir este archivo (formato no soportado o archivo dañado).")

            st.markdown(
                '<div class="cognimorph-flotante-hint">▶️ Sigue conversando abajo mientras esto '
                'suena — pon pausa o cierra (✕) cuando termines.</div>',
                unsafe_allow_html=True,
            )
            st.markdown("---")


def render_quiz(contenido_json, idx_bloque):
    """
    Dibuja un mini control de selección múltiple a partir de un bloque ```quiz``` (ver punto 7 del
    prompt en tutor_core.py): una o más preguntas, cada una con sus alternativas. Apenas el
    estudiante elige una alternativa (st.radio con index=None, sin nada preseleccionado), se
    muestra al tiro si acertó o no, con una frase que orienta en caso de error — no hace falta
    ningún botón de "enviar" por pregunta.
    Si el bloque no es JSON válido o le faltan campos, avisa en vez de romper la respuesta completa.

    OJO CON LAS KEYS: key_sufijo NO se usa para las keys de los widgets (aunque se recibe como
    parámetro) porque cambia de nombre para el MISMO mensaje según el momento en que se dibuja —
    "nueva_N" la primera vez que responde el tutor, "hist_N" de ahí en adelante (mismo N, prefijo
    distinto). Streamlit trata una key distinta como un widget NUEVO y le borra la selección hecha
    bajo la key anterior — eso hacía que las alternativas parecieran "no clickeables": el estudiante
    elegía una, el clic disparaba un rerun, y ese rerun ya dibujaba el radio con la otra key (sin
    nada elegido). Por eso la key se arma desde un hash del contenido del bloque, que es idéntico
    sin importar si se está dibujando como respuesta recién generada o como parte del historial.
    """
    try:
        datos = json.loads(contenido_json)
        preguntas = datos["preguntas"]
        if not isinstance(preguntas, list) or not preguntas:
            raise ValueError("El bloque quiz no trae ninguna pregunta.")
    except Exception as e:
        st.warning(f"⚠️ Este mini control no se pudo mostrar (formato inválido: {e}).")
        return

    hash_contenido = hashlib.sha1(contenido_json.encode("utf-8")).hexdigest()[:12]
    key_base = f"quiz_{hash_contenido}_{idx_bloque}"
    respondidas = 0
    aciertos = 0

    for i, pregunta in enumerate(preguntas):
        try:
            texto_pregunta = pregunta["pregunta"]
            alternativas = pregunta["alternativas"]
            idx_correcta = pregunta["correcta"]
            if not (0 <= idx_correcta < len(alternativas)):
                raise ValueError("el índice de 'correcta' no calza con la lista de alternativas")
        except Exception as e:
            st.warning(f"⚠️ La pregunta {i + 1} de este control no se pudo mostrar ({e}).")
            continue

        st.markdown(f"**{i + 1}. {texto_pregunta}**")
        key_pregunta = f"{key_base}_p{i}"
        idx_elegida = st.radio(
            "Elige una alternativa:",
            options=list(range(len(alternativas))),
            format_func=lambda j, alts=alternativas: alts[j],
            index=None,
            key=key_pregunta,
            label_visibility="collapsed",
        )
        if idx_elegida is not None:
            respondidas += 1
            if idx_elegida == idx_correcta:
                aciertos += 1
                st.success(f"✅ ¡Correcto! {pregunta.get('retroalimentacion_correcta', '')}".strip())
            else:
                st.error(f"❌ No es esa. {pregunta.get('retroalimentacion_incorrecta', '')}".strip())
        st.markdown("")

    if preguntas and respondidas == len(preguntas):
        st.info(f"🏁 **Resultado: {aciertos} de {len(preguntas)} correctas.**")


def render_pareados(contenido_json, idx_bloque):
    """
    Dibuja un mini control de términos pareados a partir de un bloque ```pareados``` (ver punto 7
    del prompt en tutor_core.py): una lista de {termino, definicion}. Cada término tiene un menú
    desplegable con TODAS las definiciones (mezcladas una sola vez y guardadas en session_state
    para que el orden no cambie en cada rerun) — el botón para revisar recién aparece cuando los
    ocho (o los que sean) ya tienen una definición elegida, y ahí se muestran todos los resultados
    juntos, como pidió el profesor.
    Si el bloque no es JSON válido o le faltan campos, avisa en vez de romper la respuesta completa.

    OJO CON LAS KEYS: igual que en render_quiz, key_sufijo NO se usa para las keys de los widgets
    (cambia de "nueva_N" a "hist_N" para el mismo mensaje entre el momento en que se genera y
    cualquier rerun posterior, y una key distinta borra la selección hecha bajo la anterior). La key
    se arma desde un hash del contenido del bloque, estable sin importar desde qué parte del script
    se esté dibujando.
    """
    try:
        datos = json.loads(contenido_json)
        terminos = datos["terminos"]
        if not isinstance(terminos, list) or len(terminos) < 2:
            raise ValueError("el bloque pareados necesita al menos 2 términos.")
        for t in terminos:
            if not t.get("termino") or not t.get("definicion"):
                raise ValueError("hay un término sin 'termino' o sin 'definicion'.")
    except Exception as e:
        st.warning(f"⚠️ Este mini control no se pudo mostrar (formato inválido: {e}).")
        return

    hash_contenido = hashlib.sha1(contenido_json.encode("utf-8")).hexdigest()[:12]
    key_base = f"pareados_{hash_contenido}_{idx_bloque}"
    key_orden = f"{key_base}_orden_defs"
    if key_orden not in st.session_state:
        definiciones_mezcladas = [t["definicion"] for t in terminos]
        random.shuffle(definiciones_mezcladas)
        st.session_state[key_orden] = definiciones_mezcladas
    definiciones_mezcladas = st.session_state[key_orden]

    if datos.get("instrucciones"):
        st.markdown(f"*{datos['instrucciones']}*")

    PLACEHOLDER = "— Selecciona una definición —"
    elegidas = {}
    for i, t in enumerate(terminos):
        # El término y su selectbox van UNO ENCIMA DEL OTRO (no en columnas lado a lado): así el
        # menú desplegable aprovecha todo el ancho disponible en vez de la mitad, que es lo que
        # cortaba las definiciones más largas con "..." antes de que se alcanzaran a leer completas
        # (ver también el CSS de white-space/word-break junto a "Términos pareados" más arriba).
        st.markdown(f"**{t['termino']}**")
        elegidas[i] = st.selectbox(
            f"Definición para {t['termino']}",
            options=[PLACEHOLDER] + definiciones_mezcladas,
            key=f"{key_base}_sel_{i}",
            label_visibility="collapsed",
        )
        st.markdown("")

    todas_respondidas = all(v != PLACEHOLDER for v in elegidas.values())
    key_revisado = f"{key_base}_revisado"

    if not todas_respondidas:
        st.caption(f"Completa los {len(terminos)} términos para poder revisar tus respuestas.")
        return

    if st.button("✅ Revisar mis respuestas", key=f"{key_base}_btn_revisar"):
        st.session_state[key_revisado] = True

    if st.session_state.get(key_revisado):
        aciertos = 0
        for i, t in enumerate(terminos):
            correcto = elegidas[i] == t["definicion"]
            aciertos += correcto
            if correcto:
                st.success(f"✅ **{t['termino']}** → {elegidas[i]}")
            else:
                st.error(f"❌ **{t['termino']}** → elegiste: {elegidas[i]}\n\n   Correcta: {t['definicion']}")
        st.info(f"🏁 **Resultado: {aciertos} de {len(terminos)} correctas.**")


def _formatear_control_como_bloques(datos, tema=None):
    """
    Convierte el dict estructurado que devuelve TutorCore.generar_control() (JSON forzado por
    schema — ver la nota junto a ControlGeneradoSchema en tutor_core.py) en el mismo formato de
    texto con bloques ```quiz```/```pareados``` que ya sabe dibujar mostrar_respuesta_tutor. Así
    se reutiliza TODA la infraestructura existente (widgets clickeables, descarga a PDF,
    reproductor flotante) sin cambios, sin depender de que el modelo "se acuerde" de escribir los
    bloques a mano dentro de una respuesta conversacional libre — que es justo lo que fallaba
    antes con controles grandes (8 preguntas + 8 pareados).
    """
    preguntas = (datos or {}).get("preguntas") or []
    terminos = (datos or {}).get("terminos") or []

    tema_limpio = (tema or "").strip()
    partes = [f"📝 **Aquí tienes tu control{f' de {tema_limpio}' if tema_limpio else ''}.**"]

    if preguntas:
        partes.append(
            "**Parte 1 — Selección múltiple**\n```quiz\n"
            + json.dumps({"preguntas": preguntas}, ensure_ascii=False)
            + "\n```"
        )
    if terminos:
        partes.append(
            "**Parte 2 — Términos pareados**\n```pareados\n"
            + json.dumps(
                {"instrucciones": "Selecciona la definición correcta para cada término.", "terminos": terminos},
                ensure_ascii=False,
            )
            + "\n```"
        )
    if not preguntas and not terminos:
        partes.append("⚠️ No se generó ninguna pregunta ni término pareado para este control; intenta pedirlo de nuevo.")
    else:
        partes.append("¡Mucho éxito! Cuando termines, cuéntame si quieres repasar algo puntual o pedir otro control.")

    return "\n\n".join(partes)


def _codificar_mermaid_pako(codigo_mermaid):
    """Codifica el diagrama en el formato que usan mermaid.live / mermaid.ink para generar
    una imagen a partir del texto del diagrama (comprimido con deflate + base64 url-safe)."""
    estado = {"code": codigo_mermaid, "mermaid": {"theme": "default"}}
    payload = json.dumps(estado).encode("utf-8")
    comprimido = zlib.compress(payload, 9)
    return base64.urlsafe_b64encode(comprimido).decode().rstrip("=")


def obtener_png_mermaid(codigo_mermaid, ancho=1000, timeout=12):
    """
    Pide a mermaid.ink (servicio público) que renderice el diagrama como PNG, para poder
    incrustarlo en el PDF. Si algo falla (sin internet, formato no aceptado, servicio caído),
    devuelve None y quien llama debe seguir funcionando sin el diagrama incrustado.
    Nota: esto envía el CÓDIGO del diagrama (los textos de las etiquetas, no datos del
    estudiante) a un servicio externo — razonable para contenido académico, pero queda dicho.
    """
    try:
        codigo_codificado = _codificar_mermaid_pako(codigo_mermaid)
        url = f"https://mermaid.ink/img/pako:{codigo_codificado}?type=png&width={ancho}&bgColor=white"
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return r.content
    except Exception:
        pass
    return None


def obtener_png_mermaid_cacheado(codigo_mermaid, ancho=1000):
    """
    Envuelve obtener_png_mermaid() con una caché en session_state, indexada por el contenido
    del diagrama. Evita pedirle la misma imagen a mermaid.ink dos veces (una para el botón de
    descarga PNG del esquema y otra al incrustarlo en el PDF) y evita repetir la llamada de red
    en cada rerun de Streamlit mientras el diagrama no cambie.
    """
    cache = st.session_state.setdefault("cache_pngs_mermaid", {})
    clave = hashlib.sha1(codigo_mermaid.encode("utf-8")).hexdigest()
    if clave not in cache:
        cache[clave] = obtener_png_mermaid(codigo_mermaid, ancho=ancho)
    return cache[clave]


_SUB_SUPER_MAP = str.maketrans({
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁺": "+", "⁻": "-",
})


def _md_inline_a_reportlab(texto):
    # ReportLab (fuentes base) no soporta glifos de sub/superíndice unicode: se aplanan antes de escapar.
    texto = texto.translate(_SUB_SUPER_MAP)
    texto = texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    texto = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", texto)
    return texto


MARGEN_LATERAL_PDF = 1.5 * cm
ANCHO_UTIL_PDF_PT = LETTER[0] - 2 * MARGEN_LATERAL_PDF  # ancho de página menos márgenes izq/der


def _dibujar_encabezado_pie_pdf(canvas_obj, doc_obj, asignatura, ruta_logo):
    """
    Dibuja encabezado (logo UDLA + nombre de la asignatura) y pie de página (Departamento,
    fecha de generación y N° de página) en CADA página del PDF. Se usa como callback de
    SimpleDocTemplate (onFirstPage/onLaterPages), no como parte del "story" — por eso no
    compite por espacio con el contenido, solo con los márgenes reservados en generar_pdf_desde_texto.
    """
    ancho_pagina, alto_pagina = LETTER
    canvas_obj.saveState()

    # --- Encabezado ---
    if ruta_logo and os.path.exists(ruta_logo) and not ruta_logo.lower().endswith(".svg"):
        try:
            lector_img = ImageReader(ruta_logo)
            ancho_img, alto_img = lector_img.getSize()
            alto_dibujo = 0.9 * cm
            ancho_dibujo = alto_dibujo * (ancho_img / alto_img)
            canvas_obj.drawImage(
                lector_img, MARGEN_LATERAL_PDF, alto_pagina - 1.65 * cm,
                width=ancho_dibujo, height=alto_dibujo,
                preserveAspectRatio=True, mask="auto",
            )
        except Exception:
            pass

    y_texto_encabezado = alto_pagina - 1.15 * cm
    canvas_obj.setFont("Helvetica-Bold", 9)
    canvas_obj.setFillColor(colors.HexColor("#EA580C"))
    canvas_obj.drawRightString(ancho_pagina - MARGEN_LATERAL_PDF, y_texto_encabezado, "CogniMorph — Tutor IA DMYF UDLA")
    if asignatura:
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.setFillColor(colors.HexColor("#555555"))
        canvas_obj.drawRightString(ancho_pagina - MARGEN_LATERAL_PDF, y_texto_encabezado - 11, asignatura)

    canvas_obj.setStrokeColor(colors.HexColor("#EA580C"))
    canvas_obj.setLineWidth(1)
    canvas_obj.line(MARGEN_LATERAL_PDF, alto_pagina - 1.8 * cm, ancho_pagina - MARGEN_LATERAL_PDF, alto_pagina - 1.8 * cm)

    # --- Pie de página ---
    canvas_obj.setFont("Helvetica", 7.5)
    canvas_obj.setFillColor(colors.HexColor("#777777"))
    canvas_obj.drawString(MARGEN_LATERAL_PDF, 1.15 * cm, "Departamento de Morfología y Función — Facultad de Salud y Ciencias Sociales, UDLA")
    canvas_obj.drawString(
        MARGEN_LATERAL_PDF, 1.15 * cm - 10,
        f"Generado por CogniMorph el {datetime.now().strftime('%d-%m-%Y %H:%M')}",
    )
    canvas_obj.drawRightString(ancho_pagina - MARGEN_LATERAL_PDF, 1.15 * cm, f"Página {canvas_obj.getPageNumber()}")
    canvas_obj.setStrokeColor(colors.HexColor("#DDDDDD"))
    canvas_obj.line(MARGEN_LATERAL_PDF, 1.4 * cm, ancho_pagina - MARGEN_LATERAL_PDF, 1.4 * cm)

    canvas_obj.restoreState()


def _agregar_texto_al_story(texto, styles, story):
    """Convierte un bloque de texto (markdown simple) en flowables de ReportLab, en orden."""
    lineas = texto.split("\n")
    i = 0
    while i < len(lineas):
        linea = lineas[i].rstrip()
        if linea.strip().startswith("|"):
            filas_tabla = []
            while i < len(lineas) and lineas[i].strip().startswith("|"):
                fila = [c.strip() for c in lineas[i].strip().strip("|").split("|")]
                if not re.fullmatch(r"[-: ]+", "".join(fila)):
                    filas_tabla.append([_md_inline_a_reportlab(c) for c in fila])
                i += 1
            if filas_tabla:
                filas_tabla = [[Paragraph(c, styles["Normal"]) for c in fila] for fila in filas_tabla]
                tabla = Table(filas_tabla, hAlign="LEFT")
                tabla.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EA580C")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]))
                story.append(tabla)
                story.append(Spacer(1, 10))
            continue
        if linea.startswith("### "):
            story.append(Paragraph(_md_inline_a_reportlab(linea[4:]), styles["Heading3"]))
        elif linea.startswith("## "):
            story.append(Paragraph(_md_inline_a_reportlab(linea[3:]), styles["Heading2"]))
        elif linea.startswith("# "):
            story.append(Paragraph(_md_inline_a_reportlab(linea[2:]), styles["Heading1"]))
        elif linea.strip().startswith(("- ", "* ")):
            story.append(Paragraph("• " + _md_inline_a_reportlab(linea.strip()[2:]), styles["Normal"]))
        elif linea.strip():
            story.append(Paragraph(_md_inline_a_reportlab(linea.strip()), styles["Normal"]))
        else:
            story.append(Spacer(1, 6))
        i += 1


def _agregar_diagrama_al_story(codigo_mermaid, styles, story):
    """Intenta incrustar el diagrama como imagen; si el servicio de renderizado no responde,
    deja una nota breve en su lugar (nunca revienta la generación del PDF)."""
    png_bytes = obtener_png_mermaid_cacheado(codigo_mermaid)
    if png_bytes:
        try:
            img = PILImage.open(BytesIO(png_bytes))
            ancho_px, alto_px = img.size
            ancho_pdf = min(ANCHO_UTIL_PDF_PT, ancho_px)
            alto_pdf = ancho_pdf * (alto_px / ancho_px)
            story.append(RLImage(BytesIO(png_bytes), width=ancho_pdf, height=alto_pdf))
            story.append(Spacer(1, 10))
            return
        except Exception:
            pass
    story.append(Paragraph(
        "<i>(No se pudo generar la imagen del diagrama para este PDF; revísalo en el chat.)</i>",
        styles["Normal"],
    ))
    story.append(Spacer(1, 8))


def _agregar_material_al_story(material_id, materiales_disponibles, styles, story):
    """El PDF no puede reproducir audio/video: deja una nota con el título del material ofrecido,
    para que quede registrado que existe y el estudiante sepa que debe ir a buscarlo al chat."""
    entrada = buscar_material_por_id(materiales_disponibles, material_id)
    titulo_material = entrada.get("titulo") if entrada else material_id
    story.append(Paragraph(
        f"<i>(Material de apoyo mencionado: {_md_inline_a_reportlab(titulo_material)} — disponible "
        "para escuchar/ver en el chat, no incluido en este PDF.)</i>",
        styles["Normal"],
    ))
    story.append(Spacer(1, 8))


def _agregar_quiz_al_story(contenido_json, styles, story):
    """
    El PDF no puede ofrecer alternativas clickeables ni feedback interactivo: en su lugar deja cada
    pregunta con sus alternativas y marca la correcta (✔), para que sirva como material de estudio
    ya resuelto (igual que el resto del PDF, que entrega la respuesta completa, no un formulario en
    blanco — ver ENTREGA COMPLETA en el prompt). Si el JSON viene mal formado, deja una nota breve
    en vez de romper el resto del documento.
    """
    try:
        datos = json.loads(contenido_json)
        preguntas = datos["preguntas"]
    except Exception:
        story.append(Paragraph("<i>(No se pudo incluir este mini control en el PDF.)</i>", styles["Normal"]))
        story.append(Spacer(1, 8))
        return

    for i, pregunta in enumerate(preguntas):
        try:
            texto_pregunta = pregunta["pregunta"]
            alternativas = pregunta["alternativas"]
            idx_correcta = pregunta["correcta"]
        except Exception:
            continue
        story.append(Paragraph(f"<b>{i + 1}. {_md_inline_a_reportlab(texto_pregunta)}</b>", styles["Normal"]))
        for j, alt in enumerate(alternativas):
            marca = "✔ " if j == idx_correcta else "&nbsp;&nbsp;&nbsp;"
            story.append(Paragraph(f"{marca}{_md_inline_a_reportlab(alt)}", styles["Normal"]))
        story.append(Spacer(1, 8))


def _agregar_pareados_al_story(contenido_json, styles, story):
    """Igual que _agregar_quiz_al_story: el PDF muestra los pares ya resueltos (correcta), no un
    ejercicio en blanco — para que sirva como resumen de estudio."""
    try:
        datos = json.loads(contenido_json)
        terminos = datos["terminos"]
    except Exception:
        story.append(Paragraph("<i>(No se pudo incluir este mini control en el PDF.)</i>", styles["Normal"]))
        story.append(Spacer(1, 8))
        return

    filas = [[Paragraph("<b>Término</b>", styles["Normal"]), Paragraph("<b>Definición</b>", styles["Normal"])]]
    for t in terminos:
        if t.get("termino") and t.get("definicion"):
            filas.append([
                Paragraph(_md_inline_a_reportlab(t["termino"]), styles["Normal"]),
                Paragraph(_md_inline_a_reportlab(t["definicion"]), styles["Normal"]),
            ])
    if len(filas) > 1:
        tabla = Table(filas, hAlign="LEFT", colWidths=[ANCHO_UTIL_PDF_PT * 0.3, ANCHO_UTIL_PDF_PT * 0.7])
        tabla.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EA580C")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tabla)
        story.append(Spacer(1, 10))


def generar_pdf_desde_texto(texto_completo, titulo="Respuesta de CogniMorph", asignatura=None, ruta_logo=None, materiales_disponibles=None):
    """
    Convierte la respuesta (markdown simple + diagramas Mermaid) en un PDF descargable,
    respetando el orden en que aparecen texto y diagramas en la respuesta original.
    El contenido en sí sigue yendo directo al grano (sin saludos ni relleno conversacional);
    lo único institucional es el encabezado/pie que se dibuja en cada página vía
    _dibujar_encabezado_pie_pdf (logo, asignatura, depto., fecha y N° de página).
    """
    try:
        partes = extraer_bloques(texto_completo)

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=LETTER,
            topMargin=2.2 * cm, bottomMargin=2.0 * cm,
            leftMargin=MARGEN_LATERAL_PDF, rightMargin=MARGEN_LATERAL_PDF,
        )
        styles = getSampleStyleSheet()
        story = [Paragraph(titulo, styles["Heading2"]), Spacer(1, 10)]

        for tipo, contenido in partes:
            if tipo == "mermaid":
                _agregar_diagrama_al_story(contenido, styles, story)
            elif tipo == "material":
                _agregar_material_al_story(contenido, materiales_disponibles, styles, story)
            elif tipo == "quiz":
                _agregar_quiz_al_story(contenido, styles, story)
            elif tipo == "pareados":
                _agregar_pareados_al_story(contenido, styles, story)
            else:
                _agregar_texto_al_story(contenido, styles, story)

        def _encabezado_pie(canvas_obj, doc_obj):
            _dibujar_encabezado_pie_pdf(canvas_obj, doc_obj, asignatura, ruta_logo)

        doc.build(story, onFirstPage=_encabezado_pie, onLaterPages=_encabezado_pie)
        return buffer.getvalue()
    except Exception:
        return None


def mostrar_respuesta_tutor(texto, key_sufijo, permitir_descarga=True, asignatura_sel="", ruta_logo=None, materiales_disponibles=None, es_respuesta_nueva=False):
    """
    Muestra la respuesta del tutor (texto + diagramas Mermaid + material de apoyo si los hay) y,
    opcionalmente:
    - un botón por cada esquema/diagrama para descargarlo suelto como imagen PNG (para que el
      estudiante arme su propio documento con eso), y
    - un botón para descargar la respuesta COMPLETA (texto, tablas y esquemas incrustados) como PDF.
    Las tablas solo existen dentro del PDF completo (no tiene sentido una tabla "suelta" como imagen);
    el audio/video de apoyo aparece en el reproductor flotante, no incrustado aquí (ver
    render_material). es_respuesta_nueva=True solo para la respuesta que se ACABA de generar (no al
    redibujar el historial) — es lo que permite reabrir un material que el estudiante había cerrado
    antes, si lo vuelve a pedir explícitamente.
    """
    for idx_bloque, (tipo, contenido) in enumerate(extraer_bloques(texto)):
        if tipo == "mermaid":
            try:
                render_mermaid(contenido)
            except Exception:
                st.warning("No se pudo renderizar el diagrama; se muestra el código:")
                st.code(contenido, language="text")

            if permitir_descarga:
                png_bytes = obtener_png_mermaid_cacheado(contenido)
                if png_bytes:
                    st.download_button(
                        "🖼️ Descargar este esquema como imagen (PNG)",
                        data=png_bytes,
                        file_name="esquema_cognimorph.png",
                        mime="image/png",
                        key=f"png_{key_sufijo}_{idx_bloque}",
                    )
        elif tipo == "material":
            render_material(contenido, materiales_disponibles, es_nuevo=es_respuesta_nueva)
        elif tipo == "quiz":
            render_quiz(contenido, idx_bloque)
        elif tipo == "pareados":
            render_pareados(contenido, idx_bloque)
        else:
            st.markdown(contenido)

    if permitir_descarga:
        pdf_bytes = generar_pdf_desde_texto(
            texto, asignatura=asignatura_sel, ruta_logo=ruta_logo, materiales_disponibles=materiales_disponibles,
        )
        if pdf_bytes:
            st.download_button(
                "📄 Descargar esta respuesta como PDF",
                data=pdf_bytes,
                file_name="respuesta_cognimorph.pdf",
                mime="application/pdf",
                key=f"pdf_{key_sufijo}",
            )

# --- SALUDO INICIAL (neutral, orienta sobre todo lo que el tutor puede hacer) ---
def generar_saludo(asignatura_sel, alumno_activo):
    saludo = (
        f"¡Hola **{alumno_activo['nombre']}**! Soy **CogniMorph**, tu tutor inteligente de {asignatura_sel}.\n\n"
        "Puedo ayudarte con cosas como:\n"
        "- 📅 Fechas de evaluaciones y qué contenidos entran en cada una, según el cronograma oficial.\n"
        "- 📖 Repasar cualquier tema del curso contigo, a tu ritmo.\n"
        "- 📝 Armarte preguntas de práctica o quizzes formativos para autoevaluarte.\n"
        "- 🩺 Trabajar casos clínicos aplicados a lo que estás viendo.\n\n"
    )
    if alumno_activo["riesgo"] == "ALTO":
        saludo += (
            f"Además, vi que te podría servir reforzar **{alumno_activo['tema_debil']}** — "
            "¿partimos por ahí o prefieres otra cosa?"
        )
    else:
        saludo += "¿Por dónde quieres partir hoy?"
    return saludo


# --- ESTADO DE SESIÓN ---
if "tracker" not in st.session_state:
    st.session_state.tracker = StudentTracker()

if "contexto_pdf_manual" not in st.session_state:
    st.session_state.contexto_pdf_manual = ""

# ids de materiales de apoyo actualmente abiertos en el reproductor flotante, y los que el
# estudiante cerró explícitamente (para no reabrirlos solos al redibujar el historial del chat).
if "materiales_flotantes" not in st.session_state:
    st.session_state.materiales_flotantes = []
if "materiales_cerrados" not in st.session_state:
    st.session_state.materiales_cerrados = set()

# --- BARRA LATERAL (SIDEBAR INSTITUCIONAL) ---
with st.sidebar:
    st.markdown('<div class="sidebar-card-title">⚙️ Configuración</div>', unsafe_allow_html=True)
    api_key = st.text_input("Gemini API Key:", type="password", help="Obtenla en aistudio.google.com")
    st.markdown("<br>", unsafe_allow_html=True)

    st.markdown('<div class="sidebar-card-title">📚 Asignatura DMYF</div>', unsafe_allow_html=True)
    asignatura_sel = st.selectbox(
        "Selecciona el curso:",
        [
            "MYF205 - Anatomía de Cabeza y Cuello",
            "MYF2069 - Fisiología General"
        ]
    )
    cod_materia = "MYF205" if "MYF205" in asignatura_sel else "MYF2069"
    materiales_disponibles, aviso_materiales = cargar_materiales(cod_materia)
    materiales_listos = [m for m in materiales_disponibles if m.get("existe")]
    if materiales_disponibles:
        st.caption(f"🎧 Materiales de apoyo listos: {len(materiales_listos)} de {len(materiales_disponibles)} en el índice")
    if aviso_materiales:
        st.warning(f"⚠️ Materiales de apoyo: {aviso_materiales}")

    # Panel de depuración: muestra EXACTAMENTE lo que la app leyó de materiales.json para esta
    # asignatura (id, tema, si el archivo se encontró). Existe porque "el tutor no lo reconoce"
    # puede deberse a varias causas distintas (json no actualizado, tema vacío, archivo no
    # encontrado, asignatura equivocada) y sin esto había que adivinar cuál — ahora se ve directo.
    with st.expander(f"🔍 Ver materiales cargados para {cod_materia} (debug)"):
        if not materiales_disponibles:
            st.caption(
                "No hay ninguna entrada cargada desde materiales.json para esta asignatura. "
                "Si ya corriste el script generador, confirma que guardó el archivo en "
                f"data/{cod_materia}/materiales.json (no en otra carpeta) y que tiene contenido."
            )
        else:
            for m in materiales_disponibles:
                estado = "✅ archivo encontrado" if m.get("existe") else "❌ ARCHIVO NO ENCONTRADO"
                tema_mostrado = m.get("tema") or "⚠️ (vacío — el tutor no podrá asociarlo a ningún tema)"
                st.markdown(
                    f"- **id:** `{m.get('id', '(sin id)')}` — **tema:** {tema_mostrado}\n"
                    f"  **archivo:** `{m.get('archivo', '(sin archivo)')}` — {estado}"
                )
    st.markdown("<br>", unsafe_allow_html=True)

    # SIMULADOR DE ESTUDIANTE EN BLACKBOARD
    st.markdown('<div class="sidebar-card-title">👤 Estudiante (Blackboard SSO)</div>', unsafe_allow_html=True)
    alumnos_lista = cargar_estudiantes(cod_materia)
    opciones_alumnos = {f"{a['nombre']} ({a['carrera']} - Riesgo {a['riesgo']})": a for a in alumnos_lista}
    opcion_elegida = st.selectbox("Simular ingreso de estudiante:", list(opciones_alumnos.keys()))
    alumno_activo = opciones_alumnos[opcion_elegida]
    st.caption(f"🆔 ID: {alumno_activo['id']} | Promedio previo: **{alumno_activo['promedio_previo']}**")
    st.markdown("<br>", unsafe_allow_html=True)

    st.markdown('<div class="sidebar-card-title">📄 Subir Guía Extra</div>', unsafe_allow_html=True)
    pdf = st.file_uploader("Cargar documento adicional:", type=["pdf"])
    if pdf:
        reader = PdfReader(pdf)
        st.session_state.contexto_pdf_manual = "".join([page.extract_text() or "" for page in reader.pages])
        st.success("✅ Guía extra cargada")
    st.markdown("<br>", unsafe_allow_html=True)

    st.markdown('<div class="sidebar-card-title">📊 Registro de Estudio</div>', unsafe_allow_html=True)
    col_met1, col_met2 = st.columns(2)
    col_met1.metric("Consultas", st.session_state.tracker.data.get("sesiones_chat", 0))
    col_met2.metric("Quizzes", st.session_state.tracker.data.get("quizzes_intentos", 0))

# --- REPRODUCTOR FLOTANTE DE MATERIALES (audio/video que el estudiante pidió escuchar/ver) ---
# Va ACÁ, ANTES de procesar una consulta nueva (no al final del script): así, mientras el tutor
# está pensando la respuesta a una NUEVA pregunta (el spinner de más abajo puede tardar varios
# segundos), el reproductor de un material que ya estaba sonando de un turno anterior queda
# dibujado desde el principio de este rerun y no desaparece durante esa espera. Para que un
# material RECIÉN ofrecido en la respuesta que se está generando en este mismo rerun también
# aparezca de inmediato (sin esperar al próximo mensaje), más abajo se fuerza un st.rerun() apenas
# se detecta uno nuevo — ver el bloque "if texto_a_enviar".
mostrar_reproductor_flotante(materiales_disponibles)

# --- ENCABEZADO PRINCIPAL (HEADER UDLA) ---
st.markdown(f"""
<div class="udla-header">
    <h1>Universidad de Las Américas</h1>
    <div class="udla-logo-box">
        {udla_img_html}
    </div>
</div>
""", unsafe_allow_html=True)

# Logo Central CogniMorph
col_izq, col_centro, col_der = st.columns([1, 2.5, 1])
with col_centro:
    if ruta_cognimorph:
        st.image(ruta_cognimorph, width=350)
    else:
        st.markdown("<h1 style='text-align:center; color:#EA580C;'>🧠 CogniMorph</h1>", unsafe_allow_html=True)

# Barra de Asignatura Activa
st.markdown(f"""
<div class="active-subject-badge">
    📍 <b>Asignatura activa:</b> {asignatura_sel} • <b>Estudiante:</b> {alumno_activo['nombre']} ({alumno_activo['carrera']})
</div>
""", unsafe_allow_html=True)

# ALERTA TEMPRANA ACTIVA EN PANTALLA
if alumno_activo["riesgo"] == "ALTO":
    st.markdown(f"""
    <div class="alert-card-riesgo">
        🚨 <b>Plan de Reforzamiento Activo (Alerta Temprana):</b><br>
        Hola {alumno_activo['nombre']}, detectamos que necesitas reforzar: <b>{alumno_activo['tema_debil']}</b>. 
        ¡Pregúntale a CogniMorph para repasar antes de la próxima evaluación!
    </div>
    """, unsafe_allow_html=True)

prompt_sugerido = None
# control_pendiente reemplaza el viejo enfoque de "pedirle el control al tutor conversacional":
# cuando se llena, más abajo se llama directo a TutorCore.generar_control() (salida JSON forzada
# por schema) en vez de esperar a que el modelo decida solo escribir los bloques quiz/pareados —
# ver la nota junto a ControlGeneradoSchema en tutor_core.py sobre por qué se cambió de enfoque.
control_pendiente = None

# --- BANNER LLAMATIVO: INVITACIÓN AL "CONTROL COMPLETO" (8 selección múltiple + 8 pareados) ---
st.markdown(
    '<div class="cognimorph-banner-control">'
    '<div class="titulo-banner">🎯 ¿Te sientes list@ para ponerte a prueba?</div>'
    '<div class="texto-banner">Cuando te sientas list@, cuéntame de qué clase o tema quieres tu '
    "<b>control</b> (o déjalo en blanco para que sea de todo lo visto hasta ahora) y te armo 8 "
    "preguntas de selección múltiple + 8 términos pareados, con el resultado al instante.</div></div>",
    unsafe_allow_html=True,
)
contenedor_boton_control = st.container(key="cognimorph_btn_control_completo")
with contenedor_boton_control:
    tema_control_input = st.text_input(
        "¿De qué clase o tema quieres el control?",
        key="cognimorph_tema_control_input",
        placeholder="Ej: fisiología renal, o toda la unidad 2… (déjalo en blanco = todo lo visto)",
        label_visibility="collapsed",
    )
    if st.button("🚀 ¡Quiero hacer un control!", key="cognimorph_btn_control_completo_click", use_container_width=True):
        control_pendiente = {"tema": tema_control_input, "n_preguntas": 8, "n_pareados": 8}

# --- BOTONES DE ACCIÓN RÁPIDA (SUGERENCIAS Y CRONOGRAMA) ---
st.markdown("💡 **Acciones y consultas rápidas:**")
col1, col2, col3 = st.columns(3)

if col1.button("📅 ¿Cuándo son las próximas evaluaciones?"):
    prompt_sugerido = f"¿Cuáles son las fechas de las evaluaciones y solemnes programadas para {asignatura_sel}?"

if cod_materia == "MYF205":
    if col2.button("🧠 Repasar Forámenes y Pares Craneales"):
        prompt_sugerido = "Explícame de forma socrática los forámenes de la base del cráneo y qué nervios pasan por ellos."
    if col3.button("📝 Quiz Rápido de Anatomía (3 preguntas)"):
        control_pendiente = {"tema": "Anatomía de Cabeza y Cuello", "n_preguntas": 3, "n_pareados": 0}
else:
    if col2.button("⚡ Repasar Potencial de Acción"):
        prompt_sugerido = "Explícame socráticamente las fases del potencial de acción y la apertura de canales de Na+ y K+."
    if col3.button("📝 Quiz Rápido de Fisiología (3 preguntas)"):
        control_pendiente = {"tema": "Fisiología de Membrana y Homeostasis", "n_preguntas": 3, "n_pareados": 0}

# Historial de Chat
# Se reinicia el saludo/chat cada vez que cambia la asignatura o el estudiante activo
# (antes solo se generaba una vez por sesión de navegador, por lo que el saludo de
# MYF205 se quedaba pegado aunque el usuario cambiara a MYF2069 en la barra lateral).
contexto_chat_actual = (cod_materia, alumno_activo["id"])
if st.session_state.get("contexto_chat_activo") != contexto_chat_actual:
    st.session_state.contexto_chat_activo = contexto_chat_actual
    st.session_state.mensajes = [
        {"role": "assistant", "content": generar_saludo(asignatura_sel, alumno_activo)}
    ]

for idx, m in enumerate(st.session_state.mensajes):
    avatar_actual = ruta_avatar if m["role"] == "assistant" else "👤"
    with st.chat_message(m["role"], avatar=avatar_actual):
        if m["role"] == "assistant":
            # El saludo inicial (idx 0) no necesita botón de descarga.
            mostrar_respuesta_tutor(
                m["content"], key_sufijo=f"hist_{idx}", permitir_descarga=(idx > 0),
                asignatura_sel=asignatura_sel, ruta_logo=ruta_udla, materiales_disponibles=materiales_disponibles,
            )
        else:
            st.markdown(m["content"])

# Entrada de consulta
# OJO: st.chat_input() se llama SIEMPRE, sin importar si prompt_sugerido ya trae algo — si esto
# fuera "prompt_sugerido or st.chat_input(...)", Python ni siquiera ejecutaría st.chat_input()
# cuando prompt_sugerido es verdadero (cortocircuito del "or"), y como ese widget solo se dibuja
# cuando el código lo llama, la barra de chat entera desaparecía justo al usar un botón de acceso
# rápido (que es lo que llena prompt_sugerido) — quedando el estudiante sin forma de seguir
# escribiendo. Por eso primero se guarda el resultado de chat_input en su propia variable, y
# recién ahí se decide la prioridad.
texto_chat_input = st.chat_input("Escribe tu duda, pide un quiz o consulta el cronograma...")
texto_a_enviar = prompt_sugerido or texto_chat_input

# Si el mensaje pregunta por el CONTENIDO de una evaluación puntual (ej. "¿qué contenidos entran en
# el CAT 1?"), la respuesta se busca directo en contenidos_evaluaciones.json en vez de pedírsela al
# tutor conversacional — ver la nota junto a _PATRON_PREGUNTA_CONTENIDO_EVALUACION más arriba sobre
# por qué (el modelo demostró "comerse" ítems de la lista al copiarla de memoria). Si no se encuentra
# ninguna evaluación mencionada, no se hace nada acá y el mensaje sigue su camino normal más abajo
# (el tutor conversacional igual tiene el mismo documento en su contexto, por si esta detección no
# capturó la forma exacta en que el estudiante lo preguntó).
if control_pendiente is None and texto_a_enviar and _PATRON_PREGUNTA_CONTENIDO_EVALUACION.search(texto_a_enviar):
    entrada_eval = _buscar_evaluacion_contenidos(cargar_contenidos_evaluaciones(cod_materia), texto_a_enviar)
    if entrada_eval is not None:
        ponderacion_eval = entrada_eval.get("ponderacion")
        respuesta_lookup = (
            "Según el documento oficial de contenidos por evaluación, los temas que entran en "
            f"**{entrada_eval['evaluacion']}**"
            + (f" (ponderación {ponderacion_eval})" if ponderacion_eval else "")
            + " son:\n\n"
            + "\n".join(f"- {c}" for c in entrada_eval.get("contenidos", []))
            + "\n\n¿Quieres que repasemos alguno de estos temas, o armamos un mini control de esto para practicar?"
        )
        st.session_state.mensajes.append({"role": "user", "content": texto_a_enviar})
        with st.chat_message("user", avatar="👤"):
            st.markdown(texto_a_enviar)
        with st.chat_message("assistant", avatar=ruta_avatar):
            mostrar_respuesta_tutor(
                respuesta_lookup, key_sufijo=f"nueva_{len(st.session_state.mensajes)}",
                asignatura_sel=asignatura_sel, ruta_logo=ruta_udla, materiales_disponibles=materiales_disponibles,
                es_respuesta_nueva=True,
            )
        st.session_state.mensajes.append({"role": "assistant", "content": respuesta_lookup})
        st.session_state.tracker.registrar_consulta()
        texto_a_enviar = None

# Si el estudiante escribió el pedido de control directo en el chat (en vez de usar el banner o los
# botones de arriba), lo detectamos ACÁ y lo convertimos en control_pendiente también, para que se
# procese por el mismo camino determinístico de más abajo en vez del tutor conversacional.
if (
    control_pendiente is None
    and texto_a_enviar
    and _PATRON_SOLICITUD_CONTROL.search(texto_a_enviar)
    and not _PATRON_PREGUNTA_FECHA_CONTROL.search(texto_a_enviar)
):
    control_pendiente = {
        "tema": _extraer_tema_control(texto_a_enviar),
        "n_preguntas": 8,
        "n_pareados": 8,
    }
    texto_a_enviar = None

if control_pendiente is not None:
    tema_control = (control_pendiente.get("tema") or "").strip()
    texto_usuario_mostrado = (
        f"Quiero hacer un control de: {tema_control}"
        if tema_control
        else "Quiero hacer un control de todo lo visto hasta ahora."
    )
    historial_previo = list(st.session_state.mensajes)  # turnos ANTES de esta pregunta
    st.session_state.mensajes.append({"role": "user", "content": texto_usuario_mostrado})
    with st.chat_message("user", avatar="👤"):
        st.markdown(texto_usuario_mostrado)

    with st.chat_message("assistant", avatar=ruta_avatar):
        with st.spinner("CogniMorph armando tu control..."):
            tutor = TutorCore(api_key=api_key)
            contexto_material = cargar_textos_pdfs_materia(cod_materia)
            if st.session_state.contexto_pdf_manual:
                contexto_material += "\n" + st.session_state.contexto_pdf_manual

            datos_control, error_control = tutor.generar_control(
                tema=control_pendiente.get("tema"),
                n_preguntas=control_pendiente.get("n_preguntas", 8),
                n_pareados=control_pendiente.get("n_pareados", 8),
                historial_chat=historial_previo,
                contexto_material=contexto_material,
                cronograma=cargar_cronograma(cod_materia),
                contenidos_evaluaciones=cargar_contenidos_evaluaciones(cod_materia),
            )
            if error_control:
                respuesta = error_control
            else:
                respuesta = _formatear_control_como_bloques(datos_control, tema=control_pendiente.get("tema"))

            mostrar_respuesta_tutor(
                respuesta, key_sufijo=f"nueva_{len(st.session_state.mensajes)}",
                asignatura_sel=asignatura_sel, ruta_logo=ruta_udla, materiales_disponibles=materiales_disponibles,
                es_respuesta_nueva=True,
            )
            st.session_state.mensajes.append({"role": "assistant", "content": respuesta})
            st.session_state.tracker.registrar_consulta()
            if not error_control:
                st.session_state.tracker.registrar_quiz(nota="Completado")

elif texto_a_enviar:
    historial_previo = list(st.session_state.mensajes)  # turnos ANTES de esta pregunta
    st.session_state.mensajes.append({"role": "user", "content": texto_a_enviar})
    with st.chat_message("user", avatar="👤"):
        st.markdown(texto_a_enviar)

    with st.chat_message("assistant", avatar=ruta_avatar):
        with st.spinner("CogniMorph procesando análisis pedagógico..."):
            tutor = TutorCore(api_key=api_key)
            contexto_material = cargar_textos_pdfs_materia(cod_materia)
            if st.session_state.contexto_pdf_manual:
                contexto_material += "\n" + st.session_state.contexto_pdf_manual

            # ids abiertos ANTES de procesar esta respuesta, para poder detectar si esta respuesta
            # abre algún material NUEVO (ver el st.rerun() más abajo).
            materiales_flotantes_antes = set(st.session_state.materiales_flotantes)

            respuesta = tutor.responder(
                historial_chat=historial_previo,
                pregunta_usuario=texto_a_enviar,
                contexto_material=contexto_material,
                cronograma=cargar_cronograma(cod_materia),
                estudiante_info=alumno_activo,
                materiales_disponibles=materiales_disponibles,
                contenidos_evaluaciones=cargar_contenidos_evaluaciones(cod_materia),
            )
            mostrar_respuesta_tutor(
                respuesta, key_sufijo=f"nueva_{len(st.session_state.mensajes)}",
                asignatura_sel=asignatura_sel, ruta_logo=ruta_udla, materiales_disponibles=materiales_disponibles,
                es_respuesta_nueva=True,
            )
            st.session_state.mensajes.append({"role": "assistant", "content": respuesta})
            st.session_state.tracker.registrar_consulta()
            if "quiz" in texto_a_enviar.lower():
                st.session_state.tracker.registrar_quiz(nota="Completado")

    # El reproductor flotante se dibuja ARRIBA, antes de esta sección (ver comentario junto a su
    # llamada, después de la barra lateral) — así no desaparece mientras el spinner de arriba está
    # pensando. Pero eso significa que, en ESTE MISMO rerun, ya pasó de largo antes de que
    # existiera el material que la respuesta recién ofreció. Si esta respuesta abrió un material
    # que no estaba abierto antes, forzamos un rerun inmediato (no vuelve a llamar al modelo, solo
    # redibuja la app con el estado ya actualizado) para que el reproductor lo muestre ya, sin
    # esperar a que el estudiante mande otro mensaje.
    if set(st.session_state.materiales_flotantes) - materiales_flotantes_antes:
        st.rerun()

# --- PIE DE PÁGINA INSTITUCIONAL ---
st.markdown("""
<div class="udla-footer">
    <span>Departamento de Morfología y Función</span>
    <span>Facultad de Salud y Ciencias Sociales</span>
</div>
""", unsafe_allow_html=True)