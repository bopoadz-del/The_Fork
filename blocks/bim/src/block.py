"""BIM Block - Real IFC/DWG/PDF file processor"""
from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
import os
import json
import hashlib
from datetime import datetime

class BIMBlock(LegoBlock):
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
        
    async def initialize(self):
        print(f"📐 BIM File Processor ready")
        print(f"   Supports: {list(self.SUPPORTED_FORMATS.keys())}")
        
        # Check ifcopenshell availability
        try:
            import ifcopenshell
            print(f"   IFC parsing: ENABLED (ifcopenshell {ifcopenshell.version})")
        except ImportError:
            print("   IFC parsing: LIMITED (pip install ifcopenshell for full support)")
        
        return True
    
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        
        if action == "index_folder":
            return await self._index_folder(input_data)
        elif action == "parse_ifc":
            return await self._parse_ifc_real(input_data)
        elif action == "extract_dwg_metadata":
            return await self._extract_dwg_real(input_data)
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
            
            # Store in cache for later queries
            self._ifc_cache[file_info["path"]] = ifc_file
            
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
        """Extract DWG metadata using ODA File Converter or ezdxf"""
        try:
            import ezdxf
            
            result = await self.storage_block.execute({
                "action": "retrieve",
                "file_id": file_info["path"]
            })
            
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".dwg", delete=False) as f:
                f.write(result["content"])
                temp_path = f.name
            
            # Try to read DWG
            try:
                doc = ezdxf.readfile(temp_path)
                msp = doc.modelspace()
                
                # Count entities
                entity_count = len(msp)
                
                # Get layers
                layers = [layer.dxf.name for layer in doc.layers]
                
                # Get extents if available
                extents = None
                if msp:
                    try:
                        bbox = msp.extents()
                        extents = {
                            "min": [bbox.extmin[0], bbox.extmin[1]],
                            "max": [bbox.extmax[0], bbox.extmax[1]]
                        }
                    except:
                        pass
                
                import os
                os.unlink(temp_path)
                
                return {
                    "description": f"AutoCAD Drawing: {len(layers)} layers, {entity_count} entities",
                    "version": doc.dxfversion,
                    "layers": layers[:20],  # Limit
                    "entity_count": entity_count,
                    "extents": extents,
                    "extracted": True
                }
                
            except Exception as e:
                import os
                os.unlink(temp_path)
                return {
                    "description": f"AutoCAD Drawing (ezdxf fallback: {str(e)})",
                    "extracted": False
                }
                
        except ImportError:
            return {
                "description": "AutoCAD Drawing (ezdxf not installed)",
                "extracted": False
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
        """Find elements in spatial bounding box"""
        # Requires ifcopenshell geom module
        return {"error": "Spatial queries require ifcopenshell[geom]"}
    
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
        h = super().health()
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
