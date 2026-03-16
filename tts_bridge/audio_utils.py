import asyncio
import logging

logger = logging.getLogger(__name__)


SUPPORTED_FORMATS = {"wav", "mp3", "ogg"}


import io
import wave

async def pcm_to_encoded(
    pcm_data: bytes,
    sample_rate: int = 24000,
    channels: int = 1,
    output_format: str = "wav",
) -> bytes:
    fmt = output_format.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported output format: {output_format}")

    # Use Python's built-in wave module for WAV to avoid FFmpeg dependency
    if fmt == "wav":
        with io.BytesIO() as wav_io:
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(channels)
                wav_file.setsampwidth(2)  # 16-bit PCM
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(pcm_data)
            return wav_io.getvalue()

    # For MP3 and OGG, we still need FFmpeg
    codec_args: list[str] = []
    if fmt == "ogg":
        codec_args = ["-c:a", "libopus", "-b:a", "48k"]
    elif fmt == "mp3":
        codec_args = ["-c:a", "libmp3lame", "-b:a", "128k"]

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-i",
        "pipe:0",
        *codec_args,
        "-f",
        fmt,
        "pipe:1",
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(input=pcm_data)
        if process.returncode != 0:
            error_output = stderr.decode("utf-8", errors="ignore")
            logger.error("FFmpeg conversion failed", extra={"return_code": process.returncode, "stderr": error_output})
            raise RuntimeError(f"FFmpeg conversion failed: {error_output}")
        return stdout
    except FileNotFoundError:
        logger.error("FFmpeg not found in system path. Cannot convert to non-WAV formats.")
        raise RuntimeError(f"Conversion to {fmt} failed because FFmpeg is not installed on the server.")
