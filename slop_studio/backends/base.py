"""Backend ABC — the contract every slop-studio execution backend implements."""

from abc import ABC, abstractmethod


class Backend(ABC):
    """Abstract base for slop-studio execution backends.

    Implementations expose the five HTTP-level primitives the high-level
    orchestration (queue_prompt, check_job, get_image) composes. Backend-specific
    concerns (auth headers, path prefixes, redirect-following, two-call status
    resolution) live inside the implementation — callers work with plain
    dicts/bytes/str.
    """

    name: str  # "local" | "cloud" — reserved for prompt_id prefixing in Story 6.3

    @abstractmethod
    async def submit(self, workflow: dict) -> dict:
        """POST a prepared workflow. Return {'status': 'success', 'prompt_id': '<id>'}
        or a terminal_error / transient_error dict.

        Callers inject inputs, randomize seeds, and resolve aspect ratios BEFORE
        calling submit — this method only handles the network round-trip.
        """

    @abstractmethod
    async def status(self, prompt_id: str) -> dict:
        """Return job status as {'state': pending|running|completed|failed,
        'outputs'?: dict, 'error'?: str}.

        For backends that split status and history (cloud: /api/job/{id}/status
        + /api/history_v2), implementations synthesize the unified shape by
        calling history() internally when state == completed.
        """

    @abstractmethod
    async def history(self, prompt_id: str) -> dict:
        """Fetch the outputs record for a job. Returns the outputs dict
        (node_id → {images/videos/audio: [...]}) or {} if not yet complete.
        """

    @abstractmethod
    async def view(self, filename: str, subfolder: str = "", type: str = "output") -> bytes:
        """Download raw bytes for an output file referenced in a history outputs entry."""

    @abstractmethod
    async def upload_asset(self, file_path: str) -> str:
        """Upload a local image for a LoadImage-style node input.

        Return the value to inject into the workflow node's image field
        (local: the ComfyUI filename; cloud: the asset name/id/hash — TBD Story 6.4).
        """
