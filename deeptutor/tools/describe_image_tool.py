"""Read an image file from the workspace and describe what the model sees.

The chat agent can discover image files (note assets, chat attachments,
generated figures) but had no way to look at them: it only ever saw the path.
This tool closes that gap by handing the image to the configured vision model
and returning a description the agent can reason over.

It complements - rather than replaces - OCR: OCR extracts literal text from a
scanned page, while this returns what the image *shows* (layout, diagrams,
handwriting, photographs). For a photo of a worksheet, ask for the printed
question and the handwritten answers.
"""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from typing import Any

from deeptutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult

# Formats the vision provider accepts. Pillow opens the rest and re-encodes.
_SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}

# Longest edge after downscale. Keeps the base64 payload small enough for a
# provider while keeping printed text legible.
_MAX_EDGE = 1600

# Ceiling on one vision call. Reasoning vision models can take minutes.
_VISION_TIMEOUT_S = 240


class DescribeImageTool(BaseTool):
    """Look at an image in the workspace and describe its content."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="describe_image",
            description=(
                "Read an image file and return a description of what it shows, using the "
                "configured vision model. Use this whenever the user attaches or points "
                "at an image (a photo of a worksheet, a screenshot, a diagram, a chart) "
                "and you need its actual content - the printed question, the handwritten "
                "answers, the labels on a figure. Call this tool directly with the path "
                "as it appears in the notebook page or tool output (for example "
                "/api/documents/<doc-id>/assets/<file>.png); do NOT use exec or "
                "workspace_read to fetch the image bytes, and do not copy the file "
                "elsewhere first. Supports an optional question so you can ask for "
                "exactly what you need."
            ),
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description=(
                        "Path of the image, exactly as it appears in the notebook page or "
                        "tool output - e.g. /api/documents/<doc-id>/assets/<file>.png or "
                        "co-writer/documents/doc_<doc-id>/assets/<file>.png or "
                        "chat/attachments/<session>/<file>.png. A trailing query string "
                        "(?dt_workspace=...) is accepted and ignored."
                    ),
                ),
                ToolParameter(
                    name="question",
                    type="string",
                    description=(
                        "What to look for, e.g. 'transcribe the printed question and the "
                        "handwritten answers'. Defaults to a general description."
                    ),
                    required=False,
                    default="",
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        raw_path = str(kwargs.get("path") or "").strip()
        if not raw_path:
            return ToolResult(
                content="describe_image needs a 'path' to an image file.",
                success=False,
            )
        # A notebook page references its pictures with a query string
        # (``/api/documents/<id>/assets/<file>.png?dt_workspace=``). The query
        # is not part of the file name, so strip it before resolving.
        raw_path = raw_path.split("?", 1)[0].split("#", 1)[0].strip()

        question = str(kwargs.get("question") or "").strip()

        try:
            image_path, data_uri, note = self._prepare_image(raw_path, kwargs)
        except ImageReadError as exc:
            return ToolResult(content=str(exc), success=False)

        description = await self._describe(image_path, data_uri, question)
        if description is None:
            return ToolResult(
                content=(
                    f"Could not describe {image_path.name}: the vision model did not "
                    "return an answer. The image was read successfully."
                ),
                success=False,
            )

        header = f"Description of {image_path.name}:"
        extra = f"\n({note})" if note else ""
        return ToolResult(
            content=f"{header}\n{description}{extra}",
            success=True,
            metadata={"image_path": str(image_path), "description": description},
        )

    # ------------------------------------------------------------------

    def _prepare_image(
        self,
        raw_path: str,
        kwargs: dict[str, Any],
    ) -> tuple[Path, str, str]:
        """Resolve, validate, downscale and base64-encode the image."""

        candidates: list[Path] = []
        for base in self._workspace_roots(kwargs):
            candidates.append(base / raw_path)
        candidates.append(Path(raw_path))
        # A note's pasted pictures are referenced by their API path
        # (``/api/documents/<doc-id>/assets/<file>``). On disk the document
        # folder is ``doc_<doc-id>`` under ``co-writer/documents/``, so accept
        # that shape too - it is what the agent sees in the note body.
        for base in self._workspace_roots(kwargs):
            for document_id in self._document_ids(raw_path):
                candidates.append(
                    base / "co-writer" / "documents" / f"doc_{document_id}" / "assets" / Path(raw_path).name
                )

        source: Path | None = None
        for candidate in candidates:
            try:
                if candidate.is_file():
                    source = candidate
                    break
            except OSError:
                continue
        if source is None:
            raise ImageReadError(
                f"No image file found at {raw_path!r}. "
                "Pass the workspace-relative path exactly as workspace_search or "
                "workspace_list reported it."
            )

        if source.suffix.lower() not in _SUPPORTED_SUFFIXES:
            raise ImageReadError(
                f"{source.name} is not an image file. Supported: "
                + ", ".join(sorted(_SUPPORTED_SUFFIXES))
            )

        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - Pillow ships with DeepTutor
            raise ImageReadError("Pillow is unavailable, so the image cannot be read.") from exc

        note = ""
        try:
            with Image.open(source) as image:
                image.load()
                width, height = image.size
                if max(width, height) > _MAX_EDGE:
                    scale = _MAX_EDGE / float(max(width, height))
                    resized = image.convert("RGB").resize(
                        (max(1, int(width * scale)), max(1, int(height * scale))),
                    )
                else:
                    resized = image.convert("RGB")
                buffer = io.BytesIO()
                resized.save(buffer, format="PNG")
                payload = buffer.getvalue()
                note = f"original {width}x{height}px"
        except ImageReadError:
            raise
        except Exception as exc:  # noqa: BLE001 - unreadable image is a user error
            raise ImageReadError(f"Could not decode {source.name}: {exc}") from exc

        encoded = base64.b64encode(payload).decode("ascii")
        return source, f"data:image/png;base64,{encoded}", note

    @staticmethod
    def _workspace_roots(kwargs: dict[str, Any]) -> list[Path]:
        """Directories a workspace-relative path may be resolved against.

        The workspace tools expose paths relative to the active binding's root
        (``WorkspaceBinding.root``), so that is the primary anchor. The user
        data root is kept as a fallback for paths the tools report against the
        account root instead.
        """

        roots: list[Path] = []
        try:
            from deeptutor.services.workspace import get_content_workspace_service

            service = get_content_workspace_service()
            workspace_id = str(kwargs.get("_workspace_id") or "")
            binding = (
                service.binding_by_id(workspace_id)
                if workspace_id
                else service.current_binding()
            )
            roots.append(Path(binding.root))
        except Exception:  # noqa: BLE001 - fall back to the account root
            pass
        try:
            from deeptutor.services.path_service import get_path_service

            account_root = Path(get_path_service().get_public_outputs_root())
            roots.append(account_root)
            # Isolated chat workspaces live beside the shared content
            # workspace (``<account>/workspaces/<ws-id>/...``). The workspace
            # tools only scan the content root, so a path that names one of
            # those sessions - for example a chat attachment - resolves only
            # against the account root.
            workspaces_root = account_root / "workspaces"
            if workspaces_root.is_dir():
                try:
                    for entry in sorted(workspaces_root.iterdir()):
                        if entry.is_dir():
                            roots.append(entry)
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            pass
        return roots

    @staticmethod
    def _document_ids(raw_path: str) -> list[str]:
        """Document ids referenced by a note-asset style path."""

        ids: list[str] = []
        parts = [part for part in raw_path.replace("\\", "/").split("/") if part]
        for part in parts:
            if part in ("assets", "documents", "api"):
                continue
            for candidate in (part, part.removeprefix("doc_")):
                if candidate and candidate not in ids:
                    ids.append(candidate)
        return ids

    async def _describe(
        self,
        image_path: Path,
        data_uri: str,
        question: str,
    ) -> str | None:
        """Ask the configured vision model what the image shows."""

        from deeptutor.agents.vision_solver.vision_solver_agent import VisionSolverAgent
        from deeptutor.services.llm.config import get_llm_config

        language = "zh"
        prompt = question or (
            "Describe this image in detail: what it shows, all visible text "
            "(printed and handwritten), and the structure of any diagrams or tables."
        )

        llm_config = get_llm_config()
        agent = VisionSolverAgent(
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
            language=language,
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ]

        chunks: list[str] = []
        try:
            async def _run() -> None:
                async for chunk in agent.stream_llm(
                    user_prompt="",
                    system_prompt="",
                    messages=messages,
                    model=agent.get_model(),
                ):
                    chunks.append(chunk)

            await asyncio.wait_for(_run(), timeout=_VISION_TIMEOUT_S)
        except asyncio.TimeoutError:
            return None
        except Exception:  # noqa: BLE001 - the caller reports a clean failure
            return None

        text = _strip_reasoning("".join(chunks).strip())
        return text or None


def _strip_reasoning(text: str) -> str:
    """Drop a reasoning model's private ``<think>`` block from the answer.

    Reasoning vision models emit their deliberation before the answer. It is
    noise for the calling agent, so keep only what follows the final closing
    tag - but if the model put the answer inside the reasoning, keep the whole
    text rather than returning nothing.
    """

    if "</think>" not in text:
        return text
    head, _, tail = text.rpartition("</think>")
    tail = tail.strip()
    if tail:
        return tail
    return head.replace("<think>", "").strip()


class ImageReadError(Exception):
    """The image could not be located, decoded, or prepared."""


__all__ = ["DescribeImageTool", "ImageReadError"]