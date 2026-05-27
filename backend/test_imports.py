import pkgutil
import importlib
import sys

def import_submodules(package_name):
    package = importlib.import_module(package_name)
    results = {"success": [], "failed": []}
    
    for _, name, is_pkg in pkgutil.walk_packages(package.__path__, package.__name__ + '.'):
        try:
            importlib.import_module(name)
            results["success"].append(name)
        except Exception as e:
            results["failed"].append((name, str(e)))
            
    return results

if __name__ == "__main__":
    res = import_submodules("app")
    print(f"Successfully imported {len(res['success'])} modules.")
    if res["failed"]:
        print(f"\nFAILED to import {len(res['failed'])} modules:")
        for name, err in res["failed"]:
            print(f"  - {name}: {err}")
        sys.exit(1)
    else:
        print("ALL modules imported successfully! 🚀")
