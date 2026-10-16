"""Lossless generic evidence views; no source-specific field interpretation."""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from .contracts import CaptureInput, EvidenceRef, WorkPackage

MAX_PAGE_BYTES = 16_384


def _ref_page(refs: list[dict], offset: int, limit: int) -> dict:
    page = []
    for ref in refs[offset : offset + limit]:
        candidate = {
            "refs": page + [ref],
            "next_offset": offset + len(page) + 1,
            "total": len(refs),
        }
        if len(json.dumps(candidate, ensure_ascii=False).encode()) > MAX_PAGE_BYTES:
            if not page:
                raise ValueError("single reference exceeds page limit")
            break
        page.append(ref)
    end = offset + len(page)
    return {
        "refs": page,
        "next_offset": end if end < len(refs) else None,
        "total": len(refs),
    }


def load_work_packages(manifest_path: str | Path) -> list[WorkPackage]:
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    captures = {}
    for raw in manifest["captures"]:
        metadata = dict(raw.get("metadata", {}))
        metadata["provenance"] = raw.get("provenance", {})
        metadata["sha256"] = raw.get("sha256")
        captures[raw["id"]] = CaptureInput(
            capture_id=raw["id"],
            acquisition_key=raw["acquisition_key"],
            path=(path.parent.parent / raw["path"]).resolve(),
            media_type=raw["media_type"],
            source_namespace=raw.get("source_namespace"),
            retrieved_at=metadata.get("retrieved_at"),
            linked_group=raw.get("linked_group"),
            metadata=metadata,
        )
    return [
        WorkPackage(package_id=key, captures=[captures[cid] for cid in ids])
        for key, ids in manifest["packages"].items()
    ]


class EvidenceIndex:
    def __init__(self, captures: list[CaptureInput]):
        self.captures = {c.capture_id: c for c in captures}
        if len(self.captures) != len(captures):
            raise ValueError("duplicate capture_id")
        self.originals: dict[str, bytes] = {}
        self.nodes: dict[str, dict[str, str]] = {}
        self.aliases: dict[tuple[str, str], str] = {}
        self.children: dict[tuple[str, str], list[str]] = {}
        for capture in captures:
            raw = capture.path.read_bytes()
            self.originals[capture.capture_id] = raw
            text = raw.decode("utf-8", errors="replace")
            nodes = self.nodes[capture.capture_id] = {"$": text}

            def add(
                locator: str,
                value: str,
                parent: str = "$",
                nodes=nodes,
                capture_id=capture.capture_id,
            ) -> None:
                nodes[locator] = value
                self.children.setdefault((capture_id, parent), []).append(locator)

            media = capture.media_type.split(";")[0].strip()
            if media == "application/json" or media.endswith("+json"):

                def walk(value, pointer="", parent="$"):
                    locator = "json:" + pointer
                    add(locator, json.dumps(value, ensure_ascii=False), parent)
                    if isinstance(value, (dict, list)):
                        entries = (
                            value.items()
                            if isinstance(value, dict)
                            else enumerate(value)
                        )
                        for key, child in entries:
                            escaped = str(key).replace("~", "~0").replace("/", "~1")
                            walk(child, pointer + "/" + escaped, locator)

                walk(json.loads(text))
            elif media in ("text/html", "application/xhtml+xml"):
                soup = BeautifulSoup(text, "html.parser")
                serial = 0

                def html_walk(node, locator, parent="$", capture_id=capture.capture_id):
                    nonlocal serial
                    serial += 1
                    primary = "html:@" + str(serial)
                    self.aliases[(capture_id, locator)] = primary
                    add(primary, str(node), parent)
                    counts = {}
                    for child in node.children:
                        if isinstance(child, Tag):
                            counts[child.name] = counts.get(child.name, 0) + 1
                            html_walk(
                                child,
                                locator
                                + "/"
                                + child.name
                                + "["
                                + str(counts[child.name])
                                + "]",
                                primary,
                            )

                counts = {}
                for node in soup.children:
                    if isinstance(node, Tag):
                        counts[node.name] = counts.get(node.name, 0) + 1
                        html_walk(
                            node,
                            "html:/" + node.name + "[" + str(counts[node.name]) + "]",
                        )
            elif media in ("application/xml", "text/xml") or media.endswith("+xml"):

                def xml_walk(node, locator, parent="$"):
                    add(locator, ET.tostring(node, encoding="unicode"), parent)
                    counts = {}
                    for child in node:
                        counts[child.tag] = counts.get(child.tag, 0) + 1
                        xml_walk(
                            child,
                            locator
                            + "/"
                            + child.tag
                            + "["
                            + str(counts[child.tag])
                            + "]",
                            locator,
                        )

                root = ET.fromstring(text)
                xml_walk(root, "xml:/" + root.tag + "[1]")
            elif media in ("text/csv", "text/tab-separated-values"):
                for number, row in enumerate(
                    csv.reader(
                        io.StringIO(text),
                        delimiter="\t"
                        if media.endswith("tab-separated-values")
                        else ",",
                    )
                ):
                    add("csv:/rows/" + str(number), json.dumps(row, ensure_ascii=False))

    def resolve(self, ref: EvidenceRef) -> str:
        return self.nodes[ref.capture_id][
            self.aliases.get((ref.capture_id, ref.locator), ref.locator)
        ]

    def read(
        self, ref: EvidenceRef, offset: int = 0, limit: int = MAX_PAGE_BYTES
    ) -> dict:
        if offset < 0 or not 1 <= limit <= MAX_PAGE_BYTES:
            raise ValueError("invalid page bounds")
        raw = self.resolve(ref).encode("utf-8")
        end = min(offset + limit, len(raw))
        if offset < len(raw) and raw[offset] & 0xC0 == 0x80:
            raise ValueError("offset must be a UTF-8 boundary")
        while end < len(raw) and raw[end] & 0xC0 == 0x80:
            end -= 1
        if end == offset and offset < len(raw):
            raise ValueError("limit too small for next UTF-8 character")
        chunk = raw[offset:end]
        return {
            "ref": ref.model_dump(),
            "text": chunk.decode("utf-8", errors="replace"),
            "offset": offset,
            "next_offset": end if end < len(raw) else None,
            "total_bytes": len(raw),
        }

    def list_children(
        self, ref: EvidenceRef, offset: int = 0, limit: int = 100
    ) -> dict:
        self.resolve(ref)
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("invalid page bounds")
        locator = self.aliases.get((ref.capture_id, ref.locator), ref.locator)
        children = self.children.get((ref.capture_id, locator), [])
        return _ref_page(
            [
                EvidenceRef(capture_id=ref.capture_id, locator=x).model_dump()
                for x in children
            ],
            offset,
            limit,
        )

    def search(
        self,
        query: str,
        capture_ids: list[str] | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict:
        if not query or offset < 0 or not 1 <= limit <= 100:
            raise ValueError("invalid search bounds")
        ids = capture_ids if capture_ids is not None else list(self.nodes)
        matches = [
            EvidenceRef(capture_id=cid, locator=loc).model_dump()
            for cid in ids
            for loc, value in self.nodes[cid].items()
            if query.casefold() in value.casefold()
        ]
        return _ref_page(matches, offset, limit)

    def snapshot(self, capture_ids: list[str] | None = None) -> list[dict]:
        return [
            {
                "capture_id": cid,
                "original_bytes": len(self.originals[cid]),
                "refs": [
                    {"locator": loc, "bytes": len(value.encode("utf-8"))}
                    for loc, value in self.nodes[cid].items()
                ],
            }
            for cid in (capture_ids if capture_ids is not None else self.nodes)
        ]
