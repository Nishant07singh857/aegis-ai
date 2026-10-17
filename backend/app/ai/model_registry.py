# backend/app/ai/model_registry.py
"""
AEGIS AI - Model registry for (future) self-trained models.

The user plans to train their own model later. This module is the plug-in
point for that future: a local checkpoint FILE can be registered by path,
and the registry hands back a uniform interface the rest of the system
already understands (LLMProvider).

NO TRAINING HAPPENS HERE. There is no GPU, no dataset, and no training
loop in this environment -- this file is architecture, not training code.

How the future model plugs in (when it exists):

    1. Train your model elsewhere (GPU machine, your own data).
    2. Save a checkpoint file, e.g. /models/aegis-commentary-v1.pt
       (or a directory for HF-style checkpoints).
    3. Write a small wrapper class implementing the SelfTrainedModel
       interface documented below (load / generate / metadata), e.g.::

           from backend.app.ai.model_registry import SelfTrainedModel

           class MyCommentaryModel(SelfTrainedModel):
               def load(self, path): ...
               def generate(self, prompt, **kwargs): ...
               def metadata(self): ...

    4. Register it::

           from backend.app.ai import ModelRegistry, get_llm_provider
           registry = ModelRegistry()
           registry.register(
               name="aegis-commentary-v1",
               path="/models/aegis-commentary-v1.pt",
               wrapper=MyCommentaryModel(),
           )
           provider = registry.as_provider("aegis-commentary-v1")
           # `provider` is an LLMProvider: drop it anywhere get_llm_provider()
           # output is used (e.g. ScriptGenerator).

    5. (Optional) set AEGIS_LLM_PROVIDER=self-trained:<name> -- wiring for
       that env value is intentionally left to the training milestone, when
       the wrapper class actually exists.
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import LLMProvider, ProviderUnavailableError

log = logging.getLogger("aegis.ai.registry")


class SelfTrainedModel(ABC):
    """
    Interface a future self-trained checkpoint wrapper MUST implement.

    The registry (and RegisteredModelProvider below) only ever calls these
    three methods, so a new model is a ~50-line wrapper, not a rewrite.

    Method contracts:

    load(path: str) -> None
        Load the checkpoint from ``path`` into memory. Called ONCE by the
        registry at registration time (or lazily on first use, depending on
        the wrapper). Must raise a clear exception if the file is missing
        or corrupt -- never silently continue with an unloaded model.
        Expected to handle: .pt / .bin / .safetensors files, or a directory
        containing a HuggingFace-style checkpoint. Device placement
        (cpu/cuda) is the wrapper's decision; default to CPU so inference
        also works on machines without a GPU.

    generate(prompt: str, **kwargs) -> str
        Run inference and return the generated text. Synchronous.
        Common kwargs the callers will pass:
          - max_tokens (int, default ~200)
          - temperature (float, default ~0.8)
          - system (str, optional system instruction)
        Must return a non-empty string on success. On failure raise an
        exception (do NOT return placeholder text -- the provider layer
        converts exceptions into an honest fallback, see below).

    metadata() -> Dict[str, Any]
        Describe the model honestly. Minimum keys:
          - "name": human-readable model name, e.g. "aegis-commentary-v1"
          - "checkpoint_path": where it was loaded from
          - "architecture": e.g. "llama-3.1-8b-lora", "custom-transformer"
          - "trained_on": short dataset description, e.g.
            "120k BGMI commentary lines, 2024-2025 season"
          - "parameters": approx parameter count, e.g. "8B"
          - "precision": e.g. "fp16", "int8", "fp32"
          - "device": "cpu" | "cuda" | ...
        Extra keys are welcome (eval scores, training date, license).
        This metadata surfaces in logs and API responses so operators can
        always tell which model produced a given output.

    Optional but recommended:
        predict(...) -- alias for generate() if your training code already
            uses that name; the provider below accepts either.
        unload() -- free VRAM/RAM when the model is unregistered.
    """

    @abstractmethod
    def load(self, path: str) -> None:
        """Load the checkpoint at ``path``. Raise clearly on failure."""
        ...

    @abstractmethod
    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Run inference; return generated text (non-empty on success)."""
        ...

    @abstractmethod
    def metadata(self) -> Dict[str, Any]:
        """Honest description of the model (see class docstring)."""
        ...


@dataclass
class RegisteredModel:
    """A registered checkpoint: name, path, wrapper, registration time."""
    name: str
    path: str
    wrapper: SelfTrainedModel
    registered_at: float = field(default_factory=time.time)
    loaded: bool = False


