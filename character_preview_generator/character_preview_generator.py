#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps


DEFAULT_PREVIEW_SIZE = 512
DEFAULT_PADDING = 24
PREVIEW_FILE_NAME = "preview.png"
RASTER_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
COMPONENT_ANALYSIS_LIMIT = 256
ALPHA_THRESHOLD = 8


@dataclass(frozen=True)
class CharacterAssetEntry:
    character_id: str
    kind: str
    root_dir: Path


def _resolve_asset_path(app_root: Path, raw: str, base_subdir: str) -> Path:
    value = str(raw or "").strip()
    if not value:
        return Path()
    path = Path(value)
    if path.is_absolute():
        return path
    if value.startswith("assets/"):
        return app_root / value
    return app_root / base_subdir / value


def _parse_character_entry(chr_path: Path, app_root: Path) -> CharacterAssetEntry | None:
    kind = ""
    root_dir = Path()
    for raw in chr_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("gui skin "):
            value = line[len("gui skin ") :].strip()
            root_dir = _resolve_asset_path(app_root, value, "assets/skins")
            kind = "skin"
            break
        if line.startswith("gui live2d "):
            value = line[len("gui live2d ") :].strip()
            resolved = _resolve_asset_path(app_root, value, "assets/live2d")
            root_dir = resolved if resolved.is_dir() else resolved.parent
            kind = "live2d"
            break
        if line.startswith("gui spine "):
            value = line[len("gui spine ") :].strip()
            resolved = _resolve_asset_path(app_root, value, "assets/spine")
            root_dir = resolved if resolved.is_dir() else resolved.parent
            kind = "spine"
            break
    if not kind or not root_dir:
        return None
    return CharacterAssetEntry(
        character_id=chr_path.stem.lower(),
        kind=kind,
        root_dir=root_dir,
    )


def _pick_skin_source(root_dir: Path) -> Path | None:
    preferred_emotions = (
        "normal",
        "idle",
        "default",
        "wait",
        "smile",
        "happy",
    )
    for emotion in preferred_emotions:
        emotion_dir = root_dir / emotion
        if not emotion_dir.is_dir():
            continue
        files = _sorted_raster_files(emotion_dir.iterdir())
        if not files:
            continue
        numbered = [path for path in files if path.stem == "001"]
        return numbered[0] if numbered else files[0]
    for child in sorted(root_dir.iterdir(), key=lambda item: item.name.lower()):
        if child.is_dir():
            files = _sorted_raster_files(child.iterdir())
            if files:
                return files[0]
        elif _is_raster_file(child) and child.name.lower() != PREVIEW_FILE_NAME:
            return child
    return None


def _pick_spine_source(root_dir: Path) -> Path | None:
    candidates = _sorted_raster_files(
        child
        for child in root_dir.iterdir()
        if child.is_file()
        and child.name.lower() != PREVIEW_FILE_NAME
        and not child.name.startswith("__minachan_runtime_")
    )
    if not candidates:
        return None
    return _pick_best_visual_candidate(candidates)


