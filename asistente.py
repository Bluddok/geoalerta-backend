from __future__ import annotations

from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from typing import Literal, Optional
from zoneinfo import ZoneInfo
import json
import os
import unicodedata

import psycopg2
from fastapi import APIRouter, HTTPException
from google import genai
from pydantic import BaseModel, Field


router = APIRouter(
    prefix="/asistente",
    tags=["Asistente GeoAlerta"],
)

RADIO_METROS = 500
ZONA_HORARIA = ZoneInfo("America/Guayaquil")


class MensajeConversacion(BaseModel):
    rol: Literal["usuario", "asistente"]

    contenido: str = Field(
        min_length=1,
        max_length=1200,
    )


class ConsultaAsistente(BaseModel):
    pregunta: str = Field(
        min_length=3,
        max_length=500,
    )

    historial: list[MensajeConversacion] = Field(
        default_factory=list,
        max_length=8,
    )


class RespuestaAsistente(BaseModel):
    titulo: str
    respuesta: str

    mostrar_en_mapa: bool = False

    latitud: Optional[float] = None
    longitud: Optional[float] = None

    zoom: float = 15.5

    tipo_reporte: Optional[str] = None
    cantidad_zona: Optional[int] = None

    radio_metros: int = RADIO_METROS
    total_reportes: int = 0

    generado_por: str


def _conexion():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        sslmode=os.getenv(
            "DB_SSLMODE",
            "require",
        ),
        connect_timeout=15,
    )


def _normalizar(texto: str) -> str:
    base = unicodedata.normalize(
        "NFD",
        texto.lower().strip(),
    )

    return "".join(
        caracter
        for caracter in base
        if unicodedata.category(caracter) != "Mn"
    )


def _distancia_metros(
    punto_a: dict,
    punto_b: dict,
) -> float:
    radio_tierra = 6371000.0

    latitud_a = radians(
        punto_a["latitud"],
    )

    latitud_b = radians(
        punto_b["latitud"],
    )

    diferencia_latitud = radians(
        punto_b["latitud"]
        - punto_a["latitud"],
    )

    diferencia_longitud = radians(
        punto_b["longitud"]
        - punto_a["longitud"],
    )

    valor = (
        sin(diferencia_latitud / 2) ** 2
        + cos(latitud_a)
        * cos(latitud_b)
        * sin(diferencia_longitud / 2) ** 2
    )

    return (
        2
        * radio_tierra
        * asin(sqrt(valor))
    )


def _cargar_reportes() -> list[dict]:
    conexion = _conexion()
    cursor = conexion.cursor()

    try:
        cursor.execute(
            """
            SELECT
                tipo_reporte,
                fecha_hora,
                latitud,
                longitud
            FROM public.reportes_emergencia
            WHERE
                latitud IS NOT NULL
                AND longitud IS NOT NULL
            ORDER BY fecha_hora DESC;
            """
        )

        return [
            {
                "tipo_reporte": str(fila[0]),
                "fecha_hora": fila[1],
                "latitud": float(fila[2]),
                "longitud": float(fila[3]),
            }
            for fila in cursor.fetchall()
        ]

    finally:
        cursor.close()
        conexion.close()


def _concentracion(
    reportes: list[dict],
    tipo_reporte: Optional[str] = None,
) -> Optional[dict]:
    candidatos = [
        reporte
        for reporte in reportes
        if tipo_reporte is None
        or reporte["tipo_reporte"]
        == tipo_reporte
    ]

    if not candidatos:
        return None

    mejor_concentracion = None

    for origen in candidatos:
        cantidad = sum(
            1
            for vecino in candidatos
            if _distancia_metros(
                origen,
                vecino,
            )
            <= RADIO_METROS
        )

        if (
            mejor_concentracion is None
            or cantidad
            > mejor_concentracion["cantidad"]
        ):
            mejor_concentracion = {
                "latitud": origen["latitud"],
                "longitud": origen["longitud"],
                "cantidad": cantidad,
            }

    return mejor_concentracion


