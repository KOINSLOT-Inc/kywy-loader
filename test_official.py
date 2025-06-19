#!/usr/bin/env python3
"""
Test script to verify the "official" placeholder functionality
"""
import os
import sys

# Import the app classes
sys.path.insert(0, os.path.dirname(__file__))
from kywy_loader import UF2InstallerApp

def test_official_placeholder():
    print("Testing 'official' placeholder functionality...")
    
    # Create a test app instance without starting the GUI
    app = UF2InstallerApp([])
    
    # Test loading repos from file
    app.load_repos_from_file()
    
    print(f"Loaded repos: {app.repos}")
    
    # Check that repos were loaded
    if len(app.repos) >= 2:
        print("✓ Repos loaded successfully")
        
        # Check that we have the expected repos
        expected_repos = [("Koinslot-INC", "kywy", "main"), ("Koinslot-INC", "kywy-rust", "main")]
        for expected in expected_repos:
            if expected in app.repos:
                print(f"✓ Found expected repo: {expected}")
            else:
                print(f"✗ Missing expected repo: {expected}")
    else:
        print("✗ Failed to load repos")
    
    print("Test completed!")

if __name__ == "__main__":
    test_official_placeholder()