def _pick_live2d_source(root_dir: Path) -> Path | None:
    preferred = []
    for name in ("icon.png", "icon.jpg", "icon.jpeg", "icon.webp"):
        path = root_dir / name
        if path.is_file():
            preferred.append(path)
    for child in sorted(root_dir.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_file():
            continue
        lower = child.name.lower()
        if lower == PREVIEW_FILE_NAME:
            continue
        if lower.startswith("ico_") and lower.endswith(RASTER_SUFFIXES):
            preferred.append(child)
    if preferred:
        return _pick_best_visual_candidate(preferred)

    model_files = sorted(
        [
            child
            for child in root_dir.iterdir()
            if child.is_file()
            and child.suffix.lower() == ".json"
            and child.name.lower().endswith((".model3.json", ".model.json"))
            and "__minachan_runtime_" not in child.name.lower()
        ],
        key=lambda item: item.name.lower(),
    )
    texture_candidates: list[Path] = []
    for model_file in model_files:
        texture_candidates.extend(_discover_live2d_textures(model_file))
    texture_candidates = [path for path in texture_candidates if path.is_file()]
    if texture_candidates:
        return _pick_best_visual_candidate(texture_candidates)

    fallback = _sorted_raster_files(
        child
        for child in root_dir.rglob("*")
        if child.is_file()
        and child.name.lower() != PREVIEW_FILE_NAME
    )
    if not fallback:
        return None
    return _pick_best_visual_candidate(fallback)


def _discover_live2d_textures(model_file: Path) -> list[Path]:
    try:
        decoded = json.loads(model_file.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    textures: list[str] = []
    if isinstance(decoded, dict):
        file_references = decoded.get("FileReferences")
        if isinstance(file_references, dict):
            raw_textures = file_references.get("Textures")
            if isinstance(raw_textures, list):
                textures.extend(str(item).strip() for item in raw_textures if str(item).strip())
        raw_textures = decoded.get("textures")
        if isinstance(raw_textures, list):
            textures.extend(str(item).strip() for item in raw_textures if str(item).strip())
    out: list[Path] = []
    for raw in textures:
        candidate = model_file.parent / raw.replace("\\", "/")
        if candidate.is_file():
            out.append(candidate)
    return out


def _pick_source(entry: CharacterAssetEntry) -> Path | None:
    if not entry.root_dir.is_dir():
        return None
    if entry.kind == "skin":
        return _pick_skin_source(entry.root_dir)
    if entry.kind == "spine":
        return _pick_spine_source(entry.root_dir)
    if entry.kind == "live2d":
        return _pick_live2d_source(entry.root_dir)
    return None


def collect_character_asset_entries(
    app_root: Path,
    *,
    character_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    characters_root = app_root / "assets" / "characters"
    selected = {item.strip().lower() for item in (character_ids or []) if item.strip()}
    entries_by_root: dict[Path, CharacterAssetEntry] = {}
    covered_characters = 0
    skipped_no_binding = 0

    for chr_path in sorted(
        characters_root.glob("*.chr"),
        key=lambda item: _natural_sort_key(item),
    ):
        character_id = chr_path.stem.lower()
        if selected and character_id not in selected:
            continue
        entry = _parse_character_entry(chr_path, app_root)
        if entry is None:
            skipped_no_binding += 1
            continue
        covered_characters += 1
        entries_by_root.setdefault(entry.root_dir.resolve(), entry)

    entries = [
        entry
        for _, entry in sorted(
            entries_by_root.items(),
            key=lambda item: item[0].as_posix().lower(),
        )
    ]
    return {
        "entries": entries,
        "charactersCovered": covered_characters,
        "uniqueRoots": len(entries),
        "skippedNoBinding": skipped_no_binding,
    }


def _is_raster_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in RASTER_SUFFIXES


def _natural_sort_key(path: Path) -> tuple[tuple[int, int | str], ...]:
    import re

    parts = re.split(r"(\d+)", path.name.casefold())
    out: list[tuple[int, int | str]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            out.append((0, int(part)))
        else:
            out.append((1, part))
    return tuple(out)


def _sorted_raster_files(items: Iterable[Path]) -> list[Path]:
    files = [path for path in items if _is_raster_file(path)]
    files.sort(key=_natural_sort_key)
    return files


def _pick_best_visual_candidate(candidates: Sequence[Path]) -> Path:
    best = candidates[0]
    best_score = -1.0
    for candidate in candidates:
        score = _visual_score(candidate)
        if score > best_score:
            best = candidate
            best_score = score
    return best


def _visual_score(path: Path) -> float:
    try:
        with Image.open(path) as image:
            prepared = ImageOps.exif_transpose(image).convert("RGBA")
            alpha = prepared.getchannel("A")
            bbox = alpha.point(lambda value: 255 if value > ALPHA_THRESHOLD else 0).getbbox()
            if bbox is None:
                return float(prepared.width * prepared.height)
            cropped = prepared.crop(bbox)
            return float(_opaque_pixels(cropped))
    except Exception:
        return -1.0


def _opaque_pixels(image: Image.Image) -> int:
    alpha = image.getchannel("A")
    return sum(1 for value in alpha.getdata() if value > ALPHA_THRESHOLD)


def _largest_component_bbox(alpha_mask: Image.Image) -> tuple[int, int, int, int] | None:
    width = alpha_mask.width
    height = alpha_mask.height
    if width <= 0 or height <= 0:
        return None
    scale = max(width / COMPONENT_ANALYSIS_LIMIT, height / COMPONENT_ANALYSIS_LIMIT, 1.0)
    if scale > 1.0:
        scaled_width = max(1, int(math.ceil(width / scale)))
        scaled_height = max(1, int(math.ceil(height / scale)))
        small = alpha_mask.resize((scaled_width, scaled_height), Image.Resampling.NEAREST)
    else:
        small = alpha_mask
        scaled_width = width
        scaled_height = height

    pixels = small.load()
    visited = [[False] * scaled_width for _ in range(scaled_height)]
    best_count = 0
    best_box: tuple[int, int, int, int] | None = None

    for y in range(scaled_height):
        for x in range(scaled_width):
            if visited[y][x] or pixels[x, y] <= ALPHA_THRESHOLD:
                continue
            stack = [(x, y)]
            visited[y][x] = True
            count = 0
            min_x = max_x = x
            min_y = max_y = y
            while stack:
                current_x, current_y = stack.pop()
                count += 1
                if current_x < min_x:
                    min_x = current_x
                if current_x > max_x:
                    max_x = current_x
                if current_y < min_y:
                    min_y = current_y
                if current_y > max_y:
                    max_y = current_y
                for next_x, next_y in (
                    (current_x - 1, current_y),
                    (current_x + 1, current_y),
                    (current_x, current_y - 1),
                    (current_x, current_y + 1),
                ):
                    if next_x < 0 or next_y < 0 or next_x >= scaled_width or next_y >= scaled_height:
                        continue
                    if visited[next_y][next_x] or pixels[next_x, next_y] <= ALPHA_THRESHOLD:
                        continue
                    visited[next_y][next_x] = True
                    stack.append((next_x, next_y))
            if count > best_count:
                best_count = count
                best_box = (min_x, min_y, max_x + 1, max_y + 1)

    if best_box is None:
        return None
    left = max(0, int(math.floor(best_box[0] * scale)))
    top = max(0, int(math.floor(best_box[1] * scale)))
    right = min(width, int(math.ceil(best_box[2] * scale)))
    bottom = min(height, int(math.ceil(best_box[3] * scale)))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _expanded_box(box: tuple[int, int, int, int], image_size: tuple[int, int], ratio: float) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    width = right - left
    height = bottom - top
    expand_x = max(6, int(width * ratio))
    expand_y = max(6, int(height * ratio))
    max_width, max_height = image_size
    return (
        max(0, left - expand_x),
        max(0, top - expand_y),
        min(max_width, right + expand_x),
        min(max_height, bottom + expand_y),
    )


def _detect_crop_box(image: Image.Image) -> tuple[tuple[int, int, int, int], bool]:
    prepared = ImageOps.exif_transpose(image).convert("RGBA")
    alpha = prepared.getchannel("A")
    thresholded = alpha.point(lambda value: 255 if value > ALPHA_THRESHOLD else 0)
    full_box = thresholded.getbbox()
    if full_box is None:
        return (0, 0, prepared.width, prepared.height), False

    full_box = _expanded_box(full_box, (prepared.width, prepared.height), 0.04)
    alpha_crop = thresholded.crop(full_box)
    component_box = _largest_component_bbox(alpha_crop)
    if component_box is None:
        return full_box, True

    component_box = (
        full_box[0] + component_box[0],
        full_box[1] + component_box[1],
        full_box[0] + component_box[2],
        full_box[1] + component_box[3],
    )
    full_area = max(1, (full_box[2] - full_box[0]) * (full_box[3] - full_box[1]))
    component_area = max(
        1, (component_box[2] - component_box[0]) * (component_box[3] - component_box[1])
    )
    if component_area < int(full_area * 0.62):
        return _expanded_box(component_box, (prepared.width, prepared.height), 0.18), True
    return full_box, True


def render_preview(
    source_path: Path,
    output_path: Path,
    *,
    size: int = DEFAULT_PREVIEW_SIZE,
    padding: int = DEFAULT_PADDING,
) -> None:
    with Image.open(source_path) as source:
        prepared = ImageOps.exif_transpose(source).convert("RGBA")
        crop_box, uses_alpha = _detect_crop_box(prepared)
        cropped = prepared.crop(crop_box)
        available = max(8, size - padding * 2)
        scale = min(available / cropped.width, available / cropped.height)
        target_width = max(1, int(round(cropped.width * scale)))
        target_height = max(1, int(round(cropped.height * scale)))
        resized = cropped.resize((target_width, target_height), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset_x = (size - target_width) // 2
    if uses_alpha:
        offset_y = size - padding - target_height
    else:
        offset_y = (size - target_height) // 2
    offset_y = max(padding, min(offset_y, size - padding - target_height))
    canvas.alpha_composite(resized, (offset_x, offset_y))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True, compress_level=9)


def render_placeholder_preview(
    output_path: Path,
    *,
    character_id: str,
    kind: str,
    size: int = DEFAULT_PREVIEW_SIZE,
) -> None:
    palette = {
        "skin": ((214, 156, 74, 255), (83, 52, 13, 255)),
        "live2d": ((58, 158, 168, 255), (13, 62, 68, 255)),
        "spine": ((204, 94, 94, 255), (79, 24, 24, 255)),
    }
    primary, accent = palette.get(kind, ((120, 120, 120, 255), (36, 36, 36, 255)))
    canvas = Image.new("RGBA", (size, size), primary)
    draw = ImageDraw.Draw(canvas)
    step = max(18, size // 14)
    for offset in range(-size, size * 2, step):
        draw.line((offset, 0, offset - size, size), fill=accent, width=max(2, size // 96))
    inset = max(12, size // 18)
    draw.rectangle(
        (inset, inset, size - inset, size - inset),
        outline=(255, 255, 255, 180),
        width=max(2, size // 96),
    )

    font = ImageFont.load_default()
    kind_label = kind.upper()
    character_label = character_id.replace("_", " ").upper()
    kind_box = draw.textbbox((0, 0), kind_label, font=font)
    char_box = draw.textbbox((0, 0), character_label, font=font)
    kind_width = kind_box[2] - kind_box[0]
    char_width = char_box[2] - char_box[0]
    draw.text(
        ((size - kind_width) // 2, size // 2 - 18),
        kind_label,
        fill=(255, 255, 255, 235),
        font=font,
    )
    draw.text(
        ((size - char_width) // 2, size // 2 + 4),
        character_label,
        fill=(255, 255, 255, 235),
        font=font,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True, compress_level=9)


def generate_preview_for_entry(
    entry: CharacterAssetEntry,
    *,
    size: int = DEFAULT_PREVIEW_SIZE,
    padding: int = DEFAULT_PADDING,
) -> dict[str, Any]:
    output_path = entry.root_dir / PREVIEW_FILE_NAME
    source_path = _pick_source(entry)
    if source_path is None or not source_path.is_file():
        render_placeholder_preview(
            output_path,
            character_id=entry.character_id,
            kind=entry.kind,
            size=size,
        )
        return {
            "ok": True,
            "generated": True,
            "placeholder": True,
            "outputPath": output_path,
            "warning": (
                f"{entry.character_id}: missing source image in {entry.root_dir}; "
                "generated placeholder preview"
            ),
        }

    render_preview(source_path, output_path, size=size, padding=padding)
    return {
        "ok": True,
        "generated": True,
        "placeholder": False,
        "outputPath": output_path,
        "sourcePath": source_path,
    }


def generate_character_previews(
    app_root: Path,
    *,
    force: bool = False,
    size: int = DEFAULT_PREVIEW_SIZE,
    padding: int = DEFAULT_PADDING,
    character_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    discovery = collect_character_asset_entries(
        app_root,
        character_ids=character_ids,
    )
    entries = discovery["entries"]
    covered_characters = int(discovery["charactersCovered"])
    skipped_no_binding = int(discovery["skippedNoBinding"])
    warnings: list[str] = []

    generated = 0
    skipped_existing = 0
    skipped_missing_source = 0
    generated_placeholders = 0
    generated_by_kind = {"skin": 0, "live2d": 0, "spine": 0}
    for entry in entries:
        root_dir = entry.root_dir.resolve()
        output_path = root_dir / PREVIEW_FILE_NAME
        if output_path.exists() and not force:
            skipped_existing += 1
            continue
        try:
            single_result = generate_preview_for_entry(
                entry,
                size=size,
                padding=padding,
            )
            generated += 1
            generated_by_kind[entry.kind] = generated_by_kind.get(entry.kind, 0) + 1
            if bool(single_result.get("placeholder")):
                generated_placeholders += 1
            warning = str(single_result.get("warning") or "").strip()
            if warning:
                warnings.append(warning)
        except Exception as error:
            skipped_missing_source += 1
            warnings.append(f"{entry.character_id}: preview generation failed: {error}")

    return {
        "charactersCovered": covered_characters,
        "uniqueRoots": len(entries),
        "generated": generated,
        "generatedPlaceholders": generated_placeholders,
        "generatedByKind": generated_by_kind,
        "skippedExisting": skipped_existing,
        "skippedMissingSource": skipped_missing_source,
        "skippedNoBinding": skipped_no_binding,
        "size": size,
        "padding": padding,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate normalized preview.png files for MinaChan character assets.")
    parser.add_argument(
        "--app-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing preview.png files.",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=DEFAULT_PREVIEW_SIZE,
        help="Square preview size in pixels.",
    )
    parser.add_argument(
        "--padding",
        type=int,
        default=DEFAULT_PADDING,
        help="Transparent padding inside the generated square preview.",
    )
    parser.add_argument(
        "--character",
        dest="characters",
        action="append",
        default=[],
        help="Optional character id filter. Can be passed multiple times.",
    )
    args = parser.parse_args()

    result = generate_character_previews(
        args.app_root.resolve(),
        force=bool(args.force),
        size=max(64, int(args.size)),
        padding=max(0, int(args.padding)),
        character_ids=args.characters,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
