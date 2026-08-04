import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from src.tsf_ipc import get_tsf_speech_tip_bridge

from .textbox_context import TextBoxContext, get_active_textbox_context


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContextCaptureOptions:
    debug: bool = False
    excluded_process_names: tuple[str, ...] = ()
    clipboard_fallback_enabled: bool = False


class ContextProvider(Protocol):
    name: str

    async def capture(
        self,
        options: ContextCaptureOptions,
    ) -> TextBoxContext | None: ...


class TsfContextProvider:
    name = "tsf"

    async def capture(
        self,
        options: ContextCaptureOptions,
    ) -> TextBoxContext | None:
        snapshot = await get_tsf_speech_tip_bridge().query_context()
        if snapshot is None:
            return None
        if _is_excluded_process(
            snapshot.process_name,
            options.excluded_process_names,
        ):
            _LOGGER.info(
                "TSF context skipped excluded process=%s pid=%d",
                snapshot.process_name or "unknown",
                snapshot.process_id,
            )
            return None
        return TextBoxContext(
            text=snapshot.text,
            source="tsf",
            process_id=snapshot.process_id or None,
            process_name=snapshot.process_name,
            caret_offset=snapshot.caret_offset,
            selection_start=snapshot.selection_start,
            selection_end=snapshot.selection_end,
            caret_source="tsf_selection",
        )


class UiaContextProvider:
    name = "uia"

    async def capture(
        self,
        options: ContextCaptureOptions,
    ) -> TextBoxContext | None:
        return await asyncio.to_thread(
            get_active_textbox_context,
            debug=options.debug,
            excluded_process_names=options.excluded_process_names,
            clipboard_fallback_enabled=options.clipboard_fallback_enabled,
        )


class ContextProviderRegistry:
    def __init__(self, providers: tuple[ContextProvider, ...]) -> None:
        self._providers = providers

    async def capture(
        self,
        options: ContextCaptureOptions,
    ) -> TextBoxContext | None:
        for provider in self._providers:
            try:
                captured = await provider.capture(options)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - context is best effort
                _LOGGER.warning(
                    "Context provider failed provider=%s error=%s",
                    provider.name,
                    type(exc).__name__,
                )
                continue
            if captured is not None:
                _LOGGER.info(
                    "Context captured provider=%s source=%s process=%s pid=%s chars=%d",
                    provider.name,
                    captured.source,
                    captured.process_name or "unknown",
                    captured.process_id or "unknown",
                    len(captured.text),
                )
                return captured
        return None


def _is_excluded_process(
    process_name: str | None,
    excluded_process_names: tuple[str, ...],
) -> bool:
    if not process_name:
        return False
    normalized = process_name.strip().casefold()
    return any(
        normalized == excluded.strip().casefold()
        for excluded in excluded_process_names
        if excluded.strip()
    )


DEFAULT_CONTEXT_PROVIDER_REGISTRY = ContextProviderRegistry(
    (TsfContextProvider(), UiaContextProvider())
)
