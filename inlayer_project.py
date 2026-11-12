# Inlayer - erzeugt 3D-druckbare Verpackungseinleger aus STL-Figuren.
# Copyright (C) 2026 Marco Wittwer, Mirko Wittwer
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License
# for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""Versioned, STL-free project recipes shared by the CLI and the web app."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Final, Sequence

from i18n import t
from inlayer import Config

FORMAT_NAME: Final = "inlayer-project"
PROJECT_VERSION: Final = 1
MAX_PROJECT_BYTES: Final = 2_000_000
_HASH_CHUNK_BYTES: Final = 1024 * 1024
_SHA256_RE: Final = re.compile(r"[0-9a-fA-F]{64}\Z")


class ProjectError(ValueError):
    """A project cannot be parsed, validated or matched to its sources."""


@dataclass(frozen=True)
class SourceIdentity:
    name: str
    sha256: str
    path: str | None = None


@dataclass(frozen=True)
class ProjectFigure:
    id: str
    source: SourceIdentity
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    finger_recess_position: float = 0.0


@dataclass(frozen=True)
class Project:
    config: Config
    figures: tuple[ProjectFigure, ...]
    version: int = PROJECT_VERSION


@dataclass(frozen=True)
class SourceCandidate:
    name: str
    sha256: str
    path: str | None = None


@dataclass(frozen=True)
class SourceMatch:
    figure: ProjectFigure
    candidate_index: int


@dataclass(frozen=True)
class ResolvedSource:
    figure: ProjectFigure
    path: str
    supplied_name: str


def bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _invalid(field: str) -> ProjectError:
    return ProjectError(t("project.invalid_field", field=field))


def _exact_keys(value: Any, expected: set[str], field: str) -> dict:
    if not isinstance(value, dict):
        raise _invalid(field)
    missing = sorted(expected - set(value))
    if missing:
        raise ProjectError(t("project.missing_field", field=f"{field}.{missing[0]}"))
    unknown = sorted(set(value) - expected)
    if unknown:
        raise _invalid(f"{field}.{unknown[0]}")
    return value


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()) or "\x00" in value:
        raise _invalid(field)
    if len(value) > 4096:
        raise _invalid(field)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(field) from exc
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(field)
    try:
        converted = float(value)
    except OverflowError as exc:
        raise _invalid(field) from exc
    if not math.isfinite(converted):
        raise _invalid(field)
    return converted


