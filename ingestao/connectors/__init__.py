"""
Registry central de todos os 27 conectores.
Importe `REGISTRY` para iterar sobre todas as assembleias.
"""
from .almg import ALMGConnector
from .alep import ALEPConnector
from .alesp import ALESPConnector
from .cldf import CLDFConnector
from .alepe import ALEPEConnector
from .alece import ALECEConnector
from .alego import ALEGOConnector
from ._stubs import (
    ALERJConnector, ALBAConnector,
    ALERGSConnector, ALEPAConnector, ALEMAConnector,
    ALESConnector, ALMTConnector, ALEMSConnector,
    ALERNConnector, ALPBConnector, ALESEConnector, ALALConnector,
    ALEPIConnector, ALEROConnector, ALEAMConnector, ALESCConnector,
    ALETOConnector, ALERRConnector, ALEACConnector, ALAPConnector,
)

# Mapa assembly_id → classe do conector
REGISTRY: dict[str, type] = {
    # ── Tier 1 — API REST (implementados) ─────────────────────────────────
    "almg":  ALMGConnector,   # MG — 77 dep — API REST documentada
    "alep":  ALEPConnector,   # PR — 54 dep — REST + Swagger

    # ── Tier 1 — API/XML (stubs, sprint 2) ────────────────────────────────
    "alesp": ALESPConnector,  # SP — 94 dep — XML bulk + API parcial
    "alepe": ALEPEConnector,  # PE — 49 dep — API + CSV
    "cldf":  CLDFConnector,   # DF — 24 dep — dados abertos (prioridade alta)

    # ── Tier 2 — CSV/misto (stubs, sprint 3) ──────────────────────────────
    "alece": ALECEConnector,  # CE — 46 dep
    "alego": ALEGOConnector,  # GO — 41 dep
    "ales":  ALESConnector,   # ES — 30 dep
    "almt":  ALMTConnector,   # MT — 24 dep
    "alems": ALEMSConnector,  # MS — 24 dep
    "alern": ALERNConnector,  # RN — 24 dep
    "alpb":  ALPBConnector,   # PB — 36 dep
    "alese": ALESEConnector,  # SE — 24 dep
    "alal":  ALALConnector,   # AL — 27 dep
    "alero": ALEROConnector,  # RO — 24 dep
    "aleam": ALEAMConnector,  # AM — 24 dep
    "alesc": ALESCConnector,  # SC — 40 dep

    # ── Tier 3 — scraping (stubs, sprint 4) ───────────────────────────────
    "alerj":  ALERJConnector,  # RJ — 70 dep — site instável
    "alba":   ALBAConnector,   # BA — 63 dep
    "alergs": ALERGSConnector, # RS — 55 dep — pós-enchentes
    "alepa":  ALEPAConnector,  # PA — 41 dep
    "alepi":  ALEPIConnector,  # PI — 30 dep
    "aleto":  ALETOConnector,  # TO — 24 dep
    "alerr":  ALERRConnector,  # RR — 24 dep
    "aleac":  ALEACConnector,  # AC — 24 dep
    "alap":   ALAPConnector,   # AP — 24 dep

    # ── Tier 4 — declaradamente fechada ───────────────────────────────────
    "alema": ALEMAConnector,  # MA — 42 dep — cuidado: risco jurídico
}


def get_connector(assembly_id: str):
    """Retorna uma instância do conector para o assembly_id dado."""
    cls = REGISTRY.get(assembly_id)
    if cls is None:
        raise KeyError(f"Conector não encontrado: {assembly_id!r}")
    return cls()


def all_connectors():
    """Retorna instâncias de todos os 27 conectores."""
    return [cls() for cls in REGISTRY.values()]


__all__ = ["REGISTRY", "get_connector", "all_connectors"]