def _construir_contexto(
    reportes: list[dict],
) -> dict:
    ahora = datetime.now(
        ZONA_HORARIA,
    )

    conteos_por_tipo: dict[str, int] = {}
    conteos_por_hora: dict[int, int] = {}

    reportes_hoy = 0
    reportes_ultima_semana = 0

    for reporte in reportes:
        tipo = reporte["tipo_reporte"]

        conteos_por_tipo[tipo] = (
            conteos_por_tipo.get(
                tipo,
                0,
            )
            + 1
        )

        fecha = reporte["fecha_hora"]

        if fecha.tzinfo is None:
            fecha = fecha.replace(
                tzinfo=ZONA_HORARIA,
            )

        fecha_local = fecha.astimezone(
            ZONA_HORARIA,
        )

        if fecha_local.date() == ahora.date():
            reportes_hoy += 1

        diferencia = ahora - fecha_local

        if diferencia.days < 7:
            reportes_ultima_semana += 1

        conteos_por_hora[
            fecha_local.hour
        ] = (
            conteos_por_hora.get(
                fecha_local.hour,
                0,
            )
            + 1
        )

    hora_mayor_frecuencia = None

    if conteos_por_hora:
        hora, cantidad = max(
            conteos_por_hora.items(),
            key=lambda elemento: elemento[1],
        )

        hora_mayor_frecuencia = {
            "hora": hora,
            "cantidad": cantidad,
        }

    reportes_recientes = [
        {
            "tipo_reporte":
                reporte["tipo_reporte"],

            "fecha_hora":
                reporte[
                    "fecha_hora"
                ].isoformat(),

            "latitud":
                reporte["latitud"],

            "longitud":
                reporte["longitud"],
        }
        for reporte in reportes[:8]
    ]

    return {
        "fecha_consulta":
            ahora.isoformat(),

        "zona_horaria":
            "America/Guayaquil",

        "total_reportes":
            len(reportes),

        "reportes_hoy":
            reportes_hoy,

        "reportes_ultimos_7_dias":
            reportes_ultima_semana,

        "conteos_por_tipo":
            conteos_por_tipo,

        "hora_mayor_frecuencia":
            hora_mayor_frecuencia,

        "criterio_concentracion": (
            "Cantidad de reportes dentro "
            "de un radio de 500 metros "
            "alrededor de cada punto "
            "registrado."
        ),

        "concentraciones": {
            "general":
                _concentracion(
                    reportes,
                ),

            "asalto":
                _concentracion(
                    reportes,
                    "Asalto",
                ),

            "accidente":
                _concentracion(
                    reportes,
                    "Accidente",
                ),

            "emergencia_medica":
                _concentracion(
                    reportes,
                    "Emergencia médica",
                ),
        },

        "reportes_recientes":
            reportes_recientes,
    }


def _determinar_enfoque(
    pregunta: str,
) -> str:
    texto = _normalizar(
        pregunta,
    )

    if (
        "asalto" in texto
        or "robo" in texto
    ):
        return "asalto"

    if (
        "accidente" in texto
        or "choque" in texto
    ):
        return "accidente"

    if (
        "emergencia medica" in texto
        or "medica" in texto
        or "salud" in texto
    ):
        return "emergencia_medica"

    return "general"


def _es_pregunta_geografica(
    pregunta: str,
) -> bool:
    texto = _normalizar(
        pregunta,
    )

    palabras_geograficas = (
        "donde",
        "zona",
        "ubicacion",
        "concentracion",
        "peligro",
        "mapa",
    )

    return any(
        palabra in texto
        for palabra in palabras_geograficas
    )


def _crear_prompt(
    consulta: ConsultaAsistente,
    contexto: dict,
) -> str:
    historial = [
        {
            "rol": mensaje.rol,
            "contenido": mensaje.contenido,
        }
        for mensaje
        in consulta.historial[-6:]
    ]

    historial_json = json.dumps(
        historial,
        ensure_ascii=False,
    )

    contexto_json = json.dumps(
        contexto,
        ensure_ascii=False,
    )

    return f"""
Eres el asistente analítico oficial de GeoAlerta Riobamba.

Responde exclusivamente con base en el contexto JSON verificado
que se proporciona más adelante.

Reglas obligatorias:

1. Escribe en español claro, profesional y breve.
2. No inventes barrios, calles, sectores, cantidades,
   fechas ni coordenadas.
3. No reveles nombres, cédulas, teléfonos ni datos personales.
4. No afirmes que una zona es peligrosa.
5. Utiliza la expresión:
   "zona con mayor concentración de reportes registrados".
6. Aclara que el análisis se limita a los datos disponibles
   en GeoAlerta y no constituye una predicción del riesgo real.
7. Para preguntas geográficas utiliza exclusivamente
   el análisis calculado con un radio de 500 metros.
8. Si no existen datos suficientes, indícalo claramente.
9. Devuelve únicamente el texto final para el usuario.
10. No devuelvas JSON, código ni Markdown.

Pregunta actual:

{consulta.pregunta}

Historial reciente:

{historial_json}

Contexto analítico verificado:

{contexto_json}
""".strip()


