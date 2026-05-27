import structlog

def get_production_logger(module_name: str):
    return structlog.get_logger(f"aegis.production.{module_name}")
