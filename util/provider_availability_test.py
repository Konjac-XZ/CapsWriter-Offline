"""
Provider availability testing module.
Tests all configured providers by sending a test audio file and checking for successful HTTP responses.
"""

import asyncio
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
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
    """Tests availability of all configured transcription providers."""

    def __init__(self, test_audio_path: Path):
        self.test_audio_path = test_audio_path
        self.test_audio_data: Optional[bytes] = None
        self.load_test_audio()

    def load_test_audio(self) -> None:
        """Load the test audio file into memory."""
        if not self.test_audio_path.exists():
            raise FileNotFoundError(f"Test audio file not found: {self.test_audio_path}")

        with open(self.test_audio_path, 'rb') as f:
            self.test_audio_data = f.read()

    async def test_provider(self, provider_id: str) -> TestResult:
        """Test a single provider."""
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

        # Save original environment state to restore later
        import os
        original_env = {}
        provider_env_keys = [
            "TRANSCRIBE_PROVIDER", "OPENAI_API_KEY", "OPENAI_BASE_URL", "TRANSCRIBE_MODEL",
            "TRANSCRIBE_TEMPERATURE", "OPENAI_TRANSCRIBE_STREAM", "OPENAI_TRANSCRIBE_LANGUAGE",
            "OPENAI_TRANSCRIBE_FORMAT", "REPLICATE_API_TOKEN", "ELEVENLABS_API_KEY",
            "ELEVENLABS_LANGUAGE_CODE", "SONIOX_API_KEY", "SONIOX_MODEL", "SONIOX_LANGUAGE_HINTS"
        ]

        for key in provider_env_keys:
            original_env[key] = os.environ.get(key)

        start_time = time.time()

        try:
            # Set environment variables for this provider
            os.environ["TRANSCRIBE_PROVIDER"] = provider.type

            if provider.type == "openai":
                os.environ["OPENAI_API_KEY"] = provider.settings.get("api_key", "")
                os.environ["OPENAI_BASE_URL"] = provider.settings.get("base_url", "")
                os.environ["TRANSCRIBE_MODEL"] = provider.settings.get("model", "")
                os.environ["TRANSCRIBE_TEMPERATURE"] = str(provider.settings.get("temperature", 0.2))
                os.environ["OPENAI_TRANSCRIBE_STREAM"] = str(provider.settings.get("stream", False))
                os.environ["OPENAI_TRANSCRIBE_LANGUAGE"] = provider.settings.get("language", "zh")
                os.environ["OPENAI_TRANSCRIBE_FORMAT"] = provider.settings.get("response_format", "text")

            elif provider.type == "replicate":
                os.environ["REPLICATE_API_TOKEN"] = provider.settings.get("api_token", "")
                os.environ["OPENAI_TRANSCRIBE_LANGUAGE"] = provider.settings.get("language", "zh")
                os.environ["TRANSCRIBE_TEMPERATURE"] = str(provider.settings.get("temperature", 0.2))
                os.environ["OPENAI_TRANSCRIBE_STREAM"] = str(provider.settings.get("stream", False))

            elif provider.type == "elevenlabs":
                os.environ["ELEVENLABS_API_KEY"] = provider.settings.get("api_key", "")
                os.environ["ELEVENLABS_LANGUAGE_CODE"] = provider.settings.get("language_code", "zh")

            elif provider.type == "soniox":
                os.environ["SONIOX_API_KEY"] = provider.settings.get("api_key", "")
                os.environ["SONIOX_MODEL"] = provider.settings.get("model", "stt-async-preview-v1")
                os.environ["SONIOX_LANGUAGE_HINTS"] = provider.settings.get("language_hints", "zh, en")

            # Determine MIME type based on file extension
            mime_type = "audio/mpeg" if self.test_audio_path.suffix.lower() == ".mp3" else "audio/wav"

            # Call the transcription function
            text_result, status_code, t_submit, t_complete, transport_info = await transcribe_audio(
                payload_buf=self.test_audio_data,
                payload_mime=mime_type,
                task_id=f"test_{provider_id}_{int(time.time())}",
                time_start=start_time,
                record_stop=start_time + 0.1,  # Dummy record stop time
                max_retries=1,  # Only one retry for testing
                base_delay=1.0
            )

            end_time = time.time()
            response_time_ms = int((end_time - start_time) * 1000)

            # Consider successful if we get a 2xx status code
            success = 200 <= status_code < 300

            return TestResult(
                provider_id=provider_id,
                provider_name=provider.name,
                provider_type=provider.type,
                success=success,
                response_time_ms=response_time_ms,
                status_code=status_code,
                transcription=text_result if success else None,
                error_message=None if success else f"HTTP {status_code}"
            )

        except Exception as e:
            end_time = time.time()
            response_time_ms = int((end_time - start_time) * 1000)

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
            # Restore original environment variables
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    async def test_all_providers(self) -> List[TestResult]:
        """Test all available providers concurrently."""
        providers = provider_manager.list_providers()

        if not providers:
            return []

        # Create tasks for all providers
        tasks = []
        for provider_info in providers:
            task = self.test_provider(provider_info['id'])
            tasks.append(task)

        # Run all tests concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to failed test results
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                provider_info = providers[i]
                final_results.append(TestResult(
                    provider_id=provider_info['id'],
                    provider_name=provider_info['name'],
                    provider_type=provider_info['type'],
                    success=False,
                    response_time_ms=0,
                    status_code=None,
                    transcription=None,
                    error_message=f"Test failed: {str(result)}"
                ))
            else:
                final_results.append(result)

        return final_results

    def format_results(self, results: List[TestResult]) -> str:
        """Format test results for display."""
        if not results:
            return "未找到可测试的转录服务商"

        lines = []
        lines.append("=== 转录服务商可用性测试结果 ===")
        lines.append("")

        successful = 0
        for result in results:
            status = "✅ 成功" if result.success else "❌ 失败"
            lines.append(f"{status} {result.provider_name} ({result.provider_type})")
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

        lines.append(f"测试完成: {successful}/{len(results)} 个服务商可用")
        return "\n".join(lines)


async def run_availability_test(test_audio_path: Path) -> List[TestResult]:
    """Convenience function to run availability test."""
    tester = ProviderAvailabilityTester(test_audio_path)
    return await tester.test_all_providers()