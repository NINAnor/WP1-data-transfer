"""Cut short clips out of long mp3 files while reading only the bytes needed.

The recorders write VBR mp3 with a Xing header giving the exact frame count
(hence duration) and audio byte count. Field recordings have a fairly even
bitrate, so time maps to bytes almost linearly; this measured more accurately
(~0.1 s after bias correction) than the Xing table of contents, whose offsets
are quantised to 1/256 of the file (~1.2 s). We request a byte range a little
wider than the clip and let ffmpeg trim relative to the start of that range.
"""

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

HEAD_BYTES = 16_384
# Decoding from mid-stream starts ~0.3 s early relative to the linear estimate
# (measured on TABMON recordings); compensate when trimming.
SYNC_BIAS_S = 0.3
CLIP_VERSION = "v3"

# MPEG-1 Layer III bitrates (kbps) and sample rates, indexed by header fields.
_BITRATES = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
_SAMPLE_RATES = {
    3: [44100, 48000, 32000],
    2: [22050, 24000, 16000],
    0: [11025, 12000, 8000],
}

# read_range(start, end_inclusive) -> (bytes, total_file_size)
RangeReader = Callable[[int, int], tuple[bytes, int]]


@dataclass
class Mp3Layout:
    audio_start: int
    total_size: int
    duration: float
    audio_bytes: int

    def byte_at(self, t: float) -> int:
        """Approximate byte offset of time t (seconds), assuming an even bitrate."""
        t = min(max(t, 0.0), self.duration)
        return self.audio_start + int(t / self.duration * self.audio_bytes)


def parse_layout(head: bytes, total_size: int) -> Mp3Layout:
    i = 0
    if head[:3] == b"ID3":
        size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
        i = 10 + size
    # Find the first frame sync.
    while i + 4 <= len(head) and not (head[i] == 0xFF and head[i + 1] & 0xE0 == 0xE0):
        i += 1
    if i + 4 > len(head):
        raise ValueError("no mp3 frame found in header")
    h = head[i : i + 4]
    version = (h[1] >> 3) & 0x3
    bitrate = _BITRATES[(h[2] >> 4) & 0xF] * 1000
    sample_rate = _SAMPLE_RATES.get(version, _SAMPLE_RATES[3])[(h[2] >> 2) & 0x3]
    samples_per_frame = 1152 if version == 3 else 576

    frames = None
    audio_bytes = total_size - i
    for tag in (b"Xing", b"Info"):
        j = head.find(tag, i, i + 200)
        if j < 0:
            continue
        flags = int.from_bytes(head[j + 4 : j + 8], "big")
        k = j + 8
        if flags & 1:
            frames = int.from_bytes(head[k : k + 4], "big")
            k += 4
        if flags & 2:
            audio_bytes = int.from_bytes(head[k : k + 4], "big")
            k += 4
        break

    if frames:
        duration = frames * samples_per_frame / sample_rate
    elif bitrate:
        duration = audio_bytes * 8 / bitrate
    else:
        raise ValueError("cannot determine mp3 duration")
    if duration <= 0 or audio_bytes <= 0:
        raise ValueError("empty or truncated mp3")
    return Mp3Layout(i, total_size, duration, audio_bytes)


def clip_window(
    start_s: float, context_s: float, detection_s: float
) -> tuple[float, float]:
    """(clip start, clip length) around a detection window."""
    begin = max(0.0, start_s - context_s)
    return begin, (start_s - begin) + detection_s + context_s


def cut_clip(
    read_range: RangeReader,
    start_s: float,
    context_s: float = 1.0,
    detection_s: float = 3.0,
    bitrate: str = "96k",
) -> bytes:
    """Return an mp3 clip of the detection with context on both sides."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is not installed")

    head, total = read_range(0, HEAD_BYTES - 1)
    layout = parse_layout(head, total)
    clip_start, clip_len = clip_window(start_s, context_s, detection_s)

    # Start a little before the clip so the decoder can sync, end a little after.
    chunk_t0 = max(0.0, clip_start - 1.0)
    chunk_b0 = layout.byte_at(chunk_t0) if chunk_t0 > 0 else 0
    chunk_b1 = min(total - 1, layout.byte_at(clip_start + clip_len + 4.0))
    body, _ = read_range(chunk_b0, chunk_b1)

    proc = subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
            "-ss",
            f"{clip_start - chunk_t0 + (SYNC_BIAS_S if chunk_b0 else 0.0):.3f}",
            "-t",
            f"{clip_len:.3f}",
            "-ac",
            "1",
            "-b:a",
            bitrate,
            "-f",
            "mp3",
            "pipe:1",
        ],
        input=body,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode()[-300:]}")
    return proc.stdout
