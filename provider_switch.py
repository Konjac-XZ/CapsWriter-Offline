#!/usr/bin/env python3
"""
Command-line utility for switching between transcription providers.
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from util.provider_config import provider_manager


def list_providers():
    """List all available providers."""
    providers = provider_manager.list_providers()
    if not providers:
        print("No providers configured.")
        return
    
    print("Available Providers:")
    print("=" * 50)
    
    for provider in providers:
        status = "🟢 ACTIVE" if provider['enabled'] else "⚪ Inactive"
        print(f"{status} {provider['name']} ({provider['id']})")
        print(f"   Type: {provider['type']}")
        print(f"   Description: {provider['description']}")
        print()


def switch_provider(provider_id: str):
    """Switch to specified provider."""
    if provider_id not in provider_manager.providers:
        print(f"Error: Provider '{provider_id}' not found.")
        print("Available providers:")
        for pid in provider_manager.providers.keys():
            print(f"  - {pid}")
        return False
    
    success = provider_manager.set_active_provider(provider_id)
    if success:
        provider = provider_manager.get_provider(provider_id)
        print(f"✅ Successfully switched to provider: {provider.name}")
        return True
    else:
        print(f"❌ Failed to switch to provider: {provider_id}")
        return False


def show_current():
    """Show current active provider."""
    active = provider_manager.get_active_provider()
    if active:
        print(f"Current active provider: {active.name} ({active.type})")
        print(f"Description: {active.description}")
        
        # Show some settings
        print("\nSettings:")
        for key, value in active.settings.items():
            if 'key' in key.lower() or 'token' in key.lower():
                # Hide sensitive values
                print(f"  {key}: {'*' * min(8, len(str(value)))}")
            else:
                print(f"  {key}: {value}")
    else:
        print("No active provider configured.")


def main():
    parser = argparse.ArgumentParser(description="Manage transcription providers")
    parser.add_argument("--list", "-l", action="store_true", help="List all providers")
    parser.add_argument("--switch", "-s", type=str, help="Switch to specified provider")
    parser.add_argument("--current", "-c", action="store_true", help="Show current provider")
    parser.add_argument("--reload", "-r", action="store_true", help="Reload provider configurations")
    
    args = parser.parse_args()
    
    if args.reload:
        provider_manager.load_providers()
        print("Provider configurations reloaded.")
    
    if args.list:
        list_providers()
    elif args.switch:
        switch_provider(args.switch)
    elif args.current:
        show_current()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()