class RegisteredModelProvider(LLMProvider):
    """
    LLMProvider adapter around a SelfTrainedModel wrapper.

    Lets a self-trained checkpoint be used anywhere an LLMProvider is
    expected (commentary generation, translation) with honest
    source="self-trained:<name>" labelling.
    """

    def __init__(self, registered: RegisteredModel) -> None:
        super().__init__()
        self._registered = registered

    @property
    def name(self) -> str:
        return f"self-trained:{self._registered.name}"

    def available(self) -> bool:
        # Available iff the wrapper loaded its checkpoint successfully and
        # the file still exists on disk.
        return self._registered.loaded and os.path.exists(self._registered.path)

    def generate(self, prompt: str, **kwargs: Any) -> str:
        if not self.available():
            raise ProviderUnavailableError(
                f"Self-trained model '{self._registered.name}' is not loaded."
            )
        start = time.time()
        # Accept wrappers that named the method `predict` instead.
        gen = getattr(self._registered.wrapper, "generate", None) or getattr(
            self._registered.wrapper, "predict", None
        )
        try:
            text = (gen(prompt, **kwargs) or "").strip()
        except Exception as e:
            raise ProviderUnavailableError(
                f"Self-trained model '{self._registered.name}' inference failed: {e}"
            ) from e
        if not text:
            raise ProviderUnavailableError(
                f"Self-trained model '{self._registered.name}' returned empty text."
            )
        meta = {}
        try:
            meta = self._registered.wrapper.metadata() or {}
        except Exception:
            pass  # metadata is best-effort; never break generation
        self._record(
            source=f"self-trained:{self._registered.name}",
            model=str(meta.get("architecture", self._registered.name)),
            latency_ms=(time.time() - start) * 1000.0,
            extra={"checkpoint_path": self._registered.path},
        )
        return text

    @property
    def supports_translation(self) -> bool:
        # A trained seq2seq/LLM checkpoint can translate; a classifier
        # checkpoint cannot. Default True -- wrapper authors should
        # override metadata()["capabilities"] if unsure. Conservative
        # default: the registry marks capabilities explicitly instead.
        return bool(
            self._registered.wrapper.metadata().get("capabilities", {}).get(
                "translation", True
            )
        )


class ModelRegistry:
    """
    Registry of self-trained model checkpoints, keyed by name.

    Typical lifecycle::

        registry = ModelRegistry()
        registry.register("aegis-commentary-v1",
                          "/models/aegis-commentary-v1.pt",
                          MyCommentaryModel())
        provider = registry.as_provider("aegis-commentary-v1")
    """

    def __init__(self) -> None:
        self._models: Dict[str, RegisteredModel] = {}

    def register(
        self,
        name: str,
        path: str,
        wrapper: SelfTrainedModel,
        load_now: bool = True,
    ) -> RegisteredModel:
        """
        Register a checkpoint file by path.

        Args:
            name: unique registry name, e.g. "aegis-commentary-v1".
            path: filesystem path to the checkpoint file (or directory).
            wrapper: SelfTrainedModel implementation for this checkpoint.
            load_now: call wrapper.load(path) immediately (default True).
                      Set False to defer loading until first use.

        Raises:
            ValueError: name already registered or wrapper is not a
                        SelfTrainedModel.
            FileNotFoundError: path does not exist (checked up front so a
                        typo fails fast instead of at 3am in production).
        """
        if name in self._models:
            raise ValueError(f"Model '{name}' is already registered.")
        if not isinstance(wrapper, SelfTrainedModel):
            raise ValueError(
                "wrapper must implement the SelfTrainedModel interface "
                "(load / generate / metadata)."
            )
        if not os.path.exists(path):
            raise FileNotFoundError(f"Checkpoint not found: {path}")

        registered = RegisteredModel(name=name, path=path, wrapper=wrapper)
        if load_now:
            wrapper.load(path)  # raises clearly on corrupt checkpoints
            registered.loaded = True
            log.info("Self-trained model '%s' loaded from %s", name, path)
        else:
            log.info(
                "Self-trained model '%s' registered (lazy load) from %s", name, path
            )
        self._models[name] = registered
        return registered

    def unregister(self, name: str) -> None:
        """Remove a model; calls wrapper.unload() if it defines one."""
        registered = self._models.pop(name, None)
        if registered is None:
            return
        unload = getattr(registered.wrapper, "unload", None)
        if callable(unload):
            try:
                unload()
            except Exception as e:
                log.warning("unload() for '%s' raised: %s", name, e)
        log.info("Self-trained model '%s' unregistered.", name)

    def get(self, name: str) -> Optional[RegisteredModel]:
        return self._models.get(name)

    def is_registered(self, name: str) -> bool:
        return name in self._models

    def list_models(self) -> List[Dict[str, Any]]:
        """Honest inventory: what is registered, loaded, and where from."""
        out = []
        for r in self._models.values():
            meta: Dict[str, Any] = {}
            if r.loaded:
                try:
                    meta = r.wrapper.metadata() or {}
                except Exception:
                    pass
            out.append(
                {
                    "name": r.name,
                    "path": r.path,
                    "loaded": r.loaded,
                    "registered_at": r.registered_at,
                    "metadata": meta,
                }
            )
        return out

    def as_provider(self, name: str) -> RegisteredModelProvider:
        """
        Adapt a registered model to the LLMProvider interface so it can be
        used by ScriptGenerator / LanguageManager like any other provider.
        """
        registered = self._models.get(name)
        if registered is None:
            raise KeyError(f"No model registered as '{name}'.")
        if not registered.loaded:
            # Lazy-load on first conversion.
            registered.wrapper.load(registered.path)
            registered.loaded = True
        return RegisteredModelProvider(registered)
