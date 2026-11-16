"""Sélection déterministe d'un modèle à partir d'une `RoutingRequest` ; ne lit jamais les messages."""
import logging
from dataclasses import dataclass, field

from app.core import tracing
from app.core.model_adapter import TEXT, LLMError
from app.core.model_registry import ModelRegistry, ModelSpec

log = logging.getLogger("jarvis.models")


class NoCompatibleModelError(LLMError):
    pass


@dataclass
class RoutingRequest:
    required: frozenset[str] = field(default_factory=lambda: frozenset({TEXT}))  # capacités exigées
    specialty: str | None = None  # None = demande générale -> modèle principal


@dataclass
class Route:
    model: ModelSpec
    fallback: bool = False  # vrai si une spécialité était demandée et que le principal l'a remplacée


class ModelRouter:
    """Règles : sans spécialité -> principal ; avec spécialité -> premier spécialiste activé et compatible,
    sinon repli sur le principal (`fallback="primary"`, WARNING) ou erreur (`fallback="error"`).
    Le principal doit lui aussi être activé et couvrir les capacités : jamais de suppression silencieuse."""

    def __init__(self, registry: ModelRegistry, fallback: str = "primary"):
        if fallback not in ("primary", "error"):
            raise ValueError(f"stratégie de repli inconnue : {fallback}")
        self.registry, self.fallback = registry, fallback

    def select(self, request: RoutingRequest) -> Route:
        info: dict = {}
        try:
            route = self._select(request, info)
        except NoCompatibleModelError as e:
            tracing.emit("model.selection.failed", "router", "error", specialty=request.specialty,
                         required=sorted(request.required), fallback_mode=self.fallback,
                         reason=info.get("reason"), detail=tracing.safe_detail(e))
            raise
        tracing.emit("model.selected", "router", model_id=route.model.id, role=route.model.role,
                     specialty=request.specialty, required=sorted(request.required),
                     fallback=route.fallback, reason=info["reason"])
        return route

    def _select(self, request: RoutingRequest, info: dict) -> Route:
        required = frozenset(request.required)
        if request.specialty is None:
            info["reason"] = "no_specialty"
            return Route(self._primary(required, info))
        candidates = self.registry.specialists(request.specialty)
        for spec in candidates:
            if spec.enabled and required <= spec.capabilities:
                info["reason"] = "specialist_match"
                return Route(spec)
        info["reason"] = ("no_specialist_registered" if not candidates
                          else "specialist_disabled" if not any(s.enabled for s in candidates)
                          else "specialist_incompatible")
        if self.fallback == "error":
            raise NoCompatibleModelError(f"Aucun modèle spécialisé « {request.specialty} » compatible.")
        log.warning("Aucun modèle spécialisé « %s » disponible et compatible (%s) : repli sur le modèle principal.",
                    request.specialty, ", ".join(sorted(required)))
        route = Route(self._primary(required, info), fallback=True)
        return route

    def _primary(self, required: frozenset[str], info: dict) -> ModelSpec:
        spec = self.registry.primary
        if not spec.enabled:
            info["reason"] = "primary_disabled"
            raise NoCompatibleModelError(f"Le modèle principal {spec.id} est désactivé.")
        missing = required - spec.capabilities
        if missing:
            info["reason"] = "primary_missing_capabilities"
            raise NoCompatibleModelError(
                f"Le modèle {spec.id} ne prend pas en charge : {', '.join(sorted(missing))}.")
        return spec
