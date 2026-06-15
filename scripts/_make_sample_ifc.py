"""Create a minimal valid IFC4 sample file for bim_extractor smoke tests.

Builds a 2-storey "DG2 Sample Building" with 8 walls, 2 slabs, 4 columns,
2 beams, 2 doors, 2 windows, 2 spaces, plus a pipe / duct / light fixture
so every category in IFC_CATEGORY_MAP has at least one element.

Usage:
    python scripts/_make_sample_ifc.py [out_path]

Default ``out_path`` is ``tests/fixtures/sample_office.ifc`` relative to repo root.
"""
from __future__ import annotations

import os
import sys

import ifcopenshell
import ifcopenshell.api


def build(out_path: str) -> None:
    m = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcProject", name="DG2 Sample Building")
    ifcopenshell.api.run("unit.assign_unit", m)
    ctx = ifcopenshell.api.run("context.add_context", m, context_type="Model")
    ifcopenshell.api.run(
        "context.add_context", m,
        context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx,
    )

    site = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcSite", name="Site A")
    bldg = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcBuilding", name="Office Block 1")
    s1 = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcBuildingStorey", name="Ground Floor")
    s2 = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcBuildingStorey", name="Level 1")
    ifcopenshell.api.run("aggregate.assign_object", m, products=[site], relating_object=project)
    ifcopenshell.api.run("aggregate.assign_object", m, products=[bldg], relating_object=site)
    ifcopenshell.api.run("aggregate.assign_object", m, products=[s1, s2], relating_object=bldg)

    def add_to_storey(cls_name: str, name: str, storey) -> object:
        e = ifcopenshell.api.run("root.create_entity", m, ifc_class=cls_name, name=name)
        ifcopenshell.api.run("spatial.assign_container", m, products=[e], relating_structure=storey)
        return e

    for floor, storey in (("GF", s1), ("L1", s2)):
        for side in ("N", "S", "E", "W"):
            add_to_storey("IfcWall", f"Wall-{floor}-{side}", storey)
        add_to_storey("IfcSlab", f"Slab-{floor}", storey)
        for i in (1, 2):
            add_to_storey("IfcColumn", f"Col-{floor}-{i}", storey)
        add_to_storey("IfcBeam", f"Beam-{floor}-Main", storey)

    add_to_storey("IfcDoor", "Main-Entrance-Door", s1)
    add_to_storey("IfcDoor", "L1-Door-A", s2)
    add_to_storey("IfcWindow", "GF-Window-North", s1)
    add_to_storey("IfcWindow", "L1-Window-East", s2)
    add_to_storey("IfcPipeSegment", "Pipe-Storm-001", s1)
    add_to_storey("IfcDuctSegment", "Duct-AHU-1", s2)
    add_to_storey("IfcLightFixture", "Light-GF-01", s1)

    # IfcSpace uses aggregate (not spatial.assign_container) in IFC4.
    for storey, name in ((s1, "Office-Room-1"), (s2, "Office-Room-2")):
        sp = ifcopenshell.api.run("root.create_entity", m, ifc_class="IfcSpace", name=name)
        ifcopenshell.api.run("aggregate.assign_object", m, products=[sp], relating_object=storey)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    m.write(out_path)
    print(
        f"wrote IFC -> {out_path}  size={os.path.getsize(out_path)}B  schema={m.schema}  "
        f"walls={len(m.by_type('IfcWall'))} slabs={len(m.by_type('IfcSlab'))} "
        f"columns={len(m.by_type('IfcColumn'))} beams={len(m.by_type('IfcBeam'))}"
    )


def main() -> int:
    if len(sys.argv) > 1:
        out = sys.argv[1]
    else:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = os.path.join(repo_root, "tests", "fixtures", "sample_office.ifc")
    build(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
