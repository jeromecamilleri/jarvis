"""Registre des modèles : un modèle principal (Qwen) et, plus tard, des modèles spécialisés."""
from dataclasses import dataclass

from app.core.model_adapter import LLMError, ModelAdapter

PRIMARY, SPECIALIST = "primary", "specialist"


class UnknownModelError(LLMError):
    pass


@dataclass
class ModelSpec:
    id: str
    adapter: ModelAdapter
    role: str = PRIMARY
    specialty: str | None = None  # obligatoire pour un spécialiste
    enabled: bool = True

    def __post_init__(self):
        if self.role not in (PRIMARY, SPECIALIST):
            raise ValueError(f"rôle de modèle inconnu : {self.role}")
        if self.role == SPECIALIST and not self.specialty:
            raise ValueError(f"le modèle spécialisé {self.id} doit déclarer sa spécialité")
        if self.role == PRIMARY and self.specialty:
            raise ValueError("le modèle principal n'a pas de spécialité")

    @property
    def capabilities(self) -> frozenset[str]:
        return self.adapter.capabilities


class ModelRegistry:
    def __init__(self):
        self._models: dict[str, ModelSpec] = {}
        self._primary: ModelSpec | None = None

    def register(self, spec: ModelSpec) -> ModelSpec:
        if spec.id in self._models:
            raise ValueError(f"modèle déjà enregistré : {spec.id}")
        if spec.role == PRIMARY:
            if self._primary is not None:
                raise ValueError(f"un modèle principal existe déjà : {self._primary.id}")
            self._primary = spec
        self._models[spec.id] = spec
        return spec

    def get(self, model_id: str) -> ModelSpec:
        try:
            return self._models[model_id]
        except KeyError:
            raise UnknownModelError(f"Modèle inconnu : {model_id}") from None

    @property
    def primary(self) -> ModelSpec:
        if self._primary is None:
            raise UnknownModelError("Aucun modèle principal enregistré")
        return self._primary

    def specialists(self, specialty: str) -> list[ModelSpec]:
        return [m for m in self._models.values() if m.role == SPECIALIST and m.specialty == specialty]
