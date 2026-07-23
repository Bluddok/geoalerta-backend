from main import app
from asistente import router as router_asistente


app.include_router(
    router_asistente,
)