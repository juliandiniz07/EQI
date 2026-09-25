from app.config import Config
from app.providers.base import EventoCorporativo, ProvedorEventos, ProvedorIndisponivel, TIPOS_EVENTO


def criar_provedor(config: Config) -> ProvedorEventos:
    if config.provedor_eventos == "mock":
        from app.providers.mock import ProvedorMock

        return ProvedorMock()
    if config.provedor_eventos == "brapi":
        from app.providers.brapi import ProvedorBrapi

        return ProvedorBrapi(base_url=config.brapi_base_url, token=config.brapi_token)
    raise ValueError(f"PROVEDOR_EVENTOS desconhecido: {config.provedor_eventos!r}")


__all__ = ["EventoCorporativo", "ProvedorEventos", "ProvedorIndisponivel", "TIPOS_EVENTO", "criar_provedor"]
