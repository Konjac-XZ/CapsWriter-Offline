"""
Provider availability testing module.
Tests OpenAI-compatible providers by sending a test audio file and checking for successful HTTP responses.
"""

import asyncio
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any, Callable
from dataclasses import dataclass

from util.provider_config import provider_manager
from util.transcribe_provider import transcribe_audio


@dataclass
class TestResult:
    """Result of a single provider test."""
    provider_id: str
    provider_name: str
    provider_type: str
    success: bool
    response_time_ms: int
    status_code: Optional[int]
    transcription: Optional[str]
    error_message: Optional[str]


class ProviderAvailabilityTester:
    """Tests availability of OpenAI-compatible transcription providers."""

    def __init__(self, test_audio_path: Path, progress_callback: Optional[Callable[[str], None]] = None):
        self.test_audio_path = test_audio_path
        self.test_audio_data: Optional[bytes] = None
        self.progress_callback = progress_callback
        self.load_test_audio()

    @contextmanager
    def _activate_provider(self, provider_id: str):
        """Temporarily mark a provider as active so shared helpers read its settings."""
        # provider_manager is always available via import, but guard for completeness
        manager = provider_manager
        if manager is None:
            yield
            return

        original_active = getattr(manager, "active_provider", None)
        original_enabled = {pid: prov.enabled for pid, prov in manager.providers.items()}

        target = manager.providers.get(provider_id)
        if target is not None:
            target.enabled = True
            manager.active_provider = provider_id

        try:
            yield
        finally:
            manager.active_provider = original_active
            for pid, state in original_enabled.items():
                prov = manager.providers.get(pid)
                if prov is not None:
                    prov.enabled = state

    def _log_progress(self, message: str):
        """Send progress updates via callback if available."""
        if self.progress_callback:
            self.progress_callback(message)

    def load_test_audio(self) -> None:
        """Load the test audio file into memory."""
        if not self.test_audio_path.exists():
            raise FileNotFoundError(f"Test audio file not found: {self.test_audio_path}")

        with open(self.test_audio_path, 'rb') as f:
            self.test_audio_data = f.read()

    async def test_provider(self, provider_id: str) -> TestResult:
        """Test a single provider with timeout."""
        provider = provider_manager.get_provider(provider_id)
        if not provider:
            return TestResult(
                provider_id=provider_id,
                provider_name="Unknown",
                provider_type="unknown",
                success=False,
                response_time_ms=0,
                status_code=None,
                transcription=None,
                error_message="Provider not found"
            )

        # Skip non-OpenAI providers
        if provider.type != "openai":
            return TestResult(
                provider_id=provider_id,
                provider_name=provider.name,
                provider_type=provider.type,
                success=False,
                response_time_ms=0,
                status_code=None,
                transcription=None,
                error_message="Skipped: Only OpenAI-compatible providers are tested"
            )

        self._log_progress(f"正在测试 {provider.name}...")

        # Save original environment state to restore later
        import os
        original_env = {}
        provider_env_keys = [
            "TRANSCRIBE_PROVIDER", "OPENAI_API_KEY", "OPENAI_BASE_URL", "TRANSCRIBE_MODEL",
            "TRANSCRIBE_TEMPERATURE", "OPENAI_TRANSCRIBE_STREAM", "OPENAI_TRANSCRIBE_LANGUAGE",
            "OPENAI_TRANSCRIBE_FORMAT", "OPENAI_HTTP_TIMEOUT"
        ]

        for key in provider_env_keys:
            original_env[key] = os.environ.get(key)

        start_time = time.time()

        try:
            with self._activate_provider(provider_id):
                # Set environment variables for this provider
                os.environ["TRANSCRIBE_PROVIDER"] = provider.type
                os.environ["OPENAI_API_KEY"] = provider.settings.get("api_key", "")
                os.environ["OPENAI_BASE_URL"] = provider.settings.get("base_url", "")
                os.environ["TRANSCRIBE_MODEL"] = provider.settings.get("model", "")
                os.environ["TRANSCRIBE_TEMPERATURE"] = str(provider.settings.get("temperature", 0.2))
                os.environ["OPENAI_TRANSCRIBE_STREAM"] = str(provider.settings.get("stream", False))
                os.environ["OPENAI_TRANSCRIBE_LANGUAGE"] = provider.settings.get("language", "zh")
                # Respect OpenAI-only flag to omit response_format
                if not bool(provider.settings.get("openai_omit_response_format", False)):
                    os.environ["OPENAI_TRANSCRIBE_FORMAT"] = provider.settings.get("response_format", "text")
                else:
                    if "OPENAI_TRANSCRIBE_FORMAT" in os.environ:
                        del os.environ["OPENAI_TRANSCRIBE_FORMAT"]
                # Enforce a strict per-request timeout for availability tests
                os.environ["OPENAI_HTTP_TIMEOUT"] = "10"

                # Force-recreate persistent HTTP client so new timeout applies
                try:
                    from util.openai_transcribe_http import close_http_client
                    # If there's an existing client, close it before the request
                    await close_http_client(reason="availability-test-prepare")
                except Exception:
                    pass

                # Determine MIME type based on file extension
                mime_type = "audio/mpeg" if self.test_audio_path.suffix.lower() == ".mp3" else "audio/wav"

                # Call the transcription function with timeout
                text_result, status_code, t_submit, t_complete, transport_info = await asyncio.wait_for(
                    transcribe_audio(
                        payload_buf=self.test_audio_data,
                        payload_mime=mime_type,
                        task_id=f"test_{provider_id}_{int(time.time())}",
                        time_start=start_time,
                        record_stop=start_time + 0.1,  # Dummy record stop time
                        max_retries=1,  # Only one retry for testing
                        base_delay=0.2
                    ),
                    timeout=10.0  # Hard cap including networking and parsing
                )

            end_time = time.time()
            response_time_ms = int((end_time - start_time) * 1000)

            # Consider successful if we get a 2xx status code
            success = 200 <= status_code < 300

            result = TestResult(
                provider_id=provider_id,
                provider_name=provider.name,
                provider_type=provider.type,
                success=success,
                response_time_ms=response_time_ms,
                status_code=status_code,
                transcription=text_result if success else None,
                error_message=None if success else f"HTTP {status_code}"
            )

            if success:
                self._log_progress(f"✅ {provider.name}: {response_time_ms}ms")
            else:
                self._log_progress(f"❌ {provider.name}: HTTP {status_code}")

            return result

        except asyncio.TimeoutError:
            end_time = time.time()
            response_time_ms = int((end_time - start_time) * 1000)
            self._log_progress(f"⏰ {provider.name}: 超时 (10s)")

            return TestResult(
                provider_id=provider_id,
                provider_name=provider.name,
                provider_type=provider.type,
                success=False,
                response_time_ms=response_time_ms,
                status_code=None,
                transcription=None,
                error_message="Timeout after 10 seconds"
            )

        except Exception as e:
            end_time = time.time()
            response_time_ms = int((end_time - start_time) * 1000)
            self._log_progress(f"❌ {provider.name}: {str(e)[:50]}")

            return TestResult(
                provider_id=provider_id,
                provider_name=provider.name,
                provider_type=provider.type,
                success=False,
                response_time_ms=response_time_ms,
                status_code=None,
                transcription=None,
                error_message=str(e)
            )

        finally:
            # Close the client created with the short timeout so regular ops use default later
            try:
                from util.openai_transcribe_http import close_http_client
                await close_http_client(reason="availability-test-cleanup")
            except Exception:
                pass
            # Restore original environment variables
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    async def test_all_providers(self) -> List[TestResult]:
        """Test all OpenAI-compatible providers."""
        all_providers = provider_manager.list_providers()

        # Filter to only OpenAI-compatible providers
        openai_providers = [p for p in all_providers if p['type'] == 'openai']

        if not openai_providers:
            self._log_progress("未找到 OpenAI 兼容的转录服务商")
            return []

        self._log_progress(f"找到 {len(openai_providers)} 个 OpenAI 兼容服务商，开始测试...")

        # Test providers one by one to avoid overwhelming servers
        results = []
        for provider_info in openai_providers:
            result = await self.test_provider(provider_info['id'])
            results.append(result)

        return results

    def format_results(self, results: List[TestResult]) -> str:
        """Format test results for display."""
        if not results:
            return "未找到可测试的 OpenAI 兼容转录服务商"

        lines = []
        lines.append("=== OpenAI 兼容服务商可用性测试结果 ===")
        lines.append("")

        successful = 0
        for result in results:
            if result.error_message == "Skipped: Only OpenAI-compatible providers are tested":
                continue  # Don't show skipped providers in summary

            status = "✅ 成功" if result.success else "❌ 失败"
            lines.append(f"{status} {result.provider_name}")
            lines.append(f"    响应时间: {result.response_time_ms}ms")

            if result.success:
                successful += 1
                if result.status_code:
                    lines.append(f"    HTTP状态: {result.status_code}")
                if result.transcription:
                    # Limit transcription display to 50 characters
                    transcription = result.transcription[:50] + "..." if len(result.transcription) > 50 else result.transcription
                    lines.append(f"    转录结果: {transcription}")
            else:
                if result.error_message:
                    lines.append(f"    错误: {result.error_message}")
            lines.append("")

        tested_count = len([r for r in results if r.error_message != "Skipped: Only OpenAI-compatible providers are tested"])
        lines.append(f"测试完成: {successful}/{tested_count} 个 OpenAI 兼容服务商可用")
        return "\n".join(lines)


async def run_availability_test(test_audio_path: Path, progress_callback: Optional[Callable[[str], None]] = None) -> List[TestResult]:
    """Convenience function to run availability test."""
    tester = ProviderAvailabilityTester(test_audio_path, progress_callback)
    return await tester.test_all_providers()
