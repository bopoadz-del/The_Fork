"""BIM Block - Real IFC/DWG/PDF file processor"""
from app.core.universal_base import UniversalBlock
from typing import Dict, Any, List, Optional
import os
import json
import hashlib
from datetime import datetime

class BIMBlock(UniversalBlock):
    """
    BIM File Processor - ACTUALLY parses IFC, extracts DWG metadata, OCRs PDFs
    """
    
    name = "bim"
    version = "1.0.0"
    # Original `requires = ["config", "storage", "vector", "pdf", "ocr"]`
    # referenced four blocks (`config`, `storage`, `vector`, `database`) that
    # don't exist in BLOCK_REGISTRY — the platform moved to a direct-file-path
    # architecture (document_engine, bim_extractor) and storage was never
    # implemented. We keep `pdf` + `ocr` so `process_pdf` can fall back to
    # them, and we delegate IFC parsing to `bim_extractor` (which actually
    # works today). Storage-backed actions (`index_folder`, `compare_versions`)
    # surface a clear error instead of crashing on a missing dep.
    requires = ["pdf", "ocr", "bim_extractor", "document_engine", "zvec"]
    layer = 6  # Domain layer
    tags = ["construction", "bim", "cad", "domain"]
    default_config = {
        "ifc_enabled": True,
        "dwg_enabled": True,
        "auto_index": True
    }
    
    SUPPORTED_FORMATS = {
        ".ifc": "bim_model",
        ".ifczip": "bim_model_compressed",
        ".dwg": "cad_drawing", 
        ".dxf": "cad_exchange",
        ".pdf": "drawing_pdf",
        ".rvt": "revit_model",
        ".nwd": "navisworks",
        ".xlsx": "schedule",
        ".csv": "data_export"
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        # NB: `self.storage_block` / `self.pdf_block` / etc. used to be set
        # to None here, but the platform's `wire()` populates
        # `self._dependencies[<name>]` — not those attributes. So even after
        # the dependency container wired the deps in, every action below
        # checked `self.pdf_block` (still None) and short-circuited with
        # "block not connected". The whole block was unreachable.
        #
        # The lazy properties below resolve `self.pdf_block` etc. from
        # `self.get_dep(...)` at access time, so wired deps flow through
        # transparently without touching the 200+ usage sites.
        self.projects = {}
        self._ifc_cache = {}

    @property
    def storage_block(self): return self.get_dep("storage")

    @property
    def vector_block(self):
        # Old name was `vector`, the platform's real indexer is `zvec`
        # (with a `vector_search` alias). Resolve either so legacy callers
        # of `self.vector_block` still get something usable.
        return self.get_dep("zvec") or self.get_dep("vector_search") or self.get_dep("vector")

    @property
    def pdf_block(self): return self.get_dep("pdf")

    @property
    def ocr_block(self): return self.get_dep("ocr")

    @property
    def database_block(self): return self.get_dep("database")
        
    async def _legacy_initialize(self):
        print(f"📐 BIM File Processor ready")
        print(f"   Supports: {list(self.SUPPORTED_FORMATS.keys())}")
        
        # Check ifcopenshell availability
        try:
            import ifcopenshell
            print(f"   IFC parsing: ENABLED (ifcopenshell {ifcopenshell.version})")
        except ImportError:
            print("   IFC parsing: LIMITED (pip install ifcopenshell for full support)")
        
        return True
    
    async def process(self, input_data: Dict, params: Dict = None) -> Dict:
        action = (params or {}).get("action") or (input_data.get("action") if isinstance(input_data, dict) else None)
        
        if action == "index_folder":
            return await self._index_folder(input_data)
        elif action == "parse_ifc":
            return await self._parse_ifc_real(input_data)
        elif action == "extract_dwg_metadata":
            return await self._extract_dwg_metadata(input_data)
        elif action == "process_pdf":
            return await self._process_pdf_real(input_data)
        elif action == "get_elements":
            return await self._get_ifc_elements(input_data)
        elif action == "spatial_query":
            return await self._spatial_query(input_data)
        elif action == "compare_versions":
            return await self._compare_versions_real(input_data)
        
        return {"error": f"Unknown action: {action}"}
    
    async def _index_folder(self, data: Dict) -> Dict:
        """Walk `folder_path` on disk, parse every supported BIM file's headers,
        and (when zvec is wired) embed a one-line summary per file so the
        chat's RAG can search across them.

        Rewired: previously listed via `self.storage_block.execute(list)` and
        embedded via `self.vector_block.execute(add)`. Neither block exists
        in the platform — `storage` was never built, and the vector indexer
        is named `zvec` (or `vector_search`), not `vector`. Folder listing
        now uses `os.scandir`; embedding goes through the `zvec` dep.
        """
        project_id = data.get("project_id")
        folder_path = data.get("folder_path")
        if not folder_path or not os.path.isdir(folder_path):
            return {"error": f"folder_path required and must be a directory (got {folder_path!r})"}

        bim_files: List[Dict] = []
        for entry in os.scandir(folder_path):
            if not entry.is_file():
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            if ext not in self.SUPPORTED_FORMATS:
                continue
            stat = entry.stat()
            file_info = {
                "name": entry.name,
                "type": self.SUPPORTED_FORMATS[ext],
                "path": entry.path,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
            if ext == ".ifc":
                meta = await self._parse_ifc_headers(file_info)
            elif ext in (".dwg", ".dxf"):
                meta = await self._extract_dwg_metadata(file_info)
            elif ext == ".pdf":
                meta = {"type": "drawing_pdf", "needs_ocr": True}
            else:
                meta = {"type": file_info["type"]}
            file_info.update(meta)
            bim_files.append(file_info)

            # Best-effort RAG indexing via the zvec block (when wired). zvec
            # is async-friendly via .execute(); we pass a one-line summary
            # plus the file metadata so chat search can surface it later.
            zvec = self.get_dep("zvec") or self.get_dep("vector_search")
            if zvec is not None:
                try:
                    summary = f"{entry.name}: {meta.get('description') or self.SUPPORTED_FORMATS[ext]}"
                    await zvec.execute(
                        summary,
                        {"operation": "embed", "metadata": {**file_info, "project": project_id}},
                    )
                except Exception:
                    # Indexing is best-effort — don't fail the whole index_folder
                    # call because zvec hiccupped on one entry.
                    pass

        self.projects[project_id] = {
            "folder": folder_path,
            "files": bim_files,
            "indexed_at": datetime.now().isoformat(),
            "total_files": len(bim_files),
        }
        return {
            "project_id": project_id,
            "indexed": len(bim_files),
            "by_type": self._count_by_type(bim_files),
        }
    
    async def _parse_ifc_headers(self, file_info: Dict) -> Dict:
        """Parse IFC file headers + element counts directly from disk.

        Rewired: ifcopenshell.open() reads files by path, so the old
        storage→tempfile dance was pure overhead even when it worked. With
        the storage block now correctly identified as nonexistent, this
        method just reads file_info["path"] directly.
        """
        path = file_info.get("path") or file_info.get("file_path")
        if not path or not os.path.exists(path):
            return {"error": f"file_path required and must exist (got {path!r})"}
        try:
            import ifcopenshell

            ifc_file = ifcopenshell.open(path)

            project = ifc_file.by_type("IfcProject")[0] if ifc_file.by_type("IfcProject") else None
            site = ifc_file.by_type("IfcSite")[0] if ifc_file.by_type("IfcSite") else None
            building = ifc_file.by_type("IfcBuilding")[0] if ifc_file.by_type("IfcBuilding") else None

            element_counts = {}
            for ifc_class in ["IfcWall", "IfcDoor", "IfcWindow", "IfcSlab", "IfcColumn", "IfcBeam"]:
                count = len(ifc_file.by_type(ifc_class))
                if count > 0:
                    element_counts[ifc_class.replace("Ifc", "")] = count

            # Cache the parsed summary (a plain dict — not the live
            # ifcopenshell.file). LRU-bounded so a long-running session that
            # touches many IFCs doesn't blow memory.
            cache_entry = {
                "schema": ifc_file.schema,
                "project_name": project.Name if project else None,
                "site": site.Name if site else None,
                "building": building.Name if building else None,
                "element_counts": dict(element_counts),
            }
            _IFC_CACHE_CAP = 32
            if len(self._ifc_cache) >= _IFC_CACHE_CAP:
                self._ifc_cache.pop(next(iter(self._ifc_cache)))
            self._ifc_cache[path] = cache_entry

            return {
                "description": f"IFC Model: {project.Name if project else 'Unknown'}",
                "schema": ifc_file.schema,
                "project_name": project.Name if project else None,
                "site_name": site.Name if site else None,
                "building_name": building.Name if building else None,
                "element_counts": element_counts,
                "total_elements": sum(element_counts.values()),
                "extracted": True,
            }
        except ImportError:
            return {
                "description": "IFC Model (ifcopenshell not installed)",
                "schema": "IFC2X3 (assumed)",
                "extracted": False,
            }
        except Exception as e:
            return {
                "description": f"IFC Model (parse error: {str(e)})",
                "extracted": False,
            }
    
    async def _parse_ifc_real(self, data: Dict) -> Dict:
        """Full IFC parsing with element extraction. Delegates to the
        bim_extractor block (which actually works today). The previous
        implementation fetched the file via a `storage` block that doesn't
        exist in the current platform — so this action was unreachable."""
        import os
        file_path = data.get("file_path")
        if not file_path or not os.path.exists(file_path):
            return {"error": f"file_path required and must exist (got {file_path!r})"}
        element_types = data.get("element_types", ["IfcWall", "IfcDoor", "IfcWindow"])

        extractor = self.get_dep("bim_extractor")
        if extractor is None:
            return {"error": "bim_extractor block not available"}
        result = await extractor.process(
            {"file_path": file_path},
            {"element_types": element_types},
        )
        if result.get("status") == "error":
            return {"error": result.get("error", "bim_extractor failed")}
        # Reshape bim_extractor's response into this method's contract.
        elements = result.get("elements", [])
        return {
            "file": file_path,
            "elements": elements,
            "count": len(elements),
            "schema": result.get("schema") or result.get("project_info", {}).get("schema"),
        }
    
    async def _extract_dwg_metadata(self, file_info: Dict) -> Dict:
        """DWG is binary AutoCAD — ezdxf only parses DXF (the text format).

        Previously this method tried to feed DWG bytes to `ezdxf.readfile()`,
        which always raises, falls into the silent exception handler, and
        returns `{"extracted": False}` with a misleading "ezdxf fallback"
        message. Every real DWG upload silently dropped its content.

        Honest behavior: tell the caller DWG needs to be converted to DXF
        first (via ODA File Converter, AutoCAD, or a similar tool). No
        silent failure.
        """
        return {
            "status": "error",
            "error": (
                "DWG is binary AutoCAD format — extraction not supported. "
                "Convert to DXF first (ODA File Converter is free) and "
                "re-upload, then drawing_qto / bim will process it."
            ),
            "description": "AutoCAD DWG (conversion required)",
            "extracted": False,
            "format": "dwg",
            "requires_conversion_to": "dxf",
        }
    
    async def _process_pdf_real(self, data: Dict) -> Dict:
        """Process a PDF drawing — text-layer extraction, OCR-fallback for
        image-only drawings, then heuristic drawing-info extraction.

        Rewired to read `file_path` directly from disk via document_engine
        (which already wires `pdf` + `ocr`). The original implementation
        called `self.storage_block.execute({...retrieve...})` against a
        `storage` block that doesn't exist in this codebase — so this action
        crashed for everyone.
        """
        import os
        file_path = data.get("file_path")
        if not file_path or not os.path.exists(file_path):
            return {"error": f"file_path required and must exist (got {file_path!r})"}

        # Delegate to document_engine, which does PDF → text-layer → OCR
        # fallback. Use it via the wired dep so we share the platform's
        # singleton (and its already-wired pdf+ocr).
        engine = self.get_dep("document_engine")
        if engine is None:
            return {"error": "document_engine block not available"}
        eng_result = await engine.process({}, {"pdf_path": file_path})
        if eng_result.get("status") == "error":
            return {"error": eng_result.get("error", "document_engine failed")}

        text = (eng_result.get("raw_text") or "").strip()
        drawing_info = self._extract_drawing_info(text)
        return {
            "file": file_path,
            "text": text[:5000],
            "pages": (eng_result.get("documents_parsed") or 0),
            "drawing_info": drawing_info,
            "extracted_via": eng_result.get("platform_blocks_used", []),
            "processed": True,
        }
    
    def _extract_drawing_info(self, text: str) -> Dict:
        """Extract structured info from drawing text"""
        import re
        
        # Look for drawing numbers (A-101, etc.)
        drawing_nums = re.findall(r'[A-Z]-\d{3,4}', text)
        
        # Look for dates
        dates = re.findall(r'\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}', text)
        
        # Look for scales
        scales = re.findall(r'\d+\s*:\s*\d+', text)
        
        # Look for sheet sizes
        sheet_sizes = re.findall(r'\b(A0|A1|A2|A3|A4)\b', text)
        
        return {
            "drawing_numbers": list(set(drawing_nums))[:10],
            "dates": list(set(dates))[:5],
            "scales": list(set(scales))[:5],
            "sheet_sizes": list(set(sheet_sizes))
        }
    
    async def _get_ifc_elements(self, data: Dict) -> Dict:
        """Query IFC elements with filters"""
        file_path = data.get("file_path")
        element_type = data.get("element_type", "IfcWall")
        properties = data.get("properties", [])
        
        return await self._parse_ifc_real({
            "file_path": file_path,
            "element_types": [element_type]
        })
    
    async def _spatial_query(self, data: Dict) -> Dict:
        """Find IFC elements whose AABB intersects a query bounding box.

        Uses ifcopenshell.geom to compute world-coord AABBs per element, then
        keeps those whose box overlaps the query box. Returns elements grouped
        by ifc_type with counts and IDs. Requires the `geom` extension; the
        method returns an honest error when it's not installed.
        """
        file_path = data.get("file_path")
        if not file_path:
            return {"status": "error", "error": "file_path is required"}
        bbox = data.get("bbox") or data.get("bounding_box")
        if not bbox or len(bbox) != 6:
            return {
                "status": "error",
                "error": "Provide bbox = [xmin, ymin, zmin, xmax, ymax, zmax] (world coords, metres).",
            }
        # Default to the major IFC types if none specified.
        element_types = data.get("element_types") or [
            "IfcWall", "IfcSlab", "IfcBeam", "IfcColumn",
            "IfcDoor", "IfcWindow", "IfcPipeSegment",
            "IfcDuctSegment", "IfcCableSegment",
        ]

        try:
            import ifcopenshell
            import ifcopenshell.geom
        except Exception:
            return {
                "status": "error",
                "error": "ifcopenshell.geom not available. Install ifcopenshell[all] for spatial queries.",
            }

        try:
            model = ifcopenshell.open(file_path)
        except Exception as e:
            return {"status": "error", "error": f"IFC open failed: {e}"}

        settings = ifcopenshell.geom.settings()
        try:
            settings.set(settings.USE_WORLD_COORDS, True)
        except Exception:
            pass

        qmin = (float(bbox[0]), float(bbox[1]), float(bbox[2]))
        qmax = (float(bbox[3]), float(bbox[4]), float(bbox[5]))

        hits: Dict[str, List[str]] = {}
        considered = 0
        for ifc_type in element_types:
            try:
                items = model.by_type(ifc_type)
            except Exception:
                continue
            for el in items:
                considered += 1
                try:
                    shape = ifcopenshell.geom.create_shape(settings, el)
                    verts = shape.geometry.verts
                except Exception:
                    continue
                if not verts:
                    continue
                xs = verts[0::3]; ys = verts[1::3]; zs = verts[2::3]
                emin = (min(xs), min(ys), min(zs))
                emax = (max(xs), max(ys), max(zs))
                if (emin[0] <= qmax[0] and emax[0] >= qmin[0]
                        and emin[1] <= qmax[1] and emax[1] >= qmin[1]
                        and emin[2] <= qmax[2] and emax[2] >= qmin[2]):
                    hits.setdefault(ifc_type, []).append(getattr(el, "GlobalId", str(el.id())))

        total = sum(len(v) for v in hits.values())
        return {
            "status": "success",
            "bbox": bbox,
            "elements_considered": considered,
            "match_count": total,
            "matches_by_type": {k: {"count": len(v), "ids": v[:50]} for k, v in hits.items()},
        }
    
    async def _compare_versions_real(self, data: Dict) -> Dict:
        """Compare two versions of same file"""
        old_path = data.get("old_version")
        new_path = data.get("new_version")
        
        # Parse both
        old_data = await self._parse_ifc_real({"file_path": old_path, "element_types": ["IfcWall"]})
        new_data = await self._parse_ifc_real({"file_path": new_path, "element_types": ["IfcWall"]})
        
        # Compare element counts
        old_count = old_data.get("count", 0)
        new_count = new_data.get("count", 0)
        
        return {
            "old_version": old_path,
            "new_version": new_path,
            "old_count": old_count,
            "new_count": new_count,
            "difference": new_count - old_count,
            "change_percent": ((new_count - old_count) / old_count * 100) if old_count > 0 else 0
        }
    
    def _count_by_type(self, files: List[Dict]) -> Dict:
        counts = {}
        for f in files:
            t = f["type"]
            counts[t] = counts.get(t, 0) + 1
        return counts
    
    def health(self) -> Dict:
        h = {"name": self.name, "version": self.version}
        h["projects_indexed"] = len(self.projects)
        h["files_tracked"] = sum(p.get("total_files", 0) for p in self.projects.values())
        h["ifc_cache_size"] = len(self._ifc_cache)
        
        try:
            import ifcopenshell
            h["ifcopenshell"] = True
            h["ifcopenshell_version"] = ifcopenshell.version
        except ImportError:
            h["ifcopenshell"] = False
        
        return h
