import asyncio
import logging

logger = logging.getLogger(__name__)


SUPPORTED_FORMATS = {"wav", "mp3", "ogg"}


async def pcm_to_encoded(
    pcm_data: bytes,
    sample_rate: int = 24000,
    channels: int = 1,
    output_format: str = "wav",
) -> bytes:
    fmt = output_format.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported output format: {output_format}")

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

    logger.debug("Running ffmpeg conversion", extra={"format": fmt, "sample_rate": sample_rate, "channels": channels})

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
