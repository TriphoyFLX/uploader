"""Analyze beat files: parse metadata from filename or detect BPM/key from audio."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".aiff", ".aif", ".m4a", ".ogg"}


@dataclass
class BeatMetadata:
    artist: str
    beat_name: str
    title: str  # display title from titles array
    bpm: int | None
    key: str | None
    producers: list[str]  # from @mentions in filename
    audio_path: Path
    source: str  # "filename", "audio", "mutagen"


def format_producers(producers: list[str], fallback: str = "triphoy") -> str:
    """triphoy | triphoy x foo | triphoy x foo x bar"""
    names = producers or [fallback]
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} x {names[1]}"
    return " x ".join(names)


def parse_producers(stem: str) -> list[str]:
    """Extract @producer tags: '@triphoy @foo' -> ['triphoy', 'foo']"""
    return re.findall(r"@([\w.]+)", stem)


def format_key(key: str | None) -> str:
    """Fm -> F min, A#m -> A# min, Am -> A min"""
    if not key:
        return "N/A"
    k = key.strip()
    if k.lower().endswith(" min"):
        return k
    if k.lower().endswith("min") and not k.lower().endswith(" min"):
        return k[:-3].strip() + " min"
    if k.endswith("m") and len(k) > 1:
        return k[:-1] + " min"
    return k + " min"


def find_audio_files(beats_dir: Path) -> list[Path]:
    if not beats_dir.is_dir():
        return []
    return sorted(
        f for f in beats_dir.iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    )


def find_audio_file(beats_dir: Path) -> Path | None:
    files = find_audio_files(beats_dir)
    return files[0] if files else None


def parse_filename(audio_path: Path) -> tuple[str | None, int | None, str | None]:
    """
    Parse patterns like:
      che_140bpm_Fm.wav
      dark_150bpm_Am.wav
      beat_128_Bm.wav
    """
    stem = audio_path.stem

    match = re.match(
        r"^(?P<name>.+?)_(?P<bpm>\d{2,3})(?:bpm)?_(?P<key>[A-G][#b]?m?)$",
        stem,
        re.IGNORECASE,
    )
    if match:
        return (
            match.group("name"),
            int(match.group("bpm")),
            match.group("key"),
        )

    match = re.match(
        r"^(?P<name>.+?)_(?P<bpm>\d{2,3})bpm$",
        stem,
        re.IGNORECASE,
    )
    if match:
        return match.group("name"), int(match.group("bpm")), None

    # triphoy style: "450. 149Bpm F#minor @triphoy"
    match = re.search(
        r"(?P<bpm>\d{2,3})\s*[Bb]pm\s+(?P<key>[A-G][#b]?\s*(?:minor|major|min|maj|m)?)",
        stem,
        re.IGNORECASE,
    )
    if match:
        key = _normalize_key(match.group("key"))
        return stem.split(".", 1)[0].strip() or stem, int(match.group("bpm")), key

    return stem, None, None


def _normalize_key(raw: str) -> str:
    key = raw.strip()
    key = re.sub(r"\s+", "", key)
    key = re.sub(r"minor$", "m", key, flags=re.IGNORECASE)
    key = re.sub(r"min$", "m", key, flags=re.IGNORECASE)
    key = re.sub(r"major$", "", key, flags=re.IGNORECASE)
    key = re.sub(r"maj$", "", key, flags=re.IGNORECASE)
    if not key.endswith("m") and len(key) <= 3:
        key += "m"
    return key


def read_mutagen_tags(audio_path: Path) -> tuple[int | None, str | None]:
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return None, None

    audio = MutagenFile(audio_path)
    if audio is None:
        return None, None

    bpm = None
    key = None

    for tag_key in ("TBPM", "bpm", "BPM"):
        if tag_key in audio:
            try:
                bpm = int(float(str(audio[tag_key][0])))
                break
            except (ValueError, TypeError, IndexError):
                pass

    for tag_key in ("TKEY", "key", "initialkey"):
        if tag_key in audio:
            key = str(audio[tag_key][0])
            break

    return bpm, key


def analyze_audio(audio_path: Path) -> tuple[int | None, str | None]:
    try:
        import librosa
        import numpy as np
    except ImportError:
        return None, None

    y, sr = librosa.load(str(audio_path), sr=None, mono=True, duration=90)

    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = int(round(float(np.asarray(tempo).item())))

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    key_index = int(chroma.mean(axis=1).argmax())
    keys = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    key = keys[key_index]

    return bpm, key


def analyze_beat(
    artist: str,
    audio_path: Path,
    *,
    title: str | None = None,
    use_audio_analysis: bool = True,
) -> BeatMetadata:
    beat_name, bpm, key = parse_filename(audio_path)
    source = "filename"

    if bpm is None or key is None:
        tag_bpm, tag_key = read_mutagen_tags(audio_path)
        if bpm is None and tag_bpm is not None:
            bpm = tag_bpm
            source = "mutagen"
        if key is None and tag_key is not None:
            key = tag_key
            source = "mutagen"

    if use_audio_analysis and (bpm is None or key is None):
        audio_bpm, audio_key = analyze_audio(audio_path)
        if bpm is None and audio_bpm is not None:
            bpm = audio_bpm
            source = "audio"
        if key is None and audio_key is not None:
            key = audio_key
            source = "audio" if source == "filename" else source

    producers = parse_producers(audio_path.stem)

    return BeatMetadata(
        artist=artist,
        beat_name=beat_name or audio_path.stem,
        title=title or beat_name or audio_path.stem,
        bpm=bpm,
        key=key,
        producers=producers,
        audio_path=audio_path,
        source=source,
    )
