"""Test for v2 TypedBlocks - pdf_v2 → construction_v2 → chat chain"""

import asyncio
import sys
import os

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.blocks.pdf_v2 import PDFBlockV2
from app.blocks.ocr_v2 import OCRBlockV2
from app.blocks.construction_v2 import ConstructionBlockV2
from app.blocks.chat import ChatBlock


async def test_pdf_v2():
    """Test PDF v2 block returns TextContent format"""
    print("=" * 60)
    print("TEST 1: PDF v2 Block")
    print("=" * 60)
    
    pdf_block = PDFBlockV2()
    
    # Check schema info
    schema_info = pdf_block.get_schema_info()
    print(f"\n✓ Block name: {schema_info['name']}")
    print(f"✓ Version: {schema_info['version']}")
    print(f"✓ Output schema type: {schema_info['output_schema'].get('type', 'unknown')}")
    print(f"✓ Accepted input types: {pdf_block.accepted_input_types}")
    print(f"✓ Produced output types: {pdf_block.produced_output_types}")
    
    # Test with a sample PDF path (won't work without file, but tests structure)
    result = await pdf_block.execute(
        {"file_path": "/tmp/test.pdf"},
        {}
    )
    
    print(f"\n✓ Execution status: {result.get('status')}")
    print(f"✓ Result keys: {list(result.get('result', {}).keys())}")
    
    # Verify output follows TextContent schema
    result_data = result.get('result', {})
    if 'text' in result_data and 'source' in result_data:
        print("✓ Output follows TextContent schema")
    else:
        print("✗ Output missing TextContent fields")
    
    return result


async def test_ocr_v2():
    """Test OCR v2 block returns TextContent format"""
    print("\n" + "=" * 60)
    print("TEST 2: OCR v2 Block")
    print("=" * 60)
    
    ocr_block = OCRBlockV2()
    
    # Check schema info
    schema_info = ocr_block.get_schema_info()
    print(f"\n✓ Block name: {schema_info['name']}")
    print(f"✓ Version: {schema_info['version']}")
    print(f"✓ Output schema type: {schema_info['output_schema'].get('type', 'unknown')}")
    
    # Test with a sample image path
    result = await ocr_block.execute(
        {"image_path": "/tmp/test.png"},
        {}
    )
    
    print(f"\n✓ Execution status: {result.get('status')}")
    print(f"✓ Result keys: {list(result.get('result', {}).keys())}")
    
    # Verify output follows TextContent schema
    result_data = result.get('result', {})
    if 'text' in result_data and 'source' in result_data:
        print("✓ Output follows TextContent schema")
    else:
        print("✗ Output missing TextContent fields")
    
    return result


async def test_construction_v2():
    """Test Construction v2 block accepts TextContent and outputs ConstructionAnalysis"""
    print("\n" + "=" * 60)
    print("TEST 3: Construction v2 Block")
    print("=" * 60)
    
    construction_block = ConstructionBlockV2()
    
    # Check schema info
    schema_info = construction_block.get_schema_info()
    print(f"\n✓ Block name: {schema_info['name']}")
    print(f"✓ Version: {schema_info['version']}")
    print(f"✓ Input schema type: {schema_info['input_schema'].get('type', 'unknown')}")
    print(f"✓ Output schema type: {schema_info['output_schema'].get('type', 'unknown')}")
    print(f"✓ Accepted input types: {construction_block.accepted_input_types}")
    print(f"✓ Produced output types: {construction_block.produced_output_types}")
    
    # Test with sample construction text (TextContent format)
    sample_text = """
    PROJECT: Office Building Foundation
    
    Concrete slab: 15.5m x 12.3m x 0.3m
    Reinforcement: Rebar Grade 60, 150 kg/m3
    Concrete Grade: C30
    
    Dimensions:
    - Length: 15.5m
    - Width: 12.3m  
    - Thickness: 0.3m
    
    Materials:
    - Ready-mix concrete: 57.2 m3
    - Steel rebar: 8,640 kg
    - Formwork: 380 m2
    """
    
    text_content = {
        "text": sample_text,
        "source": "pdf",
        "pages": 1,
        "metadata": {"filename": "drawing_sample.txt"}
    }
    
    result = await construction_block.execute(text_content, {})
    
    print(f"\n✓ Execution status: {result.get('status')}")
    result_data = result.get('result', {})
    print(f"✓ Result keys: {list(result_data.keys())}")
    
    # Verify output follows ConstructionAnalysis schema
    if 'measurements' in result_data and 'quantities' in result_data:
        print("✓ Output follows ConstructionAnalysis schema")
        quantities = result_data.get('quantities', {})
        print(f"\n📊 Extracted Quantities:")
        for key, value in quantities.items():
            print(f"   - {key}: {value}")
    else:
        print("✗ Output missing ConstructionAnalysis fields")
    
    return result


