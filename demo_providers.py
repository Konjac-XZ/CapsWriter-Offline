#!/usr/bin/env python3
"""
Example script demonstrating the dynamic provider switching system.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from util.provider_config import provider_manager


def demo_provider_switching():
    """Demonstrate provider switching capabilities."""
    print("=== CapsWriter Provider Management Demo ===\n")
    
    # Show all available providers
    print("1. Available Providers:")
    providers = provider_manager.list_providers()
    for provider in providers:
        status = "✅ ACTIVE" if provider['enabled'] else "⚪ Inactive"
        print(f"   {status} {provider['name']} - {provider['description']}")
    
    print(f"\nFound {len(providers)} providers configured.\n")
    
    # Show current active provider
    active = provider_manager.get_active_provider()
    if active:
        print(f"2. Current Active Provider: {active.name}")
        print(f"   Type: {active.type}")
        print(f"   Description: {active.description}")
    else:
        print("2. No active provider set.")
    
    # Demonstrate switching (if multiple providers available)
    inactive_providers = [p for p in providers if not p['enabled']]
    if inactive_providers and active:
        print(f"\n3. Switching Demo:")
        target_provider = inactive_providers[0]
        print(f"   Switching from {active.name} to {target_provider['name']}...")
        
        success = provider_manager.set_active_provider(target_provider['id'])
        if success:
            print(f"   ✅ Successfully switched to {target_provider['name']}")
            
            # Switch back to original
            print(f"   Switching back to {active.name}...")
            provider_manager.set_active_provider([p['id'] for p in providers if p['enabled']][0])
            print(f"   ✅ Restored original provider")
        else:
            print(f"   ❌ Failed to switch providers")
    else:
        print(f"\n3. Demo skipped - need multiple providers for switching demo")
    
    # Show prompt management
    current_prompt = provider_manager.get_provider_prompt()
    print(f"\n4. Current Transcription Prompt:")
    if current_prompt.strip():
        print(f'   "{current_prompt[:100]}..."' if len(current_prompt) > 100 else f'   "{current_prompt}"')
    else:
        print("   (No custom prompt set)")


if __name__ == "__main__":
    demo_provider_switching()