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
    requires = ["config", "storage", "vector", "pdf", "ocr"]
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
        self.storage_block = None
        self.vector_block = None
        self.pdf_block = None
        self.ocr_block = None
        self.database_block = None
        
        self.projects = {}
        self._ifc_cache = {}
        
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
        """Index all BIM files from storage"""
        project_id = data.get("project_id")
        folder_path = data.get("folder_path")
        
        if not self.storage_block:
            return {"error": "Storage block not connected"}
        
        # List files
        files_result = await self.storage_block.execute({
            "action": "list",
            "path": folder_path
        })
        
        if "error" in files_result:
            return files_result
        
        # Process BIM files
        bim_files = []
        for file in files_result.get("files", []):
            ext = os.path.splitext(file["name"])[1].lower()
            if ext in self.SUPPORTED_FORMATS:
                file_info = {
                    "name": file["name"],
                    "type": self.SUPPORTED_FORMATS[ext],
                    "path": f"{folder_path}/{file['name']}",
                    "size": file.get("size", 0),
                    "modified": file.get("modified", 0)
                }
                
                # ACTUALLY parse based on type
                if ext == ".ifc":
                    meta = await self._parse_ifc_headers(file_info)
                elif ext in [".dwg", ".dxf"]:
                    meta = await self._extract_dwg_metadata(file_info)
                elif ext == ".pdf":
                    meta = {"type": "drawing_pdf", "needs_ocr": True}
                else:
                    meta = {"type": file_info["type"]}
                
                file_info.update(meta)
                bim_files.append(file_info)
                
                # Index in vector DB
                if self.vector_block:
                    await self.vector_block.execute({
                        "action": "add",
                        "text": f"{file['name']}: {meta.get('description', 'BIM file')}",
                        "metadata": {**file_info, "project": project_id}
                    })
        
        self.projects[project_id] = {
            "folder": folder_path,
            "files": bim_files,
            "indexed_at": datetime.now().isoformat(),
            "total_files": len(bim_files)
        }
        
        return {
            "project_id": project_id,
            "indexed": len(bim_files),
            "by_type": self._count_by_type(bim_files)
        }
    
    async def _parse_ifc_headers(self, file_info: Dict) -> Dict:
        """Parse IFC file headers and basic structure"""
        try:
            import ifcopenshell
            
            # Read file
            result = await self.storage_block.execute({
                "action": "retrieve",
                "file_id": file_info["path"]
            })
            
            if "error" in result:
                return {"error": "Could not read file"}
            
            # Save temp and parse
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as f:
                f.write(result["content"])
                temp_path = f.name
            
            # ACTUALLY PARSE IFC
            ifc_file = ifcopenshell.open(temp_path)
            
            # Extract real data
            project = ifc_file.by_type("IfcProject")[0] if ifc_file.by_type("IfcProject") else None
            site = ifc_file.by_type("IfcSite")[0] if ifc_file.by_type("IfcSite") else None
            building = ifc_file.by_type("IfcBuilding")[0] if ifc_file.by_type("IfcBuilding") else None
            
            # Count elements by type
            element_counts = {}
            for ifc_class in ["IfcWall", "IfcDoor", "IfcWindow", "IfcSlab", "IfcColumn", "IfcBeam"]:
                count = len(ifc_file.by_type(ifc_class))
                if count > 0:
                    element_counts[ifc_class.replace("Ifc", "")] = count
            
            # Cache the parsed summary (dict) — NOT the live ifcopenshell.file
            # object, whose backing temp file we're about to delete on the next
            # line. Holding a live file ref after `os.unlink` led to opaque
            # failures on later lookups; the dict is enough for queries we
            # actually answer (counts, schema, project metadata).
            cache_entry = {
                "schema": ifc_file.schema,
                "project_name": project.Name if project else None,
                "site": site.Name if site else None,
                "building": building.Name if building else None,
                "element_counts": dict(element_counts),
            }
            # Bound the cache so it can't grow unbounded across many uploads.
            _IFC_CACHE_CAP = 32
            if len(self._ifc_cache) >= _IFC_CACHE_CAP:
                # Drop oldest entry (insertion order on Py3.7+ dicts).
                self._ifc_cache.pop(next(iter(self._ifc_cache)))
            self._ifc_cache[file_info["path"]] = cache_entry

            import os
            os.unlink(temp_path)
            
            return {
                "description": f"IFC Model: {project.Name if project else 'Unknown'}",
                "schema": ifc_file.schema,
                "project_name": project.Name if project else None,
                "site_name": site.Name if site else None,
                "building_name": building.Name if building else None,
                "element_counts": element_counts,
                "total_elements": sum(element_counts.values()),
                "extracted": True
            }
            
        except ImportError:
            # Fallback - read header only
            return {
                "description": "IFC Model (ifcopenshell not installed)",
                "schema": "IFC2X3 (assumed)",
                "extracted": False
            }
        except Exception as e:
            return {
                "description": f"IFC Model (parse error: {str(e)})",
                "extracted": False
            }
    
    async def _parse_ifc_real(self, data: Dict) -> Dict:
        """Full IFC parsing with element extraction"""
        file_path = data.get("file_path")
        element_types = data.get("element_types", ["IfcWall", "IfcDoor", "IfcWindow"])
        
        try:
            import ifcopenshell
            
            # Get file
            result = await self.storage_block.execute({
                "action": "retrieve",
                "file_id": file_path
            })
            
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as f:
                f.write(result["content"])
                temp_path = f.name
            
            ifc_file = ifcopenshell.open(temp_path)
            
            # Extract requested elements
            elements = []
            for elem_type in element_types:
                for elem in ifc_file.by_type(elem_type):
                    elem_data = {
                        "id": elem.id(),
                        "type": elem_type,
                        "name": elem.Name if hasattr(elem, "Name") else None,
                        "global_id": elem.GlobalId if hasattr(elem, "GlobalId") else None
                    }
                    
                    # Get properties
                    if hasattr(elem, "IsDefinedBy"):
                        for rel in elem.IsDefinedBy:
                            if rel.is_a("IfcRelDefinesByProperties"):
                                props = rel.RelatingPropertyDefinition
                                if props.is_a("IfcPropertySet"):
                                    for prop in props.HasProperties:
                                        if hasattr(prop, "NominalValue"):
                                            elem_data[prop.Name] = prop.NominalValue.wrappedValue
                    
                    elements.append(elem_data)
            
            import os
            os.unlink(temp_path)
            
            return {
                "file": file_path,
                "elements": elements,
                "count": len(elements),
                "schema": ifc_file.schema
            }
            
        except ImportError:
            return {"error": "ifcopenshell not installed"}
        except Exception as e:
            return {"error": f"IFC parse failed: {str(e)}"}
    
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
        """Actually process PDF drawings with OCR"""
        file_path = data.get("file_path")
        project_id = data.get("project_id")
        
        # Get file
        result = await self.storage_block.execute({
            "action": "retrieve",
            "file_id": file_path
        })
        
        if "error" in result:
            return result
        
        # Use PDF block
        if self.pdf_block:
            pdf_result = await self.pdf_block.execute({
                "action": "extract_text",
                "pdf_bytes": result["content"]
            })
            
            text = pdf_result.get("text", "")
            
            # If little text, try OCR
            if len(text.strip()) < 100 and self.ocr_block:
                # Extract images and OCR
                img_result = await self.pdf_block.execute({
                    "action": "extract_images",
                    "pdf_bytes": result["content"]
                })
                
                ocr_texts = []
                for img in img_result.get("images", [])[:5]:  # Limit
                    ocr_result = await self.ocr_block.execute({
                        "action": "extract_text",
                        "image_bytes": img["bytes"],
                        "engine": "easyocr"
                    })
                    if "text" in ocr_result:
                        ocr_texts.append(ocr_result["text"])
                
                text = "\n".join(ocr_texts)
            
            # Extract drawing info
            drawing_info = self._extract_drawing_info(text)
            
            return {
                "file": file_path,
                "text": text[:5000],  # Limit
                "pages": pdf_result.get("pages", 0),
                "drawing_info": drawing_info,
                "processed": True
            }
        
        return {"error": "PDF block not available"}
    
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
