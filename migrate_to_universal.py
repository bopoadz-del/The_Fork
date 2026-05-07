#!/usr/bin/env python3
"""
Migration Script: Add Universal Metadata to All Blocks

Adds:
- layer: Init order (0=infrastructure, 1=security, 2=ai_core, 3=domain, 4=integration, 5=interface)
- tags: Categorization for filtering
- requires: Dependency wiring for auto-assembly
- default_config: Auto-wired configuration
"""

import os
import re
from pathlib import Path

# Layer definitions
LAYERS = {
    "infrastructure": 0,
    "security": 1, 
    "ai_core": 2,
    "domain": 3,
    "integration": 4,
    "interface": 5
}

# Block metadata mapping
BLOCK_METADATA = {
    # Infrastructure (Layer 0)
    "memory": {"layer": 0, "tags": ["infrastructure", "cache", "storage"], "requires": []},
    "config": {"layer": 0, "tags": ["infrastructure", "config"], "requires": []},
    "monitoring": {"layer": 0, "tags": ["infrastructure", "monitoring"], "requires": []},
    
    # Security (Layer 1)
    "auth": {"layer": 1, "tags": ["security", "auth"], "requires": ["config"]},
    "security": {"layer": 1, "tags": ["security", "container"], "requires": ["config"]},
    
    # AI Core (Layer 2)
    "chat": {"layer": 2, "tags": ["ai", "core", "llm"], "requires": []},
    "vector_search": {"layer": 2, "tags": ["ai", "core", "vector", "search"], "requires": []},
    "ai_core": {"layer": 2, "tags": ["ai", "core", "container"], "requires": []},
    "zvec": {"layer": 2, "tags": ["ai", "vector", "zero-shot"], "requires": []},
    
    # Domain (Layer 3)
    "pdf": {"layer": 3, "tags": ["domain", "documents", "pdf"], "requires": []},
    "ocr": {"layer": 3, "tags": ["domain", "documents", "ocr", "vision"], "requires": []},
    "voice": {"layer": 3, "tags": ["domain", "audio", "tts", "stt"], "requires": []},
    "image": {"layer": 3, "tags": ["domain", "vision", "image"], "requires": []},
    "translate": {"layer": 3, "tags": ["domain", "nlp", "translation"], "requires": []},
    "code": {"layer": 3, "tags": ["domain", "code", "execution"], "requires": []},
    "web": {"layer": 3, "tags": ["domain", "web", "scraping"], "requires": []},
    "search": {"layer": 3, "tags": ["domain", "search", "web"], "requires": []},
    
    # Domain Containers (Layer 3)
    "construction": {"layer": 3, "tags": ["domain", "container", "aec", "bim"], "requires": ["pdf", "ocr"]},
    "medical": {"layer": 3, "tags": ["domain", "container", "healthcare", "hipaa"], "requires": ["pdf", "ocr"]},
    "legal": {"layer": 3, "tags": ["domain", "container", "legal", "contracts"], "requires": ["pdf", "ocr"]},
    "finance": {"layer": 3, "tags": ["domain", "container", "finance", "risk"], "requires": ["pdf", "search"]},
    
    # Integration (Layer 4)
    "google_drive": {"layer": 4, "tags": ["integration", "storage", "cloud"], "requires": ["auth"]},
    "onedrive": {"layer": 4, "tags": ["integration", "storage", "cloud"], "requires": ["auth"]},
    "local_drive": {"layer": 4, "tags": ["integration", "storage", "local"], "requires": []},
    "android_drive": {"layer": 4, "tags": ["integration", "storage", "mobile"], "requires": []},
    "store": {"layer": 4, "tags": ["integration", "marketplace", "container"], "requires": []},
    
    # Interface (Layer 5)
    "failover": {"layer": 5, "tags": ["interface", "reliability"], "requires": ["monitoring"]},
    "hal": {"layer": 5, "tags": ["interface", "hardware"], "requires": []},
}

def migrate_block_file(filepath: Path, metadata: dict) -> bool:
    """Migrate a single block file to add universal metadata"""
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Check if already migrated
    if 'layer=' in content and 'tags=' in content:
        print(f"  ⏭️  {filepath.name} already migrated")
        return False
    
    # Find BlockConfig and add new fields
    # Pattern: BlockConfig( ... )
    config_pattern = r'(BlockConfig\([^)]+)'
    
    def add_metadata(match):
        config = match.group(1)
        
        # Add layer
        if 'layer=' not in config:
            config += f',\n            layer={metadata["layer"]}'
        
        # Add tags
        if 'tags=' not in config:
            tags_str = str(metadata['tags']).replace("'", '"')
            config += f',\n            tags={tags_str}'
        
        # Add requires
        if 'requires=' not in config and metadata['requires']:
            requires_str = str(metadata['requires']).replace("'", '"')
            config += f',\n            requires={requires_str}'
        
        return config
    
    new_content = re.sub(config_pattern, add_metadata, content)
    
    if new_content != content:
        with open(filepath, 'w') as f:
            f.write(new_content)
        return True
    
    return False

def main():
    """Run migration on all blocks"""
    
    blocks_dir = Path('/workspaces/SSDPPG/app/blocks')
    containers_dir = Path('/workspaces/SSDPPG/app/containers')
    
    print("🚀 Migrating Blocks to Universal Format")
    print("=" * 60)
    
    migrated = 0
    skipped = 0
    failed = 0
    
    # Migrate core blocks
    print("\n📦 Core Blocks (app/blocks/):")
    for block_file in blocks_dir.glob('*.py'):
        if block_file.name.startswith('_'):
            continue
            
        block_name = block_file.stem
        if block_name not in BLOCK_METADATA:
            print(f"  ⚠️  No metadata for {block_name}")
            skipped += 1
            continue
        
        try:
            if migrate_block_file(block_file, BLOCK_METADATA[block_name]):
                print(f"  ✅ Migrated {block_name}")
                migrated += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ❌ Failed {block_name}: {e}")
            failed += 1
    
    # Migrate containers
    print("\n🏭 Domain Containers (app/containers/):")
    for container_file in containers_dir.glob('*.py'):
        if container_file.name.startswith('_'):
            continue
            
        container_name = container_file.stem.replace('_container', '').replace('container', '')
        if container_name not in BLOCK_METADATA:
            # Try exact match
            container_name = container_file.stem
            if container_name not in BLOCK_METADATA:
                print(f"  ⚠️  No metadata for {container_file.stem}")
                skipped += 1
                continue
        
        try:
            if migrate_block_file(container_file, BLOCK_METADATA[container_name]):
                print(f"  ✅ Migrated {container_name}")
                migrated += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  ❌ Failed {container_name}: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"📊 Summary: {migrated} migrated, {skipped} skipped, {failed} failed")
    print("\nNext steps:")
    print("  1. Update BlockConfig dataclass to accept layer/tags/requires")
    print("  2. Run tests to verify blocks still work")
    print("  3. Update universal_assembler to use new metadata")

if __name__ == "__main__":
    main()
