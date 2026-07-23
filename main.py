from datetime import date
from typing import Optional

import os
import psycopg2

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# Carga la configuración de PostgreSQL almacenada en el archivo .env.

load_dotenv()


# Crea la aplicación principal de FastAPI.

app = FastAPI(
    title="GeoAlerta Riobamba API",
    version="1.0.0",
)


# Permite que la app móvil y el geoportal consuman el backend.

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Define los datos que se reciben al registrar un ciudadano.

class UsuarioRegistro(BaseModel):
    cedula: str
    nombres: str
    apellidos: str
    celular: str
    genero: str
    fecha_nacimiento: date
    celular_contacto_emergencia: str


# Define los datos que se reciben al generar una emergencia.

class ReporteEmergencia(BaseModel):
    usuario_id: Optional[int] = None
    tipo_reporte: str
    descripcion: str
    cedula: str
    nombres: str
    apellidos: str
    celular: str
    genero: str
    fecha_nacimiento: date
    celular_contacto_emergencia: str
    latitud: float
    longitud: float


# Abre una conexión con la base geoalerta_riobamba.

def obtener_conexion():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        sslmode=os.getenv("DB_SSLMODE", "require"),
        connect_timeout=15,
    )

# Comprueba rápidamente si el backend está encendido.

@app.get("/")
def inicio():
    return {
        "mensaje": "Backend GeoAlerta Riobamba funcionando correctamente"
    }


# Registra al ciudadano o actualiza sus datos si su cédula ya existe.

@app.post("/usuarios")
def registrar_o_actualizar_usuario(usuario: UsuarioRegistro):
    conexion = None
    cursor = None

    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        consulta = """
        INSERT INTO usuarios (
            cedula,
            nombres,
            apellidos,
            celular,
            genero,
            fecha_nacimiento,
            celular_contacto_emergencia
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (cedula)
        DO UPDATE SET
            nombres = EXCLUDED.nombres,
            apellidos = EXCLUDED.apellidos,
            celular = EXCLUDED.celular,
            genero = EXCLUDED.genero,
            fecha_nacimiento = EXCLUDED.fecha_nacimiento,
            celular_contacto_emergencia =
                EXCLUDED.celular_contacto_emergencia,
            fecha_actualizacion = CURRENT_TIMESTAMP
        RETURNING id;
        """

        valores = (
            usuario.cedula,
            usuario.nombres,
            usuario.apellidos,
            usuario.celular,
            usuario.genero,
            usuario.fecha_nacimiento,
            usuario.celular_contacto_emergencia,
        )

        cursor.execute(consulta, valores)
        usuario_id = cursor.fetchone()[0]

        conexion.commit()

        return {
            "mensaje": "Usuario registrado correctamente",
            "id": usuario_id,
        }

    except Exception as error:
        if conexion is not None:
            conexion.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Error al registrar el usuario: {str(error)}",
        )

    finally:
        if cursor is not None:
            cursor.close()

        if conexion is not None:
            conexion.close()


# Devuelve los ciudadanos registrados para verificar la base.

@app.get("/usuarios")
def listar_usuarios():
    conexion = None
    cursor = None

    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        cursor.execute("""
            SELECT
                id,
                cedula,
                nombres,
                apellidos,
                celular,
                genero,
                fecha_nacimiento,
                celular_contacto_emergencia,
                fecha_registro,
                fecha_actualizacion
            FROM usuarios
            ORDER BY id DESC;
        """)

        filas = cursor.fetchall()
        usuarios = []

        for fila in filas:
            usuarios.append({
                "id": fila[0],
                "cedula": fila[1],
                "nombres": fila[2],
                "apellidos": fila[3],
                "celular": fila[4],
                "genero": fila[5],
                "fecha_nacimiento": fila[6],
                "celular_contacto_emergencia": fila[7],
                "fecha_registro": fila[8],
                "fecha_actualizacion": fila[9],
            })

        return usuarios

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Error al listar usuarios: {str(error)}",
        )

    finally:
        if cursor is not None:
            cursor.close()

        if conexion is not None:
            conexion.close()


# Guarda una emergencia con sus atributos y geometría Point.

@app.post("/reportes")
def crear_reporte(reporte: ReporteEmergencia):
    conexion = None
    cursor = None

    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        consulta = """
        INSERT INTO reportes_emergencia (
            usuario_id,
            tipo_reporte,
            descripcion,
            cedula,
            nombres,
            apellidos,
            celular,
            genero,
            fecha_nacimiento,
            celular_contacto_emergencia,
            latitud,
            longitud,
            ubicacion
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)
        )
        RETURNING id;
        """

        valores = (
            reporte.usuario_id,
            reporte.tipo_reporte,
            reporte.descripcion,
            reporte.cedula,
            reporte.nombres,
            reporte.apellidos,
            reporte.celular,
            reporte.genero,
            reporte.fecha_nacimiento,
            reporte.celular_contacto_emergencia,
            reporte.latitud,
            reporte.longitud,
            reporte.longitud,
            reporte.latitud,
        )

        cursor.execute(consulta, valores)
        reporte_id = cursor.fetchone()[0]

        conexion.commit()

        return {
            "mensaje": "Reporte registrado correctamente",
            "id": reporte_id,
        }

    except Exception as error:
        if conexion is not None:
            conexion.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Error al registrar el reporte: {str(error)}",
        )

    finally:
        if cursor is not None:
            cursor.close()

        if conexion is not None:
            conexion.close()


# Devuelve las emergencias para la app, el mapa y el geoportal.

@app.get("/reportes")
def listar_reportes():
    conexion = None
    cursor = None

    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        cursor.execute("""
            SELECT
                id,
                usuario_id,
                tipo_reporte,
                fecha_hora,
                descripcion,
                cedula,
                nombres,
                apellidos,
                celular,
                genero,
                fecha_nacimiento,
                edad,
                celular_contacto_emergencia,
                latitud,
                longitud
            FROM vista_reportes_emergencia
            ORDER BY fecha_hora DESC;
        """)

        filas = cursor.fetchall()
        reportes = []

        for fila in filas:
            reportes.append({
                "id": fila[0],
                "usuario_id": fila[1],
                "tipo_reporte": fila[2],
                "fecha_hora": fila[3],
                "descripcion": fila[4],
                "cedula": fila[5],
                "nombres": fila[6],
                "apellidos": fila[7],
                "celular": fila[8],
                "genero": fila[9],
                "fecha_nacimiento": fila[10],
                "edad": fila[11],
                "celular_contacto_emergencia": fila[12],
                "latitud": fila[13],
                "longitud": fila[14],
            })

        return reportes

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Error al listar reportes: {str(error)}",
        )

    finally:
        if cursor is not None:
            cursor.close()

        if conexion is not None:
            conexion.close()