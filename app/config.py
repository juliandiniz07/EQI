import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    modelo: str
    esforco: str
    provedor_eventos: str
    brapi_token: str
    brapi_base_url: str
    aliquota_ir_jcp: float

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            modelo=os.getenv("AGENTE_MODELO", "claude-opus-5"),
            esforco=os.getenv("AGENTE_ESFORCO", "medium"),
            provedor_eventos=os.getenv("PROVEDOR_EVENTOS", "brapi"),
            brapi_token=os.getenv("BRAPI_TOKEN", ""),
            brapi_base_url=os.getenv("BRAPI_BASE_URL", "https://brapi.dev/api"),
            aliquota_ir_jcp=float(os.getenv("ALIQUOTA_IR_JCP", "0.15")),
        )