def _consultar_gemini(
    consulta: ConsultaAsistente,
    contexto: dict,
) -> str:
    api_key = os.getenv(
        "GEMINI_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY no está configurada.",
        )

    modelo = os.getenv(
        "GEMINI_MODEL",
        "gemini-3.5-flash",
    ).strip()

    cliente = genai.Client(
        api_key=api_key,
    )

    respuesta = (
        cliente.models.generate_content(
            model=modelo,
            contents=_crear_prompt(
                consulta,
                contexto,
            ),
            config={
                "temperature": 0.2,
                "max_output_tokens": 700,
            },
        )
    )

    if not respuesta.text:
        raise RuntimeError(
            "Gemini no devolvió "
            "una respuesta.",
        )

    return respuesta.text.strip()


def _respuesta_de_respaldo(
    consulta: ConsultaAsistente,
    contexto: dict,
) -> str:
    total = contexto[
        "total_reportes"
    ]

    texto = _normalizar(
        consulta.pregunta,
    )

    enfoque = _determinar_enfoque(
        consulta.pregunta,
    )

    if total == 0:
        return (
            "Todavía no existen reportes "
            "para realizar el análisis."
        )

    if "hoy" in texto:
        return (
            "Hoy se registran "
            f"{contexto['reportes_hoy']} "
            "reporte(s) en GeoAlerta."
        )

    if (
        "semana" in texto
        or "7 dias" in texto
    ):
        return (
            "Durante los últimos siete días "
            "se registraron "
            f"{contexto['reportes_ultimos_7_dias']} "
            "reporte(s)."
        )

    if _es_pregunta_geografica(
        consulta.pregunta,
    ):
        zona = contexto[
            "concentraciones"
        ].get(enfoque)

        if zona is None:
            return (
                "No existen datos suficientes "
                "para calcular la zona con "
                "mayor concentración."
            )

        return (
            "La zona con mayor concentración "
            "de reportes registrados reúne "
            f"{zona['cantidad']} caso(s) "
            "dentro de un radio de 500 metros. "
            "Este análisis se limita a los datos "
            "disponibles en GeoAlerta y no "
            "constituye una predicción "
            "del riesgo real."
        )

    detalle = ", ".join(
        f"{tipo}: {cantidad}"
        for tipo, cantidad
        in contexto[
            "conteos_por_tipo"
        ].items()
    )

    return (
        f"GeoAlerta contiene {total} "
        "reporte(s). Distribución actual: "
        f"{detalle}."
    )


@router.get("/salud")
def salud_asistente():
    return {
        "estado": "ok",

        "gemini_configurado": bool(
            os.getenv(
                "GEMINI_API_KEY",
                "",
            ).strip()
        ),

        "modelo": os.getenv(
            "GEMINI_MODEL",
            "gemini-3.6-flash",
        ),
    }


@router.post(
    "",
    response_model=RespuestaAsistente,
)
def consultar_asistente(
    consulta: ConsultaAsistente,
):
    try:
        reportes = _cargar_reportes()

        contexto = _construir_contexto(
            reportes,
        )

        enfoque = _determinar_enfoque(
            consulta.pregunta,
        )

        zona = contexto[
            "concentraciones"
        ].get(enfoque)

        mostrar_en_mapa = (
            _es_pregunta_geografica(
                consulta.pregunta,
            )
            and zona is not None
        )

        try:
            texto_respuesta = (
                _consultar_gemini(
                    consulta,
                    contexto,
                )
            )

            generado_por = os.getenv(
                "GEMINI_MODEL",
                "gemini-3.6-flash",
            )

        except Exception as error_gemini:
            print(
                "Respaldo local activado: "
                f"{error_gemini}"
            )

            texto_respuesta = (
                _respuesta_de_respaldo(
                    consulta,
                    contexto,
                )
            )

            generado_por = (
                "analitica-local-respaldo"
            )

        tipos = {
            "general": "Todos",
            "asalto": "Asalto",
            "accidente": "Accidente",
            "emergencia_medica":
                "Emergencia médica",
        }

        return RespuestaAsistente(
            titulo="Asistente GeoAlerta",

            respuesta=texto_respuesta,

            mostrar_en_mapa=
                mostrar_en_mapa,

            latitud=(
                zona["latitud"]
                if mostrar_en_mapa
                else None
            ),

            longitud=(
                zona["longitud"]
                if mostrar_en_mapa
                else None
            ),

            zoom=15.5,

            tipo_reporte=
                tipos[enfoque],

            cantidad_zona=(
                zona["cantidad"]
                if mostrar_en_mapa
                else None
            ),

            radio_metros=
                RADIO_METROS,

            total_reportes=
                contexto[
                    "total_reportes"
                ],

            generado_por=
                generado_por,
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,

            detail=(
                "No fue posible procesar "
                "la consulta: "
                f"{str(error)}"
            ),
        ) from error