import asyncio
import io
import logging
import wave

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {"wav", "mp3", "ogg"}

_DEFAULT_FFMPEG_TIMEOUT = 30.0  # seconds; overridden by caller


async def pcm_to_encoded(
    pcm_data: bytes,
    sample_rate: int = 24000,
    channels: int = 1,
    output_format: str = "wav",
    ffmpeg_timeout: float = _DEFAULT_FFMPEG_TIMEOUT,
) -> bytes:
    """Encode raw PCM bytes to the requested audio format.

    Raises:
        ValueError: unsupported format or empty input.
        RuntimeError: FFmpeg not found or conversion failed.
        asyncio.TimeoutError: FFmpeg took longer than *ffmpeg_timeout* seconds.
    """
    fmt = output_format.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported output format: {output_format}")

    if not pcm_data:
        raise ValueError("pcm_data is empty; nothing to encode")

    # Use Python's built-in wave module for WAV (no FFmpeg dependency)
    if fmt == "wav":
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)  # 16-bit PCM
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_data)
        result = buf.getvalue()
        if not result:
            raise RuntimeError("WAV encoding produced empty output")
        return result

    # MP3 / OGG → requires FFmpeg
    codec_args: list[str] = []
    if fmt == "ogg":
        codec_args = ["-c:a", "libopus", "-b:a", "48k"]
    elif fmt == "mp3":
        codec_args = ["-c:a", "libmp3lame", "-b:a", "128k"]

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "s16le",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-i", "pipe:0",
        *codec_args,
        "-f", fmt,
        "pipe:1",
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=pcm_data),
                timeout=ffmpeg_timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            logger.error(
                "FFmpeg conversion timed out",
                extra={"format": fmt, "timeout_s": ffmpeg_timeout},
            )
            raise asyncio.TimeoutError(
                f"FFmpeg conversion to {fmt} exceeded {ffmpeg_timeout}s"
            )

        if process.returncode != 0:
            error_output = stderr.decode("utf-8", errors="ignore")
            logger.error(
                "FFmpeg conversion failed",
                extra={"return_code": process.returncode, "stderr": error_output},
            )
            raise RuntimeError(f"FFmpeg conversion failed: {error_output}")

        if not stdout:
            raise RuntimeError(
                f"FFmpeg returned no data for format {fmt}; input pcm_bytes={len(pcm_data)}"
            )

        return stdout

    except FileNotFoundError:
        logger.error("FFmpeg not found in system path. Cannot convert to non-WAV formats.")
        raise RuntimeError(
            f"Conversion to {fmt} failed: FFmpeg is not installed on the server."
        )