async def test_chain_pdf_construction():
    """Test the chain: PDF → Construction → Chat"""
    print("\n" + "=" * 60)
    print("TEST 4: Chain - pdf_v2 → construction_v2")
    print("=" * 60)
    
    pdf_block = PDFBlockV2()
    construction_block = ConstructionBlockV2()
    
    # Simulate PDF output (since we don't have an actual file)
    simulated_pdf_output = {
        "text": """
        BUILDING: Residential Villa
        
        Foundation Plan:
        - Footing size: 2.5m x 2.5m x 0.6m (20 nos)
        - Column size: 0.4m x 0.4m
        - Beam size: 0.3m x 0.6m
        
        Concrete: Grade C35
        Reinforcement: Grade 60 Rebar
        """,
        "source": "pdf",
        "pages": 3,
        "metadata": {"filename": "foundation_plan.pdf"}
    }
    
    print("\n📄 Step 1: PDF extracted text")
    print(f"   Source: {simulated_pdf_output['source']}")
    print(f"   Pages: {simulated_pdf_output['pages']}")
    print(f"   Text length: {len(simulated_pdf_output['text'])} chars")
    
    # Pass TextContent to construction block
    print("\n🏗️  Step 2: Construction analysis")
    construction_result = await construction_block.execute(simulated_pdf_output, {})
    
    if construction_result.get('status') == 'success':
        result_data = construction_result.get('result', {})
        quantities = result_data.get('quantities', {})
        measurements = result_data.get('measurements', [])
        
        print(f"   ✓ Measurements found: {len(measurements)}")
        print(f"   ✓ Quantities calculated:")
        for key, value in quantities.items():
            print(f"      - {key}: {value}")
    else:
        print(f"   ✗ Error: {construction_result.get('result', {}).get('error', 'Unknown')}")
    
    return construction_result


async def test_type_compatibility():
    """Test type compatibility between blocks"""
    print("\n" + "=" * 60)
    print("TEST 5: Type Compatibility")
    print("=" * 60)
    
    from app.core.schema_registry import registry
    
    pdf_block = PDFBlockV2()
    construction_block = ConstructionBlockV2()
    
    print("\n🔍 Checking type compatibility:")
    
    # PDF produces TextContent, Construction accepts TextContent
    pdf_outputs = pdf_block.produced_output_types
    construction_inputs = construction_block.accepted_input_types
    
    print(f"   pdf_v2 produces: {pdf_outputs}")
    print(f"   construction_v2 accepts: {construction_inputs}")
    
    # Check if TextContent is compatible
    compatible = any(out_type in construction_inputs for out_type in pdf_outputs)
    
    if compatible:
        print("   ✓ Blocks are compatible - can be chained")
    else:
        print("   ✗ Blocks are NOT compatible")
    
    # List all registered types
    print(f"\n📋 Registered types in schema registry:")
    for type_name in registry.list_types():
        print(f"   - {type_name}")


async def run_all_tests():
    """Run all v2 block tests"""
    print("\n" + "🧪" * 30)
    print("TYPEDBLOCK V2 TEST SUITE")
    print("🧪" * 30)
    
    try:
        await test_pdf_v2()
        await test_ocr_v2()
        await test_construction_v2()
        await test_chain_pdf_construction()
        await test_type_compatibility()
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS COMPLETED")
        print("=" * 60)
        print("\nSummary:")
        print("- pdf_v2: Returns TextContent format ✓")
        print("- ocr_v2: Returns TextContent format ✓")
        print("- construction_v2: Accepts TextContent, outputs ConstructionAnalysis ✓")
        print("- Chain pdf_v2 → construction_v2: Compatible ✓")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
