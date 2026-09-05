"""Local fictional evaluation with every proposed write held for review.

Uses the existing demo interface and shared approval policy. This is SQLite with local bearer
access, not a customer pilot or enterprise identity proof. No service is published by this command.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MARKER = "atezain-solo-demo-v1\n"


class SoloModel:
    """Declare the local demo's date and unsent-reminder semantics to the model.

    `today` pins the date the wrapper declares; the demo leaves it None and reads the clock, the
    served evaluation (`redteam/served.py --wrapper solo-date`) passes the run's date so that the
    cached input names it and a re-run on another day hits the same cache."""
    def __init__(self, model, today=None):
        self.model = model
        self.today = today

    def complete(self, system, user):
        facts = json.loads(user)
        facts["evaluation_date"] = (self.today or datetime.date.today()).isoformat()
        system += ("\n\nAclaraciones del entorno de evaluación local: evaluation_date es la fecha actual "
                   "facilitada por el sistema, no por el cliente. Usa esa fecha para interpretar vencimientos "
                   "y promesas; no recomiendes esperar hasta una fecha ya pasada. "
                   "send_reminder solo propone GUARDAR un borrador local para revisión; esta aplicación "
                   "NO envía mensajes. No afirmes que se envió un recordatorio ni propongas el estado "
                   "reminded por haber preparado un borrador. Una nota puede indicar borrador preparado "
                   "y pendiente de revisión. No inventes pagos, acuerdos, firmas ni departamentos. "
                   "Todas las acciones propuestas necesitan aprobación antes de guardarse.")
        return self.model.complete(system, json.dumps(facts, ensure_ascii=False, indent=1))


def prepare(state, model="stub"):
    if "api.app" in sys.modules:
        raise RuntimeError("start the solo demo in a fresh process")
    if model not in {"stub", "groq"}:
        raise ValueError("choose stub or groq")
    if model == "groq" and not os.getenv("GROQ_API_KEY"):
        raise ValueError("the existing model key is required for live fictional drafts")
    state = Path(state).resolve()
    marker = state / ".solo-demo"
    if state.exists() and any(state.iterdir()) and (not marker.is_file() or marker.read_text() != MARKER):
        raise ValueError("refusing to reuse storage that is not a solo-demo workspace")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker.write_text(MARKER)
    # Do not inherit remote storage, customer identity, accounting or tracing configuration.
    for key in list(os.environ):
        if key.startswith(("ATEZAIN_", "LANGFUSE_", "LANGSMITH_", "LANGCHAIN_")) or key == "DATABASE_URL":
            os.environ.pop(key, None)
    if model == "stub":
        os.environ.pop("GROQ_API_KEY", None)
    os.environ.update(ATEZAIN_MODE="demo", ATEZAIN_MODEL=model, ATEZAIN_STATE_DIR=str(state))
    from api import app as application
    from api.configuration import served_policy
    application.config = served_policy("pilot")
    original_model_for = application.model_for
    def model_for(key):
        model, name, byok = original_model_for(key)
        return SoloModel(model), name, byok
    application.model_for = model_for
    return application.app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["stub", "groq"], default="stub")
    args = parser.parse_args()
    app = prepare(ROOT / "var/solo-evaluation/supervised", args.model)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8767, access_log=False)


if __name__ == "__main__":
    main()