def _vector(value: Any, field: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise _invalid(field)
    x, y, z = (_number(component, f"{field}[{i}]") for i, component in enumerate(value))
    return x, y, z


def _hash(value: Any, field: str) -> str:
    text = _text(value, field)
    if _SHA256_RE.fullmatch(text) is None:
        raise _invalid(field)
    return text.lower()


def _config(raw: Any) -> Config:
    definitions = fields(Config)
    data = _exact_keys(raw, {field.name for field in definitions}, "config")
    optional = {"box_width", "box_depth", "box_height", "box_diameter", "figure_gap"}
    booleans = {"enable_finger_recesses", "enable_parallel"}
    strings = {"box_shape", "layout_style", "finger_recess_axis"}
    values: dict[str, Any] = {}
    for field in definitions:
        value = data[field.name]
        if field.name in optional and value is None:
            values[field.name] = None
        elif field.name in booleans:
            if not isinstance(value, bool):
                raise _invalid(f"config.{field.name}")
            values[field.name] = value
        elif field.name == "decimate_faces":
            if isinstance(value, bool) or not isinstance(value, int):
                raise _invalid(f"config.{field.name}")
            values[field.name] = value
        elif field.name in strings:
            values[field.name] = _text(value, f"config.{field.name}")
        else:
            values[field.name] = _number(value, f"config.{field.name}")
    try:
        return Config(**values)
    except ValueError as exc:
        raise ProjectError(str(exc)) from exc


def _figure(raw: Any, index: int) -> ProjectFigure:
    label = f"figures[{index}]"
    data = _exact_keys(
        raw, {"id", "source", "rotation", "offset", "finger_recess_position"}, label
    )
    figure_id = _text(data["id"], f"{label}.id")
    if len(figure_id) > 128:
        raise _invalid(f"{label}.id")
    source_data = _exact_keys(data["source"], {"name", "sha256", "path"}, f"{label}.source")
    source_path = source_data["path"]
    if source_path is not None:
        source_path = _text(source_path, f"{label}.source.path")
    source = SourceIdentity(
        name=_text(source_data["name"], f"{label}.source.name"),
        sha256=_hash(source_data["sha256"], f"{label}.source.sha256"),
        path=source_path,
    )
    return ProjectFigure(
        id=figure_id,
        source=source,
        rotation=_vector(data["rotation"], f"{label}.rotation"),
        offset=_vector(data["offset"], f"{label}.offset"),
        finger_recess_position=_number(
            data["finger_recess_position"], f"{label}.finger_recess_position"
        ),
    )


def project_from_dict(raw: Any) -> Project:
    data = _exact_keys(raw, {"format", "version", "config", "figures"}, "project")
    if data["format"] != FORMAT_NAME:
        raise ProjectError(t("project.unsupported_format", value=data["format"]))
    version = data["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise _invalid("version")
    if version != PROJECT_VERSION:
        raise ProjectError(t("project.unsupported_version", version=version))
    if not isinstance(data["figures"], list) or not data["figures"]:
        raise _invalid("figures")
    figures = tuple(_figure(raw_figure, i) for i, raw_figure in enumerate(data["figures"]))
    ids = [figure.id for figure in figures]
    if len(set(ids)) != len(ids):
        raise ProjectError(t("project.duplicate_id"))
    for i, figure in enumerate(figures):
        if not -1.0 <= figure.finger_recess_position <= 1.0:
            raise ProjectError(t("config.bad_finger_position", value=figure.finger_recess_position))
    return Project(config=_config(data["config"]), figures=figures, version=version)


def loads_project(data: str | bytes) -> Project:
    if isinstance(data, str):
        text = data
    else:
        if len(data) > MAX_PROJECT_BYTES:
            raise ProjectError(t("project.too_large", limit=MAX_PROJECT_BYTES))
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectError(t("project.invalid_json")) from exc
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ProjectError(t("project.invalid_json")) from exc
    if size > MAX_PROJECT_BYTES:
        raise ProjectError(t("project.too_large", limit=MAX_PROJECT_BYTES))
    try:
        raw = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise ProjectError(t("project.invalid_json")) from exc
    return project_from_dict(raw)


def load_project(path: str | os.PathLike[str]) -> Project:
    return loads_project(Path(path).read_bytes())


def project_to_dict(project: Project) -> dict[str, Any]:
    return {
        "format": FORMAT_NAME,
        "version": project.version,
        "config": {field.name: getattr(project.config, field.name) for field in fields(Config)},
        "figures": [
            {
                "id": figure.id,
                "source": {
                    "name": figure.source.name,
                    "sha256": figure.source.sha256,
                    "path": figure.source.path,
                },
                "rotation": list(figure.rotation),
                "offset": list(figure.offset),
                "finger_recess_position": figure.finger_recess_position,
            }
            for figure in project.figures
        ],
    }


def dumps_project(project: Project) -> str:
    return json.dumps(project_to_dict(project), ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def save_project(project: Project, path: str | os.PathLike[str]) -> None:
    Path(path).write_text(dumps_project(project), encoding="utf-8")


def new_project(
    config: Config,
    sources: Sequence[SourceIdentity],
    *,
    rotations: Sequence[Sequence[float]] | None = None,
    offsets: Sequence[Sequence[float]] | None = None,
    recess_positions: Sequence[float] | None = None,
    figure_ids: Sequence[str] | None = None,
    project_path: str | os.PathLike[str] | None = None,
) -> Project:
    count = len(sources)
    if count == 0:
        raise _invalid("figures")
    if rotations is None:
        rotations = [(0.0, 0.0, 0.0)] * count
    if offsets is None:
        offsets = [(0.0, 0.0, 0.0)] * count
    if recess_positions is None:
        recess_positions = [config.finger_recess_position] * count
    if figure_ids is None:
        figure_ids = [str(uuid.uuid4()) for _ in sources]
    if not all(len(values) == count for values in (rotations, offsets, recess_positions, figure_ids)):
        raise _invalid("figures")

    project_dir = None if project_path is None else os.path.dirname(os.path.abspath(project_path))
    figures = []
    for i, source in enumerate(sources):
        path = source.path
        if path is not None and project_dir is not None:
            path = os.path.relpath(os.path.abspath(path), start=project_dir)
        figure = ProjectFigure(
            id=_text(figure_ids[i], f"figures[{i}].id"),
            source=SourceIdentity(
                name=_text(source.name, f"figures[{i}].source.name"),
                sha256=_hash(source.sha256, f"figures[{i}].source.sha256"),
                path=path,
            ),
            rotation=_vector(list(rotations[i]), f"figures[{i}].rotation"),
            offset=_vector(list(offsets[i]), f"figures[{i}].offset"),
            finger_recess_position=_number(
                recess_positions[i], f"figures[{i}].finger_recess_position"
            ),
        )
        figures.append(figure)
    project = Project(config=config, figures=tuple(figures))
    project_from_dict(project_to_dict(project))
    return project


def match_project_sources(project: Project, candidates: Sequence[SourceCandidate]) -> list[SourceMatch]:
    """Matches by full content hash, never by a name or a transient upload id."""
    by_hash: dict[str, list[int]] = {}
    for i, candidate in enumerate(candidates):
        by_hash.setdefault(candidate.sha256.lower(), []).append(i)
    used: set[int] = set()
    matches = []
    for figure in project.figures:
        available = [i for i in by_hash.get(figure.source.sha256, []) if i not in used]
        if not available:
            raise ProjectError(t(
                "project.source_not_provided", name=figure.source.name,
                hash=figure.source.sha256[:12],
            ))
        index = available[0]
        used.add(index)
        matches.append(SourceMatch(figure=figure, candidate_index=index))
    unmatched = [candidate for i, candidate in enumerate(candidates) if i not in used]
    if unmatched:
        raise ProjectError(t("project.source_unmatched", name=unmatched[0].name))
    return matches


def resolve_project_paths(
    project: Project,
    input_paths: Sequence[str] | None,
    *,
    project_path: str | os.PathLike[str],
) -> list[ResolvedSource]:
    """Checks every source before the pipeline starts and returns project order."""
    if input_paths is None:
        candidates = []
        project_dir = os.path.dirname(os.path.abspath(project_path))
        for figure in project.figures:
            if figure.source.path is None:
                raise ProjectError(t(
                    "project.source_not_provided", name=figure.source.name,
                    hash=figure.source.sha256[:12],
                ))
            path = figure.source.path
            if not os.path.isabs(path):
                path = os.path.join(project_dir, path)
            path = os.path.normpath(path)
            if not os.path.isfile(path):
                raise ProjectError(t(
                    "project.source_missing", name=figure.source.name, path=path,
                ))
            actual_hash = file_hash(path)
            if actual_hash != figure.source.sha256:
                raise ProjectError(t(
                    "project.source_changed", name=figure.source.name, path=path,
                ))
            candidates.append(SourceCandidate(name=figure.source.name, sha256=actual_hash, path=path))
    else:
        candidates = []
        for supplied in input_paths:
            if not os.path.isfile(supplied):
                raise ProjectError(t("error.input_not_found", path=supplied))
            candidates.append(SourceCandidate(
                name=os.path.basename(supplied), sha256=file_hash(supplied), path=supplied,
            ))
    matches = match_project_sources(project, candidates)
    return [
        ResolvedSource(
            figure=match.figure,
            path=candidates[match.candidate_index].path or "",
            supplied_name=candidates[match.candidate_index].name,
        )
        for match in matches
    ]


def effective_offsets(project: Project) -> list[tuple[float, float, float]]:
    config = project.config
    return [
        (config.offset_x + figure.offset[0], config.offset_y + figure.offset[1],
         config.offset_z + figure.offset[2])
        for figure in project.figures
    ]
